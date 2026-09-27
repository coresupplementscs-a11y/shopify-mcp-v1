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
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urljoin, urlparse

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

# Terminal reasons an order is deliberately not reported.
SKIPPED_KINDS = ("test", "too_old", "before_start", "cancelled", "manual")

_FBCLID_RE = re.compile(r"[A-Za-z0-9_-]{10,400}")


def _s(v: Any, limit: int = 500) -> str:
    return str(v).strip()[:limit] if v not in (None, "") else ""


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return default


def _parse_time(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def _event_time(ts: Optional[float]) -> int:
    now = time.time()
    if not ts or ts > now + 60 or ts < now - meta_capi.MAX_EVENT_AGE_SECONDS + 3600:
        return int(now)
    return int(ts)


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


# --- pixel events -------------------------------------------------------------

def _clean_custom(c: dict) -> dict:
    out: dict[str, Any] = {}
    if c.get("value") not in (None, ""):
        try:
            out["value"] = round(float(c["value"]), 2)
        except (TypeError, ValueError):
            pass
    if _s(c.get("currency"), 3):
        out["currency"] = _s(c["currency"], 3).upper()
    contents = []
    items = c.get("items")
    for item in (items if isinstance(items, list) else [])[:50]:
        if not isinstance(item, dict):
            continue
        cid = _content_id({"product_id": shopify.numeric_id(item.get("product_id")),
                           "variant_id": shopify.numeric_id(item.get("variant_id")),
                           "sku": _s(item.get("sku"), 64)})
        if not cid:
            continue
        entry: dict[str, Any] = {"id": cid}
        try:
            entry["quantity"] = max(1, int(item.get("quantity") or 1))
        except (TypeError, ValueError):
            entry["quantity"] = 1
        if item.get("price") not in (None, ""):
            try:
                entry["item_price"] = round(float(item["price"]), 2)
            except (TypeError, ValueError):
                pass
        contents.append(entry)
    if contents:
        out["content_ids"] = [e["id"] for e in contents]
        out["contents"] = contents
        out["content_type"] = "product"
    if c.get("num_items") not in (None, ""):
        try:
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
    customer, checkout, custom = _dict(p.get("customer")), _dict(p.get("checkout")), _dict(p.get("custom"))
    # Contact details are only trusted from checkout events, which also carry
    # the checkout token; a bare customer object is not proof of anything.
    contact = checkout if name.startswith("checkout_") or name == "payment_info_submitted" else {}
    sess = db.upsert_session(
        client_id,
        fbp=fbp, fbc=_s(p.get("fbc"), 300), ip=_s(ip, 64), user_agent=_s(user_agent, 400),
        checkout_token=_s(checkout.get("token"), 100),
        email=_s(contact.get("email"), 200),
        phone=_s(contact.get("phone"), 40),
        first_name=_s(contact.get("first_name"), 100),
        last_name=_s(contact.get("last_name"), 100),
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
        "event_time": _event_time(float(ts) / 1000 if isinstance(ts, (int, float)) and ts > 0 else None),
        "event_id": f"{meta_name}_{event_id}",
        "action_source": "website",
        "event_source_url": _s(p.get("url"), 1000) or config.STORE_URL or None,
        "user_data": session_user_data(sess, customer_id=_s(customer.get("id"), 64)),
    }
    custom_data = _clean_custom(custom)
    if custom_data:
        event["custom_data"] = custom_data
    return {k: v for k, v in event.items() if v is not None}


# --- orders -----------------------------------------------------------------

def tracking_start() -> float:
    """When this tracker took over live order reporting.

    Stamped on first boot. Moved forward again when the service switches from
    test mode (META_TEST_EVENT_CODE set) to live, because orders placed during
    the test phase were reported live by the previous tracking app. Setting
    TRACK_ORDERS_FROM overrides it whenever set (deliberate backfill)."""
    explicit = _parse_time(config.TRACK_ORDERS_FROM) if config.TRACK_ORDERS_FROM else None
    if config.TRACK_ORDERS_FROM and explicit is None:
        log.error("TRACK_ORDERS_FROM=%r is not an ISO-8601 time; ignoring it", config.TRACK_ORDERS_FROM)
    stored = db.kv_get("tracking_start")
    mode = "test" if config.META_TEST_EVENT_CODE else "live"
    previous_mode = db.kv_get("mode")
    db.kv_set("mode", mode)
    if explicit is not None:
        value = explicit
        if stored is None or float(stored) != value:
            log.warning("TRACK_ORDERS_FROM sets the tracking start to %s", config.TRACK_ORDERS_FROM)
    elif stored is None:
        value = time.time()
    elif previous_mode == "test" and mode == "live":
        value = time.time()
        log.warning("Switched from test to live: orders created before now are left to the "
                    "previous tracker (set TRACK_ORDERS_FROM to backfill)")
    else:
        value = float(stored)
    db.kv_set("tracking_start", str(value))
    return value


def classify_order(order: dict, *, ignore_start: bool = False) -> str:
    if order.get("test") and not config.SEND_TEST_ORDERS:
        return "test"
    # An order cancelled or voided before we saw it is not revenue; the
    # reconciler recovers exactly these late, so never report a Purchase.
    if order.get("cancelled_at") or (order.get("financial_status") or "") == "voided":
        return "cancelled"
    created = _parse_time(order.get("processed_at") or order.get("created_at")) or time.time()
    if time.time() - created > meta_capi.MAX_EVENT_AGE_SECONDS - 3600:
        return "too_old"
    created_at = _parse_time(order.get("created_at")) or created
    if not ignore_start and created_at < tracking_start():
        return "before_start"
    source = order.get("source_name") or ""
    if source in config.RENEWAL_SOURCE_NAMES:
        return "renewal"
    if source in config.SKIP_SOURCE_NAMES:
        return "manual"
    return "purchase"


def is_skipped(kind: str) -> bool:
    return kind in SKIPPED_KINDS or (kind == "renewal" and not config.RENEWAL_EVENT_NAME)


def _note_attrs(order: dict) -> dict:
    return {_s(a.get("name"), 64): _s(a.get("value"), 300)
            for a in (order.get("note_attributes") or []) if isinstance(a, dict)}


def _order_browser(order: dict) -> tuple[str, str]:
    return (_s(order.get("browser_ip"), 64),
            _s(_dict(order.get("client_details")).get("user_agent"), 400))


def match_session(order: dict) -> tuple[dict, str]:
    """Find the browser that placed this order. Returns (session, how)."""
    sess = db.find_session_by_checkout(_s(order.get("checkout_token"), 100))
    if sess:
        return sess, "checkout_token"
    attrs = _note_attrs(order)
    if attrs.get("fbp") or attrs.get("fbc"):
        found = db.find_session_by_fbp(attrs.get("fbp", "")) if attrs.get("fbp") else None
        if found:
            return found, "note_attributes"
        return {"client_id": "", "fbp": attrs.get("fbp", ""), "fbc": attrs.get("fbc", "")}, "note_attributes"
    # An email alone can be claimed by anyone posting to /collect, so a session
    # only counts when Shopify's own record of the buyer's browser backs it up.
    email = _s(order.get("email") or _dict(order.get("customer")).get("email"), 200)
    order_ip, order_ua = _order_browser(order)
    for cand in db.find_sessions_by_email(email, time.time() - 3 * 3600):
        ip_ok = bool(order_ip) and cand.get("ip") == order_ip
        ua_ok = bool(order_ua) and cand.get("user_agent") == order_ua
        if ip_ok or (ua_ok and not order_ip):
            return cand, "email"
    return {}, "none"


def fbc_from_landing_site(order: dict) -> str:
    """Shopify stores the buyer's landing URL with its query string on every
    online order, so an ad click survives even when the browser lost the
    _fbc cookie. Meta accepts fbc built as fb.1.<ms>.<fbclid>."""
    try:
        query = parse_qs(urlparse(order.get("landing_site") or "").query)
    except (TypeError, ValueError):
        return ""
    fbclid = (query.get("fbclid") or [""])[0]
    if not _FBCLID_RE.fullmatch(fbclid):
        return ""
    seen = _parse_time(order.get("created_at")) or time.time()
    return f"fb.1.{int(seen * 1000)}.{fbclid}"


def _content_id(item: dict) -> str:
    field = config.CONTENT_ID_FIELD
    if field == "sku":
        return _s(item.get("sku"), 64) or _s(item.get("variant_id"), 64)
    return _s(item.get(field), 64) or _s(item.get("product_id"), 64)


def _action_source(order: dict, sess: dict) -> str:
    if (order.get("source_name") or "") == "pos":
        return "physical_store"
    _, order_ua = _order_browser(order)
    # A website event needs a browser behind it; without one Meta should not
    # be told the buyer was on the site.
    return "website" if (order_ua or sess.get("user_agent") or sess.get("fbp")) else "other"


def build_order_event(order: dict, kind: str, sess: dict) -> dict:
    renewal = kind == "renewal"
    bill = _dict(order.get("billing_address"))
    ship = _dict(order.get("shipping_address"))
    cust = _dict(order.get("customer"))
    addr = bill if bill.get("zip") or bill.get("city") else ship
    country = (addr.get("country_code") or _dict(cust.get("default_address")).get("country_code") or "")
    items = [i for i in (order.get("line_items") or []) if isinstance(i, dict)]
    contents = []
    for i in items:
        cid = _content_id(i)
        if not cid:
            continue
        try:
            qty = max(1, int(i.get("quantity") or 1))
        except (TypeError, ValueError):
            qty = 1
        contents.append({"id": cid, "quantity": qty, "item_price": _num(i.get("price"))})
    value = _num(order.get(config.PURCHASE_VALUE_FIELD), default=_num(order.get("total_price")))

    # A renewal reuses the first order's note_attributes; its fbc is weeks old
    # and was never clicked for this charge, so browser identifiers are dropped.
    browser = {} if renewal else sess
    order_ip, order_ua = _order_browser(order)
    fbc = "" if renewal else (browser.get("fbc") or fbc_from_landing_site(order))
    user_data = meta_capi.build_user_data(
        emails=[order.get("email") or "", cust.get("email") or "", sess.get("email") or ""],
        phones=[order.get("phone") or "", bill.get("phone") or "", ship.get("phone") or "",
                cust.get("phone") or ""],
        first_name=addr.get("first_name") or cust.get("first_name") or "",
        last_name=addr.get("last_name") or cust.get("last_name") or "",
        city=addr.get("city") or "", state=addr.get("province_code") or "",
        zip_code=addr.get("zip") or "", country=country,
        external_ids=[browser.get("client_id") or "", _s(cust.get("id"), 64)],
        ip="" if renewal else (order_ip or browser.get("ip") or ""),
        user_agent="" if renewal else (order_ua or browser.get("user_agent") or ""),
        fbp=browser.get("fbp") or "", fbc=fbc,
    )
    oid = str(order["id"])
    action_source = "system_generated" if renewal else _action_source(order, sess)
    event = {
        "event_name": config.RENEWAL_EVENT_NAME if renewal else "Purchase",
        "event_time": _event_time(_parse_time(order.get("processed_at") or order.get("created_at"))),
        "event_id": f"{'renewal' if renewal else 'order'}_{oid}",
        "action_source": action_source,
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
    if action_source == "website":
        source_url = sess.get("landing_url") or (
            urljoin(config.STORE_URL + "/", (order.get("landing_site") or "").lstrip("/"))
            if config.STORE_URL else "")
        if source_url:
            event["event_source_url"] = source_url
    return event


_next_try: dict[str, float] = {}


def _backoff(oid: str, attempts: int) -> None:
    _next_try[oid] = time.time() + min(3600, 30 * 2 ** max(0, attempts))


async def process_order(row: dict, *, force: bool = False, source: str = "webhook") -> str:
    """Send one stored order to Meta if it's ready. Returns its new status."""
    order, oid = row["order_json"], row["order_id"]
    force = force or bool(row.get("forced"))
    kind = classify_order(order, ignore_start=force)
    if is_skipped(kind) and not (force and kind in ("before_start", "manual")):
        db.mark_order(oid, "skipped", kind=kind, count_attempt=False)
        return "skipped"
    if not force and time.time() < _next_try.get(oid, 0):
        return "pending"
    sess, how = match_session(order)
    received = _parse_time(order.get("created_at")) or row.get("received_at") or time.time()
    if (kind == "purchase" and how != "checkout_token" and not force
            and time.time() - received < config.PURCHASE_GRACE_SECONDS):
        return "pending"                         # give the pixel a moment to report
    event = build_order_event(order, kind, sess)
    if not force and db.event_already_sent(event["event_name"], event["event_id"]):
        db.mark_order(oid, "sent", kind=kind, count_attempt=False)
        return "sent"
    try:
        trace = await meta_capi.send_event(event, source=source, order_id=oid)
    except meta_capi.MetaError as e:
        _backoff(oid, row.get("attempts", 0) + 1)
        db.mark_order(oid, "failed", error=str(e)[:1000], kind=kind)
        return "failed"
    _next_try.pop(oid, None)
    db.mark_order(oid, "sent", fbtrace_id=trace, kind=kind)
    log.info("Sent %s for order %s (%s, matched by %s, %s)", event["event_name"],
             order.get("name"), oid, how, event["action_source"])
    return "sent"


async def process_pending() -> dict:
    counts: dict[str, int] = {}
    for row in db.pending_orders():
        oid = row["order_id"]
        try:
            status = await process_order(row)
        except Exception as e:                    # one bad order must never block the queue
            log.exception("order %s crashed while processing", oid)
            _backoff(oid, row.get("attempts", 0) + 1)
            try:
                db.mark_order(oid, "failed", error=f"internal: {e!r}"[:1000])
            except Exception:
                log.exception("could not record the failure for order %s", oid)
            status = "failed"
        counts[status] = counts.get(status, 0) + 1
    return counts


def ingest_order(order: dict) -> bool:
    return db.upsert_order(order)


async def poll_orders(since_seconds: int) -> list[dict]:
    """Pull recent orders from Shopify and queue any we haven't seen.
    Returns the new orders that will actually be sent."""
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=since_seconds)
    orders = await shopify.list_orders_since(since.isoformat(timespec="seconds"))
    return [o for o in orders if ingest_order(o) and not is_skipped(classify_order(o))]


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
