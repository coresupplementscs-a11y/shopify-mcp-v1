"""
Which Meta ad a visit or a sale came from.

Ads carry URL parameters (utm_* and ad_id/adset_id/campaign_id, set once in
Ads Manager). The storefront pixel remembers the last Meta ad link a browser
arrived on; a sale is credited to that ad when it was seen within
ATTRIBUTION_WINDOW_DAYS. Shopify's own landing_site on the order is the
fallback. An fbclid alone proves an ad click but not which ad.
"""
import datetime as dt
import json
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import config

AD_KEYS = ("ad_id", "adset_id", "campaign_id", "utm_source", "utm_medium",
           "utm_campaign", "utm_term", "utm_content", "utm_id")
META_SOURCES = {"facebook", "fb", "meta", "instagram", "ig", "an", "msg", "threads"}


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


def order_attribution(order: dict, sess: dict, click: Any) -> dict:
    """Credit a sale to a Meta ad. `click` is the fbc the Purchase carried (or
    just whether it carried one). An fbc cookie lives for 90 days, so a click
    older than the window is an earlier visit, not what brought this sale."""
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
            return credit
    landing = ad_params_from_url(order.get("landing_site") or "")
    if landing:
        credit = _credit(landing, "landing_page", None)
        credit["click"] = click or landing.get("fbclid") == "1"
        return credit
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
