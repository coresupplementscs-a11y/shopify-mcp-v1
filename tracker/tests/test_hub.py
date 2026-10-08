"""Tests for the hub: the attribution, Meta reads, watchdog and database
groundwork under it, and the /hub pages and /hub/api/* endpoints on top.
Shopify, the Conversions API and the Marketing API are all mocked."""
import asyncio
import datetime as dt
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
import time
import types

import httpx
import pytest
from starlette.testclient import TestClient

import app as app_module
import attribution
import config
import db
import hub
import meta_ads
import meta_capi
import pnl
import shopify
import tracking
import watchdog
import worker

ADMIN = "admin-test"
API = {"Authorization": f"Bearer {ADMIN}"}
POST = {**API, "X-Hub-Request": "1"}
MAIN = "1298114545063437"
PII = ("jane.doe@example.com", "555-0199", "6475550199", "203.0.113.9", "Oak Ville", "Jané", "L6M 5P6")
# Every credential the service holds; none may ever appear in a hub response.
SECRETS = ("test-token", "backup-token", "ads-secret", ADMIN, "shpat_test", "whsec_test")
BACKUP_ID = "1717074239276698"
BACKUP = {"pixel_id": BACKUP_ID, "token": "backup-token", "test_event_code": ""}


# Event ids unique even when two land in one tick of Windows' clock.
_EVT_IDS = __import__('itertools').count()

def at(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


def today_ts(seconds_ago=120):
    """A moment today in store time, even just after local midnight."""
    return max(hub._range("today")["start"] + 5, time.time() - seconds_ago)


def make_order(oid, ts=None, **over):
    o = {
        "id": oid, "name": f"#c{oid}", "email": "jane.doe@example.com", "phone": "(647) 555-0199",
        "created_at": at(ts or today_ts()), "test": False, "source_name": "web",
        "financial_status": "paid", "total_price": "59.95", "currency": "USD",
        "checkout_token": f"chk{oid}", "browser_ip": "203.0.113.9",
        "client_details": {"user_agent": "Mozilla/5.0 iPhone"}, "landing_site": "/",
        "note_attributes": [], "customer": {"id": 777, "email": "jane.doe@example.com", "first_name": "Jané"},
        "billing_address": {"first_name": "Jané", "last_name": "Doe", "city": "Oak Ville", "zip": "L6M 5P6",
                            "country_code": "CA", "phone": "(647) 555-0199"},
        "shipping_address": {}, "line_items": [{"title": "SpermFuel+", "quantity": 1, "product_id": 111}],
    }
    o.update(over)
    return o


class FakeShopify:
    def __init__(self):
        self.orders, self.fail, self.listings = [], None, 0
        self.count, self.counts = None, 0     # Shopify's all-time order count (None: as many as `orders`)
        self.since = []                      # the since_id of every orders.json read that paged by id

    def handler(self, request: httpx.Request):
        if self.fail:
            return httpx.Response(self.fail, json={"errors": "nope"})
        path = request.url.path
        if path.endswith("/orders.json"):
            self.listings += 1
            if request.url.params.get("since_id") is not None:      # paging by id, like Shopify: oldest first
                since = int(request.url.params["since_id"])
                self.since.append(since)
                after = sorted((o for o in self.orders if int(o["id"]) > since), key=lambda o: int(o["id"]))
                return httpx.Response(200, json={"orders": after[:int(request.url.params.get("limit", 50))]})
            return httpx.Response(200, json={"orders": self.orders})
        if path.endswith("/shop.json"):
            return httpx.Response(200, json={"shop": {"name": "Core Supplements"}})
        if path.endswith("/orders/count.json"):
            self.counts += 1
            assert request.url.params.get("status") == "any"
            return httpx.Response(200, json={"count": len(self.orders) if self.count is None else self.count})
        if path.endswith("/webhooks.json"):
            return httpx.Response(200, json={"webhooks": []})
        m = re.search(r"/orders/(\d+)\.json$", path)
        if m:
            return httpx.Response(200, json={"order": next(o for o in self.orders if str(o["id"]) == m.group(1))})
        return httpx.Response(404, json={})


class FakeMeta:
    """Conversions API (sends) and Marketing API (reads) in one."""

    def __init__(self):
        self.sent, self.ad_rows, self.daily, self.denied = [], [], [], set()
        self.campaign_daily = []            # level=campaign rows, one per campaign per day
        self.names = {}                     # ad id -> what Graph answers for ?ids=...&fields=name,adset{name},...
        self.requests = []

    def capi(self, request: httpx.Request):
        body = json.loads(request.content)
        self.sent.append((request.url.path.split("/")[-2], [e["event_name"] for e in body["data"]]))
        return httpx.Response(200, json={"events_received": len(body["data"]), "fbtrace_id": "trace9"})

    def graph(self, request: httpx.Request):
        self.requests.append(request)
        path = request.url.path
        m = re.search(r"/act_(\d+)(/insights)?$", path)
        if m and m.group(1) in self.denied:
            return httpx.Response(403, json={"error": {"message": "(#200) Missing ads_read permission",
                                                       "type": "OAuthException", "code": 200}})
        if path.endswith("/act_123"):
            return httpx.Response(200, json={"name": "Leggings", "currency": "USD",
                                             "timezone_name": "America/New_York"})
        if path.endswith("/act_123/insights"):
            level = request.url.params.get("level")
            rows = {"account": self.daily, "campaign": self.campaign_daily}.get(level, self.ad_rows)
            return httpx.Response(200, json={"data": rows})
        if request.url.params.get("ids"):
            ids = request.url.params["ids"].split(",")
            unknown = [i for i in ids if i not in self.names]
            if unknown:                     # like Graph: one unknown id fails the whole batch
                return httpx.Response(400, json={"error": {
                    "message": f"(#803) Some of the aliases you requested do not exist: {','.join(unknown)}",
                    "type": "OAuthException", "code": 803}})
            return httpx.Response(200, json={i: self.names[i] for i in ids})
        return httpx.Response(400, json={"error": {"message": "not in this fake"}})


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", os.path.join(tempfile.mkdtemp(), "tracker.db"))
    monkeypatch.setattr(db, "_conn", None)
    db.init()
    db.kv_set("tracking_start", str(time.time() - 86400))
    db.kv_set("mode", "live")
    tracking._next_try.clear()
    app_module._hits.clear()
    hub._orders_cache.clear()
    hub._inflight.clear()
    hub._funnel_cache.clear()
    hub._identity_tried.clear()
    hub._login_failures.clear()
    hub._shop.update(name="", at=0.0)
    hub._order_count.update(at=0.0, n=None)
    hub._sales.update(at=0.0, full_at=0.0, total=None, last_id=0, task=None)
    meta_ads._cache.clear()
    # The volume check reads the real disk: tests don't depend on how full this machine is.
    import collections
    roomy = collections.namedtuple("usage", "total used free")(1000 * 1_048_576, 100 * 1_048_576, 900 * 1_048_576)
    monkeypatch.setattr(watchdog.shutil, "disk_usage", lambda path: roomy)
    pnl.reset()
    # The watchdog caches the webhook check for an hour and match quality for
    # six; tests that need a fresh read reset these themselves.
    watchdog._state.update(emq_at=time.time(), webhook_at=0.0, webhook=None, webhook_good=None)
    # hub_page.py is written separately; the tests only need its two strings.
    page = types.ModuleType("hub_page")
    page.HUB_HTML, page.LOGIN_HTML = "<main>hub</main>", "<form><p><!--error--></p></form>"
    monkeypatch.setitem(sys.modules, "hub_page", page)
    yield


@pytest.fixture
def shop(monkeypatch):
    fake = FakeShopify()
    monkeypatch.setattr(shopify, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture
def meta(monkeypatch):
    fake = FakeMeta()
    monkeypatch.setattr(meta_capi, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.capi)))
    monkeypatch.setattr(meta_ads, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.graph)))
    return fake


@pytest.fixture
def client(monkeypatch, shop, meta):
    monkeypatch.setattr(worker, "start", lambda: [])
    monkeypatch.setattr(tracking, "fire_and_forget", lambda coro: coro.close())
    app_module.mcp._session_manager = None        # the MCP session manager runs once per app
    with TestClient(app_module.create_app()) as c:
        yield c


def seed(shop):
    """Today: 4 new sales (ad id, ad name only, click only, organic), a rebill,
    a cancelled and a test order. Yesterday: one more sale."""
    a = make_order(101, total_price="59.95")
    # The live URL templates: the ad set in utm_content, the ad in utm_term.
    b = make_order(102, total_price="40.00", landing_site=(
        "/products/x?utm_source=facebook&utm_campaign=Leggings%20CBO&utm_content=Broad"
        "&utm_term=B2%20Statics%20-%20Ad%207"))
    c = make_order(103, total_price="30.00", landing_site="/products/x?fbclid=IwAR2abcDEFghiJKL")
    d = make_order(104, total_price="20.00")
    e = make_order(105, total_price="39.00", source_name="subscription_contract_checkout_one")
    f = make_order(106, cancelled_at=at(time.time()))
    g = make_order(107, test=True)
    h = make_order(108, ts=hub._range("today")["start"] - 3600)
    shop.orders = [a, b, c, d, e, f, g, h]
    # Order a went through the tracker: sent to Core Club, credited to ad AD1.
    db.upsert_order(a)
    db.mark_order("101", "sent", kind="purchase", fbtrace_id="t1")
    db.set_order_attribution("101", {"meta": True, "source": "browser", "click": True, "ad_id": "AD1",
                                     "adset_id": "AS1", "campaign_id": "C1", "ad_name": "B2 Statics - Ad 3",
                                     "adset_name": "Broad", "campaign_name": "Leggings CBO", "seen_at": time.time()})
    db.record_event("Purchase", "order_101", "webhook", "sent", {"user_data": {
        "em": ["x"], "ph": ["y"], "client_ip_address": "203.0.113.9", "fbc": "fb.1.1.CLICK"}},
        order_id="101", pixel_id=MAIN)
    db.upsert_order(e)
    db.mark_order("105", "failed", error="HTTP 400: Invalid parameter", kind="renewal")
    return shop.orders


def ad_rows():
    base = {"campaign_id": "C1", "campaign_name": "Leggings CBO", "impressions": "1000", "clicks": "20"}
    return [
        {**base, "adset_id": "AS1", "adset_name": "Broad", "ad_id": "AD1", "ad_name": "B2 Statics - Ad 3",
         "spend": "40", "actions": [{"action_type": "omni_purchase", "value": "2"}],
         "action_values": [{"action_type": "omni_purchase", "value": "119.90"}]},
        {**base, "adset_id": "AS1", "adset_name": "Broad", "ad_id": "AD7", "ad_name": "B2 Statics - Ad 7",
         "spend": "20"},
        {**base, "adset_id": "AS2", "adset_name": "Interests", "ad_id": "AD9", "ad_name": "Hook test - v2",
         "spend": "10"},
        {"campaign_id": "C2", "campaign_name": "Prospecting", "adset_id": "AS3", "adset_name": "LAL",
         "ad_id": "AD20", "ad_name": "Other ad", "spend": "100"},
    ]


# --- auth ---------------------------------------------------------------------------

def test_login_logout_and_page(client):
    r = client.get("/hub")
    assert r.status_code == 200 and "<form>" in r.text and r.headers["cache-control"] == "no-store"
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    r = client.post("/hub/login", content="token=wrong", headers=form, follow_redirects=False)
    assert r.status_code == 401 and "didn't match" in r.text and "<!--error-->" not in r.text
    r = client.post("/hub/login", content=f"token={ADMIN}", headers={**form, "X-Forwarded-Proto": "https"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/hub"
    cookie = r.headers["set-cookie"]
    assert f"hub_session={hub._session_value()}" in cookie and ADMIN not in cookie
    for part in ("httponly", "samesite=strict", "path=/hub", "max-age=7776000", "secure"):
        assert part in cookie.lower()
    session = {"Cookie": f"hub_session={hub._session_value()}"}
    assert client.get("/hub", headers=session).text == "<main>hub</main>"
    assert client.get("/hub", headers={"Cookie": "hub_session=forged"}).text.startswith("<form>")
    r = client.get("/hub/logout", headers=session, follow_redirects=False)
    assert r.status_code == 303 and 'hub_session=""' in r.headers["set-cookie"]


def test_api_needs_session_or_bearer_and_posts_need_header(client):
    r = client.get("/hub/api/watchdog")
    assert r.status_code == 401 and r.json() == {"error": "unauthorized"}
    assert client.get("/hub/api/watchdog", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/hub/api/watchdog", headers={"Cookie": f"hub_session={hub._session_value()}"}).status_code == 200
    assert client.get("/hub/api/watchdog", headers=API).status_code == 200
    assert client.post("/hub/api/watchdog/run", headers=API).status_code == 403
    assert client.post("/hub/api/resend/101").status_code == 401


def test_repeated_wrong_logins_are_throttled(client):
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    for _ in range(hub.LOGIN_FAILURES_ALLOWED):
        assert client.post("/hub/login", content="token=guess", headers=form,
                           follow_redirects=False).status_code == 401
    r = client.post("/hub/login", content=f"token={ADMIN}", headers=form, follow_redirects=False)
    assert r.status_code == 429


# --- ranges -------------------------------------------------------------------------

def test_parallel_sections_share_one_shopify_listing(shop):
    shop.orders = [make_order(1)]
    start = hub._range("today")["start"]

    async def load_page():
        return await asyncio.gather(*(hub._shopify_orders(start) for _ in range(4)))
    assert all(r == shop.orders for r in asyncio.run(load_page()))
    assert shop.listings == 1
    asyncio.run(hub._shopify_orders(start + 60))            # a narrower window reuses the cached one
    assert shop.listings == 1


def test_ranges_follow_the_store_clock():
    tz = config.store_tz()
    today = dt.datetime.now(tz).date()
    y = hub._range("yesterday")
    assert y["since"] == y["until"] == (today - dt.timedelta(days=1)).isoformat()
    assert y["end"] == hub._range("today")["start"]
    assert dt.datetime.fromtimestamp(y["start"], tz).hour == 0
    w = hub._range("7d")
    assert w["label"] == "Last 7 days" and w["since"] == (today - dt.timedelta(days=6)).isoformat()
    assert hub._range("30d")["since"] == (today - dt.timedelta(days=29)).isoformat()
    assert hub._range("bogus")["key"] == "today"


# --- data ---------------------------------------------------------------------------

def test_overview_and_orders_without_ads_connected(client, shop):
    seed(shop)
    r = client.get("/hub/api/overview?range=today", headers=API)
    assert r.status_code == 200
    o = r.json()
    assert o["error"] == "" and o["store"]["name"] == "Core Supplements" and o["range"]["label"] == "Today"
    cards = o["cards"]
    assert cards["new_sales"] == {"count": 4, "revenue": 149.95}
    assert cards["rebills"] == {"count": 1, "revenue": 39.0}
    assert cards["total_revenue"] == 188.95 and cards["orders"] == 5 and cards["aov"] == 37.49
    assert cards["spend"] is None and cards["true_roas"] is None and cards["ads_connected"] is False
    assert cards["ads_error"]
    s = o["series"]
    assert len(s["days"]) == 7 and s["new_sales"][-1] == 4 and s["new_sales"][-2] == 1
    assert s["spend"] == [None] * 7
    st = o["status"]
    assert st["level"] in ("ok", "warn", "fail") and st["checks"] and len(st["timeline"]) == 1
    assert st["headline"] == hub.HEADLINES[st["level"]] and st["mode"] == "live"
    q = o["quality"][0]
    assert q["pixel_id"] == MAIN and q["role"] == "main" and q["name"] == "Core Club"     # the default for its id
    assert q["emq"]["score"] is None and q["events_24h"]["sent"] == 1
    assert o["coverage"]["purchases"] == 1 and o["coverage"]["em"] == 100 and o["coverage"]["fbp"] == 0

    shop.listings = 0
    r = client.get("/hub/api/orders?range=today", headers=API)
    body = r.json()
    assert shop.listings == 0                           # served from the minute-long cache
    assert body["count"] == 7 and body["error"] == ""
    rows = {row["id"]: row for row in body["orders"]}
    assert "108" not in rows                            # yesterday's sale
    a = rows["101"]
    assert a["type_label"] == "New sale" and a["tracker_status"] == "sent" and a["items"] == "SpermFuel+ x1"
    assert a["pixels"] == [{"pixel_id": MAIN, "role": "main", "name": "Core Club", "sent": True}]
    assert a["details"] == {"email": True, "phone": True, "ip": True, "browser": False,
                            "ad_click_id": True, "browser_id": False}
    assert a["ad"]["ad_name"] == "B2 Statics - Ad 3" and a["ad"]["source"] == "browser"
    assert a["channel"] == "Meta ads"
    assert rows["102"]["ad"]["ad_name"] == "B2 Statics - Ad 7" and rows["102"]["tracker_status"] == "not_seen"
    assert rows["102"]["ad"]["adset_name"] == "Broad"
    # Only a first landing page with an fbclid, and nothing else known: its time can't be checked.
    assert rows["103"]["ad"] == {"click": True, "ad_name": "", "adset_name": "", "campaign_name": "",
                                 "ad_id": "", "source": "first_visit_unverified"}
    assert rows["104"]["ad"] is None and rows["104"]["channel"] == "Direct"
    assert rows["105"]["type"] == "rebill" and rows["105"]["ad"] is None and rows["105"]["can_resend"]
    assert rows["105"]["channel"] is None
    assert rows["105"]["error"] == "HTTP 400: Invalid parameter"
    assert rows["106"]["type_label"] == "Skipped: Cancelled" and not rows["106"]["can_resend"]
    assert rows["107"]["type_label"] == "Skipped: Test order"
    for text in (r.text, json.dumps(o)):
        assert not any(p in text for p in PII)
    assert client.get("/hub/api/orders?range=yesterday&limit=0", headers=API).json()["count"] == 1


def test_creatives_match_meta_rows_by_id_then_name(client, shop, meta, monkeypatch):
    seed(shop)
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    monkeypatch.setattr(config, "HUB_MIN_AD_SPEND", 0)    # every ad its own row (the $15 line has its own test)
    meta.ad_rows = ad_rows()
    meta.daily = [{"date_start": hub._today().isoformat(), "spend": "170"}]
    body = client.get("/hub/api/creatives?range=today", headers=API).json()
    assert body["connected"] is True and body["error"] == ""
    assert [c["campaign_name"] for c in body["campaigns"]] == ["Prospecting", "Leggings CBO"]
    cbo = body["campaigns"][1]
    assert cbo["spend"] == 70 and cbo["store_sales"] == 2 and cbo["store_revenue"] == 99.95
    broad = next(g for g in cbo["groups"] if g["name"] == "Broad")
    ads = {a["ad_id"]: a for a in broad["ads"]}
    assert ads["AD1"]["orders"] == ["#c101"] and ads["AD1"]["meta_purchases"] == 2
    assert ads["AD1"]["roas_store"] == 1.5 and ads["AD1"]["roas_meta"] == 3.0
    assert ads["AD7"]["orders"] == ["#c102"]            # matched by ad name from utm_content
    t = body["totals"]
    assert t["spend"] == 170 and t["store_sales"] == 3 and t["true_roas"] == round(149.95 / 170, 2)
    assert body["unlabelled"] == {"store_sales": 1, "store_revenue": 30.0, "orders": ["#c103"]}
    assert body["url_tracking"] == {"tagged_orders": 2, "meta_orders": 3}

    batch = client.get("/hub/api/creatives?range=today&group=batch", headers=API).json()
    names = {g["name"] for g in batch["campaigns"][1]["groups"]}
    assert names == {"B2 Statics"}                       # Hook test has no seller: folded into the line

    cards = client.get("/hub/api/overview?range=today", headers=API).json()["cards"]
    assert cards["ads_connected"] and cards["spend"] == 170 and cards["meta_purchases"] == 2
    assert cards["true_roas"] == round(149.95 / 170, 2) and cards["cost_per_sale"] == 42.5


def test_creatives_without_ads_come_from_store_sales(client, shop):
    seed(shop)
    body = client.get("/hub/api/creatives?range=today", headers=API).json()
    assert body["connected"] is False and "META_ADS_TOKEN" in body["error"]
    camp = body["campaigns"][0]
    assert camp["campaign_name"] == "Leggings CBO" and camp["spend"] == 0 and camp["roas_store"] is None
    assert camp["store_sales"] == 2 and body["totals"]["true_roas"] is None


def test_shopify_down_gives_partial_data_not_500(client, shop):
    shop.fail = 403
    for path in ("overview", "orders", "creatives", "funnel"):
        r = client.get(f"/hub/api/{path}?range=7d", headers=API)
        assert r.status_code == 200 and "Shopify answered 403" in r.json()["error"], path
    # Unknown, not zero: the page shows "-" and the error note, never "0 sales" or "0.00x".
    o = client.get("/hub/api/overview", headers=API).json()
    assert "Shopify answered 403" in o["error"]
    assert o["cards"]["new_sales"] == {"count": None, "revenue": None}


def test_funnel_splits_meta_browsers_from_the_rest(client, shop):
    seed(shop)
    now = time.time()
    # b-ad placed order #c101 and b-organic #c104 (checkout tokens); nobody's
    # browser is known for the other sales, so they aren't funnel purchases.
    db.upsert_session("b-ad", ad_params=json.dumps({"utm_source": "facebook"}), ad_seen_at=now,
                      checkout_token="chk101")
    db.upsert_session("b-click", fbc=f"fb.1.{int(now * 1000)}.CLICK")
    db.upsert_session("b-organic", fbp="fb.1.1.2", checkout_token="chk104")
    db.upsert_session("b-stale", fbc=f"fb.1.{int((now - 30 * 86400) * 1000)}.OLD")
    for cid, events in {"b-ad": ["PageView", "ViewContent"], "b-click": ["PageView", "AddToCart"],
                        "b-organic": ["PageView", "PageView"], "b-stale": ["PageView"]}.items():
        for i, name in enumerate(events):
            db.record_event(name, f"{cid}-{i}", "pixel", "sent", {"user_data": {}}, client_id=cid)
    body = client.get("/hub/api/funnel?range=today", headers=API).json()
    assert body["steps"][0] == "Visitors" and body["error"] == ""
    # Each browser at the furthest step it reached (a buyer at every step): b-ad bought,
    # b-click added to cart, b-organic bought, b-stale only visited (its ad click is 30 days old).
    assert body["meta"] == [2, 2, 2, 1, 1] and body["other"] == [2, 1, 1, 1, 1]
    assert body["all"] == [4, 3, 3, 2, 2] and body["untied_sales"] == 2     # #c102 and #c103: no browser known
    assert chr(0x2014) not in body["note"]                   # no em dashes in owner-facing copy


# --- actions ------------------------------------------------------------------------

def test_resend_and_test_event_return_no_customer_details(client, shop, meta):
    seed(shop)
    r = client.post("/hub/api/resend/104", headers=POST)
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["status"] == "sent" and body["order_name"] == "#c104"
    assert body["message"].startswith("Sent to Meta") and body["events"][0]["event_name"] == "Purchase"
    assert not any(p in r.text for p in PII) and "order_json" not in r.text and "payload" not in r.text
    assert (MAIN, ["Purchase"]) in meta.sent
    r = client.post("/hub/api/test-event", headers=POST, json={"test_event_code": "TEST1", "pixel_id": None})
    assert r.json()["ok"] is True and meta.sent[-1] == (MAIN, ["PageView"])
    assert client.post("/hub/api/test-event", headers=POST, content="nope").json()["ok"] is False
    assert client.post("/hub/api/resend/abc", headers=POST).json()["message"].startswith("That isn't")
    shop.fail = 403
    body = client.post("/hub/api/resend/104", headers=POST).json()
    assert body["ok"] is False and "myshopify" not in body["error"] and body["message"].startswith("Couldn't load")


def test_a_crash_becomes_an_error_not_a_500(client, monkeypatch):
    def boom(now):
        raise RuntimeError("bug")
    monkeypatch.setattr(hub, "_quality", boom)
    r = client.get("/hub/api/overview", headers=API)
    assert r.status_code == 200 and "couldn't be loaded" in r.json()["error"]


def test_login_says_so_when_admin_token_is_missing(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    r = client.post("/hub/login", content="token=", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 401 and "ADMIN_TOKEN is set" in r.text
    assert client.get("/hub/api/watchdog", headers={"Authorization": "Bearer "}).status_code == 401


def test_the_header_sums_every_dollar_the_store_took_toward_the_goal(client, shop, meta, monkeypatch):
    shop.orders = [make_order(9001, current_total_price="100.00"), make_order(9002, current_total_price="50.50"),
                   make_order(9003, current_total_price="40.00", test=True),           # a test order: left out
                   make_order(9004, current_total_price="0.00", cancelled_at="2026-10-01T10:00:00-04:00")]
    body = client.get("/hub/api/overview?range=today", headers=API).json()
    assert (body["sales_all_time"], body["sales_goal"]) == (150.5, 1_000_000) and shop.since == [0]
    # A new order is added on the next minute's read, from where the last read ended (not a recount).
    shop.orders.append(make_order(9005, current_total_price="25.00"))
    assert client.get("/hub/api/overview?range=today", headers=API).json()["sales_all_time"] == 150.5   # same minute
    hub._sales["at"] = 0.0
    assert client.get("/hub/api/overview?range=today", headers=API).json()["sales_all_time"] == 175.5
    assert shop.since == [0, 9004]
    # A refund on an older order shows at the half-hourly recount, which runs in the background.
    shop.orders[0]["current_total_price"] = "60.00"
    hub._sales.update(at=0.0, full_at=0.0)

    async def recount():
        before = await hub._sales_all_time()             # answers at once with the last sum...
        await hub._sales["task"]                         # ...while the recount runs
        return before, hub._sales["total"]
    assert asyncio.run(recount()) == (175.5, 135.5) and shop.since[-1] == 0
    # Kept across a restart; Shopify not answering keeps the last sum rather than showing none.
    hub._sales.update(at=0.0, full_at=time.time(), total=None, last_id=0, task=None)
    db.kv_set("sales_all_time", json.dumps({"total": 135.5, "last_id": 9005, "full_at": time.time()}))
    shop.fail = 404
    assert asyncio.run(hub._sales_all_time()) == 135.5


def test_the_header_counts_every_order_shopify_ever_had(client, shop, meta):
    shop.count = 3071
    body = client.get("/hub/api/overview?range=today", headers=API).json()
    assert body["orders_all_time"] == 3071 and shop.counts == 1
    # Asked at most once a minute: a refresh within the minute reuses it.
    shop.count = 3072
    assert client.get("/hub/api/overview?range=today", headers=API).json()["orders_all_time"] == 3071
    assert shop.counts == 1
    hub._order_count["at"] = 0.0
    assert client.get("/hub/api/overview?range=today", headers=API).json()["orders_all_time"] == 3072
    # Shopify not answering keeps the last count, even across a restart (it is saved).
    shop.fail = 404                                              # (a 5xx would be retried for seconds)
    hub._order_count.update(at=0.0, n=None)
    assert asyncio.run(hub._orders_all_time()) == 3072
    db.kv_set("orders_all_time", "")
    hub._order_count.update(at=0.0, n=None)
    assert asyncio.run(hub._orders_all_time()) is None             # never read: the page leaves it out


def test_watchdog_history_and_run_now(client, shop):
    db.add_watchdog_run("ok", [{"id": "a", "name": "A", "status": "ok", "detail": "fine"}])
    db.add_watchdog_run("warn", [{"id": "a", "name": "A", "status": "warn", "detail": "hmm"}])
    runs = client.get("/hub/api/watchdog", headers=API).json()["runs"]
    assert [r["status"] for r in runs] == ["warn", "ok"]
    r = client.post("/hub/api/watchdog/run", headers=POST).json()
    assert r["status"] in ("ok", "warn", "fail") and any(c["id"] == "orders" for c in r["checks"])
    assert len(client.get("/hub/api/watchdog", headers=API).json()["runs"]) == 3


# =====================================================================================
# attribution.py
# =====================================================================================

# The live URL templates put the ad set in utm_content and the ad in utm_term.
AD_URL = ("https://getcoresupps.com/products/spermfuel?utm_source=facebook&utm_medium=paid"
          "&utm_campaign=Leggings%20CBO&utm_content=Broad&utm_term=B2%20Statics%20-%20Ad%203"
          "&campaign_id=C1&adset_id=AS1&ad_id=AD1&fbclid=IwAR2abcDEFghiJKL")
AD_PARAMS = {"utm_source": "facebook", "utm_medium": "paid", "utm_campaign": "Leggings CBO",
             "utm_content": "Broad", "utm_term": "B2 Statics - Ad 3", "campaign_id": "C1",
             "adset_id": "AS1", "ad_id": "AD1", "fbclid": "1"}
AD1 = {"ad_id": "AD1", "adset_id": "AS1", "campaign_id": "C1", "utm_source": "facebook",
       "utm_content": "Broad", "utm_term": "B2 Statics - Ad 3", "utm_campaign": "Leggings CBO"}


def browser(params, seen_at):
    return {"client_id": "b1", "ad_params": json.dumps(params), "ad_seen_at": seen_at}


def test_ad_params_from_a_meta_ad_link():
    assert attribution.ad_params_from_url(AD_URL + "&gclid=G1&color=red") == AD_PARAMS
    # Shopify's landing_site is a bare path; it parses the same way.
    assert attribution.ad_params_from_url("/products/x?utm_source=IG&utm_content=Reel%201") == {
        "utm_source": "IG", "utm_content": "Reel 1"}
    assert attribution.ad_params_from_url("/?ad_id=AD9") == {"ad_id": "AD9"}
    assert attribution.ad_params_from_url("/?utm_source=an&utm_id=C9") == {"utm_source": "an", "utm_id": "C9"}
    long = attribution.ad_params_from_url("/?utm_source=fb&utm_content=" + "x" * 1000)
    assert len(long["utm_content"]) == 300


def test_fbclid_alone_proves_a_click_but_its_value_is_not_kept():
    assert attribution.ad_params_from_url("https://getcoresupps.com/?fbclid=IwAR2abcDEFghiJKL") == {"fbclid": "1"}
    assert "IwAR2" not in json.dumps(attribution.ad_params_from_url(AD_URL))


@pytest.mark.parametrize("url", [
    "https://getcoresupps.com/products/x?utm_source=google&utm_medium=cpc&utm_campaign=brand",
    "https://getcoresupps.com/?utm_source=tiktok&utm_content=Ad%203",
    "https://getcoresupps.com/?utm_source=klaviyo&utm_campaign=Welcome&utm_content=Hero",
    "https://getcoresupps.com/?fbclid=&utm_source=%20",
    "https://getcoresupps.com/products/x",
    "", None, 12345,
])
def test_ad_params_ignore_links_that_are_not_meta_ads(url):
    assert attribution.ad_params_from_url(url) == {}


def credit_of(order, sess=None, **kw):
    """The attribution record the one resolver stores for a sale."""
    return attribution.resolve(order, sess, **kw)["attribution"]


RECORD_KEYS = {"v", "meta", "source", "click", "ad_id", "adset_id", "campaign_id", "ad_name", "adset_name",
               "campaign_name", "ambiguous", "click_at", "lp", "ids_stripped", "via", "channel", "first_touch", "assists"}


def test_order_attribution_credits_the_browsers_ad_inside_the_window(monkeypatch):
    monkeypatch.setattr(config, "ATTRIBUTION_WINDOW_DAYS", 7)
    created = float(int(time.time()) - 600)
    order = {"created_at": at(created), "landing_site": "/?utm_source=facebook&utm_term=Landing%20ad"}
    seen = created - 3 * 86400
    c = credit_of(order, browser(AD1, seen))
    assert set(c) == RECORD_KEYS
    assert {k: c[k] for k in RECORD_KEYS - {"first_touch", "assists"}} == {
        "v": attribution.RESOLVER_VERSION, "meta": True, "source": "browser", "click": False,
        "ad_id": "AD1", "adset_id": "AS1", "campaign_id": "C1", "ad_name": "B2 Statics - Ad 3",
        "adset_name": "Broad", "campaign_name": "Leggings CBO", "ambiguous": False, "click_at": seen,
        "lp": "", "ids_stripped": False, "via": "", "channel": "Meta ads"}
    # The landing page is the buyer's first visit: its ad is the first touch, and an assist.
    assert c["first_touch"]["ad_name"] == "Landing ad" and c["first_touch"]["from"] == "landing_site"
    assert [(a["ad_name"], a.get("first_touch")) for a in c["assists"]] == [("Landing ad", True)]
    # The last second of the window still counts.
    edge = credit_of(order, browser(AD1, created - 7 * 86400))
    assert edge["source"] == "browser" and edge["click"] is False
    # The fbc from that same arrival makes it a click, and is what Meta is sent.
    fbc = attribution.make_fbc("IwAR2abcDEFghiJKL", seen)
    d = attribution.resolve(order, {**browser({**AD1, "fbclid": "1"}, seen), "fbc": fbc})
    assert d["attribution"]["click"] is True and d["fbc"] == fbc
    # utm_id stands in for a missing campaign_id; stored params may already be a dict.
    c = credit_of(order, {"ad_params": {"utm_source": "fb", "utm_id": "C9"}, "ad_seen_at": seen})
    assert c["source"] == "browser" and c["campaign_id"] == "C9"


def test_the_first_landing_page_only_sells_when_nothing_else_exists(monkeypatch):
    monkeypatch.setattr(config, "ATTRIBUTION_WINDOW_DAYS", 7)
    created = float(int(time.time()) - 600)
    order = {"created_at": at(created), "landing_site": (
        "/products/x?utm_source=facebook&utm_campaign=CBO&utm_content=Broad&utm_term=Landing%20ad&ad_id=AD5")}
    for sess in ({}, None,
                 browser(AD1, created + 60),                        # ad seen only after the sale
                 {"ad_params": "{not json", "ad_seen_at": created - 60},
                 {"ad_params": json.dumps(AD1), "ad_seen_at": None}):
        c = credit_of(order, sess)
        assert (c["meta"], c["source"], c["click"], c["click_at"], c["ad_id"], c["ad_name"], c["adset_name"],
                c["campaign_name"]) == (True, "first_visit_unverified", False, None, "AD5", "Landing ad", "Broad",
                                        "CBO"), sess
        assert c["first_touch"]["ad_id"] == "AD5" and c["assists"] == []      # the seller is not its own assist
    # An ad seen 8 days before the sale: the first landing page came before it, so it is
    # older than the window too. Not a Meta sale; the landing ad is still the first touch.
    c = credit_of(order, browser(AD1, created - 8 * 86400))
    assert (c["meta"], c["source"], c["ad_id"], c["channel"]) == (False, "", None, "Direct")
    assert c["first_touch"]["ad_id"] == "AD5" and c["first_touch"]["at"] is None
    click = {"created_at": at(created), "landing_site": "/?fbclid=IwAR2abcDEFghiJKL"}
    d = attribution.resolve(click, {})
    c = d["attribution"]
    assert c["source"] == "first_visit_unverified" and c["click"] is True and c["meta"] is True
    assert c["ad_id"] is None and c["ad_name"] == ""
    # Its time unknown, it goes to Meta as before: stamped with the order's time.
    assert d["fbc"] == f"fb.1.{int(created * 1000)}.IwAR2abcDEFghiJKL"
    # Shopify's first-visit record gives its real time: used for the fbc, and the window applies.
    page = "https://getcoresupps.com/?fbclid=IwAR2abcDEFghiJKL"
    d = attribution.resolve(click, {}, journey={"firstVisit": {"occurredAt": at(created - 3600), "landingPage": page}})
    assert d["attribution"]["source"] == "first_visit" and d["attribution"]["click_at"] == created - 3600
    assert d["fbc"] == attribution.make_fbc("IwAR2abcDEFghiJKL", created - 3600)
    d = attribution.resolve(click, {}, journey={"firstVisit": {"occurredAt": at(created - 10 * 86400),
                                                               "landingPage": page}})
    assert d["attribution"]["meta"] is False and d["attribution"]["channel"] == "Direct"
    assert d["fbc"] == attribution.make_fbc("IwAR2abcDEFghiJKL", created - 10 * 86400)   # its real, old time
    # So does the pixel's record of when that fbclid arrived.
    sess = {"ad_history": json.dumps([{**attribution.ad_visit({"ad_id": "AD9"}, created - 9 * 86400,
                                                               "IwAR2abcDEFghiJKL")}])}
    assert credit_of(click, sess)["meta"] is False


def test_order_attribution_with_only_a_click_or_nothing():
    created = time.time() - 60
    d = attribution.resolve({"created_at": at(created), "landing_site": "/"},
                            {"fbc": f"fb.1.{int((created - 60) * 1000)}.IwAR2abcDEFghiJKL"})
    c = d["attribution"]
    assert (c["meta"], c["source"], c["click"], c["ad_id"], c["channel"]) == (True, "click_id", True, None, "Meta ads")
    nothing = credit_of({"created_at": at(created), "landing_site": "/?utm_source=google"}, {})
    assert (nothing["meta"], nothing["source"], nothing["click"]) == (False, "", False)
    assert nothing["channel"] == "Google" and nothing["assists"] == [] and nothing["first_touch"] is None
    # An order without a usable time is judged against now.
    assert credit_of({"created_at": None}, browser(AD1, time.time() - 3600))["source"] == "browser"


def test_a_click_only_counts_when_it_is_inside_the_window(monkeypatch):
    monkeypatch.setattr(config, "ATTRIBUTION_WINDOW_DAYS", 7)
    created = time.time() - 60
    order = {"created_at": at(created), "landing_site": "/"}
    recent = f"fb.1.{int((created - 86400) * 1000)}.IwAR2recent"
    stale = f"fb.1.{int((created - 20 * 86400) * 1000)}.IwAR2stale"
    assert attribution.click_time(recent) == int((created - 86400) * 1000) / 1000
    assert attribution.click_time("fb.1.x.IwAR2") is None and attribution.click_time("") is None
    d = attribution.resolve(order, {"fbc": recent})
    assert (d["attribution"]["meta"], d["attribution"]["source"], d["fbc"]) == (True, "click_id", recent)
    # A returning customer whose ad cookie is 20 days old bought on their own. Meta
    # still gets that click with its real time and applies its own rules.
    d = attribution.resolve(order, {"fbc": stale})
    assert (d["attribution"]["meta"], d["attribution"]["source"], d["fbc"]) == (False, "", stale)
    assert credit_of(order, {"fbc": ""})["meta"] is False
    # A click whose time can't be read still counts when nothing else is known.
    assert credit_of(order, {"fbc": "fb.1.x.IwAR2"})["source"] == "click_id"
    # The browser's in-window ad gets the sale; the old cookie isn't this sale's click.
    c = credit_of(order, {**browser(AD1, created - 3600), "fbc": stale})
    assert c["source"] == "browser" and c["click"] is False


@pytest.mark.parametrize("name, batch", [
    ("B2 Statics - Ad 3", "B2 Statics"),
    ("B2 Statics - Ad 12", "B2 Statics"),
    ("b2 statics - AD 3", "b2 statics"),
    ("B2 Statics Ad3", "B2 Statics"),
    ("Hook test - v2", "Hook test"),
    ("Hook test-v2", "Hook test"),
    ("UGC Sarah | Var 4", "UGC Sarah"),
    ("Founder story: Version 2", "Founder story"),
    ("Carousel #3", "Carousel"),
    ("B2_Statics_Ad3", "B2_Statics"),
    ("B2 Statics \u2014 Ad 3", "B2 Statics"),
    ("Batch 4 - Ad 1", "Batch 4"),
    ("Summer Sale", "Summer Sale"),
    ("Ad 3", "Ad 3"),                        # nothing left to group by: keep the name
    ("", ""),
    # Names that merely end in "ad", "v" or a number are not variants.
    ("UGC Brad 2", "UGC Brad 2"),
    ("Squad 3", "Squad 3"),
    ("Promo Nov 5", "Promo Nov 5"),
    ("Rev2", "Rev2"),
])
def test_family_groups_creatives_by_batch(name, batch):
    assert attribution.family(name) == batch


# =====================================================================================
# meta_ads.py
# =====================================================================================

class Graph:
    """The Marketing API reads meta_ads makes: ad accounts, paged insights,
    Dataset Quality and dataset names."""

    def __init__(self):
        self.accounts = {"123": {"name": "Core", "currency": "USD", "timezone_name": "America/New_York"}}
        self.pages = {"123": [[]]}          # per account: pages of level=ad rows
        self.daily = {}                     # per account: level=account rows
        self.denied = {}                    # per account: (status, body)
        self.quality = {"web": []}
        self.quality_error = None
        self.ads = {}                       # per account: the ads /act_X/ads lists, with their creatives
        self.hourly = {}                    # per account: level=ad rows with an hourly breakdown
        self.requests = []

    def handler(self, request: httpx.Request):
        self.requests.append(request)
        path, params = request.url.path, request.url.params
        m = re.search(r"/act_(\d+)/ads$", path)
        if m:
            if m.group(1) in self.denied:
                status, body = self.denied[m.group(1)]
                return httpx.Response(status, json=body)
            return httpx.Response(200, json={"data": self.ads.get(m.group(1), [])})
        m = re.search(r"/act_(\d+)(/insights)?$", path)
        if m:
            acct = m.group(1)
            if acct in self.denied:
                status, body = self.denied[acct]
                return httpx.Response(status, json=body)
            if not m.group(2):
                return httpx.Response(200, json=self.accounts[acct])
            if params.get("level") == "account":
                return httpx.Response(200, json={"data": self.daily.get(acct, [])})
            if params.get("breakdowns"):
                return httpx.Response(200, json={"data": self.hourly.get(acct, [])})
            pages = self.pages.get(acct, [[]])
            page = int(params.get("after", "0"))
            body = {"data": pages[page]}
            if page + 1 < len(pages):
                body["paging"] = {"next": f"https://graph.facebook.com/v21.0/act_{acct}/insights"
                                          f"?level=ad&after={page + 1}&access_token=ads-secret"}
            return httpx.Response(200, json=body)
        if path.endswith("/dataset_quality"):
            if self.quality_error:
                return httpx.Response(403, json={"error": {"message": self.quality_error}})
            return httpx.Response(200, json=self.quality)
        if path.endswith(f"/{MAIN}"):
            return httpx.Response(200, json={"name": "Core Club", "id": MAIN})
        return httpx.Response(400, json={"error": {"message": "not in this fake"}})


@pytest.fixture
def graph(monkeypatch):
    fake = Graph()
    monkeypatch.setattr(config, "META_ADS_TOKEN", "ads-secret")
    meta_ads.set_http_client(httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


def insight(ad_id, name, spend, purchases=None, value=None, **over):
    row = {"campaign_id": "C1", "campaign_name": "Leggings CBO", "adset_id": "AS1", "adset_name": "Broad",
           "ad_id": ad_id, "ad_name": name, "spend": str(spend), "impressions": "1000", "clicks": "20"}
    if purchases is not None:
        # Meta lists the same purchases under several types; the pixel one often differs.
        row["actions"] = [{"action_type": "link_click", "value": "20"},
                          {"action_type": "offsite_conversion.fb_pixel_purchase", "value": str(purchases + 1)},
                          {"action_type": "omni_purchase", "value": str(purchases)}]
    if value is not None:
        row["action_values"] = [{"action_type": "omni_purchase", "value": str(value)}]
    row.update(over)
    return row


def test_purchases_picks_the_purchase_action_type():
    pixel = {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "5"}
    plain = {"action_type": "purchase", "value": "4"}
    omni = {"action_type": "omni_purchase", "value": "3"}
    clicks = {"action_type": "link_click", "value": "90"}
    assert meta_ads.purchases([clicks, pixel, plain, omni]) == 3.0        # Ads Manager's Purchases column
    assert meta_ads.purchases([clicks, pixel, plain]) == 4.0
    assert meta_ads.purchases([pixel, clicks]) == 5.0
    assert meta_ads.purchases([clicks]) == 0.0
    assert meta_ads.purchases(None) == meta_ads.purchases([]) == 0.0
    assert meta_ads.purchases(["junk", {"action_type": "omni_purchase", "value": "n/a"}]) == 0.0
    assert meta_ads.purchases([{"action_type": "omni_purchase", "value": "119.90"}]) == 119.9


def test_ad_insights_parses_rows_and_follows_paging(graph, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    graph.pages["123"] = [[insight("AD1", "B2 Statics - Ad 3", "40.50", purchases=2, value="119.90")],
                          [insight("AD7", "B2 Statics - Ad 7", 20)],
                          [insight("AD9", "Hook test - v2", "0", adset_id="AS2", adset_name="Interests")]]
    res = asyncio.run(meta_ads.ad_insights("2026-09-20", "2026-09-26"))
    assert res["connected"] is True and res["error"] == "" and res["currency"] == "USD"
    assert res["accounts"] == [{"id": "123", "name": "Core", "currency": "USD", "timezone": "America/New_York"}]
    assert [r["ad_id"] for r in res["rows"]] == ["AD1", "AD7", "AD9"]
    assert res["rows"][0] == {
        "account_id": "123", "campaign_id": "C1", "campaign_name": "Leggings CBO", "adset_id": "AS1",
        "adset_name": "Broad", "ad_id": "AD1", "ad_name": "B2 Statics - Ad 3", "spend": 40.5,
        "impressions": 1000, "clicks": 20, "meta_purchases": 2.0, "meta_value": 119.9, "meta_add_to_carts": 0.0}
    assert res["rows"][1]["meta_purchases"] == 0.0 and res["rows"][1]["meta_value"] == 0.0
    calls = [r for r in graph.requests if r.url.path.endswith("/insights")]
    assert len(calls) == 3
    first = calls[0].url.params
    assert first["level"] == "ad" and json.loads(first["time_range"]) == {"since": "2026-09-20", "until": "2026-09-26"}
    # The ads token, not the Conversions API one, on every page: in a header, never in the URL.
    assert {r.headers["authorization"] for r in calls} == {"Bearer ads-secret"}
    assert not any("access_token" in str(r.url) or "ads-secret" in str(r.url) for r in graph.requests)
    assert not any(s in json.dumps(res) for s in SECRETS)
    # Cached: the hub's sections loading together don't each ask Meta again.
    asyncio.run(meta_ads.ad_insights("2026-09-20", "2026-09-26"))
    assert len(graph.requests) == 4                          # the account, then three pages


def test_ad_insights_falls_back_to_the_conversions_token(graph, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    monkeypatch.setattr(config, "META_ADS_TOKEN", "")
    assert asyncio.run(meta_ads.ad_insights("2026-09-26", "2026-09-26"))["connected"] is True
    assert {r.headers["authorization"] for r in graph.requests} == {"Bearer test-token"}


def test_ad_insights_reports_a_meta_403_instead_of_raising(graph, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123", "456"])
    graph.accounts["456"] = {"name": "Leggings", "currency": "USD", "timezone_name": "America/New_York"}
    graph.pages["456"] = [[insight("AD20", "Other ad", 100, campaign_id="C2")]]
    graph.denied["123"] = (403, {"error": {"message": "(#200) Ad account owner has NOT grant ads_read permission",
                                           "type": "OAuthException", "code": 200}})
    res = asyncio.run(meta_ads.ad_insights("2026-09-26", "2026-09-26"))
    assert res["connected"] is False
    assert res["error"] == "act_123: (#200) Ad account owner has NOT grant ads_read permission"
    assert [r["ad_id"] for r in res["rows"]] == ["AD20"]     # the healthy account still reads
    assert not any(s in json.dumps(res) for s in SECRETS)


def test_ad_insights_survives_network_errors_and_odd_answers(monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])

    def run(handler):
        meta_ads.set_http_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        return asyncio.run(meta_ads.ad_insights("2026-09-26", "2026-09-26"))

    def down(request):
        raise httpx.ConnectError("no route", request=request)
    assert run(down) == {"connected": False, "rows": [], "currency": "", "accounts": [],
                         "error": "act_123: network: ConnectError"}
    res = run(lambda request: httpx.Response(502, json={"error": "Bad gateway"}))
    assert res["connected"] is False and "Bad gateway" in res["error"]
    res = run(lambda request: httpx.Response(500, text="<html>oops</html>"))
    assert res["connected"] is False and res["error"] == "act_123: HTTP 500"


def test_ad_insights_without_accounts_says_how_to_connect(graph, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", [])
    res = asyncio.run(meta_ads.ad_insights("2026-09-26", "2026-09-26"))
    assert res["connected"] is False and "META_AD_ACCOUNT_IDS" in res["error"] and res["rows"] == []
    assert asyncio.run(meta_ads.daily_spend("2026-09-20", "2026-09-26")) == {}
    assert graph.requests == []


def test_daily_spend_adds_up_every_account_per_day(graph, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123", "456"])
    graph.daily = {"123": [{"date_start": "2026-09-25", "spend": "10.5"}, {"date_start": "2026-09-26", "spend": "20"}],
                   "456": [{"date_start": "2026-09-26", "spend": "5.25"}]}
    assert asyncio.run(meta_ads.daily_spend("2026-09-25", "2026-09-26")) == {"2026-09-25": 10.5, "2026-09-26": 25.25}
    call = next(r for r in graph.requests if r.url.params.get("level") == "account")
    assert call.url.params["time_increment"] == "1"


def test_dataset_quality_reads_the_score_and_key_coverage(graph):
    graph.quality = {"web": [
        {"event_name": "Purchase", "event_match_quality": {"composite_score": 8.1, "match_key_feedback": [
            {"identifier": "email", "coverage": {"percentage": 98.5}},
            {"identifier": "phone", "coverage": {"percentage": 61}},
            {"identifier": "fbc"}, "junk"]}},
        {"event_name": "PageView", "event_match_quality": {"composite_score": 6.2}},
        {"event_name": "AddToCart"}]}
    assert asyncio.run(meta_ads.dataset_quality(MAIN, "test-token")) == {
        "Purchase": {"score": 8.1, "keys": {"email": 98.5, "phone": 61, "fbc": None}},
        "PageView": {"score": 6.2, "keys": {}},
        "AddToCart": {"score": None, "keys": {}}}
    q = graph.requests[-1].url.params
    assert q["dataset_id"] == MAIN and "event_match_quality" in q["fields"] and "access_token" not in q
    assert graph.requests[-1].headers["authorization"] == "Bearer test-token"
    graph.quality_error = "Unsupported get request"
    with pytest.raises(meta_ads.MetaReadError):
        asyncio.run(meta_ads.dataset_quality(MAIN, "test-token"))
    assert asyncio.run(meta_ads.dataset_name(MAIN, "test-token")) == "Core Club"
    assert asyncio.run(meta_ads.dataset_name("999", "test-token")) == ""     # unknown: blank, not a crash


# =====================================================================================
# watchdog.py
# =====================================================================================

TRACKER_URL = "https://tracker.example"
GOOD_KEYS = {"em": ["h"], "ph": ["h"], "client_ip_address": "203.0.113.9", "client_user_agent": "UA",
             "fbc": "fb.1.1.C", "fbp": "fb.1.1.P"}
HEALTHY_CHECKS = ("storage", "settings", "pixel", "shopify", "webhook", "orders", "pnl_revenue", f"pixel:{MAIN}",
                  "renewals", "details", f"emq:{MAIN}", "stripped", "click_ids", "ad_links", "unnamed_sales", "journey",
                  "first_visit", "ads", "meta_vs_store")


def graph_ad(ad_id, name, adset, link, tags, status="ACTIVE"):
    """An ad as /act_X/ads lists it: its ad set, campaign and creative (website URL and URL parameters)."""
    return {"id": ad_id, "name": name, "effective_status": status, "adset": {"id": "set-" + adset, "name": adset},
            "campaign": {"id": "C2", "name": "sperm 2"},
            "creative": {"url_tags": tags, "object_story_spec": {"link_data": {"link": link}}}}


NAMING_TAGS = "utm_source={{site_source_name}}&utm_campaign={{campaign.name}}&ad_id={{ad.id}}"


def test_ad_links_check_names_live_ads_whose_link_cannot_name_them(wd, graph):
    # #c4085: the "top" ads had no URL parameters, so their sales could only be "a Meta ad". Red, by name.
    graph.ads["123"] = [graph_ad("1", "New Sales Ad - Copy", "top", "https://l.example/?fbclid=fbclid", ""),
                        graph_ad("2", "New Sales Ad - Copy 3", "NB6", "https://l.example/?fbclid=fbclid", NAMING_TAGS),
                        graph_ad("3", "Static 1", "B8", "https://getcoresupps.com/products/spermfuel", NAMING_TAGS)]
    # The owner's "reject save" ads point at google.com on purpose: never a fault, never counted.
    graph.ads["123"].append(graph_ad("5", "Saved", "S", "http://google.com/", ""))
    c = run_checks()["ad_links"]
    assert c["status"] == "fail"
    assert c["detail"].startswith("1 live ad without URL parameters naming the ad: sperm 2 \u203a top \u203a "
                                  "New Sales Ad - Copy. A sale from them can only be credited to a Meta ad")
    # The stand-in links are named in the same line, not hidden behind the red one.
    assert "(they end in ad_id={{ad.id}}). 2 live ads with ?fbclid=fbclid in the website URL: " in c["detail"]
    # Parameters on every ad, but a stand-in click id typed into two website URLs: a warning that names them.
    graph.ads["123"][0]["creative"]["url_tags"] = NAMING_TAGS
    meta_ads.reset_links()
    c = run_checks()["ad_links"]
    assert c["status"] == "ok"                          # named, as information (Oct 6 2026: his choice per ad)
    assert c["detail"].startswith("2 live ads with ?fbclid=fbclid in the website URL: sperm 2 \u203a top \u203a "
                                  "New Sales Ad - Copy, sperm 2 \u203a NB6 \u203a New Sales Ad - Copy 3. Meta may add no click ID")
    # Clean links everywhere: green, and the ads without a website (a form, a call) don't count against it.
    for a in graph.ads["123"]:
        a["creative"]["object_story_spec"]["link_data"]["link"] = a["creative"]["object_story_spec"]["link_data"]["link"].split("?")[0]
    graph.ads["123"].append({"id": "4", "name": "Lead form", "effective_status": "ACTIVE", "adset": {"id": "s", "name": "L"},
                             "campaign": {"id": "C3", "name": "leads"}, "creative": {"url_tags": ""}})
    meta_ads.reset_links()
    c = run_checks()["ad_links"]
    assert c["status"] == "ok" and c["detail"] == ("All 4 live ads' links name their ad and leave Meta's click ID to Meta. "
                                                    "Left out: 1 placeholder link to google.com (account protection).")
    # Meta can't be read: a warning that says so, never a false all-clear from the last read.
    graph.denied["123"] = (403, {"error": {"message": "(#200) Missing ads_read permission", "code": 200}})
    meta_ads.reset_links()
    c = run_checks()["ad_links"]
    assert c["status"] == "warn" and "ads_read" in c["detail"]


def test_ad_links_count_ads_with_issues_only_inside_an_ad_set_and_campaign_that_are_on(wd, graph):
    live = graph_ad("1", "Live", "A", "https://getcoresupps.com/products/spermfuel", NAMING_TAGS)
    on = graph_ad("2", "Issues, on", "A", "https://getcoresupps.com/?fbclid=fbclid", NAMING_TAGS, status="WITH_ISSUES")
    old = graph_ad("3", "Issues, old campaign", "B", "http://google.com/", "", status="WITH_ISSUES")
    for a, adset, camp in ((on, "ACTIVE", "ACTIVE"), (old, "ACTIVE", "PAUSED")):
        a["adset"]["effective_status"], a["campaign"]["effective_status"] = adset, camp
    rejected = graph_ad("4", "Rejected", "A", "https://getcoresupps.com/?fbclid=fbclid", NAMING_TAGS, status="WITH_ISSUES")
    rejected["adset"]["effective_status"] = rejected["campaign"]["effective_status"] = "ACTIVE"
    rejected["issues_info"] = [{"level": "AD", "error_type": "HARD_ERROR", "error_summary": "Ad Review Rejected"}]
    graph.ads["123"] = [live, on, old, rejected]
    c = run_checks()["ad_links"]
    # The one with issues in a campaign that is on is read (its link is the fault); the old one and the one
    # Meta rejected (disabled, whatever its ad set does) are left out.
    assert c["status"] == "ok" and c["detail"].startswith("1 live ad with ?fbclid=fbclid in the website URL: sperm 2")
    assert "google" not in c["detail"]
    ids = {r["ad_id"] for r in meta_ads.cached_links()}
    assert ids == {"1", "2"}


def test_unnamed_sales_check_lists_meta_sales_that_name_no_ad(wd, monkeypatch):
    # Meta-credited sales that name their ad (or its ad set) are fine; one that names nothing is listed once the
    # tracker has had its chance to name it.
    assert run_checks()["unnamed_sales"]["detail"] == "No new sale credited to Meta in the last 2 days yet."
    bare = {"v": 6, "meta": True, "click": True, "ad_id": None, "ad_name": "", "adset_name": "", "campaign_name": "",
            "fbc": "fb.1.2.CLICK", "channel": "Meta ads"}
    db.set_order_attribution("201", bare)
    assert run_checks()["unnamed_sales"]["status"] == "ok"                 # just placed: still being named
    monkeypatch.setattr(watchdog, "UNNAMED_GRACE", 0)
    c = run_checks()["unnamed_sales"]
    assert c["status"] == "warn" and c["detail"].startswith("1 of 1 new sales credited to Meta in 2 days name no ad: #c201.")
    db.set_order_attribution("201", {**bare, "adset_name": "top", "campaign_name": "sperm 2", "ambiguous": True})
    c = run_checks()["unnamed_sales"]
    assert c["status"] == "ok" and c["detail"].startswith("All 1 new sales credited to Meta in 2 days name their ad")


def test_the_checks_name_a_meta_sale_that_names_no_ad_even_with_the_hub_closed(wd, monkeypatch):
    bare = {"v": 6, "meta": True, "source": "browser", "click": True, "ad_id": None, "ad_name": "", "adset_name": "",
            "campaign_name": "", "fbc": "fb.1.2.CLICK", "channel": "Meta ads"}
    db.set_order_attribution("201", bare)
    tried = []

    async def credit(order, rec):
        tried.append(str(order["id"]))
        return {"ad_id": "HS5", "adset_id": "S", "campaign_id": "C", "ad_name": "High Spender Static 5",
                "adset_name": "B1 Solution Aware LYST", "campaign_name": "sperm", "lp": "", "ids_stripped": False,
                "via": "", "identity_refreshed": "meta_credit"}
    monkeypatch.setattr(tracking, "name_from_meta_credit", credit)
    watchdog._name_tried.clear()
    run_checks()
    rec = db.orders_by_id(["201"])["201"]["attribution"]
    assert rec["ad_name"] == "High Spender Static 5" and rec["fbc"] == "fb.1.2.CLICK" and tried == ["201"]
    run_checks()
    assert tried == ["201"]                              # named: not tried again


def test_storage_check_warns_before_the_volume_is_full(wd, monkeypatch):
    import collections
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(watchdog.shutil, "disk_usage", lambda p: usage(1000 * 1_048_576, 800 * 1_048_576, 200 * 1_048_576))
    c = run_checks()["storage"]
    assert c["status"] == "warn" and "800 MB used of 1,000 MB (20% free)" in c["detail"] and "add space" in c["detail"]
    monkeypatch.setattr(watchdog.shutil, "disk_usage", lambda p: usage(1000 * 1_048_576, 950 * 1_048_576, 50 * 1_048_576))
    assert run_checks()["storage"]["status"] == "fail"
    monkeypatch.setattr(watchdog.shutil, "disk_usage", lambda p: usage(1000 * 1_048_576, 100 * 1_048_576, 900 * 1_048_576))
    c = run_checks()["storage"]
    assert c["status"] == "ok" and c["detail"].endswith("Volume: 100 MB used of 1,000 MB (90% free).")


@pytest.fixture
def wd(monkeypatch, graph):
    """A tracker in perfect health, with Shopify mocked at list_orders_since and
    _request. Each test breaks one link of the chain."""
    now = time.time()
    state = types.SimpleNamespace(listed=[], webhooks=[{"topic": "orders/create",
                                                        "address": f"{TRACKER_URL}/webhooks/shopify"}],
                                  list_error=None)

    async def list_orders_since(since):
        if state.list_error:
            raise state.list_error
        return state.listed

    async def request(method, path, **kw):
        return httpx.Response(200, json={"webhooks": state.webhooks},
                              request=httpx.Request(method, f"https://teststore.myshopify.com/{path}"))

    async def order_journey(order_id, timeout=8.0):
        if state.journey_error:
            raise state.journey_error
        return {"lastVisit": None, "firstVisit": None}

    # The P&L, as GET /api/orders sums it per order, and the re-syncs asked of it.
    state.pnl, state.resyncs = {"orders": {}, "covers_since": 0.0}, []

    async def order_revenue():
        if isinstance(state.pnl, Exception):
            raise state.pnl
        return state.pnl

    async def resync_shopify(since):
        state.resyncs.append(since)
        return "sent" if len(state.resyncs) == 1 else "recent"

    monkeypatch.setattr(pnl, "order_revenue", order_revenue)
    monkeypatch.setattr(pnl, "resync_shopify", resync_shopify)
    state.journey_error = None
    monkeypatch.setattr(shopify, "list_orders_since", list_orders_since)
    monkeypatch.setattr(shopify, "_request", request)
    monkeypatch.setattr(shopify, "order_journey", order_journey)
    monkeypatch.setattr(config, "PUBLIC_URL", TRACKER_URL)
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    db.upsert_session("b1", fbp="fb.1.1.1")
    db.kv_set("last_poll_ok", str(now - 30))
    sale = make_order(201, ts=now - 3600)
    rebill = make_order(202, ts=now - 3000, source_name="subscription_contract")
    state.listed += [sale, rebill]
    for o, kind, name in ((sale, "purchase", "Purchase"), (rebill, "renewal", "SubscriptionRenewal")):
        db.upsert_order(o)
        db.mark_order(str(o["id"]), "sent", kind=kind, fbtrace_id="t")
        db.record_event(name, f"x_{o['id']}", "webhook", "sent", {"user_data": GOOD_KEYS}, order_id=str(o["id"]))
    db.kv_set(f"emq:{MAIN}", json.dumps({"scores": {"Purchase": {"score": 8.4, "keys": {}}}, "taken_at": now}))
    return state


def run_checks():
    return {c["id"]: c for c in asyncio.run(watchdog.run_checks())}


def test_watchdog_healthy_state_is_all_ok(wd):
    checks = asyncio.run(watchdog.run_checks())
    assert {c["id"]: c["status"] for c in checks} == {cid: "ok" for cid in HEALTHY_CHECKS}
    assert watchdog.worst(checks) == "ok"
    by = {c["id"]: c for c in checks}
    assert by["orders"]["detail"].startswith("2 orders in 24 h: 2 sent to Meta")
    assert by["settings"]["detail"] == "Live: sending to Meta for real."
    assert by[f"emq:{MAIN}"]["detail"].startswith("Purchase scored 8.4/10")
    assert all(set(c) == {"id", "name", "status", "detail"} for c in checks)
    assert all(chr(0x2014) not in c["name"] + c["detail"] for c in checks)      # no em dashes for the owner


def _settled_sale(wd, oid, paid="59.95", hours=3, **over):
    """A sale old enough for the P&L to have read it, synced to the tracker."""
    o = make_order(oid, ts=time.time() - hours * 3600, subtotal_price=paid, **over)
    wd.listed.append(o)
    db.upsert_order(o)
    db.mark_order(str(oid), "sent", kind="purchase", fbtrace_id="t")
    return o


def test_watchdog_holds_the_pnls_revenue_up_to_shopify_order_by_order(wd):
    _settled_sale(wd, 301)
    _settled_sale(wd, 302, paid="92.65")
    wd.pnl["orders"] = {"#c301": 59.95, "#c302": 92.65}
    c = run_checks()["pnl_revenue"]
    assert c["status"] == "ok" and c["detail"] == "All 2 orders of the last day are in the P&L at exactly Shopify's amount."
    assert wd.resyncs == []
    # Oct 4 2026: a bundle's discount the P&L didn't read, so it counted the order at full price.
    wd.pnl["orders"]["#c302"] = 132.45
    c = run_checks()["pnl_revenue"]
    assert c["status"] == "fail"
    assert c["detail"].startswith("1 order(s) counted at a different amount than Shopify (over by $39.80): #c302 $132.45 vs $92.65")
    assert c["detail"].endswith("asked the P&L to re-read Shopify.")
    assert len(wd.resyncs) == 1                               # the P&L re-reads Shopify, which fixes the order
    assert "within the hour" in run_checks()["pnl_revenue"]["detail"]


def test_the_pnl_check_flags_a_missing_order_and_skips_what_the_pnl_leaves_out(wd):
    _settled_sale(wd, 311)
    # Too new for the P&L's hourly sync, refunded, test and cancelled orders aren't compared.
    _settled_sale(wd, 312, hours=1)
    _settled_sale(wd, 313, financial_status="refunded")
    _settled_sale(wd, 314, test=True)
    _settled_sale(wd, 315, cancelled_at=at(time.time() - 3600))
    c = run_checks()["pnl_revenue"]
    assert c["status"] == "fail" and "not in the P&L: #c311" in c["detail"] and "#c31" not in c["detail"].replace("#c311", "")
    wd.pnl["orders"]["#c311"] = 59.95
    assert run_checks()["pnl_revenue"]["status"] == "ok"
    # Orders older than what the P&L's listing reaches aren't judged.
    wd.pnl = {"orders": {}, "covers_since": time.time()}
    assert run_checks()["pnl_revenue"]["detail"] == "No order old enough to compare yet."


def test_the_pnl_check_warns_when_the_pnl_cant_be_read(wd):
    wd.pnl = pnl.PnlError("The P&L server did not answer (ConnectError).")
    c = run_checks()["pnl_revenue"]
    assert c["status"] == "warn" and "Couldn't read the P&L" in c["detail"]


def test_pnl_order_revenue_sums_the_pnls_lines_per_order(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        rows = [{"order_number": "#c2", "created_at": "2026-10-04T20:00:20-04:00", "line_revenue": 61.16,
                 "product_name": "SpermFuel+"},
                {"order_number": "#c1", "created_at": "2026-10-04T15:01:12-04:00", "line_revenue": 50.0},
                {"order_number": "#c1", "created_at": "2026-10-04T15:01:12-04:00", "line_revenue": 48.95}]
        return httpx.Response(200, json=rows)
    pnl.reset()
    monkeypatch.setattr(config, "PNL_URL", "https://pnl.example")
    monkeypatch.setattr(pnl, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    got = asyncio.run(pnl.order_revenue())
    assert got == {"orders": {"#c2": 61.16, "#c1": 98.95}, "covers_since": 0.0}
    assert seen[0].url.path == "/api/orders" and seen[0].url.params["limit"] == str(pnl.ORDERS_LIMIT)
    # At the listing's cap the oldest order may be cut off: it is left out and marks where coverage starts.
    pnl.reset()
    monkeypatch.setattr(pnl, "ORDERS_LIMIT", 3)
    got = asyncio.run(pnl.order_revenue())
    assert got["orders"] == {"#c2": 61.16} and got["covers_since"] == dt.datetime.fromisoformat(
        "2026-10-04T15:01:12-04:00").timestamp()


def test_watchdog_flags_a_failed_order(wd):
    o = make_order(203, ts=time.time() - 1800)
    wd.listed.append(o)
    db.upsert_order(o)
    db.mark_order("203", "failed", error="HTTP 400: Invalid parameter", kind="purchase")
    c = run_checks()["orders"]
    assert c["status"] == "fail" and c["detail"] == "failed to reach Meta (retrying): #c203"


def test_watchdog_flags_an_order_the_tracker_never_saw(wd):
    now = time.time()
    wd.listed += [make_order(204, ts=now - 1200),            # 20 min old and never picked up
                  make_order(205, ts=now - 300),             # 5 min old: the poller still has time
                  make_order(206, ts=now - 86400 - 600)]     # from before the tracker took over
    c = run_checks()["orders"]
    assert c["status"] == "fail" and c["detail"] == "not picked up: #c204"


def test_watchdog_flags_an_order_stuck_waiting(wd):
    o = make_order(207, ts=time.time() - 7200)
    wd.listed.append(o)
    db.upsert_order(o)
    db._c().execute("UPDATE orders SET received_at=? WHERE order_id='207'", (time.time() - 3600,))
    c = run_checks()["orders"]
    assert c["status"] == "fail" and c["detail"] == "waiting over 30 min: #c207"


def test_watchdog_warns_when_shopify_cant_be_read(wd):
    wd.list_error = RuntimeError("down")
    wd.webhooks = []
    by = run_checks()
    assert by["orders"]["status"] == "warn" and "RuntimeError" in by["orders"]["detail"]
    assert by["webhook"]["status"] == "warn" and "polling" in by["webhook"]["detail"]


def test_watchdog_catches_a_rebill_sent_as_purchase(wd, monkeypatch):
    monkeypatch.setattr(config, "EXTRA_PIXELS", [dict(BACKUP)])
    o = make_order(208, ts=time.time() - 3600, source_name="subscription_contract_checkout_one")
    wd.listed.append(o)
    db.upsert_order(o)
    db.mark_order("208", "sent", kind="renewal")
    for pid in (MAIN, BACKUP_ID):
        db.record_event("Purchase", "order_208", "webhook", "sent", {"user_data": GOOD_KEYS}, order_id="208",
                        pixel_id=pid)
    c = run_checks()["renewals"]
    assert c["status"] == "fail"
    assert c["detail"] == "Sent as Purchase by mistake: #c208"        # once, not once per pixel


@pytest.mark.parametrize("hours, status", [(1, "ok"), (3, "warn"), (7, "fail")])
def test_watchdog_notices_a_silent_pixel(wd, hours, status):
    db._c().execute("UPDATE sessions SET last_seen=?", (time.time() - hours * 3600,))
    c = run_checks()["pixel"]
    assert c["status"] == status and c["detail"].startswith("Last shopper activity")


def test_watchdog_pixel_never_seen_is_a_failure(wd):
    db._c().execute("DELETE FROM sessions")
    c = run_checks()["pixel"]
    assert c["status"] == "fail" and "never" in c["detail"]


def test_watchdog_flags_rejections_weak_details_and_low_match_quality(wd):
    for i in range(3):
        db.record_event("PageView", f"pv{i}", "pixel", "failed", {"user_data": {}}, error="HTTP 400")
    db.record_event("Purchase", "order_x", "webhook", "sent", {"user_data": {"ph": ["h"]}}, order_id="x")
    db.kv_set(f"emq:{MAIN}", json.dumps({"scores": {"Purchase": {"score": 4.2}}, "taken_at": time.time()}))
    db.kv_set("last_poll_ok", str(time.time() - 3600))
    by = run_checks()
    assert by[f"pixel:{MAIN}"]["status"] == "fail" and "3 of 6 events were rejected" in by[f"pixel:{MAIN}"]["detail"]
    assert by["details"]["status"] == "warn" and "Of 2 sales in 7 days: email 50%" in by["details"]["detail"]
    assert by[f"emq:{MAIN}"]["status"] == "fail"
    assert by["shopify"]["status"] == "fail"
    db.kv_set(f"emq:{MAIN}", json.dumps({"scores": {"Purchase": {"score": 6.1}}, "taken_at": time.time()}))
    assert run_checks()[f"emq:{MAIN}"]["status"] == "warn"


def test_watchdog_warns_when_ad_spend_cant_be_read(wd, graph, monkeypatch):
    graph.denied["123"] = (403, {"error": {"message": "(#200) Missing ads_read permission"}})
    c = run_checks()["ads"]
    assert c["status"] == "warn" and "ads_read" in c["detail"]
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", [])
    meta_ads._cache.clear()
    c = run_checks()["ads"]
    assert c["status"] == "warn" and c["detail"].startswith("Not connected yet")


def test_tick_records_every_run_and_alerts_once_when_it_breaks(wd, monkeypatch):
    alerts = []

    async def fake_alert(message, key):
        alerts.append((key, message))
    monkeypatch.setattr(worker, "alert", fake_alert)
    first = asyncio.run(watchdog.tick())
    assert first["status"] == "ok" and alerts == []
    runs = db.watchdog_runs(time.time() - 60)
    assert [r["status"] for r in runs] == ["ok"] and runs[0]["results"] == first["checks"]
    assert db.kv_get("watchdog_status") == "ok"
    db._c().execute("DELETE FROM sessions")                     # the storefront pixel goes quiet
    assert asyncio.run(watchdog.tick())["status"] == "fail"
    assert asyncio.run(watchdog.tick())["status"] == "fail"
    assert len(alerts) == 1 and alerts[0][0] == "watchdog" and "Storefront pixel" in alerts[0][1]
    assert [r["status"] for r in db.watchdog_runs(time.time() - 60)] == ["ok", "fail", "fail"]
    assert db.kv_get("watchdog_status") == "fail"


def test_tick_refreshes_match_quality_from_meta(wd, graph):
    graph.quality = {"web": [{"event_name": "Purchase", "event_match_quality": {
        "composite_score": 6.3, "match_key_feedback": [{"identifier": "email", "coverage": {"percentage": 97}}]}}]}
    watchdog._state["emq_at"] = 0.0
    by = {c["id"]: c for c in asyncio.run(watchdog.tick())["checks"]}
    assert db.kv_get(f"pixel_name:{MAIN}") == "Core Club"
    emq = by[f"emq:{MAIN}"]
    assert emq["status"] == "warn" and "6.3/10" in emq["detail"] and "Core Club (main)" in emq["name"]
    assert [r["score"] for r in db.emq_history(MAIN, "Purchase", 0)] == [6.3]
    asked = sum(1 for r in graph.requests if r.url.path.endswith("/dataset_quality"))
    asyncio.run(watchdog.tick())                                # refreshed every few hours, not every run
    assert sum(1 for r in graph.requests if r.url.path.endswith("/dataset_quality")) == asked


# =====================================================================================
# db.py: an existing volume from before the hub
# =====================================================================================

PRE_HUB_SCHEMA = """
CREATE TABLE sessions (
    client_id TEXT PRIMARY KEY, checkout_token TEXT, fbp TEXT, fbc TEXT, ip TEXT, user_agent TEXT,
    email TEXT, phone TEXT, first_name TEXT, last_name TEXT, landing_url TEXT,
    first_seen REAL NOT NULL, last_seen REAL NOT NULL);
CREATE INDEX idx_sessions_checkout ON sessions(checkout_token);
CREATE INDEX idx_sessions_fbp ON sessions(fbp);
CREATE INDEX idx_sessions_email ON sessions(email);
CREATE INDEX idx_sessions_seen ON sessions(last_seen);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_name TEXT NOT NULL, event_id TEXT NOT NULL,
    source TEXT NOT NULL, status TEXT NOT NULL, fbtrace_id TEXT, error TEXT, match_keys TEXT,
    order_id TEXT, payload TEXT NOT NULL, created_at REAL NOT NULL, pixel_id TEXT);
CREATE INDEX idx_events_created ON events(created_at);
CREATE INDEX idx_events_order ON events(order_id);
CREATE UNIQUE INDEX idx_events_pixel_dedup ON events(pixel_id, event_name, event_id, status);
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY, order_name TEXT, checkout_token TEXT, status TEXT NOT NULL, kind TEXT,
    attempts INTEGER NOT NULL DEFAULT 0, forced INTEGER NOT NULL DEFAULT 0, last_error TEXT,
    fbtrace_id TEXT, order_json TEXT NOT NULL, received_at REAL NOT NULL, sent_at REAL);
CREATE INDEX idx_orders_status ON orders(status);
CREATE TABLE meta_kv (key TEXT PRIMARY KEY, value TEXT);
"""


def test_a_pre_hub_database_migrates_and_keeps_its_rows(monkeypatch):
    now = time.time()
    path = os.path.join(tempfile.mkdtemp(), "live.db")
    old = sqlite3.connect(path)
    old.executescript(PRE_HUB_SCHEMA)
    old.execute("INSERT INTO sessions (client_id, fbp, email, ip, first_seen, last_seen) "
                "VALUES ('old-browser', 'fb.1.1.OLD', 'jane.doe@example.com', '203.0.113.9', 100, ?)", (now,))
    old.execute("INSERT INTO events (event_name, event_id, source, status, match_keys, order_id, payload, "
                "created_at, pixel_id) VALUES ('Purchase', 'order_9', 'webhook', 'sent', 'em,fbc', '9', '{}', ?, ?)",
                (now, MAIN))
    old.execute("INSERT INTO orders (order_id, order_name, status, kind, attempts, order_json, received_at, sent_at) "
                "VALUES ('9', '#c9', 'sent', 'purchase', 1, '{\"id\": 9}', ?, ?)", (now, now))
    old.execute("INSERT INTO meta_kv VALUES ('tracking_start', '1788220800.0')")
    old.commit()
    old.close()
    monkeypatch.setattr(db, "_conn", None)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init()

    def cols(table):
        return {r[1] for r in db._c().execute(f"PRAGMA table_info({table})")}
    assert {"ad_params", "ad_seen_at"} <= cols("sessions")
    assert "client_id" in cols("events") and "attribution" in cols("orders")
    tables = {r[0] for r in db._c().execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"watchdog_runs", "emq_snapshots"} <= tables
    assert "idx_events_pixel_time" in {r[1] for r in db._c().execute("PRAGMA index_list(events)")}

    # Every existing row is still there, unchanged.
    s = db.get_session("old-browser")
    assert s["email"] == "jane.doe@example.com" and s["first_seen"] == 100 and s["ad_params"] is None
    o = db.get_order("9")
    assert o["status"] == "sent" and o["kind"] == "purchase" and o["attempts"] == 1 and o["order_json"] == {"id": 9}
    assert db.event_already_sent("Purchase", "order_9") and db.kv_get("tracking_start") == "1788220800.0"
    assert db.orders_by_id(["9"])["9"]["attribution"] is None
    assert db.sent_order_events(["9"]) == {"9": {"pixels": {MAIN: now}, "match_keys": "em,fbc",
                                                 "event_name": "Purchase"}}

    # The hub's writes work on the migrated file.
    db.upsert_session("old-browser", ad_params=json.dumps({"ad_id": "AD1"}), ad_seen_at=now)
    s = db.get_session("old-browser")
    assert json.loads(s["ad_params"]) == {"ad_id": "AD1"} and s["ad_seen_at"] == now
    assert s["fbp"] == "fb.1.1.OLD" and s["email"] == "jane.doe@example.com" and s["first_seen"] == 100
    db.record_event("PageView", "pv1", "pixel", "sent", {"user_data": {}}, client_id="old-browser")
    funnel = db.storefront_funnel(now - 60)
    assert [(r["event_name"], r["client_id"], r["ad_params"]) for r in funnel] == [
        ("PageView", "old-browser", json.dumps({"ad_id": "AD1"}))]
    db.set_order_attribution("9", {"meta": True, "ad_id": "AD1"})
    assert db.orders_by_id(["9"])["9"]["attribution"] == {"meta": True, "ad_id": "AD1"}
    db.add_watchdog_run("ok", [{"id": "x", "status": "ok"}])
    assert db.watchdog_runs(now - 60)[0]["results"] == [{"id": "x", "status": "ok"}]
    db.add_emq_snapshot(MAIN, {"Purchase": 8.4})
    assert [r["score"] for r in db.emq_history(MAIN, "Purchase", now - 60)] == [8.4]

    # Booting again changes nothing.
    monkeypatch.setattr(db, "_conn", None)
    db.init()
    assert db.get_session("old-browser")["email"] == "jane.doe@example.com"
    assert db._c().execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


# =====================================================================================
# tracking.py: ad visits, attribution on the order, client_id on storefront events
# =====================================================================================

def collect(client, **payload):
    base = {"id": f"evt{time.time_ns()}-{next(_EVT_IDS)}", "ts": int(time.time() * 1000), "url": "https://getcoresupps.com/",
            "cid": "browser-1", "fbp": "fb.1.10.99"}
    base.update(payload)
    r = client.post("/collect", content=json.dumps(base),
                    headers={"Content-Type": "text/plain", "User-Agent": "Mozilla/5.0 Test"})
    assert r.status_code == 204, r.text
    return r


@pytest.fixture
def sends(client, monkeypatch):
    """The pixel sends /collect hands off, run on demand."""
    pending = []
    monkeypatch.setattr(tracking, "fire_and_forget", pending.append)

    def run():
        while pending:
            asyncio.run(pending.pop(0))
    return run


def test_ad_visit_is_remembered_and_the_sale_credited_to_it(client, sends, shop, meta):
    collect(client, name="page_viewed", url=AD_URL, fbc=f"fb.1.{int(time.time() * 1000)}.IwAR2abcDEFghiJKL")
    s = db.get_session("browser-1")
    assert json.loads(s["ad_params"]) == AD_PARAMS and "IwAR2" not in s["ad_params"]
    seen = s["ad_seen_at"]
    assert abs(seen - time.time()) < 5
    # Browsing on without ad parameters keeps the credit.
    collect(client, name="product_viewed", url="https://getcoresupps.com/products/spermfuel",
            custom={"items": [{"product_id": "111"}]})
    collect(client, name="checkout_started", url="https://getcoresupps.com/checkouts/cn/abc",
            checkout={"token": "chk_ad"})
    s = db.get_session("browser-1")
    assert json.loads(s["ad_params"]) == AD_PARAMS and s["ad_seen_at"] == seen
    sends()
    rows = [tuple(r) for r in db._c().execute("SELECT event_name, client_id, source FROM events ORDER BY id")]
    assert rows == [("PageView", "browser-1", "pixel"), ("ViewContent", "browser-1", "pixel"),
                    ("InitiateCheckout", "browser-1", "pixel")]

    # The order arrives: its Purchase is credited to the ad this browser came from,
    # not to whatever Shopify's landing page says. That page is the buyer's first
    # visit: its ad is kept as the first touch and listed as an assist.
    o = make_order(301, ts=time.time(), checkout_token="chk_ad",
                   landing_site="/?utm_source=facebook&utm_content=Some%20other%20ad")
    shop.orders = [o]
    db.upsert_order(o)
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    # Utm tags with no ad id and no click id: a link shared by hand, not the old listicle.
    first = {"ad_id": None, "adset_id": None, "campaign_id": None, "ad_name": "Some other ad", "adset_name": "",
             "campaign_name": "", "ambiguous": True, "at": None, "lp": "", "ids_stripped": False,
             "from": "landing_site"}
    assert db.orders_by_id(["301"])["301"]["attribution"] == {
        "v": attribution.RESOLVER_VERSION, "meta": True, "source": "browser", "click": True, "ad_id": "AD1",
        "adset_id": "AS1", "campaign_id": "C1", "ad_name": "B2 Statics - Ad 3", "adset_name": "Broad",
        "campaign_name": "Leggings CBO", "ambiguous": False, "click_at": seen, "lp": "", "ids_stripped": False,
        "via": "", "channel": "Meta ads", "first_touch": first,
        "assists": [{"ad_id": "", "ad_name": "Some other ad", "adset_name": "", "campaign_name": "", "at": None,
                     "first_touch": True}],
        "fbc": db.get_session("browser-1")["fbc"]}                  # the click Meta was sent
    purchase = db.events_for_order("301")[0]
    assert purchase["event_name"] == "Purchase" and purchase["client_id"] is None     # order events have no browser
    # Meta got the same click the record names, stamped when it arrived.
    assert purchase["payload"]["user_data"]["fbc"] == db.get_session("browser-1")["fbc"]
    assert attribution.click_time(purchase["payload"]["user_data"]["fbc"]) == pytest.approx(seen, abs=0.002)
    row = client.get("/hub/api/orders?range=today", headers=API).json()["orders"][0]
    assert row["ad"] == {"click": True, "ad_name": "B2 Statics - Ad 3", "adset_name": "Broad",
                         "campaign_name": "Leggings CBO", "ad_id": "AD1", "source": "browser",
                         "assists": [{"ad_name": "Some other ad", "adset_name": "", "campaign_name": ""}]}
    assert row["channel"] == "Meta ads"


def test_visits_that_are_not_from_meta_leave_no_ad_credit(client, sends):
    collect(client, name="page_viewed", url="https://getcoresupps.com/?utm_source=google&utm_campaign=brand",
            cid="", fbp="fb.1.1.GOOGLE")
    s = db.get_session("fb.1.1.GOOGLE")                 # no Shopify clientId: keyed on the fbp cookie
    assert s["ad_params"] is None and s["ad_seen_at"] is None
    collect(client, name="checkout_completed", cid="", fbp="fb.1.1.GOOGLE")    # enrichment only, nothing sent
    sends()
    assert [tuple(r) for r in db._c().execute("SELECT event_name, client_id FROM events")] == [
        ("PageView", "fb.1.1.GOOGLE")]


def test_sales_without_a_browser_are_credited_from_the_landing_page(meta, monkeypatch):
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 0)
    landing = "/products/x?utm_source=fb&utm_content=B2%20Statics%20-%20Ad%207&fbclid=IwAR2abcDEFghiJKL"
    db.upsert_order(make_order(302, checkout_token="nobody", landing_site=landing))
    db.upsert_order(make_order(303, checkout_token="nobody2", landing_site=landing,
                               source_name="subscription_contract_checkout_one"))
    assert asyncio.run(tracking.process_pending()) == {"sent": 2}
    got = db.orders_by_id(["302", "303"])
    credit = got["302"]["attribution"]
    # Nothing else known: the first landing page sells, marked as unverified (its time is unknown).
    assert credit["source"] == "first_visit_unverified" and credit["ad_name"] == "B2 Statics - Ad 7"
    assert credit["click"] is True
    assert got["303"]["attribution"] is None            # a rebill is never credited to an ad
    # Changed on purpose (F6): no browser was seen at the new sale's checkout, so an
    # InitiateCheckout goes right before its Purchase; never for MRR.
    assert [n for _, names in meta.sent for n in names] == ["InitiateCheckout", "Purchase", "SubscriptionRenewal"]


def test_a_returning_customer_with_an_old_ad_cookie_is_not_a_meta_sale(client, sends, shop, meta):
    old_click = f"fb.1.{int((time.time() - 20 * 86400) * 1000)}.IwAR2old"
    collect(client, name="checkout_started", url="https://getcoresupps.com/checkouts/cn/r", fbc=old_click,
            checkout={"token": "chk_back"})
    sends()                             # the checkout event is recorded, so the funnel sees this browser
    o = make_order(304, ts=time.time(), checkout_token="chk_back", landing_site="/?utm_source=klaviyo")
    shop.orders = [o]
    db.upsert_order(o)
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    credit = db.orders_by_id(["304"])["304"]["attribution"]
    assert (credit["meta"], credit["source"], credit["click"], credit["ad_id"]) == (False, "", False, None)
    assert credit["channel"] == "Email or SMS" and credit["assists"] == [] and credit["first_touch"] is None
    # Meta still gets the fbc, with its real time, and applies its own attribution rules.
    assert db.events_for_order("304")[0]["payload"]["user_data"]["fbc"] == old_click
    funnel = client.get("/hub/api/funnel?range=today", headers=API).json()
    assert funnel["meta"][4] == 0 and funnel["other"][4] == 1
    assert client.get("/hub/api/creatives?range=today", headers=API).json()["url_tracking"]["meta_orders"] == 0


# =====================================================================================
# hub.py
# =====================================================================================

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
GET_APIS = ("pnl", "overview", "orders", "creatives", "assists", "funnel", "watchdog")
POST_APIS = ("watchdog/run", "resend/104", "test-event")


def exposed(resp) -> list[str]:
    """Secrets or customer details found anywhere in a response body."""
    is_json = "json" in resp.headers.get("content-type", "")
    text = json.dumps(resp.json(), ensure_ascii=False) if is_json else resp.text
    return [s for s in PII + SECRETS if s in text]


def test_login_cookie_opens_the_hub_and_nothing_else(client):
    r = client.post("/hub/login", content="token=wrong", headers=FORM, follow_redirects=False)
    assert r.status_code == 401 and "set-cookie" not in r.headers
    r = client.post("/hub/login", content="token=" + "x" * 5000, headers=FORM, follow_redirects=False)
    assert r.status_code == 401                          # oversized: refused unread
    r = client.post("/hub/login", content=f"token={ADMIN}", headers=FORM, follow_redirects=False)
    assert r.status_code == 303
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "path=/hub" in cookie and "samesite=strict" in cookie
    assert "secure" not in cookie                        # plain http in local development
    assert ADMIN not in r.headers["set-cookie"]
    client.cookies.clear()
    session = {"Cookie": f"hub_session={hub._session_value()}"}
    assert client.get("/hub/api/overview", headers=session).status_code == 200
    # The session cookie is not the admin token: /report and /mcp stay closed to it.
    assert client.get("/report", headers=session).status_code == 401
    assert client.post("/mcp", json={}, headers=session).status_code == 401
    assert client.get("/hub/login", follow_redirects=False).headers["location"] == "/hub"


def test_every_api_needs_auth_and_every_post_the_hub_header(client, shop, meta):
    seed(shop)
    for path in GET_APIS:
        assert client.get(f"/hub/api/{path}").status_code == 401, path
        assert client.get(f"/hub/api/{path}", headers={"Authorization": f"Bearer {ADMIN}x"}).status_code == 401
        assert client.get(f"/hub/api/{path}", headers={"Authorization": ADMIN}).status_code == 401
        assert client.get(f"/hub/api/{path}?key={ADMIN}").status_code == 401     # the MCP ?key= form is not a login
        r = client.get(f"/hub/api/{path}", headers=API)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store", path
    for path in POST_APIS:
        assert client.post(f"/hub/api/{path}").status_code == 401, path
        assert client.post(f"/hub/api/{path}", headers=API).status_code == 403, path
        assert client.post(f"/hub/api/{path}", headers={**API, "X-Hub-Request": "0"}).status_code == 403, path
    assert meta.sent == [] and db.get_order("104") is None            # nothing was sent or queued


def test_overview_splits_new_sales_from_rebills_and_computes_true_roas(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    shop.orders = [make_order(401, total_price="50.00"), make_order(402, total_price="30.00"),
                   make_order(403, total_price="20.00", source_name="subscription_contract"),
                   make_order(404, total_price="25.00", source_name="subscription_contract_checkout_one"),
                   make_order(405, total_price="99.00", test=True),
                   make_order(406, total_price="80.00", financial_status="voided"),
                   make_order(407, total_price="45.00", source_name="shopify_draft_order")]
    # The campaign's name ties it to the SpermFuel+ the orders are for (no sale was credited to it).
    meta.ad_rows = [insight("AD1", "B2 Statics - Ad 3", 30, purchases=3, value=90, campaign_name="SpermFuel CBO"),
                    insight("AD2", "B2 Statics - Ad 4", 10, campaign_name="SpermFuel CBO")]
    today = hub._today()
    meta.daily = [{"date_start": today.isoformat(), "spend": "40"},
                  {"date_start": (today - dt.timedelta(days=2)).isoformat(), "spend": "12.5"}]
    meta.campaign_daily = [{"date_start": today.isoformat(), "campaign_id": "C1", "campaign_name": "SpermFuel CBO",
                            "spend": "40"}]
    o = client.get("/hub/api/overview?range=today", headers=API).json()
    c = o["cards"]
    assert c["new_sales"] == {"count": 2, "revenue": 80.0}
    assert c["rebills"] == {"count": 2, "revenue": 45.0} and c["mrr"] == {"count": 2, "revenue": 45.0}
    assert c["total_revenue"] == 125.0 and c["orders"] == 4 and c["currency"] == "USD"
    assert c["aov"] == 40.0 and c["spend"] == 40.0
    # MRR is not an ad return: Product ROAS and cost per sale use new sales of advertised products only.
    assert c["advertised"] == {"count": 2, "revenue": 80.0, "products": [
        {"product_id": "111", "title": "SpermFuel+", "campaigns": ["SpermFuel CBO"]}]}
    assert c["product_roas"] == c["true_roas"] == 2.0 and c["cost_per_sale"] == 20.0
    assert c["ad_roas"] == 0.0 and c["unmapped_campaigns"] == []        # no sale was tied to an ad click
    assert c["meta_roas"] == 2.25 and c["meta_purchases"] == 3
    assert c["ads_connected"] is True and c["ads_error"] == ""
    s = o["series"]
    assert s["spend"] == [0.0, 0.0, 0.0, 0.0, 12.5, 0.0, 40.0]
    assert s["new_revenue"][-1] == 80.0 and s["rebill_revenue"][-1] == 45.0
    assert s["new_sales"][-1] == 2 and s["rebills"][-1] == 2
    assert s["advertised_revenue"][-1] == 80.0 and s["advertised_sales"][-1] == 2
    # A day with spend and no sales: ROAS 0, no cost per sale to show.
    y = client.get("/hub/api/overview?range=yesterday", headers=API).json()["cards"]
    assert y["new_sales"]["count"] == 0 and y["true_roas"] == 0.0 and y["cost_per_sale"] is None and y["aov"] is None


def test_one_failing_ad_account_hides_roas_instead_of_overstating_it(client, shop, meta, monkeypatch):
    seed(shop)
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123", "456"])
    meta.ad_rows = ad_rows()
    meta.denied = {"456"}
    cards = client.get("/hub/api/overview?range=today", headers=API).json()["cards"]
    assert cards["ads_connected"] is False and "act_456" in cards["ads_error"]
    assert cards["spend"] is None and cards["true_roas"] is None and cards["cost_per_sale"] is None
    body = client.get("/hub/api/creatives?range=today", headers=API).json()
    assert body["connected"] is False and "act_456" in body["error"]
    # The ads that were read still show, but the total can't claim a ROAS on half the spend.
    assert body["totals"]["true_roas"] is None and body["totals"]["meta_roas"] is None
    ads = {a["ad_id"]: a for c in body["campaigns"] for g in c["groups"] for a in g["ads"]}
    assert ads["AD1"]["spend"] == 40 and ads["AD1"]["orders"] == ["#c101"]


@pytest.mark.parametrize("zone", ["America/New_York", "Asia/Tokyo"])
def test_orders_land_on_the_store_day_not_the_utc_day(client, shop, monkeypatch, zone):
    monkeypatch.setattr(config, "STORE_TIMEZONE", zone)
    tz = config.store_tz()
    today = dt.datetime.now(tz).date()

    def local(days_back, hour, minute):
        return dt.datetime.combine(today - dt.timedelta(days=days_back), dt.time(hour, minute), tzinfo=tz)
    late, early, before = local(1, 23, 30), local(1, 0, 30), local(2, 23, 30)
    shop.orders = [make_order(501, created_at=late.isoformat(), total_price="10.00"),
                   make_order(502, created_at=early.isoformat(), total_price="20.00"),
                   make_order(503, created_at=before.isoformat(), total_price="40.00")]
    y = client.get("/hub/api/overview?range=yesterday", headers=API).json()
    assert y["store"]["timezone"] == zone and y["range"]["since"] == (today - dt.timedelta(days=1)).isoformat()
    assert y["cards"]["new_sales"] == {"count": 2, "revenue": 30.0}
    assert y["series"]["new_revenue"][-3:] == [40.0, 30.0, 0.0]
    assert client.get("/hub/api/overview?range=today", headers=API).json()["cards"]["new_sales"]["count"] == 0
    rows = {r["id"]: r for r in client.get("/hub/api/orders?range=yesterday", headers=API).json()["orders"]}
    assert set(rows) == {"501", "502"}
    assert rows["501"]["time_local"] == f"{late:%b} {late.day}, 11:30 PM"
    assert rows["501"]["created_at"] == late.isoformat()
    assert rows["502"]["time_local"] == f"{early:%b} {early.day}, 12:30 AM"


def test_an_unknown_store_timezone_falls_back_to_utc(monkeypatch):
    monkeypatch.setattr(config, "STORE_TIMEZONE", "Mars/Olympus_Mons")
    assert config.store_tz() is dt.timezone.utc
    assert dt.datetime.fromtimestamp(hub._range("today")["start"], dt.timezone.utc).hour == 0


def credited(oid, total, **credit):
    """A sale the tracker sent and credited to a Meta ad."""
    o = make_order(oid, total_price=total)
    db.upsert_order(o)
    db.mark_order(str(oid), "sent", kind="purchase")
    db.set_order_attribution(str(oid), {"meta": True, "source": "browser", "click": True, **credit})
    return o


def test_creatives_join_store_sales_by_ad_id_then_by_ad_name(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    monkeypatch.setattr(config, "HUB_MIN_AD_SPEND", 0)    # every ad its own row (the $15 line has its own test)
    meta.ad_rows = [insight("AD1", "UGC Sarah - Ad 1", 50),
                    insight("AD2", "Founder story", 30),
                    insight("AD3", "Founder story", 20, adset_id="AS2", adset_name="Interests")]
    shop.orders = [
        # The ad id wins over a stale name in the link (the ad was renamed since).
        credited(601, "40.00", ad_id="AD1", ad_name="Old name", adset_name="Broad", campaign_name="Leggings CBO"),
        # No id: matched by name, ignoring case, in the ad set the link named.
        credited(602, "30.00", ad_name="founder story", adset_name="interests"),
        # Name only: the first ad with that name.
        credited(603, "25.00", ad_name="Founder Story "),
        # Meta reported no delivery for this ad in the range; it still shows, under its campaign.
        credited(604, "20.00", ad_id="AD99", ad_name="Paused winner", adset_name="Broad",
                 campaign_name="Leggings CBO"),
        # A Meta click that carried no ad parameters.
        credited(605, "15.00"),
        # Organic, and a rebill whose first order came from an ad: neither is credited.
        make_order(606, total_price="10.00"),
        make_order(607, total_price="39.00", source_name="subscription_contract",
                   landing_site="/?utm_source=facebook&ad_id=AD1"),
    ]
    body = client.get("/hub/api/creatives?range=today", headers=API).json()
    assert body["connected"] is True and body["error"] == "" and len(body["campaigns"]) == 1
    camp = body["campaigns"][0]
    assert camp["campaign_id"] == "C1" and camp["spend"] == 100 and camp["store_sales"] == 4
    assert camp["store_revenue"] == 115.0
    groups = {g["name"]: g for g in camp["groups"]}
    assert [g["name"] for g in camp["groups"]] == ["Broad", "Interests"]
    broad = {a["ad_id"]: a for a in groups["Broad"]["ads"]}
    assert [a["ad_id"] for a in groups["Broad"]["ads"]] == ["AD1", "AD2", "AD99"]
    assert broad["AD1"]["ad_name"] == "UGC Sarah - Ad 1" and broad["AD1"]["orders"] == ["#c601"]
    assert broad["AD1"]["roas_store"] == 0.8
    assert broad["AD2"]["orders"] == ["#c603"]
    assert broad["AD99"]["ad_name"] == "Paused winner" and broad["AD99"]["spend"] == 0
    assert broad["AD99"]["roas_store"] is None and broad["AD99"]["store_revenue"] == 20.0
    assert groups["Interests"]["ads"][0]["ad_id"] == "AD3" and groups["Interests"]["ads"][0]["orders"] == ["#c602"]
    assert body["unlabelled"] == {"store_sales": 1, "store_revenue": 15.0, "orders": ["#c605"]}
    assert body["url_tracking"] == {"tagged_orders": 4, "meta_orders": 5}
    t = body["totals"]
    assert t["spend"] == 100 and t["store_sales"] == 5 and t["store_revenue"] == 130.0
    assert t["true_roas"] == 1.4                          # every new sale, 140.00, over spend
    batch = client.get("/hub/api/creatives?range=today&group=batch", headers=API).json()
    assert {g["name"] for g in batch["campaigns"][0]["groups"]} == {"UGC Sarah", "Founder story", "Paused winner"}


def test_funnel_counts_each_browser_once_and_the_main_pixel_only(client, shop, monkeypatch):
    monkeypatch.setattr(config, "EXTRA_PIXELS", [dict(BACKUP)])
    now = time.time()
    db.upsert_session("b-ad", ad_params=json.dumps({"ad_id": "AD1"}), ad_seen_at=now - 60)
    db.upsert_session("b-org", fbp="fb.1.1.2")

    def event(cid, name, n, pixel=MAIN, source="pixel", ago=0):
        eid = f"{cid}-{name}-{n}"
        db.record_event(name, eid, source, "sent", {"user_data": {}}, client_id=cid, pixel_id=pixel)
        if ago:
            db._c().execute("UPDATE events SET created_at=? WHERE event_id=?", (now - ago, eid))
    for pid in (MAIN, BACKUP_ID):                         # every event also went to the backup pixel
        event("b-ad", "PageView", 1, pid)
        event("b-ad", "PageView", 2, pid)
        event("b-ad", "InitiateCheckout", 1, pid)
        event("b-org", "PageView", 1, pid)
        event("b-org", "AddToCart", 1, pid)
    event("b-org", "Search", 1)                           # not a funnel step
    event("b-late", "PageView", 1, ago=3 * 86400)         # before today
    event("b-test", "PageView", 1, source="test")         # the Test Events button, not a shopper
    body = client.get("/hub/api/funnel?range=today", headers=API).json()
    # At the furthest step each reached: b-ad a checkout (its product view wasn't recorded), b-org a cart.
    assert body["meta"] == [1, 1, 1, 1, 0] and body["other"] == [1, 1, 1, 0, 0]


def test_funnel_judges_each_visit_by_the_ad_click_before_it(client, shop):
    now = time.time()
    # Arrived from an ad 20 days ago and browsed right then.
    db.upsert_session("b-old-ad", ad_params=json.dumps({"ad_id": "AD1"}), ad_seen_at=now - 20 * 86400 - 60)
    # An organic shopper, also 20 days ago.
    db.upsert_session("b-old-organic", fbp="fb.1.1.3")
    # Clicked an ad 20 days ago and came back on their own today.
    db.upsert_session("b-returning", fbc=f"fb.1.{int((now - 20 * 86400) * 1000)}.OLDCLICK")
    for cid, ago in (("b-old-ad", 20 * 86400), ("b-old-organic", 20 * 86400), ("b-returning", 60)):
        db.record_event("PageView", f"{cid}-pv", "pixel", "sent", {"user_data": {}}, client_id=cid)
        db._c().execute("UPDATE events SET created_at=? WHERE event_id=?", (now - ago, f"{cid}-pv"))
    body = client.get("/hub/api/funnel?range=30d", headers=API).json()
    assert body["meta"][0] == 1 and body["other"][0] == 2


def test_orders_feed_ticks_each_pixel_and_shows_no_customer_details(client, shop, monkeypatch):
    monkeypatch.setattr(config, "EXTRA_PIXELS", [dict(BACKUP)])
    db.kv_set(f"pixel_name:{MAIN}", "Core Club")
    db.kv_set(f"pixel_name:{BACKUP_ID}", "Leggings backup")
    both, main_only, unseen = make_order(701), make_order(702), make_order(703)
    shop.orders = [both, main_only, unseen]
    for o in (both, main_only):
        db.upsert_order(o)
        db.mark_order(str(o["id"]), "sent", kind="purchase")
    rich = {"em": ["h"], "ph": ["h"], "fbp": "fb.1.1.P", "client_user_agent": "Mozilla/5.0 iPhone"}
    db.record_event("Purchase", "order_701", "webhook", "sent", {"user_data": rich}, order_id="701", pixel_id=MAIN)
    db.record_event("Purchase", "order_701", "webhook", "sent", {"user_data": {"em": ["h"]}}, order_id="701",
                    pixel_id=BACKUP_ID)
    db.record_event("Purchase", "order_702", "webhook", "sent", {"user_data": rich}, order_id="702", pixel_id=MAIN)
    db.record_event("Purchase", "order_702", "webhook", "failed", {"user_data": rich}, order_id="702",
                    pixel_id=BACKUP_ID, error="HTTP 400: Invalid OAuth access token")
    db.mark_order("702", "failed", error=f"[pixel {BACKUP_ID}] HTTP 400: Invalid OAuth access token", kind="purchase")
    r = client.get("/hub/api/orders?range=today", headers=API)
    rows = {row["id"]: row for row in r.json()["orders"]}

    def ticks(oid):
        return [(p["name"], p["role"], p["sent"]) for p in rows[oid]["pixels"]]
    assert ticks("701") == [("Core Club", "main", True), ("Leggings backup", "backup", True)]
    assert ticks("702") == [("Core Club", "main", True), ("Leggings backup", "backup", False)]
    assert ticks("703") == [("Core Club", "main", False), ("Leggings backup", "backup", False)]
    assert rows["702"]["tracker_status"] == "failed" and "Invalid OAuth" in rows["702"]["error"]
    assert rows["703"]["tracker_status"] == "not_seen" and rows["703"]["error"] is None
    assert rows["701"]["details"] == {"email": True, "phone": True, "ip": False, "browser": True,
                                      "ad_click_id": False, "browser_id": True}
    # Built field by field: nothing from the raw Shopify order rides along.
    assert set(rows["701"]) == {"id", "name", "created_at", "time_local", "total", "currency", "items", "type",
                                "type_label", "tracker_status", "error", "pixels", "ad", "channel", "listicle",
                                "landing", "quiz_assist", "details", "can_resend", "subscription"}
    assert exposed(r) == []


def test_no_hub_response_carries_secrets_or_customer_details(client, shop, meta, monkeypatch):
    seed(shop)
    monkeypatch.setattr(config, "EXTRA_PIXELS", [dict(BACKUP)])
    monkeypatch.setattr(config, "META_ADS_TOKEN", "ads-secret")
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    meta.ad_rows = ad_rows()
    db.upsert_session("b1", email="jane.doe@example.com", phone="(647) 555-0199", ip="203.0.113.9",
                      first_name="Jané", ad_params=json.dumps({"ad_id": "AD1"}), ad_seen_at=time.time())
    db.record_event("PageView", "pv1", "pixel", "sent", {"user_data": {"client_ip_address": "203.0.113.9"}},
                    client_id="b1")
    responses = [client.get("/hub", headers=API), client.get("/hub")]
    responses += [client.get(f"/hub/api/{p}?range=7d", headers=API) for p in GET_APIS]
    responses += [client.post("/hub/api/watchdog/run", headers=POST),
                  client.post("/hub/api/resend/101", headers=POST),
                  client.post("/hub/api/test-event", headers=POST,
                              json={"test_event_code": "TEST1", "pixel_id": BACKUP_ID})]
    for r in responses:
        assert r.status_code == 200 and exposed(r) == [], (r.url, exposed(r))
    assert responses[-1].json()["ok"] is True and meta.sent[-1] == (BACKUP_ID, ["PageView"])


def test_overview_status_reuses_a_fresh_verdict_and_rechecks_a_stale_one(client, shop):
    checks = [{"id": "orders", "name": "Every order accounted for", "status": "warn", "detail": "Couldn't list."},
              {"id": "pixel", "name": "Storefront pixel", "status": "fail", "detail": "Never reported."},
              {"id": "storage", "name": "Storage", "status": "ok", "detail": "Saved.", "extra": "dropped"}]
    db.add_watchdog_run("fail", checks)
    st = client.get("/hub/api/overview", headers=API).json()["status"]
    assert st["level"] == "fail" and st["headline"] == "Something is broken"
    assert st["reasons"] == ["Never reported.", "Couldn't list."]           # worst first
    assert st["checks"][2] == {"id": "storage", "name": "Storage", "status": "ok", "detail": "Saved."}
    assert len(st["timeline"]) == 1 and st["checked_ago"] == "just now"
    db._c().execute("UPDATE watchdog_runs SET run_at=?", (time.time() - 1200,))
    hub._orders_cache.clear()
    st = client.get("/hub/api/overview", headers=API).json()["status"]
    assert len(st["timeline"]) == 2 and st["checked_ago"] == "just now"   # re-checked live
    assert any(c["id"] == "orders" for c in st["checks"]) and len(db.watchdog_runs(time.time() - 60)) == 1


def test_overview_shows_match_quality_per_pixel(client, shop, monkeypatch):
    monkeypatch.setattr(config, "EXTRA_PIXELS", [dict(BACKUP)])
    db.kv_set(f"pixel_name:{MAIN}", "Core Club")
    db.kv_set(f"emq:{MAIN}", json.dumps({"taken_at": time.time(), "scores": {
        "Purchase": {"score": 8.1, "keys": {"email": 98.46, "phone": 61.2, "": 5, "fbc": None}},
        "PageView": {"score": 6.2, "keys": {}}}}))
    db.add_emq_snapshot(MAIN, {"Purchase": 7.9, "PageView": 6.0})
    db.kv_set(f"emq:{BACKUP_ID}", json.dumps({"taken_at": time.time(), "scores": {
        "Purchase": {"score": None}, "PageView": {"score": 5.5, "keys": {"ip": 99}}}}))
    q = client.get("/hub/api/overview", headers=API).json()["quality"]
    main, backup = q
    # Meta's own name when read, else the owner's name for the id (not "Backup pixel" any more).
    assert (main["name"], main["role"], backup["name"], backup["role"]) == ("Core Club", "main", "Eczema", "backup")
    assert main["emq"]["event"] == "Purchase" and main["emq"]["score"] == 8.1
    assert main["meta_keys"] == {"email": 98, "phone": 61}
    assert [h["score"] for h in main["emq_history"]] == [7.9]
    # Meta hasn't scored the backup's purchases yet: show its best-scored event instead.
    assert backup["emq"] == {"event": "PageView", "score": 5.5, "taken_at": backup["emq"]["taken_at"]}
    assert backup["meta_keys"] == {"ip": 99} and backup["last_sent_ago"] == "never"


def test_resending_a_skipped_order_says_why_in_plain_words(client, shop, meta):
    seed(shop)
    body = client.post("/hub/api/resend/107", headers=POST).json()           # a test order
    assert body["ok"] is False and body["status"] == "skipped" and body["kind"] == "test"
    assert body["message"] == "Not sent: it is a test order." and meta.sent == []
    body = client.post("/hub/api/resend/105", headers=POST).json()           # the failed rebill
    assert body["ok"] is True and body["kind"] == "renewal" and body["was_sent_before"] is False
    assert meta.sent == [(MAIN, ["SubscriptionRenewal"])]
    for text in (body["message"], body["note"]):
        assert chr(0x2014) not in text


# =====================================================================================
# Regressions from the hub review
# =====================================================================================

def test_shopify_down_leaves_sales_unknown_not_zero(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    meta.ad_rows = [insight("AD1", "B2 Statics - Ad 3", 40, purchases=3, value=90)]
    meta.daily = [{"date_start": hub._today().isoformat(), "spend": "40"}]
    shop.fail = 403
    o = client.get("/hub/api/overview?range=today", headers=API).json()
    assert "Shopify answered 403" in o["error"]
    c = o["cards"]
    # Not 0 sales and a 0.00x True ROAS: the page shows "-" next to the error note.
    assert c["new_sales"] == {"count": None, "revenue": None} and c["rebills"] == {"count": None, "revenue": None}
    assert c["true_roas"] is None and c["cost_per_sale"] is None and c["aov"] is None
    assert c["total_revenue"] is None and c["orders"] is None
    # What Meta said still shows.
    assert c["spend"] == 40.0 and c["meta_roas"] == 2.25 and c["ads_connected"] is True
    s = o["series"]
    for k in ("new_revenue", "rebill_revenue", "new_sales", "rebills"):
        assert s[k] == [None] * 7, k
    assert s["spend"][-1] == 40.0
    f = client.get("/hub/api/funnel?range=today", headers=API).json()
    assert "Shopify answered 403" in f["error"] and f["meta"][4] is None and f["other"][4] is None
    cr = client.get("/hub/api/creatives?range=today", headers=API).json()
    assert cr["totals"]["true_roas"] is None and cr["totals"]["spend"] == 40


def _sale(oid, revenue, **credit):
    return {"type": "new_sale", "id": str(oid), "order": {"name": f"#c{oid}"}, "revenue": revenue,
            "credit": {"meta": True, **credit}}


def test_a_sale_for_an_unreported_ad_id_is_not_merged_into_a_same_named_ad():
    # The original ad (111) is paused; its duplicate (222) has the same name and is still spending.
    rows = [{"campaign_id": "C1", "campaign_name": "Leggings CBO", "adset_id": "AS9", "adset_name": "Broad - Copy",
             "ad_id": "222", "ad_name": "UGC 1", "spend": 50.0}]
    facts = [_sale(1, 60.0, ad_id="111", ad_name="UGC 1", adset_name="Broad", campaign_id="C1",
                   campaign_name="Leggings CBO")]
    built = hub.build_creatives(facts, rows, "adset")
    ads = {a["ad_id"]: (g["name"], a) for c in built["campaigns"] for g in c["groups"] for a in g["ads"]}
    assert "222" not in ads                             # no sale: no row, its $50 sits in the line
    (camp,) = built["campaigns"]
    assert camp["small"]["count"] == 1 and camp["small"]["spend"] == 50.0
    group, paused = ads["111"]
    assert group == "Broad" and paused["store_sales"] == 1 and paused["spend"] == 0
    assert paused["orders"] == ["#c1"] and paused["ad_name"] == "UGC 1"
    # A link without an id still matches by name.
    built = hub.build_creatives([_sale(2, 60.0, ad_name="ugc 1")], rows, "adset")
    ads = {a["ad_id"]: a for c in built["campaigns"] for g in c["groups"] for a in g["ads"]}
    assert set(ads) == {"222"} and ads["222"]["store_sales"] == 1


def test_funnel_judges_a_returning_browser_once_across_its_steps(client, shop):
    now = time.time()
    # Came from an ad 19 days ago and looked at a product then; came back on its own an hour ago.
    db.upsert_session("b-back", ad_params=json.dumps({"ad_id": "AD1"}), ad_seen_at=now - 19 * 86400)
    for eid, name, ago in (("pv-old", "PageView", 19 * 86400 - 60), ("vc-old", "ViewContent", 19 * 86400 - 30),
                           ("pv-new", "PageView", 3600)):
        db.record_event(name, eid, "pixel", "sent", {"user_data": {}}, client_id="b-back")
        db._c().execute("UPDATE events SET created_at=? WHERE event_id=?", (now - ago, eid))
    body = client.get("/hub/api/funnel?range=30d", headers=API).json()
    # One shopper, one group: never a product view in "Meta ads" with its visit in "everyone else".
    assert body["meta"] == [1, 1, 0, 0, 0] and body["other"] == [0, 0, 0, 0, 0]


def test_funnel_browser_counts_are_cached_briefly(client, shop, monkeypatch):
    calls = []
    real = db.storefront_funnel

    def counting(*args, **kw):
        calls.append(args)
        return real(*args, **kw)
    monkeypatch.setattr(db, "storefront_funnel", counting)
    db.record_event("PageView", "pv1", "pixel", "sent", {"user_data": {}}, client_id="b1")
    first = client.get("/hub/api/funnel?range=30d", headers=API).json()
    again = client.get("/hub/api/funnel?range=30d", headers=API).json()
    assert first["other"][0] == again["other"][0] == 1 and len(calls) == 1
    assert calls[0][2] == hub.FUNNEL_EVENTS                   # only the funnel's steps are read
    client.get("/hub/api/funnel?range=7d", headers=API)
    assert len(calls) == 2                                    # each range has its own entry
    hub._funnel_cache[("30d", hub._range("30d")["start"])] = (time.time() - 1, [0] * 4, [0] * 4)
    client.get("/hub/api/funnel?range=30d", headers=API)
    assert len(calls) == 3                                    # expired: read again


def test_watchdog_and_quality_card_judge_match_quality_by_the_same_event(wd):
    assert watchdog.emq_event({"Purchase": {"score": 8.1}, "PageView": {"score": 9}}) == "Purchase"
    assert watchdog.emq_event({"Purchase": {"score": None}, "PageView": {"score": 4.4},
                               "AddToCart": {"score": 7.9}}) == "AddToCart"
    assert watchdog.emq_event({"Purchase": "junk", "PageView": {"score": "n/a"}, "AddToCart": None}) is None
    assert watchdog.emq_event(None) is None and watchdog.emq_event({}) is None
    for scores, event, status in (
            ({"Purchase": {"score": None}, "PageView": {"score": 4.4}, "AddToCart": {"score": 7.9}}, "AddToCart", "ok"),
            ({"AddToCart": {"score": 7.9}}, "AddToCart", "ok"),
            ({"Purchase": "junk", "PageView": {"score": 4.4}}, "PageView", "fail")):
        db.kv_set(f"emq:{MAIN}", json.dumps({"scores": scores, "taken_at": time.time()}))
        check = run_checks()[f"emq:{MAIN}"]
        card = hub._quality(time.time())[0]["emq"]
        assert check["status"] == status and check["detail"].startswith(f"{event} scored ")
        assert card["event"] == event and f"{card['score']:.1f}/10" in check["detail"]
    db.kv_set(f"emq:{MAIN}", json.dumps({"scores": {"Purchase": {"score": None}}, "taken_at": time.time()}))
    assert run_checks()[f"emq:{MAIN}"]["detail"] == "Meta hasn't scored this pixel yet."
    assert hub._quality(time.time())[0]["emq"]["score"] is None


def test_meta_tokens_never_reach_the_log(graph, monkeypatch, caplog):
    # app.py keeps httpx, which logs every request URL at INFO, to warnings only.
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    # Even with httpx at INFO, no URL carries a token any more.
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.INFO, logger="httpx")
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    graph.pages["123"] = [[insight("AD1", "B2 Statics - Ad 3", 40)], [insight("AD7", "B2 Statics - Ad 7", 20)]]
    graph.daily = {"123": [{"date_start": "2026-09-26", "spend": "60"}]}
    res = asyncio.run(meta_ads.ad_insights("2026-09-26", "2026-09-26"))
    assert [r["ad_id"] for r in res["rows"]] == ["AD1", "AD7"]      # followed the page whose link had the token
    assert asyncio.run(meta_ads.daily_spend("2026-09-26", "2026-09-26")) == {"2026-09-26": 60.0}
    asyncio.run(meta_ads.dataset_quality(MAIN, "test-token"))
    assert asyncio.run(meta_ads.dataset_name(MAIN, "test-token")) == "Core Club"
    assert "HTTP Request: GET https://graph.facebook.com" in caplog.text
    assert "ads-secret" not in caplog.text and "test-token" not in caplog.text
    assert "access_token" not in caplog.text


@pytest.mark.parametrize("next_link", ["https://evil.example/next?access_token=ads-secret",
                                       "http://graph.facebook.com/v21.0/act_123/insights?after=1", 123])
def test_a_paging_link_off_meta_never_gets_the_token(monkeypatch, next_link):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    monkeypatch.setattr(config, "META_ADS_TOKEN", "ads-secret")
    urls = []

    def handler(request):
        urls.append(str(request.url))
        if request.url.path.endswith("/act_123"):
            return httpx.Response(200, json={"name": "Core", "currency": "USD"})
        return httpx.Response(200, json={"data": [insight("AD1", "x", 1)], "paging": {"next": next_link}})
    meta_ads.set_http_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = asyncio.run(meta_ads.ad_insights("2026-09-26", "2026-09-26"))
    assert res["connected"] is False and "unexpected paging link" in res["error"]
    assert len(urls) == 2 and all(u.startswith("https://graph.facebook.com/") for u in urls)


def test_meta_read_cache_drops_expired_entries():
    meta_ads._cache.update({"ads:2026-08-01:2026-08-01": (time.time() - 1, {"rows": []}),
                            "daily:2026-08-01:2026-08-07": (time.time() - 60, {}),
                            "acct:123": (time.time() + 600, {"name": "Core"})})
    meta_ads._store("ads:2026-09-27:2026-09-27", {"rows": []}, 120)
    assert set(meta_ads._cache) == {"acct:123", "ads:2026-09-27:2026-09-27"}


def test_daily_spend_with_an_unreadable_account_is_unknown_not_zero(graph, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123", "456"])
    graph.daily = {"123": [{"date_start": "2026-09-26", "spend": "20"}]}
    graph.denied["456"] = (400, {"error": {"message": "User request limit reached", "code": 17}})
    assert asyncio.run(meta_ads.daily_spend("2026-09-20", "2026-09-26")) is None
    asked = len(graph.requests)
    graph.denied.clear()
    graph.daily["456"] = [{"date_start": "2026-09-26", "spend": "5"}]
    # The failure wasn't cached: the next refresh reads again and gets the full sum.
    assert asyncio.run(meta_ads.daily_spend("2026-09-20", "2026-09-26")) == {"2026-09-26": 25.0}
    assert len(graph.requests) > asked


def test_a_failed_daily_spend_read_draws_no_spend_line(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    shop.orders = [make_order(801, total_price="50.00")]
    meta.ad_rows = [insight("AD1", "B2 Statics - Ad 3", 25, campaign_name="SpermFuel CBO")]
    meta.daily = [{"date_start": hub._today().isoformat(), "spend": "25"}]

    def graph(request):                     # the per-ad read works, the per-day one times out
        if request.url.params.get("level") == "account":
            raise httpx.ReadTimeout("slow", request=request)
        return meta.graph(request)
    monkeypatch.setattr(meta_ads, "_client", httpx.AsyncClient(transport=httpx.MockTransport(graph)))
    o = client.get("/hub/api/overview?range=today", headers=API).json()
    assert o["cards"]["spend"] == 25.0 and o["cards"]["true_roas"] == 2.0
    assert o["series"]["spend"] == [None] * 7                  # not seven $0 days


def test_a_meta_read_error_is_not_shown_as_not_connected(client, shop, monkeypatch):
    shop.orders = [make_order(901)]
    o = client.get("/hub/api/overview", headers=API).json()["cards"]
    assert o["ads_connected"] is False and o["ads_configured"] is False
    assert client.get("/hub/api/creatives", headers=API).json()["configured"] is False
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])

    def down(request):
        raise httpx.ConnectError("no route", request=request)
    monkeypatch.setattr(meta_ads, "_client", httpx.AsyncClient(transport=httpx.MockTransport(down)))
    hub._orders_cache.clear()
    c = client.get("/hub/api/overview", headers=API).json()["cards"]
    assert c["ads_connected"] is False and c["ads_configured"] is True
    assert c["spend"] is None and c["true_roas"] is None and "ConnectError" in c["ads_error"]
    cr = client.get("/hub/api/creatives", headers=API).json()
    assert cr["connected"] is False and cr["configured"] is True and "ConnectError" in cr["error"]


def test_one_failed_webhook_read_does_not_stick_for_an_hour(wd, monkeypatch):
    ok = run_checks()["webhook"]
    assert ok["status"] == "ok"
    good_request = shopify._request

    async def down(method, path, **kw):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(shopify, "_request", down)
    watchdog._state["webhook_at"] = 0.0                    # the hourly re-check is due
    assert run_checks()["webhook"] == ok                   # the last real answer, not a stale warn
    # And Shopify is asked again on the next run, not in an hour.
    assert time.time() - watchdog._state["webhook_at"] >= 3600 - config.WATCHDOG_INTERVAL_SECONDS - 5
    watchdog._state["webhook_at"] -= config.WATCHDOG_INTERVAL_SECONDS + 1
    wd.webhooks = []
    monkeypatch.setattr(shopify, "_request", good_request)
    c = run_checks()["webhook"]
    assert c["status"] == "warn" and "isn't notifying" in c["detail"]
    # With no earlier answer to fall back on, the failure itself shows.
    watchdog._state.update(webhook=None, webhook_good=None, webhook_at=0.0)
    monkeypatch.setattr(shopify, "_request", down)
    c = run_checks()["webhook"]
    assert c["status"] == "warn" and c["detail"] == "Couldn't check with Shopify (ConnectError)."


def test_hub_and_watchdog_reads_use_an_index_not_every_row():
    now = time.time()
    db.record_event("PageView", "pv1", "pixel", "sent", {"user_data": {}}, client_id="b1")
    seen = []
    db._c().set_trace_callback(seen.append)
    try:
        db.last_sent_at(MAIN)
        db.event_stats(now - 86400, MAIN)
        db.storefront_funnel(now - 86400, now + 60, hub.FUNNEL_EVENTS)
        db.purchase_match_keys(now - 7 * 86400)
        db.renewal_orders_sent_as_purchase(now - 7 * 86400)
        assert db.first_storefront_event_at() is not None             # the funnel's "Counting since"
    finally:
        db._c().set_trace_callback(None)
    plans = {sql: " | ".join(r[3] for r in db._c().execute("EXPLAIN QUERY PLAN " + sql))
             for sql in seen if sql.lstrip().upper().startswith("SELECT")}
    assert len(plans) == 7, plans
    for sql, plan in plans.items():
        uses = "idx_events_order" if "kind='renewal'" in sql else "idx_events_pixel_time"
        assert uses in plan, (sql, plan)
        if "LIMIT 1" in sql:                                           # read in index order, stopping at the first
            assert "TEMP B-TREE" not in plan, (sql, plan)



# =====================================================================================
# Kaching rebill tags: the hub and the tracker decide rebills the same way
# =====================================================================================

RECURRING = "Kaching Subscription Recurring Order"


def test_hub_and_tracker_agree_on_rebills(client, shop):
    orders = [make_order(1001, source_name="subscription_contract_checkout_one"),
              make_order(1002, tags="Kaching Bundles, " + RECURRING, landing_site="/?utm_source=facebook&ad_id=AD1"),
              make_order(1003, tags="Kaching Subscription First Order"),
              make_order(1004, tags=RECURRING + " Paused"),
              make_order(1005, tags=[RECURRING.upper()]),
              make_order(1006)]
    for o in orders:
        assert (hub.order_type(o)[0] == "rebill") is (tracking.classify_order(o) == "renewal"), o["id"]
    shop.orders = orders
    cards = client.get("/hub/api/overview?range=today", headers=API).json()["cards"]
    assert cards["rebills"]["count"] == 3 and cards["new_sales"]["count"] == 3
    rows = {r["id"]: r for r in client.get("/hub/api/orders?range=today", headers=API).json()["orders"]}
    assert rows["1002"]["type"] == "rebill" and rows["1002"]["ad"] is None     # a rebill is never an ad's sale
    assert rows["1003"]["type"] == "new_sale" and rows["1004"]["type"] == "new_sale"


# =====================================================================================
# Assisted sales: the click history, assists on the sale, and the hub
# =====================================================================================

def visit(ad_id, name, at_ts, adset="Broad", campaign="Leggings CBO"):
    return {"ad_id": ad_id, "ad_name": name, "adset_name": adset, "campaign_name": campaign, "at": at_ts}


def test_click_history_appends_dedupes_and_trims():
    t = 1_790_000_000.0
    first = attribution.ad_visit(AD1, t)
    assert first == visit("AD1", "B2 Statics - Ad 3", t)
    # A link that doesn't say which ad can't be named as an assist, so it isn't kept.
    assert attribution.ad_visit({"fbclid": "1"}, t) is None
    assert attribution.ad_visit({"utm_source": "facebook", "utm_campaign": "CBO"}, t) is None
    history = attribution.add_ad_visit(None, first)
    assert history == [first]
    # The same ad within 30 minutes is the same visit: by id, or by name when a link has no id.
    assert attribution.add_ad_visit(json.dumps(history), {**first, "at": t + 60}) == history
    assert attribution.add_ad_visit(history, visit("", "b2 statics - AD 3", t + 1799)) == history
    assert len(attribution.add_ad_visit(history, {**first, "at": t + 1800})) == 2     # half an hour on: a new visit
    # A different ad is always added, even a same-named one with its own id.
    assert [v["ad_id"] for v in attribution.add_ad_visit(history, visit("AD2", "B2 Statics - Ad 3", t + 5))] == [
        "AD1", "AD2"]
    many = []
    for i in range(24):
        many = attribution.add_ad_visit(many, visit(f"AD{i}", f"Ad {i}", t + i * 60))
    # Changed on purpose (F4): the newest 20, not 10.
    assert attribution.HISTORY_MAX == 20
    assert [v["ad_id"] for v in many] == [f"AD{i}" for i in range(4, 24)]
    # Junk in a stored history is dropped, never a crash.
    junk = [1, "x", {"ad_id": "AD1"}, {"ad_id": "AD2", "at": "soon"}, {"ad_id": "AD3", "at": float("inf")},
            {"ad_id": "AD4", "ad_name": None, "at": t}]
    assert attribution.ad_history(json.dumps(junk)) == [visit("AD4", "", t, adset="", campaign="")]
    assert attribution.ad_history("{not json") == attribution.ad_history(None) == attribution.ad_history(7) == []


def test_the_pixel_keeps_each_browsers_last_twenty_ad_visits(client, sends):
    base = "https://getcoresupps.com/products/spermfuel?utm_source=facebook&utm_campaign=CBO&utm_content=Broad"

    def arrive(ad_id, name, event="page_viewed"):
        collect(client, name=event, url=f"{base}&utm_term={name}&ad_id={ad_id}")
    arrive("AD7", "Ad%207")
    arrive("AD7", "Ad%207", event="product_viewed")         # the same page's next pixel event
    arrive("AD7", "Ad%207")                                 # and a reload
    collect(client, name="page_viewed", url="https://getcoresupps.com/?fbclid=IwAR2abcDEFghiJKL")   # names no ad
    collect(client, name="page_viewed", url="https://getcoresupps.com/collections/all")             # not from an ad
    arrive("AD1", "Ad%203")
    s = db.get_session("browser-1")
    history = json.loads(s["ad_history"])
    assert [(v["ad_id"], v["ad_name"], v["adset_name"], v["campaign_name"]) for v in history] == [
        ("AD7", "Ad 7", "Broad", "CBO"), ("AD1", "Ad 3", "Broad", "CBO")]
    assert history[-1]["at"] == s["ad_seen_at"]
    assert all(set(v) == {"ad_id", "ad_name", "adset_name", "campaign_name", "at"} for v in history)
    assert "IwAR2" not in s["ad_history"]
    for i in range(22):
        arrive(f"X{i}", f"X{i}")
    assert [v["ad_id"] for v in json.loads(db.get_session("browser-1")["ad_history"])] == [
        f"X{i}" for i in range(2, 22)]                     # changed on purpose (F4): 20 kept, not 10
    sends()


def test_assists_are_the_other_ads_clicked_earlier_in_the_window(monkeypatch):
    monkeypatch.setattr(config, "ATTRIBUTION_WINDOW_DAYS", 7)
    created = float(int(time.time()) - 600)
    order = {"created_at": at(created), "landing_site": "/"}
    seen = created - 3600
    day = 86400
    history = [
        visit("AD9", "Old ad", created - 7 * day - 60),                  # just before the window
        visit("", "b2 statics - ad 7", created - 3 * day),               # AD7 by name: listed once, as its newest
        visit("AD5", "UGC Sarah - Ad 1", created - 2 * day, adset="Interests"),
        visit("AD7", "B2 Statics - Ad 7", created - 1 * day),
        visit("", "B2 Statics - Ad 3", created - 7200),                  # the credited ad by name
        visit("AD1", "B2 Statics - Ad 3", seen),                         # the credited click itself
    ]
    sess = {"ad_params": json.dumps(AD1), "ad_seen_at": seen, "ad_history": json.dumps(history)}
    credit = credit_of(order, sess)
    assert (credit["source"], credit["ad_id"], credit["click_at"]) == ("browser", "AD1", seen)
    assert credit["assists"] == [visit("AD7", "B2 Statics - Ad 7", created - day),
                                 visit("AD5", "UGC Sarah - Ad 1", created - 2 * day, adset="Interests")]
    # The window counts back from the sale: a shorter one drops the older assist.
    monkeypatch.setattr(config, "ATTRIBUTION_WINDOW_DAYS", 1)
    assert [a["ad_id"] for a in credit_of(order, sess)["assists"]] == ["AD7"]
    monkeypatch.setattr(config, "ATTRIBUTION_WINDOW_DAYS", 7)
    # Changed on purpose (F4): every other ad of a full click history (19), newest first, not five.
    busy = [visit(f"A{i}", f"Ad {i}", created - 3 * day + i * 3600) for i in range(22)] + [visit("AD1", "x", seen)]
    helped = credit_of(order, {**sess, "ad_history": json.dumps(busy)})["assists"]
    assert attribution.ASSISTS_MAX == 19 and [a["ad_id"] for a in helped] == [f"A{i}" for i in range(21, 2, -1)]
    # Only the credited ad in the history: no assists.
    assert credit_of(order, {**sess, "ad_history": json.dumps(history[-1:])})["assists"] == []
    # Every arrival in the browser's history is a click too: the newest one before
    # the sale gets it (here AD5, two minutes before), whatever the first landing page says.
    landing = {"created_at": at(created), "landing_site": "/?utm_source=facebook&utm_term=Landing%20ad&ad_id=AD5"}
    later = {"ad_history": json.dumps([visit("AD7", "B2 Statics - Ad 7", created - day),
                                       visit("AD5", "Landing ad", created - 120),
                                       visit("AD8", "After the sale", created + 60)])}
    credit = credit_of(landing, later)
    assert (credit["source"], credit["ad_id"]) == ("browser", "AD5")
    assert [a["ad_id"] for a in credit["assists"]] == ["AD7"]       # not the ad clicked after the sale
    # Only a click after the sale: no ad credited, no assists.
    after = {"ad_history": json.dumps([visit("AD8", "After the sale", created + 60)])}
    assert (credit_of(order, after)["meta"], credit_of(order, after)["assists"]) == (False, [])


def test_a_sale_lists_the_ads_clicked_before_the_last_one(client, sends, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    monkeypatch.setattr(config, "HUB_MIN_AD_SPEND", 0)    # every ad its own row (the $15 line has its own test)
    meta.ad_rows = [insight("AD1", "B2 Statics - Ad 3", 40, purchases=1, value=59.95),
                    insight("AD7", "B2 Statics - Ad 7", 20),
                    insight("AD9", "Hook test - v2", 10, adset_id="AS2", adset_name="Interests")]
    # Every ad click has its own fbclid: one seen before is an old click coming back.
    ad7 = AD_URL.replace("ad_id=AD1", "ad_id=AD7").replace("Ad%203", "Ad%207").replace(
        "fbclid=IwAR2abcDEFghiJKL", "fbclid=IwAR2ad7CLICKxyz")
    hook = ("https://getcoresupps.com/?utm_source=facebook&utm_campaign=Leggings%20CBO&utm_content=Interests"
            "&utm_term=Hook%20test%20-%20v2&ad_id=AD9")
    for url in (ad7, hook, AD_URL):
        collect(client, name="page_viewed", url=url)
        time.sleep(0.05)                                  # each arrival its own moment (Windows clock ~16 ms)
    collect(client, name="checkout_started", url="https://getcoresupps.com/checkouts/cn/x",
            checkout={"token": "chk_assist"})
    sends()
    o = make_order(1101, ts=time.time(), checkout_token="chk_assist")
    shop.orders = [o]
    db.upsert_order(o)
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    credit = db.orders_by_id(["1101"])["1101"]["attribution"]
    assert credit["ad_id"] == "AD1"
    assert [(a["ad_id"], a["ad_name"]) for a in credit["assists"]] == [("AD9", "Hook test - v2"),
                                                                       ("AD7", "B2 Statics - Ad 7")]

    body = client.get("/hub/api/creatives?range=today", headers=API).json()
    camp = body["campaigns"][0]
    groups = {g["name"]: g for g in camp["groups"]}
    ads = {a["ad_id"]: a for g in camp["groups"] for a in g["ads"]}
    assert (ads["AD1"]["store_sales"], ads["AD1"]["assists"], ads["AD1"]["assist_orders"]) == (1, 0, [])
    # Changed on purpose: an ad that only helped has no row here; the Assists section lists it.
    assert "AD7" not in ads and "AD9" not in ads and "Interests" not in groups
    assert groups["Broad"]["assists"] == 1 and groups["Broad"]["small"]["count"] == 1      # AD7, in the line
    helpers = {h["ad_id"] for h in client.get("/hub/api/assists?range=today", headers=API).json()["rows"]}
    assert {"AD7", "AD9"} <= helpers
    # Two ads helped, but it is one sale: the campaign counts sales, not ads.
    assert (camp["assists"], camp["assist_orders"]) == (1, ["#c1101"])
    # Assists never add to sales or revenue.
    assert camp["store_sales"] == 1 and camp["store_revenue"] == 59.95
    assert body["totals"]["store_sales"] == 1 and body["totals"]["store_revenue"] == 59.95

    r = client.get("/hub/api/orders?range=today", headers=API)
    ad = r.json()["orders"][0]["ad"]
    assert ad["ad_name"] == "B2 Statics - Ad 3" and ad["source"] == "browser"
    assert ad["assists"] == [{"ad_name": "Hook test - v2", "adset_name": "Interests", "campaign_name": "Leggings CBO"},
                             {"ad_name": "B2 Statics - Ad 7", "adset_name": "Broad", "campaign_name": "Leggings CBO"}]
    assert exposed(r) == [] and exposed(client.get("/hub/api/creatives?range=today", headers=API)) == []


def test_creatives_count_assists_per_ad_and_never_as_sales():
    base = {"campaign_id": "C1", "campaign_name": "CBO", "adset_id": "AS1", "adset_name": "Broad"}
    rows = [{**base, "ad_id": "A1", "ad_name": "Ad one", "spend": 50.0},
            {**base, "ad_id": "A2", "ad_name": "Ad two", "spend": 30.0},
            {**base, "adset_id": "AS2", "adset_name": "Interests", "ad_id": "A3", "ad_name": "Ad three", "spend": 20.0}]
    one, two, three = ({"ad_id": "A1", "ad_name": "Ad one"}, {"ad_id": "A2", "ad_name": "Ad two"},
                       {"ad_id": "A3", "ad_name": "Ad three"})
    facts = [
        _sale(1, 60.0, **one, assists=[two, three]),
        # The seller named again without its id is not its own assist; A1 twice counts once.
        _sale(2, 40.0, **two, assists=[{"ad_id": "", "ad_name": "ad TWO"}, one, {"ad_id": "", "ad_name": "Ad One"}]),
        # A sale whose last ad carried no name still credits the ads clicked before it.
        _sale(3, 30.0, assists=[three, "junk", {"ad_id": "", "ad_name": ""}]),
        # An assist for an ad Meta reported no delivery for (A8) counts for its ad set, with no row of its own.
        _sale(4, 20.0, ad_id="A9", ad_name="Paused", adset_name="Broad", campaign_name="CBO",
              assists=[{"ad_id": "A8", "ad_name": "Old paused", "adset_name": "Broad", "campaign_name": "CBO"}]),
        {"type": "rebill", "id": "5", "order": {"name": "#c5"}, "revenue": 39.0,
         "credit": {"meta": True, **one, "assists": [three]}},
    ]
    built = hub.build_creatives(facts, rows, "adset")
    assert len(built["campaigns"]) == 1
    camp = built["campaigns"][0]
    groups = {g["name"]: g for g in camp["groups"]}
    ads = {a["ad_id"]: a for g in camp["groups"] for a in g["ads"]}
    got = {k: (a["store_sales"], a["assists"], a["assist_orders"]) for k, a in ads.items()}
    # Changed on purpose: A3 and A8 only helped, so they have no row; the Assists section lists them.
    assert got == {"A1": (1, 1, ["#c2"]), "A2": (1, 1, ["#c1"]), "A9": (1, 0, [])}
    assert (groups["Broad"]["assists"], groups["Broad"]["store_sales"]) == (3, 3)
    assert sorted(groups["Broad"]["assist_orders"]) == ["#c1", "#c2", "#c4"]
    assert groups["Broad"]["small"]["count"] == 1 and groups["Broad"]["small"]["spend"] == 0      # A8
    assert "Interests" not in groups and camp["small"] == {"count": 1, "spend": 20.0, "store_sales": 0,
                                                            "store_revenue": 0.0, "meta_purchases": 0,
                                                            "meta_value": 0.0}                    # A3's ad set
    # #c1 had help in both ad sets; the campaign counts it once: 4 assisted sales, not 5 ad credits.
    assert (camp["assists"], camp["store_sales"], camp["store_revenue"]) == (4, 3, 120.0)
    assert sorted(camp["assist_orders"]) == ["#c1", "#c2", "#c3", "#c4"]
    assert all("assist_ids" not in a for a in ads.values())             # rollup bookkeeping stays inside
    t = built["totals"]
    assert t["store_sales"] == 4 and t["store_revenue"] == 150.0          # assists added nothing
    assert built["unlabelled"] == {"store_sales": 1, "store_revenue": 30.0, "orders": ["#c3"]}


def test_an_ad_set_counts_a_sale_with_help_from_several_of_its_ads_once():
    # A CBO where the buyer clicked two other ads in the same ad set before the one that sold.
    base = {"campaign_id": "C1", "campaign_name": "CBO", "adset_id": "AS1", "adset_name": "Broad"}
    rows = [{**base, "ad_id": f"A{i}", "ad_name": f"B2 Statics - Ad {i}", "spend": 10.0 * i} for i in (1, 2, 3)]
    facts = [_sale(1, 60.0, ad_id="A1", ad_name="B2 Statics - Ad 1",
                   assists=[{"ad_id": "A2", "ad_name": "B2 Statics - Ad 2"},
                            {"ad_id": "A3", "ad_name": "B2 Statics - Ad 3"}])]
    for group in ("adset", "batch"):
        camp = hub.build_creatives(facts, rows, group)["campaigns"][0]
        (g,) = camp["groups"]
        ads = {a["ad_id"]: (a["store_sales"], a["assists"]) for a in g["ads"]}
        assert ads == {"A1": (1, 0)} and g["small"]["count"] == 2        # the helpers have no row of their own
        assert (g["store_sales"], g["assists"], g["assist_orders"]) == (1, 1, ["#c1"]), group
        assert (camp["store_sales"], camp["assists"], camp["assist_orders"]) == (1, 1, ["#c1"]), group


def test_order_feed_assists_are_names_only():
    helped = [{"ad_id": "A9", "ad_name": "", "adset_name": "Broad", "campaign_name": "CBO", "at": 1.0},
              "junk", {"ad_name": "Hook", "adset_name": None}] + [{"ad_name": f"Ad {i}"} for i in range(6)]
    ad = hub._ad({"meta": True, "source": "browser", "click": True, "ad_id": "A1", "ad_name": "Seller",
                  "assists": helped})
    assert ad["assists"][:2] == [{"ad_name": "Ad A9", "adset_name": "Broad", "campaign_name": "CBO"},
                                 {"ad_name": "Hook", "adset_name": "", "campaign_name": ""}]
    # Changed on purpose (F4): no top five any more; every assist on the record is listed.
    assert len(ad["assists"]) == 8 and all(set(a) == {"ad_name", "adset_name", "campaign_name"} for a in ad["assists"])
    # A sale without help has no assists key, like the records stored before assists existed.
    assert "assists" not in hub._ad({"meta": True, "ad_name": "Seller"})
    assert "assists" not in hub._ad({"meta": True, "ad_name": "Seller", "assists": "junk"})


def test_a_database_from_before_assists_gains_the_click_history(monkeypatch):
    now = time.time()
    path = os.path.join(tempfile.mkdtemp(), "live.db")
    old = sqlite3.connect(path)
    old.executescript(PRE_HUB_SCHEMA)
    old.execute("ALTER TABLE sessions ADD COLUMN ad_params TEXT")
    old.execute("ALTER TABLE sessions ADD COLUMN ad_seen_at REAL")
    old.execute("INSERT INTO sessions (client_id, fbp, ad_params, ad_seen_at, first_seen, last_seen) "
                "VALUES ('b1', 'fb.1.1.OLD', ?, ?, 100, ?)", (json.dumps({"ad_id": "AD7", "utm_content": "Ad 7"}),
                                                                 now - 60, now))
    old.commit()
    old.close()
    monkeypatch.setattr(db, "_conn", None)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init()
    assert "ad_history" in {r[1] for r in db._c().execute("PRAGMA table_info(sessions)")}
    s = db.get_session("b1")
    assert s["ad_history"] is None and s["fbp"] == "fb.1.1.OLD" and s["first_seen"] == 100
    # The browser's last ad still gets its sale; with no history yet there are no assists.
    credit = credit_of({"created_at": at(now)}, s)
    assert credit["ad_id"] == "AD7" and credit["assists"] == []
    db.upsert_session("b1", ad_params=json.dumps(AD1), ad_seen_at=now, ad_visit=attribution.ad_visit(AD1, now))
    s = db.get_session("b1")
    assert [v["ad_id"] for v in json.loads(s["ad_history"])] == ["AD1"] and s["fbp"] == "fb.1.1.OLD"
    monkeypatch.setattr(db, "_conn", None)
    db.init()                                             # booting again changes nothing
    assert [v["ad_id"] for v in json.loads(db.get_session("b1")["ad_history"])] == ["AD1"]


# =====================================================================================
# Meta's click vs view split per creative
# =====================================================================================

def split_rows():
    return [
        insight("AD1", "B2 Statics - Ad 3", 40,
                actions=[{"action_type": "link_click", "value": "20", "7d_click": "20"},
                         {"action_type": "omni_purchase", "value": "5", "7d_click": "2", "1d_view": "3"}],
                action_values=[{"action_type": "omni_purchase", "value": "299.75", "7d_click": "119.90",
                                "1d_view": "179.85"}]),
        # Meta leaves out a window with nothing in it.
        insight("AD7", "B2 Statics - Ad 7", 20, actions=[{"action_type": "omni_purchase", "value": "1", "7d_click": "1"}],
                action_values=[{"action_type": "omni_purchase", "value": "59.95", "7d_click": "59.95"}]),
        # The answer without window keys: totals only.
        insight("AD9", "Hook test - v2", 10, purchases=2, value="119.90", adset_id="AS2", adset_name="Interests"),
        insight("AD20", "Nothing sold", 5, adset_id="AS2", adset_name="Interests"),
    ]


def test_purchase_split_reads_the_click_and_view_windows():
    both = {"action_type": "omni_purchase", "value": "5", "7d_click": "3", "1d_view": "2"}
    assert meta_ads.purchase_split([{"action_type": "link_click", "value": "90", "7d_click": "90"}, both]) == (3.0, 2.0)
    # The same action type purchases() reads, so the split belongs to the total beside it.
    pixel = {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "9", "1d_view": "9"}
    assert meta_ads.purchase_split([pixel, both]) == (3.0, 2.0) and meta_ads.purchases([pixel, both]) == 5.0
    assert meta_ads.purchase_split([pixel]) == (0.0, 9.0)                   # an empty window is left out
    assert meta_ads.purchase_split([{"action_type": "omni_purchase", "value": "4"}]) == (None, None)
    assert meta_ads.purchase_split([{"action_type": "omni_purchase", "value": "4", "7d_click": "n/a",
                                     "1d_view": "1"}]) == (None, 1.0)
    assert meta_ads.purchase_split([{"action_type": "omni_purchase", "7d_click": "inf", "1d_view": "1"}]) == (None, 1.0)
    # No purchases at all: nothing to split.
    for empty in (None, [], ["junk"], [{"action_type": "link_click", "value": "5", "7d_click": "5"}], "x"):
        assert meta_ads.purchase_split(empty) == (0.0, 0.0), empty


def test_ad_insights_ask_meta_for_click_and_view_sales(graph, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    graph.pages["123"] = [split_rows()]
    res = asyncio.run(meta_ads.ad_insights("2026-09-20", "2026-09-26"))
    assert res["connected"] is True
    rows = {r["ad_id"]: r for r in res["rows"]}

    def split(ad):
        return tuple(rows[ad].get(k) for k in meta_ads.SPLIT_KEYS)
    assert (rows["AD1"]["meta_purchases"], rows["AD1"]["meta_value"]) == (5.0, 299.75)     # totals as before
    assert split("AD1") == (2.0, 3.0, 119.9, 179.85)
    assert split("AD7") == (1.0, 0.0, 59.95, 0.0)
    # Without window keys the row is exactly what it was: totals, no split.
    assert rows["AD9"]["meta_purchases"] == 2.0 and not set(meta_ads.SPLIT_KEYS) & set(rows["AD9"])
    assert not set(meta_ads.SPLIT_KEYS) & set(rows["AD20"])
    call = next(r for r in graph.requests if r.url.path.endswith("/insights"))
    assert json.loads(call.url.params["action_attribution_windows"]) == ["7d_click", "1d_view"]
    asyncio.run(meta_ads.daily_spend("2026-09-20", "2026-09-26"))
    daily = [r for r in graph.requests if r.url.params.get("level") == "account"]
    assert daily and all("action_attribution_windows" not in r.url.params for r in daily)


def test_creatives_show_metas_click_and_view_split(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    monkeypatch.setattr(config, "HUB_MIN_AD_SPEND", 0)    # every ad its own row (the $15 line has its own test)
    meta.ad_rows = split_rows()
    body = client.get("/hub/api/creatives?range=today", headers=API).json()
    camp = body["campaigns"][0]
    groups = {g["name"]: g for g in camp["groups"]}
    ads = {a["ad_id"]: a for g in camp["groups"] for a in g["ads"]}

    def split(x):
        return tuple(x[k] for k in meta_ads.SPLIT_KEYS)
    assert split(ads["AD1"]) == (2, 3, 119.9, 179.85) and ads["AD1"]["meta_purchases"] == 5
    assert split(ads["AD7"]) == (1, 0, 59.95, 0.0)
    assert split(ads["AD9"]) == (None, None, None, None)            # Meta gave totals only: unknown, not 0
    assert "AD20" not in ads                                        # nothing sold: no row
    assert split(groups["Broad"]) == (3, 3, 179.85, 179.85) and groups["Broad"]["meta_purchases"] == 6
    # A total is only split when every ad in it is.
    assert split(groups["Interests"]) == (None, None, None, None)
    assert split(camp) == (None, None, None, None) and camp["meta_purchases"] == 8
    assert body["totals"]["meta_click_purchases"] is None and body["totals"]["meta_purchases"] == 8
    meta.ad_rows = split_rows()[:2]
    meta_ads._cache.clear()
    t = client.get("/hub/api/creatives?range=today", headers=API).json()["totals"]
    assert split(t) == (3, 3, 179.85, 179.85) and t["meta_purchases"] == 6


# =====================================================================================
# Product ROAS: the products the running campaigns sell, and their sales
# =====================================================================================

def line(pid, title, price=None, qty=1):
    item = {"product_id": pid, "title": title, "quantity": qty}
    if price is not None:
        item["price"] = str(price)
    return item


def fact(oid, revenue, lines, kind="new_sale", **credit):
    """A classified order as hub._facts makes it; credit=... makes it a Meta sale."""
    return {"type": kind, "id": str(oid), "ts": time.time(), "revenue": revenue, "reason": "", "stored": None,
            "order": {"id": oid, "name": f"#c{oid}", "line_items": lines},
            "credit": {"meta": True, "source": "browser", "click": True, **credit} if credit else None}


def row(ad_id, campaign_id, campaign_name, spend):
    return {"ad_id": ad_id, "ad_name": f"Ad {ad_id}", "adset_id": "AS1", "adset_name": "Broad",
            "campaign_id": campaign_id, "campaign_name": campaign_name, "spend": float(spend),
            "meta_purchases": 0.0, "meta_value": 0.0}


def test_campaigns_are_tied_to_the_products_they_sell():
    rows = [row("A1", "C1", "Leggings CBO", 60), row("A2", "C2", "sperm - ASC", 40),
            row("A3", "C3", "Test | Hydration", 30), row("A4", "C4", "Broad 3", 20),
            row("A5", "C5", "Prospecting Q4", 10), row("A6", "C6", "Winners", 10),
            row("A7", "C7", "Paused SpermFuel", 0), row("A8", "C8", "CBO 250 - Launch", 5)]
    learned = [
        # The store tied these sales to a campaign: by its id, by its name (any case), by the ad's id.
        fact(1, 50.0, [line(222, "Fleecies Leggings", 50), line(555, "Free Gift", 0)], campaign_id="C1"),
        fact(2, 30.0, [line(666, "Protein", 30)], campaign_name="prospecting q4"),
        fact(3, 20.0, [line(333, "Mascara Pro", 20)], ad_id="A6"),
        # Not tied to an ad, or MRR: teach nothing, but their titles are known.
        fact(4, 40.0, [line(111, "SpermFuel+", 40), line(444, "Hydration Stix", 10)]),
        fact(5, 39.0, [line(777, "Bottle", 39)], kind="rebill"),
        fact(6, 30.0, [line(888, "Collagen 2500mg", 30)]),
    ]
    mapping, titles = hub.product_map(hub._campaigns(rows), learned, {"A6": "C6"})
    assert mapping == {
        "C1": {"222"},                  # learned; the free gift in the order isn't a product it sells
        "C2": {"111"},                  # by name: "sperm" is in "spermfuel"; "asc" isn't a product word (maScara)
        "C3": {"444"},                  # "test" is left out, "hydration" matches
        "C4": set(),                    # nothing to go on
        "C5": {"666"}, "C6": {"333"},
        "C7": {"111"},
        "C8": set()}                    # "250" is a budget, not the "2500" in "Collagen 2500mg"
    assert titles["111"] == "SpermFuel+" and titles["777"] == "Bottle" and "555" not in titles
    # Numbers alone are budgets, dates or years; words with a digit in them stay.
    assert hub._words("CBO 250 - B12 Launch 2026") == ["b12", "launch"]
    assert hub._words("Christmas 2026 - Tree") == ["christmas", "tree"]

    in_range = [
        fact(10, 40.0, [line(111, "SpermFuel+", 40)]),
        # Mixed: 60 of the 80 in line items is advertised, so 75% of the 75.00 total counts.
        fact(11, 75.0, [line(222, "Fleecies Leggings", 30, qty=2), line(777, "Bottle", 20)]),
        fact(12, 20.0, [line(777, "Bottle", 20)]),                                    # nothing advertised
        fact(13, 40.0, [line(111, "SpermFuel+", 40)], kind="rebill"),                 # MRR never counts
        fact(14, 20.0, [line(111, "SpermFuel+", 0), line(777, "Bottle", 20)]),        # a free advertised item
        fact(15, 100.0, [line(111, "SpermFuel+", qty=1), line(777, "Bottle", qty=3)]),  # no prices: by quantity
    ]
    adv = hub._advertising(rows, None, learned, in_range)
    assert (adv["count"], adv["revenue"]) == (3, 121.25)                    # 40 + 56.25 + 25
    assert adv["products"] == [
        {"product_id": "222", "title": "Fleecies Leggings", "campaigns": ["Leggings CBO"]},
        {"product_id": "444", "title": "Hydration Stix", "campaigns": ["Test | Hydration"]},
        {"product_id": "333", "title": "Mascara Pro", "campaigns": ["Winners"]},
        {"product_id": "666", "title": "Protein", "campaigns": ["Prospecting Q4"]},
        # The paused campaign sells it too, but only campaigns with spend make a product advertised.
        {"product_id": "111", "title": "SpermFuel+", "campaigns": ["sperm - ASC"]}]
    assert adv["unmapped"] == [{"campaign_name": "Broad 3", "spend": 20.0},
                               {"campaign_name": "CBO 250 - Launch", "spend": 5.0}]
    assert adv["per_day"] is None


def test_a_campaign_sells_the_main_line_of_its_sales_not_the_add_ons():
    rows = [row("A1", "C1", "SpermFuel CBO", 100), row("A2", "C2", "Sperm scale", 50),
            row("A3", "C3", "Old import", 0)]
    protection = line(999, "Shipping Protection", 1.99)       # the cart drawer's paid Address Protection
    amino = line(888, "Amino add-on", 25)
    learned = [
        fact(1, 52.98, [line(111, "SpermFuel+", 49.99), protection], campaign_id="C1"),
        # Four sales with add-ons, and one where the buyer also took a pricier product (1 in 5).
        *[fact(10 + i, 76.98, [line(111, "SpermFuel+", 49.99), amino, protection], campaign_id="C2")
          for i in range(4)],
        fact(14, 111.97, [line(111, "SpermFuel+", 49.99), line(222, "Fleecies", 59.99), protection],
             campaign_id="C2"),
        # No prices on the lines: nothing tells the main one, so every product in it counts, as before.
        fact(20, 50.0, [line(111, "SpermFuel+"), line(888, "Amino add-on")], campaign_id="C3"),
        # Organic orders carry the same protection line.
        fact(30, 31.98, [line(333, "Oxy Cleanse", 29.99), protection]),
        fact(31, 51.98, [line(222, "Fleecies", 49.99), protection]),
    ]
    mapping, _ = hub.product_map(hub._campaigns(rows), learned, {})
    assert mapping == {"C1": {"111"}, "C2": {"111"}, "C3": {"111", "888"}}
    # A product that is the main line of a quarter of the sales or more is one the campaign sells.
    common = learned[:1] + [fact(15, 111.97, [line(111, "SpermFuel+", 49.99), line(222, "Fleecies", 59.99)],
                                 campaign_id="C1")]
    assert hub.product_map(hub._campaigns(rows), common, {})[0]["C1"] == {"111", "222"}
    # The organic orders' protection line doesn't make them advertised sales: 1 sale, in proportion.
    adv = hub._advertising(rows, None, learned, [learned[0], learned[-2], learned[-1]])
    assert adv["count"] == 1 and adv["revenue"] == round(52.98 * 49.99 / 51.98, 2)
    assert adv["products"] == [{"product_id": "111", "title": "SpermFuel+",
                                "campaigns": ["Sperm scale", "SpermFuel CBO"]}]


def test_overview_leads_with_product_roas(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    tz, today = config.store_tz(), hub._today()

    def day_ts(back):
        return dt.datetime.combine(today - dt.timedelta(days=back), dt.time(12), tzinfo=tz).timestamp()
    leggings = credited(1201, "50.00", ad_id="AD1", campaign_id="C1", ad_name="Leggings UGC")
    leggings["line_items"] = [line(222, "Fleecies Leggings", 50)]
    shop.orders = [
        leggings,
        make_order(1202, total_price="40.00", line_items=[line(111, "SpermFuel+", 40)]),
        make_order(1203, total_price="60.00", line_items=[line(111, "SpermFuel+", 30), line(666, "Protein", 30)]),
        make_order(1204, total_price="30.00", line_items=[line(666, "Protein", 30)]),
        make_order(1205, total_price="39.00", source_name="subscription_contract",
                   line_items=[line(111, "SpermFuel+", 39)]),
        # Yesterday only the leggings campaign spent: SpermFuel+ wasn't advertised that day.
        make_order(1206, ts=day_ts(1), total_price="40.00", line_items=[line(111, "SpermFuel+", 40)]),
        make_order(1207, ts=day_ts(1), total_price="50.00", line_items=[line(222, "Fleecies Leggings", 50)]),
        # Two days ago nothing spent: nothing was advertised.
        make_order(1208, ts=day_ts(2), total_price="50.00", line_items=[line(222, "Fleecies Leggings", 50)]),
    ]
    meta.ad_rows = [insight("AD1", "Leggings UGC", 60, purchases=2, value=150, campaign_id="C1",
                            campaign_name="Leggings CBO"),
                    insight("AD2", "Hook 2", 40, campaign_id="C2", campaign_name="Sperm Scale"),
                    insight("AD3", "Static 1", 20, campaign_id="C3", campaign_name="Broad 3"),
                    insight("AD4", "Old", 0, campaign_id="C4", campaign_name="Protein push")]
    meta.daily = [{"date_start": today.isoformat(), "spend": "120"},
                  {"date_start": (today - dt.timedelta(days=1)).isoformat(), "spend": "10"}]
    meta.campaign_daily = [
        {"date_start": today.isoformat(), "campaign_id": "C1", "campaign_name": "Leggings CBO", "spend": "60"},
        {"date_start": today.isoformat(), "campaign_id": "C2", "campaign_name": "Sperm Scale", "spend": "40"},
        {"date_start": today.isoformat(), "campaign_id": "C3", "campaign_name": "Broad 3", "spend": "20"},
        {"date_start": (today - dt.timedelta(days=1)).isoformat(), "campaign_id": "C1",
         "campaign_name": "Leggings CBO", "spend": "10"}]
    r = client.get("/hub/api/overview?range=today", headers=API)
    c = r.json()["cards"]
    # 50 (leggings, learned from its credited sale) + 40 + half of 60 (SpermFuel+, by the
    # campaign's name). The Protein and MRR orders don't count, and the campaign that
    # sells Protein spent nothing. All spend counts, the unmapped campaign's too.
    assert c["spend"] == 120.0 and c["advertised"]["count"] == 3 and c["advertised"]["revenue"] == 120.0
    assert c["product_roas"] == c["true_roas"] == 1.0 and c["cost_per_sale"] == 40.0
    assert c["ad_roas"] == round(50 / 120, 2) and c["meta_roas"] == 1.25
    assert c["advertised"]["products"] == [
        {"product_id": "222", "title": "Fleecies Leggings", "campaigns": ["Leggings CBO"]},
        {"product_id": "111", "title": "SpermFuel+", "campaigns": ["Sperm Scale"]}]
    assert c["unmapped_campaigns"] == [{"campaign_name": "Broad 3", "spend": 20.0}]
    assert c["mrr"] == {"count": 1, "revenue": 39.0}
    s = r.json()["series"]
    assert s["advertised_revenue"][-3:] == [0.0, 50.0, 120.0] and s["advertised_sales"][-3:] == [0, 1, 3]
    assert s["ad_revenue"][-1] == 50.0 and s["rebill_revenue"][-1] == 39.0
    assert exposed(r) == []
    # The per-day campaign spend is one read per refresh, level=campaign, day by day.
    calls = [q for q in meta.requests if q.url.params.get("level") == "campaign"]
    assert len(calls) == 1 and calls[0].url.params["time_increment"] == "1"
    # Yesterday's range (this fake answers every range with the same campaigns): its two sales count.
    y = client.get("/hub/api/overview?range=yesterday", headers=API).json()["cards"]
    assert y["advertised"]["count"] == 2 and y["advertised"]["revenue"] == 90.0 and y["product_roas"] == 0.75
    cr = client.get("/hub/api/creatives?range=today", headers=API).json()["totals"]
    assert cr["product_roas"] == cr["true_roas"] == 1.0 and cr["ad_roas"] == round(50 / 120, 2)


def test_product_roas_needs_ad_spend_and_shopify(client, shop, meta, monkeypatch):
    shop.orders = [make_order(1301, total_price="40.00"),
                   make_order(1302, total_price="39.00", source_name="subscription_contract")]
    o = client.get("/hub/api/overview", headers=API).json()
    c = o["cards"]
    # Ads not connected: no ROAS to show (the page says "Connect ad spend"); MRR is store data.
    assert c["product_roas"] is None and c["ad_roas"] is None and c["true_roas"] is None
    assert c["advertised"] == {"count": None, "revenue": None, "products": []} and c["cost_per_sale"] is None
    assert c["unmapped_campaigns"] == [] and c["mrr"] == {"count": 1, "revenue": 39.0}
    assert o["series"]["advertised_revenue"] == [None] * 7
    # Connected, but Shopify can't be read: unknown, not 0.00x.
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    meta.ad_rows = [insight("AD1", "Ad", 40, campaign_name="SpermFuel CBO")]
    shop.fail = 403
    hub._orders_cache.clear()
    c = client.get("/hub/api/overview", headers=API).json()["cards"]
    assert c["spend"] == 40.0 and c["product_roas"] is None and c["ad_roas"] is None
    assert c["advertised"]["count"] is None and c["mrr"] == {"count": None, "revenue": None}
    # Connected, but which campaigns ran each day can't be read: the ROAS stands, the
    # per-day advertised line is unknown rather than a row of $0 days.
    shop.fail = None
    hub._orders_cache.clear()
    meta_ads._cache.clear()

    def graph(request):
        if request.url.params.get("level") == "campaign":
            raise httpx.ReadTimeout("slow", request=request)
        return meta.graph(request)
    monkeypatch.setattr(meta_ads, "_client", httpx.AsyncClient(transport=httpx.MockTransport(graph)))
    o = client.get("/hub/api/overview", headers=API).json()
    assert o["cards"]["product_roas"] == 1.0 and o["series"]["advertised_revenue"] == [None] * 7


# =====================================================================================
# Meta's names for ads, by ad id
# =====================================================================================

class NamesGraph:
    """Graph's ?ids= read: one unknown id fails the whole batch, as on Meta."""

    def __init__(self, known):
        self.known, self.requests, self.fail, self.say_which = known, [], None, True

    def handler(self, request: httpx.Request):
        self.requests.append(request)
        if self.fail:
            if isinstance(self.fail, Exception):
                raise self.fail
            return httpx.Response(self.fail[0], json=self.fail[1])
        ids = request.url.params["ids"].split(",")
        unknown = [i for i in ids if i not in self.known]
        if unknown:
            error = ({"message": "(#803) Some of the aliases you requested do not exist: " + ",".join(unknown),
                      "code": 803} if self.say_which else {"message": "(#100) Unsupported get request.", "code": 100})
            return httpx.Response(400, json={"error": {**error, "type": "OAuthException"}})
        return httpx.Response(200, json={i: {"id": i, "name": self.known[i][0], "adset": {"id": "9", "name": self.known[i][1]},
                                              "campaign": {"id": "8", "name": self.known[i][2]}} for i in ids})


@pytest.fixture
def names(monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    monkeypatch.setattr(config, "META_ADS_TOKEN", "ads-secret")
    fake = NamesGraph({str(1000 + i): (f"Ad {i}", f"Set {i}", "sperm") for i in range(60) if i != 3})
    meta_ads.set_http_client(httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


def test_ad_names_are_read_by_id_fifty_at_a_time_and_kept_a_day(names):
    ids = [str(1000 + i) for i in range(60)]
    got = asyncio.run(meta_ads.ad_names(ids + ["1000", "AD1", "", None, "12 34", "1,2"]))
    assert set(got) == set(ids) - {"1003"}                       # Meta doesn't know 1003: it is absent
    assert got["1007"] == {"ad_name": "Ad 7", "adset_name": "Set 7", "campaign_name": "sperm"}
    asked = [r.url.params["ids"].split(",") for r in names.requests]
    # 50 ids, refused for 1003 (Meta names it), the other 49 again, then the last 10.
    assert [len(a) for a in asked] == [50, 49, 10] and "1003" not in asked[1]
    for r in names.requests:
        assert r.url.path == "/v21.0/" and r.url.params["fields"] == "name,adset{name},campaign{name}"
        assert r.headers["authorization"] == "Bearer ads-secret"
        assert "ads-secret" not in str(r.url) and "access_token" not in str(r.url)
    # A day per id, the unknown one included: asking again costs no call.
    assert asyncio.run(meta_ads.ad_names(ids)) == got and len(names.requests) == 3
    expires = meta_ads._cache["adname:1003"][0]
    assert abs(expires - time.time() - meta_ads.NAMES_TTL) < 60
    meta_ads._cache["adname:1001"] = (time.time() - 1, meta_ads._cache["adname:1001"][1])
    asyncio.run(meta_ads.ad_names(["1001", "1002"]))
    assert names.requests[-1].url.params["ids"] == "1001" and len(names.requests) == 4


def test_ad_names_never_raise(names, monkeypatch):
    # Meta refuses the batch without saying which id: halve it until the unknown one stands alone.
    names.say_which = False
    got = asyncio.run(meta_ads.ad_names(["1000", "1001", "1003", "1004"]))
    assert set(got) == {"1000", "1001", "1004"}
    assert [r.url.params["ids"] for r in names.requests] == ["1000,1001,1003,1004", "1000,1001", "1003,1004",
                                                             "1003", "1004"]
    # A rate limit or a network error: nothing, no split, asked again in 10 minutes rather than a day.
    for fail in ((400, {"error": {"message": "User request limit reached", "code": 17}}),
                 httpx.ConnectError("no route")):
        meta_ads._cache.clear()
        names.requests.clear()
        names.fail = fail
        assert asyncio.run(meta_ads.ad_names(["1005", "1006"])) == {}
        assert len(names.requests) == 1
        assert abs(meta_ads._cache["adname:1005"][0] - time.time() - meta_ads.NAMES_RETRY_TTL) < 60
    names.fail = None
    # Garbage in, or no ad account set up: no call at all.
    names.requests.clear()
    meta_ads._cache.clear()
    assert asyncio.run(meta_ads.ad_names(None)) == asyncio.run(meta_ads.ad_names("1000")) == {}
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", [])
    assert asyncio.run(meta_ads.ad_names(["1000"])) == {} and names.requests == []


def test_ad_names_stop_at_the_first_timeout_or_rate_limit(names):
    ids = [str(2000 + i) for i in range(120)]
    for fail in (httpx.ConnectError("no route"), httpx.ReadTimeout("slow"),
                 (400, {"error": {"message": "User request limit reached", "code": 17}})):
        meta_ads._cache.clear()
        names.requests.clear()
        names.fail = fail
        assert asyncio.run(meta_ads.ad_names(ids)) == {}
        # One call, not one per 50 ids: the rest would meet the same wall, and a
        # throttled account would only be throttled longer.
        assert len(names.requests) == 1
        for ad in ids:
            assert abs(meta_ads._cache[f"adname:{ad}"][0] - time.time() - meta_ads.NAMES_RETRY_TTL) < 60
        # A short wait of its own, not the 30 seconds the spend reads get.
        assert names.requests[0].extensions["timeout"]["read"] == meta_ads.NAMES_TIMEOUT < 30
        # Asked again within the 10 minutes: no call at all.
        assert asyncio.run(meta_ads.ad_names(ids)) == {} and len(names.requests) == 1
    names.fail = None


def test_campaign_spend_per_day(graph, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    graph.pages["123"] = [[{"date_start": "2026-09-26", "campaign_id": "C1", "campaign_name": "sperm", "spend": "20"},
                           {"date_start": "2026-09-26", "campaign_id": "C2", "campaign_name": "Leggings", "spend": "5.5"},
                           {"date_start": "2026-09-27", "campaign_id": "C1", "campaign_name": "sperm", "spend": "7"},
                           {"campaign_id": "C9", "spend": "1"}]]            # no day: left out
    assert asyncio.run(meta_ads.campaign_daily_spend("2026-09-21", "2026-09-27")) == {
        "2026-09-26": [{"campaign_id": "C1", "campaign_name": "sperm", "spend": 20.0},
                       {"campaign_id": "C2", "campaign_name": "Leggings", "spend": 5.5}],
        "2026-09-27": [{"campaign_id": "C1", "campaign_name": "sperm", "spend": 7.0}]}
    call = graph.requests[-1]
    assert call.url.params["level"] == "campaign" and call.url.params["time_increment"] == "1"
    assert call.headers["authorization"] == "Bearer ads-secret" and "ads-secret" not in str(call.url)
    graph.denied["123"] = (400, {"error": {"message": "User request limit reached", "code": 17}})
    assert asyncio.run(meta_ads.campaign_daily_spend("2026-09-20", "2026-09-26")) is None
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", [])
    assert asyncio.run(meta_ads.campaign_daily_spend("2026-09-20", "2026-09-26")) == {}


def test_ads_are_shown_by_metas_names_for_their_id(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    monkeypatch.setattr(config, "HUB_MIN_AD_SPEND", 0)    # these ads have no spend: give them rows anyway
    meta.names = {"120001": {"id": "120001", "name": "Sperm UGC 3", "adset": {"id": "7", "name": "MOF 3"},
                             "campaign": {"id": "C9", "name": "sperm"}},
                  "120002": {"id": "120002", "name": "Hook B", "adset": {"id": "6", "name": "TOF 1"},
                             "campaign": {"id": "C9", "name": "sperm"}}}
    # An old link with utm_content and utm_term swapped: its "ad name" is really the ad set.
    shop.orders = [credited(1401, "60.00", ad_id="120001", ad_name="MOF 3", adset_name="Sperm UGC 3",
                            campaign_name="sperm", campaign_id="C9", assists=[
                                visit("120002", "TOF 1", time.time() - 3600, adset="Hook B", campaign="sperm"),
                                visit("", "Old link ad", time.time() - 7200, adset="Old set", campaign="sperm"),
                                visit("999999", "Deleted ad", time.time() - 9000, adset="Gone", campaign="sperm")])]
    r = client.get("/hub/api/orders?range=today", headers=API)
    ad = r.json()["orders"][0]["ad"]
    assert (ad["ad_name"], ad["adset_name"], ad["campaign_name"], ad["ad_id"]) == ("Sperm UGC 3", "MOF 3", "sperm",
                                                                                 "120001")
    assert ad["assists"] == [{"ad_name": "Hook B", "adset_name": "TOF 1", "campaign_name": "sperm"},
                             {"ad_name": "Old link ad", "adset_name": "Old set", "campaign_name": "sperm"},
                             # Meta doesn't know it any more: the link's names stand in.
                             {"ad_name": "Deleted ad", "adset_name": "Gone", "campaign_name": "sperm"}]
    asked = [q for q in meta.requests if q.url.params.get("ids")]
    assert [q.url.params["ids"] for q in asked] == ["120001,120002,999999", "120001,120002"]
    assert all(q.headers["authorization"] == "Bearer test-token" and "test-token" not in str(q.url) for q in asked)
    # The creatives table: no delivery in the range, so these rows come from the sale, named by Meta.
    body = client.get("/hub/api/creatives?range=today", headers=API).json()
    (camp,) = body["campaigns"]
    rows = {a["ad_id"]: (g["name"], a["ad_name"]) for g in camp["groups"] for a in g["ads"]}
    assert rows == {"120001": ("MOF 3", "Sperm UGC 3")}             # 120002 only helped: no row here
    assert camp["campaign_name"] == "sperm"
    # The assists section too: each assisting ad by Meta's names (the link's when Meta doesn't
    # know it), and the ad that closed the sale by Meta's.
    rows = client.get("/hub/api/assists?range=today", headers=API).json()["rows"]
    assert [(h["ad_id"], h["ad_name"], h["adset_name"], h["campaign_name"]) for h in rows] == [
        ("999999", "Deleted ad", "Gone", "sperm"), ("120002", "Hook B", "TOF 1", "sperm"),
        ("", "Old link ad", "Old set", "sperm")]
    for h in rows:
        assert h["closers"] == [{"ad_id": "120001", "ad_name": "Sperm UGC 3", "adset_name": "MOF 3", "sales": 1,
                                 "value": 60.0}]
    # Every name came from the first read's cache.
    assert len([q for q in meta.requests if q.url.params.get("ids")]) == 2


# =====================================================================================
# MRR: what the owner calls subscription rebills
# =====================================================================================

def test_rebills_read_mrr_everywhere_the_owner_looks(client, shop, meta, monkeypatch):
    shop.orders = [make_order(1501, source_name="subscription_contract", total_price="39.00")]
    r = client.get("/hub/api/orders?range=today", headers=API)
    (o,) = r.json()["orders"]
    assert o["type"] == "rebill" and o["type_label"] == "MRR"                # the API value stays
    ov = client.get("/hub/api/overview?range=today", headers=API)
    check = next(c for c in ov.json()["status"]["checks"] if c["id"] == "renewals")
    assert check["name"] == "MRR kept out of sales"
    assert check["detail"] == "MRR orders go to Meta as SubscriptionRenewal, never as Purchase."
    assert ov.json()["cards"]["mrr"] == {"count": 1, "revenue": 39.0}
    monkeypatch.setattr(config, "RENEWAL_EVENT_NAME", "")
    body = client.post("/hub/api/resend/1501", headers=POST).json()
    assert body["message"] == "Not sent: sending MRR to Meta is switched off." and meta.sent == []
    for text in (r.text, ov.text, json.dumps(body)):
        assert "Rebill" not in text


# =====================================================================================
# Funnel: Purchases are the funnel's own browsers that bought
# =====================================================================================

def test_funnel_purchases_come_from_the_browsers_it_counted(client, shop):
    now = time.time()
    db.upsert_session("b-meta", ad_params=json.dumps({"ad_id": "AD1"}), ad_seen_at=now - 60, checkout_token="chk1")
    db.upsert_session("b-other", fbp="fb.1.1.2", checkout_token="chk2")
    db.upsert_session("b-gone", fbp="fb.1.1.3", checkout_token="chk3")
    db.upsert_session("b-mrr", fbp="fb.1.1.4", checkout_token="chk5")

    def event(cid, name, ago=0):
        db.record_event(name, f"{cid}-{name}", "pixel", "sent", {"user_data": {}}, client_id=cid)
        if ago:
            db._c().execute("UPDATE events SET created_at=? WHERE event_id=?", (now - ago, f"{cid}-{name}"))
    event("b-meta", "PageView")
    event("b-other", "AddToCart")             # its page view was before the range: still a visitor
    event("b-gone", "PageView", ago=3 * 86400)
    event("b-mrr", "PageView")
    shop.orders = [
        make_order(1601, checkout_token="chk1"),                      # organic landing, Meta browser: Meta
        make_order(1602, checkout_token="chk2", landing_site="/?utm_source=facebook&ad_id=AD1"),   # the other way
        make_order(1603, checkout_token="chk3"),                      # its visit wasn't seen today, but it bought
        make_order(1604, checkout_token="chk4"),                      # no browser at all
        make_order(1605, checkout_token="chk5", source_name="subscription_contract"),   # MRR is no purchase
    ]
    body = client.get("/hub/api/funnel?range=today", headers=API).json()
    # Every buyer counts at every step, so no step is ever more than the one above it.
    assert body["meta"] == [1, 1, 1, 1, 1] and body["other"] == [3, 2, 2, 2, 2]
    for group in (body["meta"], body["other"], body["all"]):
        assert all(a >= b for a, b in zip(group, group[1:])), group
    assert body["untied_sales"] == 1                               # #c1604: the pixel never saw its shopper
    assert "no step is more than the one above it" in body["note"] and chr(0x2014) not in body["note"]


# =====================================================================================
# Assisted sales section
# =====================================================================================

def test_assists_section_lists_each_ad_that_sold_and_the_ads_before_it(client, shop):
    def ad(ad_id, name, adset="TOF 1"):
        return {"ad_id": ad_id, "ad_name": name, "adset_name": adset, "campaign_name": "sperm", "at": time.time() - 600}
    ugc, hook, static = ad("A1", "UGC 1", "MOF 3"), ad("A2", "Hook 2"), ad("A3", "Static 3")
    seller = {k: ugc[k] for k in ("ad_id", "ad_name", "adset_name", "campaign_name")}
    shop.orders = [
        credited(1701, "60.00", **seller, assists=[hook, static]),
        # Hook 2 again by name only is the same ad; the seller is never its own assist.
        credited(1702, "40.00", **seller, assists=[hook, {**hook, "ad_id": "", "ad_name": "hook 2"}, ugc]),
        credited(1703, "30.00", ad_id="A4", ad_name="Carousel", assists=[hook]),
        credited(1704, "25.00", ad_id="A4", ad_name="Carousel"),                           # no help
        # A link with the name only: the one ad that goes by "UGC 1".
        credited(1705, "20.00", ad_id="A5", ad_name="Founder", assists=[{**ugc, "ad_id": ""}]),
        credited(1706, "15.00", assists=[static]),                                          # seller unnamed
        credited(1707, "10.00", ad_id="A2", ad_name="Hook 2", assists=[hook]),              # only itself
        make_order(1708, total_price="99.00"),                                              # not from an ad
        make_order(1709, total_price="39.00", source_name="subscription_contract"),
    ]
    r = client.get("/hub/api/assists?range=today", headers=API)
    body = r.json()
    assert body["error"] == "" and body["sales_without_assists"] == 2 and body["currency"] == "USD"
    # One row per ad that assisted: how many new sales it helped, and the ads that closed them.
    got = [(x["ad_id"], x["ad_name"], x["assists"], [(c["ad_id"], c["ad_name"], c["sales"], c["value"])
                                                    for c in x["closers"]]) for x in body["rows"]]
    assert got == [("A2", "Hook 2", 3, [("A1", "UGC 1", 2, 100.0), ("A4", "Carousel", 1, 30.0)]),
                   # Changed on purpose (F5): a closer with no name at all is a Meta ad whose name is unknown.
                   ("A3", "Static 3", 2, [("A1", "UGC 1", 1, 60.0), ("", "Meta ad (name unknown)", 1, 15.0)]),
                   # Named by the link only: the one ad that goes by "UGC 1".
                   ("A1", "UGC 1", 1, [("A5", "Founder", 1, 20.0)])]
    top = body["rows"][0]
    assert (top["adset_name"], top["campaign_name"], top["spend"]) == ("TOF 1", "sperm", None)   # ads not connected
    assert top["closers"][0] == {"ad_id": "A1", "ad_name": "UGC 1", "adset_name": "MOF 3", "sales": 2, "value": 100.0}
    assert set(top) == {"ad_id", "ad_name", "adset_name", "campaign_name", "spend", "assists", "closers"}
    assert body["ads_connected"] is False
    assert "Sep 27, 2026" in body["note"] and chr(0x2014) not in body["note"]
    assert exposed(r) == []
    # Another day: nothing yet. Shopify down: unknown, not "no assisted sales".
    empty = client.get("/hub/api/assists?range=yesterday", headers=API).json()
    assert empty["rows"] == [] and empty["sales_without_assists"] == 0
    shop.fail = 403
    hub._orders_cache.clear()
    down = client.get("/hub/api/assists?range=today", headers=API).json()
    assert down["rows"] == [] and down["sales_without_assists"] is None and "Shopify answered 403" in down["error"]


def test_no_earlier_ad_click_is_only_counted_where_click_history_exists(client, shop, monkeypatch):
    since = time.time() - 3600
    monkeypatch.setattr(hub, "ASSISTS_FROM", since)

    def sale(oid, ts, **credit):
        """A sale the tracker credited to a Meta ad when it was placed, at `ts`."""
        o = make_order(oid, ts=ts, total_price="50.00")
        db.upsert_order(o)
        db.mark_order(str(oid), "sent", kind="purchase")
        db.set_order_attribution(str(oid), {"meta": True, "source": "browser", "click": True, **credit})
        return o
    ugc = {"ad_id": "A1", "ad_name": "UGC 1"}
    shop.orders = [
        sale(1801, time.time() - 120, **ugc),                                          # no earlier ad click
        sale(1802, time.time() - 120, **ugc, assists=[visit("A2", "Hook 2", since + 60)]),
        # Placed before click history started: whether an ad helped it can't be known.
        sale(1803, since - 600, **ugc),
        # Not credited by the tracker (yet): its ad comes from the landing page alone, its clicks are unknown.
        make_order(1804, landing_site="/?utm_source=facebook&ad_id=A1&utm_content=UGC%201"),
    ]
    body = client.get("/hub/api/assists?range=7d", headers=API).json()
    assert [(r["ad_id"], r["assists"], r["closers"][0]["ad_id"], r["closers"][0]["value"]) for r in body["rows"]] == [
        ("A2", 1, "A1", 50.0)]
    assert body["sales_without_assists"] == 1
    # The range starts before click history did, so the page says since when it counts.
    assert body["sales_without_assists_since"] == hub._time_local(dt.datetime.fromtimestamp(since, config.store_tz()))
    # A range that starts after click history did: every sale the tracker credited counts, no "since".
    monkeypatch.setattr(hub, "ASSISTS_FROM", 0.0)
    body = client.get("/hub/api/assists?range=7d", headers=API).json()
    assert body["sales_without_assists"] == 2 and body["sales_without_assists_since"] == ""


# =====================================================================================
# Batch 4B: the P&L section (the P&L app's own numbers, through /hub/api/pnl)
# =====================================================================================

# The P&L page's script as the live app serves it (We Tracked is cancelled there).
PNL_PAGE = """<script>
const SW_TOOLS = [
  {name:'Luxury Tools (1 acct)', monthly:29.53, freq:'monthly'},
  // {name:'We Tracked',      monthly:52.73, freq:'monthly'},
  {name:'Zoho',            monthly:1.23,  annual:14.76, freq:'annual'},
  {name:"Software",        monthly:139.90, freq:'monthly'},
];
const DAYS_PER_MONTH = 30.44;
</script>"""


def pnl_block(**over):
    """Part of a P&L block as /api/pnl sends it."""
    b = {"kpi": {"revenue": 500.0, "revenue_new": 400.0, "revenue_recurring": 100.0, "cogs_recurring": 20.0,
                 "orders_recurring": 3, "net_provisional": False, "aov_recurring": None},
         "revenue": {"new": 400.0, "recurring": 100.0, "total": 500.0}, "orders": {"new": 8, "recurring": 3, "total": 11},
         "cogs": {"new": 80.0, "recurring": 20.0, "total": 100.0, "coverage": 1},
         "fees": {"processing": {"total": 15.0}, "conversion": {"total": 2.0}, "total": {"new": 13.0, "total": 17.0}},
         "net": {"value": 200.0, "provisional": False, "provisional_reason": None},
         "subs": {"active": 40, "mrr_runrate": 1600.0}, "mrr_at_risk": 120.0, "overdue_subs": 3, "be_roas": 1.8,
         "spend": 150.0, "cac_per_new_sub": None, "status": "tracked", "badges": ["new"],
         "by_day": [{"date": "2026-09-27", "revenue": 500.0, "revenue_new": 400.0, "revenue_recurring": 100.0,
                     "cogs": 100.0, "fees": 17.0, "spend": 150.0, "orders": 11}],
         "by_variant": [{"product_title": "SpermFuel+", "variant_title": "1 bottle", "packs": 4, "cogs": 50.0}],
         "campaigns": [{"campaign_id": "C9", "campaign_name": "sperm", "spend": 150.0, "status": "mapped",
                        "confidence": 1, "meta_status": "ACTIVE"}]}
    b.update(over)
    return b


def pnl_payload(meta_synced_minutes_ago=2):
    ran = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=meta_synced_minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    everything = pnl_block()
    # Nothing like these is in the P&L today; if it ever is, the whitelist keeps it out.
    everything.update(customer={"email": "jane.doe@example.com"}, note="Jané, L6M 5P6")
    return {"version": 2, "period": {"from": "2026-09-27", "to": "2026-09-27"}, "generated_at": "2026-09-28T01:06:10Z",
            "scope": {"product": "all", "title": "All products"}, "all": everything,
            "products": [pnl_block(product_id="111", title="SpermFuel+", qty_mode="packs", tracked_reason="ads")],
            "unattributed": pnl_block(products=[{"product_id": "222", "title": "Gift", "orders": 1, "why": "organic"}],
                                      campaigns=[{"campaign_id": "C8", "campaign_name": "old", "spend": 5.0,
                                                  "status": "unmapped"}]),
            "store": {"shipping_revenue": 3.29, "chargebacks": 0, "fee_adjustment": 1.5, "fees_source": "modelled",
                      "platform_bills": 12.0, "platform_bill_count": 2, "platform_bills_cad": 16.4, "net_final": 404.29,
                      "payouts_note": "payouts run 2026-05-28..2026-09-18"},
            "reconcile": {"revenue_ok": True}, "audit_summary": {"status": "warn", "errors": 0},
            "last_sync": {"shopify": {"ran_at": ran, "status": "ok"}, "meta": {"ran_at": ran, "status": "ok"},
                          "products": None},
            "totals": {"revenue": 500.0}, "by_product": [{"product_name": "SpermFuel+"}], "meta_spend": [],
            "currency_breakdown": [], "by_day": everything["by_day"],
            "orders": [{"name": "#c1", "email": "jane.doe@example.com", "phone": "555-0199"}]}


class FakePnl:
    """The P&L app: its page, /api/pnl, /api/state, /api/last-sync and the Meta sync."""

    def __init__(self):
        self.payload, self.page = pnl_payload(), PNL_PAGE
        self.state = {"updated_at": "2026-09-27 20:03:35", "state": {"manualMigrated": True, "manual": {
            "all": {"revenue": [{"id": "old", "name": "Old Store", "val": 4639, "migrated": True}], "cogs": [],
                    "ads": [{"id": "a1", "name": "TikTok test", "val": "12.50", "date": "2026-09-27", "auto": False}],
                    "opex": []},
            "15292558639357": {"revenue": [{"id": "x", "name": "Not the all scope", "val": 1}]}}}}
        self.last_sync = {"meta_rate_limited_until": None}
        self.fail, self.requests = {}, []

    def handler(self, request: httpx.Request):
        self.requests.append(request)
        path = request.url.path
        fail = self.fail.get(path)
        if isinstance(fail, Exception):
            raise fail
        if fail:
            return httpx.Response(fail, text="Bad gateway")
        if path == "/api/pnl":
            return httpx.Response(200, json=self.payload)
        if path == "/api/state":
            return httpx.Response(200, json=self.state)
        if path == "/api/last-sync":
            return httpx.Response(200, json=self.last_sync)
        if path == "/api/sync/meta" and request.method == "POST":
            return httpx.Response(200, json={"ok": True, "rows": 3})
        if path == "/":
            return httpx.Response(200, text=self.page)
        return httpx.Response(404, json={})

    def reads(self, path):
        return [q for q in self.requests if q.url.path == path and q.method == "GET"]


@pytest.fixture
def pnl_app(monkeypatch):
    fake = FakePnl()
    monkeypatch.setattr(config, "PNL_URL", "https://pnl.example")
    monkeypatch.setattr(pnl, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


def test_the_pnl_section_reads_the_pnl_trimmed_to_what_its_page_reads(client, pnl_app, monkeypatch):
    today = pnl.today()
    r = client.get("/hub/api/pnl", headers=API)
    body = r.json()
    assert (body["ok"], body["error"], body["pnl_url"]) == (True, "", "https://pnl.example")
    assert body["creative_url"] == config.CREATIVE_URL          # the Creatives tab's app comes with it
    assert body["core_hub_url"] == config.CORE_HUB_URL and "key=" not in config.CORE_HUB_URL   # no key in the code
    assert body["range"] == {"from": today, "to": today}                     # today in New York by default
    (q,) = pnl_app.reads("/api/pnl")
    assert dict(q.url.params) == {"from": today, "to": today, "product": "all"}
    p = body["pnl"]
    assert set(p) == {"version", "period", "generated_at", "all", "products", "unattributed", "store", "by_day"}
    a = p["all"]
    # The fields the copied code reads, as the P&L sent them.
    assert a["kpi"] == {"revenue": 500.0, "revenue_new": 400.0, "revenue_recurring": 100.0, "cogs_recurring": 20.0,
                        "orders_recurring": 3, "net_provisional": False, "aov_recurring": None}
    assert a["revenue"] == {"new": 400.0, "recurring": 100.0, "total": 500.0} and a["cogs"]["recurring"] == 20.0
    assert a["fees"]["processing"] == {"total": 15.0} and a["net"] == {"value": 200.0, "provisional": False}
    assert (a["mrr_at_risk"], a["overdue_subs"], a["be_roas"], a["cac_per_new_sub"]) == (120.0, 3, 1.8, None)
    assert a["by_day"][0]["date"] == "2026-09-27" and p["by_day"] == a["by_day"]
    for gone in ("status", "badges", "by_variant", "campaigns", "customer", "note"):
        assert gone not in a, gone
    (prod,) = p["products"]
    assert set(prod) == {"product_id", "title", "revenue", "orders", "by_variant", "campaigns"}
    assert prod["by_variant"] == [{"variant_title": "1 bottle", "packs": 4, "cogs": 50.0}]
    assert prod["campaigns"] == [{"campaign_id": "C9", "campaign_name": "sperm", "spend": 150.0}]
    u = p["unattributed"]
    assert set(u) == {"revenue", "cogs", "products", "campaigns"} and u["products"] == [{"product_id": "222"}]
    assert u["campaigns"] == [{"campaign_id": "C8", "campaign_name": "old", "status": "unmapped", "spend": 5.0}]
    assert p["store"] == {"shipping_revenue": 3.29, "chargebacks": 0, "fee_adjustment": 1.5, "fees_source": "modelled",
                          "platform_bills": 12.0, "platform_bill_count": 2, "platform_bills_cad": 16.4}
    # The owner's own lines (the all-products scope only) and the software the P&L page lists.
    assert body["manual"] == {"revenue": [{"id": "old", "name": "Old Store", "val": 4639.0}], "cogs": [],
                              "ads": [{"id": "a1", "name": "TikTok test", "val": 12.5, "date": "2026-09-27"}],
                              "opex": []}
    assert body["manual_error"] == ""
    assert (body["sw_tools_source"], body["days_per_month"]) == ("pnl", 30.44)
    assert body["sw_tools"] == [{"name": "Luxury Tools (1 acct)", "monthly": 29.53, "freq": "monthly"},
                                {"name": "Zoho", "monthly": 1.23, "freq": "annual", "annual": 14.76},
                                {"name": "Software", "monthly": 139.9, "freq": "monthly"}]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", body["last_sync"]["meta"]["ran_at"])
    assert body["last_sync"]["meta"]["status"] == "ok" and body["last_sync"]["products"] is None
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", body["fetched_at"]) and body["meta_sync_started"] is False
    assert exposed(r) == [] and "#c1" not in r.text and "reconcile" not in r.text
    # A minute per range: the next load asks nothing; the P&L's "All" is its own read.
    client.get("/hub/api/pnl", headers=API)
    assert len(pnl_app.reads("/api/pnl")) == 1 and len(pnl_app.reads("/api/state")) == 1
    assert len(pnl_app.reads("/")) == 1
    assert client.get(f"/hub/api/pnl?from=2000-01-01&to={today}", headers=API).json()["ok"] is True
    assert pnl_app.reads("/api/pnl")[-1].url.params["from"] == "2000-01-01"
    assert all("authorization" not in q.headers for q in pnl_app.requests)
    # Once the P&L asks for a key it goes in a header, never in the URL or the answer.
    monkeypatch.setattr(config, "PNL_API_KEY", "pnl-secret")
    pnl.reset()
    r = client.get("/hub/api/pnl", headers=API)
    assert r.json()["ok"] and "pnl-secret" not in r.text
    assert all(q.headers["authorization"] == "Bearer pnl-secret" and "pnl-secret" not in str(q.url)
               for q in pnl_app.requests[-3:])


def test_the_pnl_section_says_so_when_the_pnl_does_not_answer(client, pnl_app):
    for fail, error in ((httpx.ConnectTimeout("slow"), "The P&L server did not answer"),
                        (502, "The P&L server did not answer (it said 502)"),
                        (httpx.ConnectError("down"), "The P&L server did not answer")):
        pnl_app.fail["/api/pnl"] = fail
        body = client.get("/hub/api/pnl", headers=API).json()
        # Never zeros: no numbers at all, and the reason in plain words.
        assert (body["ok"], body["error"]) == (False, error) and "pnl" not in body and "manual" not in body
        assert body["pnl_url"] == "https://pnl.example" and body["range"]["from"] == pnl.today()
    pnl_app.fail = {}
    pnl_app.payload = {"version": 1, "totals": {"revenue": 500.0}}             # an old P&L the page can't read
    body = client.get("/hub/api/pnl", headers=API).json()
    assert body["ok"] is False and body["error"].startswith("The P&L server did not answer")
    # Its page or its saved lines failing doesn't hide the P&L: the copied software list
    # stands in, and the lines are said to be left out.
    pnl.reset()
    pnl_app.payload = pnl_payload()
    pnl_app.fail = {"/": 500, "/api/state": httpx.ReadTimeout("slow")}
    body = client.get("/hub/api/pnl", headers=API).json()
    assert body["ok"] is True and body["sw_tools_source"] == "copy"
    assert body["sw_tools"] == pnl.SW_TOOLS_COPY and body["days_per_month"] == 30.44
    assert body["manual"] is None and "left out" in body["manual_error"]
    # Lines read once keep showing when a later read fails.
    pnl.reset()
    pnl_app.fail = {}
    assert client.get("/hub/api/pnl", headers=API).json()["manual"]["revenue"][0]["name"] == "Old Store"
    pnl._cache.pop("state")
    pnl_app.fail = {"/api/state": 500}
    body = client.get("/hub/api/pnl", headers=API).json()
    assert body["manual"]["revenue"][0]["name"] == "Old Store" and body["manual_error"] == ""
    # So does the software list read once: the P&L's own list, not the older copy.
    pnl._cache.pop("software")
    pnl_app.fail = {"/": 500}
    body = client.get("/hub/api/pnl", headers=API).json()
    assert body["sw_tools_source"] == "pnl" and [t["name"] for t in body["sw_tools"]] == [
        "Luxury Tools (1 acct)", "Zoho", "Software"]
    for text in (body["manual_error"], hub.PNL_BAD_RANGE, pnl.UNAVAILABLE):
        assert chr(0x2014) not in text


def test_the_pnl_section_checks_its_dates(client, pnl_app):
    today = pnl.today()
    for query in ("from=2026-02-30", "from=27-09-2026", "to=tomorrow", f"from={today}&to=2026-01-01",
                  "from=1999-12-31&to=2000-01-05", "from=2026-9-1"):
        body = client.get(f"/hub/api/pnl?{query}", headers=API).json()
        assert (body["ok"], body["error"]) == (False, hub.PNL_BAD_RANGE), query
    assert pnl_app.requests == []                                             # nothing was asked
    body = client.get("/hub/api/pnl?from=2026-09-01&to=2026-09-27", headers=API).json()
    assert body["ok"] and body["range"] == {"from": "2026-09-01", "to": "2026-09-27"}


def test_the_pnl_section_keeps_the_pnls_meta_spend_fresh_like_its_page(client, pnl_app, monkeypatch):
    started = []
    monkeypatch.setattr(tracking, "fire_and_forget", lambda coro: started.append(coro))
    today = pnl.today()
    pnl_app.payload = pnl_payload(meta_synced_minutes_ago=20)
    body = client.get("/hub/api/pnl", headers=API).json()
    assert body["ok"] and body["meta_sync_started"] is True and len(started) == 1
    assert pnl_app.reads("/api/last-sync") == []                              # the page load didn't wait on it
    assert asyncio.run(started[0]) == "sent"
    (post,) = [q for q in pnl_app.requests if q.method == "POST"]
    assert post.url.path == "/api/sync/meta" and json.loads(post.content) == {"from": today, "to": today}
    # At most once every 15 minutes, whatever the range.
    pnl._cache.clear()
    week = (dt.date.fromisoformat(today) - dt.timedelta(days=6)).isoformat()
    assert client.get(f"/hub/api/pnl?from={week}&to={today}", headers=API).json()["meta_sync_started"] is False
    # Meta spend synced 5 minutes ago, or a range without today: nothing to top up.
    pnl.reset()
    pnl_app.payload = pnl_payload(meta_synced_minutes_ago=5)
    assert client.get("/hub/api/pnl", headers=API).json()["meta_sync_started"] is False
    pnl.reset()
    pnl_app.payload = pnl_payload(meta_synced_minutes_ago=20)
    yesterday = (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
    assert client.get(f"/hub/api/pnl?from={yesterday}&to={yesterday}", headers=API).json()["meta_sync_started"] is False
    # Meta is rate limiting the P&L: it isn't asked.
    pnl_app.last_sync = {"meta_rate_limited_until": at(time.time() + 3600)}
    assert client.get("/hub/api/pnl", headers=API).json()["meta_sync_started"] is True
    assert asyncio.run(started[-1]) == "rate_limited" and len([q for q in pnl_app.requests if q.method == "POST"]) == 1
    assert len(started) == 2
    # The P&L not answering the sync is logged, never raised.
    pnl_app.last_sync = {"meta_rate_limited_until": None}
    pnl_app.fail["/api/sync/meta"] = httpx.ConnectError("down")
    assert asyncio.run(pnl.sync_meta(today, today)) == "failed"


def test_all_time_on_the_pnl_asks_for_a_meta_sync_of_the_last_week_only(client, pnl_app, monkeypatch):
    # "All" starts in 2000: Meta keeps 37 months, and the P&L would try every month since.
    started = []
    monkeypatch.setattr(tracking, "fire_and_forget", lambda coro: started.append(coro))
    today = pnl.today()
    week = (dt.date.fromisoformat(today) - dt.timedelta(days=7)).isoformat()
    pnl_app.payload = pnl_payload(meta_synced_minutes_ago=20)
    body = client.get(f"/hub/api/pnl?from=2000-01-01&to={today}", headers=API).json()
    assert body["ok"] and body["range"] == {"from": "2000-01-01", "to": today} and body["meta_sync_started"] is True
    assert pnl_app.reads("/api/pnl")[-1].url.params["from"] == "2000-01-01"      # the numbers are still all time
    assert asyncio.run(started[0]) == "sent"
    (post,) = [q for q in pnl_app.requests if q.method == "POST"]
    assert json.loads(post.content) == {"from": week, "to": today}
    # A range inside the last week is synced as it is; an older start is cut to the last week.
    assert pnl.sync_from(today) == today and pnl.sync_from(week) == week and pnl.sync_from("2020-01-01") == week


def test_the_pnl_pages_software_list_is_read_carefully():
    tools, days = pnl.parse_software(PNL_PAGE)
    assert [t["name"] for t in tools] == ["Luxury Tools (1 acct)", "Zoho", "Software"] and days == 30.44
    # Anything unclear and the copy stands in, rather than a guess.
    for broken in (PNL_PAGE.replace("const DAYS_PER_MONTH = 30.44;", ""),
                   PNL_PAGE.replace("monthly:29.53", "monthly:lots"),
                   PNL_PAGE.replace("30.44", "300"),
                   "<script>const SW_TOOLS = [];\nconst DAYS_PER_MONTH = 30.44;</script>", "", "<html>maintenance</html>"):
        assert pnl.parse_software(broken) is None
    # Changed on purpose: the copy is the live P&L page's list, We Tracked cancelled ($205.11 a month).
    assert sum(t["monthly"] for t in pnl.SW_TOOLS_COPY) == pytest.approx(205.11) and pnl.DAYS_PER_MONTH_COPY == 30.44
    assert "We Tracked" not in [t["name"] for t in pnl.SW_TOOLS_COPY]


# =====================================================================================
# Batch 4B: assists by assisting ad, the $15 line, the funnel and the listicle
# =====================================================================================

def test_assists_show_each_assisting_ads_spend_and_the_ads_that_closed(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    meta.ad_rows = [insight("A1", "UGC 1", 80, adset_name="MOF 3", campaign_name="sperm"),
                    insight("A2", "Hook 2", 40, adset_name="TOF 1", campaign_name="sperm"),
                    insight("A3", "Static 3", 55.5, adset_name="TOF 1", campaign_name="sperm")]

    def ad(ad_id, name):
        return {"ad_id": ad_id, "ad_name": name, "adset_name": "TOF 1", "campaign_name": "sperm",
                "at": time.time() - 600}
    shop.orders = [
        credited(1901, "60.00", ad_id="A1", ad_name="UGC 1", assists=[ad("A2", "Hook 2"), ad("A3", "Static 3")]),
        # A link that named the ad only: the one delivering ad of that name.
        credited(1902, "40.00", ad_id="A1", ad_name="UGC 1", assists=[ad("", "static 3")]),
        credited(1903, "30.00", ad_id="A2", ad_name="Hook 2", assists=[ad("A9", "Paused hook")]),
        make_order(1904, total_price="39.00", source_name="subscription_contract"),     # MRR never counts
    ]
    r = client.get("/hub/api/assists?range=today", headers=API)
    body = r.json()
    # Most assists first; the same count goes by spend. An ad with no delivery spent $0.
    assert [(x["ad_id"], x["ad_name"], x["adset_name"], x["assists"], x["spend"]) for x in body["rows"]] == [
        ("A3", "Static 3", "TOF 1", 2, 55.5), ("A2", "Hook 2", "TOF 1", 1, 40.0), ("A9", "Paused hook", "TOF 1", 1, 0.0)]
    # Both of Static 3's sales were closed by UGC 1: one entry, 2 sales, their value.
    assert body["rows"][0]["closers"] == [{"ad_id": "A1", "ad_name": "UGC 1", "adset_name": "MOF 3", "sales": 2,
                                           "value": 100.0}]
    assert body["rows"][2]["closers"] == [{"ad_id": "A2", "ad_name": "Hook 2", "adset_name": "TOF 1", "sales": 1,
                                           "value": 30.0}]
    assert body["ads_connected"] is True and body["error"] == "" and exposed(r) == []
    # Spend read from Meta by the ad's id; a name-only ad, in the ad set its link named.
    built = hub.build_assists(
        [_sale(1, 50.0, ad_id="A1", assists=[{"ad_id": "", "ad_name": "Twin", "adset_name": "Set B"}])], {}, 0.0,
        [{"ad_id": "T1", "ad_name": "Twin", "adset_name": "Set A", "spend": 20.0},
         {"ad_id": "T2", "ad_name": "Twin", "adset_name": "Set B", "spend": 7.0}], spend_known=True)
    assert [(x["ad_id"], x["spend"]) for x in built["rows"]] == [("", 7.0)]


def test_only_creatives_with_a_sale_get_a_row_and_the_rest_share_one_line():
    assert config.HUB_MIN_AD_SPEND == 15.0                                # the owner's default

    def r(ad_id, adset_id, adset, spend, campaign=("C1", "sperm"), **over):
        return {"campaign_id": campaign[0], "campaign_name": campaign[1], "ad_id": ad_id, "ad_name": f"Ad {ad_id}",
                "adset_id": adset_id, "adset_name": adset, "spend": float(spend), "meta_purchases": 0.0,
                "meta_value": 0.0, **over}
    # Changed on purpose: only an ad with a sale (the store's or Meta's) gets a row.
    # Spend and add to carts don't; the rest share one line.
    rows = [r("A1", "S1", "B1 Rips", 50), r("A2", "S1", "B1 Rips", 15),                # exactly $15 gets a row
            r("A3", "S1", "B1 Rips", 9.5, meta_purchases=1.0, meta_value=59.95),      # a Meta sale
            r("A4", "S1", "B1 Rips", 0, meta_add_to_carts=1.0),                       # an add to cart
            r("A5", "S2", "B2 Statics", 12), r("A6", "S2", "B2 Statics", 2.5),        # A6 has a store sale
            r("A8", "S2", "B2 Statics", 1.5),
            r("A7", "S3", "TOF", 4, campaign=("C2", "leggings"))]
    facts = [_sale(1, 60.0, ad_id="A1", ad_name="Ad A1", lp="listicle-v2-one-line"),
             _sale(2, 40.0, ad_id="A1", ad_name="Ad A1"),
             _sale(3, 30.0, ad_id="A3", ad_name="Ad A3", lp="listicle", ids_stripped=True),
             _sale(4, 20.0, ad_id="A6", ad_name="Ad A6")]
    built = hub.build_creatives(facts, rows, "adset", min_spend=15.0)
    sperm, leggings = built["campaigns"]
    groups = {g["name"]: g for g in sperm["groups"]}
    rips, statics = groups["B1 Rips"], groups["B2 Statics"]
    assert [a["ad_id"] for a in rips["ads"]] == ["A1", "A3"]
    assert rips["small"] == {"count": 2, "spend": 15.0, "store_sales": 0, "store_revenue": 0.0,
                             "meta_purchases": 0, "meta_value": 0.0}                 # A2 ($15) and A4 (an add to cart)
    assert [a["ad_id"] for a in statics["ads"]] == ["A6"]
    assert statics["small"] == {"count": 2, "spend": 13.5, "store_sales": 0, "store_revenue": 0.0,
                                "meta_purchases": 0, "meta_value": 0.0}
    assert sperm["small"]["count"] == 0
    # The rows and the lines add up to the totals, which still count every ad.
    assert rips["spend"] == 50 + 9.5 + rips["small"]["spend"] and statics["spend"] == 2.5 + statics["small"]["spend"]
    assert sperm["spend"] == rips["spend"] + statics["spend"]
    assert (sperm["store_sales"], sperm["store_revenue"]) == (4, 150.0)
    assert leggings["groups"] == [] and leggings["small"]["count"] == 1 and leggings["spend"] == 4
    assert built["totals"]["spend"] == 94.5 and built["totals"]["store_sales"] == 4
    # How many of an ad's sales came through a listicle.
    ads = {a["ad_id"]: a for a in rips["ads"]}
    assert (ads["A1"]["store_sales"], ads["A1"]["via_listicle"]) == (2, 1)
    assert ads["A3"]["via_listicle"] == 1
    # The rule never depended on a minimum: without one, still only the sellers have rows.
    every = hub.build_creatives(facts, rows, "adset")
    assert sum(len(g["ads"]) for c in every["campaigns"] for g in c["groups"]) == 3
    assert sum(g["small"]["count"] for c in every["campaigns"] for g in c["groups"]) +         sum(c["small"]["count"] for c in every["campaigns"]) == 5


def test_creatives_apply_the_minimum_only_when_all_spend_was_read(client, shop, meta, monkeypatch):
    seed(shop)
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    meta.ad_rows = ad_rows()                          # AD9 spent $10: into the line
    body = client.get("/hub/api/creatives?range=today", headers=API).json()
    assert body["min_ad_spend"] == 15.0
    cbo = next(c for c in body["campaigns"] if c["campaign_name"] == "Leggings CBO")
    assert [g["name"] for g in cbo["groups"]] == ["Broad"] and cbo["small"]["count"] == 1 and cbo["spend"] == 70
    # Spend not read (one account failing): nothing is folded away on a guess.
    meta.denied.add("123")
    meta_ads._cache.clear()
    body = client.get("/hub/api/creatives?range=today", headers=API).json()
    assert body["connected"] is False and body["min_ad_spend"] is None
    # Still only sellers have rows: a row never comes from spend.
    assert all(a["store_sales"] > 0 or a["meta_purchases"] > 0
               for c in body["campaigns"] for g in c["groups"] for a in g["ads"])


def test_funnel_splits_ad_shoppers_by_the_listicle_and_the_feed_badges_them(client, shop):
    now = time.time()
    arrivals = {"b-lp": {"utm_source": "facebook", "ad_id": "A1", "lp": "listicle-v2-one-line"},
                "b-lp2": {"utm_source": "facebook", "ad_id": "A1", "lp": "listicle-v2-one-line"},
                # The old listicle forwarded the utm tags without any ad id.
                "b-old": {"utm_source": "facebook", "utm_content": "B1 Rips", "utm_term": "2", "fbclid": "1"},
                "b-pdp": {"utm_source": "facebook", "ad_id": "A2", "adset_id": "S2", "campaign_id": "C1"},
                "b-pdp2": {"utm_source": "facebook", "ad_id": "A2", "adset_id": "S2", "campaign_id": "C1"}}
    for cid, params in arrivals.items():
        db.upsert_session(cid, ad_params=json.dumps(params), ad_seen_at=now - 60)
    db.upsert_session("b-organic", fbp="fb.1.1.9")
    for cid in [*arrivals, "b-organic"]:
        db.record_event("PageView", f"{cid}-pv", "pixel", "sent", {"user_data": {}}, client_id=cid)
    shop.orders = [credited(2001, "60.00", ad_id="A1", ad_name="UGC 1", lp="listicle-v2-one-line"),
                   credited(2002, "40.00", ad_id="A1", ad_name="UGC 1", lp="listicle", ids_stripped=True),
                   credited(2003, "30.00", ad_id="A2", ad_name="Hook 2"),
                   make_order(2004, total_price="99.00"),
                   make_order(2005, total_price="39.00", source_name="subscription_contract")]
    body = client.get("/hub/api/funnel?range=today", headers=API).json()
    rows = {r["key"]: r for r in body["listicle"]["rows"]}
    # Changed on purpose: the conversion rate is the share of these visitors who bought (tied by
    # checkout). None of them did; the credited sales came from buyers the pixel never saw.
    # Changed on purpose (F2): "Product page" first, then "Listicle"; (Oct 6 2026) then "Quiz Funnel".
    assert [r["key"] for r in body["listicle"]["rows"]] == ["direct", "listicle", "quiz"]
    assert rows["listicle"] == {"key": "listicle", "label": "Listicle", "visitors": 3, "sales": 2,
                                "revenue": 100.0, "conversion": 0, "assists": None}
    assert rows["direct"] == {"key": "direct", "label": "Product page", "visitors": 2, "sales": 1,
                              "revenue": 30.0, "conversion": 0, "assists": None}
    assert rows["quiz"] == {"key": "quiz", "label": "Quiz Funnel", "visitors": 0, "sales": 0, "revenue": 0,
                            "conversion": None, "assists": 0, "assist_revenue": 0}
    note = body["listicle"]["note"]
    assert note.count(".") == 1 and note.endswith(".") and len(note) < 120            # one plain sentence
    assert body["meta"][0] == 5 and body["other"][0] == 1 and body["untied_sales"] == 4
    assert chr(0x2014) not in body["listicle"]["note"]
    # One listicle visitor placed #c2001: one of three bought, whatever the credited sales say.
    db.upsert_session("b-lp", checkout_token="chk2001")
    body = client.get("/hub/api/funnel?range=today", headers=API).json()
    rows = {r["key"]: r for r in body["listicle"]["rows"]}
    assert (rows["listicle"]["visitors"], rows["listicle"]["sales"], rows["listicle"]["conversion"]) == (3, 2, 0.3333)
    assert rows["direct"]["conversion"] == 0 and body["untied_sales"] == 3 and body["meta"][4] == 1
    orders = {o["id"]: o["listicle"] for o in client.get("/hub/api/orders?range=today", headers=API).json()["orders"]}
    assert [orders[i] for i in ("2001", "2002", "2003", "2004", "2005")] == [True, True, False, False, False]
    # Shopify down: visitors still count, the sales are unknown rather than none.
    shop.fail = 403
    hub._orders_cache.clear()
    body = client.get("/hub/api/funnel?range=today", headers=API).json()
    assert [(r["visitors"], r["sales"], r["revenue"], r["conversion"]) for r in body["listicle"]["rows"]] == [
        (2, None, None, None), (3, None, None, None), (0, None, None, None)]
    assert body["untied_sales"] is None and body["meta"][4] is None and body["all"][4] is None


def test_quiz_funnel_sales_and_the_quizs_assists_on_listicle_sales(client, shop):
    # The quiz funnel (Oct 6 2026): straight to the store with lp=quiz is a quiz sale; through the listicle
    # (which passes on the quiz's via=quiz) is a listicle sale the quiz assisted.
    now = time.time()
    arrivals = {"b-quiz": {"utm_source": "facebook", "ad_id": "A1", "lp": "quiz"},
                "b-quiz-lst": {"utm_source": "facebook", "ad_id": "A1", "lp": "ranking-listicle", "via": "quiz"},
                "b-lst": {"utm_source": "facebook", "ad_id": "A2", "lp": "ranking-listicle"}}
    for cid, params in arrivals.items():
        db.upsert_session(cid, ad_params=json.dumps(params), ad_seen_at=now - 60)
        db.record_event("PageView", f"{cid}-pv", "pixel", "sent", {"user_data": {}}, client_id=cid)
    shop.orders = [credited(3001, "60.00", ad_id="A1", ad_name="Quiz ad", lp="quiz"),
                   credited(3002, "40.00", ad_id="A1", ad_name="Quiz ad", lp="ranking-listicle", via="quiz"),
                   credited(3003, "30.00", ad_id="A2", ad_name="Lyst ad", lp="ranking-listicle")]
    body = client.get("/hub/api/funnel?range=today", headers=API).json()
    rows = {r["key"]: r for r in body["listicle"]["rows"]}
    # Changed on purpose (Oct 7 2026): the quiz's visitors are everyone who started in it, through the
    # listicle too (same ad click); its sales stay the ones straight from it.
    assert (rows["quiz"]["visitors"], rows["quiz"]["sales"], rows["quiz"]["revenue"]) == (2, 1, 60.0)
    assert (rows["quiz"]["assists"], rows["quiz"]["assist_revenue"]) == (1, 40.0)
    assert (rows["listicle"]["visitors"], rows["listicle"]["sales"], rows["listicle"]["revenue"]) == (2, 2, 70.0)
    assert rows["direct"]["sales"] == 0
    # The shopper the quiz sent through the listicle buys: a conversion for the quiz and for the listicle.
    db.upsert_session("b-quiz-lst", checkout_token="chk3002")
    rows = {r["key"]: r for r in client.get("/hub/api/funnel?range=today", headers=API).json()["listicle"]["rows"]}
    assert (rows["quiz"]["conversion"], rows["listicle"]["conversion"], rows["direct"]["conversion"]) == (0.5, 0.5, None)
    assert rows["quiz"]["sales"] == 1 and rows["listicle"]["sales"] == 2                 # each sale counted once
    orders = {o["id"]: o for o in client.get("/hub/api/orders?range=today", headers=API).json()["orders"]}
    assert [(orders[i]["landing"], orders[i]["listicle"], orders[i]["quiz_assist"]) for i in ("3001", "3002", "3003")] == [
        ("quiz", False, False), ("listicle", True, True), ("listicle", True, False)]


def test_funnel_says_since_when_it_has_been_counting(client, shop):
    assert client.get("/hub/api/funnel?range=7d", headers=API).json()["counting_since"] == ""     # nothing yet
    first = hub._range("yesterday")["start"] + 3600
    db.record_event("PageView", "pv-first", "pixel", "sent", {"user_data": {}}, client_id="b1")
    db._c().execute("UPDATE events SET created_at=? WHERE event_id='pv-first'", (first,))
    db.record_event("PageView", "pv-test", "test", "sent", {"user_data": {}}, client_id="b-test")   # not a shopper
    db._c().execute("UPDATE events SET created_at=? WHERE event_id='pv-test'", (first - 86400,))
    week = client.get("/hub/api/funnel?range=7d", headers=API).json()
    assert week["counting_since"] == hub._time_local(dt.datetime.fromtimestamp(first, config.store_tz()))
    assert client.get("/hub/api/funnel?range=today", headers=API).json()["counting_since"] == ""


# =====================================================================================
# The funnel's switch (F3) and assists with no top few (F4)
# =====================================================================================

def test_funnel_names_its_three_groups_for_the_switch(client, shop, monkeypatch):
    body = client.get("/hub/api/funnel?range=today", headers=API).json()
    tip = ("Shoppers who did not come from a Meta ad in the 7 days before: typed the site in, Google, email, "
           "the Shop app, returning customers.")
    assert body["groups"] == [{"key": "meta", "label": "Meta ads", "tip": ""},
                              {"key": "other", "label": "Not from Meta", "tip": tip},
                              {"key": "all", "label": "All", "tip": ""}]
    assert all(len(body[g["key"]]) == len(body["steps"]) == 5 for g in body["groups"])
    assert "Not from Meta" in body["note"] and "everyone else" not in json.dumps(body).lower()
    assert chr(0x2014) not in json.dumps(body)
    # The tip follows the attribution window.
    monkeypatch.setattr(config, "ATTRIBUTION_WINDOW_DAYS", 3)
    hub._funnel_cache.clear()
    body = client.get("/hub/api/funnel?range=today", headers=API).json()
    assert "from a Meta ad in the 3 days before:" in body["groups"][1]["tip"]


def test_every_ad_that_helped_is_listed_with_no_top_few(client, shop):
    helpers = [{"ad_id": f"H{i}", "ad_name": f"Hook {i}", "adset_name": "TOF", "campaign_name": "sperm",
                "at": time.time() - 600 - i} for i in range(attribution.ASSISTS_MAX)]
    # One sale with help from 19 other ads; 12 more sales, each closed by its own ad, with help from Hook 0.
    closers = [credited(2800 + i, "10.00", ad_id=f"C{i}", ad_name=f"Closer {i}", assists=[helpers[0]])
               for i in range(12)]
    shop.orders = [credited(2799, "50.00", ad_id="A1", ad_name="UGC 1", assists=helpers), *closers]
    body = client.get("/hub/api/assists?range=today", headers=API).json()
    rows = {r["ad_id"]: r for r in body["rows"]}
    assert len(rows) == 19 and rows["H0"]["assists"] == 13 and len(rows["H0"]["closers"]) == 13
    assert all(rows[f"H{i}"]["closers"] == [{"ad_id": "A1", "ad_name": "UGC 1", "adset_name": "", "sales": 1,
                                             "value": 50.0}] for i in range(1, 19))
    # The section and its note say "Creatives that got the sale", never "Videos".
    assert "Creatives that got the sale" in body["note"] and "Videos" not in body["note"]
    feed = {o["id"]: o for o in client.get("/hub/api/orders?range=today", headers=API).json()["orders"]}
    assert [a["ad_name"] for a in feed["2799"]["ad"]["assists"]] == [f"Hook {i}" for i in range(19)]
    ads = {a["ad_id"]: a for c in client.get("/hub/api/creatives?range=today", headers=API).json()["campaigns"]
           for g in c["groups"] for a in g["ads"]}
    assert not any(k.startswith("H") for k in ads) and len(ads) == 13   # helpers have no row; the 13 sellers do


def test_orders_feed_tags_the_first_order_of_a_subscription(client, shop, monkeypatch):
    # The order webhook never says an order was on a subscription plan; GraphQL's
    # line items do. Each answer is kept, and a failed read never breaks the list.
    monkeypatch.setitem(hub._sub_backoff, "until", 0.0)      # other tests' fake Shopify can't answer it
    sub, plain = make_order(801), make_order(802)
    shop.orders = [sub, plain]
    asked = []

    async def on_sub(order_id, timeout=4.0):
        asked.append(str(order_id))
        return str(order_id) == "801"
    monkeypatch.setattr(shopify, "order_on_subscription", on_sub)
    rows = {o["id"]: o for o in client.get("/hub/api/orders?range=today", headers=API).json()["orders"]}
    assert rows["801"]["subscription"] is True and rows["802"]["subscription"] is False
    client.get("/hub/api/orders?range=today", headers=API)
    assert sorted(asked) == ["801", "802"]                               # asked once, then remembered

    async def down(order_id, timeout=4.0):
        raise shopify.GraphQLError("Access denied", "ACCESS_DENIED")
    monkeypatch.setattr(shopify, "order_on_subscription", down)
    with db._lock:
        db._c().execute("DELETE FROM meta_kv WHERE key='sub:802'")      # 802 not known yet, and Shopify refuses
    rows = {o["id"]: o for o in client.get("/hub/api/orders?range=today", headers=API).json()["orders"]}
    assert rows["802"]["subscription"] is None and db.kv_get("sub:802") is None and rows["801"]["subscription"]


def test_funnel_splits_every_product_shoppers_viewed_or_bought(client, shop):
    # SpermFuel+ and Axis 3 run ads at the same time: each has its own funnel and product page vs listicle.
    now = time.time()
    db.upsert_session("b-sperm", ad_params=json.dumps({"ad_id": "AD1", "lp": "listicle"}), ad_seen_at=now - 60,
                      checkout_token="chkS")
    db.upsert_session("b-axis", ad_params=json.dumps({"ad_id": "AD2"}), ad_seen_at=now - 60)
    db.upsert_session("b-axis-buyer", ad_params=json.dumps({"ad_id": "AD2"}), ad_seen_at=now - 60, checkout_token="chkA")
    db.upsert_session("b-home", fbp="fb.1.1.9")

    def view(cid, title=None, name="ViewContent"):
        cd = {"content_name": title} if title else {}
        db.record_event(name, f"{cid}-{name}", "pixel", "sent", {"user_data": {}, "custom_data": cd}, client_id=cid)
    view("b-sperm", "SpermFuel+")
    view("b-axis", "Axis 3")
    view("b-axis-buyer", "SpermFuel+")                 # looked at SpermFuel+ first, then bought Axis 3
    view("b-home", name="PageView")
    shop.orders = [make_order(1701, checkout_token="chkS"),
                   make_order(1702, checkout_token="chkA", line_items=[
                       {"title": "Axis 3", "quantity": 3, "product_id": 222, "price": "30.00"},
                       {"title": "Shipping Protection", "quantity": 1, "product_id": 333, "price": "2.00"}])]
    body = client.get("/hub/api/funnel?range=today", headers=API).json()
    assert [p["key"] for p in body["products"]] == ["Axis 3", "SpermFuel+"]
    axis, sperm = body["by_product"]["Axis 3"], body["by_product"]["SpermFuel+"]
    assert axis["meta"] == [2, 2, 1, 1, 1] and sperm["meta"] == [1, 1, 1, 1, 1]
    assert body["meta"] == [3, 3, 2, 2, 2] and body["other"][0] == 1          # all products together, as before
    rows = {r["key"]: r for r in sperm["listicle"]["rows"]}
    assert rows["listicle"]["visitors"] == 1 and rows["direct"]["visitors"] == 0
    rows = {r["key"]: r for r in axis["listicle"]["rows"]}
    assert rows["direct"]["visitors"] == 2 and rows["listicle"]["visitors"] == 0


def test_an_assist_names_the_creative_that_got_the_sale_not_the_order():
    base = {"campaign_id": "C1", "campaign_name": "CBO", "adset_id": "AS1", "adset_name": "Broad"}
    rows = [{**base, "ad_id": "A1", "ad_name": "Ad one", "spend": 50.0},
            {**base, "adset_id": "AS2", "adset_name": "B4 Statics -", "ad_id": "A2", "ad_name": "MOF 2 - Copy 3", "spend": 1.0}]
    facts = [_sale(1, 33.24, ad_id="A2", ad_name="MOF 2 - Copy 3", assists=[{"ad_id": "A1", "ad_name": "Ad one"}]),
             _sale(2, 33.24, ad_id="A2", ad_name="MOF 2 - Copy 3", assists=[{"ad_id": "A1", "ad_name": "Ad one"}]),
             _sale(3, 20.0, assists=[{"ad_id": "A1", "ad_name": "Ad one"}])]          # its own ad unnamed
    camp = hub.build_creatives(facts, rows, "adset", every_ad=True)["campaigns"][0]
    ads = {a["ad_id"]: a for g in camp["groups"] for a in g["ads"]}
    assert ads["A1"]["assist_closers"] == ["B4 Statics - · MOF 2 - Copy 3"] * 2 + [hub.UNNAMED_AD]
    broad = next(g for g in camp["groups"] if g["name"] == "Broad")
    assert broad["assist_closers"] == ads["A1"]["assist_closers"]          # one per sale, like assist_orders
