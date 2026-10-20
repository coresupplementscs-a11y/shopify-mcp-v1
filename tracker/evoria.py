"""Evoria (evoria.online): SPERMFUEL+ sold through a second Shopify store, beside
Core's and never mixed into it.

The funnel: Meta ad > the Evoria listicle (8reaonse.netlify.app, lp
8-reasons-listicle-2) > evoria.online/products/spermfuel > cart drawer > Lasso's
checkout (checkout.evoria.online), paid through Whop. Lasso writes every paid
checkout back to Evoria's Shopify as an order (channel LP-FUNNEL) carrying the
buyer, the bundle lines, Lasso's own fbc / fbp / utm tags, and the cart's
attributes, where the cart drawer saves ad_id, adset_id, campaign_id, lp and
the browser's ids (Lasso drops those from its own link).

What the tracker does for Evoria:
- POST /evoria/collect: Evoria's Shopify custom pixel posts page views,
  product views and add to carts; each goes to Core Club through the
  Conversions API (event ids evoria_<event>_<id>). InitiateCheckout stays
  Lasso's own (its Meta integration sends it to Core Club).
- POST /evoria/webhooks: Evoria's orders/create and orders/updated webhooks.
  One Purchase per new order (event id evoria_order_<id>), one
  SubscriptionRenewal per rebill, to Core Club. Lasso's Purchase stays
  unticked, so nothing counts twice.
- Its own tables (evoria_sessions, evoria_orders, evoria_events): no query of
  Core's ever sees an Evoria row, so Core's numbers can't move.
- The hub's Evoria tab reads them (hub.api_evoria).

A browser that opened an Evoria page with ?core_test=CODE sends everything with
that Meta test_event_code: it shows in Events Manager's Test events only.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import base64
import json
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs

import attribution
import config
import database
import db
import meta_capi
import tracking

log = logging.getLogger("tracker.evoria")

STORE_HOST = "evoria.online"
# The listicles that send to Evoria (their own ListicleView names), and their hosts.
LISTICLE_NAMES = {"SpermFuel+ 8 Reasons Listicle 2"}
LANDING_HOSTS = {"8reaonse.netlify.app", STORE_HOST, "checkout.evoria.online"}
PIXEL_EVENTS = {"page_viewed": "PageView", "product_viewed": "ViewContent", "product_added_to_cart": "AddToCart"}
EVENT_PREFIX = "evoria_"
RENEWAL_TAGS = {"subscription renewal"}       # Kaching rebills on Evoria, written by Lasso (LP-FUNNEL)
TEST_CODE = re.compile(r"^[A-Za-z0-9_-]{4,40}$")
RETRY_DAYS = 3

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS evoria_sessions (
        client_id   TEXT PRIMARY KEY,
        first_seen  REAL NOT NULL,
        last_seen   REAL NOT NULL,
        ip          TEXT,
        user_agent  TEXT,
        fbp         TEXT,
        fbc         TEXT,
        landing_url TEXT,
        ad_params   TEXT,
        ad_seen_at  REAL,
        ad_history  TEXT,
        test_code   TEXT,
        views       INTEGER NOT NULL DEFAULT 0,
        carts       INTEGER NOT NULL DEFAULT 0)""",
    "CREATE INDEX IF NOT EXISTS idx_evoria_sessions_fbp ON evoria_sessions(fbp)",
    "CREATE INDEX IF NOT EXISTS idx_evoria_sessions_seen ON evoria_sessions(first_seen)",
    """CREATE TABLE IF NOT EXISTS evoria_orders (
        order_id    TEXT PRIMARY KEY,
        order_name  TEXT,
        created_at  REAL NOT NULL,
        received_at REAL NOT NULL,
        kind        TEXT,
        status      TEXT NOT NULL,
        order_json  TEXT NOT NULL,
        attribution TEXT,
        error       TEXT,
        sent_at     REAL)""",
    "CREATE INDEX IF NOT EXISTS idx_evoria_orders_created ON evoria_orders(created_at)",
    """CREATE TABLE IF NOT EXISTS evoria_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        event_name  TEXT NOT NULL,
        event_id    TEXT NOT NULL,
        source      TEXT NOT NULL,
        status      TEXT NOT NULL,
        order_id    TEXT,
        client_id   TEXT,
        test        INTEGER NOT NULL DEFAULT 0,
        error       TEXT,
        created_at  REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_evoria_events_at ON evoria_events(created_at)",
)
_ready = False


def init() -> None:
    global _ready
    if _ready:
        return
    for sql in SCHEMA:
        db.run(sql)
    _ready = True


def _s(v: Any, limit: int) -> str:
    return str(v if v is not None else "").strip()[:limit]


def _host(url: Any) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def _test_code(url: Any) -> str:
    try:
        code = (parse_qs(urlparse(str(url or "")).query).get("core_test") or [""])[0]
    except ValueError:
        return ""
    return code if TEST_CODE.match(code) else ""


# --- which ads are Evoria's -------------------------------------------------------------

def note_listicle(content_name: Any, url: Any) -> None:
    """A landing page event (landing.py): an Evoria listicle's host joins the
    hosts whose ads are Evoria's, so a new copy is picked up without a deploy."""
    if _s(content_name, 120) in LISTICLE_NAMES:
        host = _host(url)
        if host and host not in landing_hosts():
            known = sorted(landing_hosts() | {host})
            db.kv_set("evoria:landing_hosts", json.dumps(known))


def landing_hosts() -> set[str]:
    try:
        learned = set(json.loads(db.kv_get("evoria:landing_hosts") or "[]"))
    except ValueError:
        learned = set()
    return LANDING_HOSTS | {h for h in learned if isinstance(h, str)}


def _remembered(key: str) -> set[str]:
    try:
        got = json.loads(db.kv_get(key) or "[]")
    except ValueError:
        got = []
    return {str(x) for x in got if x} if isinstance(got, list) else set()


def _remember(key: str, ids: set[str]) -> set[str]:
    known = _remembered(key)
    if ids - known:
        known |= ids
        db.kv_set(key, json.dumps(sorted(known)))
    return known


def campaign_ids(links: Optional[list] = None) -> set[str]:
    """Evoria's campaigns (whop sperm): one where most ads with a link send clicks to
    an Evoria page. The whole campaign is Evoria's, its paused ads and an odd ad
    linking elsewhere too, and stays Evoria's once seen (an ad paused today drops
    out of the live links but still spent today)."""
    import meta_ads
    hosts = landing_hosts()
    rows = links if links is not None else meta_ads.cached_links()
    votes: dict[str, list[int]] = {}
    for r in rows or []:
        cid, link = str(r.get("campaign_id") or ""), r.get("link")
        if cid and link and not r.get("placeholder"):
            v = votes.setdefault(cid, [0, 0])
            v[0 if _host(link) in hosts else 1] += 1
    return _remember("evoria:campaigns", {c for c, (evo, other) in votes.items() if evo > other})


def ad_ids(links: Optional[list] = None) -> set[str]:
    """Evoria's ads: every ad in an Evoria campaign, and any ad elsewhere whose clicks
    land on an Evoria page. Their spend, and Meta's sales on them, are Evoria's,
    never Core's. Remembered, so an ad that stops serving keeps its spend here."""
    import meta_ads
    hosts = landing_hosts()
    rows = links if links is not None else meta_ads.cached_links()
    camps = campaign_ids(rows)
    seen = {str(r.get("ad_id")) for r in rows or [] if r.get("ad_id") and
            (_host(r.get("link")) in hosts or str(r.get("campaign_id") or "") in camps)}
    return _remember("evoria:ads", seen)


def owns(row: dict, ads: set[str], camps: set[str]) -> bool:
    """One Meta row (an ad's spend or purchases): Evoria's or Core's."""
    return str(row.get("ad_id") or "") in ads or str(row.get("campaign_id") or "") in camps


# --- the storefront pixel ---------------------------------------------------------------

def _session(client_id: str) -> dict:
    rows = db.query("SELECT * FROM evoria_sessions WHERE client_id=?", (client_id,))
    return dict(rows[0]) if rows else {}


def ingest_pixel(p: dict, ip: str, user_agent: str) -> Optional[dict]:
    """Record one Evoria pixel event; return the Meta event to send (None for
    one we keep without sending). Raises ValueError on payloads we refuse."""
    init()
    name = _s(p.get("name"), 64)
    url = _s(p.get("url"), 1000)
    if _host(url) != STORE_HOST:
        raise ValueError("not an Evoria page")
    if name not in PIXEL_EVENTS:
        return None
    client_id = _s(p.get("cid"), 100) or _s(p.get("fbp"), 100)
    event_id = _s(p.get("id"), 100)
    if not client_id or not event_id:
        raise ValueError("missing client or event id")
    now = time.time()
    cur = _session(client_id) or {"client_id": client_id, "first_seen": now, "views": 0, "carts": 0}
    fbp = _s(p.get("fbp"), 100) or cur.get("fbp") or ""
    fbclid = attribution.fbclid_of(url)
    fbc = attribution.real_fbc(_s(p.get("fbc"), 300)) or cur.get("fbc") or ""
    if fbclid and not attribution.stand_in(fbclid) and not attribution.link_tag(fbclid):
        fbc = attribution.make_fbc(fbclid, now)
    ad = attribution.ad_params_from_url(url)
    if ad and name == "page_viewed":
        ad = attribution.with_landing(ad)
        cur["ad_params"], cur["ad_seen_at"] = json.dumps(ad), now
        visit = attribution.ad_visit(ad, now, fbclid)
        if visit:
            cur["ad_history"] = json.dumps(attribution.add_ad_visit(cur.get("ad_history"), visit))
    if name == "page_viewed" and not cur.get("landing_url"):
        cur["landing_url"] = url
    code = _test_code(url) or cur.get("test_code") or ""
    cur.update(last_seen=now, ip=_s(ip, 64) or cur.get("ip"), user_agent=_s(user_agent, 400) or cur.get("user_agent"),
               fbp=fbp, fbc=fbc, test_code=code,
               views=int(cur.get("views") or 0) + (name == "product_viewed"),
               carts=int(cur.get("carts") or 0) + (name == "product_added_to_cart"))
    cols = ("client_id", "first_seen", "last_seen", "ip", "user_agent", "fbp", "fbc", "landing_url", "ad_params",
            "ad_seen_at", "ad_history", "test_code", "views", "carts")
    db.run(f"INSERT OR REPLACE INTO evoria_sessions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
           tuple(cur.get(c) for c in cols))
    if database.is_bot(user_agent, ip):
        return None                              # a crawler: kept off Meta
    ts = p.get("ts")
    at = float(ts) / 1000 if isinstance(ts, (int, float)) and ts > 0 else now
    at = now if at > now + 60 or at < now - 7 * 86400 else at
    event: dict[str, Any] = {
        "event_name": PIXEL_EVENTS[name],
        "event_time": int(at),
        "event_id": f"{EVENT_PREFIX}{name}_{event_id}",
        "action_source": "website",
        "event_source_url": url,
        "user_data": meta_capi.build_user_data(ip=_s(ip, 64), user_agent=_s(user_agent, 400), fbp=fbp, fbc=fbc,
                                               external_ids=[client_id]),
        "custom_data": {**_custom(name, p.get("custom")), "store": "evoria"},
    }
    return event


def _custom(name: str, c: Any) -> dict:
    """Value, currency and products only: nothing about the visitor."""
    c = c if isinstance(c, dict) else {}
    out: dict[str, Any] = {}
    try:
        if c.get("value") is not None:
            out["value"] = round(float(c["value"]), 2)
    except (TypeError, ValueError):
        pass
    if _s(c.get("currency"), 3):
        out["currency"] = _s(c["currency"], 3).upper()
    items = [i for i in c.get("items") or [] if isinstance(i, dict)]
    ids = [_s(i.get("product_id"), 40) for i in items if i.get("product_id")]
    if ids:
        out.update(content_type="product", content_ids=ids,
                   contents=[{"id": _s(i.get("product_id"), 40), "quantity": int(i.get("quantity") or 1)}
                             for i in items if i.get("product_id")])
    if name == "product_viewed" and _s(c.get("content_name"), 120):
        out["content_name"] = _s(c["content_name"], 120)
    return out


def _recorder(test: bool):
    def record(event: dict, source: str, status: str, order_id: str, pixel_id: str, client_id: str = "", **kw) -> None:
        try:
            db.run("INSERT INTO evoria_events (event_name, event_id, source, status, order_id, client_id, test, error, "
                   "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                   (event["event_name"], event["event_id"], source, status, order_id or None, client_id or None,
                    1 if test else 0, _s(kw.get("error"), 500) or None, time.time()))
        except Exception:
            log.exception("Evoria: could not record %s", event.get("event_id"))
    return record


async def send(event: dict, *, source: str, client_id: str = "", order_id: str = "", test_code: str = "") -> bool:
    """To Core Club only (where the Evoria ads and the listicle already are)."""
    pixel = meta_capi.primary_pixel()
    if test_code:
        pixel = {**pixel, "test_event_code": test_code}
    try:
        await meta_capi.send_event(event, source=source, pixel=pixel, client_id=client_id, order_id=order_id,
                                   test_event_code=test_code, recorder=_recorder(bool(test_code)), attempts=3)
        return True
    except meta_capi.MetaError:
        return False


async def send_pixel_event(event: dict, client_id: str) -> None:
    await send(event, source="evoria-pixel", client_id=client_id, test_code=_session(client_id).get("test_code") or "")


# --- orders (Evoria's webhooks) ---------------------------------------------------------

def verify_webhook(body: bytes, hmac_header: str) -> bool:
    secret = config.EVORIA_WEBHOOK_SECRET
    if not secret or not hmac_header:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), hmac_header.strip())


def note_webhook(topic: str, ok: bool, test: bool = False) -> None:
    """The last webhook Evoria's Shopify sent, and whether its signature held (the tab's footer)."""
    db.kv_set("evoria:last_webhook", json.dumps({"topic": _s(topic, 40), "ok": ok, "test": test, "at": time.time()}))


def last_webhook() -> dict:
    try:
        return json.loads(db.kv_get("evoria:last_webhook") or "{}")
    except ValueError:
        return {}


def classify(order: dict) -> str:
    """purchase | renewal | test | cancelled. Shopify's "Send test notification"
    sample order (example.com buyer) is a test: it never reaches Meta."""
    email = str(order.get("email") or (order.get("customer") or {}).get("email") or "").lower()
    if order.get("test") or order.get("_shopify_test") or email.endswith("@example.com"):
        return "test"
    if order.get("cancelled_at"):
        return "cancelled"
    tags = {t.strip().lower() for t in str(order.get("tags") or "").split(",") if t.strip()}
    if tags & RENEWAL_TAGS or tracking.is_renewal(order):
        return "renewal"
    return "purchase"


def store_order(order: dict, topic: str) -> bool:
    """Keep Evoria's order (an update replaces the JSON, never the decision).
    True when it is new and should be sent."""
    init()
    oid = _s(order.get("id"), 40)
    if not oid:
        raise ValueError("not an order")
    created = tracking._parse_time(order.get("created_at")) or time.time()
    known = db.query("SELECT status FROM evoria_orders WHERE order_id=?", (oid,))
    if known:
        db.run("UPDATE evoria_orders SET order_json=?, order_name=? WHERE order_id=?",
               (json.dumps(order), _s(order.get("name"), 40), oid))
        return False
    kind = classify(order)
    db.run("INSERT INTO evoria_orders (order_id, order_name, created_at, received_at, kind, status, order_json) "
           "VALUES (?,?,?,?,?,?,?)",
           (oid, _s(order.get("name"), 40), created, time.time(), kind,
            "pending" if kind in ("purchase", "renewal") else "skipped", json.dumps(order)))
    return kind in ("purchase", "renewal")


def match_session(order: dict) -> dict:
    """The browser behind an Evoria order: the client id the cart drawer saved on
    the cart, else the fbp Lasso or the drawer saved."""
    attrs = attribution.note_attributes(order)
    for cid in (attrs.get("core_cid"), attrs.get("_core_cid")):
        if cid:
            s = _session(_s(cid, 100))
            if s:
                return s
    for fbp in (attrs.get("core_fbp"), attrs.get("fbp")):
        if fbp:
            rows = db.query("SELECT * FROM evoria_sessions WHERE fbp=? ORDER BY last_seen DESC LIMIT 1", (_s(fbp, 100),))
            if rows:
                return dict(rows[0])
    return {}


async def process_order(order_id: str) -> str:
    """Decide the order's last click and send its one Purchase (or renewal)."""
    init()
    rows = db.query("SELECT * FROM evoria_orders WHERE order_id=?", (str(order_id),))
    if not rows:
        return "missing"
    row = dict(rows[0])
    if row["status"] in ("sent", "skipped"):
        return row["status"]
    order = json.loads(row["order_json"])
    kind = row["kind"]
    sess = match_session(order)
    import meta_ads
    catalog = meta_ads.cached_catalog() if config.META_AD_ACCOUNT_IDS else []
    decision = attribution.resolve(order, sess, journey=None, catalog=catalog) if kind == "purchase" else \
        {"attribution": {"meta": False, "channel": "MRR"}, "fbc": ""}
    browser = {**sess, "decided_fbc": decision["fbc"]} if kind == "purchase" else {}
    event = tracking.build_order_event(order, kind, browser)
    event["event_id"] = EVENT_PREFIX + event["event_id"]
    event.setdefault("custom_data", {})["store"] = "evoria"
    if event.get("action_source") == "website":
        event["event_source_url"] = sess.get("landing_url") or f"https://{STORE_HOST}/products/spermfuel"
    attrs = attribution.note_attributes(order)
    code = _s(sess.get("test_code"), 40) or (attrs.get("core_test") if TEST_CODE.match(attrs.get("core_test") or "") else "")
    db.run("UPDATE evoria_orders SET attribution=? WHERE order_id=?",
           (json.dumps({**decision["attribution"], "fbc": decision["fbc"], "test": bool(code)}), str(order_id)))
    if db.query("SELECT 1 FROM evoria_events WHERE event_id=? AND status='sent' LIMIT 1", (event["event_id"],)):
        db.run("UPDATE evoria_orders SET status='sent' WHERE order_id=?", (str(order_id),))
        return "sent"                            # never twice
    ok = await send(event, source="evoria-webhook", client_id=sess.get("client_id") or "", order_id=str(order_id),
                    test_code=code)
    db.run("UPDATE evoria_orders SET status=?, sent_at=?, error=? WHERE order_id=?",
           ("sent" if ok else "failed", time.time() if ok else None, None if ok else "Meta refused it", str(order_id)))
    log.info("Evoria order %s: %s %s", order.get("name") or order_id, event["event_name"], "sent" if ok else "failed")
    return "sent" if ok else "failed"


def match_keys(order_id: str) -> dict:
    """What the order's Purchase gave Meta to match the buyer (key names only, never
    values) and whether its ad click went with it: rebuilt the way it was sent."""
    rows = db.query("SELECT order_json, kind, attribution FROM evoria_orders WHERE order_id=?", (str(order_id),))
    if not rows or rows[0]["kind"] not in ("purchase", "renewal"):
        return {}
    order = json.loads(rows[0]["order_json"])
    sess = match_session(order)
    rec = json.loads(rows[0]["attribution"] or "{}")
    browser = {**sess, "decided_fbc": rec.get("fbc") or ""} if rows[0]["kind"] == "purchase" else {}
    try:
        ud = tracking.build_order_event(order, rows[0]["kind"], browser).get("user_data") or {}
    except Exception:
        return {}
    return {"keys": sorted(k for k, v in ud.items() if v), "click": bool(ud.get("fbc")),
            "browser": bool(sess), "order_notes": sorted(attribution.note_attributes(order)),
            "source": _s(order.get("source_name"), 40), "landing_site": bool(order.get("landing_site")),
            "click_to_order_min": round(((tracking._parse_time(order.get("created_at")) or time.time())
                                         - (attribution.click_time(ud["fbc"]) or time.time())) / 60) if ud.get("fbc") else None}


async def retry_failed() -> int:
    """Orders of the last RETRY_DAYS Meta didn't take yet: once more (the backend sync calls it)."""
    init()
    n = 0
    for r in db.query("SELECT order_id FROM evoria_orders WHERE status IN ('pending','failed') AND received_at>=?",
                      (time.time() - RETRY_DAYS * 86400,)):
        n += 1 if await process_order(str(r["order_id"])) == "sent" else 0
        await asyncio.sleep(0.2)
    return n


# --- the hub's Evoria tab ---------------------------------------------------------------

def facts(start: float, end: float) -> list[dict]:
    """Evoria's orders in [start, end), shaped like the hub's facts (hub.build_creatives
    takes them as they are): new_sale / rebill / skipped, revenue kept, the click."""
    init()
    import hub
    out = []
    for r in db.query("SELECT * FROM evoria_orders WHERE created_at>=? AND created_at<? ORDER BY created_at DESC",
                      (start, end)):
        order = json.loads(r["order_json"])
        credit = json.loads(r["attribution"]) if r["attribution"] else None
        kind = r["kind"]
        if credit and credit.get("test"):
            kind = "test"                         # a ?core_test= purchase: listed as Test, never a sale
        typ = "new_sale" if kind == "purchase" and not hub.refunded(order) else "rebill" if kind == "renewal" else "skipped"
        out.append({"order": order, "id": r["order_id"], "ts": r["created_at"], "type": typ,
                    "reason": "" if typ != "skipped" else ("Refunded" if kind == "purchase" else kind.title()),
                    "test_credit": credit if kind == "test" else None,
                    "revenue": hub.kept(order), "credit": credit if typ == "new_sale" else None,
                    "kind": kind, "decided": credit, "stored": {"status": r["status"]}})
    return out


def with_cancelled(facts_: list[dict]) -> list[dict]:
    """Like Core's Creatives (hub.with_cancelled): a sale cancelled after the buyer paid
    counts again, at the price paid, tagged cancelled. The ad did its part and Ads
    Manager keeps the purchase."""
    out = []
    for f in facts_:
        o = f["order"]
        if f["type"] == "skipped" and f.get("kind") == "purchase" and o.get("cancelled_at") and f.get("decided"):
            try:
                value = float(o.get("total_price") or 0)
            except (TypeError, ValueError):
                value = 0.0
            f = {**f, "type": "new_sale", "cancelled": True, "revenue": value, "reason": "", "credit": f["decided"]}
        out.append(f)
    return out


def funnel(start: float, end: float) -> dict:
    init()
    row = db.query("SELECT COUNT(*) AS visitors, SUM(views > 0) AS viewers, SUM(carts > 0) AS carters "
                   "FROM evoria_sessions WHERE first_seen>=? AND first_seen<? AND COALESCE(test_code, '')=''",
                   (start, end))[0]
    return {"visitors": int(row["visitors"] or 0), "product_views": int(row["viewers"] or 0),
            "add_to_carts": int(row["carters"] or 0)}


def health(since: float, test: bool = False) -> dict:
    """Sent and failed Evoria events since `since`, by name (the tab's footer);
    `test`: only the ones sent with a test_event_code (a ?core_test= run)."""
    init()
    out: dict[str, dict] = {}
    for r in db.query("SELECT event_name, status, COUNT(*) AS n FROM evoria_events WHERE created_at>=? AND test=? "
                      "GROUP BY event_name, status", (since, 1 if test else 0)):
        out.setdefault(r["event_name"], {})[r["status"]] = int(r["n"])
    return out
