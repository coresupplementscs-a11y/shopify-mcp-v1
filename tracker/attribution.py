"""
Which Meta ad a visit or a sale came from.

Ads carry URL parameters (utm_* and ad_id/adset_id/campaign_id, set once in
Ads Manager). The storefront pixel remembers the last Meta ad link a browser
arrived on; a sale is credited to that ad when it was seen within
ATTRIBUTION_WINDOW_DAYS. Shopify's own landing_site on the order is the
fallback. An fbclid alone proves an ad click but not which ad.

Like Triple Whale's last click with assists: the pixel also keeps a short
history of the Meta ads each browser arrived from. Other ads clicked earlier
in the window are listed on the sale as assists; they never take its credit.
"""
import datetime as dt
import json
import math
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import config

AD_KEYS = ("ad_id", "adset_id", "campaign_id", "utm_source", "utm_medium",
           "utm_campaign", "utm_term", "utm_content", "utm_id")
META_SOURCES = {"facebook", "fb", "meta", "instagram", "ig", "an", "msg", "threads"}
HISTORY_MAX = 10                   # Meta ad arrivals remembered per browser
REPEAT_SECONDS = 30 * 60           # the same ad again this soon is the same visit
ASSISTS_MAX = 5
VISIT_KEYS = ("ad_id", "ad_name", "adset_name", "campaign_name")


def _query(url: Any) -> dict[str, str]:
    try:
        q = parse_qs(urlparse(str(url or "")).query)
    except (TypeError, ValueError):
        return {}
    return {k: (v[0] if v else "") for k, v in q.items()}


def ad_params_from_url(url: Any) -> dict:
    """The Meta ad identifiers in a landing URL, or {} when it isn't a Meta ad link."""
    q = _query(url)
    params = {k: q[k].strip()[:300] for k in AD_KEYS if q.get(k, "").strip()}
    click = bool(q.get("fbclid", "").strip())
    source = params.get("utm_source", "").lower()
    if not (click or params.get("ad_id") or source in META_SOURCES):
        return {}
    if click:
        params["fbclid"] = "1"                      # presence only; the value lives in fbc
    return params


def _credit(params: dict, source: str, seen_at: Optional[float]) -> dict:
    return {
        "meta": True,
        "source": source,
        "ad_id": params.get("ad_id", ""),
        "adset_id": params.get("adset_id", ""),
        "campaign_id": params.get("campaign_id", "") or params.get("utm_id", ""),
        "ad_name": params.get("utm_content", ""),
        "adset_name": params.get("utm_term", ""),
        "campaign_name": params.get("utm_campaign", ""),
        "seen_at": seen_at,
    }


def click_time(fbc: Any) -> Optional[float]:
    """When the ad was clicked, from an fbc of the form fb.1.<ms>.<fbclid>."""
    parts = str(fbc or "").split(".")
    if len(parts) < 4:
        return None
    try:
        return int(parts[2]) / 1000
    except ValueError:
        return None


def ad_visit(params: dict, at: float) -> Optional[dict]:
    """One Meta ad arrival for a browser's click history, or None when the link
    doesn't say which ad (an fbclid alone): that can't be named as an assist."""
    visit = {"ad_id": params.get("ad_id", ""), "ad_name": params.get("utm_content", ""),
             "adset_name": params.get("utm_term", ""), "campaign_name": params.get("utm_campaign", ""),
             "at": float(at)}
    return visit if visit["ad_id"] or visit["ad_name"] else None


def ad_history(raw: Any) -> list[dict]:
    """A stored click history (JSON text or a list), oldest first. A malformed
    entry is dropped rather than breaking the attribution of a sale."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw else []
        except ValueError:
            return []
    out = []
    for v in raw if isinstance(raw, list) else []:
        if not isinstance(v, dict):
            continue
        try:
            at = float(v.get("at"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(at):
            out.append({**{k: str(v.get(k) or "")[:300] for k in VISIT_KEYS}, "at": at})
    out.sort(key=lambda v: v["at"])
    return out


def same_ad(a: dict, b: dict) -> bool:
    """By ad id when both carry one, else by ad name (ignoring case)."""
    if a.get("ad_id") and b.get("ad_id"):
        return str(a["ad_id"]) == str(b["ad_id"])
    name = str(a.get("ad_name") or "").strip().lower()
    return bool(name) and name == str(b.get("ad_name") or "").strip().lower()


def add_ad_visit(raw: Any, visit: dict) -> list[dict]:
    """The click history with one more arrival, newest HISTORY_MAX kept. The
    same ad again within REPEAT_SECONDS (a reload, the next page, several
    pixel events for one page) is the same visit and is not added twice."""
    history = ad_history(raw)
    if any(same_ad(v, visit) and abs(visit["at"] - v["at"]) < REPEAT_SECONDS for v in history):
        return history
    history.append(visit)
    history.sort(key=lambda v: v["at"])
    return history[-HISTORY_MAX:]


def _assists(credit: dict, sess: Optional[dict], order_ts: float, window: float) -> list[dict]:
    """Other ads the buyer arrived from earlier in the window, newest first,
    each once. They helped; the sale stays with the credited (last) ad, which
    is never its own assist."""
    latest = credit.get("seen_at") or order_ts
    out: list[dict] = []
    for v in reversed(ad_history((sess or {}).get("ad_history"))):
        if not order_ts - window <= v["at"] <= latest:
            continue
        if same_ad(v, credit) or any(same_ad(v, o) for o in out):
            continue
        out.append(v)
        if len(out) == ASSISTS_MAX:
            break
    return out


def _with_assists(credit: dict, sess: Optional[dict], order_ts: float, window: float) -> dict:
    # Only when there are any: most sales have none, and their record stays as before.
    helped = _assists(credit, sess, order_ts, window)
    if helped:
        credit["assists"] = helped
    return credit


def order_attribution(order: dict, sess: dict, click: Any) -> dict:
    """Credit a sale to a Meta ad. `click` is the fbc the Purchase carried (or
    just whether it carried one). An fbc cookie lives for 90 days, so a click
    older than the window is an earlier visit, not what brought this sale.
    Earlier ads from the browser's click history ride along as `assists`."""
    try:
        order_ts = dt.datetime.fromisoformat(str(order.get("created_at")).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        order_ts = time.time()
    window = config.ATTRIBUTION_WINDOW_DAYS * 86400
    if isinstance(click, str):
        clicked_at = click_time(click)
        click = bool(click) and (clicked_at is None or order_ts - clicked_at <= window)
    click = bool(click)
    raw = sess.get("ad_params") if sess else None
    seen = sess.get("ad_seen_at") if sess else None
    if raw and seen and 0 <= order_ts - float(seen) <= window:
        try:
            params = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except ValueError:
            params = {}
        if params:
            credit = _credit(params, "browser", float(seen))
            credit["click"] = click or params.get("fbclid") == "1"
            return _with_assists(credit, sess, order_ts, window)
    landing = ad_params_from_url(order.get("landing_site") or "")
    if landing:
        credit = _credit(landing, "landing_page", None)
        credit["click"] = click or landing.get("fbclid") == "1"
        return _with_assists(credit, sess, order_ts, window)
    return {"meta": click, "source": "click_id" if click else "", "click": click}


_SEPARATORS = " -\u2013\u2014|:_"             # space, hyphen, en/em dash, pipe, colon, underscore
# The variant word must start a word ((?<![^\W_]): not right after a letter or
# digit), so "UGC Brad 2" stays whole instead of becoming "UGC Br" + "ad 2".
_VARIANT = re.compile(r"[\s\-\u2013\u2014|:_]*(?:(?<![^\W_])(?:ad|v|var|variation|version)|#)\s*\d+\s*$",
                      re.IGNORECASE)


def family(ad_name: str) -> str:
    """Group creatives by batch: 'B2 Statics - Ad 3' -> 'B2 Statics'."""
    name = _VARIANT.sub("", ad_name or "").strip(_SEPARATORS)
    return name or (ad_name or "")
