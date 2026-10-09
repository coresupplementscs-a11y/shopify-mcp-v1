"""
The hub: a private dashboard at /hub for the store owner. It shows that
tracking is healthy, what the ads really sold, which Meta creative sold it
(and which ones helped), and Product ROAS: Shopify sales of the products the
running campaigns sell, over ad spend. Subscription rebills (the owner calls
them MRR) never count as ad sales. Profit belongs to the owner's P&L app: the
top section shows the P&L's own numbers, read through /hub/api/pnl.

  GET  /hub                      the page (login form until signed in)
  POST /hub/login                ADMIN_TOKEN -> session cookie
  GET  /hub/logout
  GET  /hub/api/pnl              the P&L app's numbers for a range (pnl.py)
  GET  /hub/api/overview         status, cards, 7-day series, match quality
  GET  /hub/api/orders           orders in the range and how each was tracked
  GET  /hub/api/creatives        spend vs store-confirmed sales and assists per ad
  GET  /hub/api/assists          per ad that assisted sales: its spend and the ads that closed them
  GET  /hub/api/funnel           each browser's furthest step: Meta ads, not from Meta and all; listicle
  GET  /hub/api/watchdog         the last 24 h of health checks
  POST /hub/api/watchdog/run     run the checks now
  POST /hub/api/resend/{id}      re-send one order to Meta (never one placed before go-live)
  POST /hub/api/test-event       send a Test Events PageView
  GET  /hub/api/proposals        fixes the watchdog suggests, waiting for a yes
  POST /hub/api/proposals/{id}/approve   do it
  POST /hub/api/proposals/{id}/dismiss   don't

Which ad got a sale is the tracker's stored decision (attribution.resolve,
the same one the Purchase sent to Meta carried). An order without one (placed
before go-live, or not handled yet) is decided by that same resolver here.

Every API response is built from our own fields only: no tokens and no
customer contact details (email, phone, IP, address, name) ever leave here.
Shopify or Meta being down gives partial data with an `error`, never a 500.
"""
import asyncio
import datetime as dt
import hashlib
import hmac
import json
import logging
import math
import os
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qs

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

import attribution
import backend
import config
import database
import db
import meta_ads
import meta_capi
import pnl
import shopify
import tracking
import watchdog

log = logging.getLogger("tracker.hub")

COOKIE = "hub_session"
COOKIE_MAX_AGE = 90 * 86400
NO_STORE = {"Cache-Control": "no-store"}
PAGE_HEADERS = {**NO_STORE, "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff"}
LOGIN_FAILURES_ALLOWED = 20
LOGIN_FAILURE_WINDOW = 600
MAX_FORM_BYTES = 4096

RANGES = {                      # key: (label, days back from today, days in range)
    "today": ("Today", 0, 1),
    "yesterday": ("Yesterday", 1, 1),
    "7d": ("Last 7 days", 6, 7),
    "30d": ("Last 30 days", 29, 30),
}
HEADLINES = {"ok": "All good", "warn": "Needs a look", "fail": "Something is broken"}
FUNNEL_STEPS = ["Visitors", "Product views", "Add to cart", "Checkout", "Purchases"]
FUNNEL_EVENTS = ["PageView", "ViewContent", "AddToCart", "InitiateCheckout"]
COVERAGE_KEYS = ("em", "ph", "client_ip_address", "client_user_agent", "fbc", "fbp")
# The main pixel's recorded match keys, as the owner-facing detail chips.
DETAIL_KEYS = {"email": "em", "phone": "ph", "ip": "client_ip_address",
               "browser": "client_user_agent", "ad_click_id": "fbc", "browser_id": "fbp"}
SKIP_REASONS = tracking.SKIP_REASONS
# Orders placed before go-live: WeTracked sent them, under its own event ids.
BEFORE_GO_LIVE = "before_go_live"
BEFORE_GO_LIVE_LABEL = "Before go-live, WeTracked sent this"
BEFORE_GO_LIVE_REFUSAL = ("Not sent: this order was placed before go-live, so WeTracked already sent it to Meta. "
                          "Sending it again would make Meta count it twice.")
# Orders on record that were never credited (mostly skipped pre-go-live ones)
# are decided and stored while a page loads: this many per request, and the
# page waits this long for them (the rest finish in the background).
CREDIT_PER_REQUEST = 20
CREDIT_WAIT = 4.0
# Which products a campaign sells is learned from this many store days of sales.
LEARN_DAYS = 30
# Words campaign names use for their setup, never for a product. Without this a
# campaign called "Test - SpermFuel" would also claim a "Testosterone" product.
CAMPAIGN_WORDS = {"abo", "cbo", "asc", "adv", "advantage", "test", "tests", "testing", "new", "copy",
                  "campaign", "campaigns", "sale", "sales", "broad", "lal", "lookalike", "prospecting",
                  "retargeting", "scale", "scaling", "tof", "mof", "bof", "ugc", "static", "statics",
                  "video", "videos", "the", "and", "for", "with", "ads", "purchase", "purchases",
                  "conversion", "conversions", "traffic"}
# A product is one a campaign sells when it is the main line (the biggest
# subtotal) of at least this share of the sales tied to the campaign.
MAIN_LINE_SHARE = 0.25
# An ad the tracker knows came from Meta but can't name (no ad id, no name).
UNNAMED_AD = "Meta ad (name unknown)"
# A sent sale's credit is checked against late pixel events this often (tracking.realign_sent).
REALIGN_RETRY = 600
# A sent sale whose ad is still unnamed is looked for again after this long.
IDENTITY_RETRY = 3600
# Click history (and so assists) started being recorded with this release.
ASSISTS_SINCE = "Sep 27, 2026"
# The moment that release went out (6:25 PM New York time). A sale before it
# has no click history, so whether an earlier ad helped it can't be known.
ASSISTS_FROM = dt.datetime(2026, 9, 27, 22, 25, tzinfo=dt.timezone.utc).timestamp()


# --- auth ---------------------------------------------------------------------

def _session_value() -> str:
    """The cookie proves the owner typed ADMIN_TOKEN, without storing the
    token itself in the browser. Changing ADMIN_TOKEN signs everyone out."""
    return hmac.new(config.ADMIN_TOKEN.encode(), b"hub-session-v1", hashlib.sha256).hexdigest()


def _same(a: str, b: str) -> bool:
    # Bytes, so a non-ASCII guess is a plain mismatch instead of a TypeError.
    return hmac.compare_digest(a.encode(), b.encode())


def _authed(request: Request) -> bool:
    if not config.ADMIN_TOKEN:
        return False
    cookie = request.cookies.get(COOKIE, "")
    if cookie and _same(cookie, _session_value()):
        return True
    auth = request.headers.get("authorization", "")
    return auth.lower().startswith("bearer ") and _same(auth[7:].strip(), config.ADMIN_TOKEN)


def _caller(request: Request) -> str:
    """The real caller's address, by the same trusted-proxy rule as /collect
    (app.py hands its function over in app.state; it runs as __main__, so it
    can't be imported from here)."""
    find = getattr(request.app.state, "client_ip", None) if "app" in request.scope else None
    ip = find(request) if find else ""
    return ip or (request.client.host if request.client else "") or "unknown"


_login_failures: dict[str, list[float]] = {}


def _recent_failures(ip: str) -> list[float]:
    now = time.time()
    if len(_login_failures) > 10_000:
        _login_failures.clear()
    fails = [t for t in _login_failures.get(ip, []) if now - t < LOGIN_FAILURE_WINDOW]
    _login_failures[ip] = fails
    return fails


async def _read_capped(request: Request, limit: int) -> Optional[bytes]:
    """The body, or None when it is bigger than any honest hub request."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return None
    return body


def _login_page(message: str = "", status: int = 200) -> HTMLResponse:
    from hub_page import LOGIN_HTML              # written separately; loaded on use
    return HTMLResponse(LOGIN_HTML.replace("<!--error-->", message), status_code=status,
                        headers=PAGE_HEADERS)


async def page(request: Request) -> Response:
    if not _authed(request):
        return _login_page()
    from hub_page import HUB_HTML
    return HTMLResponse(HUB_HTML, headers=PAGE_HEADERS)


async def slash(request: Request) -> Response:
    return RedirectResponse("/hub", status_code=303)


def _secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return (proto or request.url.scheme) == "https"


async def login(request: Request) -> Response:
    if request.method == "GET":
        return RedirectResponse("/hub", status_code=303)
    if not config.ADMIN_TOKEN:
        return _login_page("The hub stays locked until ADMIN_TOKEN is set in Railway > tracker > Variables.", 401)
    ip = _caller(request)
    if len(_recent_failures(ip)) >= LOGIN_FAILURES_ALLOWED:
        return _login_page("Too many wrong tries. Wait 10 minutes and try again.", 429)
    body = await _read_capped(request, MAX_FORM_BYTES)
    token = ""
    if body is not None:
        token = (parse_qs(body.decode("utf-8", "replace")).get("token") or [""])[0].strip()
    if not (token and _same(token, config.ADMIN_TOKEN)):
        _login_failures.setdefault(ip, []).append(time.time())
        log.warning("Hub login failed")
        return _login_page("That token didn't match. Check ADMIN_TOKEN in Railway and try again.", 401)
    _login_failures.pop(ip, None)
    resp = RedirectResponse("/hub", status_code=303, headers=NO_STORE)
    resp.set_cookie(COOKIE, _session_value(), max_age=COOKIE_MAX_AGE, path="/hub", httponly=True,
                    samesite="strict", secure=_secure(request))
    return resp


async def logout(request: Request) -> Response:
    resp = RedirectResponse("/hub", status_code=303, headers=NO_STORE)
    resp.delete_cookie(COOKIE, path="/hub", httponly=True, samesite="strict", secure=_secure(request))
    return resp


def _json(data: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers=NO_STORE)


def _api(handler, post: bool = False):
    """Auth for every /hub/api/* call. POSTs also need X-Hub-Request, which a
    cross-site form can't send. A crash becomes an `error`, not a 500."""
    async def endpoint(request: Request) -> Response:
        if not _authed(request):
            return _json({"error": "unauthorized"}, 401)
        if post and request.headers.get("x-hub-request") != "1":
            return _json({"error": "forbidden"}, 403)
        try:
            return _json(await handler(request))
        except Exception:
            log.exception("hub %s failed", request.url.path)
            return _json({"error": "This part of the hub couldn't be loaded. The details are in the server log."})
    return endpoint


# --- small helpers ---------------------------------------------------------------

def _money(v: Any) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return round(x, 2) if math.isfinite(x) else 0.0


def _ratio(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or not b:
        return None
    return round(a / b, 2)


def _count(v: float):
    """Meta reports purchase counts as floats; show whole ones as ints."""
    v = round(v or 0, 2)
    return int(v) if float(v).is_integer() else v


def _midnight(day: dt.date, tz) -> float:
    return dt.datetime.combine(day, dt.time(), tzinfo=tz).timestamp()


def _today():
    return dt.datetime.now(config.store_tz()).date()


def _range(key: Optional[str]) -> dict:
    """A store-local day range as [start, end) epochs plus its dates."""
    key = key if key in RANGES else "today"
    label, back, days = RANGES[key]
    tz = config.store_tz()
    first = _today() - dt.timedelta(days=back)
    last = first + dt.timedelta(days=days - 1)
    return {"key": key, "label": label, "since": first.isoformat(), "until": last.isoformat(),
            "start": _midnight(first, tz), "end": _midnight(last + dt.timedelta(days=1), tz)}


def custom_range(since: Any, until: Any) -> Optional[dict]:
    """A store-local range of whole days from `since` to `until` (YYYY-MM-DD),
    within the last LEARN_DAYS days (what the order listing covers), or None."""
    try:
        first, last = dt.date.fromisoformat(str(since)), dt.date.fromisoformat(str(until))
    except ValueError:
        return None
    today = _today()
    if not (today - dt.timedelta(days=LEARN_DAYS - 1) <= first <= last <= today):
        return None
    tz = config.store_tz()
    label = f"{first:%b} {first.day}" + ("" if last == first else f" to {last:%b} {last.day}")
    return {"key": f"{first.isoformat()}_{last.isoformat()}", "label": label, "since": first.isoformat(),
            "until": last.isoformat(), "start": _midnight(first, tz), "end": _midnight(last + dt.timedelta(days=1), tz)}


def _req_range(request: Any) -> dict:
    """The range a request asks for: since/until days when both are given and valid, else a range key."""
    q = request.query_params
    if q.get("since") and q.get("until"):
        rng = custom_range(q.get("since"), q.get("until"))
        if rng:
            return rng
    return _range(q.get("range"))


def _public_range(rng: dict) -> dict:
    return {k: rng[k] for k in ("key", "label", "since", "until")}


def _listing_start() -> float:
    """Where every section's Shopify listing starts: LEARN_DAYS store days
    back. That covers the widest range, the 7-day charts and what the
    campaigns sold, and one start means the sections share one listing."""
    return _midnight(_today() - dt.timedelta(days=LEARN_DAYS - 1), config.store_tz())


def _tonight() -> float:
    return _midnight(_today() + dt.timedelta(days=1), config.store_tz())


def _pixels() -> list[dict]:
    """Every dataset we send to, by id, name and role. Never the token."""
    out = []
    for p in meta_capi.destinations():
        pid = p["pixel_id"]
        out.append({"pixel_id": pid, "role": "main" if pid == config.META_PIXEL_ID else "backup",
                    "name": watchdog.pixel_name(pid)})
    return out


# --- Shopify orders (shared by every section) ---------------------------------------

ORDERS_TTL = 60
_orders_cache: dict[float, tuple[float, list[dict]]] = {}     # window start -> (fetched at, orders)
_inflight: dict[float, asyncio.Task] = {}


async def _fetch_orders(start: float) -> list[dict]:
    since = dt.datetime.fromtimestamp(start, dt.timezone.utc).isoformat(timespec="seconds")
    orders = await shopify.list_orders_since(since)
    _orders_cache[start] = (time.time(), orders)
    return orders


async def _shopify_orders(start: float) -> list[dict]:
    """Orders created since `start` (possibly more; callers filter). The page
    loads four sections at once, so they share one Shopify listing a minute."""
    now = time.time()
    for s, (at, orders) in list(_orders_cache.items()):
        if now - at > ORDERS_TTL:
            _orders_cache.pop(s, None)
        elif s <= start:
            return orders
    loop = asyncio.get_running_loop()
    task = _inflight.get(start)
    if task is None or task.done() or task.get_loop() is not loop:
        task = loop.create_task(_fetch_orders(start))
        _inflight[start] = task
        task.add_done_callback(lambda t: _inflight.pop(start, None) if _inflight.get(start) is t else None)
    # Shielded: one browser tab closing must not cancel the fetch the others wait on.
    return await asyncio.shield(task)


def _shopify_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"Couldn't load orders from Shopify (Shopify answered {e.response.status_code})."
    return f"Couldn't load orders from Shopify ({type(e).__name__})."


async def _orders_from(start: float) -> tuple[list[dict], str]:
    try:
        return await _shopify_orders(start), ""
    except Exception as e:
        log.warning("hub: Shopify order list failed: %s", type(e).__name__)
        return [], _shopify_error(e)


_crediting: set = set()                         # order ids being decided in the background
_identity_tried: dict[str, float] = {}          # order id -> when its unnamed ad was last looked for


async def _credit_missing(orders: list[dict]) -> None:
    """Orders the tracker has on record as sent or skipped but never credited
    (placed before go-live, mostly) get the same resolver's decision, with
    everything the tracker can find (their browser, Shopify's visit record),
    stored so it is decided once. A sale the tracker sent as a bare click
    (#c3711) gets its ad named when Shopify's visit record or its landing page
    proves it is that click (tracking.refresh_identity: names only, nothing is
    sent), tried again at most every IDENTITY_RETRY while it stays unnamed.
    Only new sales: rebills are never credited."""
    ids = [str(o["id"]) for o in orders if o.get("id") is not None]
    rows = db.orders_by_id(ids)
    now = time.time()
    if len(_identity_tried) > 10_000:
        _identity_tried.clear()
    todo = []
    for o in orders:
        oid = str(o.get("id"))
        r = rows.get(oid)
        if not r or oid in _crediting:
            continue
        if not r["attribution"] and r["status"] in ("sent", "skipped"):
            rec = None                          # never credited: decide it
        elif attribution.needs_identity(r["attribution"]) and now - _identity_tried.get(oid, 0) >= IDENTITY_RETRY:
            rec = r["attribution"]              # sent as a bare click: look for its ad
        elif ((isinstance(r["attribution"], dict) and r["attribution"].get("fbc")
               or tracking.unclicked_without_ad(r["attribution"]) and r["status"] == "sent")
              and now - (r.get("received_at") or 0) < tracking.REALIGN_DAYS * 86400
              and now - _identity_tried.get(oid, 0) >= REALIGN_RETRY):
            rec = r["attribution"]              # sent: check it against pixel events that came in late
        else:
            continue
        if order_type(o, r)[0] == "new_sale":
            todo.append((o, rec))
    if not todo:
        return

    async def one(o: dict, rec: Optional[dict]) -> None:
        oid = str(o["id"])
        _crediting.add(oid)
        try:
            if rec is None:
                db.set_order_attribution(oid, await tracking.credit_order(o))
            else:
                _identity_tried[oid] = time.time()
                if not await tracking.refresh_identity(o, rec):
                    await tracking.realign_sent(o, rec)
        except Exception as e:
            log.warning("hub: crediting order %s failed: %s", oid, type(e).__name__)
        finally:
            _crediting.discard(oid)
    tasks = [asyncio.ensure_future(one(o, rec)) for o, rec in todo[:CREDIT_PER_REQUEST]]
    await asyncio.wait(tasks, timeout=CREDIT_WAIT)


async def _credited_orders(start: float) -> tuple[list[dict], str]:
    """The Shopify listing, with every order on record credited first."""
    orders, err = await _orders_from(start)
    if orders:
        try:
            await _credit_missing(orders)
        except Exception:
            log.exception("hub: crediting orders failed")
    return orders, err


def order_type(order: dict, stored: Optional[dict] = None) -> tuple[str, str]:
    """new_sale | rebill | skipped, and why it was skipped. Unlike
    tracking.classify_order this ignores age: a 10-day-old sale is still a sale.
    Rebills are decided by tracking.is_renewal, the same test the tracker
    sends by, so the hub never counts a sale Meta was told was a rebill. An
    order a pixel already accepted (`stored`, from db.orders_by_id) is what
    Meta got, even after an MRR tag was approved."""
    if order.get("test"):
        return "skipped", "Test order"
    if order.get("cancelled_at") or (order.get("financial_status") or "") == "voided":
        return "skipped", "Cancelled"
    reported = (stored or {}).get("reported")        # the event a pixel accepted, if any
    rebill = reported != "Purchase" if reported else tracking.is_renewal(order)
    if rebill:
        return "rebill", ""
    if (order.get("source_name") or "") in config.SKIP_SOURCE_NAMES:
        return "skipped", "Draft or POS order"
    return "new_sale", ""


def ad_credit(order: dict, stored: Optional[dict], catalog: Optional[list] = None) -> dict:
    """The click credited with a sale: the tracker's stored decision (what the
    Purchase sent to Meta carried), else the same resolver on what the order
    itself says (the old tracker's note, the first landing page, as first
    touch unless nothing else exists). Never the landing page over a later click."""
    if stored and stored.get("attribution"):
        return stored["attribution"]
    return attribution.resolve(order, catalog=catalog)["attribution"]


def _facts(orders: list[dict], start: float, end: float) -> list[dict]:
    """The orders created in [start, end), newest first, classified."""
    picked = []
    for o in orders:
        ts = tracking._parse_time(o.get("created_at"))
        if ts is not None and start <= ts < end and o.get("id") is not None:
            picked.append((ts, o))
    picked.sort(key=lambda p: p[0], reverse=True)
    stored = db.orders_by_id([str(o["id"]) for _, o in picked])
    catalog = meta_ads.cached_catalog() if config.META_AD_ACCOUNT_IDS else []
    out = []
    for ts, o in picked:
        oid = str(o["id"])
        kind, reason = order_type(o, stored.get(oid))
        # Only new sales are credited to ads: a rebill is billed by the
        # subscription app, not by anyone clicking.
        credit = ad_credit(o, stored.get(oid), catalog) if kind == "new_sale" else None
        out.append({"order": o, "id": oid, "ts": ts, "type": kind, "reason": reason,
                    "revenue": _money(o.get("total_price")), "credit": credit, "stored": stored.get(oid)})
    return out


def _currency(orders: list[dict]) -> str:
    return next((o["currency"] for o in orders if o.get("currency")), "USD")


def _meta_credited(f: dict) -> bool:
    return f["type"] == "new_sale" and bool((f["credit"] or {}).get("meta"))


# --- Meta reads ---------------------------------------------------------------------

async def _ads(since: str, until: str) -> dict:
    try:
        return await meta_ads.ad_insights(since, until)
    except Exception as e:                      # ad_insights reports errors itself; this is a backstop
        log.warning("hub: ad insights failed: %s", type(e).__name__)
        return {"connected": False, "rows": [], "currency": "",
                "error": f"Couldn't read ad spend from Meta ({type(e).__name__})."}


async def _daily_spend(since: str, until: str) -> Optional[dict]:
    """Spend per day, or None when it couldn't all be read (no spend line is
    drawn then, rather than a line of false $0 days)."""
    try:
        return await meta_ads.daily_spend(since, until)
    except Exception as e:
        log.warning("hub: daily spend failed: %s", type(e).__name__)
        return None


async def _campaign_days(since: str, until: str) -> Optional[dict]:
    """Which campaigns spent on each day, or None when it couldn't all be read."""
    try:
        return await meta_ads.campaign_daily_spend(since, until)
    except Exception as e:
        log.warning("hub: campaign spend per day failed: %s", type(e).__name__)
        return None


async def _names(ad_ids) -> dict[str, dict]:
    """Meta's own ad, ad set and campaign names by ad id ({} when unknown)."""
    try:
        return await meta_ads.ad_names(sorted({str(a) for a in ad_ids if a}))
    except Exception as e:                      # ad_names never raises; this is a backstop
        log.warning("hub: ad names failed: %s", type(e).__name__)
        return {}


def _credited_ad_ids(facts: list[dict]) -> set[str]:
    """Every ad id the store-confirmed sales name, as seller or assist."""
    ids = set()
    for f in facts:
        if _meta_credited(f):
            for a in [f["credit"], *_assists(f["credit"])]:
                ad_id = str(a.get("ad_id") or "").strip()
                if ad_id:
                    ids.add(ad_id)
    return ids


def _ad_label(a: dict, names: dict, ad_id: str = "") -> dict:
    """An ad as the owner sees it: Meta's names for its id, else the names its
    link carried (only a fallback: links don't agree on which of utm_content
    and utm_term holds the ad)."""
    ad_id = ad_id or str(a.get("ad_id") or "").strip()
    m = names.get(ad_id) or {}
    return {"ad_id": ad_id,
            **{k: m.get(k) or str(a.get(k) or "").strip() for k in ("ad_name", "adset_name", "campaign_name")}}


_shop = {"name": "", "at": 0.0}


async def _store_name() -> str:
    """Shopify's shop name, read at most once a day (every 10 min after a failure)."""
    now = time.time()
    fresh = 86400 if _shop["name"] else 600
    if now - _shop["at"] < fresh:
        return _shop["name"] or _fallback_name()
    _shop["at"] = now
    try:
        name = str((await shopify.get_shop()).get("name") or "")
        if name:
            db.kv_set("shop_name", name)
    except Exception as e:
        log.info("hub: shop name unavailable: %s", type(e).__name__)
        name = db.kv_get("shop_name") or ""
    _shop["name"] = name
    return name or _fallback_name()


ORDER_COUNT_TTL = 60                # Shopify's all-time order count, asked at most once a minute
_order_count: dict[str, Any] = {"at": 0.0, "n": None}


async def _orders_all_time() -> Optional[int]:
    """Shopify's all-time order count for the header, at most a minute old; the
    last one read (kept across restarts) when Shopify doesn't answer, else None."""
    now = time.time()
    if now - _order_count["at"] < ORDER_COUNT_TTL:
        return _order_count["n"]
    _order_count["at"] = now
    try:
        n = await shopify.order_count()
        db.kv_set("orders_all_time", str(n))
    except Exception as e:
        log.info("hub: order count unavailable: %s", type(e).__name__)
        kept = db.kv_get("orders_all_time")
        n = _order_count["n"] if _order_count["n"] is not None else (int(kept) if kept and kept.isdigit() else None)
    _order_count["n"] = n
    return n


SALES_TTL = 60                      # new orders are added to the all-time sales at most once a minute
SALES_RECOUNT = 1800                # and every order is summed again every half hour (refunds, edits)
SALES_FIELDS = "id,current_total_price,cancelled_at,test"
_sales: dict[str, Any] = {"at": 0.0, "full_at": 0.0, "total": None, "last_id": 0, "task": None}


def _sale_value(o: dict) -> float:
    """An order's part of the all-time sales: its current total (after refunds and
    edits, in the shop's currency); nothing for a test or cancelled order."""
    if o.get("test") or o.get("cancelled_at"):
        return 0.0
    try:
        return float(o.get("current_total_price") or 0)
    except (TypeError, ValueError):
        return 0.0


async def _count_sales(full: bool) -> None:
    start = 0 if full else int(_sales["last_id"] or 0)
    total = 0.0 if full else float(_sales["total"] or 0.0)
    last = start
    for o in await shopify.orders_after(start, SALES_FIELDS):
        total += _sale_value(o)
        last = max(last, int(o["id"]))
    now = time.time()
    _sales.update(total=round(total, 2), last_id=last, full_at=now if full else _sales["full_at"])
    db.kv_set("sales_all_time", json.dumps({k: _sales[k] for k in ("total", "last_id", "full_at")}))


async def _sales_all_time() -> Optional[float]:
    """Every dollar the store has taken, all time, for the header: the sum of every
    order's current total (refunds and edits taken off; test and cancelled orders
    left out). New orders are added at most once a minute; the whole sum is redone
    every SALES_RECOUNT in the background, so a refund on an older order shows too.
    Kept across restarts; None while Shopify has never been read."""
    now = time.time()
    if _sales["total"] is None:
        try:
            kept = json.loads(db.kv_get("sales_all_time") or "{}")
        except ValueError:
            kept = {}
        if isinstance(kept, dict) and isinstance(kept.get("total"), (int, float)):
            _sales.update(total=float(kept["total"]), last_id=int(kept.get("last_id") or 0),
                          full_at=float(kept.get("full_at") or 0.0))
    if now - _sales["at"] < SALES_TTL and _sales["total"] is not None:
        return _sales["total"]
    _sales["at"] = now
    try:
        if _sales["total"] is None:
            await _count_sales(full=True)                  # the very first count: the page waits for it once
        else:
            if now - _sales["full_at"] >= SALES_RECOUNT and not (_sales["task"] and not _sales["task"].done()):
                _sales["task"] = asyncio.ensure_future(_count_sales(full=True))
            else:
                await _count_sales(full=False)
    except Exception as e:
        log.info("hub: all-time sales unavailable: %s", type(e).__name__)
    return _sales["total"]


def _fallback_name() -> str:
    return (db.kv_get("shop_name") or config.STORE_URL.split("://")[-1] or config.SHOPIFY_STORE)


# --- advertised products ------------------------------------------------------------
# Product ROAS counts only the products the running campaigns sell. Which
# products those are is worked out with no setup: from what the store saw each
# campaign sell, else from the campaign's name.

def _lines(order: dict) -> list[dict]:
    """An order's line items: product id, title, subtotal (price x quantity),
    quantity, and whether it was free (a price of exactly 0, like a gift)."""
    out = []
    for i in order.get("line_items") or []:
        if not isinstance(i, dict):
            continue
        try:
            qty = float(i.get("quantity") if i.get("quantity") not in (None, "") else 1)
        except (TypeError, ValueError):
            qty = 1.0
        qty = qty if math.isfinite(qty) and qty > 0 else 0.0
        priced = i.get("price") not in (None, "")
        price = max(0.0, _money(i.get("price"))) if priced else 0.0
        out.append({"product_id": shopify.numeric_id(i.get("product_id")),
                    "title": str(i.get("title") or i.get("name") or "").strip()[:200],
                    "subtotal": price * qty, "qty": qty, "free": priced and price == 0})
    return out


def advertised_share(order: dict, products: set) -> float:
    """How much of an order is advertised products, 0 to 1: their line-item
    subtotal over all of the order's. A mixed order counts in proportion."""
    lines = _lines(order)
    ads = [ln for ln in lines if ln["product_id"] in products and not ln["free"]]
    if not ads:
        return 0.0
    total = sum(ln["subtotal"] for ln in lines)
    if total > 0:
        return sum(ln["subtotal"] for ln in ads) / total
    qty = sum(ln["qty"] for ln in lines)             # no prices on the lines: go by quantity
    return sum(ln["qty"] for ln in ads) / qty if qty else 0.0


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def _words(campaign_name: Any) -> list[str]:
    # A number alone is a budget, a date or a year ("CBO 250", "Christmas 2026"),
    # never a product: it would find "Collagen 2500mg". "b12" or "5htp" stay.
    return [w for w in re.split(r"[^a-z0-9]+", str(campaign_name or "").lower())
            if len(w) >= 3 and w not in CAMPAIGN_WORDS and not w.isdigit()]


def _camp_key(campaign_id: Any, name: Any) -> str:
    cid = str(campaign_id or "").strip()
    return cid or ("n:" + _low(name) if _low(name) else "")


def _campaigns(rows: list[dict]) -> dict[str, dict]:
    """Campaigns in Meta insights rows (per ad or per campaign) with their total spend."""
    out: dict[str, dict] = {}
    for r in rows:
        key = _camp_key(r.get("campaign_id"), r.get("campaign_name"))
        if not key:
            continue
        c = out.setdefault(key, {"campaign_id": str(r.get("campaign_id") or ""), "campaign_name": "", "spend": 0.0})
        c["campaign_name"] = c["campaign_name"] or str(r.get("campaign_name") or "").strip()
        c["spend"] += _money(r.get("spend"))
    return out


def product_map(camps: dict[str, dict], facts: list[dict], ad_campaigns: dict[str, str]) -> tuple[dict, dict]:
    """Which Shopify products each campaign sells: ({campaign key: {product
    ids}}, {product id: title}). Learned from the new sales the store tied to
    the campaign in `facts` (its id on the sale, else its name, else the ad's
    campaign in `ad_campaigns`): the main line of each sale, the one with the
    biggest subtotal, when it is the main line of at least MAIN_LINE_SHARE of
    the campaign's sales. Cheaper add-ons in the same cart (Shipping
    Protection, an extra bottle) aren't what the ad sold. A campaign with no such sale is
    matched by name to the product titles in `facts`: a word of 3+ letters
    from its name inside a title, both in lowercase letters and digits only
    ('sperm' finds 'SpermFuel+'). A campaign that matches nothing maps to an
    empty set."""
    by_name: dict[str, str] = {}
    for k, c in camps.items():
        if _low(c["campaign_name"]):
            by_name.setdefault(_low(c["campaign_name"]), k)
    titles: dict[str, str] = {}
    sales: dict[str, int] = {k: 0 for k in camps}                 # campaign -> its sales that taught something
    mains: dict[str, dict[str, int]] = {k: {} for k in camps}     # campaign -> product -> sales it led
    for f in facts:                                  # newest first, so each product keeps its latest title
        if f["type"] not in ("new_sale", "rebill"):
            continue
        sold = [ln for ln in _lines(f["order"]) if ln["product_id"] and not ln["free"]]
        for ln in sold:
            if ln["title"]:
                titles.setdefault(ln["product_id"], ln["title"])
        if not sold or not _meta_credited(f):
            continue
        top = max(ln["subtotal"] for ln in sold)
        main = {ln["product_id"] for ln in sold if ln["subtotal"] == top}   # every line when none has a price
        c = f["credit"]
        for key in (str(c.get("campaign_id") or "").strip(), by_name.get(_low(c.get("campaign_name")), ""),
                    ad_campaigns.get(str(c.get("ad_id") or "").strip(), "")):
            if key in mains:
                sales[key] += 1
                for pid in main:
                    mains[key][pid] = mains[key].get(pid, 0) + 1
                break
    norm = {pid: _norm(t) for pid, t in titles.items()}
    out = {}
    for k, c in camps.items():
        # A buyer who adds a pricier second product makes it one sale's main
        # line; it only counts as the campaign's once that is common.
        found = {pid for pid, n in mains[k].items() if n >= MAIN_LINE_SHARE * sales[k]}
        if not found:
            words = _words(c["campaign_name"])
            found = {pid for pid, t in norm.items() if any(w in t for w in words)}
        out[k] = found
    return out, titles


def _advertising(rows: list[dict], days: Optional[dict], facts: list[dict], in_range: list[dict]) -> dict:
    """The products the campaigns spending in `rows` sell and their new sales
    in range (MRR never counts). `days` is spend per campaign per day for the
    charts; `facts` are the last LEARN_DAYS days' orders."""
    day_rows = [r for d in (days or {}).values() for r in d]
    camps = _campaigns(rows + day_rows)
    ad_campaigns = {str(r.get("ad_id")): _camp_key(r.get("campaign_id"), r.get("campaign_name"))
                    for r in rows if r.get("ad_id")}
    mapping, titles = product_map(camps, facts, ad_campaigns)
    spending = {k: c for k, c in _campaigns(rows).items() if c["spend"] > 0}
    products = set().union(*(mapping[k] for k in spending))
    count, revenue = 0, 0.0
    for f in in_range:
        share = advertised_share(f["order"], products) if f["type"] == "new_sale" else 0.0
        if share > 0:
            count += 1
            revenue += f["revenue"] * share
    listed = []
    for pid in products:
        names = sorted({spending[k]["campaign_name"] or "Unnamed campaign" for k in spending if pid in mapping[k]},
                       key=str.lower)
        listed.append({"product_id": pid, "title": titles.get(pid) or f"Product {pid}", "campaigns": names})
    listed.sort(key=lambda p: (p["title"].lower(), p["product_id"]))
    unmapped = sorted(({"campaign_name": c["campaign_name"] or "Unnamed campaign", "spend": round(c["spend"], 2)}
                       for k, c in spending.items() if not mapping[k]), key=lambda c: -c["spend"])
    # Per chart day, the products of the campaigns that spent that day (none on a day without spend).
    per_day = None
    if days is not None:
        per_day = {day: set().union(*(mapping.get(_camp_key(r.get("campaign_id"), r.get("campaign_name")), set())
                                      for r in spent if _money(r.get("spend")) > 0))
                   for day, spent in days.items()}
    return {"count": count, "revenue": round(revenue, 2), "products": listed, "unmapped": unmapped,
            "per_day": per_day}


# --- the P&L (the top section) --------------------------------------------------------

PNL_BAD_RANGE = "Pick a date range with dates like 2026-09-27, the first on or before the last."


def _pnl_range(request: Request) -> Optional[tuple[str, str]]:
    """The requested [from, to] days, today (New York) by default, or None when
    either isn't a real date or they are the wrong way round."""
    today = pnl.today()
    days = []
    for name in ("from", "to"):
        v = (request.query_params.get(name) or today).strip()
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                return None
            dt.date.fromisoformat(v)
        except ValueError:
            return None
        days.append(v)
    frm, to = days
    return (frm, to) if pnl.FIRST_DAY <= frm <= to else None


async def api_pnl(request: Request) -> dict:
    """The P&L app's numbers for a range, for the page's copy of the P&L's
    own code (see pnl.py). The P&L not answering is an error, never zeros."""
    base: dict[str, Any] = {"pnl_url": config.PNL_URL, "creative_url": config.CREATIVE_URL,
                            "core_hub_url": config.CORE_HUB_URL}
    picked = _pnl_range(request)
    if picked is None:
        return {**base, "ok": False, "error": PNL_BAD_RANGE}
    frm, to = picked
    base["range"] = {"from": frm, "to": to}
    try:
        report, (manual, manual_error), software = await asyncio.gather(
            pnl.report(frm, to), pnl.manual_lines(), pnl.software())
    except pnl.PnlError as e:
        return {**base, "ok": False, "error": str(e)}
    started = pnl.meta_sync_due(frm, to, report["last_sync"])
    if started:                                 # never awaited: the page doesn't wait on Meta
        tracking.fire_and_forget(pnl.sync_meta(pnl.sync_from(frm), to))
    return {**base, "ok": True, "pnl": report["pnl"], "manual": manual, "manual_error": manual_error,
            **software, "last_sync": report["last_sync"], "fetched_at": report["fetched_at"],
            "meta_sync_started": started, "error": ""}


# --- overview -----------------------------------------------------------------------

async def _status() -> dict:
    """The latest watchdog verdict, re-checked live when it is over 15 minutes old."""
    now = time.time()
    runs = db.watchdog_runs(now - 86400)
    latest = runs[-1] if runs else None
    problem = ""
    if latest is None or latest["run_at"] < now - 900:
        try:
            checks = await watchdog.run_checks()
            level = watchdog.worst(checks)
            db.add_watchdog_run(level, checks)
            latest = {"run_at": time.time(), "status": level, "results": checks}
            runs.append(latest)
        except Exception as e:
            log.exception("hub: live watchdog run failed")
            problem = f"Couldn't run the health checks just now ({type(e).__name__})."
    checks = latest["results"] if latest else []
    level = latest["status"] if latest else "warn"
    if problem and level == "ok":
        level = "warn"
    bad = sorted((c for c in checks if c.get("status") != "ok"),
                 key=lambda c: -watchdog.RANK.get(c.get("status"), 1))
    reasons = ([problem] if problem else []) + [c.get("detail", "") for c in bad]
    checked_at = latest["run_at"] if latest else None
    return {
        "level": level, "headline": HEADLINES.get(level, "Needs a look"), "reasons": reasons,
        "checks": [{k: c.get(k) for k in ("id", "name", "status", "detail")} for c in checks],
        "checked_at": checked_at, "checked_ago": watchdog.ago(checked_at),
        "timeline": [{"at": r["run_at"], "status": r["status"]} for r in runs],
        "mode": "test" if config.META_TEST_EVENT_CODE else "live",
    }


def _cards(facts: list[dict], ads: dict, currency: str, shop_ok: bool = True,
           adv: Optional[dict] = None) -> dict:
    """The overview cards for the orders in range. `adv` is _advertising()'s
    answer, or None when it can't be known (ads not fully read, or Shopify down)."""
    new = [f for f in facts if f["type"] == "new_sale"]
    rebills = [f for f in facts if f["type"] == "rebill"]
    new_rev = round(sum(f["revenue"] for f in new), 2)
    rebill_rev = round(sum(f["revenue"] for f in rebills), 2)
    connected = bool(ads.get("connected"))
    # Half-read spend (one ad account failing) would overstate ROAS, so it's all or nothing.
    spend = round(sum(r["spend"] for r in ads.get("rows", [])), 2) if connected else None
    meta_value = round(sum(r["meta_value"] for r in ads.get("rows", [])), 2) if connected else None
    meta_purchases = _count(sum(r["meta_purchases"] for r in ads.get("rows", []))) if connected else None
    # Sales the store tied to a Meta ad click (the creatives table's store-confirmed sales).
    ad_rev = round(sum(f["revenue"] for f in new if _meta_credited(f)), 2)
    adv = adv if connected and shop_ok else None
    product_roas = _ratio(adv["revenue"], spend) if adv else None
    cards = {
        "currency": currency,
        # The headline: sales of the products the running campaigns sell, over all ad spend.
        "product_roas": product_roas,
        "ad_roas": _ratio(ad_rev, spend),
        "meta_roas": _ratio(meta_value, spend),
        "spend": spend,
        "advertised": ({"count": adv["count"], "revenue": adv["revenue"], "products": adv["products"]} if adv
                       else {"count": None, "revenue": None, "products": []}),
        "cost_per_sale": _ratio(spend, adv["count"]) if adv else None,
        "mrr": {"count": len(rebills), "revenue": rebill_rev},
        # Spend of campaigns no product could be tied to: in the ROAS denominator, named on the page.
        "unmapped_campaigns": adv["unmapped"] if adv else [],
        "true_roas": product_roas,              # the old name, for anything that still reads it
        "meta_purchases": meta_purchases,
        "ads_connected": connected,
        # Set up but unreadable just now is a Meta hiccup, not "connect ad spend".
        "ads_configured": bool(config.META_AD_ACCOUNT_IDS),
        "ads_error": ads.get("error") or "",
        # Every new sale, whatever product: kept for API compatibility only.
        # The dashboard doesn't show them; the P&L app owns those numbers.
        "new_sales": {"count": len(new), "revenue": new_rev},
        "rebills": {"count": len(rebills), "revenue": rebill_rev},
        "total_revenue": round(new_rev + rebill_rev, 2),
        "orders": len(new) + len(rebills),
        "aov": _ratio(new_rev, len(new)),
    }
    if not shop_ok:
        # Shopify couldn't be read: sales are unknown, not zero. A 0.00x ROAS
        # would look exactly like a day of spend with no sales.
        unknown = {"count": None, "revenue": None}
        cards.update(new_sales=unknown, rebills=unknown, mrr=unknown, total_revenue=None, orders=None, aov=None,
                     ad_roas=None, cost_per_sale=None)
    return cards


SERIES_SALES = ("new_revenue", "rebill_revenue", "new_sales", "rebills", "ad_revenue",
                "advertised_revenue", "advertised_sales")
SERIES_COUNTS = {"new_sales", "rebills", "advertised_sales"}      # the rest are money


def _series(facts: list[dict], daily: Optional[dict], shop_ok: bool = True,
            day_products: Optional[dict] = None) -> dict:
    """The last 7 days. advertised_* count, per day, the products of the
    campaigns that spent that day (a day without spend has none); they are
    unknown (None) without `day_products`. ad_revenue is the sales the store
    tied to a Meta ad click. MRR is rebill_*."""
    tz = config.store_tz()
    today = _today()
    days = [(today - dt.timedelta(days=6 - i)).isoformat() for i in range(7)]
    idx = {d: i for i, d in enumerate(days)}
    out = {"days": days, **{k: [0 if k in SERIES_COUNTS else 0.0] * 7 for k in SERIES_SALES},
           "spend": [round(daily.get(d, 0.0), 2) for d in days] if daily is not None else [None] * 7}
    if not shop_ok:                             # unknown days, not days without sales
        out.update({k: [None] * 7 for k in SERIES_SALES})
        return out
    if day_products is None:
        out.update(advertised_revenue=[None] * 7, advertised_sales=[None] * 7)
    for f in facts:
        i = idx.get(dt.datetime.fromtimestamp(f["ts"], tz).date().isoformat())
        if i is None:
            continue
        if f["type"] == "new_sale":
            out["new_sales"][i] += 1
            out["new_revenue"][i] = round(out["new_revenue"][i] + f["revenue"], 2)
            if _meta_credited(f):
                out["ad_revenue"][i] = round(out["ad_revenue"][i] + f["revenue"], 2)
            share = advertised_share(f["order"], day_products.get(days[i], set())) if day_products is not None else 0
            if share > 0:
                out["advertised_sales"][i] += 1
                out["advertised_revenue"][i] = round(out["advertised_revenue"][i] + f["revenue"] * share, 2)
        elif f["type"] == "rebill":
            out["rebills"][i] += 1
            out["rebill_revenue"][i] = round(out["rebill_revenue"][i] + f["revenue"], 2)
    return out


def _quality(now: float) -> list[dict]:
    out = []
    for p in _pixels():
        pid = p["pixel_id"]
        try:
            data = json.loads(db.kv_get(f"emq:{pid}") or "{}")
        except ValueError:
            data = {}
        scores = data.get("scores") if isinstance(data, dict) else None
        scores = scores if isinstance(scores, dict) else {}
        # Same choice as the watchdog's match-quality check, so the card and the tile agree.
        event = watchdog.emq_event(scores) or "Purchase"
        entry = scores.get(event) if isinstance(scores.get(event), dict) else {}
        keys = {k: round(v) for k, v in (entry.get("keys") or {}).items()
                if k and isinstance(v, (int, float))}
        stats = db.event_stats(now - 86400, pid)["by_event"]
        out.append({
            **p,
            "emq": {"event": event, "score": entry.get("score"),
                    "taken_at": data.get("taken_at") if entry.get("score") is not None else None},
            "emq_history": [{"at": r["taken_at"], "score": r["score"]}
                            for r in db.emq_history(pid, event, now - 30 * 86400)],
            "meta_keys": keys,
            "events_24h": {"sent": sum(v.get("sent", 0) for v in stats.values()),
                           "failed": sum(v.get("failed", 0) for v in stats.values())},
            "last_sent_ago": watchdog.ago(db.last_sent_at(pid)),
        })
    return out


def _coverage(now: float) -> dict:
    keys = [set(m.split(",")) for m in db.purchase_match_keys(now - 7 * 86400)]
    out: dict[str, Any] = {"purchases": len(keys)}
    for k in COVERAGE_KEYS:
        out[k] = round(100 * sum(1 for m in keys if k in m) / len(keys)) if keys else None
    return out


async def api_overview(request: Request) -> dict:
    rng = _req_range(request)
    now = time.time()
    tz = config.store_tz()
    today = _today()
    week = ((today - dt.timedelta(days=6)).isoformat(), today.isoformat())
    # One listing covers the range, the 7-day sparklines and what each campaign
    # sold in the last 30 days; every range ends tonight.
    start, end = _listing_start(), _tonight()
    (orders, shop_err), ads, daily, days, status, name, all_time, sales = await asyncio.gather(
        _credited_orders(start),
        _ads(rng["since"], rng["until"]),
        _daily_spend(*week),
        _campaign_days(*week),
        _status(),
        _store_name(),
        _orders_all_time(),
        _sales_all_time(),
    )
    facts = _facts(orders, start, end)
    in_range = [f for f in facts if rng["start"] <= f["ts"] < rng["end"]]
    shop_ok = not shop_err
    connected = bool(ads.get("connected"))
    adv = _advertising(ads.get("rows", []), days, facts, in_range) if connected and shop_ok else None
    return {
        "generated_at": dt.datetime.now(tz).isoformat(timespec="seconds"),
        "store": {"name": name, "domain": config.STORE_URL, "timezone": getattr(tz, "key", "UTC")},
        "range": _public_range(rng),
        "status": status,
        "orders_all_time": all_time,
        # Every dollar the store has taken, toward the goal in the header.
        "sales_all_time": sales, "sales_goal": config.SALES_GOAL,
        "cards": _cards(in_range, ads, _currency(orders), shop_ok, adv),
        "series": _series(facts, daily if connected else None, shop_ok, adv["per_day"] if adv else None),
        "quality": _quality(now),
        "coverage": _coverage(now),
        "error": shop_err,
    }


# --- orders -------------------------------------------------------------------------

def _items(order: dict) -> str:
    lines = [i for i in (order.get("line_items") or []) if isinstance(i, dict)]
    parts = []
    for i in lines[:2]:
        title = str(i.get("title") or i.get("name") or "Item").strip()
        parts.append(f"{title} x{i.get('quantity') or 1}")
    text = ", ".join(parts)
    return text + (f" +{len(lines) - 2} more" if len(lines) > 2 else "")


def _assists(credit: Optional[dict]) -> list[dict]:
    """The earlier ads stored with a sale (attribution.resolve)."""
    helped = (credit or {}).get("assists")
    return [a for a in helped if isinstance(a, dict)] if isinstance(helped, list) else []


def _assist_name(a: dict) -> str:
    name = str(a.get("ad_name") or "").strip()
    return name or (f"Ad {a['ad_id']}" if a.get("ad_id") else UNNAMED_AD)


def _ad(credit: Optional[dict], names: Optional[dict] = None) -> Optional[dict]:
    """The ad that got a sale and the ads that assisted it, named by Meta
    when their ad id is known to it (`names`), else by their link."""
    if not credit or not credit.get("meta"):
        return None
    names = names or {}
    seller = _ad_label(credit, names)
    ad = {"click": bool(credit.get("click")), "ad_name": seller["ad_name"], "adset_name": seller["adset_name"],
          "campaign_name": seller["campaign_name"], "ad_id": seller["ad_id"], "source": credit.get("source") or ""}
    # The ad that got the sale is never its own assist (its name may have been found after the sale).
    helped = [h for h in (_ad_label(a, names) for a in _assists(credit)[:attribution.ASSISTS_MAX])
              if not attribution.same_ad(h, seller)]
    if helped:                                  # only sales that had help carry the key
        ad["assists"] = [{"ad_name": _assist_name(a), "adset_name": a["adset_name"],
                          "campaign_name": a["campaign_name"]} for a in helped]
    return ad


def _time_local(when: dt.datetime) -> str:
    return f"{when:%b} {when.day}, {when.hour % 12 or 12}:{when:%M} {'AM' if when.hour < 12 else 'PM'}"


def before_go_live(f: dict, go_live: float) -> bool:
    """A sale or rebill placed before go-live: WeTracked sent it live. That
    holds whatever the tracker stored for it (skipped as before the tracking
    start, or 'sent' in test mode, which only reached Test Events). The one
    exception is an order the tracker itself sent live after go-live (forced
    by Claude's tool), which keeps its sent label."""
    if f["type"] not in ("new_sale", "rebill") or f["ts"] >= go_live:
        return False
    stored = f["stored"] or {}
    return not (stored.get("status") == "sent" and (stored.get("sent_at") or 0) >= go_live)


def channel(f: dict) -> Optional[str]:
    """Where a new sale came from: 'Meta ads', or the channel the resolver
    found for a sale no Meta click got ('Shop app ads', 'Google', 'Direct'...)."""
    if f["type"] != "new_sale":
        return None
    credit = f["credit"] or {}
    if credit.get("channel"):
        return credit["channel"]
    # A record stored before channels existed.
    return attribution.META_CHANNEL if credit.get("meta") else attribution.channel_of(f["order"])


def _order_row(f: dict, sent: Optional[dict], pixels: list[dict], tz, names: Optional[dict] = None,
               go_live: float = 0.0) -> dict:
    """One order for the table. Built field by field so the customer's contact
    details in the Shopify order can never slip through."""
    o, stored = f["order"], f["stored"] or {}
    when = dt.datetime.fromtimestamp(f["ts"], tz)
    early = before_go_live(f, go_live)
    # Before go-live the tracker ran in test mode: what it sent then only reached Test Events.
    reached = {pid: t for pid, t in ((sent or {}).get("pixels") or {}).items() if not early or t >= go_live}
    keys = set(((sent or {}).get("match_keys") or "").split(","))
    return {
        "id": f["id"],
        "name": o.get("name") or f["id"],
        "created_at": when.isoformat(timespec="seconds"),
        "time_local": _time_local(when),
        "total": f["revenue"],
        "currency": o.get("currency") or "USD",
        "items": _items(o),
        "type": BEFORE_GO_LIVE if early else f["type"],
        # The owner calls subscription rebills MRR; the type value stays "rebill".
        "type_label": (BEFORE_GO_LIVE_LABEL if early else
                       {"new_sale": "New sale", "rebill": "MRR"}.get(f["type"]) or f"Skipped: {f['reason']}"),
        "tracker_status": stored.get("status") or "not_seen",
        "error": (stored.get("last_error") or "")[:300] or None,
        "pixels": [{**p, "sent": p["pixel_id"] in reached} for p in pixels],
        "ad": _ad(f["credit"], names),
        "channel": channel(f),
        # A Meta sale whose click came through the listicle (the page's "Listicle" badge).
        "listicle": _meta_credited(f) and came_through_listicle(f["credit"]),
        # Through the quiz funnel ("Quiz Funnel" badge), or the quiz then the listicle ("Quiz assist").
        "landing": landing_of(f["credit"]) if _meta_credited(f) else "",
        "quiz_assist": _meta_credited(f) and attribution.quiz_assist(f["credit"]),
        "details": {name: key in keys for name, key in DETAIL_KEYS.items()},
        # WeTracked sent every order placed before go-live under its own event
        # id: a resend would count twice, and the server refuses it.
        "can_resend": f["type"] in ("new_sale", "rebill") and f["ts"] >= go_live,
    }


# Which new sales started a subscription (a "Sub" tag in the orders list). A
# plan never changes after checkout, so each answer is kept for good; a failed
# read is tried again later and never blocks the list.
SUB_LOOKUPS_PER_LOAD = 12
_sub_backoff = {"until": 0.0}


async def _subscription_flags(order_ids: list[str]) -> dict[str, bool]:
    out, todo = {}, []
    for oid in order_ids:
        v = db.kv_get(f"sub:{oid}")
        if v in ("1", "0"):
            out[oid] = v == "1"
        else:
            todo.append(oid)
    if not todo or time.time() < _sub_backoff["until"]:
        return out

    async def one(oid):
        try:
            on = await shopify.order_on_subscription(oid)
        except Exception as e:                    # scope, throttling, timeout: try again later
            _sub_backoff["until"] = time.time() + 600
            log.info("Subscription lookup for order %s failed: %s", oid, type(e).__name__)
            return
        db.kv_set(f"sub:{oid}", "1" if on else "0")
        out[oid] = on
    await asyncio.gather(*(one(o) for o in todo[:SUB_LOOKUPS_PER_LOAD]))
    return out


async def api_orders(request: Request) -> dict:
    rng = _req_range(request)
    try:
        limit = int(request.query_params.get("limit") or 100)
    except ValueError:
        limit = 100
    limit = max(1, min(limit, 200))
    orders, err = await _credited_orders(_listing_start())
    facts = _facts(orders, rng["start"], rng["end"])
    shown = facts[:limit]
    sent = db.sent_order_events([f["id"] for f in shown])
    pixels, tz, go_live = _pixels(), config.store_tz(), tracking.go_live_at()
    names = await _names(_credited_ad_ids(shown))
    subs = await _subscription_flags([f["id"] for f in shown if f["type"] == "new_sale"])
    rows = [_order_row(f, sent.get(f["id"]), pixels, tz, names, go_live) for f in shown]
    for r in rows:
        r["subscription"] = subs.get(r["id"])   # the first order of a subscription; None when not known yet
    return {"orders": rows, "count": len(facts), "error": err}


# --- creatives ----------------------------------------------------------------------

# Not assists: one sale can have help from several ads in the same ad set or
# campaign, so a rollup counts the sales, not the ads (see _assisted).
SUM_FIELDS = ("spend", "meta_purchases", "meta_value", "store_sales", "store_revenue")
# Each click/view field and the Meta total it splits.
SPLIT_OF = dict(zip(meta_ads.SPLIT_KEYS, ("meta_purchases", "meta_purchases", "meta_value", "meta_value")))


def _split(r: dict) -> dict:
    """Meta's click/view split for one ad: unknown (None) when Meta didn't
    split a non-zero total, 0 when there was nothing to split."""
    out = {}
    for key, total in SPLIT_OF.items():
        v = r.get(key)
        known = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        out[key] = float(v) if known else (None if r.get(total) else 0.0)
    return out


def _known_sum(items: list[dict], key: str) -> Optional[float]:
    # Unknown when any part is: a split of only some ads wouldn't add up to the total beside it.
    vals = [i[key] for i in items]
    return None if any(v is None for v in vals) else sum(vals)


def _split_out(src: dict) -> dict:
    return {k: None if src.get(k) is None else round(src[k], 2) if k.endswith("_value") else _count(src[k])
            for k in SPLIT_OF}


def _entry(r: dict) -> dict:
    return {"ad_id": str(r.get("ad_id") or ""), "ad_name": str(r.get("ad_name") or ""),
            "adset_id": str(r.get("adset_id") or ""), "adset_name": str(r.get("adset_name") or ""),
            "campaign_id": str(r.get("campaign_id") or ""), "campaign_name": str(r.get("campaign_name") or ""),
            "spend": r.get("spend", 0.0), "impressions": r.get("impressions", 0), "clicks": r.get("clicks", 0),
            "meta_purchases": r.get("meta_purchases", 0.0), "meta_value": r.get("meta_value", 0.0), **_split(r),
            "meta_add_to_carts": r.get("meta_add_to_carts", 0.0),
            "store_sales": 0, "store_revenue": 0.0, "orders": [], "assists": 0, "assist_orders": [],
            "assist_ids": [],                   # order ids beside assist_orders, for rollups only
            "assist_by": [],                    # beside them, the row of the ad that got each sale
            "via_listicle": 0, "via_quiz": 0}


def _closer_label(row: Optional[dict]) -> str:
    """The creative that got an assisted sale, as "ad set · ad"."""
    if not row:
        return UNNAMED_AD
    ad = row["ad_name"] or (f"Ad {row['ad_id']}" if row["ad_id"] else UNNAMED_AD)
    return f"{row['adset_name']} · {ad}" if row["adset_name"] else ad


def _assisted(items: list[dict]) -> list[str]:
    """The sales any of these ads assisted, each once, however many of them helped it."""
    labels: dict[str, str] = {}
    for i in items:
        for oid, label in zip(i["assist_ids"], i["assist_orders"]):
            labels.setdefault(oid, label)
    return list(labels.values())


def _assisted_closers(items: list[dict]) -> list[str]:
    """Beside _assisted: the creative that got each of those sales."""
    closers: dict[str, str] = {}
    for i in items:
        for oid, row in zip(i["assist_ids"], i["assist_by"]):
            closers.setdefault(oid, _closer_label(row))
    return list(closers.values())


def _totals(items: list[dict]) -> dict:
    t = {k: sum(i[k] for i in items) for k in SUM_FIELDS}
    helped = _assisted(items)
    return {"spend": round(t["spend"], 2), "meta_purchases": _count(t["meta_purchases"]),
            "meta_value": round(t["meta_value"], 2), "store_sales": t["store_sales"],
            "store_revenue": round(t["store_revenue"], 2), "assists": len(helped), "assist_orders": helped,
            "assist_closers": _assisted_closers(items),
            **_split_out({k: _known_sum(items, k) for k in SPLIT_OF}),
            "roas_meta": _ratio(t["meta_value"], t["spend"]), "roas_store": _ratio(t["store_revenue"], t["spend"])}


def _low(s: Any) -> str:
    return str(s or "").strip().lower()


def _ad_out(a: dict) -> dict:
    return {"ad_id": a["ad_id"], "ad_name": a["ad_name"] or (f"Ad {a['ad_id']}" if a["ad_id"] else UNNAMED_AD),
            "adset_name": a["adset_name"], "spend": round(a["spend"], 2), "impressions": a["impressions"],
            "clicks": a["clicks"], "meta_purchases": _count(a["meta_purchases"]),
            "meta_value": round(a["meta_value"], 2), **_split_out(a), "store_sales": a["store_sales"],
            "store_revenue": round(a["store_revenue"], 2), "roas_meta": _ratio(a["meta_value"], a["spend"]),
            "roas_store": _ratio(a["store_revenue"], a["spend"]), "orders": a["orders"],
            "assists": a["assists"], "assist_orders": a["assist_orders"],
            "assist_closers": [_closer_label(x) for x in a["assist_by"]], "via_listicle": a["via_listicle"],
            "via_quiz": a.get("via_quiz", 0),
            "meta_add_to_carts": _count(a.get("meta_add_to_carts", 0.0))}


def _small(ads: list[dict]) -> dict:
    """The ads summed in one line instead of a row each: '+N ads under $15'."""
    return {"count": len(ads), "spend": round(sum(a["spend"] for a in ads), 2),
            "store_sales": sum(a["store_sales"] for a in ads),
            "store_revenue": round(sum(a["store_revenue"] for a in ads), 2),
            "meta_purchases": _count(sum(a["meta_purchases"] for a in ads)),
            "meta_value": round(sum(a["meta_value"] for a in ads), 2)}


def landing_of(credit: Optional[dict]) -> str:
    """Where a sale's ad click landed before the store, by the stored record's
    lp and ids_stripped: 'quiz' (the quiz funnel), 'listicle' (a landing page
    the link named with lp, or the old listicle that stripped the ad ids) or ''
    (straight to the product page)."""
    c = credit or {}
    return attribution.landing_kind(c.get("lp"), c.get("ids_stripped"))


def came_through_listicle(credit: Optional[dict]) -> bool:
    """Whether a sale's ad click came through a listicle (not the quiz)."""
    return landing_of(credit) == attribution.LISTICLE


def _named(c: dict) -> tuple[str, str]:
    return str(c.get("ad_id") or "").strip(), str(c.get("ad_name") or "").strip()


def build_creatives(facts: list[dict], rows: list[dict], group: str, names: Optional[dict] = None,
                    min_spend: Optional[float] = None, every_ad: bool = False) -> dict:
    """Meta's per-ad numbers side by side with the sales Shopify confirms for
    each ad, grouped campaign > ad set (or batch) > ad. A sale counts for the
    last ad its buyer clicked; earlier ads it names count as assists, which
    never add to sales or revenue. An ad with sales but no delivery in the
    range gets its row from the sale, named by Meta by its id (`names`) when
    Meta knows it, else by its link.

    With `min_spend`, only ads that spent that much get a row. The rest are
    summed in `small`: a group's own under its ads, and at the campaign the
    ads of its groups where no ad spent that much (those groups aren't
    listed). Every total still counts every ad, so rows plus small lines add
    up to the totals."""
    names = names or {}
    entries: list[dict] = []
    by_id: dict[str, dict] = {}
    by_name: dict[str, list[dict]] = {}

    def add(e: dict) -> dict:
        entries.append(e)
        if e["ad_id"]:
            by_id.setdefault(e["ad_id"], e)
        if _low(e["ad_name"]):
            by_name.setdefault(_low(e["ad_name"]), []).append(e)
        return e

    def match(c: dict) -> dict:
        """The row for the ad a sale or an assist names."""
        ad_id, name = _named(c)
        e = by_id.get(ad_id) if ad_id else None
        # Names only stand in for a missing id. An id Meta didn't report (a
        # paused ad) gets its own row below, not a same-named duplicate's.
        if e is None and name and not ad_id:
            # The same creative often runs in several ad sets: prefer the one the link named.
            same = by_name.get(name.lower(), [])
            e = next((x for x in same if _low(x["adset_name"]) == _low(c.get("adset_name"))),
                     same[0] if same else None)
            if e is None and _low(c.get("adset_name")):
                # Links don't agree on which name is the ad's: try them the other way round.
                e = next((x for x in by_name.get(_low(c.get("adset_name")), [])
                          if _low(x["adset_name"]) == name.lower()), None)
        if e is None:                           # Meta reported no delivery for it in the range
            e = add(_entry({**c, **_ad_label({**c, "ad_name": name}, names, ad_id), "spend": 0.0}))
        return e

    for r in sorted(rows, key=lambda r: -(r.get("spend") or 0)):
        add(_entry(r))

    new = [f for f in facts if f["type"] == "new_sale"]
    confirmed = [f for f in new if _meta_credited(f)]
    unlabelled = {"store_sales": 0, "store_revenue": 0.0, "orders": []}
    tagged = 0
    sold_by: dict[str, dict] = {}               # order id -> the row its sale went to
    for f in confirmed:
        c = f["credit"]
        label = f["order"].get("name") or f["id"]
        if not any(_named(c)):
            unlabelled["store_sales"] += 1
            unlabelled["store_revenue"] += f["revenue"]
            unlabelled["orders"].append(label)
            continue
        tagged += 1
        e = sold_by[f["id"]] = match(c)
        e["store_sales"] += 1
        e["store_revenue"] += f["revenue"]
        e["orders"].append(label)
        # A buyer who started in the quiz is "via quiz" even when the quiz sent them on through the listicle.
        if landing_of(c) == attribution.QUIZ or attribution.quiz_assist(c):
            e["via_quiz"] = e.get("via_quiz", 0) + 1
        elif came_through_listicle(c):
            e["via_listicle"] += 1

    # After every sale has its row, so an assist for a paused ad reuses the
    # row a sale made for it (which knows its ad set and campaign ids).
    for f in confirmed:
        label = f["order"].get("name") or f["id"]
        counted = [sold_by.get(f["id"])]
        for a in _assists(f["credit"])[:attribution.ASSISTS_MAX]:
            if not any(_named(a)):
                continue
            e = match(a)
            # The ad that got the sale is never its own assist, and an ad
            # assists one sale once however its links named it.
            if any(e is x for x in counted):
                continue
            counted.append(e)
            e["assists"] += 1
            e["assist_orders"].append(label)
            e["assist_ids"].append(f["id"])
            e["assist_by"].append(counted[0])     # the row of the ad that got the sale (None: no ad named)

    # Links may carry names only; resolve them to Meta's ids so they join the right campaign.
    camp_names: dict[str, str] = {}
    camp_ids: dict[str, str] = {}
    set_names: dict[str, str] = {}
    set_ids: dict[tuple, str] = {}
    for e in entries:
        if e["campaign_id"]:
            camp_names.setdefault(e["campaign_id"], e["campaign_name"])
            if _low(e["campaign_name"]):
                camp_ids.setdefault(_low(e["campaign_name"]), e["campaign_id"])
        if e["adset_id"]:
            set_names.setdefault(e["adset_id"], e["adset_name"])
            if _low(e["adset_name"]):
                set_ids.setdefault((e["campaign_id"], _low(e["adset_name"])), e["adset_id"])

    campaigns: dict[str, dict] = {}
    for e in entries:
        cid = e["campaign_id"] or camp_ids.get(_low(e["campaign_name"]), "")
        if cid:
            ckey, cname = "id:" + cid, camp_names.get(cid) or e["campaign_name"] or "Unknown campaign"
        elif _low(e["campaign_name"]):
            ckey, cname = "n:" + _low(e["campaign_name"]), e["campaign_name"]
        else:
            ckey, cname = "unknown", "Unknown campaign"
        if group == "batch":
            fam = attribution.family(e["ad_name"]).strip()
            gkey, gname = ("b:" + fam.lower(), fam) if fam else ("unknown", "Unnamed ads")
        else:
            sid = e["adset_id"] or set_ids.get((cid, _low(e["adset_name"])), "")
            if sid:
                gkey, gname = "id:" + sid, set_names.get(sid) or e["adset_name"] or "Unknown ad set"
            elif _low(e["adset_name"]):
                gkey, gname = "n:" + _low(e["adset_name"]), e["adset_name"]
            else:
                gkey, gname = "unknown", "Unknown ad set"
        camp = campaigns.setdefault(ckey, {"campaign_id": cid, "campaign_name": cname, "groups": {}})
        camp["groups"].setdefault(gkey, {"key": gkey, "name": gname, "ads": []})["ads"].append(e)

    def listed(a: dict) -> bool:
        # Only a creative that got a sale (the store's or Meta's) earns a row.
        # Spend and add to carts don't: the rest are summed in one line.
        return every_ad or a["store_sales"] > 0 or a["meta_purchases"] > 0

    out = []
    for camp in campaigns.values():
        groups, all_ads, collapsed = [], [], []
        for g in camp["groups"].values():
            ads = sorted(g["ads"], key=lambda a: (-a["store_sales"], -a["meta_purchases"], -a["spend"],
                                                  -a["assists"]))
            all_ads.extend(ads)
            shown = [a for a in ads if listed(a)]
            if not shown:                       # an ad set with no ad that spent enough: one line at the campaign
                collapsed.extend(ads)
                continue
            groups.append({"key": g["key"], "name": g["name"], **_totals(ads), "ads": [_ad_out(a) for a in shown],
                           "small": _small([a for a in ads if not listed(a)])})
        groups.sort(key=lambda g: (-g["spend"], -g["store_sales"]))
        out.append({"campaign_id": camp["campaign_id"], "campaign_name": camp["campaign_name"],
                    **_totals(all_ads), "groups": groups, "small": _small(collapsed)})
    out.sort(key=lambda c: (-c["spend"], -c["store_revenue"]))

    spend = sum(e["spend"] for e in entries)
    meta_value = sum(e["meta_value"] for e in entries)
    store_revenue = round(sum(f["revenue"] for f in confirmed), 2)
    return {
        "totals": {"spend": round(spend, 2), "meta_purchases": _count(sum(e["meta_purchases"] for e in entries)),
                   "meta_value": round(meta_value, 2),
                   **_split_out({k: _known_sum(entries, k) for k in SPLIT_OF}),
                   "store_sales": len(confirmed),
                   "store_revenue": store_revenue,
                   "meta_roas": _ratio(meta_value, spend),
                   # Ad ROAS: the sales the store traced to a Meta ad click, over spend.
                   "ad_roas": _ratio(store_revenue, spend)},
        "campaigns": out,
        "unlabelled": {**unlabelled, "store_revenue": round(unlabelled["store_revenue"], 2)},
        "url_tracking": {"tagged_orders": tagged, "meta_orders": len(confirmed)},
    }


async def api_creatives(request: Request) -> dict:
    rng = _req_range(request)
    group = "batch" if request.query_params.get("group") == "batch" else "adset"
    start = _listing_start()
    (orders, shop_err), ads = await asyncio.gather(_credited_orders(start), _ads(rng["since"], rng["until"]))
    learned = _facts(orders, start, _tonight())
    facts = [f for f in learned if rng["start"] <= f["ts"] < rng["end"]]
    rows = ads.get("rows", [])
    # Ads Meta reported delivery for already carry Meta's names; the rest are named by id.
    reported = {str(r.get("ad_id") or "") for r in rows}
    names = await _names(_credited_ad_ids(facts) - reported)
    # Only when all spend was read: without it every ad would look like it spent nothing.
    min_spend = config.HUB_MIN_AD_SPEND if ads.get("connected") else None
    built = build_creatives(facts, rows, group, names, min_spend,
                            every_ad=request.query_params.get("all") == "1")     # the agent reads every ad
    adv = _advertising(rows, None, learned, facts) if ads.get("connected") and not shop_err else None
    product_roas = _ratio(adv["revenue"], built["totals"]["spend"]) if adv else None
    built["totals"].update(product_roas=product_roas, true_roas=product_roas)
    if not ads.get("connected"):
        # With one ad account failing the rows hold only part of the spend; a
        # total ROAS on it would be overstated (the overview hides it too).
        built["totals"].update(ad_roas=None, meta_roas=None)
    if shop_err:                                # sales unknown: not a 0.00x ROAS
        built["totals"].update(ad_roas=None)
    return {
        "connected": bool(ads.get("connected")),
        "configured": bool(config.META_AD_ACCOUNT_IDS),
        "error": " ".join(e for e in (ads.get("error"), shop_err) if e),
        "currency": _currency(orders),
        "range": _public_range(rng),
        "group": group,
        # The spend an ad needs for a row of its own; None: every ad has one.
        "min_ad_spend": min_spend,
        **built,
    }


# --- assists ------------------------------------------------------------------------

def _history_known(f: dict, since: float) -> bool:
    """Whether a sale's click history is on record: it was placed after click
    history started (`since`) and the tracker credited it itself. A sale the
    tracker hasn't handled (yet) is decided from what the order itself says."""
    return f["ts"] >= since and bool((f["stored"] or {}).get("attribution"))


def build_assists(facts: list[dict], names: Optional[dict] = None, since: float = 0.0,
                  rows: Optional[list[dict]] = None, spend_known: bool = False) -> dict:
    """One row per ad that assisted at least one new sale: a Meta ad the buyer
    clicked before the one that got the sale (the last one clicked). Each row
    has the ad's Meta spend in the range from `rows` (None unless
    `spend_known`: spend isn't read), how many new sales it assisted, and the
    ads that got those sales (its closers), each with how many of them it
    closed and their value. A sale counts once per assisting ad however its
    links spelled that ad, the ad that got a sale is never its own assist, and
    MRR never counts. Ads are named by Meta by their id (`names`), else by
    their link. Sales an ad got with no earlier ad click are only counted, and
    only when their click history is on record (see _history_known): for the
    rest it is unknown, not "no earlier click"."""
    names = names or {}
    rows = rows or []
    confirmed = [f for f in facts if _meta_credited(f)]
    # A link without an ad id names the ad only: tie that name to the one ad id
    # that goes by it (in the sales, or in Meta's delivery), so both spellings
    # of an ad count as one ad.
    ids_by_name: dict[str, set] = {}
    for f in confirmed:
        for a in [f["credit"], *_assists(f["credit"])]:
            ad_id, name = _named(a)
            if not ad_id:
                continue
            for n in (name, (names.get(ad_id) or {}).get("ad_name")):
                if _low(n):
                    ids_by_name.setdefault(_low(n), set()).add(ad_id)
    spend_by_id: dict[str, float] = {}
    spend_by_name: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        ad_id, spend = str(r.get("ad_id") or "").strip(), _money(r.get("spend"))
        if ad_id:
            spend_by_id[ad_id] = spend_by_id.get(ad_id, 0.0) + spend
            if _low(r.get("ad_name")):
                ids_by_name.setdefault(_low(r.get("ad_name")), set()).add(ad_id)
        if _low(r.get("ad_name")):
            spend_by_name.setdefault(_low(r.get("ad_name")), []).append((_low(r.get("adset_name")), spend))

    def key(a: dict) -> str:
        """One key per ad: its id, else the id its name belongs to, else its name."""
        ad_id, name = _named(a)
        if ad_id:
            return "id:" + ad_id
        if not name:
            return ""
        ids = ids_by_name.get(name.lower(), set())
        return "id:" + next(iter(ids)) if len(ids) == 1 else "n:" + name.lower()

    def label(a: dict, k: str) -> dict:
        out = _ad_label(a, names, k[3:] if k.startswith("id:") else "")
        if not out["ad_name"]:
            out["ad_name"] = f"Ad {out['ad_id']}" if out["ad_id"] else UNNAMED_AD
        return out

    def spend_of(k: str, ad: dict) -> Optional[float]:
        if not spend_known:
            return None
        if k.startswith("id:"):
            return round(spend_by_id.get(k[3:], 0.0), 2)
        # Known by name only: the ad of that name, in the ad set its link named when Meta has it there.
        same = spend_by_name.get(k[2:], [])
        here = [s for adset, s in same if adset and adset == _low(ad["adset_name"])]
        return round(sum(here) if here else sum(s for _, s in same), 2)

    helped: dict[str, dict] = {}
    without = 0
    for f in confirmed:
        closer = f["credit"]
        ck = key(closer)                        # "" when the link didn't say which ad got the sale
        helpers: dict[str, dict] = {}
        for a in _assists(closer)[:attribution.ASSISTS_MAX]:
            k = key(a)
            if k and k != ck and k not in helpers:
                helpers[k] = a
        if not helpers:
            if _history_known(f, since):
                without += 1
            continue
        for k, a in helpers.items():
            row = helped.get(k)
            if row is None:
                row = helped[k] = {**label(a, k), "assists": 0, "closers": {}}
            row["assists"] += 1
            c = row["closers"].get(ck)
            if c is None:
                seller = label(closer, ck)
                c = row["closers"][ck] = {"ad_id": seller["ad_id"], "ad_name": seller["ad_name"],
                                          "adset_name": seller["adset_name"], "sales": 0, "value": 0.0}
            c["sales"] += 1
            c["value"] += f["revenue"]
    out = []
    for k, row in helped.items():
        closers = sorted(({**c, "value": round(c["value"], 2)} for c in row.pop("closers").values()),
                         key=lambda c: (-c["value"], -c["sales"], _low(c["ad_name"]), c["ad_id"]))
        out.append({**row, "spend": spend_of(k, row), "closers": closers})
    out.sort(key=lambda r: (-r["assists"], -(r["spend"] or 0.0), _low(r["ad_name"]), r["ad_id"]))
    return {"rows": out, "sales_without_assists": without}


def _row_names(rows: list[dict]) -> dict[str, dict]:
    """Meta's names for the ads in insights rows, by ad id (like ad_names gives)."""
    return {str(r["ad_id"]): {k: str(r.get(k) or "") for k in ("ad_name", "adset_name", "campaign_name")}
            for r in rows if r.get("ad_id")}


async def api_assists(request: Request) -> dict:
    rng = _req_range(request)
    (orders, err), ads = await asyncio.gather(_credited_orders(_listing_start()), _ads(rng["since"], rng["until"]))
    facts = _facts(orders, rng["start"], rng["end"])
    rows = ads.get("rows", [])
    # Ads Meta reported delivery for already carry Meta's names; the rest are named by id.
    reported = _row_names(rows)
    names = {**reported, **await _names(_credited_ad_ids(facts) - set(reported))}
    since = ASSISTS_FROM
    connected = bool(ads.get("connected"))
    built = build_assists(facts, names, since, rows, spend_known=connected)
    if err:                                     # sales unknown, not "no assisted sales"
        built["sales_without_assists"] = None
    days = config.ATTRIBUTION_WINDOW_DAYS
    return {
        **built,
        # A range that starts before click history did counts only the sales since then.
        "sales_without_assists_since": (_time_local(dt.datetime.fromtimestamp(since, config.store_tz()))
                                        if rng["start"] < since else ""),
        "range": _public_range(rng),
        "currency": _currency(orders),
        # Spend shows "-" without it; the creatives section says why.
        "ads_connected": connected,
        "note": (f"Each row is an ad a buyer clicked before the Meta ad that got the sale (the last one they "
                 f"clicked, within {days} days). Creatives that got the sale are those last ads, with the value of the "
                 "sales this ad helped. A sale counts once for each ad that helped it, and MRR never counts. "
                 f"Assists count from {ASSISTS_SINCE}, when click history started."),
        "error": err,
    }


# --- funnel -------------------------------------------------------------------------

FUNNEL_TTL = {"today": 60}                      # seconds; other ranges keep 5 minutes
PURCHASE_STEP = len(FUNNEL_EVENTS)              # the last step, Purchases, comes from Shopify
# range -> (expires, {browser: {"step": furthest step, "meta": from a Meta ad, "landing": '', 'listicle' or 'quiz'}})
_funnel_cache: dict[tuple, tuple] = {}
LISTICLE_ROWS = (("direct", "Product page"), ("listicle", "Listicle"), ("quiz", "Quiz Funnel"))
_ROW_KIND = {"direct": "", "listicle": attribution.LISTICLE, "quiz": attribution.QUIZ}
# The funnel's three views, as the page's switch names them.
NOT_FROM_META = "Not from Meta"
NOT_FROM_META_TIP = ("Shoppers who did not come from a Meta ad in the {days} days before: typed the site in, Google, "
                     "email, the Shop app, returning customers.")


def _from_ad(sess: dict, at: float, end: float) -> bool:
    """Whether a browser came from a Meta ad: its ad arrival or its ad click
    falls in the attribution window before `at`, up to `end` (the range's)."""
    lo = at - config.ATTRIBUTION_WINDOW_DAYS * 86400
    seen = (sess.get("ad_seen_at"), attribution.click_time(sess.get("fbc")))
    return any(t is not None and lo <= float(t) <= end for t in seen)


def _ad_params(sess: dict) -> dict:
    try:
        params = json.loads(sess.get("ad_params") or "{}")
    except (TypeError, ValueError):
        return {}
    return params if isinstance(params, dict) else {}


def _landing(sess: dict) -> str:
    """Where a browser's ad click landed before the store: 'quiz', 'listicle'
    (its link's lp, or utm tags without ad ids: the old listicle stripped them)
    or '' (the product page)."""
    params = _ad_params(sess)
    if not params:
        return ""
    lp, stripped = attribution.landing_page(params)
    return attribution.landing_kind(lp, stripped)


def _started_in_quiz(sess: dict) -> bool:
    """Whether a browser's ad click started in the quiz: straight from it to the
    store, or on through the listicle (which passes on the quiz's via=quiz)."""
    return _landing(sess) == attribution.QUIZ or str(_ad_params(sess).get("via") or "").lower().startswith(attribution.QUIZ)


def _browser_steps(rng: dict) -> dict[str, dict]:
    """Every browser the storefront pixel saw in the range: the furthest
    storefront step it reached (an index into FUNNEL_EVENTS; one whose page
    view fell just before the range still visited), whether it came from a
    Meta ad and whether that click came through the listicle. Each browser is
    judged once, from its first step in the range, so one shopper can't be
    "Meta ads" on one step and "Not from Meta" on the next; the window runs
    back from that step, not from the end of the range, so a 30-day view still
    credits its first weeks' ad clicks. Cached a few minutes: the 30-day read
    is heavy and the page asks every minute. Callers must not change it."""
    key, now = (rng["key"], rng["start"]), time.time()
    for k in [k for k, v in _funnel_cache.items() if v[0] < now]:
        del _funnel_cache[k]
    hit = _funnel_cache.get(key)
    if hit:
        return hit[1]
    end = min(rng["end"], now)
    first: dict[str, dict] = {}
    furthest: dict[str, int] = {}
    for r in db.storefront_funnel(rng["start"], rng["end"], FUNNEL_EVENTS):
        cid = r["client_id"]
        f = first.get(cid)
        if f is None or float(r["first_at"]) < float(f["first_at"]):
            first[cid] = r
        furthest[cid] = max(furthest.get(cid, 0), FUNNEL_EVENTS.index(r["event_name"]))
    viewed = db.viewed_products(rng["start"], rng["end"])
    browsers = {cid: {"step": furthest[cid], "meta": _from_ad(r, float(r["first_at"]), end),
                      "landing": _landing(r), "quiz": _started_in_quiz(r), "product": viewed.get(cid, "")}
                for cid, r in first.items()}
    _funnel_cache[key] = (now + FUNNEL_TTL.get(rng["key"], 300), browsers)
    return browsers


def _tie_sales(sales: list[dict]) -> tuple[dict[str, float], int]:
    """The browser behind each new sale, by its order's checkout token:
    ({browser: when it bought}, how many sales no known browser placed). A
    browser that bought twice is one browser."""
    tokens = {f["id"]: tracking._s(f["order"].get("checkout_token"), 100) for f in sales}
    browser = db.client_ids_by_checkout([t for t in tokens.values() if t])
    tied: dict[str, float] = {}
    untied = 0
    for f in sales:
        cid = browser.get(tokens[f["id"]]) if tokens[f["id"]] else None
        if cid:
            tied.setdefault(cid, f["ts"])
        else:
            untied += 1
    return tied, untied


def _main_product(order: dict) -> str:
    """The title of an order's main line (the biggest paid subtotal): the product it was bought for."""
    sold = [ln for ln in _lines(order) if ln["title"] and not ln["free"]]
    return max(sold, key=lambda ln: ln["subtotal"])["title"] if sold else ""


def _funnel_counts(browsers: dict[str, dict], tied: dict[str, float], err: str) -> tuple[list, list]:
    """(Meta ads, Not from Meta) browsers per step: each at the furthest step it
    reached and every step before it. Purchases are None (unknown) on `err`."""
    meta, other = [0] * len(FUNNEL_STEPS), [0] * len(FUNNEL_STEPS)
    for cid, b in browsers.items():
        top = PURCHASE_STEP if cid in tied else b["step"]
        counts = meta if b["meta"] else other
        for i in range(top + 1):
            counts[i] += 1
    if err:                                     # Shopify couldn't be read: purchases unknown, not zero
        meta[PURCHASE_STEP] = other[PURCHASE_STEP] = None
    return meta, other


# The most products the funnel's product switch offers (the ones with the most visitors).
FUNNEL_PRODUCTS_MAX = 8


def _by_product(browsers: dict[str, dict], sales: Optional[list[dict]], tied: dict[str, float],
                err: str) -> tuple[list[dict], dict]:
    """The funnel and the product page vs listicle split for each product: a
    browser belongs to the product it bought, else the first one it viewed; a
    sale to its main line. Products with the most visitors first."""
    keys = {b["product"] for b in browsers.values() if b.get("product")}
    keys |= {_main_product(f["order"]) for f in sales or []} - {""}
    out, listed = {}, []
    for key in keys:
        mine = {cid: b for cid, b in browsers.items() if b.get("product") == key}
        m, o = _funnel_counts(mine, tied, err)
        sold = None if sales is None else [f for f in sales if _main_product(f["order"]) == key]
        out[key] = {"meta": m, "other": o, "all": [None if a is None else a + b for a, b in zip(m, o)],
                    "listicle": _listicle_split(mine, sold, tied)}
        listed.append({"key": key, "label": key, "visitors": len(mine), "sales": len(sold or [])})
    listed.sort(key=lambda p: (-p["visitors"], -p["sales"], p["label"].lower()))
    listed = listed[:FUNNEL_PRODUCTS_MAX]
    return listed, {p["key"]: out[p["key"]] for p in listed}


def _listicle_split(browsers: dict[str, dict], sales: Optional[list[dict]], tied: dict[str, float]) -> dict:
    """Shoppers from Meta ads: straight to the product page vs through the
    listicle. Visitors are the funnel's Meta-ad browsers, by where their ad click
    landed; sales and revenue are the new sales credited to a Meta ad, by the
    stored record's lp and ids_stripped (came_through_listicle). The
    conversion rate is the share of those visitors that bought (`tied`, by
    checkout), so it never passes 100% when credited sales come from buyers
    the pixel never saw (older sales, a range before it started counting).
    The quiz's row counts every shopper who started in the quiz, the ones it
    sent through the listicle too: same ad click, so their buys are its sales
    and its conversion rate. A buy through the listicle is a listicle sale as
    well (its row keeps it), and the quiz row shows how many of its sales went
    that way as assists.
    Unknown (None) while Shopify can't be read."""
    rows = []
    for key, label in LISTICLE_ROWS:
        kind = _ROW_KIND[key]
        shoppers = [cid for cid, b in browsers.items() if b["meta"] and (
            b.get("landing", "") == kind or (key == "quiz" and b.get("quiz")))]
        visitors = len(shoppers)
        row = {"key": key, "label": label, "visitors": visitors, "sales": None, "revenue": None, "conversion": None,
               # The quiz's assists: listicle sales whose shopper started in the quiz.
               "assists": None}
        if sales is not None:
            mine = [f for f in sales if _meta_credited(f) and (landing_of(f["credit"]) == kind or (
                key == "quiz" and attribution.quiz_assist(f["credit"])))]
            bought = sum(1 for cid in shoppers if cid in tied)
            row.update(sales=len(mine), revenue=round(sum(f["revenue"] for f in mine), 2),
                       conversion=round(bought / visitors, 4) if visitors else None)
            if key == "quiz":
                helped = [f for f in sales if _meta_credited(f) and attribution.quiz_assist(f["credit"])]
                row.update(assists=len(helped), assist_revenue=round(sum(f["revenue"] for f in helped), 2))
        rows.append(row)
    return {"rows": rows,
            "note": "Shoppers from Meta ads by the page their ad click landed on, and the share of them who bought. "
                    "A quiz shopper who bought through the listicle counts in both rows."}


async def api_funnel(request: Request) -> dict:
    rng = _req_range(request)
    orders, err = await _orders_from(_listing_start())
    browsers = {cid: dict(b) for cid, b in _browser_steps(rng).items()}   # the cached ones stay as they are
    sales, tied, untied = None, {}, None
    if not err:
        sales = [f for f in _facts(orders, rng["start"], rng["end"]) if f["type"] == "new_sale"]
        tied, untied = _tie_sales(sales)
        # A buyer belongs to the product it bought.
        tokens = {tracking._s(f["order"].get("checkout_token"), 100): f for f in sales}
        for token, cid in db.client_ids_by_checkout([t for t in tokens if t]).items():
            if cid in browsers and _main_product(tokens[token]["order"]):
                browsers[cid]["product"] = _main_product(tokens[token]["order"])
        # A buyer went through every step, even when the pixel missed its visit
        # in the range: judged by its session, from the moment it bought.
        unseen = [cid for cid in tied if cid not in browsers]
        found = db.sessions_ad_data(unseen)
        end = min(rng["end"], time.time())
        for cid in unseen:
            s = found.get(cid) or {}
            browsers[cid] = {"step": 0, "meta": _from_ad(s, tied[cid], end), "landing": _landing(s),
                             "product": ""}
        for token, cid in db.client_ids_by_checkout([t for t in tokens if t]).items():
            if cid in unseen:
                browsers[cid]["product"] = _main_product(tokens[token]["order"])
    meta, other = _funnel_counts(browsers, tied, err)
    products, by_product = _by_product(browsers, sales, tied, err)
    first = db.first_storefront_event_at()
    days = config.ATTRIBUTION_WINDOW_DAYS
    return {
        "steps": FUNNEL_STEPS, "meta": meta, "other": other,
        "all": [None if m is None else m + o for m, o in zip(meta, other)],
        # The page's switch: which array is which, in the owner's words.
        "groups": [{"key": "meta", "label": attribution.META_CHANNEL, "tip": ""},
                   {"key": "other", "label": NOT_FROM_META, "tip": NOT_FROM_META_TIP.format(days=days)},
                   {"key": "all", "label": "All", "tip": ""}],
        # Sales the pixel never saw: no browser is known for their checkout.
        "untied_sales": untied,
        # The range starts before the pixel's oldest browser on record: fewer visitors than there were.
        "counting_since": (_time_local(dt.datetime.fromtimestamp(first, config.store_tz()))
                           if first and rng["start"] < first else ""),
        "listicle": _listicle_split(browsers, sales, tied),
        # The page's product switch: the same funnel and split for one product at a time.
        "products": products, "by_product": by_product,
        "range": _public_range(rng),
        "note": ("Each shopper's browser counts once, at the furthest step it reached in this range, so no step is "
                 "more than the one above it. A buyer counts at every step. Purchases are the browsers tied to a "
                 "new order by its checkout; MRR never counts. Meta ads means the browser came from a Meta ad in "
                 f"the {days} days before; Not from Meta is every other shopper."),
        "error": err,
    }


# --- the agent (agent.py imports this module, so it is imported here, when asked) ----

async def api_agent(request: Request) -> dict:
    import agent
    return await agent.api_agent(request)


async def api_agent_status(request: Request) -> dict:
    import agent
    return await agent.api_status(request)


# --- watchdog and actions ------------------------------------------------------------

async def api_watchdog(request: Request) -> dict:
    runs = db.watchdog_runs(time.time() - 86400)
    return {"runs": [{"at": r["run_at"], "status": r["status"], "checks": r["results"]}
                     for r in reversed(runs[-300:])]}


# --- the Backend tab ------------------------------------------------------------------

BACKEND_DAYS = {"7d": 7, "30d": 30, "90d": 90}


async def api_backend(request: Request) -> dict:
    """Deliveries, refunds and chargebacks for the orders of the last 7, 30 or 90 days."""
    days = BACKEND_DAYS.get(str(request.query_params.get("range") or ""), 30)
    return backend.overview(days)


async def api_database(request: Request) -> dict:
    """Who buys, from where, on what, and the quiz: the orders of the last 7, 30 or 90 days."""
    q = request.query_params
    days = BACKEND_DAYS.get(str(q.get("range") or ""), 30)
    return database.overview(days, country=q.get("country") or "", landing=q.get("landing") or "", kind=q.get("kind") or "")


async def api_database_export(request: Request) -> Response:
    """A CSV of the sales, the abandoned checkouts or the quiz takers (signed in only)."""
    if not _authed(request):
        return _json({"error": "unauthorized"}, 401)
    q = request.query_params
    days = BACKEND_DAYS.get(str(q.get("range") or ""), 30)
    what = str(q.get("what") or "sales")
    try:
        name, text = database.export_csv(what if what in ("sales", "abandoned", "quiz") else "sales", days)
    except Exception:
        log.exception("hub export failed")
        return _json({"error": "The export could not be made. The details are in the server log."})
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers={**NO_STORE, "Content-Disposition": f'attachment; filename="{name}"'})


async def api_backend_alerts(request: Request) -> dict:
    return backend.alerts()


async def api_backend_sync(request: Request) -> dict:
    """Read Shopify and 17TRACK now instead of at the next half hour."""
    if not backend._state["running"]:
        tracking.fire_and_forget(backend.sync())
    return {"started": True, **backend.status()}


async def api_watchdog_run(request: Request) -> dict:
    try:
        result = await watchdog.tick()
    except Exception as e:
        log.exception("hub: watchdog run failed")
        return {"status": "warn", "checks": [], "error": f"Couldn't run the checks ({type(e).__name__})."}
    return {"status": result["status"], "checks": result["checks"]}


def _resend_message(res: dict, kind: str) -> str:
    status = res.get("status")
    if res.get("error"):
        if not res.get("order_id"):
            return "That isn't a Shopify order the tracker can look up."
        return "Couldn't load this order from Shopify, so nothing was sent. Try again in a minute."
    if status == "refused":
        return BEFORE_GO_LIVE_REFUSAL
    if res.get("already_sent"):
        return "Already sent. Every pixel has this order, so nothing was sent again."
    if status == "sent":
        return "Sent to Meta. Meta ignores repeats of the same order, so nothing is counted twice."
    if status == "skipped":
        return f"Not sent: {SKIP_REASONS.get(kind, 'it is skipped on purpose')}."
    if status == "failed":
        return "Meta didn't accept it yet. The tracker keeps retrying on its own."
    return "Queued. It goes out within a minute."


async def api_resend(request: Request) -> dict:
    # Never an order placed before go-live: WeTracked sent it, and Meta can't dedupe the two.
    res = await tracking.resend_order(request.path_params["order_id"], allow_before_go_live=False)
    row = res.get("order") or {}
    message = _resend_message(res, row.get("kind") or "")
    if res.get("error"):                        # the raw text (a Shopify URL) is for the log, not the owner
        log.warning("hub: resend of order %s failed: %s", res.get("order_id") or "?", str(res["error"])[:300])
    # Status fields only: the stored order and the event payloads carry customer details.
    return {
        "ok": res.get("status") == "sent",
        "order_id": res.get("order_id"),
        "order_name": res.get("order_name"),
        "status": res.get("status"),
        "kind": row.get("kind"),
        "was_sent_before": bool(res.get("was_sent_before")),
        "before_go_live": bool(res.get("before_go_live")),
        "error": message if res.get("error") else (row.get("last_error") or ""),
        "message": message,
        "note": ("Meta ignores repeats of the same order, so it isn't counted twice."
                 if res.get("note") and res.get("status") != "refused" else ""),
        "events": [{"event_name": e.get("event_name"), "pixel_id": e.get("pixel_id"), "status": e.get("status"),
                    "error": e.get("error") or "", "at": e.get("created_at"), "fbtrace_id": e.get("fbtrace_id") or ""}
                   for e in res.get("events") or []],
    }


async def api_test_event(request: Request) -> dict:
    body = await _read_capped(request, MAX_FORM_BYTES)
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return {"ok": False, "error": "Enter the code from Events Manager > Test events and pick a pixel."}
    code = str(data.get("test_event_code") or "").strip()
    pixel_id = str(data.get("pixel_id") or "").strip() or None
    return await tracking.send_test_event(code, pixel_id)


# --- proposals: the watchdog suggests, the owner decides ------------------------------

PROPOSAL_DAYS = 7                               # decided proposals stay listed this long
APPROVE_LABELS = {"resend": "Send it", "renewal_tag": "Yes, count them as MRR", "stripped_ids": "Got it"}


def _proposal_out(p: dict) -> dict:
    """A proposal for the page: our own words and ids only."""
    info = p["kind"] == "stripped_ids"
    return {"id": p["id"], "kind": p["kind"], "title": p["title"], "detail": p["detail"], "status": p["status"],
            "created_at": p["created_at"], "decided_at": p["decided_at"], "result": p["result"] or "",
            "informational": info, "approve_label": APPROVE_LABELS.get(p["kind"], "Approve"),
            "can_dismiss": not info}


async def api_proposals(request: Request) -> dict:
    return {"proposals": [_proposal_out(p) for p in db.proposals(time.time() - PROPOSAL_DAYS * 86400)]}


def _proposal_id(request: Request) -> Optional[int]:
    try:
        return int(request.path_params["proposal_id"])
    except (KeyError, TypeError, ValueError):
        return None


async def run_proposal(p: dict) -> tuple[str, str]:
    """Do what an approved proposal says. Returns (done | failed, what happened)."""
    action = p["action"] if isinstance(p["action"], dict) else {}
    kind = action.get("type")
    if kind == "resend_order":
        # Only the pixels still missing it: one that has it never gets it again.
        res = await tracking.resend_order(str(action.get("order_id") or ""), allow_before_go_live=False,
                                          only_missing=True)
        if res.get("error"):
            log.warning("hub: proposal %s resend failed: %s", p["id"], str(res["error"])[:300])
        message = _resend_message(res, (res.get("order") or {}).get("kind") or "")
        return ("done" if res.get("status") == "sent" else "failed"), message
    if kind == "add_renewal_tag":
        tag = str(action.get("tag") or "").strip().lower()
        if not tag:
            return "failed", "This proposal names no tag."
        db.add_extra_renewal_tag(tag)
        return "done", (f"Orders tagged {tag} now count as MRR: they go to Meta as SubscriptionRenewal from now "
                        "on. Orders already sent stay as they were.")
    return "done", "Got it."


async def api_proposal_approve(request: Request) -> dict:
    pid = _proposal_id(request)
    p = db.get_proposal(pid) if pid is not None else None
    if p is None:
        return {"ok": False, "error": "That suggestion doesn't exist any more."}
    # pending -> approved first, so a double click can't run it twice.
    if not db.decide_proposal(pid, "approved"):
        return {"ok": False, "error": f"This was already {p['status']}.", "proposal": _proposal_out(p)}
    try:
        status, result = await run_proposal(p)
    except Exception as e:
        log.exception("hub: proposal %s failed", pid)
        status, result = "failed", f"It couldn't be done ({type(e).__name__}). The details are in the server log."
    db.decide_proposal(pid, status, result, only_from="approved")
    return {"ok": status == "done", "proposal": _proposal_out(db.get_proposal(pid))}


async def api_proposal_dismiss(request: Request) -> dict:
    pid = _proposal_id(request)
    p = db.get_proposal(pid) if pid is not None else None
    if p is None:
        return {"ok": False, "error": "That suggestion doesn't exist any more."}
    if not db.decide_proposal(pid, "dismissed", "Dismissed. Nothing was changed."):
        return {"ok": False, "error": f"This was already {p['status']}.", "proposal": _proposal_out(p)}
    return {"ok": True, "proposal": _proposal_out(db.get_proposal(pid))}


routes = [
    Route("/hub", page, methods=["GET"]),
    Route("/hub/", slash, methods=["GET"]),
    Route("/hub/login", login, methods=["GET", "POST"]),
    Route("/hub/logout", logout, methods=["GET"]),
    Route("/hub/api/pnl", _api(api_pnl), methods=["GET"]),
    Route("/hub/api/overview", _api(api_overview), methods=["GET"]),
    Route("/hub/api/orders", _api(api_orders), methods=["GET"]),
    Route("/hub/api/creatives", _api(api_creatives), methods=["GET"]),
    Route("/hub/api/assists", _api(api_assists), methods=["GET"]),
    Route("/hub/api/funnel", _api(api_funnel), methods=["GET"]),
    Route("/hub/api/watchdog", _api(api_watchdog), methods=["GET"]),
    Route("/hub/api/agent", _api(api_agent, post=True), methods=["POST"]),
    Route("/hub/api/agent/status", _api(api_agent_status), methods=["GET"]),
    Route("/hub/api/watchdog/run", _api(api_watchdog_run, post=True), methods=["POST"]),
    Route("/hub/api/backend", _api(api_backend), methods=["GET"]),
    Route("/hub/api/backend/alerts", _api(api_backend_alerts), methods=["GET"]),
    Route("/hub/api/backend/sync", _api(api_backend_sync, post=True), methods=["POST"]),
    Route("/hub/api/database", _api(api_database), methods=["GET"]),
    Route("/hub/api/database/export", api_database_export, methods=["GET"]),
    Route("/hub/api/resend/{order_id}", _api(api_resend, post=True), methods=["POST"]),
    Route("/hub/api/test-event", _api(api_test_event, post=True), methods=["POST"]),
    Route("/hub/api/proposals", _api(api_proposals), methods=["GET"]),
    Route("/hub/api/proposals/{proposal_id}/approve", _api(api_proposal_approve, post=True), methods=["POST"]),
    Route("/hub/api/proposals/{proposal_id}/dismiss", _api(api_proposal_dismiss, post=True), methods=["POST"]),
]
