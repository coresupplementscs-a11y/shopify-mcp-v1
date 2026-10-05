"""
Turns storefront pixel events and Shopify orders into Meta events.

Purchases come only from real Shopify orders (event_id order_<id>), never from
the public /collect endpoint, so junk traffic cannot inflate them. The pixel's
job is to hand us the browser identifiers (fbp, fbc, IP, user agent), the ad
clicks the browser arrived from and the checkout token that ties a browser to
its order. Which click a sale is credited to is decided once, by
attribution.resolve: the Purchase carries that click and the order keeps the
same record for the hub.
"""
import asyncio
import datetime as dt
import json
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

import attribution
import config
import db
import meta_ads
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
# Each skip reason in the owner's words (the hub and the watchdog's suggestions).
SKIP_REASONS = {"test": "it is a test order", "cancelled": "it was cancelled",
                "too_old": "it is older than the 7 days Meta accepts",
                "manual": "it is a draft or POS order", "renewal": "sending MRR to Meta is switched off",
                "before_start": "it was placed before the tracker took over"}

_FBCLID_RE = attribution.FBCLID_RE
# Words in an order tag that suggest a subscription rebill the settings don't know yet.
REBILL_WORDS = ("recurring", "rebill", "renewal")


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


def pixel_client_id(p: dict) -> str:
    """The browser behind a pixel payload: Shopify's clientId, else the fbp cookie."""
    return _s(p.get("cid"), 100) or _s(p.get("fbp"), 100)


def ingest_pixel_event(p: dict, ip: str, user_agent: str) -> Optional[dict]:
    """Record what the pixel told us; return the Meta event to send, if any.
    Raises ValueError on payloads we refuse."""
    name = _s(p.get("name"), 64)
    if name not in PIXEL_TO_META:
        raise ValueError(f"unknown event {name!r}")
    fbp = _s(p.get("fbp"), 100)
    client_id = pixel_client_id(p)
    if not client_id:
        raise ValueError("missing client id")
    customer, checkout, custom = _dict(p.get("customer")), _dict(p.get("checkout")), _dict(p.get("custom"))
    # Contact details are only trusted from checkout events, which also carry
    # the checkout token; a bare customer object is not proof of anything.
    contact = checkout if name.startswith("checkout_") or name == "payment_info_submitted" else {}
    # Every arrival from a Meta ad (its parameters or an fbclid) becomes the
    # browser's current click, the newest always winning, and joins its short
    # click history, for assists. The page it came through (lp) rides along.
    ad = attribution.ad_params_from_url(p.get("url"))
    fbclid = attribution.fbclid_of(p.get("url"))
    fake = attribution.stand_in_fbclid(p.get("url"))
    if fake and ad and name == "page_viewed":
        db.note_stand_in_click(ad, fake)           # the ad's own URL holds it: the watchdog names the campaign
    arrival = None
    if ad:
        ad = attribution.with_landing(ad)
        ref = attribution.referrer_host(p.get("ref"))
        if ref:
            ad["ref"] = ref                         # the site that sent the shopper, host only
        arrival = {"params": ad, "fbclid": fbclid, "at": time.time()}
    sess = db.upsert_session(
        client_id,
        # A link carrying its own fbclid is the truth about this click; the
        # cookie may still hold an older one.
        fbp=fbp, fbc="" if fbclid else attribution.real_fbc(_s(p.get("fbc"), 300)), ip=_s(ip, 64),
        user_agent=_s(user_agent, 400),
        checkout_token=_s(checkout.get("token"), 100),
        email=_s(contact.get("email"), 200),
        phone=_s(contact.get("phone"), 40),
        first_name=_s(contact.get("first_name"), 100),
        last_name=_s(contact.get("last_name"), 100),
        landing_url=_s(p.get("url"), 1000) if name == "page_viewed" else "",
        arrival=arrival,
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


def go_live_at() -> float:
    """The stored tracking start, read without moving it (tracking_start()
    does that on boot). Orders created before it were sent by WeTracked."""
    try:
        return float(db.kv_get("tracking_start") or 0)
    except ValueError:
        return 0.0


def pixel_start(pixel_id: str) -> float:
    """When a backup pixel started getting orders from this tracker. Stamped the
    first time it is configured: older orders were reported to it by the
    previous tracker, so sending them again would double-count them."""
    stored = db.kv_get(f"pixel_start:{pixel_id}")
    if stored is None:
        stored = str(time.time())
        db.kv_set(f"pixel_start:{pixel_id}", stored)
    return float(stored)


def order_destinations(order: dict) -> list[dict]:
    """The main dataset gets every order; a backup only those created after it was added."""
    created = _parse_time(order.get("created_at")) or time.time()
    return [meta_capi.primary_pixel(),
            *(p for p in config.EXTRA_PIXELS if created >= pixel_start(p["pixel_id"]))]


def order_tags(order: dict) -> set[str]:
    """The order's Shopify tags, trimmed and lower-cased. The REST API and
    webhooks send one comma-separated string; a list is accepted too."""
    raw = order.get("tags")
    parts = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    return {str(t).strip().lower() for t in parts if str(t).strip()}


def renewal_tags() -> set[str]:
    """RENEWAL_TAGS plus the tags the owner approved in the hub (proposals)."""
    return config.RENEWAL_TAGS | db.extra_renewal_tags()


def is_renewal(order: dict) -> bool:
    """A subscription rebill: billed by the subscription app, not bought after
    an ad. The tracker (what Meta gets) and the hub (what the owner sees) both
    decide with this, so they can never disagree."""
    if (order.get("source_name") or "") in config.RENEWAL_SOURCE_NAMES:
        return True
    tags = order_tags(order)
    return bool(tags) and bool(tags & renewal_tags())


def rebill_like_tags(order: dict) -> set[str]:
    """Tags that read like a subscription rebill but aren't treated as one yet."""
    tags = order_tags(order)
    known = renewal_tags() if tags else set()
    return {t for t in tags if t not in known and any(w in t for w in REBILL_WORDS)}


def is_new_sale(order: dict, reported: str = "") -> bool:
    """A real new sale (what the hub calls new_sale): not a test, cancelled,
    rebill or back-office order. Its age and the tracking start don't matter.
    `reported` is the event a pixel already accepted for it, if any
    (db.sent_event_name): the order stays what Meta got."""
    if order.get("test") or order.get("cancelled_at") or (order.get("financial_status") or "") == "voided":
        return False
    rebill = reported != "Purchase" if reported else is_renewal(order)
    return not rebill and (order.get("source_name") or "") not in config.SKIP_SOURCE_NAMES


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
    if is_renewal(order):
        return "renewal"
    if (order.get("source_name") or "") in config.SKIP_SOURCE_NAMES:
        return "manual"
    return "purchase"


def is_skipped(kind: str) -> bool:
    return kind in SKIPPED_KINDS or (kind == "renewal" and not config.RENEWAL_EVENT_NAME)


def order_event_key(kind: str, order_id: Any) -> tuple[str, str]:
    """(event_name, event_id) an order is reported to Meta with."""
    if kind == "renewal":
        return config.RENEWAL_EVENT_NAME, f"renewal_{order_id}"
    return "Purchase", f"order_{order_id}"


def reported_kind(order_id: Any) -> str:
    """'purchase' or 'renewal' once any dataset accepted the order, else "".
    An order keeps what it was reported as: an MRR tag approved later, or a
    tag added after the send, never turns it into the other event."""
    name = db.sent_event_name(str(order_id))
    return "" if not name else "purchase" if name == "Purchase" else "renewal"


def _note_attrs(order: dict) -> dict:
    return attribution.note_attributes(order)


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
    """The fbc of the order's landing_site fbclid, stamped with the order's
    time. Only the fallback for callers without a decision: landing_site is
    the buyer's FIRST landing page, so attribution.resolve uses it only when
    nothing else exists (rule d), and with its real time whenever that is known."""
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
    if renewal:
        fbc = ""
    elif "decided_fbc" in browser:              # attribution.resolve's click, even when that is none
        fbc = browser["decided_fbc"]
    else:
        fbc = browser.get("fbc") or fbc_from_landing_site(order)
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
    event_name, event_id = order_event_key(kind, oid)
    event = {
        "event_name": event_name,
        "event_time": _event_time(_parse_time(order.get("processed_at") or order.get("created_at"))),
        "event_id": event_id,
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


# --- express checkout: the InitiateCheckout the pixel never saw ------------------------

CHECKOUT_EVENT = "InitiateCheckout"
# A checkout_started from the buyer's browser this long before the order counts.
CHECKOUT_LOOKBACK = 3600


def checkout_event_id(order: dict) -> str:
    token = _s(order.get("checkout_token"), 100)
    return f"checkout_{token}" if token else f"checkout_order_{order['id']}"


def express_checkout_event(order: dict, kind: str, purchase: dict, sess: dict) -> Optional[dict]:
    """The server InitiateCheckout sent right before a new sale's Purchase when
    the storefront pixel never reported the checkout: Shop Pay, Google Pay and
    Apple Pay from the cart skip the checkout page, so checkout_started never
    fires. None when the pixel saw the buyer's browser (`sess`, or any browser
    with the order's checkout token) start a checkout from about an hour before
    the order, and never for MRR, test orders or orders placed before the
    tracking start. Only a Purchase sent as a website event gets one: Meta
    needs the buyer's browser for a website event. It carries the Purchase's
    user_data and the order's value and items."""
    if kind != "purchase" or order.get("test") or purchase.get("action_source") != "website":
        return None
    created = _parse_time(order.get("created_at"))
    if created is None or created < tracking_start():
        return None
    browsers = [sess["client_id"]] if sess.get("client_id") else []
    if db.pixel_checkout_seen(browsers, _s(order.get("checkout_token"), 100), created - CHECKOUT_LOOKBACK):
        return None
    cd = purchase.get("custom_data") or {}
    return {
        "event_name": CHECKOUT_EVENT,
        "event_time": _event_time(created - 1),
        "event_id": checkout_event_id(order),
        "action_source": "website",
        "event_source_url": config.STORE_URL or f"https://{config.SHOPIFY_STORE}.myshopify.com",
        "user_data": dict(purchase["user_data"]),
        "custom_data": {k: cd[k] for k in ("value", "currency", "content_ids", "contents", "num_items") if k in cd},
    }


async def _send_checkout(event: dict, pixel: dict, source: str, order_id: str) -> None:
    """One express-checkout InitiateCheckout to one dataset, once ever (a retry
    or a resend never repeats it). A failure is recorded like any send and
    never holds up the Purchase after it."""
    if db.event_already_sent(event["event_name"], event["event_id"], pixel["pixel_id"]):
        return
    try:
        await meta_capi.send_event(event, source=source, order_id=order_id, pixel=pixel)
    except meta_capi.MetaError:
        pass                                    # recorded as failed; the Purchase goes out regardless
    except Exception:
        log.exception("InitiateCheckout for order %s crashed", order_id)


_next_try: dict[str, float] = {}


def _backoff(oid: str, attempts: int) -> None:
    _next_try[oid] = time.time() + min(3600, 30 * 2 ** max(0, attempts))


def match_schedule() -> list[int]:
    """Ages (seconds after the order was placed) at which an unmatched new
    sale tries matching again; at the last one it is sent with what it has."""
    grace = max(0, config.PURCHASE_GRACE_SECONDS)
    if not grace:
        return [0]
    return [s for s in config.MATCH_RETRY_SECONDS if 0 < s < grace] + [grace]


# --- Shopify's visit record ---------------------------------------------------------

JOURNEY_REASONS = {
    "scope": ("The tracker's Shopify app isn't allowed to read visit history{need}. Sales still go out "
              "with the storefront pixel's data."),
    "throttled": "Shopify asked the tracker to slow down; visit history is tried again with the next sale.",
    "timeout": "Shopify took too long to share visit history; it is tried again with the next sale.",
    "error": "Shopify's visit history couldn't be read ({what}); it is tried again with the next sale.",
}
SCOPE_RETRY_SECONDS = 3600              # a missing permission isn't asked about on every order
_SCOPE_RE = re.compile(r"Required access: `?([a-z_]+)")
_journey_logged: set = set()


def _journey_note(ok: bool, reason: str = "", detail: str = "") -> None:
    """Remember how the last visit-history read went, for the watchdog. A
    problem is logged once, not once per order, until reads work again."""
    db.kv_set("journey_status", json.dumps({"ok": ok, "reason": reason, "detail": detail, "at": time.time()}))
    if ok:
        if _journey_logged:
            log.info("Shopify visit history can be read again")
        _journey_logged.clear()
    elif reason not in _journey_logged:
        _journey_logged.add(reason)
        log.warning("Shopify visit history unavailable (%s): %s", reason, detail)


def journey_status() -> dict:
    """How the last read of Shopify's visit history went: {ok, reason, detail, at}, or {}."""
    try:
        st = json.loads(db.kv_get("journey_status") or "{}")
    except ValueError:
        st = {}
    return st if isinstance(st, dict) else {}


async def journey_for(order: dict) -> Optional[dict]:
    """Shopify's record of the buyer's visits for an order, or None. Never
    raises and never waits past JOURNEY_TIMEOUT_SECONDS: a missing scope,
    throttling, a timeout or an empty record just mean deciding without it."""
    st = journey_status()
    if st.get("reason") == "scope" and time.time() - float(st.get("at") or 0) < SCOPE_RETRY_SECONDS:
        return None
    timeout = max(1, config.JOURNEY_TIMEOUT_SECONDS)
    try:
        summary = await asyncio.wait_for(shopify.order_journey(str(order["id"]), timeout=timeout), timeout + 1)
    except shopify.GraphQLError as e:
        if e.code == "ACCESS_DENIED" or "access denied" in str(e).lower():
            scope = _SCOPE_RE.search(str(e))
            _journey_note(False, "scope", JOURNEY_REASONS["scope"].format(
                need=f" (it needs {scope.group(1)})" if scope else ""))
        elif e.code == "THROTTLED":
            _journey_note(False, "throttled", JOURNEY_REASONS["throttled"])
        else:
            _journey_note(False, "error", JOURNEY_REASONS["error"].format(what=e.code or "GraphQL error"))
        return None
    except (asyncio.TimeoutError, httpx.TimeoutException):
        _journey_note(False, "timeout", JOURNEY_REASONS["timeout"])
        return None
    except httpx.HTTPStatusError as e:
        _journey_note(False, "error", JOURNEY_REASONS["error"].format(
            what=f"Shopify answered {e.response.status_code}"))
        return None
    except Exception as e:
        _journey_note(False, "error", JOURNEY_REASONS["error"].format(what=type(e).__name__))
        return None
    _journey_note(True)
    return summary


# --- the decision -------------------------------------------------------------------

async def decide(order: dict, sess: dict, journey: Optional[dict]) -> dict:
    """attribution.resolve, with Meta's ads to match link names against. Only
    a real storefront session counts as one: match_session also hands back the
    note_attributes' ids, which resolve reads from the order itself."""
    catalog = await meta_ads.ad_catalog()
    return attribution.resolve(order, sess if sess.get("client_id") else {}, journey=journey, catalog=catalog)


def _record(raw: Any) -> dict:
    """A stored attribution record (JSON text, or already decoded)."""
    if isinstance(raw, dict):
        return raw
    try:
        rec = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


async def credit_order(order: dict) -> dict:
    """The attribution record for an order, from everything that can be found
    about it now. Stores and sends nothing."""
    sess, _ = match_session(order)
    return (await decide(order, sess, await journey_for(order)))["attribution"]


async def process_order(row: dict, *, force: bool = False, source: str = "webhook") -> str:
    """Send one stored order to every dataset that should have it, once it's
    ready. Returns its new status: 'sent' only when all of them accepted it.

    `force` is the operator's own call (a resend): it skips the backoff. The
    row's stored forced flag keeps only the resend's intent for the retries
    after it: the start/skip rules stay ignored, and they wait out the backoff
    and go only to the datasets still missing the event, like any retry."""
    order, oid = row["order_json"], row["order_id"]
    forced = force or bool(row.get("forced"))
    kind = classify_order(order, ignore_start=forced)
    if kind in ("purchase", "renewal"):
        kind = reported_kind(oid) or kind       # sent somewhere already: every dataset gets the same event
    if is_skipped(kind) and not (forced and kind in ("before_start", "manual")):
        db.mark_order(oid, "skipped", kind=kind, count_attempt=False)
        return "skipped"
    if not force and time.time() < _next_try.get(oid, 0):
        return "pending"
    sess, how = match_session(order)
    decision = None
    prior = _record(row.get("attribution"))
    if kind == "purchase" and "fbc" in prior:
        # Decided when it was first sent: a retry (a backup pixel that failed)
        # or a resend repeats that click, so every dataset and the hub agree,
        # whatever resolver version decided it.
        decision = {"attribution": prior, "fbc": prior["fbc"]}
        sess = {**sess, "decided_fbc": prior["fbc"]}
    elif kind == "purchase":
        created = _parse_time(order.get("created_at")) or row.get("received_at") or time.time()
        age = time.time() - created
        steps = match_schedule()
        last_try = forced or age >= steps[-1]
        if not last_try and time.time() < (row.get("wait_until") or 0):
            return "pending"                     # waiting for the shopper's visit to show up
        journey = await journey_for(order)
        matched = bool(sess.get("client_id")) or bool((journey or {}).get("lastVisit"))
        if matched or last_try:
            decision = await decide(order, sess, journey)
            # A browser with no ad click leaves only the first landing page, its
            # time unknown, and Shopify builds its visit record after the order:
            # that waits like a sale nobody matched, so an old click isn't sent as new.
            matched = matched and decision["attribution"].get("source") != "first_visit_unverified"
        if not matched and not last_try:
            # No browser (or one without an ad click) and no Shopify visit yet: look again at the next step.
            db.set_order_wait(oid, created + next(s for s in steps if s > age))
            return "pending"
        sess = {**sess, "decided_fbc": decision["fbc"]}
    event = build_order_event(order, kind, sess)
    try:
        checkout = express_checkout_event(order, kind, event, sess)
    except Exception:                           # never a reason to hold up the Purchase
        log.exception("InitiateCheckout for order %s couldn't be built", oid)
        checkout = None
    if decision and decision["attribution"] is not prior:
        # The record keeps the click Meta is sent (fbc), which marks it as the tracker's own decision.
        # A record reused as it was is not written again (its ad may have been named since).
        db.set_order_attribution(oid, {**decision["attribution"], "fbc": decision["fbc"]})
    trace, sent_to, errors = "", [], []
    for pixel in order_destinations(order):
        pid = pixel["pixel_id"]
        # Only the datasets that still lack the event: a retry never repeats a
        # send, and a manual resend reaches each dataset once (resend_mark).
        if db.event_already_sent(event["event_name"], event["event_id"], pid, after=row.get("resend_mark") or 0):
            continue
        if checkout:
            await _send_checkout(checkout, pixel, source, oid)
        try:
            t = await meta_capi.send_event(event, source=source, order_id=oid, pixel=pixel)
        except meta_capi.MetaError as e:
            errors.append(str(e) if pid == config.META_PIXEL_ID else f"[pixel {pid}] {e}")
            continue
        sent_to.append(pid)
        if pid == config.META_PIXEL_ID:
            trace = t
    if errors:
        _backoff(oid, row.get("attempts", 0) + 1)
        db.mark_order(oid, "failed", error="; ".join(errors)[:1000], kind=kind)
        return "failed"
    _next_try.pop(oid, None)
    db.mark_order(oid, "sent", fbtrace_id=trace, kind=kind, count_attempt=bool(sent_to))
    if sent_to:
        log.info("Sent %s for order %s (%s, matched by %s, %s) to %s", event["event_name"],
                 order.get("name"), oid, how, event["action_source"], ", ".join(sent_to))
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
    new = []
    for o in orders:
        if ingest_order(o):
            if not is_skipped(classify_order(o)):
                new.append(o)
        else:
            # The webhook's copy can predate tags an app adds seconds later
            # (a Kaching rebill tag); an order not yet sent picks them up here.
            db.refresh_order_tags(o)
    return new


# One shopper adding the same product again, or starting checkout again, within
# REPEAT_WINDOW of the last one Meta got is the same intent, not another add to
# cart or checkout: #c3714's buyer went back and forth three times in six minutes
# and Meta counted 3 add to carts and 3 checkouts for one sale. The repeat is kept
# (status "repeat", main dataset) so the hub's funnel still sees every step, but
# it is not sent. Purchases never go through here.
REPEAT_EVENTS = ("AddToCart", "InitiateCheckout")
REPEAT_WINDOW = 30 * 60


def _products(event: dict) -> set:
    return {str(i) for i in ((event.get("custom_data") or {}).get("content_ids") or [])}


def is_repeat(event: dict, client_id: str) -> bool:
    """Whether this browser already sent Meta the same step in the window: any
    checkout, or an add to cart of the same product(s)."""
    name = event.get("event_name")
    if name not in REPEAT_EVENTS or not client_id:
        return False
    since = time.time() - REPEAT_WINDOW
    for prev in db.recent_pixel_sends(name, client_id, since):
        if name == "InitiateCheckout" or _products(prev["payload"]) == _products(event):
            return True
    return False


async def send_pixel_event(event: dict, client_id: str = "") -> None:
    try:
        repeat = is_repeat(event, client_id)
    except Exception:                             # never a reason to lose a real event
        log.exception("repeat check failed")
        repeat = False
    if repeat:
        db.record_event(event["event_name"], event["event_id"], "pixel", "repeat", event, client_id=client_id)
        return
    for pixel in meta_capi.destinations():
        try:
            await meta_capi.send_event(event, source="pixel", attempts=2, pixel=pixel, client_id=client_id)
        except meta_capi.MetaError:
            pass                                  # recorded as failed; reported in /report
        except Exception:                         # never let a background send crash the loop
            log.exception("pixel event send to %s crashed", pixel["pixel_id"])


# --- attribution backfill ------------------------------------------------------

BACKFILL_DAYS = 7


async def refresh_identity(order: dict, rec: Any) -> bool:
    """Name the ad of a sale the tracker sent as a bare click (#c3711: the
    browser kept only its _fbc cookie), from Shopify's visit record and the
    order's landing page, when one of them is provably that same click
    (attribution.sent_click_identity). Only the ad's names, ids and landing
    page are written; the fbc, source and click time stay what Meta got, and
    nothing is sent or resent. True when the record was updated."""
    if not attribution.needs_identity(rec):
        return False
    journey = await journey_for(order)
    found = attribution.sent_click_identity(rec, order, journey, await meta_ads.ad_catalog())
    return bool(found) and db.refresh_order_identity(str(order["id"]), rec["fbc"], found)


# How long after a sale its credit is checked against late pixel events.
REALIGN_DAYS = 2


def _same_credit(a: dict, b: dict) -> bool:
    if a.get("ad_id") or b.get("ad_id"):
        return str(a.get("ad_id") or "") == str(b.get("ad_id") or "")
    return str(a.get("ad_name") or "") == str(b.get("ad_name") or "")


def _without_self_assists(rec: dict, sess: dict) -> dict:
    """The record without assists that are its own closing ad under an older
    link name: an assist with no ad id whose ad set and ad names are the ones
    this browser's link for the closer carried (#c3737: "MOF 3 - Copy" in B1 VSL
    is BOF). `rec` itself when there is nothing to take away."""
    closer = str(rec.get("ad_id") or "")
    if not closer:
        return rec
    names = {(attribution._low(v.get("adset_name")), attribution._low(v.get("ad_name")))
             for v in attribution.ad_history(sess.get("ad_history")) if str(v.get("ad_id") or "") == closer}
    assists = rec.get("assists") or []
    kept = [a for a in assists if a.get("ad_id") or
            (attribution._low(a.get("adset_name")), attribution._low(a.get("ad_name"))) not in names]
    return rec if len(kept) == len(assists) else {**rec, "assists": kept}


def _assist_id(a: dict) -> str:
    return str(a.get("ad_id") or "") or "name:" + str(a.get("ad_name") or "").strip().lower()


async def realign_sent(order: dict, rec: Any) -> bool:
    """A sent sale's stored credit, decided again from its browser as it is now,
    when the new decision picks the very click Meta was sent (the same fbc) but
    names another ad: the record is corrected to match what Meta got. Late
    pixel events cause it (#c3744: the BOF page view arrived after the sale, so
    the record named the ad clicked 7 minutes before). Nothing is sent or
    resent, and the fbc never changes. True when the record was rewritten."""
    if not (isinstance(rec, dict) and rec.get("meta") and rec.get("fbc")):
        return False
    sess, _ = match_session(order)
    if not sess.get("client_id"):
        return False
    cleaned = _without_self_assists(rec, sess)
    new = await decide(order, sess, None)
    if new["fbc"] != rec["fbc"]:
        # The browser has moved on since (a later click): only the proven cleanup applies.
        if cleaned is rec:
            return False
        return db.realign_order_attribution(str(order["id"]), rec["fbc"], {**cleaned, "realigned": True})
    tidied, rec = cleaned is not rec, cleaned
    if _same_credit(new["attribution"], rec):
        # Same closer: only assists that turn out to be wrong are taken away (#c3737: the
        # closer under its old link name). Assists are never added here: this decision
        # doesn't read Shopify's visit record, which the first one may have used.
        old_ids = [_assist_id(a) for a in rec.get("assists") or []]
        new_ids = {_assist_id(a) for a in new["attribution"].get("assists") or []}
        kept = [a for a, k in zip(rec.get("assists") or [], old_ids) if k in new_ids]
        if len(kept) == len(old_ids) and not tidied:
            return False
        fixed = {**rec, "assists": kept, "realigned": True}
        return db.realign_order_attribution(str(order["id"]), rec["fbc"], fixed)
    fixed = {**new["attribution"], "fbc": rec["fbc"], "realigned": True}
    if db.realign_order_attribution(str(order["id"]), rec["fbc"], fixed):
        log.info("Order %s: stored credit corrected to the ad of the click Meta was sent; nothing was sent",
                 order.get("name") or order["id"])
        return True
    return False


async def refresh_identities() -> int:
    """Every sent record of the last BACKFILL_DAYS days that names no ad gets
    refresh_identity. Safe to run any number of times: a named record is left
    alone. Returns how many it named."""
    since = time.time() - BACKFILL_DAYS * 86400
    done = 0
    for row in db.orders_since(since - 3 * 86400, ("sent", "failed")):
        order = row["order_json"]
        if (_parse_time(order.get("created_at")) or 0) < since or not is_new_sale(order, row["reported"] or ""):
            continue
        rec = row["attribution"]
        if isinstance(rec, dict) and rec.get("meta") and not rec.get("fbc"):
            # Decided before records kept the click Meta was sent (#c3711, #c3712 on the first
            # day): take it from the Purchase itself, so the sale can be named and checked
            # like any other. The Purchase is what Meta got; nothing is sent.
            fbc = db.sent_purchase_fbc(row["order_id"])
            if fbc and db.keep_sent_fbc(row["order_id"], fbc):
                rec = {**rec, "fbc": fbc}
        try:
            done += await refresh_identity(order, rec)
            # At startup the whole BACKFILL_DAYS window is checked once; the hub keeps checking
            # the last REALIGN_DAYS as pixel events come in late.
            rec = db.orders_by_id([row["order_id"]]).get(str(row["order_id"]), {}).get("attribution")
            done += await realign_sent(order, rec)
        except Exception:
            log.exception("ad name refresh: order %s failed", row["order_id"])
    return done


async def backfill_attribution() -> int:
    """At startup: name the ads of sent records that name none
    (refresh_identities, every start), then, once per resolver version,
    re-decide the stored credit of the last BACKFILL_DAYS days' new sales
    (sent or skipped) with the current resolver. Records it already wrote stay
    as they are, and so does every record the tracker sent (it carries the
    fbc), whatever version decided it: they are what Meta was sent. Nothing is
    ever sent or resent to Meta here; a Purchase can't be corrected after the
    fact, and a resend under a new id would count twice. Returns how many
    records it rewrote."""
    named = await refresh_identities()
    if named:
        log.info("Ad name refresh: named the ad of %d sent sale(s); nothing was sent to Meta", named)
    version = str(attribution.RESOLVER_VERSION)
    if db.kv_get("attribution_backfill") == version:
        return named
    since = time.time() - BACKFILL_DAYS * 86400
    done = 0
    # received_at can trail created_at: the reconciler reads 3 days back.
    for row in db.orders_since(since - 3 * 86400):
        order = row["order_json"]
        if (_parse_time(order.get("created_at")) or 0) < since or not is_new_sale(order, row["reported"] or ""):
            continue
        rec = row["attribution"] or {}
        if "fbc" in rec or rec.get("v", 0) >= attribution.RESOLVER_VERSION:
            continue
        try:
            db.set_order_attribution(row["order_id"], await credit_order(order))
            done += 1
        except Exception:
            log.exception("attribution backfill: order %s failed", row["order_id"])
    db.kv_set("attribution_backfill", version)
    log.info("Attribution backfill v%s: re-decided %d order(s) from the last %d days; nothing was sent to Meta",
             version, done, BACKFILL_DAYS)
    return named + done


# --- operator actions (Claude tools and hub buttons) ------------------------

BEFORE_GO_LIVE_NOTE = ("This order was placed before go-live, so WeTracked already sent it to Meta under its "
                       "own event id. Sending it again makes Meta count it twice.")


def missing_datasets(row: dict) -> list[dict]:
    """The datasets a stored order's event still has to reach: the ones that
    never accepted it, or not since the operator's last resend (resend_mark).
    The event is the one the order was reported as, once it was."""
    order, oid = row["order_json"], str(row["order_id"])
    kind = reported_kind(oid) or classify_order(order, ignore_start=True)
    name, event_id = order_event_key(kind, oid)
    return [p for p in order_destinations(order)
            if not db.event_already_sent(name, event_id, p["pixel_id"], after=row.get("resend_mark") or 0)]


async def resend_order(order_id: str, allow_before_go_live: bool = True, only_missing: bool = False) -> dict:
    """Re-fetch the order from Shopify and send it now, even if it was sent
    before (Meta dedupes on event_id) or falls before the tracking start.
    With `only_missing` (an approved suggestion) it goes only to the datasets
    that don't have it yet, and when none is missing nothing is sent
    (already_sent). WeTracked sent every order placed before go-live under
    its own event id, so resending one counts it twice: the hub refuses
    (allow_before_go_live=False) and Claude's tool gets a warning with the result."""
    oid = shopify.numeric_id(order_id)
    if not oid:
        return {"error": "order_id must be the numeric Shopify order id"}
    try:
        order = await shopify.get_order(oid)
    except Exception as e:
        return {"order_id": oid, "error": f"Could not load the order from Shopify: {e}"}
    before = (_parse_time(order.get("created_at")) or time.time()) < go_live_at()
    if before and not allow_before_go_live:
        return {"order_id": oid, "order_name": order.get("name"), "status": "refused", "before_go_live": True,
                "was_sent_before": False, "note": "", "order": {}, "events": []}
    previous = db.get_order(oid)
    db.upsert_order(order)
    db.reset_order(oid, order=order, forced=True, only_missing=only_missing)
    row = db.get_order(oid)
    nothing_missing = only_missing and not missing_datasets(row)
    status = await process_order(row, force=True, source="manual")
    after = db.get_order(oid) or {}
    after.pop("order_json", None)
    out = {
        "order_id": oid, "order_name": order.get("name"), "status": status,
        "was_sent_before": bool(previous and previous.get("status") == "sent"),
        "already_sent": nothing_missing and status == "sent",
        "note": "Meta dedupes on event_id, so a repeat send is not double-counted." if previous else "",
        "order": after,
        "events": db.events_for_order(oid, limit=5),
    }
    if before:                                  # Meta's dedupe doesn't reach WeTracked's copy
        out.update(before_go_live=True, warning=BEFORE_GO_LIVE_NOTE, note=BEFORE_GO_LIVE_NOTE)
    return out


async def send_test_event(test_event_code: str, pixel_id: Optional[str] = None) -> dict:
    """One PageView tagged with a Test Events code, to prove a pixel's token works."""
    pixel = next((p for p in meta_capi.destinations() if p["pixel_id"] == (pixel_id or config.META_PIXEL_ID)),
                 None)
    if pixel is None:
        return {"ok": False, "error": f"pixel {pixel_id} is not configured"}
    if not _s(test_event_code, 64):
        return {"ok": False, "error": "enter the code from Events Manager > Test events"}
    event = {
        "event_name": "PageView",
        "event_time": int(time.time()),
        "event_id": f"test_{int(time.time() * 1000)}",
        "action_source": "website",
        "event_source_url": config.STORE_URL or f"https://{config.SHOPIFY_STORE}.myshopify.com",
        "user_data": meta_capi.build_user_data(ip="127.0.0.1", user_agent="meta-tracker-test"),
    }
    try:
        trace = await meta_capi.send_event(event, source="test", test_event_code=_s(test_event_code, 64),
                                           attempts=1, pixel=pixel)
        return {"ok": True, "pixel_id": pixel["pixel_id"], "fbtrace_id": trace,
                "next": "Check Events Manager > Test events for a PageView from this server."}
    except meta_capi.MetaError as e:
        return {"ok": False, "pixel_id": pixel["pixel_id"], "error": str(e)}


def fire_and_forget(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


_background: set = set()
