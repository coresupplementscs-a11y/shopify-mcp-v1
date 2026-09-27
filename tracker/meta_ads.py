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
import json
import logging
import math
import re
import time
from typing import Any, Optional

import httpx

import config

log = logging.getLogger("tracker.meta_ads")

GRAPH = "https://graph.facebook.com"
# Meta reports the same purchases under several action types; take the first present.
PURCHASE_TYPES = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase")
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


def _check(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400 or (isinstance(data, dict) and "error" in data):
        err = data.get("error") if isinstance(data, dict) else None
        if not isinstance(err, dict):            # a proxy in front of Meta: {"error": "Bad gateway"}
            err = {"message": str(err)[:300]} if err else {}
        raise MetaReadError(err.get("error_user_msg") or err.get("message") or f"HTTP {resp.status_code}",
                            code=err.get("code"))
    return data if isinstance(data, dict) else {}


def _auth(token: str) -> dict:
    # A header, not ?access_token=: request URLs end up in logs (httpx logs
    # every one at INFO), headers don't.
    return {"Authorization": f"Bearer {token}"}


async def _get(path: str, params: dict, token: str, timeout: Any = httpx.USE_CLIENT_DEFAULT) -> dict:
    try:
        resp = await _http().get(f"{GRAPH}/{config.META_API_VERSION}/{path}", params=params,
                                 headers=_auth(token), timeout=timeout)
    except httpx.HTTPError as e:
        raise MetaReadError(f"network: {type(e).__name__}")
    return _check(resp)


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
        try:
            data = _check(await _http().get(url, headers=_auth(token)))
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


async def ad_insights(since: str, until: str, ttl: float = 120) -> dict:
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
                    **_split(r),
                })
        except MetaReadError as e:
            errors.append(f"act_{acct}: {e}")
    result = {"connected": not errors, "rows": rows, "currency": currency, "accounts": accounts,
              "error": "; ".join(errors)}
    return _store(key, result, ttl if not errors else 60)


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
