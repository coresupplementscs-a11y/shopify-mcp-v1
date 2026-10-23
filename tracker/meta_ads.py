"""
Reads from Meta for the hub: ad spend and Meta-attributed purchases per ad,
split into click and view sales (Marketing API insights), spend per campaign
per day, ads' current names by ad id, Event Match Quality per dataset
(Dataset Quality API) and dataset names. Results are cached briefly (names
for a day). Nothing here raises to the caller: failures come back as an
`error` string the hub can show (the daily series come back as None, and an
ad whose name can't be read is simply left out).
The token travels in a header, never in a URL, so it can't reach a log line.
"""
import asyncio
import datetime as dt
import json
import logging
import math
import re
import time
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

import attribution
import config
import db

log = logging.getLogger("tracker.meta_ads")

GRAPH = "https://graph.facebook.com"
# Meta reports the same purchases under several action types; take the first present.
PURCHASE_TYPES = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase")
ADD_TO_CART_TYPES = ("omni_add_to_cart", "add_to_cart", "offsite_conversion.fb_pixel_add_to_cart")
# Asking for these makes Meta add a "7d_click" and a "1d_view" count to each
# action next to its usual "value": how many sales came from a click versus
# from someone who only saw the ad.
CLICK_WINDOW, VIEW_WINDOW = "7d_click", "1d_view"
SPLIT_KEYS = ("meta_click_purchases", "meta_view_purchases", "meta_click_value", "meta_view_value")
AD_FIELDS = ("account_id,campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,"
             "spend,impressions,clicks,actions,action_values")
# Ad names by id: one Graph call answers up to 50 ids.
NAME_FIELDS = "name,adset{name},campaign{name}"
NAMES_PER_CALL = 50
NAMES_TTL = 86400                  # a day per id
NAMES_RETRY_TTL = 600              # Meta couldn't answer just now: ask again in 10 minutes
NAME_CALLS_MAX = 12                # Graph calls one lookup may make, the retries below included
# Names are a nicety the orders, creatives and assists sections wait on: a slow
# Meta gets this long per call, not the 30 seconds the spend reads get.
NAMES_TIMEOUT = 8.0
# "Does not exist" (or can't be loaded): Meta refuses a whole batch for one such id.
UNKNOWN_ID_CODES = {100, 803}
_AD_ID = re.compile(r"\d{1,32}")   # Meta ad ids are numbers; anything else came from a hand-made link

_cache: dict[str, tuple[float, Any]] = {}
_client: Optional[httpx.AsyncClient] = None


class MetaReadError(Exception):
    def __init__(self, message: str = "", code: Any = None):
        super().__init__(message)
        self.code = code


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


def set_http_client(client: httpx.AsyncClient) -> None:
    global _client
    _client = client
    _cache.clear()
    reset_catalog()
    reset_links()


def reset_catalog() -> None:
    """Forget the in-memory ad catalog (tests; a new database)."""
    _catalog.update(at=0.0, rows=None, task=None, db=None)


def ads_token() -> str:
    return config.META_ADS_TOKEN or config.META_ACCESS_TOKEN


def _cached(key: str) -> Any:
    hit = _cache.get(key)
    return hit[1] if hit and hit[0] > time.time() else None


def _store(key: str, value: Any, ttl: float) -> Any:
    # Keys carry dates, so without this the cache grows for as long as the process runs.
    now = time.time()
    for k in [k for k, (exp, _) in _cache.items() if exp < now]:
        del _cache[k]
    _cache[key] = (now + ttl, value)
    return value


# Meta's rate limits (ads_management and insights business use cases, user and app limits).
RATE_CODES = {4, 17, 32, 613, 80000, 80001, 80002, 80003, 80004, 80005, 80006, 80008, 80009, 80014}
SLOW_MIN = 300                     # seconds an ad account is left alone after Meta says slow down
PROBE_LIMIT = 80                   # after a slow-down, heavy reads wait until usage is under this (%)
_slow: dict[str, float] = {}       # ad account id -> no calls before this time
_loaded = False                    # _slow and _last_good read back from the database (a deploy restarts the app)


def _restore() -> None:
    """A restart (every deploy) used to forget Meta's slow-down, so the new app called the
    throttled account at once and showed dashes instead of the last spend read."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        for acct, until in json.loads(db.kv_get("meta:slow") or "{}").items():
            _slow[str(acct)] = max(_slow.get(str(acct), 0.0), float(until))
        for key, (at, result) in json.loads(db.kv_get("meta:last_good") or "{}").items():
            if key not in _last_good:
                _last_good[key] = (float(at), result)
    except Exception:              # a bad saved copy only costs the memory, never a read
        log.warning("meta_ads: saved slow-down state unreadable", exc_info=True)


def _save_slow() -> None:
    try:
        db.kv_set("meta:slow", json.dumps({a: t for a, t in _slow.items() if t > time.time() - 3600}))
    except Exception:
        log.warning("meta_ads: could not save the slow-down", exc_info=True)


def _account_of(path: Any) -> str:
    m = re.match(r"act_(\d+)", str(path or ""))
    return m.group(1) if m else ""


def _note_usage(resp: httpx.Response, account: str, limit: float = 95) -> None:
    """Meta's usage header: an ad account near its limit (95%+) or told to wait is left
    alone until then. The app reading ads is on Meta's development tier (19 Oct 2026),
    whose limits are low: every call kept off a throttled account shortens the wait."""
    raw = resp.headers.get("x-business-use-case-usage") if resp is not None else None
    if not raw:
        return
    try:
        usage = json.loads(raw)
    except ValueError:
        return
    for acct, entries in (usage.items() if isinstance(usage, dict) else []):
        worst, wait = 0.0, 0.0
        for e in entries if isinstance(entries, list) else []:
            if isinstance(e, dict):
                worst = max([worst] + [float(e.get(k) or 0) for k in ("call_count", "total_cputime", "total_time")])
                wait = max(wait, float(e.get("estimated_time_to_regain_access") or 0) * 60)
        if wait > 0 or worst >= limit:
            _slow[str(acct)] = max(_slow.get(str(acct), 0.0), time.time() + max(wait, SLOW_MIN))
            _save_slow()


def slowed_until(account: str) -> float:
    """When the tracker reads this ad account again (0: it may now)."""
    _restore()
    t = _slow.get(str(account), 0.0)
    return t if t > time.time() else 0.0


def _clock(ts: float) -> str:
    """'6:22 AM' in the store's time (the server runs on UTC)."""
    d = dt.datetime.fromtimestamp(ts, config.store_tz())
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def _gate(path: Any) -> None:
    acct = _account_of(path)
    until = slowed_until(acct) if acct else 0.0
    if until:
        at = _clock(until)
        raise MetaReadError(f"Meta asked Core HQ to slow down on this ad account; reading again after {at}", code=17)


def _check(resp: httpx.Response, account: str = "") -> dict:
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if account:
        _note_usage(resp, account)
    if resp.status_code >= 400 or (isinstance(data, dict) and "error" in data):
        err = data.get("error") if isinstance(data, dict) else None
        if not isinstance(err, dict):            # a proxy in front of Meta: {"error": "Bad gateway"}
            err = {"message": str(err)[:300]} if err else {}
        if account and err.get("code") in RATE_CODES:
            _slow[account] = max(_slow.get(account, 0.0), time.time() + SLOW_MIN)
            _save_slow()
        raise MetaReadError(err.get("error_user_msg") or err.get("message") or f"HTTP {resp.status_code}",
                            code=err.get("code"))
    return data if isinstance(data, dict) else {}


def _auth(token: str) -> dict:
    # A header, not ?access_token=: request URLs end up in logs (httpx logs
    # every one at INFO), headers don't.
    return {"Authorization": f"Bearer {token}"}


async def _probe(path: Any, token: str) -> None:
    """After a slow-down, one light read first: Meta's usage is a rolling hour, so a heavy
    report sent the moment the wait ends tipped the account straight back over (19 Oct,
    CPU at 96-108% every 5 minutes). Heavy reads resume once usage is under PROBE_LIMIT."""
    acct = _account_of(path)
    if not acct or acct not in _slow or slowed_until(acct):
        return
    try:
        resp = await _http().get(f"{GRAPH}/{config.META_API_VERSION}/act_{acct}", params={"fields": "id"},
                                 headers=_auth(token))
    except httpx.HTTPError as e:
        raise MetaReadError(f"network: {type(e).__name__}")
    _note_usage(resp, acct, limit=PROBE_LIMIT)
    if slowed_until(acct):
        _save_slow()
        _gate(path)
    _check(resp, acct)
    _slow.pop(acct, None)
    _save_slow()


async def _get(path: str, params: dict, token: str, timeout: Any = httpx.USE_CLIENT_DEFAULT) -> dict:
    _gate(path)
    await _probe(path, token)
    try:
        resp = await _http().get(f"{GRAPH}/{config.META_API_VERSION}/{path}", params=params,
                                 headers=_auth(token), timeout=timeout)
    except httpx.HTTPError as e:
        raise MetaReadError(f"network: {type(e).__name__}")
    return _check(resp, _account_of(path))


async def _paged(path: str, params: dict, token: str, max_pages: int = 20) -> list[dict]:
    data = await _get(path, params, token)
    rows = list(data.get("data", []))
    for _ in range(max_pages):
        nxt = (data.get("paging") or {}).get("next")
        if not nxt:
            break
        # Meta's next link repeats the token in its query; drop it and send the header.
        try:
            url = httpx.URL(nxt).copy_remove_param("access_token")
        except (httpx.InvalidURL, TypeError):
            raise MetaReadError("unexpected paging link")
        if url.scheme != "https" or url.host != httpx.URL(GRAPH).host:   # the token goes to Meta only
            raise MetaReadError("unexpected paging link")
        _gate(path)
        try:
            data = _check(await _http().get(url, headers=_auth(token)), _account_of(path))
        except httpx.HTTPError as e:
            raise MetaReadError(f"network: {type(e).__name__}")
        rows.extend(data.get("data", []))
    return rows


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _purchase_entry(actions: Any) -> Optional[dict]:
    by = {a.get("action_type"): a for a in (actions or []) if isinstance(a, dict)}
    return next((by[t] for t in PURCHASE_TYPES if t in by), None)


def purchases(actions: Any) -> float:
    entry = _purchase_entry(actions)
    return _num(entry.get("value")) if entry else 0.0


def _window(entry: dict, key: str) -> Optional[float]:
    if key not in entry:
        # Meta leaves a window out when it has nothing in it (a link click
        # never counts as a view), so beside the other window it means 0.
        other = VIEW_WINDOW if key == CLICK_WINDOW else CLICK_WINDOW
        return 0.0 if other in entry else None
    try:
        v = float(entry[key])
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def add_to_carts(actions: Any) -> float:
    """Meta's add-to-cart count for one ad, from the first type it reports."""
    by = {a.get("action_type"): a for a in (actions or []) if isinstance(a, dict)}
    entry = next((by[t] for t in ADD_TO_CART_TYPES if t in by), None)
    return _num(entry.get("value")) if entry else 0.0


def purchase_split(actions: Any) -> tuple[Optional[float], Optional[float]]:
    """(click, view): the purchases (or their value) Meta counts in the 7-day
    click and the 1-day view windows, from the same action type purchases()
    reads. None where Meta didn't split them; (0, 0) when there were none."""
    entry = _purchase_entry(actions)
    if entry is None:
        return 0.0, 0.0
    return _window(entry, CLICK_WINDOW), _window(entry, VIEW_WINDOW)


def _split(row: dict) -> dict:
    """The click/view keys for one ad, or {} when Meta gave no split for its
    sales at all (the hub then shows the total alone)."""
    actions, values = row.get("actions"), row.get("action_values")
    split = (*purchase_split(actions), *purchase_split(values))
    reported = any(CLICK_WINDOW in e or VIEW_WINDOW in e
                   for e in (_purchase_entry(actions), _purchase_entry(values)) if e)
    return dict(zip(SPLIT_KEYS, split)) if reported else {}


async def account_info(account_id: str) -> dict:
    key = f"acct:{account_id}"
    hit = _cached(key)
    if hit is not None:
        return hit
    data = await _get(f"act_{account_id}", {"fields": "name,currency,timezone_name"}, ads_token())
    return _store(key, data, 3600)


_last_good: dict[str, tuple[float, dict]] = {}     # insights key -> (read at, the last full read)


def _save_good() -> None:
    """The last full reads of the last 6 hours, kept in the database for after a restart."""
    try:
        now = time.time()
        for k in [k for k, (at, _) in _last_good.items() if now - at > 6 * 3600]:
            del _last_good[k]
        db.kv_set("meta:last_good", json.dumps({k: [at, r] for k, (at, r) in _last_good.items()}))
    except Exception:
        log.warning("meta_ads: could not save the last spend read", exc_info=True)


def _range_ttl(until: str, today_ttl: float) -> float:
    """A range that ended before yesterday no longer changes much: read it every half hour."""
    try:
        old = dt.date.fromisoformat(until) < dt.date.today() - dt.timedelta(days=1)
    except ValueError:
        old = False
    return max(today_ttl, 1800) if old else today_ttl


async def ad_insights(since: str, until: str, ttl: float = 300) -> dict:
    """Per-ad spend and Meta-attributed purchases for the days [since, until].
    meta_purchases/meta_value are Meta's usual totals. When Meta splits them
    by window a row also has meta_click_purchases, meta_view_purchases,
    meta_click_value and meta_view_value (None for a window it left unclear);
    without a split those keys are absent."""
    if not config.META_AD_ACCOUNT_IDS:
        return {"connected": False, "rows": [], "currency": "",
                "error": "Not connected yet: add META_ADS_TOKEN and META_AD_ACCOUNT_IDS in Railway."}
    key = f"ads:{since}:{until}"
    hit = _cached(key)
    if hit is not None:
        return hit
    rows, errors, currency, accounts = [], [], "", []
    for acct in config.META_AD_ACCOUNT_IDS:
        try:
            info = await account_info(acct)
            currency = currency or info.get("currency", "")
            accounts.append({"id": acct, "name": info.get("name", ""), "currency": info.get("currency", ""),
                             "timezone": info.get("timezone_name", "")})
            for r in await _paged(f"act_{acct}/insights", {
                    "level": "ad", "fields": AD_FIELDS, "limit": 500,
                    "time_range": json.dumps({"since": since, "until": until}),
                    "action_attribution_windows": json.dumps([CLICK_WINDOW, VIEW_WINDOW])}, ads_token()):
                rows.append({
                    "account_id": acct,
                    "campaign_id": r.get("campaign_id", ""), "campaign_name": r.get("campaign_name", ""),
                    "adset_id": r.get("adset_id", ""), "adset_name": r.get("adset_name", ""),
                    "ad_id": r.get("ad_id", ""), "ad_name": r.get("ad_name", ""),
                    "spend": _num(r.get("spend")), "impressions": int(_num(r.get("impressions"))),
                    "clicks": int(_num(r.get("clicks"))),
                    "meta_purchases": purchases(r.get("actions")),
                    "meta_value": purchases(r.get("action_values")),
                    "meta_add_to_carts": add_to_carts(r.get("actions")),
                    **_split(r),
                })
        except MetaReadError as e:
            errors.append(f"act_{acct}: {e}")
    result = {"connected": not errors, "rows": rows, "currency": currency, "accounts": accounts,
              "error": "; ".join(errors)}
    if not errors:
        _last_good[key] = (time.time(), result)
        _save_good()
        return _store(key, result, _range_ttl(until, ttl))
    _restore()
    stale = _last_good.get(key)
    if stale and time.time() - stale[0] < 6 * 3600:
        # Meta said slow down (or failed): the last full read stands in, said as such, not dashes.
        at = _clock(stale[0])
        return _store(key, {**stale[1], "stale_at": stale[0],
                            "error": f"Meta is limiting reads just now: spend and Meta sales as of {at}."}, 60)
    return _store(key, result, 60)


async def purchases_by_age_gender(since: str, until: str) -> Optional[list[dict]]:
    """Meta's purchases by age band and gender across the ad accounts:
    [{"age", "gender", "n", "value", "spend"}]. None when an account can't be read."""
    if not config.META_AD_ACCOUNT_IDS:
        return []
    key = f"agegender:{since}:{until}"
    hit = _cached(key)
    if hit is not None:
        return hit
    out: dict[tuple, dict] = {}
    for acct in config.META_AD_ACCOUNT_IDS:
        try:
            for r in await _paged(f"act_{acct}/insights", {
                    "level": "account", "fields": "spend,actions,action_values", "breakdowns": "age,gender",
                    "time_range": json.dumps({"since": since, "until": until})}, ads_token()):
                k = (str(r.get("age") or ""), str(r.get("gender") or ""))
                e = out.setdefault(k, {"age": k[0], "gender": k[1], "n": 0.0, "value": 0.0, "spend": 0.0})
                e["n"] += purchases(r.get("actions"))
                e["value"] += purchases(r.get("action_values"))
                e["spend"] += _num(r.get("spend"))
        except MetaReadError as e:
            log.info("age and gender for act_%s unavailable: %s", acct, e)
            return None
    rows = sorted(out.values(), key=lambda e: (e["age"], e["gender"]))
    return _store(key, rows, 600)


async def daily_spend(since: str, until: str) -> Optional[dict]:
    """Spend per day across the ad accounts: {'YYYY-MM-DD': amount}. None when
    any account can't be read: part of the spend would chart as real $0 days."""
    if not config.META_AD_ACCOUNT_IDS:
        return {}
    key = f"daily:{since}:{until}"
    hit = _cached(key)
    if hit is not None:
        return hit
    out: dict[str, float] = {}
    for acct in config.META_AD_ACCOUNT_IDS:
        try:
            for r in await _paged(f"act_{acct}/insights", {
                    "level": "account", "fields": "spend", "time_increment": 1,
                    "time_range": json.dumps({"since": since, "until": until})}, ads_token()):
                day = r.get("date_start", "")
                out[day] = out.get(day, 0.0) + _num(r.get("spend"))
        except MetaReadError as e:
            log.info("daily spend for act_%s unavailable: %s", acct, e)
            return None                         # not cached: the next refresh asks again
    return _store(key, out, 300)


async def campaign_daily_spend(since: str, until: str) -> Optional[dict]:
    """Spend per campaign per day across the ad accounts:
    {'YYYY-MM-DD': [{'campaign_id', 'campaign_name', 'spend'}, ...]}. The hub
    reads it to know which campaigns ran on each day of its charts. Meta lists
    only campaigns that delivered that day. None when any account can't be
    read: a day would look like it had no campaigns running."""
    if not config.META_AD_ACCOUNT_IDS:
        return {}
    key = f"cdaily:{since}:{until}"
    hit = _cached(key)
    if hit is not None:
        return hit
    out: dict[str, list[dict]] = {}
    for acct in config.META_AD_ACCOUNT_IDS:
        try:
            for r in await _paged(f"act_{acct}/insights", {
                    "level": "campaign", "fields": "campaign_id,campaign_name,spend", "time_increment": 1,
                    "limit": 500, "time_range": json.dumps({"since": since, "until": until})}, ads_token()):
                day = str(r.get("date_start") or "")
                if day:
                    out.setdefault(day, []).append({"campaign_id": str(r.get("campaign_id") or ""),
                                                    "campaign_name": str(r.get("campaign_name") or ""),
                                                    "spend": _num(r.get("spend"))})
        except MetaReadError as e:
            log.info("campaign spend per day for act_%s unavailable: %s", acct, e)
            return None                         # not cached: the next refresh asks again
    return _store(key, out, 300)


# --- the campaign table (Ads Manager's columns) ------------------------------------

CHECKOUT_TYPES = ("omni_initiated_checkout", "initiate_checkout", "offsite_conversion.fb_pixel_initiate_checkout")
CAMPAIGN_FIELDS = ("id,name,status,effective_status,bid_strategy,created_time,daily_budget,lifetime_budget,"
                   "adsets.limit(1){optimization_goal,bid_strategy}")
CAMPAIGN_STATS = ("campaign_id,spend,impressions,cpm,inline_link_clicks,unique_inline_link_clicks,"
                  "inline_link_click_ctr,cost_per_inline_link_click,actions,action_values")


def _first(actions: Any, types: tuple) -> float:
    by = {a.get("action_type"): a for a in (actions or []) if isinstance(a, dict)}
    entry = next((by[t] for t in types if t in by), None)
    return _num(entry.get("value")) if entry else 0.0


def bid_label(campaign: dict) -> str:
    """Ads Manager's words for a campaign's bid strategy (its own, else its first ad set's)."""
    sets = ((campaign.get("adsets") or {}).get("data") or [{}])
    first = sets[0] if sets and isinstance(sets[0], dict) else {}
    strategy = campaign.get("bid_strategy") or first.get("bid_strategy") or ""
    fixed = {"COST_CAP": "Cost per result goal", "LOWEST_COST_WITH_BID_CAP": "Bid cap",
             "LOWEST_COST_WITH_MIN_ROAS": "ROAS goal"}
    if strategy in fixed:
        return fixed[strategy]
    return "Highest value" if first.get("optimization_goal") == "VALUE" else "Highest volume"


def _delivery(obj: dict) -> str:
    eff = str(obj.get("effective_status") or obj.get("status") or "")
    if eff == "ACTIVE":
        return "Active"
    if eff in ("PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED"):
        return "Off"
    return eff.replace("_", " ").title() or "Off"


def _metrics(st: dict, currency: str) -> dict:
    """Ads Manager's numbers for one row of Meta's insights (empty: nothing in the range)."""
    spend = _num(st.get("spend"))
    link = _num(st.get("inline_link_clicks"))
    atc = _first(st.get("actions"), ADD_TO_CART_TYPES)
    ic = _first(st.get("actions"), CHECKOUT_TYPES)
    res = purchases(st.get("actions"))
    value = purchases(st.get("action_values"))
    return {
        "spend": round(spend, 2), "impressions": int(_num(st.get("impressions"))),
        "unique_link_clicks": int(_num(st.get("unique_inline_link_clicks"))), "link_clicks": int(link),
        "cpc": round(_num(st.get("cost_per_inline_link_click")), 2) if link else None,
        "cpm": round(_num(st.get("cpm")), 2) if st.get("impressions") else None,
        "ctr": round(_num(st.get("inline_link_click_ctr")), 2) if st.get("impressions") else None,
        "add_to_carts": int(atc), "cost_per_add_to_cart": round(spend / atc, 2) if atc else None,
        "checkouts": int(ic), "cost_per_checkout": round(spend / ic, 2) if ic else None,
        "results": res, "cost_per_result": round(spend / res, 2) if res else None,
        "roas": round(value / spend, 2) if spend and value else None,
        "conversion_value": round(value, 2), "currency": currency,
    }


def _budget(obj: dict, none_label: str) -> dict:
    daily, life = _num(obj.get("daily_budget")) / 100, _num(obj.get("lifetime_budget")) / 100
    return {"budget": daily or life or None, "budget_type": "Daily" if daily else "Lifetime" if life else none_label}


# What each level reads: its list (with the fields Ads Manager shows) and its stats.
LEVELS = {
    "campaign": {"list": "campaigns", "fields": CAMPAIGN_FIELDS, "id": "campaign_id"},
    "adset": {"list": "adsets", "fields": "id,name,status,effective_status,bid_strategy,created_time,daily_budget,"
                                          "lifetime_budget,optimization_goal", "id": "adset_id"},
    "ad": {"list": "ads", "fields": "id,name,status,effective_status,created_time", "id": "ad_id"},
}


async def level_table(level: str, parent: str, since: str, until: str, ttl: float = 180) -> dict:
    """Ads Manager's table at one level for the days [since, until]: every campaign in the
    account(s) (parent ''), every ad set of campaign `parent`, or every ad of ad set
    `parent`. Each row: on/off, bid strategy, date created, delivery, budget, spend,
    unique link clicks, CPC (per link click), CPM, link CTR, adds to cart and their cost,
    checkouts initiated and their cost, results (website purchases), cost per result,
    results ROAS and the purchases' conversion value. Meta's own numbers (7-day click,
    1-day view). An ad set or ad without its own bid strategy or budget uses its parent's."""
    lv = LEVELS[level]
    if not config.META_AD_ACCOUNT_IDS:
        return {"connected": False, "rows": [], "error": "Not connected yet: add META_ADS_TOKEN and META_AD_ACCOUNT_IDS."}
    key = f"table:{level}:{parent}:{since}:{until}"
    hit = _cached(key)
    if hit is not None:
        return hit
    rows, errors = [], []
    owners = [f"act_{a}" for a in config.META_AD_ACCOUNT_IDS] if level == "campaign" else [str(parent)]
    try:
        currency = (await account_info(config.META_AD_ACCOUNT_IDS[0])).get("currency", "")
    except MetaReadError:
        currency = ""
    for owner in owners:
        try:
            items = _cached(f"{lv['list']}:{owner}")
            if items is None:
                items = _store(f"{lv['list']}:{owner}", await _paged(f"{owner}/{lv['list']}", {
                    "fields": lv["fields"], "limit": 100}, ads_token()), 900)
            stats = {str(r.get(lv["id"])): r for r in await _paged(f"{owner}/insights", {
                "level": level, "fields": f"{lv['id']},{CAMPAIGN_STATS.split(',', 1)[1]}", "limit": 500,
                "time_range": json.dumps({"since": since, "until": until}),
                "action_attribution_windows": json.dumps([CLICK_WINDOW, VIEW_WINDOW])}, ads_token())}
            for it in items:
                iid = str(it.get("id") or "")
                row = {"id": iid, "name": str(it.get("name") or ""), "on": str(it.get("status") or "") == "ACTIVE",
                       "delivery": _delivery(it), "created": str(it.get("created_time") or "")[:10],
                       **_metrics(stats.get(iid) or {}, currency)}
                if level == "campaign":
                    row.update(campaign_id=iid, campaign_name=row["name"], bid_strategy=bid_label(it), **_budget(it, "Ad set budget"))
                elif level == "adset":
                    own = it.get("bid_strategy")
                    row.update(bid_strategy=bid_label({"bid_strategy": own, "adsets": {"data": [it]}}) if own or it.get("optimization_goal")
                               else "Using campaign bid strategy", **_budget(it, "Using campaign budget"))
                else:
                    row.update(bid_strategy="Using ad set bid strategy", budget=None, budget_type="Using ad set budget")
                rows.append(row)
        except MetaReadError as e:
            errors.append(f"{owner}: {e}")
    result = {"connected": not errors, "rows": rows, "error": "; ".join(errors)}
    return _store(key, result, _range_ttl(until, ttl) if not errors else 60)


async def campaign_table(since: str, until: str, ttl: float = 180) -> dict:
    """Every campaign with Ads Manager's columns (level_table at the top level)."""
    return await level_table("campaign", "", since, until, ttl)


# --- ad names by id ---------------------------------------------------------------

def _name(obj: Any) -> str:
    return str(obj.get("name") or "").strip()[:300] if isinstance(obj, dict) else ""


def _names_of(entry: Any) -> dict:
    if not isinstance(entry, dict):
        return {}
    names = {"ad_name": _name(entry), "adset_name": _name(entry.get("adset")),
             "campaign_name": _name(entry.get("campaign"))}
    return names if any(names.values()) else {}


def _remember_missing(ad_ids: list[str], ttl: float) -> None:
    for ad in ad_ids:
        _store(f"adname:{ad}", {}, ttl)


async def ad_names(ad_ids: Any) -> dict[str, dict]:
    """Meta's current names for ads, by ad id: {ad_id: {'ad_name',
    'adset_name', 'campaign_name'}}. Links carry names too, but they are
    whatever the URL parameters said when the ad was set up (older ads even
    had utm_content and utm_term swapped), so the hub prefers these. Asked 50
    ids per Graph call and cached a day per id. Never raises: an id Meta
    doesn't know, or can't answer for just now, is simply absent."""
    try:
        return await _ad_names(ad_ids)
    except Exception as e:
        log.info("ad names unavailable: %s", type(e).__name__)
        return {}


async def _ad_names(ad_ids: Any) -> dict[str, dict]:
    ids = ad_ids if isinstance(ad_ids, (list, tuple, set)) else []
    wanted = list(dict.fromkeys(s for s in (str(a or "").strip() for a in ids) if _AD_ID.fullmatch(s)))
    if not wanted or not config.META_AD_ACCOUNT_IDS:
        return {}
    out: dict[str, dict] = {}
    ask = []
    for ad in wanted:
        hit = _cached(f"adname:{ad}")
        if hit is None:
            ask.append(ad)
        elif hit:                               # {} is a remembered "Meta doesn't know it"
            out[ad] = hit
    queue = [ask[i:i + NAMES_PER_CALL] for i in range(0, len(ask), NAMES_PER_CALL)]
    calls = 0
    while queue:
        batch = queue.pop(0)
        if calls >= NAME_CALLS_MAX:
            _remember_missing(batch, NAMES_RETRY_TTL)
            continue
        calls += 1
        try:
            data = await _get("", {"ids": ",".join(batch), "fields": NAME_FIELDS}, ads_token(),
                              timeout=NAMES_TIMEOUT)
        except MetaReadError as e:
            if e.code not in UNKNOWN_ID_CODES:  # rate limit, token, network: nothing to learn about the ids
                # The next batch would meet the same wall (and a rate limit
                # would only get worse, for the spend reads too): stop here.
                log.info("ad names unavailable: %s", e)
                for rest in [batch, *queue]:
                    _remember_missing(rest, NAMES_RETRY_TTL)
                break
            # Meta refuses the whole batch when one id doesn't exist. It usually
            # names that id: drop it and ask for the rest again. Otherwise halve
            # the batch until the unknown id stands alone.
            named = [ad for ad in batch if re.search(rf"(?<!\d){ad}(?!\d)", str(e))]
            if named or len(batch) == 1:
                _remember_missing(named or batch, NAMES_TTL)
                rest = [ad for ad in batch if ad not in named]
                if named and rest:
                    queue.insert(0, rest)
            else:
                queue[:0] = [batch[:len(batch) // 2], batch[len(batch) // 2:]]
            continue
        for ad in batch:
            names = _names_of(data.get(ad))
            _store(f"adname:{ad}", names, NAMES_TTL)
            if names:
                out[ad] = names
    return out


# --- the ad catalog: what utm names are matched against -------------------------------

CATALOG_TTL = 1800                 # a fresh read of the window's ads every half hour at most
CATALOG_WAIT = 8.0                 # a Purchase waits this long for it, then uses what is known
CATALOG_KEYS = ("ad_id", "ad_name", "adset_id", "adset_name", "campaign_id", "campaign_name")
_catalog: dict[str, Any] = {"at": 0.0, "rows": None, "task": None, "db": None}


def _catalog_state() -> dict:
    # A different database (a test, a new volume) starts from its own stored catalog.
    if _catalog["db"] != db.DB_PATH:
        _catalog.update(at=0.0, rows=None, task=None, db=db.DB_PATH)
    return _catalog


def _catalog_row(r: dict) -> dict:
    return {k: str(r.get(k) or "").strip()[:300] for k in CATALOG_KEYS}


def cached_catalog() -> list[dict]:
    """Every ad known right now, without asking Meta: the last catalog read
    (kept in the database across restarts), the insights rows in the cache and
    the ads named by id. Newer knowledge of an ad id replaces older."""
    state = _catalog_state()
    rows = state["rows"]
    if rows is None:
        try:
            rows = json.loads(db.kv_get("ad_catalog") or "[]")
        except ValueError:
            rows = []
        state["rows"] = rows = [_catalog_row(r) for r in rows if isinstance(r, dict)]
    by_id = {r["ad_id"]: r for r in rows if r.get("ad_id")}
    now = time.time()
    for key, (exp, value) in list(_cache.items()):
        if exp < now:
            continue
        if key.startswith("ads:") and isinstance(value, dict):
            for r in value.get("rows") or []:
                if r.get("ad_id"):
                    by_id[str(r["ad_id"])] = _catalog_row(r)
        elif key.startswith("adname:") and value and key[7:] not in by_id:
            by_id[key[7:]] = _catalog_row({**value, "ad_id": key[7:]})
    return list(by_id.values())


async def _read_catalog() -> list[dict]:
    today = dt.datetime.now(config.store_tz()).date()
    since = (today - dt.timedelta(days=max(1, config.ATTRIBUTION_WINDOW_DAYS))).isoformat()
    ins = await ad_insights(since, today.isoformat(), ttl=CATALOG_TTL)
    if ins.get("rows"):
        seen = {str(r["ad_id"]): _catalog_row(r) for r in ins["rows"] if r.get("ad_id")}
        _catalog_state()["rows"] = rows = list(seen.values())
        db.kv_set("ad_catalog", json.dumps(rows))
    # A failed read is asked again in 5 minutes, not in half an hour.
    _catalog_state()["at"] = time.time() if ins.get("connected") else time.time() - CATALOG_TTL + 300
    return cached_catalog()


async def ad_catalog(wait: float = CATALOG_WAIT) -> list[dict]:
    """The Meta ads of the attribution window (id, name, ad set and campaign,
    ids and names), for turning a link's utm names into one ad id
    (attribution.resolve_names). Read from Meta at most every CATALOG_TTL; a
    slow Meta gets `wait` seconds, then the last known catalog is used and the
    read finishes in the background. [] when no ad account is set up."""
    if not config.META_AD_ACCOUNT_IDS:
        return []
    state = _catalog_state()
    if time.time() - state["at"] < CATALOG_TTL:
        return cached_catalog()
    loop = asyncio.get_running_loop()
    task = state["task"]
    if task is None or task.done() or task.get_loop() is not loop:
        task = state["task"] = loop.create_task(_read_catalog())
    try:
        return await asyncio.wait_for(asyncio.shield(task), wait)
    except Exception as e:                      # a timeout or a Meta hiccup: what is known will do
        log.info("ad catalog: using the last known ads (%s)", type(e).__name__)
        return cached_catalog()


# --- the ads' links: which live ads' links name their ad ---------------------------------

LINKS_TTL = 1800                   # the live ads' links are read again every half hour at most
LINKS_WAIT = 8.0                   # a sale being named waits this long for a fresh read
LINKS_MEMORY_DAYS = 14             # how long an ad seen with a link that didn't name it stays on record
# A link names its ad when its URL parameters carry the ad's id or name (Meta
# fills them in on every click); the catalog turns the name into the id.
NAMING_TAGS = ("{{ad.id}}", "{{ad.name}}")
# Ads Meta can serve: an edited ad in review keeps serving its old link, and an
# ad "with issues" still serves in the placements that work, but only inside an
# ad set and campaign that are on (the account holds hundreds of old ones in
# campaigns that are off).
LINK_STATUSES = ("ACTIVE", "PENDING_REVIEW", "IN_PROCESS", "WITH_ISSUES")
LINK_FIELDS = ("id,name,effective_status,issues_info,adset{id,name,effective_status},"
               "campaign{id,name,effective_status},creative{url_tags,object_story_spec,asset_feed_spec}")
# Without object_story_spec: Meta refuses it for some Advantage+ creative ads ("For a Degrees of
# freedom enabled ad, the first value of Asset Feed Spec field should match...") and fails the
# whole page of ads with it (B12 and B13, Oct 16 2026).
LINK_FIELDS_SPLIT = ("id,name,effective_status,issues_info,adset{id,name,effective_status},"
                     "campaign{id,name,effective_status},creative{id,url_tags,asset_feed_spec}")
LINK_KEYS = ("ad_name", "adset_id", "adset_name", "campaign_id", "campaign_name", "link")
HOURLY_TTL = 600


def _day_ttl(day: str) -> float:
    """Meta's hourly report for one day: today every 10 minutes, yesterday every half hour
    (late purchases still land), older days every 6 hours."""
    try:
        age = (dt.date.today() - dt.date.fromisoformat(day)).days
    except ValueError:
        return HOURLY_TTL
    return HOURLY_TTL if age <= 0 else 1800 if age == 1 else 6 * 3600
_links: dict[str, Any] = {"at": 0.0, "rows": None, "task": None, "db": None, "error": ""}


def reset_links() -> None:
    """Forget the in-memory read of the ads' links (tests; a new database)."""
    _links.update(at=0.0, rows=None, task=None, db=None, error="")


def _links_state() -> dict:
    if _links["db"] != db.DB_PATH:
        _links.update(at=0.0, rows=None, task=None, db=db.DB_PATH, error="")
    return _links


# After a rejection the owner points an ad at google.com on purpose ("reject save", Oct 6 2026) to
# protect the account. No shopper reaches the store from it, so there is nothing to name there and
# nothing for the link check to say; it is never the ad a sale is named after either.
PLACEHOLDER_HOSTS = ("google.com",)


def placeholder_link(link: Any) -> bool:
    host = attribution._host(link)
    return any(host == h or host.endswith("." + h) for h in PLACEHOLDER_HOSTS)


def creative_link(creative: Any) -> str:
    """The website URL an ad creative sends clicks to ('' for a form, a call, a catalog ad...)."""
    cr = creative if isinstance(creative, dict) else {}
    oss = cr.get("object_story_spec") or {}
    link = ((oss.get("link_data") or {}).get("link")
            or (((oss.get("video_data") or {}).get("call_to_action") or {}).get("value") or {}).get("link"))
    if not link:
        urls = (cr.get("asset_feed_spec") or {}).get("link_urls") or []
        link = next((u.get("website_url") for u in urls if isinstance(u, dict) and u.get("website_url")), "")
    return str(link or "").strip()[:500]


def _link_row(a: dict) -> dict:
    cr = a.get("creative") if isinstance(a.get("creative"), dict) else {}
    tags = str(cr.get("url_tags") or "")
    link = creative_link(cr)
    return {"ad_id": str(a.get("id") or ""), "ad_name": str(a.get("name") or "").strip()[:300],
            "adset_id": str((a.get("adset") or {}).get("id") or ""),
            "adset_name": str((a.get("adset") or {}).get("name") or "").strip()[:300],
            "campaign_id": str((a.get("campaign") or {}).get("id") or ""),
            "campaign_name": str((a.get("campaign") or {}).get("name") or "").strip()[:300],
            "status": str(a.get("effective_status") or ""), "link": link,
            "names_ad": any(t in tags for t in NAMING_TAGS),
            "stand_in": attribution.stand_in_fbclid(link), "placeholder": placeholder_link(link)}


def cached_links() -> list[dict]:
    """The last read of the ads' links (kept in the database across restarts)."""
    state = _links_state()
    if state["rows"] is None:
        try:
            rows = json.loads(db.kv_get("ad_links") or "[]")
        except ValueError:
            rows = []
        state["rows"] = [r for r in rows if isinstance(r, dict) and r.get("ad_id")]
    return state["rows"]


def links_error() -> str:
    """Why the last read of the ads' links failed ('' when it didn't)."""
    return str(_links_state().get("error") or "")


def _load_unnamed() -> dict:
    try:
        seen = json.loads(db.kv_get("ad_links_unnamed") or "{}")
    except ValueError:
        seen = {}
    return {str(k): v for k, v in seen.items() if isinstance(v, dict)} if isinstance(seen, dict) else {}


def note_unnamed(ad_id: str, row: dict, first: float, last: float) -> None:
    """Put on record that an ad's link didn't name it between `first` and `last`."""
    seen = _load_unnamed()
    rec = seen.get(str(ad_id)) or {}
    rec.update({k: str(row.get(k) or "")[:300] for k in LINK_KEYS})
    rec["first"] = min(float(rec.get("first") or first), first)
    rec["last"] = max(float(rec.get("last") or last), last)
    seen[str(ad_id)] = rec
    db.kv_set("ad_links_unnamed", json.dumps(seen))


def _remember_unnamed(rows: list[dict], now: float) -> None:
    """Keep, per ad, when it was first and last seen with a link that doesn't
    name it, so a sale from before the owner fixed the link is still named
    (an edited ad serves its old link while in review, so one in review that
    was on record stays on it)."""
    seen = {k: v for k, v in _load_unnamed().items()
            if now - float(v.get("last") or 0) < LINKS_MEMORY_DAYS * 86400}
    for r in rows:
        if not r["link"] or r.get("placeholder") or (r["names_ad"] and (r["status"] == "ACTIVE" or r["ad_id"] not in seen)):
            continue
        rec = seen.get(r["ad_id"]) or {"first": now}
        rec.update({k: r[k] for k in LINK_KEYS}, last=now)
        seen[r["ad_id"]] = rec
    db.kv_set("ad_links_unnamed", json.dumps(seen))


def unnamed_ads_at(at: float) -> list[dict]:
    """The ads whose link didn't name them when a shopper arrived at `at`: on
    record from a read before and one after (reads are LINKS_TTL apart)."""
    out = []
    for ad_id, v in _load_unnamed().items():
        if placeholder_link(v.get("link")):
            continue                            # recorded before it was known to be a placeholder
        if float(v.get("first") or 0) - LINKS_TTL <= at <= float(v.get("last") or 0) + LINKS_TTL:
            out.append({"ad_id": ad_id, **{k: str(v.get(k) or "") for k in LINK_KEYS}})
    return out


async def _link_ads(acct: str) -> list[dict]:
    """The account's servable ads with their creatives' links. One read when
    Meta allows it; when an ad's creative makes Meta refuse the read, the ads
    again without the field it refuses, then that field per creative (50 at a
    time, one by one where a batch is refused) for the ads whose link isn't
    in the rest. An ad whose link can't be read keeps its tags (names_ad)."""
    params = {"fields": LINK_FIELDS, "limit": 100, "effective_status": json.dumps(list(LINK_STATUSES))}
    try:
        return await _paged(f"act_{acct}/ads", params, ads_token())
    except MetaReadError as e:
        log.info("ad links: full read refused (%s); reading in parts", str(e)[:120])
    ads = await _paged(f"act_{acct}/ads", {**params, "fields": LINK_FIELDS_SPLIT}, ads_token())
    need = [a for a in ads if isinstance(a.get("creative"), dict) and a["creative"].get("id")
            and not creative_link(a["creative"])]
    for i in range(0, len(need), 50):
        chunk = need[i:i + 50]
        ids = [str(a["creative"]["id"]) for a in chunk]
        try:
            got = await _get("", {"ids": ",".join(ids), "fields": "object_story_spec"}, ads_token())
        except MetaReadError:
            got = {}
            for cid in ids:
                try:
                    got[cid] = await _get(cid, {"fields": "object_story_spec"}, ads_token())
                except MetaReadError:
                    pass
        for a in chunk:
            oss = (got.get(str(a["creative"]["id"])) or {}).get("object_story_spec")
            if isinstance(oss, dict):
                a["creative"]["object_story_spec"] = oss
    return ads


async def _read_links() -> list[dict]:
    rows, errors = [], []
    for acct in config.META_AD_ACCOUNT_IDS:
        try:
            for a in await _link_ads(acct):
                if not a.get("id"):
                    continue
                if a.get("effective_status") == "WITH_ISSUES" and (
                        not all((a.get(k) or {}).get("effective_status") == "ACTIVE" for k in ("adset", "campaign"))
                        or any(isinstance(i, dict) and i.get("error_type") == "HARD_ERROR"
                               for i in a.get("issues_info") or [])):
                    continue                    # a rejected ad is disabled whatever its ad set does
                rows.append(_link_row(a))
        except MetaReadError as e:
            errors.append(f"act_{acct}: {e}")
    state = _links_state()
    state["error"] = "; ".join(errors)
    if errors:
        state["at"] = time.time() - LINKS_TTL + 300      # asked again in 5 minutes
        return cached_links()
    state["rows"] = rows
    db.kv_set("ad_links", json.dumps(rows))
    _remember_unnamed(rows, time.time())
    state["at"] = time.time()
    return rows


async def ad_links(wait: float = LINKS_WAIT) -> list[dict]:
    """Every ad Meta can serve right now (active, or edited and in review) with
    the link its clicks get: the ad's, ad set's and campaign's id and name,
    `status`, `link`, `names_ad` (its URL parameters carry the ad's id or
    name) and `stand_in` (an fbclid typed into the link, '' when none). Read
    from Meta at most every LINKS_TTL; a slow Meta gets `wait` seconds, then
    the last read is used and the read finishes in the background. [] when no
    ad account is set up."""
    if not config.META_AD_ACCOUNT_IDS:
        return []
    state = _links_state()
    if time.time() - state["at"] < LINKS_TTL:
        return cached_links()
    loop = asyncio.get_running_loop()
    task = state["task"]
    if task is None or task.done() or task.get_loop() is not loop:
        task = state["task"] = loop.create_task(_read_links())
    try:
        return await asyncio.wait_for(asyncio.shield(task), wait)
    except Exception as e:                      # a timeout or a Meta hiccup: the last read will do
        log.info("ad links: using the last read (%s)", type(e).__name__)
        return cached_links()


async def hourly_ad_purchases(day: str) -> Optional[dict[tuple, dict]]:
    """Meta's own purchases per ad per hour of the ad account's day `day`, by when
    the purchase happened (conversion time): {(ad_id, hour): {"n", "value", and
    the ad's ids and names}}. None when it can't be read."""
    if not config.META_AD_ACCOUNT_IDS:
        return None
    key = f"hourly_purchases:{day}"
    hit = _cached(key)
    if hit is not None:
        return hit
    old = _day_ttl(day) >= 6 * 3600
    if old:                                     # a past day, read before the last restart
        try:
            saved = json.loads(db.kv_get(f"meta:{key}") or "null")
        except ValueError:
            saved = None
        if saved and time.time() - float(saved.get("at") or 0) < 24 * 3600:
            out = {(str(a), int(h)): e for a, h, e in saved.get("rows") or []}
            return _store(key, out, _day_ttl(day))
    out: dict[tuple, dict] = {}
    for acct in config.META_AD_ACCOUNT_IDS:
        try:
            rows = await _paged(f"act_{acct}/insights", {
                "level": "ad", "limit": 500, "action_report_time": "conversion",
                "fields": "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,actions,action_values",
                "breakdowns": "hourly_stats_aggregated_by_advertiser_time_zone",
                "time_range": json.dumps({"since": day, "until": day})}, ads_token())
        except MetaReadError as e:
            log.warning("purchases per hour: %s", e)
            return None
        for r in rows:
            n = purchases(r.get("actions"))
            if not n or not r.get("ad_id"):
                continue
            try:
                hour = int(str(r.get("hourly_stats_aggregated_by_advertiser_time_zone") or "")[:2])
            except ValueError:
                continue
            e = out.setdefault((str(r["ad_id"]), hour), {"n": 0.0, "value": 0.0,
                                                          **{k: str(r.get(k) or "") for k in CATALOG_KEYS}})
            e["n"] += n
            e["value"] += purchases(r.get("action_values"))
    if old:
        # Meta's hourly reports cost its slow-down budget the most: a past day is kept, so
        # every deploy doesn't re-read the 30 days behind the 30-day view at once.
        try:
            db.kv_set(f"meta:{key}", json.dumps({"at": time.time(), "rows": [[a, h, e] for (a, h), e in out.items()]}))
        except Exception:
            log.warning("purchases per hour: could not keep %s", day, exc_info=True)
    return _store(key, out, _day_ttl(day))


async def hourly_link_clicks(day: str, ad_ids: Any) -> dict[str, dict[int, int]]:
    """Link clicks per hour of the ad account's day `day` (YYYY-MM-DD) for
    these ads: {ad_id: {hour: clicks}}, hours in the account's timezone. Meta
    files a click under the hour its ad was shown. {} when it can't be read."""
    ids = sorted({str(a) for a in ad_ids if a})
    if not ids or not config.META_AD_ACCOUNT_IDS:
        return {}
    key = f"hourly:{day}:{','.join(ids)}"
    hit = _cached(key)
    if hit is not None:
        return hit
    out: dict[str, dict[int, int]] = {}
    for acct in config.META_AD_ACCOUNT_IDS:
        try:
            rows = await _paged(f"act_{acct}/insights", {
                "level": "ad", "fields": "ad_id,inline_link_clicks", "limit": 500,
                "breakdowns": "hourly_stats_aggregated_by_advertiser_time_zone",
                "time_range": json.dumps({"since": day, "until": day}),
                "filtering": json.dumps([{"field": "ad.id", "operator": "IN", "value": ids}])}, ads_token())
        except MetaReadError as e:
            log.warning("link clicks per hour: %s", e)
            return {}
        for r in rows:
            try:
                hour = int(str(r.get("hourly_stats_aggregated_by_advertiser_time_zone") or "")[:2])
            except ValueError:
                continue
            clicks = int(_num(r.get("inline_link_clicks")))
            if clicks and r.get("ad_id"):
                hours = out.setdefault(str(r["ad_id"]), {})
                hours[hour] = hours.get(hour, 0) + clicks
    return _store(key, out, _day_ttl(day))


async def account_tz(account_id: str):
    """The ad account's timezone (the store's when it can't be read)."""
    try:
        name = (await account_info(account_id)).get("timezone_name") or ""
        return ZoneInfo(name) if name else config.store_tz()
    except Exception:
        return config.store_tz()


async def dataset_quality(pixel_id: str, token: str) -> dict:
    """Event Match Quality per event: {'Purchase': {'score': 8.1, 'keys': {'email': 98.5, ...}}}."""
    data = await _get("dataset_quality", {"dataset_id": pixel_id,
                                          "fields": "web{event_name,event_match_quality}"}, token)
    out: dict[str, dict] = {}
    for row in data.get("web", []) or []:
        emq = row.get("event_match_quality") or {}
        out[row.get("event_name", "")] = {
            "score": emq.get("composite_score"),
            "keys": {f.get("identifier"): (f.get("coverage") or {}).get("percentage")
                     for f in emq.get("match_key_feedback", []) or [] if isinstance(f, dict)},
        }
    return out


async def dataset_name(pixel_id: str, token: str) -> str:
    key = f"name:{pixel_id}"
    hit = _cached(key)
    if hit is not None:
        return hit
    try:
        name = (await _get(pixel_id, {"fields": "name"}, token)).get("name", "")
    except MetaReadError:
        name = ""
    return _store(key, name, 86400)
