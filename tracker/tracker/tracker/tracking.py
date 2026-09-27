"""
Turns storefront pixel events and Shopify orders into Meta events.

Purchases come only from real Shopify orders (event_id order_<id>), never from
the public /collect endpoint, so junk traffic cannot inflate them. The pixel's
job is to hand us the browser identifiers (fbp, fbc, IP, user agent) and the
checkout token that ties a browser to its order.
"""
import asyncio
import datetime as dt
import logging
import time
from typing import Any, Optional
from urllib.parse import urljoin

import config
import db
import meta_capi
import shopify

log = logging.getLogger("tracker.tracking")

PIXEL_TO_META = {
    "page_viewed": "PageView",
    "product_viewed": "ViewContent",
    "collection_viewed": None,
    "search_submitted": "Search",
    "product_added_to_cart": "AddToCart",
    "checkout_started": "InitiateCheckout",
    "payment_info_submitted": "AddPaymentInfo",
    # Enrichment only: they carry contact details / the checkout token.
    "checkout_contact_info_submitted": None,
    "checkout_address_info_submitted": None,
    "checkout_shipping_info_submitted": None,
    "checkout_completed": None,
}


def _s(v: Any, limit: int = 500) -> str:
    return str(v).strip()[:limit] if v not in (None, "") else ""


def _parse_time(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _event_time(ts: Optional[float]) -> int:
    now = time.time()
    if not ts or ts > now + 60 or ts < now - meta_capi.MAX_EVENT_AGE_SECONDS + 3600:
        return int(now)
    return int(ts)


# --- pixel events -------------------------------------------------------------

def _clean_custom(c: dict) -> dict:
    out: dict[str, Any] = {}
    try:
        if c.get("value") not in (None, ""):
            out["value"] = round(float(c["value"]), 2)
    except (TypeError, ValueError):
        pass
    if _s(c.get("currency"), 3):
        out["currency"] = _s(c["currency"], 3).upper()
    contents = []
    for item in (c.get("items") or [])[:50]:
        if not isinstance(item, dict):
            continue
        cid = _content_id({"product_id": shopify.numeric_id(item.get("product_id")),
                           "variant_id": shopify.numeric_id(item.get("variant_id")),
                           "sku": _s(item.get("sku"), 64)})
        if not cid:
            continue
        entry: dict[str, Any] = {"id": cid}
        try:
            entry["quantity"] = int(item.get("quantity") or 1)
            if item.get("price") not in (None, ""):
                entry["item_price"] = round(float(item["price"]), 2)
        except (TypeError, ValueError):
            pass
        contents.append(entry)
    if contents:
        out["content_ids"] = [e["id"] for e in contents]
        out["contents"] = contents
        out["content_type"] = "product"
    try:
        if c.get("num_items"):
            out["num_items"] = int(c["num_items"])
    except (TypeError, ValueError):
        pass
    if _s(c.get("content_name"), 200):
        out["content_name"] = _s(c["content_name"], 200)
    if _s(c.get("search_string"), 200):
        out["search_string"] = _s(c["search_string"], 200)
    return out


def session_user_data(sess: dict, *, customer_id: str = "") -> dict:
    return meta_capi.build_user_data(
        emails=[sess.get("email") or ""], phones=[sess.get("phone") or ""],
        first_name=sess.get("first_name") or "", last_name=sess.get("last_name") or "",
        external_ids=[sess.get("client_id") or "", customer_id],
        ip=sess.get("ip") or "", user_agent=sess.get("user_agent") or "",
        fbp=sess.get("fbp") or "", fbc=sess.get("fbc") or "",
    )


def ingest_pixel_event(p: dict, ip: str, user_agent: str) -> Optional[dict]:
    """Record what the pixel told us; return the Meta event to send, if any.
    Raises ValueError on payloads we refuse."""
    name = _s(p.get("name"), 64)
    if name not in PIXEL_TO_META:
        raise ValueError(f"unknown event {name!r}")
    fbp = _s(p.get("fbp"), 100)
    client_id = _s(p.get("cid"), 100) or fbp
    if not client_id:
        raise ValueError("missing client id")
    customer = p.get("customer") or {}
    checkout = p.get("checkout") or {}
    sess = db.upsert_session(
        client_id,
        fbp=fbp, fbc=_s(p.get("fbc"), 300), ip=_s(ip, 64), user_agent=_s(user_agent, 400),
        checkout_token=_s(checkout.get("token"), 100),
        email=_s(checkout.get("email") or customer.get("email"), 200),
        phone=_s(checkout.get("phone") or customer.get("phone"), 40),
        first_name=_s(checkout.get("first_name") or customer.get("first_name"), 100),
        last_name=_s(checkout.get("last_name") or customer.get("last_name"), 100),
        landing_url=_s(p.get("url"), 1000) if name == "page_viewed" else "",
    )
    meta_name = PIXEL_TO_META[name]
    if not meta_name:
        return None
    event_id = _s(p.get("id"), 100)
    if not event_id:
        raise ValueError("missing event id")
    ts = p.get("ts")
    event = {
        "event_name": meta_name,
        "event_time": _event_time(float(ts) / 1000 if isinstance(ts, (int, float)) else None),
        "event_id": f"{meta_name}_{event_id}",
        "action_source": "website",
        "event_source_url": _s(p.get("url"), 1000) or config.STORE_URL or None,
        "user_data": session_user_data(sess, customer_id=_s(customer.get("id"), 64)),
    }
    custom = _clean_custom(p.get("custom") or {})
    if custom:
        event["custom_data"] = custom
    return {k: v for k, v in event.items() if v is not None}


# --- orders -----------------------------------------------------------------

def tracking_start() -> float:
    """When this tracker took over order reporting (set once, on first boot)."""
    value = db.kv_get("tracking_start")
    if value is None:
        value = str(_parse_time(config.TRACK_ORDERS_FROM) or time.time())
        db.kv_set("tracking_start", value)
    return float(value)


def classify_order(order: dict, *, ignore_start: bool = False) -> str:
    if order.get("test") and not config.SEND_TEST_ORDERS:
        return "test"
    created = _parse_time(order.get("processed_at") or order.get("created_at")) or time.time()
    if time.time() - created > meta_capi.MAX_EVENT_AGE_SECONDS - 3600:
        return "too_old"
    created_at = _parse_time(order.get("created_at")) or created
    if not ignore_start and created_at < tracking_start():
        return "before_start"
    if (order.get("source_name") or "") in config.RENEWAL_SOURCE_NAMES:
        return "renewal"
    return "purchase"


def _note_attrs(order: dict) -> dict:
    return {_s(a.get("name"), 64): _s(a.get("value"), 300)
            for a in (order.get("note_attributes") or []) if isinstance(a, dict)}


def match_session(order: dict) -> tuple[dict, str]:
    """Find the browser that placed this order. Returns (session, how)."""
    sess = db.find_session_by_checkout(_s(order.get("checkout_token"), 100))
    if sess:
        return sess, "checkout_token"
    attrs = _note_attrs(order)
    if attrs.get("fbp") or attrs.get("fbc"):
        found = db.get_session(attrs.get("fbp", "")) if attrs.get("fbp") else None
        if found:
            return found, "note_attributes"
        return {"client_id": attrs.get("fbp", ""), "fbp": attrs.get("fbp", ""),
                "fbc": attrs.get("fbc", "")}, "note_attributes"
    email = _s(order.get("email") or (order.get("customer") or {}).get("email"), 200)
    sess = db.find_session_by_email(email)
    if sess and time.time() - (sess.get("last_seen") or 0) < 3 * 3600:
        return sess, "email"
    return {}, "none"


def _content_id(item: dict) -> str:
    field = config.CONTENT_ID_FIELD
    if field == "sku":
        return _s(item.get("sku"), 64) or _s(item.get("variant_id"), 64)
    return _s(item.get(field), 64) or _s(item.get("product_id"), 64)


def build_order_event(order: dict, kind: str, sess: dict) -> dict:
    renewal = kind == "renewal"
    bill = order.get("billing_address") or {}
    ship = order.get("shipping_address") or {}
    cust = order.get("customer") or {}
    addr = bill if bill.get("zip") or bill.get("city") else ship
    country = addr.get("country_code") or ""
    items = [i for i in (order.get("line_items") or []) if isinstance(i, dict)]
    contents = [{"id": _content_id(i), "quantity": int(i.get("quantity") or 1),
                 "item_price": round(float(i.get("price") or 0), 2)} for i in items if _content_id(i)]
    try:
        value = round(float(order.get(config.PURCHASE_VALUE_FIELD) or order.get("total_price") or 0), 2)
    except (TypeError, ValueError):
        value = 0.0

    # A renewal reuses the first order's note_attributes; its fbc is weeks old
    # and was never clicked for this charge, so browser identifiers are dropped.
    browser = {} if renewal else sess
    user_data = meta_capi.build_user_data(
        emails=[order.get("email") or "", cust.get("email") or "", sess.get("email") or ""],
        phones=[order.get("phone") or "", bill.get("phone") or "", ship.get("phone") or "",
                cust.get("phone") or ""],
        first_name=addr.get("first_name") or cust.get("first_name") or "",
        last_name=addr.get("last_name") or cust.get("last_name") or "",
        city=addr.get("city") or "", state=addr.get("province_code") or "",
        zip_code=addr.get("zip") or "", country=country,
        external_ids=[browser.get("client_id") or "", _s(cust.get("id"), 64)],
        ip="" if renewal else (_s(order.get("browser_ip"), 64) or browser.get("ip") or ""),
        user_agent="" if renewal else (_s((order.get("client_details") or {}).get("user_agent"), 400)
                                       or browser.get("user_agent") or ""),
        fbp=browser.get("fbp") or "", fbc=browser.get("fbc") or "",
    )
    oid = str(order["id"])
    source_url = sess.get("landing_url") or (
        urljoin(config.STORE_URL + "/", (order.get("landing_site") or "").lstrip("/"))
        if config.STORE_URL else "")
    event = {
        "event_name": config.RENEWAL_EVENT_NAME if renewal else "Purchase",
        "event_time": _event_time(_parse_time(order.get("processed_at") or order.get("created_at"))),
        "event_id": f"{'renewal' if renewal else 'order'}_{oid}",
        "action_source": "system_generated" if renewal else "website",
        "user_data": user_data,
        "custom_data": {
            "value": value,
            "currency": (order.get("currency") or "USD").upper(),
            "order_id": oid,
            "content_type": "product",
            "content_ids": [c["id"] for c in contents],
            "contents": contents,
            "num_items": sum(c["quantity"] for c in contents),
        },
    }
    if not renewal and source_url:
        event["event_source_url"] = source_url
    return event


_next_try: dict[str, float] = {}


async def process_order(row: dict, *, force: bool = False, source: str = "webhook") -> str:
    """Send one stored order to Meta if it's ready. Returns its new status."""
    order, oid = row["order_json"], row["order_id"]
    kind = classify_order(order, ignore_start=force)
    if kind in ("test", "too_old", "before_start") or (kind == "renewal" and not config.RENEWAL_EVENT_NAME):
        db.mark_order(oid, "skipped", kind=kind)
        return "skipped"
    if not force and time.time() < _next_try.get(oid, 0):
        return "pending"
    sess, how = match_session(order)
    created = _parse_time(order.get("created_at")) or time.time()
    if (kind == "purchase" and how != "checkout_token" and not force
            and time.time() - created < config.PURCHASE_GRACE_SECONDS):
        return "pending"                         # give the pixel a moment to report
    event = build_order_event(order, kind, sess)
    if db.event_already_sent(event["event_name"], event["event_id"]):
        db.mark_order(oid, "sent", kind=kind)
        return "sent"
    try:
        trace = await meta_capi.send_event(event, source=source, order_id=oid)
    except meta_capi.MetaError as e:
        attempts = row.get("attempts", 0) + 1
        _next_try[oid] = time.time() + min(3600, 30 * 2 ** attempts)
        db.mark_order(oid, "failed", error=str(e)[:1000], kind=kind)
        return "failed"
    _next_try.pop(oid, None)
    db.mark_order(oid, "sent", fbtrace_id=trace, kind=kind)
    log.info("Sent %s for order %s (%s, matched by %s)", event["event_name"],
             order.get("name"), oid, how)
    return "sent"


async def process_pending() -> dict:
    counts: dict[str, int] = {}
    for row in db.pending_orders():
        status = await process_order(row)
        counts[status] = counts.get(status, 0) + 1
    return counts


def ingest_order(order: dict) -> bool:
    return db.upsert_order(order)


async def poll_orders(since_seconds: int) -> int:
    """Pull recent orders from Shopify and queue any we haven't seen."""
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=since_seconds)
    orders = await shopify.list_orders_since(since.isoformat(timespec="seconds"))
    return sum(1 for o in orders if ingest_order(o))


async def send_pixel_event(event: dict) -> None:
    try:
        await meta_capi.send_event(event, source="pixel", attempts=2)
    except meta_capi.MetaError:
        pass                                      # recorded as failed; reported in /report
    except Exception:                             # never let a background send crash the loop
        log.exception("pixel event send crashed")


def fire_and_forget(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


_background: set = set()
