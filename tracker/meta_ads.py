"""
Reads from Meta for the hub: ad spend and Meta-attributed purchases per ad
(Marketing API insights), Event Match Quality per dataset (Dataset Quality
API) and dataset names. Results are cached briefly. Nothing here raises to
the caller: failures come back as an `error` string the hub can show (the
daily spend series comes back as None). The token travels in a header, never
in a URL, so it can't reach a log line.
"""
import json
import logging
import time
from typing import Any, Optional

import httpx

import config

log = logging.getLogger("tracker.meta_ads")

GRAPH = "https://graph.facebook.com"
# Meta reports the same purchases under several action types; take the first present.
PURCHASE_TYPES = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase")
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


def purchases(actions: Any) -> float:
    by = {a.get("action_type"): a.get("value") for a in (actions or []) if isinstance(a, dict)}
    for t in PURCHASE_TYPES:
        if t in by:
            return _num(by[t])
    return 0.0


async def account_info(account_id: str) -> dict:
    key = f"acct:{account_id}"
    hit = _cached(key)
    if hit is not None:
        return hit
    data = await _get(f"act_{account_id}", {"fields": "name,currency,timezone_name"}, ads_token())
    return _store(key, data, 3600)


async def ad_insights(since: str, until: str, ttl: float = 120) -> dict:
    """Per-ad spend and Meta-attributed purchases for the days [since, until]."""
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
                    "time_range": json.dumps({"since": since, "until": until})}, ads_token()):
                rows.append({
                    "account_id": acct,
                    "campaign_id": r.get("campaign_id", ""), "campaign_name": r.get("campaign_name", ""),
                    "adset_id": r.get("adset_id", ""), "adset_name": r.get("adset_name", ""),
                    "ad_id": r.get("ad_id", ""), "ad_name": r.get("ad_name", ""),
                    "spend": _num(r.get("spend")), "impressions": int(_num(r.get("impressions"))),
                    "clicks": int(_num(r.get("clicks"))),
                    "meta_purchases": purchases(r.get("actions")),
                    "meta_value": purchases(r.get("action_values")),
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
