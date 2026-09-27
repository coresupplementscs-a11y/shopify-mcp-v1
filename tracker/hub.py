"""
The hub: a private dashboard at /hub for the store owner. It shows that
tracking is healthy, what really sold (new sales apart from subscription
rebills), which Meta creative sold it, and true ROAS from Shopify revenue.

  GET  /hub                      the page (login form until signed in)
  POST /hub/login                ADMIN_TOKEN -> session cookie
  GET  /hub/logout
  GET  /hub/api/overview         status, cards, 7-day series, match quality
  GET  /hub/api/orders           orders in the range and how each was tracked
  GET  /hub/api/creatives        spend vs store-confirmed sales and assists per ad
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
                "manual": "it is a draft or POS order", "renewal": "rebills are switched off",
                "before_start": "it was placed before the tracker took over"}


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


def _week_start() -> float:
    return _midnight(_today() - dt.timedelta(days=6), config.store_tz())


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


def _cards(facts: list[dict], ads: dict, currency: str, shop_ok: bool = True) -> dict:
    new = [f for f in facts if f["type"] == "new_sale"]
    rebills = [f for f in facts if f["type"] == "rebill"]
    new_rev = round(sum(f["revenue"] for f in new), 2)
    rebill_rev = round(sum(f["revenue"] for f in rebills), 2)
    connected = bool(ads.get("connected"))
    # Half-read spend (one ad account failing) would overstate ROAS, so it's all or nothing.
    spend = round(sum(r["spend"] for r in ads.get("rows", [])), 2) if connected else None
    meta_value = round(sum(r["meta_value"] for r in ads.get("rows", [])), 2) if connected else None
    meta_purchases = _count(sum(r["meta_purchases"] for r in ads.get("rows", []))) if connected else None
    cards = {
        "currency": currency,
        "new_sales": {"count": len(new), "revenue": new_rev},
        "rebills": {"count": len(rebills), "revenue": rebill_rev},
        # Money that actually came in: new sales plus rebills, not skipped orders.
        "total_revenue": round(new_rev + rebill_rev, 2),
        "orders": len(new) + len(rebills),
        "aov": _ratio(new_rev, len(new)),
        "spend": spend,
        "true_roas": _ratio(new_rev, spend),
        "cost_per_sale": _ratio(spend, len(new)),
        "meta_roas": _ratio(meta_value, spend),
        "meta_purchases": meta_purchases,
        "ads_connected": connected,
        # Set up but unreadable just now is a Meta hiccup, not "connect ad spend".
        "ads_configured": bool(config.META_AD_ACCOUNT_IDS),
        "ads_error": ads.get("error") or "",
    }
    if not shop_ok:
        # Shopify couldn't be read: sales are unknown, not zero. A 0.00x True
        # ROAS would look exactly like a day of spend with no sales.
        cards.update(new_sales={"count": None, "revenue": None}, rebills={"count": None, "revenue": None},
                     total_revenue=None, orders=None, aov=None, true_roas=None, cost_per_sale=None)
    return cards


def _series(facts: list[dict], daily: Optional[dict], shop_ok: bool = True) -> dict:
    tz = config.store_tz()
    today = _today()
    days = [(today - dt.timedelta(days=6 - i)).isoformat() for i in range(7)]
    idx = {d: i for i, d in enumerate(days)}
    out = {"days": days, "new_revenue": [0.0] * 7, "rebill_revenue": [0.0] * 7,
           "new_sales": [0] * 7, "rebills": [0] * 7,
           "spend": [round(daily.get(d, 0.0), 2) for d in days] if daily is not None else [None] * 7}
    if not shop_ok:                             # unknown days, not days without sales
        out.update({k: [None] * 7 for k in ("new_revenue", "rebill_revenue", "new_sales", "rebills")})
        return out
    for f in facts:
        i = idx.get(dt.datetime.fromtimestamp(f["ts"], tz).date().isoformat())
        if i is None:
            continue
        if f["type"] == "new_sale":
            out["new_sales"][i] += 1
            out["new_revenue"][i] = round(out["new_revenue"][i] + f["revenue"], 2)
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
    # One listing covers both the range and the 7-day sparklines; every range ends tonight.
    start = min(rng["start"], _week_start())
    end = _midnight(today + dt.timedelta(days=1), tz)
    (orders, shop_err), ads, daily, status, name = await asyncio.gather(
        _orders_from(start),
        _ads(rng["since"], rng["until"]),
        _daily_spend((today - dt.timedelta(days=6)).isoformat(), today.isoformat()),
        _status(),
        _store_name(),
    )
    facts = _facts(orders, start, end)
    in_range = [f for f in facts if rng["start"] <= f["ts"] < rng["end"]]
    shop_ok = not shop_err
    return {
        "generated_at": dt.datetime.now(tz).isoformat(timespec="seconds"),
        "store": {"name": name, "domain": config.STORE_URL, "timezone": getattr(tz, "key", "UTC")},
        "range": _public_range(rng),
        "status": status,
        "cards": _cards(in_range, ads, _currency(orders), shop_ok),
        "series": _series(facts, daily if ads.get("connected") else None, shop_ok),
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


def _ad(credit: Optional[dict]) -> Optional[dict]:
    if not credit or not credit.get("meta"):
        return None
    ad = {"click": bool(credit.get("click")), "ad_name": credit.get("ad_name") or "",
          "adset_name": credit.get("adset_name") or "", "campaign_name": credit.get("campaign_name") or "",
          "ad_id": credit.get("ad_id") or "", "source": credit.get("source") or ""}
    helped = _assists(credit)[:attribution.ASSISTS_MAX]
    if helped:                                  # only sales that had help carry the key
        ad["assists"] = [{"ad_name": _assist_name(a), "adset_name": str(a.get("adset_name") or ""),
                          "campaign_name": str(a.get("campaign_name") or "")} for a in helped]
    return ad


def _time_local(when: dt.datetime) -> str:
    return f"{when:%b} {when.day}, {when.hour % 12 or 12}:{when:%M} {'AM' if when.hour < 12 else 'PM'}"


def _order_row(f: dict, sent: Optional[dict], pixels: list[dict], tz) -> dict:
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
        "type_label": {"new_sale": "New sale", "rebill": "Rebill"}.get(f["type"]) or f"Skipped: {f['reason']}",
        "tracker_status": stored.get("status") or "not_seen",
        "error": (stored.get("last_error") or "")[:300] or None,
        "pixels": [{**p, "sent": p["pixel_id"] in reached} for p in pixels],
        "ad": _ad(f["credit"]),
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
    orders, err = await _orders_from(min(rng["start"], _week_start()))
    facts = _facts(orders, rng["start"], rng["end"])
    shown = facts[:limit]
    sent = db.sent_order_events([f["id"] for f in shown])
    pixels, tz = _pixels(), config.store_tz()
    return {"orders": [_order_row(f, sent.get(f["id"]), pixels, tz) for f in shown],
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


def build_creatives(facts: list[dict], rows: list[dict], group: str) -> dict:
    """Meta's per-ad numbers side by side with the sales Shopify confirms for
    each ad, grouped campaign > ad set (or batch) > ad. A sale counts for the
    last ad its buyer clicked; earlier ads it names count as assists, which
    never add to sales or revenue."""
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
            e = add(_entry({**c, "ad_id": ad_id, "ad_name": name, "spend": 0.0}))
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
    return {
        "totals": {"spend": round(spend, 2), "meta_purchases": _count(sum(e["meta_purchases"] for e in entries)),
                   "meta_value": round(meta_value, 2),
                   **_split_out({k: _known_sum(entries, k) for k in SPLIT_OF}),
                   "store_sales": len(confirmed),
                   "store_revenue": round(sum(f["revenue"] for f in confirmed), 2),
                   "meta_roas": _ratio(meta_value, spend),
                   # True ROAS counts every new sale, not only the ones a link could tie to an ad.
                   "true_roas": _ratio(sum(f["revenue"] for f in new), spend)},
        "campaigns": out,
        "unlabelled": {**unlabelled, "store_revenue": round(unlabelled["store_revenue"], 2)},
        "url_tracking": {"tagged_orders": tagged, "meta_orders": len(confirmed)},
    }


async def api_creatives(request: Request) -> dict:
    rng = _range(request.query_params.get("range"))
    group = "batch" if request.query_params.get("group") == "batch" else "adset"
    (orders, shop_err), ads = await asyncio.gather(
        _orders_from(min(rng["start"], _week_start())), _ads(rng["since"], rng["until"]))
    facts = _facts(orders, rng["start"], rng["end"])
    built = build_creatives(facts, ads.get("rows", []), group)
    if not ads.get("connected"):
        # With one ad account failing the rows hold only part of the spend; a
        # total ROAS on it would be overstated (the overview hides it too).
        built["totals"].update(true_roas=None, meta_roas=None)
    if shop_err:                                # sales unknown: not a 0.00x ROAS
        built["totals"].update(true_roas=None)
    return {
        "connected": bool(ads.get("connected")),
        "configured": bool(config.META_AD_ACCOUNT_IDS),
        "error": " ".join(e for e in (ads.get("error"), shop_err) if e),
        "currency": _currency(orders),
        "range": _public_range(rng),
        "group": group,
        **built,
    }


# --- funnel -------------------------------------------------------------------------

FUNNEL_TTL = {"today": 60}                      # seconds; other ranges keep 5 minutes
_funnel_cache: dict[tuple, tuple[float, list[int], list[int]]] = {}


def _browser_steps(rng: dict) -> tuple[list[int], list[int]]:
    """Distinct browsers per storefront step (all but Purchases), from Meta ads
    and from everyone else. Cached a few minutes: the 30-day read is heavy and
    the page asks every minute."""
    key, now = (rng["key"], rng["start"]), time.time()
    for k in [k for k, v in _funnel_cache.items() if v[0] < now]:
        del _funnel_cache[k]
    hit = _funnel_cache.get(key)
    if hit:
        return list(hit[1]), list(hit[2])
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
        (meta if from_ad[r["client_id"]] else other)[FUNNEL_EVENTS.index(r["event_name"])] += 1
    _funnel_cache[key] = (now + FUNNEL_TTL.get(rng["key"], 300), meta, other)
    return list(meta), list(other)


async def api_funnel(request: Request) -> dict:
    rng = _range(request.query_params.get("range"))
    orders, err = await _orders_from(min(rng["start"], _week_start()))
    meta, other = _browser_steps(rng)
    if err:                                     # Shopify couldn't be read: purchases unknown, not zero
        meta.append(None)
        other.append(None)
    else:
        sales = [f for f in _facts(orders, rng["start"], rng["end"]) if f["type"] == "new_sale"]
        meta.append(sum(1 for f in sales if _meta_credited(f)))
        other.append(len(sales) - meta[-1])
    days = config.ATTRIBUTION_WINDOW_DAYS
    return {
        "steps": FUNNEL_STEPS, "meta": meta, "other": other, "range": _public_range(rng),
        "note": (f"Each step counts a shopper's browser once. Meta ads means the browser came from a Meta ad "
                 f"in the {days} days before. Purchases are new sales from Shopify. Counting started when the "
                 "hub was installed, so days before that show fewer visitors."),
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
    Route("/hub/api/funnel", _api(api_funnel), methods=["GET"]),
    Route("/hub/api/watchdog", _api(api_watchdog), methods=["GET"]),
    Route("/hub/api/watchdog/run", _api(api_watchdog_run, post=True), methods=["POST"]),
    Route("/hub/api/resend/{order_id}", _api(api_resend, post=True), methods=["POST"]),
    Route("/hub/api/test-event", _api(api_test_event, post=True), methods=["POST"]),
]
