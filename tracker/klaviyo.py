"""Core HQ x Klaviyo (sper/38 section 11, Option A, locked 17 Oct 2026).

Push: every new sale writes what Core HQ knows about it onto the buyer's
Klaviyo profile (where the ad click landed, the ad and its angle, bottles,
him or her, country), and the day 17TRACK says the parcel arrived becomes
rebuild_start_date, the date the post-purchase emails count from.

Read: flow and email stats (sent, opens, clicks, unsubscribes, Klaviyo's own
order count) for the Email tab. Klaviyo counts any order in its window;
Core HQ's email sales are only those whose last click was an email link
(attribution.py), so one sale stays one row.

Nothing here runs without KLAVIYO_PRIVATE_KEY. A failed push is retried by
the backend sync; it never holds up an order or the Purchase sent to Meta.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from typing import Any, Optional

import httpx

import attribution
import config
import database
import db

log = logging.getLogger("tracker.klaviyo")

BASE = "https://a.klaviyo.com"
REVISION = "2026-10-15"
PLACED_ORDER = "Placed Order"
PUSH_DAYS = 3                 # new sales the sweep pushes, or pushes again after a failure
REBUILD_DAYS = 30             # deliveries the sweep turns into rebuild_start_date
SWEEP_MAX = 60                # profile pushes per sweep (Klaviyo allows 750 a minute)
REPORT_DAILY_MAX = 200        # Klaviyo allows 225 flow reports a day
FLOW_STATS = ("recipients", "delivered", "opens_unique", "clicks_unique", "unsubscribes",
              "conversions", "conversion_value")

_client: Optional[httpx.AsyncClient] = None
_cache: dict[str, tuple[float, Any]] = {}


class KlaviyoError(Exception):
    pass


def configured() -> bool:
    return bool(config.KLAVIYO_PRIVATE_KEY)


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30)
    return _client


def _headers() -> dict:
    return {"Authorization": f"Klaviyo-API-Key {config.KLAVIYO_PRIVATE_KEY}", "revision": REVISION,
            "accept": "application/vnd.api+json", "content-type": "application/vnd.api+json"}


async def _call(method: str, path: str, body: Optional[dict] = None, params: Optional[dict] = None) -> dict:
    """One Klaviyo request; waits out a 429 or 503 once (Retry-After). Never logs the key."""
    for attempt in range(2):
        try:
            r = await _http().request(method, BASE + path, headers=_headers(), json=body, params=params)
        except httpx.HTTPError as e:
            raise KlaviyoError(f"network: {type(e).__name__}")
        if r.status_code in (429, 503) and attempt == 0:
            try:
                wait = min(float(r.headers.get("Retry-After") or 5), 30.0)
            except ValueError:
                wait = 5.0
            await asyncio.sleep(wait)
            continue
        if r.status_code >= 400:
            try:
                errs = r.json().get("errors") or []
                detail = "; ".join(str(e.get("detail") or e.get("title") or "") for e in errs)[:200]
            except ValueError:
                detail = ""
            raise KlaviyoError(f"HTTP {r.status_code}{': ' + detail if detail else ''}")
        return r.json() if r.content else {}
    raise KlaviyoError("rate limited")


# --- push: the buyer's profile ----------------------------------------------------------

def _email(order: dict) -> str:
    cust = order.get("customer") or {}
    return str(order.get("email") or order.get("contact_email") or cust.get("email") or "").strip()[:200]


def bottles(order: dict) -> int:
    """SPERMFUEL+ bottles in the order (1, 3, 5...); every unit when no line names it."""
    lines = [li for li in order.get("line_items") or [] if isinstance(li, dict)]
    mine = [li for li in lines if "spermfuel" in str(li.get("title") or "").lower().replace(" ", "")]
    return sum(int(li.get("quantity") or 0) for li in (mine or lines))


def lp_source(rec: dict) -> str:
    """Where the sale's last click landed: 'quiz', the listicle's own name, 'email', or 'PDP'."""
    if not rec.get("meta"):
        return "email" if rec.get("email") else "PDP"
    kind = attribution.landing_kind(rec.get("lp"), rec.get("ids_stripped"))
    if kind == attribution.QUIZ:
        return "quiz"
    if kind == attribution.LISTICLE:
        return str(rec.get("lp") or "listicle")[:80]
    return "PDP"


def profile_properties(order: dict, rec: Optional[dict]) -> dict:
    """What Core HQ writes on the buyer's profile for one new sale (no quiz answers: Marwan's call)."""
    rec = rec if isinstance(rec, dict) else {}
    x = database.enrich_order(order)
    addr = order.get("shipping_address") or order.get("billing_address") or {}
    meta = bool(rec.get("meta"))
    props = {
        "lp_source": lp_source(rec),
        "core_hq_channel": str(rec.get("channel") or ("Meta ads" if meta else "Direct"))[:60],
        "bottle_count": bottles(order),
        "him_or_her": x.get("who") or "",
        "country": str(addr.get("country_code") or "").upper()[:2],
        "last_order_name": str(order.get("name") or "")[:40],
        "last_order_at": str(order.get("created_at") or "")[:40],
    }
    if meta:
        props.update(ad_id=str(rec.get("ad_id") or ""), ad_name=str(rec.get("ad_name") or "")[:200],
                     ad_angle=str(rec.get("adset_name") or "")[:200],      # the ad sets are named by angle
                     ad_campaign=str(rec.get("campaign_name") or "")[:200])
    return props


async def upsert(email: str, properties: dict) -> int:
    """Create or update the profile with these properties (others stay as they are). 201 new, 200 updated."""
    body = {"data": {"type": "profile", "attributes": {"email": email, "properties": properties}}}
    for attempt in range(2):
        try:
            r = await _http().post(BASE + "/api/profile-import", headers=_headers(), json=body)
        except httpx.HTTPError as e:
            raise KlaviyoError(f"network: {type(e).__name__}")
        if r.status_code == 429 and attempt == 0:
            await asyncio.sleep(min(float(r.headers.get("Retry-After") or 2), 30.0))
            continue
        if r.status_code >= 400:
            raise KlaviyoError(f"HTTP {r.status_code}")
        return r.status_code
    raise KlaviyoError("rate limited")


def _done(key: str) -> bool:
    return (db.kv_get(key) or "").startswith("ok")


async def push_order(order_id: str) -> bool:
    """Push one stored new sale to its buyer's profile, once. False when it can't (yet)."""
    if not configured():
        return False
    key = f"klaviyo:order:{order_id}"
    if _done(key):
        return True
    rows = _order_rows([order_id])
    row = rows[0] if rows else None
    if not row or row.get("kind") != "purchase":
        return False
    order = row["order_json"]
    email = _email(order)
    if not email:
        db.kv_set(key, "skip: no email")
        return False
    try:
        code = await upsert(email, profile_properties(order, row.get("attribution")))
    except KlaviyoError as e:
        db.kv_set(key, f"err: {e}"[:200])
        log.warning("Klaviyo: order %s not pushed (%s)", order.get("name") or order_id, e)
        return False
    db.kv_set(key, f"ok {int(time.time())} {code}")
    log.info("Klaviyo: order %s pushed to its buyer's profile", order.get("name") or order_id)
    return True


def _order_rows(order_ids: list[str]) -> list[dict]:
    """Stored orders with their JSON, by id."""
    out = []
    for oid in order_ids:
        r = db.query("SELECT order_id, kind, status, order_json, attribution FROM orders WHERE order_id=?", (str(oid),))
        if not r:
            continue
        r = dict(r[0])
        try:
            r["order_json"] = json.loads(r["order_json"] or "{}")
            r["attribution"] = json.loads(r["attribution"]) if r["attribution"] else None
        except ValueError:
            continue
        out.append(r)
    return out


async def push_delivered(order_id: str, delivered_at: float) -> bool:
    """The day the parcel arrived becomes the buyer's rebuild_start_date (store's timezone), once per order."""
    if not configured():
        return False
    key = f"klaviyo:delivered:{order_id}"
    if _done(key):
        return True
    rows = _order_rows([order_id])
    if not rows or rows[0].get("kind") != "purchase":
        return False
    email = _email(rows[0]["order_json"])
    if not email:
        db.kv_set(key, "skip: no email")
        return False
    day = dt.datetime.fromtimestamp(delivered_at, config.store_tz()).date().isoformat()
    try:
        await upsert(email, {"rebuild_start_date": day, "rebuild_order_name": str(rows[0]["order_json"].get("name") or "")})
    except KlaviyoError as e:
        db.kv_set(key, f"err: {e}"[:200])
        return False
    db.kv_set(key, f"ok {int(time.time())} {day}")
    return True


async def sweep() -> dict:
    """New sales of the last PUSH_DAYS not on Klaviyo yet, and parcels delivered in
    the last REBUILD_DAYS without their rebuild date. Run by the backend sync."""
    if not configured():
        return {"orders": 0, "deliveries": 0}
    now = time.time()
    pushed = delivered = 0
    for r in db.query("SELECT order_id FROM orders WHERE kind='purchase' AND status='sent' AND received_at>=? "
                      "ORDER BY received_at", (now - PUSH_DAYS * 86400,)):
        if pushed >= SWEEP_MAX:
            break
        if not _done(f"klaviyo:order:{r['order_id']}") and not (db.kv_get(f"klaviyo:order:{r['order_id']}") or "").startswith("skip"):
            pushed += 1 if await push_order(str(r["order_id"])) else 0
    for s in db.query("SELECT order_id, MIN(delivered_at) AS at FROM shipments WHERE delivered_at>=? GROUP BY order_id",
                      (now - REBUILD_DAYS * 86400,)):
        if delivered >= SWEEP_MAX:
            break
        k = f"klaviyo:delivered:{s['order_id']}"
        if not _done(k) and not (db.kv_get(k) or "").startswith("skip"):
            delivered += 1 if await push_delivered(str(s["order_id"]), float(s["at"])) else 0
    return {"orders": pushed, "deliveries": delivered}


# --- read: flow stats for the Email tab ----------------------------------------------------

def _cached(key: str) -> Any:
    hit = _cache.get(key)
    return hit[1] if hit and hit[0] > time.time() else None


async def placed_order_metric() -> str:
    """Klaviyo's id for Shopify's Placed Order (what its flow reports count as a conversion)."""
    known = db.kv_get("klaviyo:placed_order_metric")
    if known:
        return known
    cursor: Optional[str] = None
    for _ in range(10):
        data = await _call("GET", "/api/metrics/", params={"page[cursor]": cursor} if cursor else None)
        for m in data.get("data") or []:
            if (m.get("attributes") or {}).get("name") == PLACED_ORDER:
                db.kv_set("klaviyo:placed_order_metric", str(m["id"]))
                return str(m["id"])
        nxt = (data.get("links") or {}).get("next")
        if not nxt:
            break
        cursor = httpx.URL(nxt).params.get("page[cursor]")
        if not cursor:
            break
    raise KlaviyoError("no Placed Order metric (is Shopify connected to Klaviyo?)")


def _report_budget() -> bool:
    """Whether today's flow reports are still under REPORT_DAILY_MAX (Klaviyo allows 225 a day)."""
    day = dt.date.today().isoformat()
    try:
        seen = json.loads(db.kv_get("klaviyo:reports") or "{}")
    except ValueError:
        seen = {}
    n = int(seen.get(day) or 0)
    if n >= REPORT_DAILY_MAX:
        return False
    db.kv_set("klaviyo:reports", json.dumps({day: n + 1}))
    return True


async def flow_report(start: float, end: float, ttl: float) -> list[dict]:
    """Klaviyo's numbers per flow email for [start, end): one row each with
    flow_id, flow_name, message_id, message_name and FLOW_STATS. Cached for
    `ttl` seconds; the last answer stands in when the daily allowance is spent."""
    key = f"flows:{int(start)}:{int(end)}"
    hit = _cached(key)
    if hit is not None:
        return hit
    stale = _cache.get(key)
    if not _report_budget():
        if stale:
            return stale[1]
        raise KlaviyoError("today's Klaviyo report allowance is used up; numbers come back tomorrow")
    tz = config.store_tz()
    body = {"data": {"type": "flow-values-report", "attributes": {
        "statistics": list(FLOW_STATS),
        "timeframe": {"start": dt.datetime.fromtimestamp(start, tz).isoformat(),
                      "end": dt.datetime.fromtimestamp(end - 1, tz).isoformat()},
        "conversion_metric_id": await placed_order_metric(),
        "group_by": ["flow_id", "flow_message_id", "flow_name", "flow_message_name"]}}}
    rows: list[dict] = []
    params: Optional[dict] = None
    for _ in range(10):
        data = await _call("POST", "/api/flow-values-reports/", body=body, params=params)
        for r in ((data.get("data") or {}).get("attributes") or {}).get("results") or []:
            g, st = r.get("groupings") or {}, r.get("statistics") or {}
            rows.append({"flow_id": str(g.get("flow_id") or ""), "flow_name": str(g.get("flow_name") or ""),
                         "message_id": str(g.get("flow_message_id") or ""),
                         "message_name": str(g.get("flow_message_name") or ""),
                         **{k: float(st.get(k) or 0) for k in FLOW_STATS}})
        nxt = (data.get("links") or {}).get("next")
        cursor = httpx.URL(nxt).params.get("page_cursor") if nxt else None
        if not cursor:
            break
        params = {"page_cursor": cursor}
    _cache[key] = (time.time() + ttl, rows)
    return rows


async def tracking_off() -> Optional[int]:
    """How many live flow emails send their links without UTM tags (None when it can't be read). Hourly."""
    hit = _cached("utm_off")
    if hit is not None:
        return hit
    off = 0
    flows = await _call("GET", "/api/flows/", params={"fields[flow]": "name,status"})
    for f in flows.get("data") or []:
        if (f.get("attributes") or {}).get("status") != "live":
            continue
        acts = await _call("GET", f"/api/flows/{f['id']}/flow-actions/")
        for a in acts.get("data") or []:
            d = (a.get("attributes") or {}).get("definition") or {}
            data = d.get("data") or {}
            msg = data.get("message") if isinstance(data.get("message"), dict) else {}
            if d.get("type") == "send-email" and not (msg.get("add_tracking_params") or data.get("add_tracking_params")):
                off += 1
    _cache["utm_off"] = (time.time() + 3600, off)
    return off
