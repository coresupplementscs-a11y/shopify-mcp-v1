"""
Reads from Meta for the hub: ad spend and Meta-attributed purchases per ad,
split into click and view sales (Marketing API insights), Event Match
Quality per dataset (Dataset Quality API) and dataset names. Results are
cached briefly. Nothing here raises to the caller: failures come back as an
`error` string the hub can show (the daily spend series comes back as None).
The token travels in a header, never in a URL, so it can't reach a log line.
"""
import json
import logging
import math
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

_cache: dict[str, tuple[float, Any]] = {}
_client: Optional[httpx.AsyncClient] = None


class MetaReadError(Exception):
    pass


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
        raise MetaReadError(err.get("error_user_msg") or err.get("message") or f"HTTP {resp.status_code}")
    return data if isinstance(data, dict) else {}


def _auth(token: str) -> dict:
    # A header, not ?access_token=: request URLs end up in logs (httpx logs
    # every one at INFO), headers don't.
    return {"Authorization": f"Bearer {token}"}


async def _get(path: str, params: dict, token: str) -> dict:
    try:
        resp = await _http().get(f"{GRAPH}/{config.META_API_VERSION}/{path}", params=params,
                                 headers=_auth(token))
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
