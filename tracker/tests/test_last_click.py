"""Batch 4A: the one last-click decision. What Meta receives and what the hub
shows come from the same stored record (attribution.resolve), built from the
storefront session, Shopify's visit record, the old tracker's note and, only
when nothing else exists, the first landing page.

The seven real orders of Sep 27, 2026 are regression fixtures here, with fake
fbclids and their real times and ids. Shopify, the Conversions API and the
Marketing API are all mocked."""
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
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

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
import shopify
import tracking
import watchdog
import worker

ADMIN = "admin-test"
API = {"Authorization": f"Bearer {ADMIN}"}
POST = {**API, "X-Hub-Request": "1"}
MAIN = "1298114545063437"
BACKUP_ID = "1717074239276698"
PII = ("jane.doe@example.com", "555-0199", "203.0.113.9", "Jané", "L6M 5P6")
NY = ZoneInfo("America/New_York")

# Meta's ads on Sep 27, 2026. Campaign 'sperm' = 120250768746100090.
SPERM = "120250768746100090"
MOF3, STATIC, BOF, AD5, AD2 = ("120250788259650090", "120250808802210090", "120250836493630090",
                               "120250785471590090", "120250785421790090")
CP25, CP25_SET, CP_CAMP = "120224886196460724", "120224886196440724", "120224886196450724"


def _ad(ad_id, name, adset_id, adset, campaign_id=SPERM, campaign="sperm"):
    return {"ad_id": ad_id, "ad_name": name, "adset_id": adset_id, "adset_name": adset,
            "campaign_id": campaign_id, "campaign_name": campaign}


CATALOG = [_ad(MOF3, "MOF 3", "120250787597660090", "B2 Statics"),
           _ad(STATIC, "Static", "120250808379090090", "B1 VSL"),
           _ad(BOF, "BOF", "120250808379090090", "B1 VSL"),
           _ad(AD5, "5", "120250785359380090", "B1 Rips"),
           _ad(AD2, "2", "120250785359380090", "B1 Rips"),
           _ad(CP25, "CP2.5", CP25_SET, "CP BCBO2 set", CP_CAMP, "CP - BCBO2 (INT)")]


def ny(hms: str) -> float:
    h, m, s = map(int, hms.split(":"))
    return dt.datetime(2026, 9, 27, h, m, s, tzinfo=NY).timestamp()


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


def landing(path: str, **params) -> str:
    return f"{path}?{urlencode(params)}"


def notes(**attrs) -> list[dict]:
    return [{"name": k, "value": v} for k, v in attrs.items()]


def order(oid, ts, **over):
    o = {"id": oid, "name": f"#c{oid}", "email": "jane.doe@example.com", "phone": "(647) 555-0199",
         "created_at": iso(ts), "processed_at": iso(ts), "test": False, "source_name": "web",
         "financial_status": "paid", "total_price": "59.95", "currency": "USD", "checkout_token": f"chk{oid}",
         "browser_ip": "203.0.113.9", "client_details": {"user_agent": "Mozilla/5.0 iPhone"},
         "landing_site": None, "note_attributes": [],
         "customer": {"id": 777, "email": "jane.doe@example.com", "first_name": "Jané"},
         "billing_address": {"first_name": "Jané", "zip": "L6M 5P6", "country_code": "CA"},
         "shipping_address": {}, "line_items": [{"title": "SpermFuel+", "quantity": 1, "product_id": 111}]}
    o.update(over)
    return o


# --- the real orders, fake fbclids -------------------------------------------------

def c3711(ts=None):
    return order(3711, ts or ny("16:23:44"), total_price="61.47", landing_site=landing(
        "/products/spermfuel", utm_source="fb", utm_medium="paid_social", utm_campaign="sperm",
        utm_content="B2 Statics", utm_term="MOF 3", campaign_id=SPERM, adset_id="120250787597660090",
        ad_id=MOF3, placement="Facebook_Mobile_Feed", fbclid="FAKEclick3711aaa"))


def c3709(ts=None, click_ms=1790535102158):
    return order(3709, ts or ny("14:52:33"), landing_site=landing(
        "/products/spermfuel", utm_source="fb", utm_campaign="sperm", utm_content="B1 VSL", utm_term="Static",
        campaign_id=SPERM, adset_id="120250808379090090", ad_id=STATIC, placement="Instagram_Reels",
        fbclid="FAKEclick3709aaa"),
        note_attributes=notes(utm_source="fb", utm_content="B1 VSL", utm_term="Static", utm_campaign="sperm",
                              utm_id=SPERM, fbc=f"fb.1.{click_ms}.FAKEclick3709aaa"))


def c3708(ts=None):
    return order(3708, ts or ny("13:59:26"), total_price="122.64",
                 landing_site="/products/amino-acid-capsules-hashi?variant=47895611244797")


def c3707(ts=None, click_ms=1790531757040):
    return order(3707, ts or ny("13:57:04"), total_price="33.24", landing_site=landing(
        "/products/spermfuel", utm_source="fb", utm_campaign="sperm", utm_content="B1 Rips", utm_term="5",
        campaign_id=SPERM, adset_id="120250785359380090", ad_id=AD5, fbclid="FAKEfirst3707aaa"),
        note_attributes=notes(utm_source="fb", utm_medium="paid_social", utm_id=SPERM, utm_content="B1 Rips", utm_term="2", utm_campaign="sperm",
                              fbc=f"fb.1.{click_ms}.FAKElast3707aaaa"))


def c3706(ts=None):
    return order(3706, ts or ny("09:19:56"), landing_site=landing(
        "/products/spermfuel", utm_source="fb", utm_campaign="sperm", utm_content="B1 Rips", utm_term="5",
        campaign_id=SPERM, adset_id="120250785359380090", ad_id=AD5, fbclid="FAKEfirst3706aaa"),
        note_attributes=notes(utm_source="fb", utm_medium="paid_social", utm_id=SPERM, utm_content="B1 Rips", utm_term="2", utm_campaign="sperm",
                              fbc="fb.1.1790515146419.FAKElast3706aaaa"))


def c3705(ts=None):
    return order(3705, ts or ny("02:51:23"), total_price="91.88", landing_site=None,
                 note_attributes=notes(utm_source="shop_campaigns", utm_medium="paid",
                                       utm_campaign="0d6c3b52-7a8e-4a8e-9d0e-5d0f0c1b2a3c"))


def c3704(ts=None):
    return order(3704, ts or ny("01:04:11"), total_price="62.20", landing_site=landing(
        "/products/cayenne-pepper-drops", ad_id=CP25, campaign_id=CP_CAMP, fbclid="FAKEfirst3704aaa",
        utm_campaign="CP - BCBO2 (INT)", utm_content="CP2.5", utm_id=CP_CAMP, utm_medium="CP - BCBO2 (INT)",
        utm_source="Facebook", utm_term=CP25_SET),
        note_attributes=notes(utm_content="B1 VSL", utm_term="Static", utm_campaign="sperm",
                              fbc="fb.1.1790485002895.FAKElast3704aaaa"))


def decide(o, sess=None, journey=None, catalog=CATALOG):
    return attribution.resolve(o, sess, journey=journey, catalog=catalog)


# --- fakes ---------------------------------------------------------------------------

class FakeShopify:
    """Admin REST (orders, shop, webhooks) and the GraphQL visit record."""

    def __init__(self):
        self.orders, self.journeys, self.mode, self.graphql_calls = [], {}, "ok", 0

    def handler(self, request: httpx.Request):
        path = request.url.path
        if path.endswith("/graphql.json"):
            self.graphql_calls += 1
            if self.mode == "denied":
                return httpx.Response(200, json={"data": {"order": {"customerJourneySummary": None}}, "errors": [
                    {"message": "Access denied for customerJourneySummary field. Required access: `read_orders` "
                                "access scope.", "extensions": {"code": "ACCESS_DENIED"}}]})
            if self.mode == "throttled":
                return httpx.Response(200, json={"errors": [{"message": "Throttled",
                                                             "extensions": {"code": "THROTTLED"}}]})
            if self.mode == "timeout":
                raise httpx.ReadTimeout("slow", request=request)
            oid = json.loads(request.content)["variables"]["id"].rsplit("/", 1)[-1]
            return httpx.Response(200, json={"data": {"order": {"customerJourneySummary": self.journeys.get(oid)}}})
        if path.endswith("/orders.json"):
            return httpx.Response(200, json={"orders": self.orders})
        if path.endswith("/shop.json"):
            return httpx.Response(200, json={"shop": {"name": "Core Supplements"}})
        if path.endswith("/webhooks.json"):
            return httpx.Response(200, json={"webhooks": []})
        m = re.search(r"/orders/(\d+)\.json$", path)
        if m:
            return httpx.Response(200, json={"order": next(o for o in self.orders if str(o["id"]) == m.group(1))})
        return httpx.Response(404, json={})


class FakeMeta:
    def __init__(self):
        self.sent, self.ad_rows, self.graph_calls = [], [], 0

    def capi(self, request: httpx.Request):
        body = json.loads(request.content)
        self.sent.append((request.url.path.split("/")[-2], body["data"]))
        return httpx.Response(200, json={"events_received": len(body["data"]), "fbtrace_id": "trace9"})

    def graph(self, request: httpx.Request):
        self.graph_calls += 1
        path = request.url.path
        if path.endswith("/act_123"):
            return httpx.Response(200, json={"name": "Core", "currency": "USD", "timezone_name": "America/New_York"})
        if path.endswith("/act_123/insights"):
            return httpx.Response(200, json={"data": self.ad_rows})
        return httpx.Response(400, json={"error": {"message": "(#803) unknown", "code": 803}})

    def events(self, name="Purchase"):
        return [e for _, batch in self.sent for e in batch if e["event_name"] == name]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", os.path.join(tempfile.mkdtemp(), "tracker.db"))
    monkeypatch.setattr(db, "_conn", None)
    db.init()
    db.kv_set("tracking_start", str(time.time() - 86400))
    db.kv_set("mode", "live")
    tracking._next_try.clear()
    tracking._journey_logged.clear()
    app_module._hits.clear()
    hub._orders_cache.clear()
    hub._inflight.clear()
    hub._funnel_cache.clear()
    hub._identity_tried.clear()
    hub._shop.update(name="", at=0.0)
    meta_ads._cache.clear()
    meta_ads.reset_catalog()
    watchdog._state.update(emq_at=time.time(), webhook_at=0.0, webhook=None, webhook_good=None)
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
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    return fake


async def _no_sleep(_):
    return None


@pytest.fixture
def client(monkeypatch, shop, meta):
    monkeypatch.setattr(worker, "start", lambda: [])
    monkeypatch.setattr(tracking, "fire_and_forget", lambda coro: coro.close())
    app_module.mcp._session_manager = None
    with TestClient(app_module.create_app()) as c:
        yield c


def collect(client, **payload):
    base = {"id": f"evt{time.time_ns()}", "ts": int(time.time() * 1000), "url": "https://getcoresupps.com/",
            "cid": "browser-1", "fbp": "fb.1.10.99"}
    base.update(payload)
    r = client.post("/collect", content=json.dumps(base), headers={"Content-Type": "text/plain"})
    assert r.status_code == 204, r.text
    return r


def stored(oid) -> dict:
    return db.orders_by_id([str(oid)])[str(oid)]["attribution"]


# =====================================================================================
# The seven real orders (R3, R7, R8)
# =====================================================================================

def test_c3711_a_direct_ad_click_with_nothing_else_known_sells_from_its_landing_page():
    d = decide(c3711())
    c = d["attribution"]
    assert (c["meta"], c["ad_id"], c["ad_name"], c["adset_name"], c["campaign_name"]) == (
        True, MOF3, "MOF 3", "B2 Statics", "sperm")
    # No browser and no Shopify visit record: its time can't be checked, so it is marked,
    # and Meta gets the click stamped with the order's time, as before.
    assert c["source"] == "first_visit_unverified" and c["click_at"] is None
    assert d["fbc"] == f"fb.1.{int(ny('16:23:44') * 1000)}.FAKEclick3711aaa"
    assert c["first_touch"]["ad_id"] == MOF3 and c["assists"] == []
    # With Shopify's record of the last visit it is a verified click, stamped when it happened.
    journey = {"lastVisit": {"occurredAt": iso(ny("16:21:10")), "landingPage": "https://getcoresupps.com"
                             + c3711()["landing_site"], "utmParameters": {"source": "fb", "content": "B2 Statics",
                                                                            "term": "MOF 3", "campaign": "sperm"}}}
    d = decide(c3711(), journey=journey)
    assert (d["attribution"]["source"], d["attribution"]["ad_id"]) == ("shopify_last_visit", MOF3)
    assert d["attribution"]["click_at"] == ny("16:21:10")
    assert d["fbc"] == f"fb.1.{int(ny('16:21:10') * 1000)}.FAKEclick3711aaa"


def test_c3709_the_old_trackers_last_click_is_the_same_ad_as_the_landing_page():
    d = decide(c3709())
    c = d["attribution"]
    assert (c["source"], c["ad_id"], c["ad_name"], c["adset_name"]) == ("order_note", STATIC, "Static", "B1 VSL")
    assert c["click_at"] == 1790535102.158 and d["fbc"] == "fb.1.1790535102158.FAKEclick3709aaa"
    assert c["first_touch"]["ad_id"] == STATIC and c["assists"] == []        # the same ad: no assist
    assert c["ambiguous"] is False and c["channel"] == "Meta ads"


def test_c3708_a_sale_with_no_ad_at_all_is_direct():
    d = decide(c3708())
    c = d["attribution"]
    assert (c["meta"], c["source"], c["ad_id"], c["channel"], d["fbc"]) == (False, "", None, "Direct", "")
    assert c["first_touch"] is None and c["assists"] == []


@pytest.mark.parametrize("make, click_at, first", [
    (c3707, 1790531757.04, "FAKElast3707aaaa"),
    (c3706, 1790515146.419, "FAKElast3706aaaa"),
])
def test_c3707_c3706_the_last_click_is_ad_2_by_its_names_and_ad_5_assisted(make, click_at, first):
    d = decide(make())
    c = d["attribution"]
    # utm_content=B1 Rips, utm_term=2: matched to Meta's names both ways, one ad fits.
    assert (c["source"], c["ad_id"], c["ad_name"], c["adset_name"], c["campaign_id"]) == (
        "order_note", AD2, "2", "B1 Rips", SPERM)
    assert c["click_at"] == click_at and d["fbc"] == f"fb.1.{int(round(click_at * 1000))}.{first}"
    # The note carried no ad ids: that click came through the old listicle.
    assert (c["lp"], c["ids_stripped"]) == ("", False)          # utm_id on the note: not the old listicle
    assert c["first_touch"]["ad_id"] == AD5 and c["first_touch"]["from"] == "landing_site"
    assert [(a["ad_id"], a["ad_name"], a.get("first_touch")) for a in c["assists"]] == [(AD5, "5", True)]


def test_c3705_shop_app_ads_are_their_own_channel_not_meta():
    d = decide(c3705())
    c = d["attribution"]
    assert (c["meta"], c["channel"], c["ad_id"], d["fbc"]) == (False, "Shop app ads", None, "")


def test_c3704_the_old_ads_first_visit_assists_the_static_ad_that_sold():
    d = decide(c3704())
    c = d["attribution"]
    assert (c["source"], c["ad_id"], c["ad_name"]) == ("order_note", STATIC, "Static")
    assert d["fbc"] == "fb.1.1790485002895.FAKElast3704aaaa"
    ft = c["first_touch"]
    assert (ft["ad_id"], ft["ad_name"], ft["campaign_id"], ft["adset_id"]) == (CP25, "CP2.5", CP_CAMP, CP25_SET)
    assert [(a["ad_id"], a["ad_name"]) for a in c["assists"]] == [(CP25, "CP2.5")]


def test_landing_site_never_sells_over_a_later_click():
    # Every real order with a later click: the landing page is the first touch, never the seller.
    for make in (c3707, c3706, c3704):
        c = decide(make())["attribution"]
        assert c["source"] == "order_note" and c["first_touch"]["ad_id"] != c["ad_id"]
    # Without Meta's names the note's click still wins; its names are kept, marked ambiguous.
    c = decide(c3707(), catalog=[])["attribution"]
    assert (c["source"], c["ad_id"], c["ad_name"], c["adset_name"], c["ambiguous"]) == (
        "order_note", None, "2", "B1 Rips", True)


# =====================================================================================
# R7: names to ids
# =====================================================================================

def ad(content="", term="", campaign_id="", campaign_name="", adset_id=""):
    return attribution._ad_from_params({k: v for k, v in (("utm_content", content), ("utm_term", term),
                                                          ("campaign_id", campaign_id), ("utm_campaign", campaign_name),
                                                          ("adset_id", adset_id)) if v})


def test_utm_names_resolve_to_one_meta_ad_either_way_round():
    assert attribution.resolve_names(ad("B1 Rips", "2", SPERM), CATALOG)["ad_id"] == AD2
    assert attribution.resolve_names(ad("2", "B1 Rips", SPERM), CATALOG)["ad_id"] == AD2        # swapped
    assert attribution.resolve_names(ad("B2 Statics", "MOF 3", campaign_name="Sperm"), CATALOG)["ad_id"] == MOF3
    # The campaign scopes it: the same names in another campaign don't count.
    other = CATALOG + [_ad("999", "2", "998", "B1 Rips", "777", "leggings")]
    assert attribution.resolve_names(ad("B1 Rips", "2", SPERM), other)["ad_id"] == AD2
    # Two ads fit (no campaign to tell them apart), or none: unresolved.
    assert attribution.resolve_names(ad("B1 Rips", "2"), other) is None
    assert attribution.resolve_names(ad("B1 VSL"), CATALOG) is None          # the ad set alone: Static or BOF
    assert attribution.resolve_names(ad("Nope", "Nothing", SPERM), CATALOG) is None
    assert attribution.resolve_names(ad("B1 Rips", "2", SPERM), []) is None
    # The oldest links carried the ad set's id in utm_term.
    assert attribution.resolve_names(ad("CP2.5", CP25_SET, CP_CAMP), CATALOG)["ad_id"] == CP25
    ident = attribution.identify(ad("B1 Rips", "2"), other)
    assert (ident["ad_id"], ident["ad_name"], ident["ambiguous"]) == (None, "2", True)


def test_the_catalog_comes_from_insights_and_names_and_survives_a_restart(meta, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    meta.ad_rows = [{**r, "spend": "1"} for r in CATALOG]
    got = asyncio.run(meta_ads.ad_catalog())
    assert {r["ad_id"] for r in got} == {r["ad_id"] for r in CATALOG}
    calls = meta.graph_calls
    asyncio.run(meta_ads.ad_catalog())                     # half an hour between reads
    assert meta.graph_calls == calls
    meta_ads._cache.clear()
    meta_ads.reset_catalog()                               # a restart: the last read is on the volume
    assert {r["ad_id"] for r in meta_ads.cached_catalog()} == {r["ad_id"] for r in CATALOG}
    # Nothing set up: no call, no ads.
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", [])
    assert asyncio.run(meta_ads.ad_catalog()) == []


# =====================================================================================
# R8: channels for sales no Meta click got
# =====================================================================================

@pytest.mark.parametrize("over, channel", [
    ({"note_attributes": notes(utm_source="shop_campaigns", utm_medium="paid")}, "Shop app ads"),
    ({"landing_site": "/?gclid=abc"}, "Google"),
    ({"landing_site": "/?utm_source=google&utm_medium=cpc"}, "Google"),
    ({"landing_site": "/", "referring_site": "https://www.google.com/"}, "Google"),
    ({"landing_site": "/?ttclid=abc"}, "TikTok"),
    ({"landing_site": "/?utm_source=klaviyo&utm_campaign=Welcome"}, "Email or SMS"),
    ({"landing_site": "/?utm_source=postscript"}, "Email or SMS"),
    ({"landing_site": "/?utm_medium=sms"}, "Email or SMS"),
    ({"landing_site": "/?utm_source=attentive"}, "Email or SMS"),
    ({"landing_site": "/", "referring_site": "https://blog.example.org/post?x=1"}, "Other referral (blog.example.org)"),
    ({"landing_site": "/", "referring_site": "https://getcoresupps.com/cart"}, "Direct"),
    ({"landing_site": "/products/x"}, "Direct"),
    ({"landing_site": None}, "Direct"),
])
def test_channels(over, channel):
    assert decide(order(1, time.time(), **over))["attribution"]["channel"] == channel


def test_shopify_last_visit_names_the_channel_first():
    journey = {"lastVisit": {"occurredAt": iso(time.time() - 60), "landingPage": "https://getcoresupps.com/?gclid=z",
                             "utmParameters": {}}}
    o = order(1, time.time(), note_attributes=notes(utm_source="klaviyo"))
    assert decide(o, journey=journey)["attribution"]["channel"] == "Google"


# =====================================================================================
# R1, R2: the session's current click is always the newest
# =====================================================================================

def test_the_newest_ad_arrival_is_the_sessions_click_stamped_when_it_arrived(client):
    first_url = ("https://getcoresupps.com/products/spermfuel?utm_source=fb&utm_campaign=sperm&utm_content=B1%20Rips"
                 f"&utm_term=5&campaign_id={SPERM}&adset_id=S1&ad_id={AD5}&fbclid=FAKEfirstclick1")
    collect(client, name="page_viewed", url=first_url, fbc="fb.1.20.OLDCOOKIE1234")
    s = db.get_session("browser-1")
    first_at = s["ad_seen_at"]
    # The link's own fbclid wins over whatever the cookie said, stamped when it arrived here.
    assert s["fbc"] == attribution.make_fbc("FAKEfirstclick1", first_at)
    # The next page, and a reload of the same link, are the same click.
    collect(client, name="product_viewed", url="https://getcoresupps.com/products/spermfuel",
            fbc=s["fbc"], custom={"items": [{"product_id": "111"}]})
    collect(client, name="page_viewed", url=first_url)
    s = db.get_session("browser-1")
    assert s["ad_seen_at"] == first_at and s["fbc"] == attribution.make_fbc("FAKEfirstclick1", first_at)
    # A later click on another ad, through the new listicle (lp=...), replaces it.
    time.sleep(0.05)                    # past the Windows clock tick (~16 ms)
    second = ("https://getcoresupps.com/products/spermfuel?utm_source=fb&utm_campaign=sperm&utm_content=B1%20Rips"
              f"&utm_term=2&campaign_id={SPERM}&adset_id=S1&ad_id={AD2}&fbclid=FAKEsecondclick&lp=listicle-v2-one-line")
    collect(client, name="page_viewed", url=second, fbc=attribution.make_fbc("FAKEfirstclick1", first_at))
    s = db.get_session("browser-1")
    assert s["ad_seen_at"] > first_at
    assert s["fbc"] == attribution.make_fbc("FAKEsecondclick", s["ad_seen_at"])
    params = json.loads(s["ad_params"])
    assert params["ad_id"] == AD2 and params["lp"] == "listicle-v2-one-line" and "ids_stripped" not in params
    assert "FAKEsecondclick" not in s["ad_params"] and "FAKEsecondclick" not in s["ad_history"]
    history = json.loads(s["ad_history"])
    assert [(v["ad_id"], v.get("lp")) for v in history] == [(AD5, None), (AD2, "listicle-v2-one-line")]
    assert history[-1]["click"] == attribution.click_key("FAKEsecondclick")
    # An older cookie coming back later never takes over from the newer click.
    collect(client, name="page_viewed", url="https://getcoresupps.com/",
            fbc=attribution.make_fbc("FAKEfirstclick1", first_at))
    assert db.get_session("browser-1")["fbc"] == s["fbc"]
    # A newer cookie (a click the pixel saw elsewhere) does.
    time.sleep(0.05)                                      # a later moment than the second click (Windows clock)
    newer = attribution.make_fbc("FAKEthirdclick1", time.time())
    collect(client, name="page_viewed", url="https://getcoresupps.com/", fbc=newer)
    assert db.get_session("browser-1")["fbc"] == newer


def test_utm_tags_without_ad_ids_came_through_the_listicle(client):
    stripped = ("https://getcoresupps.com/products/spermfuel?utm_source=fb&utm_campaign=sperm"
                "&utm_content=B1%20Rips&utm_term=2&fbclid=FAKEstripped123")
    collect(client, name="page_viewed", url=stripped, ref="https://fertilityinmen.netlify.app/?fbclid=x")
    s = db.get_session("browser-1")
    params = json.loads(s["ad_params"])
    assert (params["lp"], params["ids_stripped"], params["ref"]) == ("listicle", True, "fertilityinmen.netlify.app")
    (visit,) = json.loads(s["ad_history"])
    assert (visit["lp"], visit["ids_stripped"], visit["ref"]) == ("listicle", True, "fertilityinmen.netlify.app")
    # The order's record keeps it.
    c = decide(order(1, time.time() + 5), s)["attribution"]
    assert (c["source"], c["ad_id"], c["lp"], c["ids_stripped"]) == ("browser", AD2, "listicle", True)
    # An explicit lp is kept as is; ids present means nothing was stripped.
    assert attribution.landing_page({"utm_source": "fb", "lp": "listicle-v2-one-line"}) == ("listicle-v2-one-line",
                                                                                         False)
    assert attribution.landing_page({"utm_source": "fb", "ad_id": "1"}) == ("", False)
    assert attribution.landing_page({"fbclid": "1"}) == ("", False)


def test_the_browsers_click_beats_shopifys_and_the_note_only_when_newer():
    now = ny("13:57:04")
    note_click = 1790531757.04                      # 13:55:57
    sess = {"client_id": "b", "ad_params": json.dumps({"ad_id": MOF3, "utm_source": "fb"}), "ad_seen_at": now - 30}
    assert decide(c3707(now), sess)["attribution"]["ad_id"] == MOF3                    # newer than the note
    sess["ad_seen_at"] = note_click - 600
    c = decide(c3707(now), sess)["attribution"]
    assert c["ad_id"] == AD2 and [a["ad_id"] for a in c["assists"]] == [MOF3, AD5]     # older, then first touch


# =====================================================================================
# R3 (b): Shopify's visit record, and how it degrades
# =====================================================================================

def test_journey_is_read_and_its_problems_are_noted_once(shop, caplog):
    o = c3711(time.time() - 30)
    shop.journeys["3711"] = {"lastVisit": {"occurredAt": iso(time.time() - 90), "landingPage": "https://x/?a=1"},
                             "firstVisit": None}
    got = asyncio.run(tracking.journey_for(o))
    assert got["lastVisit"]["landingPage"] == "https://x/?a=1" and tracking.journey_status()["ok"] is True
    # No record yet (Shopify builds it after the order): nothing, and not a problem.
    shop.journeys.clear()
    assert asyncio.run(tracking.journey_for(o)) is None and tracking.journey_status()["ok"] is True
    for mode, reason in (("throttled", "throttled"), ("timeout", "timeout")):
        shop.mode = mode
        assert asyncio.run(tracking.journey_for(o)) is None
        assert tracking.journey_status()["reason"] == reason
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tracker.tracking"):
        shop.mode = "denied"
        for _ in range(3):
            assert asyncio.run(tracking.journey_for(o)) is None
    st = tracking.journey_status()
    assert st["reason"] == "scope" and "read_orders" in st["detail"]
    # Logged once, and Shopify isn't asked again for an hour.
    assert sum("visit history unavailable" in r.getMessage() for r in caplog.records) == 1
    calls = shop.graphql_calls
    asyncio.run(tracking.journey_for(o))
    assert shop.graphql_calls == calls


def test_a_journey_failure_never_holds_a_purchase_up(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 0)
    shop.mode = "timeout"
    db.upsert_order(c3707(time.time() - 60, click_ms=int((time.time() - 120) * 1000)))
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    (purchase,) = meta.events()
    assert purchase["event_id"] == "order_3707"                      # the event id never changes
    assert stored(3707)["source"] == "order_note"


# =====================================================================================
# R4: an unmatched new sale waits for its visit
# =====================================================================================

@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(time, "time", lambda: now[0])
    return now


def test_an_unmatched_sale_waits_then_goes_out_with_the_best_data(client, shop, meta, clock):
    created = clock[0] - 5
    db.upsert_order(order(501, created, landing_site=c3711()["landing_site"]))
    assert asyncio.run(tracking.process_pending()) == {"pending": 1}
    row = db.get_order("501")
    assert row["wait_until"] == pytest.approx(created + 60) and row["match_tries"] == 1 and meta.sent == []
    asked = shop.graphql_calls
    # A restart forgets nothing: the wait is on the order row.
    tracking._next_try.clear()
    clock[0] += 20
    assert asyncio.run(tracking.process_pending()) == {"pending": 1}
    assert shop.graphql_calls == asked                               # not asked before the next step
    clock[0] = created + 61
    assert asyncio.run(tracking.process_pending()) == {"pending": 1}
    assert db.get_order("501")["wait_until"] == pytest.approx(created + 120)
    clock[0] = created + 121
    assert asyncio.run(tracking.process_pending()) == {"pending": 1}
    assert db.get_order("501")["wait_until"] == pytest.approx(created + 300)
    clock[0] = created + 301
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert shop.graphql_calls == asked + 3 and db.get_order("501")["match_tries"] == 3
    # Nothing better turned up: the first landing page, unverified, like before.
    assert stored(501)["source"] == "first_visit_unverified" and len(meta.events()) == 1


def test_the_visit_turning_up_while_waiting_sends_it_then(client, shop, meta, clock):
    created = clock[0] - 5
    db.upsert_order(order(502, created, landing_site=c3711()["landing_site"]))
    assert asyncio.run(tracking.process_pending()) == {"pending": 1}
    shop.journeys["502"] = {"lastVisit": {"occurredAt": iso(created - 40), "landingPage": "https://getcoresupps.com"
                                          + c3711()["landing_site"], "utmParameters": {"source": "fb"}}}
    clock[0] = created + 61
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert stored(502)["source"] == "shopify_last_visit"
    assert meta.events()[0]["user_data"]["fbc"] == attribution.make_fbc("FAKEclick3711aaa", created - 40)


def test_matched_sales_send_at_once_and_old_ones_never_wait(client, shop, meta, clock):
    db.upsert_session("b1", fbp="fb.1.1.1", checkout_token="chk601",
                      ad_params=json.dumps({"ad_id": MOF3, "utm_source": "fb"}), ad_seen_at=clock[0] - 100)
    db.upsert_order(order(601, clock[0] - 5))                          # its browser is known
    shop.journeys["602"] = {"lastVisit": {"occurredAt": iso(clock[0] - 50), "landingPage": "https://x/"}}
    db.upsert_order(order(602, clock[0] - 5))                          # Shopify knows its last visit
    db.upsert_order(order(603, clock[0] - 20 * 3600))                  # recovered late: no time to wait
    assert asyncio.run(tracking.process_pending()) == {"sent": 3}
    assert stored(601)["ad_id"] == MOF3 and stored(602)["channel"] == "Direct"


def test_the_retry_steps_follow_the_settings(monkeypatch):
    assert tracking.match_schedule() == [60, 120, 300]
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 90)
    assert tracking.match_schedule() == [60, 90]
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 0)
    assert tracking.match_schedule() == [0]


# =====================================================================================
# R3 + R5: what Meta got and what the order keeps are one decision
# =====================================================================================

def test_the_purchase_carries_the_click_the_order_record_names(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    meta.ad_rows = [{**r, "spend": "1"} for r in CATALOG]
    now = time.time()
    db.upsert_order(c3707(now - 60, click_ms=int((now - 120) * 1000)))
    stale = int((now - 8 * 86400) * 1000)
    db.upsert_order(c3704(now - 60) | {"note_attributes": notes(utm_content="B1 VSL", utm_term="Static",
                                                                utm_campaign="sperm", fbc=f"fb.1.{stale}.FAKElast3704aaaa")})
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 0)
    assert asyncio.run(tracking.process_pending()) == {"sent": 2}
    by_id = {e["custom_data"]["order_id"]: e for e in meta.events()}
    c = stored(3707)
    assert c["ad_id"] == AD2 and by_id["3707"]["user_data"]["fbc"] == f"fb.1.{int((now - 120) * 1000)}.FAKElast3707aaaa"
    # An old tracker click from 8 days before the order is out of the window, and the
    # first landing page came before it, so it is older still: not a Meta sale. Meta gets
    # that old click with its real time, never the landing page stamped "now".
    old = stored(3704)
    assert old["meta"] is False and old["source"] == "" and old["ad_id"] is None
    assert old["first_touch"]["ad_id"] == CP25 and old["fbc"] == f"fb.1.{stale}.FAKElast3704aaaa"
    assert by_id["3704"]["user_data"]["fbc"] == f"fb.1.{stale}.FAKElast3704aaaa"
    # The hub shows exactly that record.
    shop.orders = [c3707(now - 60, click_ms=int((now - 120) * 1000))]
    row = client.get("/hub/api/orders?range=today", headers=API).json()["orders"][0]
    assert (row["ad"]["ad_id"], row["ad"]["ad_name"], row["ad"]["source"], row["channel"]) == (
        AD2, "2", "order_note", "Meta ads")
    assert row["ad"]["assists"] == [{"ad_name": "5", "adset_name": "B1 Rips", "campaign_name": "sperm"}]


def test_a_retry_repeats_the_first_decision(client, shop, meta, monkeypatch):
    """A backup pixel failing sends the order round again: it must carry the
    click the main pixel got, even if Shopify's record turned up since."""
    monkeypatch.setattr(config, "EXTRA_PIXELS", [{"pixel_id": BACKUP_ID, "token": "b", "test_event_code": ""}])
    db.kv_set(f"pixel_start:{BACKUP_ID}", str(time.time() - 86400))
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 0)
    now = time.time()
    real, backup_calls = meta.capi, []

    def flaky(request):
        purchase = json.loads(request.content)["data"][0]["event_name"] == "Purchase"
        if f"/{BACKUP_ID}/" in request.url.path and purchase:
            backup_calls.append(1)
            if len(backup_calls) <= 3:                      # the first send's three tries
                return httpx.Response(500, json={"error": {"message": "down"}})
        return real(request)
    monkeypatch.setattr(meta_capi, "_client", httpx.AsyncClient(transport=httpx.MockTransport(flaky)))
    db.upsert_order(c3711(now - 60))
    assert asyncio.run(tracking.process_pending()) == {"failed": 1}
    first = stored(3711)
    assert first["source"] == "first_visit_unverified" and first["fbc"].endswith(".FAKEclick3711aaa")
    shop.journeys["3711"] = {"lastVisit": {"occurredAt": iso(now - 90), "landingPage": "https://getcoresupps.com/"
                                           "?utm_source=fb&ad_id=" + BOF + "&fbclid=FAKEotherclick1"}}
    tracking._next_try.clear()
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert stored(3711) == first
    fbcs = {pid: e["user_data"]["fbc"] for pid, batch in meta.sent for e in batch}
    assert fbcs == {MAIN: first["fbc"], BACKUP_ID: first["fbc"]}


def test_orders_without_a_record_get_the_same_decision_and_keep_it(client, shop, meta):
    now = time.time()
    # Placed before go-live: skipped by the tracker, WeTracked sent it.
    early = c3707(now - 2 * 86400, click_ms=int((now - 2 * 86400 - 120) * 1000))
    db.kv_set("tracking_start", str(now - 86400))
    db.upsert_order(early)
    assert asyncio.run(tracking.process_pending()) == {"skipped": 1}
    assert stored(3707) is None
    shop.orders = [early, c3708(now - 60)]                   # c3708 was never seen by the tracker
    rows = {r["id"]: r for r in client.get("/hub/api/orders?range=7d", headers=API).json()["orders"]}
    # Decided by the same resolver and stored, so it is decided once.
    assert stored(3707)["source"] == "order_note" and stored(3707)["ad_name"] == "2"
    assert rows["3707"]["ad"]["source"] == "order_note" and rows["3707"]["ad"]["ad_name"] == "2"
    assert rows["3708"]["ad"] is None and rows["3708"]["channel"] == "Direct"
    assert db.get_order("3708") is None                      # nothing is added to the queue
    assert meta.sent == []


# =====================================================================================
# R6: the backfill re-decides, and never sends
# =====================================================================================

def test_the_backfill_re_decides_the_last_week_and_never_sends(meta, shop, caplog):
    now = time.time()
    legacy = {"meta": True, "source": "landing_page", "click": True, "ad_id": AD5, "ad_name": "B1 Rips"}
    a = c3707(now - 3600, click_ms=int((now - 3700) * 1000))        # sent, credited the old way
    db.upsert_order(a)
    db.mark_order("3707", "sent", kind="purchase")
    db.set_order_attribution("3707", legacy)
    b = c3709(now - 2 * 86400, click_ms=int((now - 2 * 86400 - 60) * 1000))   # skipped, never credited
    db.upsert_order(b)
    db.mark_order("3709", "skipped", kind="before_start")
    c = c3711(now - 1800)                                            # decided by this resolver: left alone
    db.upsert_order(c)
    db.mark_order("3711", "sent", kind="purchase")
    mine = {**decide(c)["attribution"], "ad_name": "kept"}
    db.set_order_attribution("3711", mine)
    d = c3706(now - 9 * 86400)                                       # older than a week
    db.upsert_order(d)
    db.mark_order("3706", "skipped", kind="too_old")
    e = order(3712, now - 600, source_name="subscription_contract")  # MRR is never credited
    db.upsert_order(e)
    db.mark_order("3712", "sent", kind="renewal")
    with caplog.at_level(logging.INFO, logger="tracker.tracking"):
        assert asyncio.run(tracking.backfill_attribution()) == 2
    assert any("re-decided 2 order(s)" in r.getMessage() for r in caplog.records)
    assert stored(3707)["source"] == "order_note" and stored(3707)["ad_name"] == "2"
    assert stored(3707)["v"] == attribution.RESOLVER_VERSION
    assert stored(3709)["source"] == "order_note" and stored(3709)["ad_name"] == "Static"
    assert stored(3711) == mine and stored(3712) is None
    assert db.get_order("3706")["attribution"] is None
    assert meta.sent == []                                           # nothing went to Meta
    assert db.get_order("3707")["status"] == "sent" and db.get_order("3709")["status"] == "skipped"
    # Once per version.
    db.set_order_attribution("3707", legacy)
    assert asyncio.run(tracking.backfill_attribution()) == 0 and stored(3707) == legacy


def test_the_backfill_runs_at_startup(monkeypatch):
    ran = []

    async def fake():
        ran.append(True)
        return 0
    monkeypatch.setattr(tracking, "backfill_attribution", fake)

    async def boot():
        tasks = worker.start()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(boot())
    assert ran == [True]


# =====================================================================================
# R9: the watchdog notices
# =====================================================================================

@pytest.fixture
def wd(monkeypatch, shop):
    async def list_orders_since(since):
        return []

    async def request(method, path, **kw):
        return httpx.Response(200, json={"webhooks": []}, request=httpx.Request(method, "https://x/"))
    monkeypatch.setattr(shopify, "list_orders_since", list_orders_since)
    monkeypatch.setattr(shopify, "_request", request)
    return shop


def checks():
    return {c["id"]: c for c in asyncio.run(watchdog.run_checks())}


def test_watchdog_names_a_landing_page_that_drops_the_ad_ids(wd, client):
    by = checks()
    assert by["stripped"]["status"] == "ok" and by["stripped"]["detail"].startswith("No visit")
    collect(client, name="page_viewed", url=f"https://getcoresupps.com/?utm_source=fb&ad_id={AD2}&fbclid=FAKEgood12345")
    assert checks()["stripped"]["status"] == "ok"
    collect(client, name="page_viewed", cid="browser-2", ref="https://fertilityinmen.netlify.app/",
            url="https://getcoresupps.com/?utm_source=fb&utm_content=B1%20Rips&utm_term=2&fbclid=FAKEbad123456")
    c = checks()["stripped"]
    assert c["status"] == "warn" and c["name"] == "Ad tags without an ad ID"
    assert c["detail"].startswith("1 of 2 visits from Meta ads in 24 h came through fertilityinmen.netlify.app")
    assert chr(0x2014) not in c["detail"]


def test_watchdog_says_whether_shopify_visit_history_can_be_read(wd):
    assert checks()["journey"] == {"id": "journey", "name": "Shopify visit history", "status": "ok",
                                   "detail": "Not checked yet: it is read with the next order."}
    db.upsert_order(c3711(time.time() - 60))                # something to ask about
    wd.mode = "denied"
    c = checks()["journey"]
    assert c["status"] == "warn" and "isn't allowed to read visit history" in c["detail"]
    assert "read_orders" in c["detail"]
    tracking._journey_note(True)
    assert checks()["journey"]["status"] == "ok"


def test_watchdog_warns_when_many_sales_were_credited_from_a_first_visit_only(wd):
    assert checks()["first_visit"]["status"] == "ok"
    for i, source in enumerate(["browser", "order_note", "first_visit_unverified", "browser", "browser"]):
        o = order(800 + i, time.time() - 600)
        db.upsert_order(o)
        db.mark_order(str(o["id"]), "sent", kind="purchase")
        db.set_order_attribution(str(o["id"]), {"meta": True, "source": source})
    c = checks()["first_visit"]
    assert c["status"] == "ok" and c["detail"].startswith("1 of 5 new sales in 7 days (20%)")
    o = order(806, time.time() - 600)
    db.upsert_order(o)
    db.mark_order("806", "sent", kind="purchase")
    db.set_order_attribution("806", {"meta": True, "source": "first_visit"})
    c = checks()["first_visit"]
    assert c["status"] == "warn" and "2 of 6" in c["detail"] and "storefront pixel" in c["detail"]


def test_watchdog_counts_ads_where_meta_and_the_store_disagree(wd, meta, monkeypatch):
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    meta.ad_rows = [{**CATALOG[0], "spend": "10", "actions": [{"action_type": "omni_purchase", "value": "2"}]},
                    {**CATALOG[1], "spend": "5", "actions": [{"action_type": "omni_purchase", "value": "1"}]}]
    now = time.time()
    for oid, ad_id in ((901, MOF3), (902, STATIC)):
        db.upsert_order(order(oid, max(now - 60, hub._range("today")["start"] + 1)))
        db.mark_order(str(oid), "sent", kind="purchase")
        db.set_order_attribution(str(oid), {"meta": True, "ad_id": ad_id})
    c = checks()["meta_vs_store"]
    # Informational: never a warning.
    assert c["status"] == "ok" and c["detail"].startswith("Today 1 of 2 ads with sales show a different")


# =====================================================================================
# R10: proposals, approved by the owner
# =====================================================================================

def test_the_watchdog_proposes_each_fix_once(wd, client, meta):
    now = time.time()
    failed = order(1001, now - 3600)
    db.upsert_order(failed)
    db.mark_order("1001", "failed", error="HTTP 500: down", kind="purchase")
    for _ in range(3):
        db.mark_order("1001", "failed", error="HTTP 500: down", kind="purchase")
    early = order(1002, now - 2 * 86400)                     # before go-live: WeTracked's, never proposed
    db.upsert_order(early)
    db.mark_order("1002", "failed", kind="purchase")
    db._c().execute("UPDATE orders SET attempts=9, received_at=? WHERE order_id='1002'", (now - 7200,))
    tagged = order(1003, now - 600, tags="Kaching Bundles, Recurring Order (Loop)")
    db.upsert_order(tagged)
    db.mark_order("1003", "sent", kind="purchase")
    collect(client, name="page_viewed", ref="https://fertilityinmen.netlify.app/",
            url="https://getcoresupps.com/?utm_source=fb&utm_content=B1%20Rips&utm_term=2&fbclid=FAKEbad123456")
    assert asyncio.run(watchdog.propose(time.time())) == 3
    assert asyncio.run(watchdog.propose(time.time())) == 0             # deduped by key
    got = {p["kind"]: p for p in client.get("/hub/api/proposals", headers=API).json()["proposals"]}
    assert set(got) == {"resend", "renewal_tag", "stripped_ids"}
    assert got["resend"]["title"] == "Send order #c1001 to Meta" and got["resend"]["approve_label"] == "Send it"
    assert "after 4 tries" in got["resend"]["detail"]
    assert got["renewal_tag"]["title"] == "Treat orders tagged Recurring Order (Loop) as MRR?"
    assert got["stripped_ids"]["informational"] is True and got["stripped_ids"]["can_dismiss"] is False
    assert got["stripped_ids"]["title"] == "The page at fertilityinmen.netlify.app is dropping the ad IDs"
    for p in got.values():
        assert p["status"] == "pending" and chr(0x2014) not in p["title"] + p["detail"]
    r = client.get("/hub/api/proposals", headers=API)
    assert not any(s in r.text for s in PII + ("test-token", ADMIN))


def test_approving_does_what_it_says_and_only_once(client, shop, meta):
    now = time.time()
    failed = order(1101, now - 3600)
    shop.orders = [failed]
    db.upsert_order(failed)
    db.mark_order("1101", "failed", error="HTTP 500: down", kind="purchase")
    db._c().execute("UPDATE orders SET attempts=5 WHERE order_id='1101'")
    tagged = order(1102, now - 600, tags="Recurring Order (Loop)")
    db.upsert_order(tagged)
    db.mark_order("1102", "sent", kind="purchase")
    asyncio.run(watchdog.propose(now))
    ids = {p["kind"]: p["id"] for p in db.proposals()}
    # POSTs need the hub header, like every hub action.
    assert client.post(f"/hub/api/proposals/{ids['resend']}/approve", headers=API).status_code == 403
    assert client.post(f"/hub/api/proposals/{ids['resend']}/approve").status_code == 401
    body = client.post(f"/hub/api/proposals/{ids['resend']}/approve", headers=POST).json()
    assert body["ok"] is True and body["proposal"]["status"] == "done"
    assert body["proposal"]["result"].startswith("Sent to Meta")
    assert [e["event_id"] for e in meta.events()] == ["order_1101"]
    again = client.post(f"/hub/api/proposals/{ids['resend']}/approve", headers=POST).json()
    assert again["ok"] is False and "already done" in again["error"] and len(meta.events()) == 1
    # MRR tag: future orders only.
    assert tracking.classify_order(order(1103, now - 60, tags="recurring order (loop)")) == "purchase"
    body = client.post(f"/hub/api/proposals/{ids['renewal_tag']}/approve", headers=POST).json()
    assert body["ok"] is True and "from now on" in body["proposal"]["result"]
    assert tracking.classify_order(order(1103, now - 60, tags="Recurring Order (Loop)")) == "renewal"
    assert "recurring order (loop)" in tracking.renewal_tags() and "recurring order (loop)" not in config.RENEWAL_TAGS
    assert db.get_order("1102")["kind"] == "purchase" and len(meta.events()) == 1       # nothing resent
    assert worker.build_report()["config"]["renewal_tags"] == ["kaching subscription recurring order",
                                                               "recurring order (loop)"]
    # Dismissing, and unknown ids.
    assert client.post("/hub/api/proposals/999/dismiss", headers=POST).json()["ok"] is False
    assert client.post("/hub/api/proposals/abc/approve", headers=POST).json()["ok"] is False


def test_informational_proposals_are_just_marked_seen_and_resends_close_themselves(client, meta):
    db.add_proposal("stripped:x", "stripped_ids", "x is dropping the ad IDs", "detail", {"type": "info"})
    pid = db.proposals()[0]["id"]
    body = client.post(f"/hub/api/proposals/{pid}/approve", headers=POST).json()
    assert body["proposal"]["status"] == "done" and meta.sent == []
    now = time.time()
    o = order(1201, now - 3600)
    db.upsert_order(o)
    db.mark_order("1201", "failed", error="HTTP 500", kind="purchase")
    db._c().execute("UPDATE orders SET attempts=5 WHERE order_id='1201'")
    asyncio.run(watchdog.propose(now))
    db.mark_order("1201", "sent", kind="purchase")                  # the retries got it through
    asyncio.run(watchdog.propose(now))
    (p,) = [p for p in db.proposals() if p["kind"] == "resend"]
    assert p["status"] == "done" and p["result"].startswith("Sent on its own")
    dismissed = db.add_proposal("renewal_tag:x", "renewal_tag", "t", "d", {"type": "add_renewal_tag", "tag": "x"})
    assert dismissed
    pid = next(p["id"] for p in db.proposals() if p["kind"] == "renewal_tag")
    assert client.post(f"/hub/api/proposals/{pid}/dismiss", headers=POST).json()["proposal"]["status"] == "dismissed"
    assert "x" not in tracking.renewal_tags()


def test_tick_makes_proposals(wd, monkeypatch):
    made = []

    async def fake(now):
        made.append(now)
        return 0
    monkeypatch.setattr(watchdog, "propose", fake)
    asyncio.run(watchdog.tick())
    assert len(made) == 1


# =====================================================================================
# R11: backup
# =====================================================================================

def test_backup_is_a_consistent_sqlite_file_for_the_bearer_token_only(client, caplog):
    db.upsert_order(order(1301, time.time() - 60))
    db.kv_set("marker", "yes")
    assert client.get("/admin/backup").status_code == 401
    assert client.get(f"/admin/backup?key={ADMIN}").status_code == 401                  # not in a URL
    assert client.get("/admin/backup", headers={"Cookie": f"hub_session={hub._session_value()}"}).status_code == 401
    assert client.get("/admin/backup", headers={"Authorization": "Bearer nope"}).status_code == 401
    before = set(os.listdir(tempfile.gettempdir()))
    with caplog.at_level(logging.DEBUG):
        r = client.get("/admin/backup", headers=API)
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    assert re.search(r'filename="tracker-\d{8}-\d{4}\.db"', r.headers["content-disposition"])
    path = os.path.join(tempfile.mkdtemp(), "copy.db")
    with open(path, "wb") as f:
        f.write(r.content)
    copy = sqlite3.connect(path)
    assert copy.execute("SELECT value FROM meta_kv WHERE key='marker'").fetchone() == ("yes",)
    assert copy.execute("SELECT order_id FROM orders").fetchall() == [("1301",)]
    copy.close()
    leftover = {f for f in set(os.listdir(tempfile.gettempdir())) - before if f.startswith("tracker-backup-")}
    assert leftover == set()                                          # the temp file is gone
    assert not any(ADMIN in r.getMessage() for r in caplog.records)


# =====================================================================================
# R12: pixel names
# =====================================================================================

def test_pixels_are_called_by_their_names(monkeypatch):
    monkeypatch.setattr(config, "EXTRA_PIXELS", [{"pixel_id": BACKUP_ID, "token": "t", "test_event_code": ""},
                                                 {"pixel_id": "555", "token": "t", "test_event_code": ""}])
    assert [p["name"] for p in hub._pixels()] == ["Core Club", "Eczema", "Pixel 555"]
    assert watchdog.pixel_label(MAIN) == "Core Club (main)" and watchdog.pixel_label(BACKUP_ID) == "Eczema (backup)"
    db.kv_set(f"pixel_name:{BACKUP_ID}", "QC Pixel")                  # Meta's own name, once read
    assert watchdog.pixel_name(BACKUP_ID) == "QC Pixel"
    monkeypatch.setenv("META_PIXEL_NAME", "Club")
    monkeypatch.setenv("META_PIXEL_ID_2", BACKUP_ID)
    monkeypatch.setenv("META_PIXEL_NAME_2", "Eczema backup")
    monkeypatch.setattr(config, "PIXEL_NAME_OVERRIDES", config._pixel_name_overrides())
    assert watchdog.pixel_label(MAIN) == "Club (main)" and watchdog.pixel_name(BACKUP_ID) == "Eczema backup"


def test_a_failing_backup_is_named_in_the_report(monkeypatch):
    monkeypatch.setattr(config, "EXTRA_PIXELS", [{"pixel_id": BACKUP_ID, "token": "t", "test_event_code": ""}])
    for i in range(3):
        db.record_event("PageView", f"p{i}", "pixel", "failed", {"user_data": {}}, pixel_id=BACKUP_ID)
    assert any(p.startswith("Eczema (backup): 3 of 3 events failed") for p in worker.build_report()["problems"])


# =====================================================================================
# R13: orders placed before go-live
# =====================================================================================

def test_orders_before_go_live_are_marked_and_never_resent_from_the_hub(client, shop, meta):
    now = time.time()
    db.kv_set("tracking_start", str(now - 3600))
    skipped = order(1401, now - 7200)
    unseen = order(1402, now - 5400)
    rebill = order(1403, now - 7000, source_name="subscription_contract")
    live = order(1404, now - 600)
    shop.orders = [skipped, unseen, rebill, live]
    db.upsert_order(skipped)
    db.mark_order("1401", "skipped", kind="before_start")
    rows = {r["id"]: r for r in client.get("/hub/api/orders?range=7d", headers=API).json()["orders"]}
    for oid in ("1401", "1402", "1403"):
        assert rows[oid]["type"] == "before_go_live", oid
        assert rows[oid]["type_label"] == "Before go-live, WeTracked sent this" and rows[oid]["can_resend"] is False
    assert rows["1404"]["type"] == "new_sale" and rows["1404"]["can_resend"] is True
    # Still sales in the cards: only the feed's label changes.
    cards = client.get("/hub/api/overview?range=7d", headers=API).json()["cards"]
    assert cards["new_sales"]["count"] == 3 and cards["mrr"]["count"] == 1
    body = client.post("/hub/api/resend/1401", headers=POST).json()
    assert body["ok"] is False and body["status"] == "refused" and body["before_go_live"] is True
    assert body["message"].startswith("Not sent: this order was placed before go-live")
    assert "count it twice" in body["message"] and chr(0x2014) not in body["message"]
    assert meta.sent == [] and db.get_order("1401")["status"] == "skipped"


def test_claudes_resend_still_works_before_go_live_but_warns(client, shop, meta):
    now = time.time()
    db.kv_set("tracking_start", str(now - 3600))
    shop.orders = [order(1501, now - 7200)]
    r = client.post("/mcp?key=admin-test", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "tracker_resend_order", "arguments": {"order_id": "1501"}}},
        headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 200
    result = json.loads(r.json()["result"]["content"][0]["text"])
    assert result["status"] == "sent" and result["before_go_live"] is True
    assert "count it twice" in result["warning"] and "count it twice" in result["note"]
    assert [e["event_id"] for e in meta.events()] == ["order_1501"]


# =====================================================================================
# Review fixes: one decision, sent once, shown as sent
# =====================================================================================

def backup_down(monkeypatch, meta):
    """Eczema (the backup) refuses every event until up[0] is set; Core Club takes them.
    `refused` counts the refused Purchases."""
    monkeypatch.setattr(config, "EXTRA_PIXELS", [{"pixel_id": BACKUP_ID, "token": "b", "test_event_code": ""}])
    db.kv_set(f"pixel_start:{BACKUP_ID}", str(time.time() - 86400))
    real, up, refused = meta.capi, [False], []

    def handler(request):
        if f"/{BACKUP_ID}/" in request.url.path and not up[0]:
            if json.loads(request.content)["data"][0]["event_name"] == "Purchase":
                refused.append(1)
            return httpx.Response(400, json={"error": {"message": "Invalid parameter", "code": 100}})
        return real(request)
    monkeypatch.setattr(meta_capi, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return up, refused


def accepted(meta, pixel_id, event_id, name="Purchase"):
    """How many times a dataset accepted this event."""
    return sum(1 for pid, batch in meta.sent for e in batch
               if pid == pixel_id and e["event_id"] == event_id and e["event_name"] == name)


def test_a_browser_without_an_ad_click_waits_for_shopifys_visit_record(client, shop, meta, clock):
    """A matched browser with no ad click leaves only the first landing page,
    its time unknown. It waits like an unmatched sale, so an old click is never
    sent to Meta stamped with the purchase time."""
    created = clock[0] - 5
    landing = c3711()["landing_site"]                                # MOF 3, fbclid FAKEclick3711aaa
    db.upsert_session("b701", fbp="fb.1.1.701", checkout_token="chk701")             # no ad arrival, no _fbc
    db.upsert_session("b702", fbp="fb.1.1.702", checkout_token="chk702")
    # This browser still has its _fbc cookie for that click: its time is known, it goes at once.
    db.upsert_session("b703", fbp="fb.1.1.703", checkout_token="chk703",
                      fbc=attribution.make_fbc("FAKEclick3711aaa", created - 3600))
    for oid in (701, 702, 703):
        db.upsert_order(order(oid, created, landing_site=landing))
    assert asyncio.run(tracking.process_pending()) == {"pending": 2, "sent": 1}
    assert db.get_order("701")["wait_until"] == pytest.approx(created + 60)
    c = stored(703)
    assert (c["source"], c["ad_id"], c["ad_name"]) == ("click_id", MOF3, "MOF 3")
    assert c["click_at"] == pytest.approx(created - 3600, abs=0.002) and c["assists"] == []
    assert c["fbc"] == attribution.make_fbc("FAKEclick3711aaa", created - 3600)
    assert [e["custom_data"]["order_id"] for e in meta.events()] == ["703"]
    # Shopify's record of 701 turns up: that landing page was its first visit, 10 days ago.
    page = "https://getcoresupps.com" + landing
    shop.journeys["701"] = {"firstVisit": {"occurredAt": iso(created - 10 * 86400), "landingPage": page},
                            "lastVisit": {"occurredAt": iso(created - 60), "landingPage": "https://getcoresupps.com/"}}
    clock[0] = created + 61
    assert asyncio.run(tracking.process_pending()) == {"sent": 1, "pending": 1}
    c = stored(701)
    old = attribution.make_fbc("FAKEclick3711aaa", created - 10 * 86400)
    assert (c["meta"], c["source"], c["channel"], c["fbc"]) == (False, "", "Direct", old)
    assert meta.events()[-1]["custom_data"]["order_id"] == "701" and meta.events()[-1]["user_data"]["fbc"] == old
    # No record ever turns up for 702: at the last step it goes out with what it has, like before.
    clock[0] = created + 121
    assert asyncio.run(tracking.process_pending()) == {"pending": 1}
    clock[0] = created + 301
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert stored(702)["source"] == "first_visit_unverified" and stored(702)["meta"] is True


def test_a_known_older_click_stops_the_first_landing_page_selling_unverified():
    now = ny("16:23:44")
    nine_days = int((now - 9 * 86400) * 1000)
    # The old tracker's note carries the landing page's own click, 9 days old: its real
    # time is known, so the window applies. Meta gets it with that time.
    d = decide(c3709(now, click_ms=nine_days))
    c = d["attribution"]
    assert (c["meta"], c["source"], c["ad_id"], c["channel"]) == (False, "", None, "Direct")
    assert c["first_touch"]["ad_id"] == STATIC and c["first_touch"]["at"] == nine_days / 1000
    assert d["fbc"] == f"fb.1.{nine_days}.FAKEclick3709aaa"
    # The browser's only click is 8 days old. The landing page (an fbclid the pixel never
    # saw) came before it, so it is older still: no sale, and never stamped "now".
    stale = now - 8 * 86400
    sess = {"client_id": "b", "ad_params": json.dumps({"ad_id": BOF, "utm_source": "fb", "fbclid": "1"}),
            "ad_seen_at": stale, "fbc": attribution.make_fbc("FAKEstaleclick1", stale)}
    d = decide(c3711(now), sess)
    assert (d["attribution"]["meta"], d["attribution"]["ad_id"]) == (False, None)
    assert d["fbc"] == attribution.make_fbc("FAKEstaleclick1", stale)
    # A click from after the sale proves nothing about the landing page: it still sells.
    later = {**sess, "ad_seen_at": now + 3600, "fbc": attribution.make_fbc("FAKEstaleclick1", now + 3600)}
    c = decide(c3711(now), later)["attribution"]
    assert (c["meta"], c["source"], c["ad_id"]) == (True, "first_visit_unverified", MOF3)
    # Nothing else at all: the landing page still sells, unverified.
    assert decide(c3711(now))["attribution"]["source"] == "first_visit_unverified"


def test_the_landing_page_names_the_ad_of_the_same_click():
    # The note has names only; the landing page is the same click (fbclid) and has the ad id.
    for catalog in ([], CATALOG + [_ad("120250899999990090", "Static", "120250808379090090", "B1 VSL")]):
        c = decide(c3709(), catalog=catalog)["attribution"]
        assert (c["source"], c["ad_id"], c["ad_name"], c["adset_name"], c["ambiguous"]) == (
            "order_note", STATIC, "Static", "B1 VSL", False), catalog
        assert c["assists"] == []
    # The browser kept only the _fbc cookie of the landing page's click (its page view was lost).
    now = ny("16:23:44")
    sess = {"client_id": "b", "fbc": attribution.make_fbc("FAKEclick3711aaa", now - 600)}
    d = decide(c3711(now), sess)
    c = d["attribution"]
    assert (c["source"], c["ad_id"], c["ad_name"], c["adset_name"]) == ("click_id", MOF3, "MOF 3", "B2 Statics")
    assert c["assists"] == [] and d["fbc"] == sess["fbc"]              # never its own assist


# =====================================================================================
# P9: Shopify cuts landing_site at 255 characters, fbclid and all (the real #c3711)
# =====================================================================================

SHARED = "IwZXh0bgNhZW0BMAB"                   # the start many fbclids share
CUT = SHARED + "wZ"                           # what was left of #c3711's: still only the fbclid's header
FULL = CUT + "FAKEtail3711_aem_abcdefgh"
OTHER = CUT + "OTHERclickDifferentAd_aem_xyz"  # another click whose fbclid has the same header


def cut_landing(fbclid=FULL, after=""):
    """#c3711's ad link as Shopify stores it: cut at 255 characters."""
    return landing("/products/spermfuel", utm_source="fb", utm_medium="paid_social", utm_campaign="sperm",
                   utm_content="B2 Statics", utm_term="MOF 3", campaign_id=SPERM, adset_id="120250787597660090",
                   ad_id=MOF3, placement="Facebook_Mobile_Feed", fbclid=fbclid)[:255] + after


def browser_click(fbclid, at):
    """The buyer's browser kept only the _fbc cookie of its click (its page view was lost)."""
    return {"client_id": "b-3711", "fbc": attribution.make_fbc(fbclid, at)}


def test_c3711_a_landing_page_shopify_cut_short_names_the_ad_of_the_same_click():
    now = ny("16:23:44")
    o = {**c3711(now), "landing_site": cut_landing()}
    assert len(o["landing_site"]) == 255 and o["landing_site"].endswith("&fbclid=" + CUT)
    sess = browser_click(FULL, now - 220)
    # Changed on purpose: with no time for the landing page, its 19 characters are only the header
    # every fbclid starts with, so they can't say which click it was. "click_id" with no ad, and
    # MOF 3 is the first-touch assist.
    c = decide(o, sess)["attribution"]
    assert (c["source"], c["ad_id"], c["ad_name"]) == ("click_id", None, "")
    assert (c["first_touch"]["ad_id"], c["first_touch"]["at"]) == (MOF3, None)
    assert [(a["ad_id"], a.get("first_touch")) for a in c["assists"]] == [(MOF3, True)]
    # Shopify's first visit (the same cut page) within half an hour of the click: the same click.
    journey = {"firstVisit": {"occurredAt": iso(now - 900), "landingPage": o["landing_site"]}}
    d = decide(o, sess, journey=journey)
    c = d["attribution"]
    assert (c["source"], c["ad_id"], c["ad_name"], c["adset_name"], c["campaign_name"]) == (
        "click_id", MOF3, "MOF 3", "B2 Statics", "sperm")
    assert c["click_at"] == now - 220 and c["assists"] == []
    # The first landing page is that click, with Shopify's time; Meta gets the whole click id.
    assert (c["first_touch"]["ad_id"], c["first_touch"]["at"]) == (MOF3, now - 900)
    assert d["fbc"] == sess["fbc"] and CUT + "FAKEtail" in d["fbc"]
    # Shopify's first visit with the whole link: the same page, and so the same click.
    journey = {"firstVisit": {"occurredAt": iso(now - 900), "landingPage": "https://getcoresupps.com"
                              + cut_landing()[:-len(CUT)] + FULL}}
    assert decide(o, sess, journey=journey)["attribution"]["first_touch"]["at"] == now - 900


def test_a_cut_that_runs_past_the_header_matches_without_a_time():
    # A shorter link leaves more of the fbclid: past the header it is the click's own, so no time is needed.
    now = ny("16:23:44")
    whole = CUT + "FAKEtail3711_aem_" + "abcdefgh" * 8
    link = landing("/products/spermfuel", utm_source="fb", utm_medium="paid_social", utm_campaign="sperm",
                   utm_content="B2 Statics", utm_term="MOF 3", campaign_id=SPERM, adset_id="120250787597660090",
                   ad_id=MOF3, fbclid=whole)
    o = {**c3711(now), "landing_site": link[:255]}
    cut = attribution.fbclid_of(o["landing_site"])
    assert len(cut) >= attribution.CUT_FBCLID_UNTIMED and whole.startswith(cut) and cut != whole
    d = decide(o, browser_click(whole, now - 220))
    c = d["attribution"]
    assert (c["source"], c["ad_id"], c["click_at"], c["assists"]) == ("click_id", MOF3, now - 220, [])
    assert c["first_touch"]["at"] == now - 220 and whole in d["fbc"]
    # A landing page shorter than Shopify's limit wasn't cut: its fbclid is whole, and another.
    unc = {**o, "landing_site": link[:255 - 20]}
    assert not attribution.cut_short(unc["landing_site"])
    assert decide(unc, browser_click(whole, now - 220))["attribution"]["ad_id"] is None


def test_a_cut_fbclid_never_matches_on_the_start_many_clicks_share():
    now = ny("16:23:44")
    o = {**c3711(now), "landing_site": cut_landing()}

    def unmatched(d, first_at=None):
        c = d["attribution"]
        # The browser's click keeps no ad, and the landing page's ad stays a separate first touch.
        assert (c["source"], c["ad_id"], c["ad_name"]) == ("click_id", None, ""), c
        assert c["first_touch"]["ad_id"] == MOF3 and c["first_touch"]["at"] == first_at
        assert [(a["ad_id"], a.get("first_touch")) for a in c["assists"]] == [(MOF3, True)]
    # A different ad's click that shares only the common start: not a continuation of the cut one.
    other = SHARED + "xQFAKEotherAdClick_aem_zyxwvuts"
    unmatched(decide(o, browser_click(other, now - 220)))
    # Another click whose fbclid begins with the whole 19-character cut: the cut is only the header
    # every fbclid shares, so with no time for the landing page it is not that click.
    d = decide(o, browser_click(OTHER, now - 3600))
    unmatched(d)
    assert "OTHER" in d["fbc"]                                        # Meta still gets that click
    # The same with both moments known and more than half an hour apart.
    journey = {"firstVisit": {"occurredAt": iso(now - 3600 - 31 * 60), "landingPage": o["landing_site"]}}
    unmatched(decide(o, browser_click(OTHER, now - 3600), journey=journey), first_at=now - 3600 - 31 * 60)
    # A landing page cut down to the common start alone says nothing about which click it was.
    short = {**o, "landing_site": o["landing_site"][:-len(CUT)] + SHARED}
    assert short["landing_site"].endswith("&fbclid=" + SHARED)
    unmatched(decide(short, browser_click(FULL, now - 220)))
    # An fbclid Shopify didn't cut (a parameter comes after it) is a whole, different click.
    whole = {**o, "landing_site": cut_landing() + "&utm_id=1"}
    unmatched(decide(whole, browser_click(FULL, now - 220)))
    # Both moments known and hours apart: two clicks, however alike their ids look.
    journey = {"firstVisit": {"occurredAt": iso(now - 3 * 3600), "landingPage": o["landing_site"]}}
    unmatched(decide(o, browser_click(FULL, now - 220), journey=journey), first_at=now - 3 * 3600)
    # The browser's click named another ad (its link was seen): never borrowed, never the same click.
    sess = {**browser_click(FULL, now - 220), "ad_seen_at": now - 220, "ad_params": json.dumps(
        {"utm_source": "fb", "utm_content": "B1 Rips", "utm_term": "2", "campaign_id": SPERM, "ad_id": AD2,
         "fbclid": "1"})}
    c = decide(o, sess)["attribution"]
    assert (c["source"], c["ad_id"], c["first_touch"]["ad_id"], c["first_touch"]["at"]) == ("browser", AD2, MOF3, None)
    assert [a["ad_id"] for a in c["assists"]] == [MOF3]


def test_a_click_that_names_another_ad_never_takes_the_cut_landing_pages_ad():
    # A names-only record (the old tracker's note, a listicle click) of another ad, whose fbclid
    # begins with the cut: it keeps its own ad, and the landing page's MOF 3 is the first-touch assist.
    now = ny("16:23:44")
    o = {**c3711(now), "landing_site": cut_landing(),
         "note_attributes": notes(utm_source="fb", utm_content="B1 Rips", utm_term="2", utm_campaign="sperm",
                                  fbc=attribution.make_fbc(OTHER, now - 3600))}

    def own_ad(c, source, click_at, first_at, ad_id=AD2):
        assert (c["source"], c["ad_id"], c["ad_name"], c["adset_name"], c["click_at"]) == (
            source, ad_id, "2", "B1 Rips", click_at), c
        assert (c["first_touch"]["ad_id"], c["first_touch"]["at"]) == (MOF3, first_at)
        assert [(a["ad_id"], a.get("first_touch")) for a in c["assists"]] == [(MOF3, True)]
    # The hub's path for an order with no stored record: no browser, no visit record.
    d = decide(o)
    own_ad(d["attribution"], "order_note", now - 3600, None)
    assert "OTHER" in d["fbc"]
    # Shopify's first visit ten minutes before the note's click: the times fit, the ads don't.
    journey = {"firstVisit": {"occurredAt": iso(now - 4200), "landingPage": o["landing_site"]}}
    own_ad(decide(o, journey=journey)["attribution"], "order_note", now - 3600, now - 4200)
    # Without Meta's names the note's names stay, never swapped for the landing page's ad.
    c = decide(o, journey=journey, catalog=[])["attribution"]
    own_ad(c, "order_note", now - 3600, now - 4200, ad_id=None)
    assert c["ambiguous"] is True
    # The pixel saw both clicks: MOF 3 (its own click), then a listicle click on ad 2 (names only).
    t1, t2 = now - 7200, now - 7200 + 600
    mof3 = {"utm_source": "fb", "utm_content": "B2 Statics", "utm_term": "MOF 3", "campaign_id": SPERM, "ad_id": MOF3}
    lst = {"utm_source": "fb", "utm_content": "B1 Rips", "utm_term": "2", "utm_campaign": "sperm"}
    sess = {"client_id": "b-3711", "ad_params": json.dumps({**lst, "fbclid": "1"}), "ad_seen_at": t2,
            "fbc": attribution.make_fbc(OTHER, t2),
            "ad_history": json.dumps([attribution.ad_visit(mof3, t1, FULL), attribution.ad_visit(lst, t2, OTHER)])}
    plain = {**o, "note_attributes": []}
    d = decide(plain, sess)
    own_ad(d["attribution"], "browser", t2, None)
    assert d["fbc"] == sess["fbc"] and d["attribution"]["lp"] == "listicle"
    journey = {"firstVisit": {"occurredAt": iso(t1), "landingPage": o["landing_site"]}}
    own_ad(decide(plain, sess, journey=journey)["attribution"], "browser", t2, t1)
    c = decide(plain, sess, journey=journey, catalog=[])["attribution"]
    own_ad(c, "browser", t2, t1, ad_id=None)


def test_records_name_other_ads_by_id_else_by_their_names_either_way_round():
    other = attribution.other_ad
    assert other({"ad_id": MOF3}, {"ad_id": AD2}) and not other({"ad_id": MOF3}, {"ad_id": MOF3})
    landing_ad = ad("B2 Statics", "MOF 3")
    assert other(landing_ad, ad("B1 Rips", "2")) and not other(landing_ad, ad("MOF 3", "B2 Statics"))
    assert not other({**landing_ad, "ad_id": MOF3}, ad("B2 Statics", "MOF 3"))       # names agree with the id
    assert other(ad("B1 Rips", "5"), ad("B1 Rips", "2"))                             # one ad set, two ads
    # The oldest links carried one name (and the ad set's id): it must be one of the other's names.
    assert not other(landing_ad, ad("MOF 3", "120250787597660090")) and other(landing_ad, ad("2", "120250787597660090"))
    # A record that names no ad can't be told apart from any.
    assert not other(landing_ad, attribution._ad_from_params({})) and not other({"ad_id": MOF3}, ad("B1 Rips", "2"))


def test_the_backfill_re_decides_a_cut_landing_page_it_never_sent(meta, shop):
    now = time.time()
    o = {**c3711(now - 1800), "id": 3713, "name": "#c3713", "checkout_token": "chk3713", "landing_site": cut_landing()}
    db.upsert_session("b-3713", checkout_token="chk3713", fbc=attribution.make_fbc(FULL, now - 2000))
    # Changed on purpose: the cut is only the fbclid's header, so Shopify's first visit has to date it.
    shop.journeys["3713"] = {"firstVisit": {"occurredAt": iso(now - 2100), "landingPage": o["landing_site"]}}
    db.upsert_order(o)
    db.mark_order("3713", "skipped", kind="before_start")
    old = {"v": 3, "meta": True, "source": "click_id", "click": True, "ad_id": None, "ad_name": "",
           "assists": [{"ad_id": MOF3, "ad_name": "MOF 3", "first_touch": True}]}
    db.set_order_attribution("3713", old)
    assert asyncio.run(tracking.backfill_attribution()) == 1
    rec = stored(3713)
    assert (rec["v"], rec["ad_id"], rec["ad_name"], rec["assists"]) == (attribution.RESOLVER_VERSION, MOF3, "MOF 3", [])
    assert meta.sent == [] and db.get_order("3713")["status"] == "skipped"     # never sent, never resent


def test_an_older_ad_link_opened_again_never_takes_over_from_a_newer_click(client, clock):
    x = ("https://getcoresupps.com/products/spermfuel?utm_source=fb&utm_campaign=sperm&utm_content=B1%20Rips"
         f"&utm_term=5&campaign_id={SPERM}&ad_id={AD5}&fbclid=FAKEclickXxxxx1")
    y = (x.replace(f"ad_id={AD5}", f"ad_id={AD2}").replace("utm_term=5", "utm_term=2")
         .replace("FAKEclickXxxxx1", "FAKEclickYyyyy2"))
    t1 = int(clock[0] * 1000) / 1000
    collect(client, name="page_viewed", url=x)
    clock[0] += 600
    t2 = int(clock[0] * 1000) / 1000
    collect(client, name="page_viewed", url=y)
    # An hour later the old tab comes back (a restored tab, the back button). The pixel
    # re-stamps its _fbc cookie with that old fbclid and the next page sends it.
    clock[0] += 3600
    collect(client, name="page_viewed", url=x)
    collect(client, name="product_viewed", url="https://getcoresupps.com/products/spermfuel",
            fbc=attribution.make_fbc("FAKEclickXxxxx1", clock[0]), custom={"items": [{"product_id": "111"}]})
    s = db.get_session("browser-1")
    assert json.loads(s["ad_params"])["ad_id"] == AD2 and s["ad_seen_at"] == t2
    assert s["fbc"] == attribution.make_fbc("FAKEclickYyyyy2", t2)
    history = json.loads(s["ad_history"])
    assert [(v["ad_id"], v["at"]) for v in history] == [(AD5, t1), (AD2, t2)]      # no new entry for the old click
    # The sale goes to the newer click, with its real time; the older one assists.
    d = decide(order(1, clock[0] + 60), s)
    assert (d["attribution"]["ad_id"], d["attribution"]["click_at"], d["fbc"]) == (AD2, t2, s["fbc"])
    assert [a["ad_id"] for a in d["attribution"]["assists"]] == [AD5]
    # Reloading the newer link is still the same click.
    clock[0] += 60
    collect(client, name="page_viewed", url=y)
    again = db.get_session("browser-1")
    assert (again["fbc"], again["ad_seen_at"], again["ad_history"]) == (s["fbc"], t2, s["ad_history"])


def test_a_record_the_tracker_sent_is_final_whatever_resolver_decided_it(client, shop, meta, monkeypatch):
    now = time.time()
    # The backfill never rewrites what Meta was sent, even a record from an older resolver.
    sent = c3711(now - 3600)
    db.upsert_order(sent)
    db.mark_order("3711", "sent", kind="purchase")
    old = {**decide(sent)["attribution"], "v": 1, "fbc": "fb.1.1790000000000.FAKEclick3711aaa"}
    db.set_order_attribution("3711", old)
    assert asyncio.run(tracking.backfill_attribution()) == 0 and stored(3711) == old
    # A partial send retried after a resolver bump: the backup gets the click Core Club got,
    # even though Shopify's record of a newer visit turned up since.
    up, _ = backup_down(monkeypatch, meta)
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 0)
    db.upsert_order(c3707(now - 60, click_ms=int((now - 120) * 1000)))
    assert asyncio.run(tracking.process_pending()) == {"failed": 1}
    first = stored(3707)
    monkeypatch.setattr(attribution, "RESOLVER_VERSION", attribution.RESOLVER_VERSION + 1)
    shop.journeys["3707"] = {"lastVisit": {"occurredAt": iso(now - 30), "landingPage": "https://getcoresupps.com/"
                                           "?utm_source=fb&ad_id=" + BOF + "&fbclid=FAKEotherclick1"}}
    up[0] = True
    tracking._next_try.clear()
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert stored(3707) == first
    fbcs = {pid: e["user_data"]["fbc"] for pid, batch in meta.sent for e in batch if e["event_id"] == "order_3707"}
    assert fbcs == {MAIN: first["fbc"], BACKUP_ID: first["fbc"]}


def test_resending_a_partly_failed_order_never_repeats_core_clubs_purchase(client, shop, meta, monkeypatch):
    """Core Club accepted it, only the backup failed: approving the suggestion
    sends it to the backup alone, and the retries after it never send it to
    Core Club again, every 10 seconds, until the backup recovers."""
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 0)
    up, refused = backup_down(monkeypatch, meta)
    now = time.time()
    o = order(2001, now - 3600)
    shop.orders = [o]
    db.upsert_order(o)
    for _ in range(3):
        assert asyncio.run(tracking.process_pending()) == {"failed": 1}
        tracking._next_try.clear()
    assert accepted(meta, MAIN, "order_2001") == 1 and len(refused) == 3
    asyncio.run(watchdog.propose(time.time()))
    (p,) = [p for p in db.proposals() if p["kind"] == "resend"]
    assert p["title"] == "Send order #c2001 to Eczema (backup)"
    assert p["detail"].startswith("Eczema (backup) hasn't accepted it after 3 tries. The other pixels already have it")
    assert chr(0x2014) not in p["title"] + p["detail"]
    body = client.post(f"/hub/api/proposals/{p['id']}/approve", headers=POST).json()
    assert body["proposal"]["status"] == "failed" and body["proposal"]["result"].startswith("Meta didn't accept it")
    assert accepted(meta, MAIN, "order_2001") == 1 and len(refused) == 4
    # The send loop's next ticks wait out the backoff, and never touch Core Club.
    for _ in range(3):
        assert asyncio.run(tracking.process_pending()) == {"pending": 1}
    assert accepted(meta, MAIN, "order_2001") == 1 and len(refused) == 4
    up[0] = True
    tracking._next_try.clear()
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert accepted(meta, MAIN, "order_2001") == 1 and accepted(meta, BACKUP_ID, "order_2001") == 1
    # The express-checkout InitiateCheckout (F6): once per pixel, through every retry and the resend.
    for pid in (MAIN, BACKUP_ID):
        assert accepted(meta, pid, "checkout_chk2001", "InitiateCheckout") == 1

    # Every pixel has it already: no suggestion, and approving an old one sends nothing.
    db.mark_order("2001", "failed", error="HTTP 400", kind="purchase")
    db._c().execute("UPDATE orders SET attempts=5 WHERE order_id='2001'")
    assert tracking.missing_datasets(db.get_order("2001")) == []
    db._c().execute("DELETE FROM proposals")
    asyncio.run(watchdog.propose(time.time()))
    assert db.proposals() == []
    db.add_proposal("resend:2001", "resend", "Send order #c2001 to Meta", "d", {"type": "resend_order",
                                                                             "order_id": "2001"})
    pid = db.proposals()[0]["id"]
    body = client.post(f"/hub/api/proposals/{pid}/approve", headers=POST).json()
    assert body["proposal"]["status"] == "done" and body["proposal"]["result"].startswith("Already sent")
    assert accepted(meta, MAIN, "order_2001") == 1 and accepted(meta, BACKUP_ID, "order_2001") == 1


def test_the_resend_button_sends_once_more_and_its_retries_never_loop(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 0)
    up, refused = backup_down(monkeypatch, meta)
    o = order(2004, time.time() - 3600)
    shop.orders = [o]
    db.upsert_order(o)
    assert asyncio.run(tracking.process_pending()) == {"failed": 1}
    # The owner asks for a resend: every pixel gets it once more (Meta dedupes on event_id).
    body = client.post("/hub/api/resend/2004", headers=POST).json()
    assert body["status"] == "failed" and accepted(meta, MAIN, "order_2004") == 2
    assert db.get_order("2004")["forced"] == 1
    for _ in range(3):                                     # retries: the backup only
        tracking._next_try.clear()
        assert asyncio.run(tracking.process_pending()) == {"failed": 1}
    assert accepted(meta, MAIN, "order_2004") == 2 and len(refused) == 5
    up[0] = True
    tracking._next_try.clear()
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert accepted(meta, MAIN, "order_2004") == 2 and accepted(meta, BACKUP_ID, "order_2004") == 1
    # The resend repeats the Purchase, never the express-checkout InitiateCheckout (F6).
    for pid in (MAIN, BACKUP_ID):
        assert accepted(meta, pid, "checkout_chk2004", "InitiateCheckout") == 1


def test_a_resend_suggestion_closes_itself_when_the_order_is_skipped(client, meta):
    now = time.time()
    db.upsert_order(order(2003, now - 3600))
    db.mark_order("2003", "failed", error="HTTP 500", kind="purchase")
    db._c().execute("UPDATE orders SET attempts=5 WHERE order_id='2003'")
    asyncio.run(watchdog.propose(now))
    db.mark_order("2003", "skipped", kind="too_old", count_attempt=False)
    asyncio.run(watchdog.propose(now))
    (p,) = db.proposals()
    assert p["status"] == "done" and p["result"] == ("Not needed any more: it is older than the 7 days Meta "
                                                     "accepts. Nothing was sent.")
    assert meta.sent == []


def test_an_approved_mrr_tag_never_relabels_an_order_a_pixel_already_has(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 0)
    loop = "Recurring Order (Loop)"
    now = time.time()
    sold = order(2101, now - 600, tags=loop)                  # sent as a Purchase
    db.upsert_order(sold)
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    # Core Club takes the next one as a Purchase, the backup is failing.
    up, _ = backup_down(monkeypatch, meta)
    partly = order(2201, now - 300, tags=loop)
    db.upsert_order(partly)
    assert asyncio.run(tracking.process_pending()) == {"failed": 1}
    unsent = order(2102, now - 120, tags=loop)                # not handled by the tracker yet
    shop.orders = [sold, partly, unsent]
    rows = {r["id"]: r for r in client.get("/hub/api/orders?range=7d", headers=API).json()["orders"]}
    assert {oid: r["type"] for oid, r in rows.items()} == {"2101": "new_sale", "2201": "new_sale",
                                                           "2102": "new_sale"}
    asyncio.run(watchdog.propose(time.time()))
    pid = next(p["id"] for p in db.proposals() if p["kind"] == "renewal_tag")
    body = client.post(f"/hub/api/proposals/{pid}/approve", headers=POST).json()
    assert body["ok"] is True and "Orders already sent stay as they were" in body["proposal"]["result"]
    # The backup gets the same Purchase; nothing goes out as MRR for an order already reported.
    up[0] = True
    tracking._next_try.clear()
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert accepted(meta, MAIN, "order_2201") == accepted(meta, BACKUP_ID, "order_2201") == 1
    assert meta.events("SubscriptionRenewal") == []
    assert db.get_order("2201")["kind"] == "purchase" and db.renewal_orders_sent_as_purchase(now - 86400) == []
    # The hub keeps showing what Meta got; only the order not sent yet becomes MRR.
    hub._orders_cache.clear()
    rows = {r["id"]: r for r in client.get("/hub/api/orders?range=7d", headers=API).json()["orders"]}
    assert (rows["2101"]["type"], rows["2101"]["type_label"], rows["2101"]["tracker_status"]) == (
        "new_sale", "New sale", "sent")
    assert rows["2201"]["type"] == "new_sale"
    assert (rows["2102"]["type"], rows["2102"]["type_label"]) == ("rebill", "MRR")
    assert tracking.classify_order(unsent) == "renewal"
    # The watchdog's per-ad sales count reads the same thing.
    week = {r["order_id"]: r for r in db.orders_since(now - 86400, ("sent", "failed", "pending"))}
    assert [tracking.is_new_sale(week[oid]["order_json"], week[oid]["reported"] or "") for oid in ("2101", "2201")] == [
        True, True]
    assert tracking.is_new_sale(unsent) is False
    cards = client.get("/hub/api/overview?range=7d", headers=API).json()["cards"]
    assert cards["new_sales"]["count"] == 2 and cards["mrr"]["count"] == 1


def test_orders_from_the_test_phase_read_before_go_live(client, shop, meta):
    now = time.time()
    db.kv_set("tracking_start", str(now - 3600))
    test_phase = order(2301, now - 5 * 3600)                   # sent in test mode: only reached Test Events
    forced = order(2302, now - 5 * 3600)                       # sent live after go-live by Claude's tool
    shop.orders = [test_phase, forced]
    for o, sent_at in ((test_phase, now - 5 * 3600 + 60), (forced, now - 600)):
        oid = str(o["id"])
        db.upsert_order(o)
        db.mark_order(oid, "sent", kind="purchase")
        db.record_event("Purchase", f"order_{oid}", "webhook", "sent", {"user_data": {"em": ["x"]}},
                        order_id=oid, pixel_id=MAIN)
        db._c().execute("UPDATE orders SET sent_at=? WHERE order_id=?", (sent_at, oid))
        db._c().execute("UPDATE events SET created_at=? WHERE order_id=?", (sent_at, oid))
    rows = {r["id"]: r for r in client.get("/hub/api/orders?range=7d", headers=API).json()["orders"]}
    early = rows["2301"]
    assert (early["type"], early["type_label"], early["can_resend"]) == (
        "before_go_live", "Before go-live, WeTracked sent this", False)
    assert [p["sent"] for p in early["pixels"]] == [False]
    late = rows["2302"]
    assert (late["type"], late["type_label"], late["tracker_status"], late["can_resend"]) == (
        "new_sale", "New sale", "sent", False)
    assert [p["sent"] for p in late["pixels"]] == [True]
    body = client.post("/hub/api/resend/2301", headers=POST).json()
    assert body["status"] == "refused" and accepted(meta, MAIN, "order_2301") == 0


def test_only_meta_utm_tags_without_ids_mean_the_old_listicle():
    # The old listicle forwarded Meta's utm tags (and the click id) and dropped the ad ids.
    assert attribution.landing_page({"utm_source": "fb", "utm_content": "B1 Rips", "utm_term": "2", "fbclid": "1"}) == ("listicle", True)
    # The same tags with no click id: a link typed or shared by hand, not an ad.
    assert attribution.landing_page({"utm_source": "fb", "utm_content": "B1 Rips", "utm_term": "2"}) == ("", False)
    # Shopify's own links (a store preview, the Shop app) and email tags are not a listicle.
    assert attribution.landing_page({"utm_source": "shop-website", "utm_medium": "referral"}) == ("", False)
    assert attribution.landing_page({"utm_source": "klaviyo", "utm_campaign": "welcome"}) == ("", False)
    assert attribution.landing_page({"utm_source": "fb", "ad_id": "120250785421790090"}) == ("", False)


# =====================================================================================
# F5: a sale sent as a bare click gets its ad named later (#c3711), nothing sent
# =====================================================================================

def sent_bare_click(now, oid=3711):
    """#c3711 as the tracker sent it: the browser kept only its _fbc cookie and Shopify cut the
    landing page's fbclid, so the record Meta's Purchase came from names no ad."""
    o = {**c3711(now - 1800), "id": oid, "name": f"#c{oid}", "checkout_token": f"chk{oid}",
         "landing_site": cut_landing()}
    d = decide(o, browser_click(FULL, now - 2000))
    rec = {**d["attribution"], "fbc": d["fbc"]}
    assert (rec["source"], rec["ad_id"], rec["ad_name"]) == ("click_id", None, "")
    db.upsert_order(o)
    db.mark_order(str(oid), "sent", kind="purchase")
    db.set_order_attribution(str(oid), rec)
    return o, rec


def whole_visit(fbclid, at):
    """Shopify's record of a last visit through #c3711's ad link, whole (never cut)."""
    return {"lastVisit": {"occurredAt": iso(at), "landingPage": "https://getcoresupps.com"
                          + cut_landing()[:-len(CUT)] + fbclid}}


def test_a_sent_click_is_named_from_shopifys_last_visit_and_nothing_is_sent(meta, shop):
    now = time.time()
    o, before = sent_bare_click(now)
    db.kv_set("attribution_backfill", str(attribution.RESOLVER_VERSION))     # the re-decide already ran
    shop.journeys["3711"] = whole_visit(FULL, now - 2000)                     # the very same fbclid
    # It runs at every start, whatever resolver version the backfill is on.
    assert asyncio.run(tracking.backfill_attribution()) == 1
    rec = stored(3711)
    assert {k: rec[k] for k in attribution.IDENTITY_KEYS} == {
        "ad_id": MOF3, "adset_id": "120250787597660090", "campaign_id": SPERM, "ad_name": "MOF 3",
        "adset_name": "B2 Statics", "campaign_name": "sperm", "lp": "", "ids_stripped": False}
    assert rec["identity_refreshed"] is True
    # Only the ad's names, ids and landing page: the click Meta got (fbc, source, time) is untouched.
    def rest(r):
        return {k: v for k, v in r.items() if k not in (*attribution.IDENTITY_KEYS, "identity_refreshed")}
    assert rest(rec) == rest(before) and rec["fbc"] == before["fbc"] and rec["click_at"] == before["click_at"]
    assert meta.sent == [] and db.get_order("3711")["status"] == "sent"         # never sent, never resent
    # Idempotent: a named record is left alone, and Shopify isn't asked again.
    calls = shop.graphql_calls
    assert asyncio.run(tracking.backfill_attribution()) == 0
    assert stored(3711) == rec and shop.graphql_calls == calls and meta.sent == []


def test_a_sent_click_is_named_only_when_a_record_is_provably_that_click():
    now = time.time()
    o = {**c3711(now - 1800), "landing_site": cut_landing()}
    d = decide(o, browser_click(FULL, now - 2000))
    rec = {**d["attribution"], "fbc": d["fbc"]}
    name = attribution.sent_click_identity
    # The cut landing page alone is only the header every fbclid starts with: no time, no name.
    assert name(rec, o) is None
    # Shopify dates that landing page within half an hour of the click Meta got: the same click.
    near = {"firstVisit": {"occurredAt": iso(now - 2000 - 900), "landingPage": o["landing_site"]}}
    got = name(rec, o, near)
    assert (got["ad_id"], got["ad_name"], got["identity_refreshed"]) == (MOF3, "MOF 3", True)
    assert set(got) == {*attribution.IDENTITY_KEYS, "identity_refreshed"}
    # Hours apart: two clicks, however alike their ids look.
    far = {"firstVisit": {"occurredAt": iso(now - 2000 - 3 * 3600), "landingPage": o["landing_site"]}}
    assert name(rec, o, far) is None
    # Shopify's last visit through another click (its own fbclid) is not the click Meta got.
    assert name(rec, o, whole_visit(OTHER, now - 2000)) is None
    # Only a bare click the tracker sent is named: never a named record, one it never sent,
    # or another source.
    same = whole_visit(FULL, now - 2000)
    assert name(rec, o, same)["ad_id"] == MOF3
    assert name({**rec, "ad_name": "MOF 3"}, o, same) is None
    assert name({k: v for k, v in rec.items() if k != "fbc"}, o, same) is None
    assert name({**rec, "source": "browser"}, o, same) is None


def test_the_hub_names_a_sent_click_when_it_shows_the_order(client, shop, meta):
    now = time.time()
    named, before = sent_bare_click(now)
    unnamed, _ = sent_bare_click(now, oid=3712)          # no visit record: it stays unknown
    shop.orders = [named, unnamed]
    shop.journeys["3711"] = whole_visit(FULL, now - 2000)
    rows = {r["id"]: r for r in client.get("/hub/api/orders?range=7d", headers=API).json()["orders"]}
    ad = rows["3711"]["ad"]
    assert (ad["ad_id"], ad["ad_name"], ad["adset_name"], ad["source"]) == (MOF3, "MOF 3", "B2 Statics", "click_id")
    # Its first-visit ad turned out to be the ad that sold: never listed as its own assist.
    assert "assists" not in ad
    assert stored(3711)["identity_refreshed"] is True and stored(3711)["fbc"] == before["fbc"]
    assert rows["3712"]["ad"]["ad_name"] == "" and stored(3712)["ad_id"] is None
    # Still unknown: the Assists section calls the ad that got the sale "Meta ad (name unknown)".
    body = client.get("/hub/api/assists?range=7d", headers=API).json()
    assert [(r["ad_id"], r["ad_name"], [(c["ad_id"], c["ad_name"], c["sales"]) for c in r["closers"]])
            for r in body["rows"]] == [(MOF3, "MOF 3", [("", "Meta ad (name unknown)", 1)])]
    # An unnamed sale is looked for again at most once an hour; a named one never again. Nothing is sent.
    calls = shop.graphql_calls
    client.get("/hub/api/orders?range=7d", headers=API)
    assert shop.graphql_calls == calls and meta.sent == []


# =====================================================================================
# F6: an express checkout (Shop Pay, Google Pay, Apple Pay from the cart) still reports a checkout
# =====================================================================================

def with_backup(monkeypatch):
    monkeypatch.setattr(config, "EXTRA_PIXELS", [{"pixel_id": BACKUP_ID, "token": "b", "test_event_code": ""}])
    db.kv_set(f"pixel_start:{BACKUP_ID}", str(time.time() - 86400))


def pixel_checkout(cid, token="", ago=60, status="sent"):
    """A checkout_started the storefront pixel reported from browser `cid`, `ago` seconds ago."""
    db.upsert_session(cid, fbp=f"fb.1.1.{cid}", checkout_token=token)
    eid = f"InitiateCheckout_{cid}"
    db.record_event("InitiateCheckout", eid, "pixel", status, {"user_data": {}}, client_id=cid)
    db._c().execute("UPDATE events SET created_at=? WHERE event_id=?", (time.time() - ago, eid))


def sent_names(meta):
    return [(pid, e["event_name"]) for pid, batch in meta.sent for e in batch]


def test_an_express_checkout_gets_an_initiatecheckout_right_before_its_purchase(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 0)
    with_backup(monkeypatch)
    o = order(2401, time.time() - 120)                  # paid from the cart: the pixel saw no checkout
    shop.orders = [o]
    db.upsert_order(o)
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    # Each pixel: the InitiateCheckout, then the Purchase.
    assert sent_names(meta) == [(MAIN, "InitiateCheckout"), (MAIN, "Purchase"),
                                (BACKUP_ID, "InitiateCheckout"), (BACKUP_ID, "Purchase")]
    checkout, purchase = [e for pid, batch in meta.sent if pid == MAIN for e in batch]
    assert checkout == {
        "event_name": "InitiateCheckout", "event_time": int(tracking._parse_time(o["created_at"]) - 1),
        "event_id": "checkout_chk2401", "action_source": "website", "event_source_url": "https://getcoresupps.com",
        "user_data": purchase["user_data"],
        "custom_data": {k: purchase["custom_data"][k] for k in ("value", "currency", "content_ids", "contents",
                                                                "num_items")}}
    assert checkout["custom_data"]["value"] == 59.95 and checkout["user_data"]["em"]
    # Recorded under the order like the Purchase, one per pixel; the order is still what its Purchase was.
    assert [(e["event_name"], e["pixel_id"], e["source"], e["status"])
            for e in reversed(db.events_for_order("2401"))] == [
        ("InitiateCheckout", MAIN, "webhook", "sent"), ("Purchase", MAIN, "webhook", "sent"),
        ("InitiateCheckout", BACKUP_ID, "webhook", "sent"), ("Purchase", BACKUP_ID, "webhook", "sent")]
    assert tracking.reported_kind("2401") == "purchase" and db.orders_by_id(["2401"])["2401"]["reported"] == "Purchase"
    assert db.sent_order_events(["2401"])["2401"]["event_name"] == "Purchase"
    row = client.get("/hub/api/orders?range=7d", headers=API).json()["orders"][0]
    assert [p["sent"] for p in row["pixels"]] == [True, True]
    # The Purchase is exactly the one it would be without the InitiateCheckout.
    monkeypatch.setattr(tracking, "express_checkout_event", lambda *a: None)
    db._c().execute("DELETE FROM events")
    db.reset_order("2401")
    meta.sent.clear()
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert [e for pid, batch in meta.sent if pid == MAIN for e in batch] == [purchase]


def test_no_server_initiatecheckout_when_the_pixel_saw_the_buyers_checkout(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 0)
    now = time.time()

    def names(o):
        meta.sent.clear()
        db.upsert_order(o)
        assert asyncio.run(tracking.process_pending()) == {"sent": 1}
        return [n for _, n in sent_names(meta)]
    # The browser with the order's checkout token started a checkout: the pixel already sent it.
    pixel_checkout("b2402", "chk2402", ago=300)
    assert names(order(2402, now - 60)) == ["Purchase"]
    # Matched by client id: the browser the old tracker's note names (by its fbp).
    pixel_checkout("b2403")
    assert names(order(2403, now - 60, note_attributes=notes(fbp="fb.1.1.b2403"))) == ["Purchase"]
    # A checkout the pixel couldn't get to Meta still happened.
    pixel_checkout("b2404", "chk2404", status="failed")
    assert names(order(2404, now - 60)) == ["Purchase"]
    # From about an hour before the order on.
    pixel_checkout("b2405", "chk2405", ago=1800 + 50 * 60)
    assert names(order(2405, now - 1800)) == ["Purchase"]
    pixel_checkout("b2406", "chk2406", ago=2 * 3600)
    assert names(order(2406, now - 60)) == ["InitiateCheckout", "Purchase"]
    # Someone else's checkout says nothing about this buyer.
    pixel_checkout("b-else", "chk-else")
    assert names(order(2407, now - 60)) == ["InitiateCheckout", "Purchase"]
    # No checkout token on the order: the event id falls back to the order id.
    assert names(order(2408, now - 60, checkout_token=None)) == ["InitiateCheckout", "Purchase"]
    assert meta.sent[0][1][0]["event_id"] == "checkout_order_2408"


def test_never_an_initiatecheckout_for_mrr_tests_skips_early_or_browserless_orders(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 0)
    monkeypatch.setattr(config, "SEND_TEST_ORDERS", True)
    now = time.time()
    db.upsert_order(order(2501, now - 60, source_name="subscription_contract"))     # MRR
    db.upsert_order(order(2502, now - 60, test=True))                                # a test order Meta still gets
    db.upsert_order(order(2503, now - 60, cancelled_at=iso(now - 30)))              # skipped
    db.upsert_order(order(2504, now - 60, browser_ip=None, client_details={}))      # no browser at all
    assert asyncio.run(tracking.process_pending()) == {"sent": 3, "skipped": 1}
    assert sorted(n for _, n in sent_names(meta)) == ["Purchase", "Purchase", "SubscriptionRenewal"]
    assert meta.events()[-1]["action_source"] == "other"               # not a website event: Meta needs a browser
    # Placed before the tracking start and forced out by Claude's resend tool: the Purchase only.
    early = order(2505, now - 2 * 86400)
    shop.orders = [early]
    meta.sent.clear()
    res = asyncio.run(tracking.resend_order("2505"))
    assert res["status"] == "sent" and res["before_go_live"] is True
    assert [n for _, n in sent_names(meta)] == ["Purchase"]


def test_an_order_is_what_its_purchase_was_whatever_the_initiatecheckout_did(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 0)
    real, refuse = meta.capi, ["InitiateCheckout"]

    def handler(request):
        if json.loads(request.content)["data"][0]["event_name"] in refuse:
            return httpx.Response(400, json={"error": {"message": "Invalid parameter", "code": 100}})
        return real(request)
    monkeypatch.setattr(meta_capi, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    # Meta refuses the InitiateCheckout: recorded as failed, the Purchase goes out all the same.
    db.upsert_order(order(2601, time.time() - 60))
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    row = db.get_order("2601")
    assert (row["status"], row["last_error"]) == ("sent", None)
    assert [(e["event_name"], e["status"]) for e in reversed(db.events_for_order("2601"))] == [
        ("InitiateCheckout", "failed"), ("Purchase", "sent")]
    assert [n for _, n in sent_names(meta)] == ["Purchase"] and asyncio.run(tracking.process_pending()) == {}
    # Only the InitiateCheckout reached Meta: the order isn't reported yet (no pixel tick, a
    # rebill tag added now still counts), and the retry that gets the Purchase through never repeats it.
    refuse[:] = ["Purchase"]
    meta.sent.clear()
    db.upsert_order(order(2602, time.time() - 60, tags=""))
    assert asyncio.run(tracking.process_pending()) == {"failed": 1}
    assert [n for _, n in sent_names(meta)] == ["InitiateCheckout"]
    assert db.sent_event_name("2602") == "" and tracking.reported_kind("2602") == ""
    assert db.sent_order_events(["2602"]) == {} and db.orders_by_id(["2602"])["2602"]["reported"] is None
    assert db.refresh_order_tags({"id": 2602, "tags": "VIP"}) is True
    refuse[:] = []
    tracking._next_try.clear()
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert [n for _, n in sent_names(meta)] == ["InitiateCheckout", "Purchase"]


def test_the_funnel_counts_an_express_buyer_once(client, shop, meta, monkeypatch):
    monkeypatch.setattr(config, "PURCHASE_GRACE_SECONDS", 0)
    db.upsert_session("b2701", fbp="fb.1.1.2701", checkout_token="chk2701")
    for name in ("PageView", "AddToCart"):
        db.record_event(name, f"{name}-2701", "pixel", "sent", {"user_data": {}}, client_id="b2701")
    o = order(2701, time.time() - 60)                   # paid from the cart
    shop.orders = [o]
    db.upsert_order(o)
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert [n for _, n in sent_names(meta)] == ["InitiateCheckout", "Purchase"]
    # A buyer counts at every step once: the server's InitiateCheckout adds no second checkout.
    body = client.get("/hub/api/funnel?range=7d", headers=API).json()
    assert body["other"] == [1, 1, 1, 1, 1] and body["all"] == [1, 1, 1, 1, 1] and body["meta"] == [0] * 5


def test_repeat_add_to_cart_and_checkout_from_one_shopper_reach_meta_once(meta):
    # #c3714: one buyer added to cart and started checkout three times in six minutes,
    # and Meta counted 3 add to carts and 3 checkouts for one sale.
    def ev(name, n, products=("111",)):
        return {"event_name": name, "event_id": f"{name}_sh-{n}", "event_time": int(time.time()),
                "action_source": "website", "user_data": {"fbp": "fb.1.10.99"},
                "custom_data": {"content_ids": list(products)}}

    def send(e, cid="browser-1"):
        asyncio.run(tracking.send_pixel_event(e, cid))

    for n in range(3):
        send(ev("AddToCart", f"a{n}"))
        send(ev("InitiateCheckout", f"c{n}"))
    send(ev("AddToCart", "other", ("222",)))              # a different product is a new add to cart
    send(ev("AddToCart", "b1"), cid="browser-2")         # and another shopper is another shopper
    got = [(e["event_name"], e["event_id"]) for e in meta.events("AddToCart") + meta.events("InitiateCheckout")]
    assert got.count(("AddToCart", "AddToCart_sh-a0")) >= 1 and got.count(("InitiateCheckout", "InitiateCheckout_sh-c0")) >= 1
    assert not [g for g in got if g[1] in ("AddToCart_sh-a1", "AddToCart_sh-a2", "InitiateCheckout_sh-c1",
                                           "InitiateCheckout_sh-c2")]
    assert ("AddToCart", "AddToCart_sh-other") in got and ("AddToCart", "AddToCart_sh-b1") in got
    # Kept for the hub's funnel, marked as repeats, never counted as sent or failed.
    stats = db.event_stats(0)["by_event"]
    assert stats["AddToCart"] == {"sent": 3, "repeat": 2} and stats["InitiateCheckout"] == {"sent": 1, "repeat": 2}
    # Once the window has passed, the same step reaches Meta again.
    with db._lock:
        db._c().execute("UPDATE events SET created_at=created_at-? WHERE status='sent'", (tracking.REPEAT_WINDOW + 5,))
    send(ev("InitiateCheckout", "c9"))
    assert ("InitiateCheckout", "InitiateCheckout_sh-c9") in [(e["event_name"], e["event_id"]) for e in meta.events("AddToCart") + meta.events("InitiateCheckout")]


def test_ids_in_the_utm_tags_are_the_ads_ids_not_stripped_tags():
    # The listicle campaign's ads use utm_content={{ad.id}}&utm_term={{adset.id}}&utm_campaign={{campaign.id}}.
    url = ("https://getcoresupps.com/products/x?utm_source=facebook&utm_medium=paid&utm_campaign=120250600384390090"
           "&utm_content=120250602689570090&utm_term=120250602689530090&fbclid=FAKEclick1&lp=listicle")
    p = attribution.ad_params_from_url(url)
    assert (p["ad_id"], p["adset_id"], p["campaign_id"]) == ("120250602689570090", "120250602689530090",
                                                            "120250600384390090")
    assert "utm_content" not in p and "utm_term" not in p and "utm_campaign" not in p
    assert attribution.landing_page(p) == ("listicle", False)
    # A click history written before the fix: the id kept as the ad's name, marked stripped.
    old = [{"ad_id": "", "ad_name": "120250602689570090", "adset_name": "", "campaign_name": "120250600384390090",
            "at": 5.0, "lp": "listicle", "ids_stripped": True}]
    (v,) = attribution.ad_history(old)
    assert (v["ad_id"], v["ad_name"], v["campaign_name"], v.get("ids_stripped")) == ("120250602689570090", "", "", None)
    # Names and the oldest template (ad name in utm_content, ad set id in utm_term) are left alone.
    legacy = {"utm_source": "fb", "utm_content": "Hook B", "utm_term": "120250602689530090"}
    assert attribution.utm_ids(legacy) is legacy
    named = {"utm_source": "fb", "utm_content": "B1 Rips", "utm_term": "2", "utm_campaign": "sperm"}
    assert attribution.utm_ids(named) is named


def test_a_click_minutes_after_another_ad_is_not_that_ad_and_the_record_follows_the_click_meta_got(meta):
    # #c3744: ad "6" at 03:30:41, then BOF at 03:37:47, whose page view the pixel reported only
    # after the sale. The fbc Meta was sent is BOF's click; the record must never name ad 6 for it.
    now = time.time()
    six_at, bof_ms = now - 800, int((now - 380) * 1000)
    fbc = attribution.make_fbc("FAKEbofclick123", bof_ms / 1000)
    six = {"ad_id": "AD6", "ad_name": "6", "adset_name": "B5 Statics - Her 2 Him", "campaign_name": "sperm",
           "at": six_at, "click": attribution.click_key("FAKEsixclick123")}
    db.upsert_session("b3744", fbp="fb.1.1.3744", checkout_token="chk3744", fbc=fbc,
                      ad_params=json.dumps({"ad_id": "AD6", "utm_source": "fb", "fbclid": "1"}), ad_seen_at=six_at,
                      ad_history=json.dumps([six]))
    o = order(3744, now - 60)
    sess = db.get_session("b3744")
    rec = attribution.resolve(o, sess)["attribution"]
    assert rec["source"] == "click_id" and not rec["ad_id"]              # not ad 6: its click was another one
    assert "AD6" in [a.get("ad_id") for a in rec["assists"]]
    db.upsert_order(o)
    db.mark_order("3744", "sent", kind="purchase")
    db.set_order_attribution("3744", {**rec, "fbc": fbc})
    # The late page view: BOF's arrival, stamped with its click.
    bof = {"ad_id": "ADBOF", "ad_name": "BOF", "adset_name": "B1 VSL", "campaign_name": "sperm",
           "at": bof_ms / 1000, "click": attribution.click_key("FAKEbofclick123")}
    db.upsert_session("b3744", ad_params=json.dumps({"ad_id": "ADBOF", "utm_source": "ig", "fbclid": "1"}),
                      ad_seen_at=bof_ms / 1000, ad_history=json.dumps([six, bof]))
    stored = db.orders_by_id(["3744"])["3744"]["attribution"]
    assert asyncio.run(tracking.realign_sent(o, stored)) is True
    fixed = db.orders_by_id(["3744"])["3744"]["attribution"]
    assert (fixed["ad_id"], fixed["fbc"], fixed["realigned"]) == ("ADBOF", fbc, True)
    assert [a.get("ad_id") for a in fixed["assists"]] == ["AD6"]
    assert asyncio.run(tracking.realign_sent(o, fixed)) is False         # settled: nothing more to change
    assert meta.events() == []                                           # nothing was sent to Meta


def test_the_closing_ad_under_its_old_link_name_is_never_its_own_assist():
    # #c3737: BOF's link still said "MOF 3 - Copy" in B1 VSL. An earlier visit with those
    # names and no ids is BOF itself, not another ad that helped.
    now = time.time()
    bof = {"ad_id": "ADBOF", "ad_name": "MOF 3 - Copy", "adset_name": "B1 VSL", "at": now - 300,
           "click": attribution.click_key("FAKEbofagain1")}
    sess = {"client_id": "b3737", "fbc": attribution.make_fbc("FAKEbofagain1", now - 300),
            "ad_params": json.dumps({"ad_id": "ADBOF", "utm_source": "ig", "utm_content": "B1 VSL",
                                     "utm_term": "MOF 3 - Copy", "fbclid": "1"}), "ad_seen_at": now - 300,
            "ad_history": json.dumps([bof])}
    o = order(3737, now - 60, landing_site="/products/spermfuel?utm_source=ig&utm_content=B1%20VSL"
                                          "&utm_term=MOF%203%20-%20Copy&fbclid=FAKEbofearly1")
    rec = attribution.resolve(o, sess)["attribution"]
    assert rec["ad_id"] == "ADBOF" and rec["assists"] == []
    # A record already stored with it (decided before this fix) loses it; a real other ad stays.
    stored = {**rec, "fbc": sess["fbc"], "assists": [
        {"ad_id": "", "ad_name": "MOF 3 - Copy", "adset_name": "B1 VSL", "at": now - 86400},
        {"ad_id": "AD6", "ad_name": "6", "adset_name": "B5 Statics - Her 2 Him", "at": now - 900}]}
    kept = tracking._without_self_assists(stored, sess)["assists"]
    assert [a["ad_id"] for a in kept] == ["AD6"]


def test_meta_tags_without_a_click_id_are_a_hand_shared_link_not_a_stripped_ad():
    # A phone opened /products/spermfuel?utm_source=fb&utm_content=B1 with no fbclid: someone typed
    # or shared the link. Not the listicle, and nothing for the health check to flag.
    hand = attribution.ad_params_from_url("/products/spermfuel?utm_source=fb&utm_medium=paid_social"
                                          "&utm_campaign=sperm&utm_content=B1")
    assert hand["utm_content"] == "B1" and "fbclid" not in hand
    assert attribution.landing_page(hand) == ("", False)
    # The same tags behind a real ad click are the old listicle, as before.
    clicked = attribution.ad_params_from_url("/products/spermfuel?utm_source=fb&utm_medium=paid_social"
                                             "&utm_campaign=sperm&utm_content=B1&fbclid=FAKEhandclick1")
    assert attribution.landing_page(clicked) == ("listicle", True)
    # The old tracker's note: its fbc is the click.
    o = order(3801, time.time() - 60, note_attributes=[
        {"name": "fbc", "value": attribution.make_fbc("FAKEnoteclick12", time.time() - 300)},
        {"name": "utm_source", "value": "fb"}, {"name": "utm_content", "value": "B1 Rips"},
        {"name": "utm_term", "value": "2"}])
    rec = attribution.resolve(o, {})["attribution"]
    assert rec["source"] == "order_note" and rec["lp"] == "listicle" and rec["ids_stripped"] is True


def test_a_first_day_record_without_the_sent_click_gets_it_from_the_purchase(shop, meta):
    # #c3711 and #c3712 were decided before records kept the fbc Meta was sent, so the ad name
    # refresh and the realign check skipped them. At startup the fbc comes from the Purchase itself.
    now = time.time()
    fbc = attribution.make_fbc("FAKEfirstdayclick", now - 200)
    o = order(3711, now - 100)
    db.upsert_order(o)
    db.mark_order("3711", "sent", kind="purchase")
    db.record_event("Purchase", "order_3711", "webhook", "sent", {"user_data": {"fbc": fbc, "em": ["h"]}},
                    order_id="3711", pixel_id=config.META_PIXEL_ID)
    db.set_order_attribution("3711", {"v": 4, "meta": True, "source": "click_id", "click": True, "ad_id": None,
                                      "ad_name": "", "adset_name": "", "campaign_name": "", "assists": []})
    assert attribution.needs_identity(db.orders_by_id(["3711"])["3711"]["attribution"]) is False
    asyncio.run(tracking.refresh_identities())
    rec = db.orders_by_id(["3711"])["3711"]["attribution"]
    assert rec["fbc"] == fbc and rec["source"] == "click_id" and attribution.needs_identity(rec) is True
    assert meta.events() == []                                            # nothing sent or resent
    # A record that has its fbc is never touched.
    assert db.keep_sent_fbc("3711", "fb.1.1.OTHER") is False and db.orders_by_id(["3711"])["3711"]["attribution"]["fbc"] == fbc
