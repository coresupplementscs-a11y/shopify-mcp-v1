"""
The Backend tab: deliveries, refunds and chargebacks, after the sale.

Every half hour `sync` reads the store's recent orders from Shopify (their
fulfilments with tracking numbers, their refunds, their shipping country and
city) and the store's Shopify Payments disputes, and keeps them in the
tracker's database. With a 17TRACK API key (TRACK17_KEY) each tracking number
is registered with 17TRACK once and then asked about until it is delivered,
so the hub can say where every parcel is, how long delivery takes per
country, and which parcels are stuck before they turn into chargebacks.

The 17TRACK Shopify app can also push each parcel's status into the order
itself (its "Order Status Auto-push"); those come free with the order
listing, so a parcel is followed that way when no API key is set (or
until 17TRACK's own, finer events arrive for it).

`overview` turns that into the tab's numbers; `attention` is the list of
things that need a hand today (a dispute to answer, a parcel with no carrier
update for a week, an order not shipped after three days). Nothing here is
ever sent anywhere: Shopify and 17TRACK are only read (17TRACK's register
call tells it which numbers to follow). No names, emails or addresses are
stored or shown: the shipping country and city only.
"""
import asyncio
import datetime as dt
import logging
import re
import time
from typing import Any, Optional

import httpx

import config
import database
import db
import shopify

log = logging.getLogger("backend")

ORDER_FIELDS = ("id,name,created_at,cancelled_at,test,financial_status,total_price,currency,source_name,tags,"
                "fulfillments,refunds,shipping_address,billing_address,customer,client_details,line_items")
ABANDONED_FIELDS = ("id,token,created_at,updated_at,completed_at,total_price,currency,email,landing_site,"
                    "shipping_address,billing_address,shipping_lines,line_items,client_details")
FIRST_SYNC_DAYS = 90            # the first sync reads this far back
KEEP_DAYS = 120                 # orders older than this leave the backend's tables
RESYNC_OVERLAP = 3600           # each sync re-reads orders updated this long before the last one

# 17TRACK (api.17track.net, v2.4): one security key, 40 numbers a call, 3 calls a second.
TRACK17 = "https://api.17track.net/track/v2.4"
TRACK17_BATCH = 40
TRACK17_PAUSE = 0.4
TRACK17_CHECK_EVERY = 45 * 60   # a parcel's status is asked for at most this often
TRACK17_CHECKS_PER_SYNC = 12    # batches a sync asks about (480 parcels)
ALREADY_REGISTERED = -18019901
# Shopify's carrier names at the store -> 17TRACK's carrier codes (its auto-detect misses the small ones).
CARRIER_CODES = {"china post": 3011, "chinapost": 3011, "hua_han": 190003, "huahan": 190003, "hua han": 190003,
                 "wanbexpress": 190086, "wanb express": 190086, "wanb": 190086, "lingxun": 190360,
                 "jy": 190365, "sdh": 190744, "tdpacket": 191829, "td packet": 191829, "td": 191829}
DONE = ("Delivered", "Expired", "Returned")   # 17TRACK statuses after which a parcel is left alone
# Shopify's shipment_status (what the 17TRACK app pushes into the order) -> the same words 17TRACK uses.
SHOPIFY_STATUS = {"delivered": ("Delivered", "Delivered"), "failure": ("DeliveryFailure", "Delivery failed"),
                  "attempted_delivery": ("DeliveryFailure", "Delivery attempted"), "in_transit": ("InTransit", "In transit"),
                  "out_for_delivery": ("OutForDelivery", "Out for delivery"),
                  "ready_for_pickup": ("AvailableForPickup", "Ready for pickup"), "confirmed": ("InfoReceived", "Confirmed by carrier"),
                  "label_printed": ("InfoReceived", "Label printed"), "label_purchased": ("InfoReceived", "Label purchased")}
FROM_SHOPIFY = 2                # `registered` when a parcel's status comes from Shopify, not 17TRACK's API
FAILED = ("DeliveryFailure", "Exception", "Expired", "Returned")

# What needs a hand.
STUCK_DAYS = 7                  # no carrier update for this long
LATE_DAYS = 20                  # shipped this long ago and still not delivered
NOT_SCANNED_DAYS = 5            # shipped this long ago and the carrier has never scanned it
UNFULFILLED_DAYS = 3            # paid this long ago and not shipped
DISPUTE_URGENT_DAYS = 3         # a response due within this many days
OPEN_DISPUTE = ("needs_response", "under_review")
WON = ("won", "prevented", "charge_refunded")
LOST = ("lost", "accepted")
ATTENTION_MAX = 60

_PII = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|\+?\(?\d[\d\s().-]{5,}\d")

_client: Optional[httpx.AsyncClient] = None
_state: dict[str, Any] = {"last": 0.0, "error": "", "running": False, "track17": ""}


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


# --- time and text helpers ---------------------------------------------------------

def _ts(value: Any) -> Optional[float]:
    """An ISO time from Shopify or 17TRACK as an epoch, or None."""
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def _money(v: Any) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _s(v: Any, n: int) -> str:
    return str(v or "").strip()[:n]


def clean_note(note: Any) -> str:
    """A refund's staff note without any email or phone number that got typed into it."""
    return re.sub(r"\s{2,}", " ", _PII.sub("", str(note or ""))).strip()[:140]


def refund_kind(note: Any) -> str:
    """What a refund was for, from its note: the store's Ethoca alerts are refunds that stop a chargeback."""
    n = str(note or "").lower()
    if "ethoca" in n or "alert" in n or "chargeback" in n or "dispute" in n:
        return "Chargeback alert"
    if "cancel" in n:
        return "Cancelled"
    if "not receiv" in n or "never arrived" in n or "lost" in n or "didn't arrive" in n or "didnt arrive" in n:
        return "Not received"
    if "return" in n:
        return "Return"
    if "duplicate" in n or "double" in n or "twice" in n:
        return "Duplicate"
    if "damag" in n or "broken" in n or "leak" in n:
        return "Damaged"
    return "Other" if n.strip() else "No note"


def carrier_code(name: Any) -> Optional[int]:
    key = re.sub(r"[^a-z_ ]", "", str(name or "").lower()).strip()
    return CARRIER_CODES.get(key) or CARRIER_CODES.get(key.replace(" ", ""))


# --- Shopify -> the backend's tables -------------------------------------------------

def _store_order(o: dict, now: float) -> None:
    oid = str(o.get("id") or "")
    created = _ts(o.get("created_at"))
    if not oid or created is None:
        return
    addr = o.get("shipping_address") or o.get("billing_address") or {}
    fulfils = [f for f in o.get("fulfillments") or [] if isinstance(f, dict) and f.get("status") in (None, "success")]
    fulfilled_at = min((t for t in (_ts(f.get("created_at")) for f in fulfils) if t), default=None)
    x = database.enrich_order(o)
    db.run("INSERT INTO backend_orders (order_id, order_name, created_at, country, city, total, currency, "
           "financial_status, cancelled_at, fulfilled_at, test, updated_at, device, app, qty, product, kind, who, faith, "
           "hour, weekday) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
           "ON CONFLICT(order_id) DO UPDATE SET order_name=excluded.order_name, country=excluded.country, "
           "city=excluded.city, total=excluded.total, currency=excluded.currency, "
           "financial_status=excluded.financial_status, cancelled_at=excluded.cancelled_at, "
           "fulfilled_at=excluded.fulfilled_at, test=excluded.test, updated_at=excluded.updated_at, "
           "device=excluded.device, app=excluded.app, qty=excluded.qty, product=excluded.product, kind=excluded.kind, "
           "who=excluded.who, faith=excluded.faith, hour=excluded.hour, weekday=excluded.weekday",
           (oid, _s(o.get("name"), 40), created, _s(addr.get("country_code"), 2).upper(), _s(addr.get("city"), 60),
            _money(o.get("total_price")), _s(o.get("currency"), 3).upper(), _s(o.get("financial_status"), 30),
            _ts(o.get("cancelled_at")), fulfilled_at, 1 if o.get("test") else 0, now, x["device"], x["app"], x["qty"],
            x["product"], x["kind"], x["who"], x["faith"], x["hour"], x["weekday"]))
    numbers = set()
    for f in fulfils:
        nums = [n for n in (f.get("tracking_numbers") or [f.get("tracking_number")]) if n]
        for n in nums:
            n = _s(n, 60)
            numbers.add(n)
            db.run("INSERT INTO shipments (tracking_number, order_id, carrier, carrier_code, fulfilled_at, updated_at) "
                   "VALUES (?,?,?,?,?,?) ON CONFLICT(tracking_number) DO UPDATE SET order_id=excluded.order_id, "
                   "carrier=excluded.carrier, carrier_code=COALESCE(shipments.carrier_code, excluded.carrier_code), "
                   "fulfilled_at=excluded.fulfilled_at, updated_at=excluded.updated_at",
                   (n, oid, _s(f.get("tracking_company"), 60), carrier_code(f.get("tracking_company")),
                    _ts(f.get("created_at")) or created, now))
            _apply_shopify_status(n, f, now)
    if numbers:
        db.run(f"DELETE FROM shipments WHERE order_id=? AND tracking_number NOT IN ({','.join('?' * len(numbers))})",
               (oid, *numbers))
    else:
        db.run("DELETE FROM shipments WHERE order_id=?", (oid,))
    for r in o.get("refunds") or []:
        rid, at = str(r.get("id") or ""), _ts(r.get("created_at") or r.get("processed_at"))
        if not rid or at is None:
            continue
        amount = sum(_money(t.get("amount")) for t in r.get("transactions") or []
                     if t.get("kind") == "refund" and t.get("status") in (None, "success"))
        db.run("INSERT INTO refunds (refund_id, order_id, created_at, amount, currency, note) VALUES (?,?,?,?,?,?) "
               "ON CONFLICT(refund_id) DO UPDATE SET amount=excluded.amount, note=excluded.note",
               (rid, oid, at, round(amount, 2), _s(o.get("currency"), 3).upper(), clean_note(r.get("note"))))


def _apply_shopify_status(number: str, f: dict, now: float) -> None:
    """The status the 17TRACK app pushed into the order's fulfilment, for a
    parcel 17TRACK's API isn't following (its own events are finer)."""
    status_, words = SHOPIFY_STATUS.get(str(f.get("shipment_status") or ""), ("", ""))
    if not status_:
        return
    at = _ts(f.get("updated_at")) or now
    db.run("UPDATE shipments SET registered=?, status=?, sub_status=?, last_event=?, last_event_at=?, "
           "delivered_at=CASE WHEN ?='Delivered' THEN COALESCE(delivered_at, ?) ELSE delivered_at END, "
           "updated_at=? WHERE tracking_number=? AND registered IN (0, -1, ?)",
           (FROM_SHOPIFY, status_, _s(f.get("shipment_status"), 40), words, at, status_, at, now, number, FROM_SHOPIFY))


def _store_dispute(d: dict, now: float) -> None:
    did, oid = str(d.get("id") or ""), str(d.get("order_id") or "")
    if not did or not oid:
        return
    db.run("INSERT INTO disputes (dispute_id, order_id, type, status, reason, network_reason, amount, currency, "
           "initiated_at, evidence_due_by, evidence_sent_on, finalized_on, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
           "ON CONFLICT(dispute_id) DO UPDATE SET status=excluded.status, reason=excluded.reason, "
           "amount=excluded.amount, evidence_due_by=excluded.evidence_due_by, evidence_sent_on=excluded.evidence_sent_on, "
           "finalized_on=excluded.finalized_on, updated_at=excluded.updated_at",
           (did, oid, _s(d.get("type"), 20), _s(d.get("status"), 30), _s(d.get("reason"), 60),
            _s(d.get("network_reason_code"), 20), _money(d.get("amount")), _s(d.get("currency"), 3).upper(),
            _ts(d.get("initiated_at")) or now, _ts(d.get("evidence_due_by")), _ts(d.get("evidence_sent_on")),
            _ts(d.get("finalized_on")), now))


async def sync() -> dict:
    """Read what changed at Shopify since the last sync (everything from the
    last FIRST_SYNC_DAYS the first time), then ask 17TRACK about the parcels."""
    if _state["running"]:
        return status()
    _state["running"] = True
    now = time.time()
    try:
        last = db.kv_get("backend_synced_at")
        if last and db.query("SELECT 1 FROM backend_orders WHERE kind IS NULL LIMIT 1"):
            last = None                             # columns added since the last full read: read everything once more
        params: dict[str, Any] = {"status": "any", "limit": 250, "fields": ORDER_FIELDS}
        if last:
            params["updated_at_min"] = _iso(float(last) - RESYNC_OVERLAP)
        else:
            params["created_at_min"] = _iso(now - FIRST_SYNC_DAYS * 86400)
        orders = await shopify.list_orders(params)
        for o in orders:
            _store_order(o, now)
        try:
            disputes = await shopify.list_disputes()
            for d in disputes:
                _store_dispute(d, now)
            await _orders_of_disputes(disputes, now)
        except httpx.HTTPStatusError as e:         # the app may lack the disputes scope: deliveries still work
            log.warning("backend: disputes could not be read (%s)", e.response.status_code)
            _state["error"] = f"Shopify would not show the disputes ({e.response.status_code})."
        else:
            _state["error"] = ""
        try:
            await _sync_abandoned(db.kv_get("abandoned_synced_at"), now)
            db.kv_set("abandoned_synced_at", str(now))
        except Exception as e:                      # the checkouts are the Database tab's; the rest stands
            log.warning("backend: abandoned checkouts could not be read (%s)", type(e).__name__)
        db.kv_set("backend_synced_at", str(now))
        cutoff = now - KEEP_DAYS * 86400          # old orders go, except the ones a dispute still points at
        old = "SELECT order_id FROM backend_orders WHERE created_at<? AND order_id NOT IN (SELECT order_id FROM disputes)"
        db.run(f"DELETE FROM shipments WHERE order_id IN ({old})", (cutoff,))
        db.run(f"DELETE FROM refunds WHERE order_id IN ({old})", (cutoff,))
        db.run(f"DELETE FROM backend_orders WHERE order_id IN ({old})", (cutoff,))
        db.run("DELETE FROM abandoned WHERE created_at<?", (cutoff,))
        db.run("DELETE FROM quiz_events WHERE at<?", (now - database.QUIZ_KEEP_DAYS * 86400,))
        _state["last"] = now
        log.info("backend: %d orders read from Shopify", len(orders))
    except Exception as e:
        _state["error"] = f"Shopify could not be read ({type(e).__name__})."
        log.warning("backend sync: %s: %s", type(e).__name__, e)
    finally:
        _state["running"] = False
    try:
        await track()
    except Exception as e:
        _state["track17"] = f"17TRACK could not be read ({type(e).__name__})."
        log.warning("backend 17TRACK: %s: %s", type(e).__name__, e)
    return status()


async def _sync_abandoned(last: Optional[str], now: float) -> None:
    """The store's abandoned checkouts: everything from the last
    ABANDONED_FIRST_DAYS the first time, then what changed since."""
    params: dict[str, Any] = {"limit": 250, "fields": ABANDONED_FIELDS}
    if last:
        params["updated_at_min"] = _iso(float(last) - RESYNC_OVERLAP)
    else:
        params["created_at_min"] = _iso(now - database.ABANDONED_FIRST_DAYS * 86400)
    for c in await shopify.list_abandoned(params):
        database.store_abandoned(c, now)


async def _orders_of_disputes(disputes: list[dict], now: float) -> None:
    """A dispute can be about an order older than the window: read that order
    too, so the dispute shows its name and country."""
    known = {r["order_id"] for r in db.query("SELECT order_id FROM backend_orders")}
    for oid in {str(d.get("order_id") or "") for d in disputes} - known - {""}:
        try:
            _store_order(await shopify.get_order(oid), now)
        except Exception as e:                  # one missing order must not stop the sync
            log.warning("backend: order %s of a dispute could not be read (%s)", oid, type(e).__name__)
            break


# --- 17TRACK ------------------------------------------------------------------------

async def _t17(path: str, body: list[dict]) -> dict:
    resp = await _http().post(f"{TRACK17}/{path}", json=body,
                              headers={"17token": config.TRACK17_KEY, "Content-Type": "application/json"})
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") not in (0, None):
        raise RuntimeError(f"17TRACK answered {data.get('code')}: {str(data.get('message') or '')[:120]}")
    return data.get("data") or {}


def _batches(rows: list[dict]) -> list[list[dict]]:
    return [rows[i:i + TRACK17_BATCH] for i in range(0, len(rows), TRACK17_BATCH)]


def _number_body(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        item: dict[str, Any] = {"number": r["tracking_number"]}
        if r.get("carrier_code"):
            item["carrier"] = int(r["carrier_code"])
        out.append(item)
    return out


async def track() -> None:
    """Register the parcels 17TRACK doesn't know yet, then ask it about the
    ones still on their way. Only parcels shipped in the last
    TRACK17_BACKFILL_DAYS are registered (each costs one of its quota)."""
    if not config.TRACK17_KEY:
        _state["track17"] = "off"
        return
    now = time.time()
    fresh = db.query("SELECT tracking_number, carrier_code FROM shipments WHERE registered IN (0, ?) AND fulfilled_at>=? "
                     f"AND (status IS NULL OR status NOT IN ({','.join('?' * len(DONE))})) ORDER BY fulfilled_at",
                     (FROM_SHOPIFY, now - config.TRACK17_BACKFILL_DAYS * 86400, *DONE))
    for batch in _batches(fresh):
        data = await _t17("register", _number_body(batch))
        for a in data.get("accepted") or []:
            db.run("UPDATE shipments SET registered=1, carrier_code=COALESCE(?, carrier_code), reg_error='', updated_at=? "
                   "WHERE tracking_number=?", (a.get("carrier"), now, str(a.get("number"))))
        for r in data.get("rejected") or []:
            err = r.get("error") or {}
            if err.get("code") == ALREADY_REGISTERED:
                db.run("UPDATE shipments SET registered=1, reg_error='', updated_at=? WHERE tracking_number=?",
                       (now, str(r.get("number"))))
            else:
                db.run("UPDATE shipments SET registered=-1, reg_error=?, updated_at=? WHERE tracking_number=?",
                       (_s(err.get("message"), 120) or f"code {err.get('code')}", now, str(r.get("number"))))
        await asyncio.sleep(TRACK17_PAUSE)
    due = db.query("SELECT tracking_number, carrier_code FROM shipments WHERE registered=1 AND "
                   f"(status IS NULL OR status NOT IN ({','.join('?' * len(DONE))})) AND "
                   "(checked_at IS NULL OR checked_at<?) ORDER BY checked_at", (*DONE, now - TRACK17_CHECK_EVERY))
    for batch in _batches(due)[:TRACK17_CHECKS_PER_SYNC]:
        data = await _t17("gettrackinfo", _number_body(batch))
        for a in data.get("accepted") or []:
            _apply_track(str(a.get("number")), a.get("track_info") or {}, now)
        for r in data.get("rejected") or []:
            db.run("UPDATE shipments SET checked_at=?, reg_error=? WHERE tracking_number=?",
                   (now, _s((r.get("error") or {}).get("message"), 120), str(r.get("number"))))
        await asyncio.sleep(TRACK17_PAUSE)
    _state["track17"] = "on"


def _apply_track(number: str, info: dict, now: float) -> None:
    latest = info.get("latest_status") or {}
    event = info.get("latest_event") or {}
    metrics = info.get("time_metrics") or {}
    status_ = _s(latest.get("status"), 30) or "NotFound"
    delivered_at = None
    for m in info.get("milestone") or []:
        if isinstance(m, dict) and m.get("key_stage") == "Delivered" and m.get("time_iso"):
            delivered_at = _ts(m.get("time_iso"))
    if status_ == "Delivered" and delivered_at is None:
        delivered_at = _ts(event.get("time_iso")) or now
    transit = metrics.get("days_of_transit")
    try:
        transit = float(transit) if transit not in (None, "") else None
    except (TypeError, ValueError):
        transit = None
    db.run("UPDATE shipments SET status=?, sub_status=?, last_event=?, last_event_at=?, delivered_at=?, "
           "transit_days=?, checked_at=?, updated_at=? WHERE tracking_number=?",
           (status_, _s(latest.get("sub_status"), 40), _s(event.get("description"), 120), _ts(event.get("time_iso")),
            delivered_at, transit, now, now, number))


def status() -> dict:
    """What the tab says at the bottom: when it last synced and whether 17TRACK is on."""
    last = _state["last"] or (float(db.kv_get("backend_synced_at") or 0))
    tracked = db.query("SELECT COUNT(*) AS n FROM shipments WHERE registered=1")[0]["n"]
    pushed = db.query("SELECT COUNT(*) AS n FROM shipments WHERE registered=? AND fulfilled_at>=?",
                      (FROM_SHOPIFY, time.time() - 30 * 86400))[0]["n"]
    return {"synced_at": last or None, "running": _state["running"], "error": _state["error"],
            "track17": _state["track17"] or ("on" if config.TRACK17_KEY else "off"), "tracked": tracked,
            "pushed": pushed, "every": config.BACKEND_SYNC_SECONDS}


def shopify_pushes() -> bool:
    """Whether the 17TRACK app is pushing statuses into the store's orders (any
    parcel of the last 30 days got one). Until it does, a parcel without a
    status is simply not followed, not "never scanned"."""
    return bool(db.query("SELECT 1 FROM shipments WHERE registered=? AND fulfilled_at>=? LIMIT 1",
                         (FROM_SHOPIFY, time.time() - 30 * 86400)))


# --- the tab's numbers -----------------------------------------------------------

def _days(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return round((b - a) / 86400, 1) if a and b and b >= a else None


def ship_state(s: dict, now: float, pushes: bool = False) -> str:
    """One word for a parcel: delivered, failed, stuck, late, unscanned,
    transit, untracked. `pushes`: the 17TRACK app is pushing statuses into
    Shopify, so a parcel still without one after NOT_SCANNED_DAYS was never
    scanned. A Shopify status only changes at milestones, so "stuck" (no
    carrier event for a week) is judged on 17TRACK's own events alone."""
    status_ = s.get("status") or ""
    if status_ == "Delivered":
        return "delivered"
    if status_ in FAILED:
        return "failed"
    shipped = s.get("fulfilled_at") or 0
    if s.get("registered") not in (1, FROM_SHOPIFY):
        if pushes and s.get("registered") == 0 and now - shipped >= NOT_SCANNED_DAYS * 86400:
            return "unscanned"
        return "untracked"
    if status_ in ("", "NotFound") and now - shipped >= NOT_SCANNED_DAYS * 86400:
        return "unscanned"
    if s.get("registered") == 1 and s.get("last_event_at") and now - s["last_event_at"] >= STUCK_DAYS * 86400:
        return "stuck"
    if now - shipped >= LATE_DAYS * 86400:
        return "late"
    return "transit"


def _transit_days(s: dict) -> Optional[float]:
    if s.get("transit_days") is not None:
        return round(float(s["transit_days"]), 1)
    return _days(s.get("fulfilled_at"), s.get("delivered_at"))


def _mean(values: list) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _rate(a: int, b: int) -> Optional[float]:
    return round(a / b, 4) if b else None


def _dispute_bucket(status_: str) -> str:
    if status_ in OPEN_DISPUTE:
        return "open"
    if status_ in WON:
        return "won"
    if status_ in LOST:
        return "lost"
    return "other"


def _group(orders: list[dict], ships: list[dict], refunds: list[dict], disputes: list[dict], now: float,
           key, pushes: bool = False) -> list[dict]:
    rows: dict[str, dict] = {}

    def row(k: str) -> dict:
        return rows.setdefault(k, {"key": k, "orders": 0, "revenue": 0.0, "shipped": 0, "parcels": 0, "delivered": 0,
                                   "transit": 0, "stuck": 0, "failed": 0, "untracked": 0, "refunds": 0, "refunded": 0.0,
                                   "chargebacks": 0, "charged_back": 0.0, "days": []})
    for o in orders:
        r = row(key(o))
        r["orders"] += 1
        r["revenue"] += o["total"] or 0
        if o.get("fulfilled_at"):
            r["shipped"] += 1
    for s in ships:
        r = row(key(s))
        r["parcels"] += 1
        st = ship_state(s, now, pushes)
        if st == "untracked":
            r["untracked"] += 1
        elif st == "delivered":
            r["delivered"] += 1
            r["days"].append(_transit_days(s))
        elif st in ("stuck", "late", "unscanned"):
            r["stuck"] += 1
        elif st == "failed":
            r["failed"] += 1
        elif st == "transit":
            r["transit"] += 1
    for f in refunds:
        r = row(key(f))
        r["refunds"] += 1
        r["refunded"] += f["amount"] or 0
    for d in disputes:
        r = row(key(d))
        r["chargebacks"] += 1
        r["charged_back"] += d["amount"] or 0
    out = []
    for r in rows.values():
        out.append({**{k: v for k, v in r.items() if k != "days"}, "revenue": round(r["revenue"], 2),
                    "refunded": round(r["refunded"], 2), "charged_back": round(r["charged_back"], 2),
                    "avg_days": _mean(r["days"]), "refund_rate": _rate(r["refunds"], r["orders"]),
                    "chargeback_rate": _rate(r["chargebacks"], r["orders"])})
    out.sort(key=lambda r: (-r["orders"], r["key"]))
    return out


def overview(days: int, now: Optional[float] = None) -> dict:
    """The Backend tab for the orders of the last `days` days."""
    now = now or time.time()
    start = now - days * 86400
    orders = db.query("SELECT * FROM backend_orders WHERE created_at>=? AND test=0 AND cancelled_at IS NULL "
                      "ORDER BY created_at DESC", (start,))
    ships = db.query("SELECT s.*, o.order_name, o.country, o.city, o.created_at AS ordered_at FROM shipments s "
                     "JOIN backend_orders o ON o.order_id=s.order_id WHERE o.created_at>=? AND o.test=0 "
                     "ORDER BY s.fulfilled_at DESC", (start,))
    refunds = db.query("SELECT r.*, o.order_name, o.country, o.total FROM refunds r JOIN backend_orders o "
                       "ON o.order_id=r.order_id WHERE r.created_at>=? AND o.test=0 ORDER BY r.created_at DESC", (start,))
    disputes = db.query("SELECT d.*, o.order_name, o.country FROM disputes d LEFT JOIN backend_orders o "
                        "ON o.order_id=d.order_id WHERE d.initiated_at>=? ORDER BY d.initiated_at DESC", (start,))
    pushes = shopify_pushes()
    states = [ship_state(s, now, pushes) for s in ships]
    delivered = [s for s, st in zip(ships, states) if st == "delivered"]
    shipped = [o for o in orders if o.get("fulfilled_at")]
    refunded_orders = {f["order_id"] for f in refunds}
    buckets = {"open": 0, "won": 0, "lost": 0, "other": 0}
    for d in disputes:
        buckets[_dispute_bucket(d["status"])] += 1
    hist_edges = ((0, 7, "Up to 7 days"), (7, 14, "8 to 14"), (14, 21, "15 to 21"), (21, 30, "22 to 30"), (30, 10 ** 6, "Over 30"))
    hist = []
    for lo, hi, label in hist_edges:
        n = sum(1 for s in delivered if (_transit_days(s) or 0) > lo and (_transit_days(s) or 0) <= hi) if lo else \
            sum(1 for s in delivered if (_transit_days(s) or 0) <= hi)
        hist.append({"label": label, "n": n})
    tiles = {
        "orders": len(orders), "revenue": round(sum(o["total"] or 0 for o in orders), 2),
        "shipped": len(shipped), "shipped_rate": _rate(len(shipped), len(orders)),
        "days_to_ship": _mean([_days(o["created_at"], o["fulfilled_at"]) for o in shipped]),
        "delivered": len(delivered), "delivered_rate": _rate(len(delivered), len(ships)),
        "days_to_deliver": _mean([_transit_days(s) for s in delivered]),
        "in_transit": states.count("transit"), "stuck": sum(states.count(k) for k in ("stuck", "late", "unscanned")),
        "failed": states.count("failed"), "untracked": states.count("untracked"),
        "unfulfilled": sum(1 for o in orders if not o.get("fulfilled_at")),
        "refunds": len(refunds), "refunded": round(sum(f["amount"] or 0 for f in refunds), 2),
        "refund_rate": _rate(len(refunded_orders), len(orders)),
        "chargebacks": len(disputes), "charged_back": round(sum(d["amount"] or 0 for d in disputes), 2),
        "chargeback_rate": _rate(len(disputes), len(orders)), "disputes_open": buckets["open"],
        "disputes_won": buckets["won"], "disputes_lost": buckets["lost"],
    }
    currency = next((o["currency"] for o in orders if o.get("currency")), "USD")
    reasons: dict[str, dict] = {}
    for f in refunds:
        r = reasons.setdefault(refund_kind(f["note"]), {"kind": refund_kind(f["note"]), "n": 0, "amount": 0.0})
        r["n"] += 1
        r["amount"] += f["amount"] or 0
    return {
        "days": days, "currency": currency, "tiles": tiles, "transit_histogram": hist,
        "countries": _group(orders, ships, refunds, disputes, now, lambda r: r.get("country") or "??", pushes),
        "carriers": [{k: v for k, v in r.items() if k not in ("orders", "revenue", "refunds", "refunded", "chargebacks",
                                                                 "charged_back", "refund_rate", "chargeback_rate")}
                     for r in _group([], ships, [], [], now, lambda r: r.get("carrier") or "Unknown", pushes)],
        "refund_reasons": sorted(({**r, "amount": round(r["amount"], 2)} for r in reasons.values()),
                                 key=lambda r: -r["n"]),
        "shipments": [_ship_row(s, st, now) for s, st in list(zip(ships, states))[:200]],
        "refunds": [{"order_id": f["order_id"], "order_name": f["order_name"], "at": f["created_at"],
                     "amount": f["amount"], "currency": f["currency"], "country": f["country"],
                     "kind": refund_kind(f["note"]), "note": f["note"] or ""} for f in refunds[:150]],
        "disputes": [_dispute_row(d, now) for d in disputes],
        "attention": attention(now),
        "sync": status(),
    }


def _ship_row(s: dict, state: str, now: float) -> dict:
    return {"order_id": s["order_id"], "order_name": s["order_name"], "country": s["country"], "city": s["city"],
            "carrier": s["carrier"], "tracking_number": s["tracking_number"], "ordered_at": s["ordered_at"],
            "shipped_at": s["fulfilled_at"], "status": s["status"] or ("Pending" if s["registered"] == 1 else "Not tracked"),
            "source": "17track" if s["registered"] == 1 else "shopify" if s["registered"] == FROM_SHOPIFY else "",
            "state": state, "last_event": s["last_event"] or "", "last_event_at": s["last_event_at"],
            "delivered_at": s["delivered_at"], "days": _transit_days(s) if state == "delivered" else _days(s["fulfilled_at"], now),
            "error": s["reg_error"] or ""}


def _dispute_row(d: dict, now: float) -> dict:
    due = d.get("evidence_due_by")
    return {"dispute_id": d["dispute_id"], "order_id": d["order_id"], "order_name": d.get("order_name") or "",
            "country": d.get("country") or "", "type": d["type"], "status": d["status"],
            "bucket": _dispute_bucket(d["status"]), "reason": d["reason"], "amount": d["amount"],
            "currency": d["currency"], "opened_at": d["initiated_at"], "due_by": due,
            "days_left": round((due - now) / 86400, 1) if due else None, "finalized_at": d.get("finalized_on")}


def attention(now: Optional[float] = None) -> list[dict]:
    """What needs a hand, worst first: disputes awaiting an answer, parcels
    that stopped moving or never got scanned, orders not shipped. Not limited
    to the tab's range; the oldest ones matter most."""
    now = now or time.time()
    items: list[dict] = []
    # Only disputes waiting for an answer: one under review is with Shopify, nothing to do until it decides.
    for d in db.query("SELECT d.*, o.order_name FROM disputes d LEFT JOIN backend_orders o ON o.order_id=d.order_id "
                      "WHERE d.status='needs_response' ORDER BY d.evidence_due_by"):
        left = (d["evidence_due_by"] - now) / 86400 if d.get("evidence_due_by") else None
        urgent = left is not None and left <= DISPUTE_URGENT_DAYS
        when = ("response overdue" if left is not None and left < 0 else
                f"respond within {max(1, int(left + 0.999))} days" if left is not None else "needs a response")
        items.append({"level": "fail" if urgent else "warn", "kind": "dispute", "order_id": d["order_id"],
                      "order_name": d.get("order_name") or "", "amount": d["amount"], "currency": d["currency"],
                      "text": f"{d['type'].capitalize()} on {d.get('order_name') or 'an order'}: "
                              f"{(d['reason'] or 'no reason given').replace('_', ' ')}, {when}.", "since": d["initiated_at"],
                      "sort": (0 if urgent else 1, d.get("evidence_due_by") or now)})
    ships = db.query("SELECT s.*, o.order_name, o.country, o.created_at AS ordered_at FROM shipments s "
                     "JOIN backend_orders o ON o.order_id=s.order_id WHERE o.test=0 AND o.cancelled_at IS NULL AND "
                     f"(s.status IS NULL OR s.status NOT IN ({','.join('?' * len(DONE))})) ORDER BY s.fulfilled_at", DONE)
    pushes = shopify_pushes()
    for s in ships:
        st = ship_state(s, now, pushes)
        if st not in ("stuck", "late", "unscanned", "failed"):
            continue
        where = f" to {s['country']}" if s.get("country") else ""
        since_ship = int((now - (s["fulfilled_at"] or now)) / 86400)
        if st == "failed":
            text = f"{s['order_name']}{where}: carrier reports {s['status'].replace('Failure', ' failure').lower()}" + \
                   (f" ({s['last_event']})" if s.get("last_event") else "") + "."
        elif st == "unscanned":
            text = f"{s['order_name']}{where}: shipped {since_ship} days ago, the carrier hasn't scanned it yet."
        elif st == "stuck":
            quiet = int((now - s["last_event_at"]) / 86400)
            text = f"{s['order_name']}{where}: no carrier update for {quiet} days" + \
                   (f" (last: {s['last_event']})" if s.get("last_event") else "") + "."
        else:
            text = f"{s['order_name']}{where}: shipped {since_ship} days ago and still not delivered."
        items.append({"level": "fail" if st == "failed" else "warn", "kind": "parcel", "order_id": s["order_id"],
                      "order_name": s["order_name"], "tracking_number": s["tracking_number"], "state": st,
                      "text": text, "since": s["fulfilled_at"], "sort": (2 if st == "failed" else 3, s["fulfilled_at"] or 0)})
    # Paid in full and never shipped. A refund, even a partial one, means it was dealt with.
    for o in db.query("SELECT * FROM backend_orders WHERE test=0 AND cancelled_at IS NULL AND fulfilled_at IS NULL "
                      "AND financial_status='paid' AND created_at<? ORDER BY created_at",
                      (now - UNFULFILLED_DAYS * 86400,)):
        age = int((now - o["created_at"]) / 86400)
        items.append({"level": "warn", "kind": "unfulfilled", "order_id": o["order_id"], "order_name": o["order_name"],
                      "text": f"{o['order_name']}: paid {age} days ago and not shipped yet.", "since": o["created_at"],
                      "sort": (4, o["created_at"])})
    items.sort(key=lambda i: i["sort"])
    return [{k: v for k, v in i.items() if k != "sort"} for i in items[:ATTENTION_MAX]]


def alerts(now: Optional[float] = None) -> dict:
    """The count on the tab: things needing a hand, and how many are urgent."""
    items = attention(now)
    return {"count": len(items), "urgent": sum(1 for i in items if i["level"] == "fail")}
