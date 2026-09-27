"""
The hub: a private dashboard at /hub for the store owner. It shows that
tracking is healthy, what the ads really sold, which Meta creative sold it
(and which ones helped), and Product ROAS: Shopify sales of the products the
running campaigns sell, over ad spend. Subscription rebills (the owner calls
them MRR) never count as ad sales. Profit, and products no campaign is
advertising, belong to the owner's P&L app, so the dashboard doesn't show them.

  GET  /hub                      the page (login form until signed in)
  POST /hub/login                ADMIN_TOKEN -> session cookie
  GET  /hub/logout
  GET  /hub/api/overview         status, cards, 7-day series, match quality
  GET  /hub/api/orders           orders in the range and how each was tracked
  GET  /hub/api/creatives        spend vs store-confirmed sales and assists per ad
  GET  /hub/api/assists          per ad that got sales: the ads clicked before it
  GET  /hub/api/funnel           Meta ad browsers vs everyone else
  GET  /hub/api/watchdog         the last 24 h of health checks
  POST /hub/api/watchdog/run     run the checks now
  POST /hub/api/resend/{id}      re-send one order to Meta
  POST /hub/api/test-event       send a Test Events PageView

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
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qs

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

import attribution
import config
import db
import meta_ads
import meta_capi
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
SKIP_REASONS = {"test": "it is a test order", "cancelled": "it was cancelled",
                "too_old": "it is older than the 7 days Meta accepts",
                "manual": "it is a draft or POS order", "renewal": "sending MRR to Meta is switched off",
                "before_start": "it was placed before the tracker took over"}
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
        main = pid == config.META_PIXEL_ID
        out.append({"pixel_id": pid, "role": "main" if main else "backup",
                    "name": db.kv_get(f"pixel_name:{pid}") or ("Main pixel" if main else "Backup pixel")})
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


def order_type(order: dict) -> tuple[str, str]:
    """new_sale | rebill | skipped, and why it was skipped. Unlike
    tracking.classify_order this ignores age: a 10-day-old sale is still a sale.
    Rebills are decided by tracking.is_renewal, the same test the tracker
    sends by, so the hub never counts a sale Meta was told was a rebill."""
    if order.get("test"):
        return "skipped", "Test order"
    if order.get("cancelled_at") or (order.get("financial_status") or "") == "voided":
        return "skipped", "Cancelled"
    if tracking.is_renewal(order):
        return "rebill", ""
    if (order.get("source_name") or "") in config.SKIP_SOURCE_NAMES:
        return "skipped", "Draft or POS order"
    return "new_sale", ""


def ad_credit(order: dict, stored: Optional[dict]) -> dict:
    """The Meta ad credited with a sale: what the tracker stored when it sent
    the Purchase, else what Shopify's landing page says."""
    if stored and stored.get("attribution"):
        return stored["attribution"]
    return attribution.order_attribution(order, {}, click=tracking.fbc_from_landing_site(order))


def _facts(orders: list[dict], start: float, end: float) -> list[dict]:
    """The orders created in [start, end), newest first, classified."""
    picked = []
    for o in orders:
        ts = tracking._parse_time(o.get("created_at"))
        if ts is not None and start <= ts < end and o.get("id") is not None:
            picked.append((ts, o))
    picked.sort(key=lambda p: p[0], reverse=True)
    stored = db.orders_by_id([str(o["id"]) for _, o in picked])
    out = []
    for ts, o in picked:
        oid = str(o["id"])
        kind, reason = order_type(o)
        # Only new sales are credited to ads: a rebill is billed by the
        # subscription app, not by anyone clicking.
        credit = ad_credit(o, stored.get(oid)) if kind == "new_sale" else None
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
    link carried (only a fallback: older ads had utm_content and utm_term swapped)."""
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
    rng = _range(request.query_params.get("range"))
    now = time.time()
    tz = config.store_tz()
    today = _today()
    week = ((today - dt.timedelta(days=6)).isoformat(), today.isoformat())
    # One listing covers the range, the 7-day sparklines and what each campaign
    # sold in the last 30 days; every range ends tonight.
    start, end = _listing_start(), _tonight()
    (orders, shop_err), ads, daily, days, status, name = await asyncio.gather(
        _orders_from(start),
        _ads(rng["since"], rng["until"]),
        _daily_spend(*week),
        _campaign_days(*week),
        _status(),
        _store_name(),
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
    """The earlier ads stored with a sale (attribution.order_attribution)."""
    helped = (credit or {}).get("assists")
    return [a for a in helped if isinstance(a, dict)] if isinstance(helped, list) else []


def _assist_name(a: dict) -> str:
    name = str(a.get("ad_name") or "").strip()
    return name or (f"Ad {a['ad_id']}" if a.get("ad_id") else "Unnamed ad")


def _ad(credit: Optional[dict], names: Optional[dict] = None) -> Optional[dict]:
    """The ad that got a sale and the ads that assisted it, named by Meta
    when their ad id is known to it (`names`), else by their link."""
    if not credit or not credit.get("meta"):
        return None
    names = names or {}
    seller = _ad_label(credit, names)
    ad = {"click": bool(credit.get("click")), "ad_name": seller["ad_name"], "adset_name": seller["adset_name"],
          "campaign_name": seller["campaign_name"], "ad_id": seller["ad_id"], "source": credit.get("source") or ""}
    helped = [_ad_label(a, names) for a in _assists(credit)[:attribution.ASSISTS_MAX]]
    if helped:                                  # only sales that had help carry the key
        ad["assists"] = [{"ad_name": _assist_name(a), "adset_name": a["adset_name"],
                          "campaign_name": a["campaign_name"]} for a in helped]
    return ad


def _time_local(when: dt.datetime) -> str:
    return f"{when:%b} {when.day}, {when.hour % 12 or 12}:{when:%M} {'AM' if when.hour < 12 else 'PM'}"


def _order_row(f: dict, sent: Optional[dict], pixels: list[dict], tz, names: Optional[dict] = None) -> dict:
    """One order for the table. Built field by field so the customer's contact
    details in the Shopify order can never slip through."""
    o, stored = f["order"], f["stored"] or {}
    when = dt.datetime.fromtimestamp(f["ts"], tz)
    reached = (sent or {}).get("pixels", {})
    keys = set(((sent or {}).get("match_keys") or "").split(","))
    return {
        "id": f["id"],
        "name": o.get("name") or f["id"],
        "created_at": when.isoformat(timespec="seconds"),
        "time_local": _time_local(when),
        "total": f["revenue"],
        "currency": o.get("currency") or "USD",
        "items": _items(o),
        "type": f["type"],
        # The owner calls subscription rebills MRR; the type value stays "rebill".
        "type_label": {"new_sale": "New sale", "rebill": "MRR"}.get(f["type"]) or f"Skipped: {f['reason']}",
        "tracker_status": stored.get("status") or "not_seen",
        "error": (stored.get("last_error") or "")[:300] or None,
        "pixels": [{**p, "sent": p["pixel_id"] in reached} for p in pixels],
        "ad": _ad(f["credit"], names),
        "details": {name: key in keys for name, key in DETAIL_KEYS.items()},
        "can_resend": f["type"] in ("new_sale", "rebill"),
    }


async def api_orders(request: Request) -> dict:
    rng = _range(request.query_params.get("range"))
    try:
        limit = int(request.query_params.get("limit") or 100)
    except ValueError:
        limit = 100
    limit = max(1, min(limit, 200))
    orders, err = await _orders_from(_listing_start())
    facts = _facts(orders, rng["start"], rng["end"])
    shown = facts[:limit]
    sent = db.sent_order_events([f["id"] for f in shown])
    pixels, tz = _pixels(), config.store_tz()
    names = await _names(_credited_ad_ids(shown))
    return {"orders": [_order_row(f, sent.get(f["id"]), pixels, tz, names) for f in shown],
            "count": len(facts), "error": err}


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
            "store_sales": 0, "store_revenue": 0.0, "orders": [], "assists": 0, "assist_orders": [],
            "assist_ids": []}                   # order ids beside assist_orders, for rollups only


def _assisted(items: list[dict]) -> list[str]:
    """The sales any of these ads assisted, each once, however many of them helped it."""
    labels: dict[str, str] = {}
    for i in items:
        for oid, label in zip(i["assist_ids"], i["assist_orders"]):
            labels.setdefault(oid, label)
    return list(labels.values())


def _totals(items: list[dict]) -> dict:
    t = {k: sum(i[k] for i in items) for k in SUM_FIELDS}
    helped = _assisted(items)
    return {"spend": round(t["spend"], 2), "meta_purchases": _count(t["meta_purchases"]),
            "meta_value": round(t["meta_value"], 2), "store_sales": t["store_sales"],
            "store_revenue": round(t["store_revenue"], 2), "assists": len(helped), "assist_orders": helped,
            **_split_out({k: _known_sum(items, k) for k in SPLIT_OF}),
            "roas_meta": _ratio(t["meta_value"], t["spend"]), "roas_store": _ratio(t["store_revenue"], t["spend"])}


def _low(s: Any) -> str:
    return str(s or "").strip().lower()


def _ad_out(a: dict) -> dict:
    return {"ad_id": a["ad_id"], "ad_name": a["ad_name"] or (f"Ad {a['ad_id']}" if a["ad_id"] else "Unnamed ad"),
            "adset_name": a["adset_name"], "spend": round(a["spend"], 2), "impressions": a["impressions"],
            "clicks": a["clicks"], "meta_purchases": _count(a["meta_purchases"]),
            "meta_value": round(a["meta_value"], 2), **_split_out(a), "store_sales": a["store_sales"],
            "store_revenue": round(a["store_revenue"], 2), "roas_meta": _ratio(a["meta_value"], a["spend"]),
            "roas_store": _ratio(a["store_revenue"], a["spend"]), "orders": a["orders"],
            "assists": a["assists"], "assist_orders": a["assist_orders"]}


def _named(c: dict) -> tuple[str, str]:
    return str(c.get("ad_id") or "").strip(), str(c.get("ad_name") or "").strip()


def build_creatives(facts: list[dict], rows: list[dict], group: str, names: Optional[dict] = None) -> dict:
    """Meta's per-ad numbers side by side with the sales Shopify confirms for
    each ad, grouped campaign > ad set (or batch) > ad. A sale counts for the
    last ad its buyer clicked; earlier ads it names count as assists, which
    never add to sales or revenue. An ad with sales but no delivery in the
    range gets its row from the sale, named by Meta by its id (`names`) when
    Meta knows it, else by its link."""
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

    out = []
    for camp in campaigns.values():
        groups, all_ads = [], []
        for g in camp["groups"].values():
            ads = sorted(g["ads"], key=lambda a: (-a["store_sales"], -a["meta_purchases"], -a["spend"],
                                                  -a["assists"]))
            all_ads.extend(ads)
            groups.append({"key": g["key"], "name": g["name"], **_totals(ads), "ads": [_ad_out(a) for a in ads]})
        groups.sort(key=lambda g: (-g["spend"], -g["store_sales"]))
        out.append({"campaign_id": camp["campaign_id"], "campaign_name": camp["campaign_name"],
                    **_totals(all_ads), "groups": groups})
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
    rng = _range(request.query_params.get("range"))
    group = "batch" if request.query_params.get("group") == "batch" else "adset"
    start = _listing_start()
    (orders, shop_err), ads = await asyncio.gather(_orders_from(start), _ads(rng["since"], rng["until"]))
    learned = _facts(orders, start, _tonight())
    facts = [f for f in learned if rng["start"] <= f["ts"] < rng["end"]]
    rows = ads.get("rows", [])
    # Ads Meta reported delivery for already carry Meta's names; the rest are named by id.
    reported = {str(r.get("ad_id") or "") for r in rows}
    names = await _names(_credited_ad_ids(facts) - reported)
    built = build_creatives(facts, rows, group, names)
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
        **built,
    }


# --- assists ------------------------------------------------------------------------

def _history_known(f: dict, since: float) -> bool:
    """Whether a sale's click history is on record: it was placed after click
    history started (`since`) and the tracker credited it itself. A sale the
    tracker hasn't handled (yet) is credited from its landing page alone."""
    return f["ts"] >= since and bool((f["stored"] or {}).get("attribution"))


def build_assists(facts: list[dict], names: Optional[dict] = None, since: float = 0.0) -> dict:
    """For each ad that got at least one new sale with help (the last Meta ad
    the buyer clicked), the other Meta ads that buyer clicked earlier, with
    how many of its sales each one helped. A sale counts once per assisting
    ad however its links spelled that ad, and the ad that got a sale is never
    its own assist. Ads are named by Meta by their id (`names`), else by their
    link. Sales an ad got with no earlier ad click are only counted, and only
    when their click history is on record (see _history_known): for the rest
    it is unknown, not "no earlier click"."""
    names = names or {}
    confirmed = [f for f in facts if _meta_credited(f)]
    # A link without an ad id names the ad only: tie that name to the one ad id
    # that goes by it, so both spellings of an ad count as one ad.
    ids_by_name: dict[str, set] = {}
    for f in confirmed:
        for a in [f["credit"], *_assists(f["credit"])]:
            ad_id, name = _named(a)
            if not ad_id:
                continue
            for n in (name, (names.get(ad_id) or {}).get("ad_name")):
                if _low(n):
                    ids_by_name.setdefault(_low(n), set()).add(ad_id)

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
        if not out["ad_name"] and out["ad_id"]:
            out["ad_name"] = f"Ad {out['ad_id']}"
        return out

    rows: dict[str, dict] = {}
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
        row = rows.get(ck)
        if row is None:
            row = rows[ck] = {**label(closer, ck), "sales": 0, "revenue": 0.0, "by": {}}
        row["sales"] += 1
        row["revenue"] += f["revenue"]
        for k, a in helpers.items():
            helper = row["by"].get(k)
            if helper is None:
                helper = row["by"][k] = {**label(a, k), "sales": 0}
            helper["sales"] += 1
    out = []
    for row in rows.values():
        by = sorted(row.pop("by").values(), key=lambda e: (-e["sales"], _low(e["ad_name"]), e["ad_id"]))
        out.append({**row, "revenue": round(row["revenue"], 2), "assisted_by": by})
    out.sort(key=lambda r: (-r["sales"], -r["revenue"], _low(r["ad_name"]), r["ad_id"]))
    return {"rows": out, "sales_without_assists": without}


async def api_assists(request: Request) -> dict:
    rng = _range(request.query_params.get("range"))
    orders, err = await _orders_from(_listing_start())
    facts = _facts(orders, rng["start"], rng["end"])
    since = ASSISTS_FROM
    built = build_assists(facts, await _names(_credited_ad_ids(facts)), since)
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
        "note": (f"Each row is the ad that got the sale: the last Meta ad the buyer clicked. Assisted by lists the "
                 f"other Meta ads the same buyer clicked in the {days} days before, and in how many of those sales. "
                 f"A sale counts once for each ad that helped it. Assists count from {ASSISTS_SINCE}, when click "
                 "history started."),
        "error": err,
    }


# --- funnel -------------------------------------------------------------------------

FUNNEL_TTL = {"today": 60}                      # seconds; other ranges keep 5 minutes
# range -> (expires, Meta counts, everyone else's counts, {browser: came from a Meta ad})
_funnel_cache: dict[tuple, tuple[float, list[int], list[int], dict[str, bool]]] = {}


def _browser_steps(rng: dict) -> tuple[list[int], list[int], dict[str, bool]]:
    """Distinct browsers per storefront step (all but Purchases), from Meta ads
    and from everyone else, and each browser's group. Visitors counts every
    browser seen at any step in the range (one whose page view fell just
    before the range still visited), so no later step can outnumber it.
    Cached a few minutes: the 30-day read is heavy and the page asks every minute."""
    key, now = (rng["key"], rng["start"]), time.time()
    for k in [k for k, v in _funnel_cache.items() if v[0] < now]:
        del _funnel_cache[k]
    hit = _funnel_cache.get(key)
    if hit:
        return list(hit[1]), list(hit[2]), hit[3]
    end = min(rng["end"], now)
    window = config.ATTRIBUTION_WINDOW_DAYS * 86400
    rows = db.storefront_funnel(rng["start"], rng["end"], FUNNEL_EVENTS)
    # Each browser is judged once, from its first step in the range, so one
    # shopper can't be "Meta ads" on one step and "everyone else" on the next.
    # The window runs back from that step, not from the end of the range, so a
    # 30-day view still credits its first weeks' ad clicks.
    first: dict[str, dict] = {}
    for r in rows:
        f = first.get(r["client_id"])
        if f is None or float(r["first_at"]) < float(f["first_at"]):
            first[r["client_id"]] = r
    from_ad = {}
    for cid, r in first.items():
        lo = float(r["first_at"]) - window
        seen = (r.get("ad_seen_at"), attribution.click_time(r.get("fbc")))
        from_ad[cid] = any(t is not None and lo <= float(t) <= end for t in seen)
    meta, other = [0] * len(FUNNEL_EVENTS), [0] * len(FUNNEL_EVENTS)
    for r in rows:
        if r["event_name"] != FUNNEL_EVENTS[0]:
            (meta if from_ad[r["client_id"]] else other)[FUNNEL_EVENTS.index(r["event_name"])] += 1
    meta[0] = sum(1 for v in from_ad.values() if v)
    other[0] = len(from_ad) - meta[0]
    _funnel_cache[key] = (now + FUNNEL_TTL.get(rng["key"], 300), meta, other, from_ad)
    return list(meta), list(other), from_ad


def _buyers(sales: list[dict], groups: dict[str, bool]) -> tuple[int, int]:
    """Browsers counted in the funnel that bought: (from Meta ads, everyone
    else). A sale is tied to its browser by the order's checkout token; a
    sale whose browser isn't in the funnel isn't counted, and a browser that
    bought twice counts once, like on every other step."""
    tokens = [t for t in (tracking._s(f["order"].get("checkout_token"), 100) for f in sales) if t]
    browser = db.client_ids_by_checkout(tokens)
    bought = {browser.get(t) for t in tokens} & set(groups)
    meta = sum(1 for cid in bought if groups[cid])
    return meta, len(bought) - meta


async def api_funnel(request: Request) -> dict:
    rng = _range(request.query_params.get("range"))
    orders, err = await _orders_from(_listing_start())
    meta, other, groups = _browser_steps(rng)
    if err:                                     # Shopify couldn't be read: purchases unknown, not zero
        meta.append(None)
        other.append(None)
    else:
        sales = [f for f in _facts(orders, rng["start"], rng["end"]) if f["type"] == "new_sale"]
        bought = _buyers(sales, groups)
        meta.append(bought[0])
        other.append(bought[1])
    days = config.ATTRIBUTION_WINDOW_DAYS
    return {
        "steps": FUNNEL_STEPS, "meta": meta, "other": other, "range": _public_range(rng),
        "note": (f"Each step counts a shopper's browser once. Visitors are all the browsers seen in the store in "
                 f"this range. Meta ads means the browser came from a Meta ad in the {days} days before. Purchases "
                 "are the browsers above that placed a new order in Shopify, so they can never be more than the "
                 "visitors. A sale whose shopper the storefront pixel didn't see isn't counted here. Counting "
                 "started when the hub was installed, so days before that show fewer visitors."),
        "error": err,
    }


# --- watchdog and actions ------------------------------------------------------------

async def api_watchdog(request: Request) -> dict:
    runs = db.watchdog_runs(time.time() - 86400)
    return {"runs": [{"at": r["run_at"], "status": r["status"], "checks": r["results"]}
                     for r in reversed(runs[-300:])]}


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
    if status == "sent":
        return "Sent to Meta. Meta ignores repeats of the same order, so nothing is counted twice."
    if status == "skipped":
        return f"Not sent: {SKIP_REASONS.get(kind, 'it is skipped on purpose')}."
    if status == "failed":
        return "Meta didn't accept it yet. The tracker keeps retrying on its own."
    return "Queued. It goes out within a minute."


async def api_resend(request: Request) -> dict:
    res = await tracking.resend_order(request.path_params["order_id"])
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
        "error": message if res.get("error") else (row.get("last_error") or ""),
        "message": message,
        "note": "Meta ignores repeats of the same order, so it isn't counted twice." if res.get("note") else "",
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


routes = [
    Route("/hub", page, methods=["GET"]),
    Route("/hub/", slash, methods=["GET"]),
    Route("/hub/login", login, methods=["GET", "POST"]),
    Route("/hub/logout", logout, methods=["GET"]),
    Route("/hub/api/overview", _api(api_overview), methods=["GET"]),
    Route("/hub/api/orders", _api(api_orders), methods=["GET"]),
    Route("/hub/api/creatives", _api(api_creatives), methods=["GET"]),
    Route("/hub/api/assists", _api(api_assists), methods=["GET"]),
    Route("/hub/api/funnel", _api(api_funnel), methods=["GET"]),
    Route("/hub/api/watchdog", _api(api_watchdog), methods=["GET"]),
    Route("/hub/api/watchdog/run", _api(api_watchdog_run, post=True), methods=["POST"]),
    Route("/hub/api/resend/{order_id}", _api(api_resend, post=True), methods=["POST"]),
    Route("/hub/api/test-event", _api(api_test_event, post=True), methods=["POST"]),
]
