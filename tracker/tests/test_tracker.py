import asyncio
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import tempfile
import logging
import time

import httpx
import pytest
from starlette.testclient import TestClient

import app as app_module
import attribution
import config
import db
import meta_capi
import shopify
import tracking
import worker


def sha(v):
    return hashlib.sha256(v.encode()).hexdigest()


def iso(seconds_ago=0):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds_ago)).isoformat()


class FakeMeta:
    def __init__(self):
        self.events = []
        self.responses = []          # queue of (status, json) to return first
        self.fail_by_pixel = {}      # pixel id -> queue of (status, json) for that dataset only
        self.calls = []              # (pixel id, access token, event names) per accepted request

    def handler(self, request: httpx.Request):
        pixel_id = request.url.path.rstrip("/").split("/")[-2]
        body = json.loads(request.content)
        # The express-checkout InitiateCheckout sent before a Purchase (F6) always goes
        # through: the queued failures are meant for the order's own event.
        checkout = body["data"][0]["event_id"].startswith("checkout_")
        if not checkout and self.fail_by_pixel.get(pixel_id):
            status, body = self.fail_by_pixel[pixel_id].pop(0)
            return httpx.Response(status, json=body)
        if not checkout and self.responses:
            status, body = self.responses.pop(0)
            return httpx.Response(status, json=body)
        self.events.extend(body["data"])
        self.calls.append((pixel_id, body["access_token"], [e["event_name"] for e in body["data"]]))
        return httpx.Response(200, json={"events_received": len(body["data"]), "fbtrace_id": "trace123"})

    def names_for(self, pixel_id):
        return [n for p, _, names in self.calls if p == pixel_id for n in names]


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "tracker.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "_conn", None)
    db.init()
    db.kv_set("tracking_start", str(time.time() - 86400))
    db.kv_set("mode", "live")
    tracking._next_try.clear()
    app_module._hits.clear()
    yield


@pytest.fixture
def meta(monkeypatch):
    fake = FakeMeta()
    monkeypatch.setattr(meta_capi, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    return fake


async def _no_sleep(_):
    return None


def order(**over):
    o = {
        "id": 5550001, "name": "#c3703", "email": "Jane.Doe@Example.com ", "phone": "(647) 555-0199",
        "created_at": iso(300), "processed_at": iso(300), "test": False, "source_name": "web",
        "financial_status": "paid", "total_price": "59.95", "subtotal_price": "49.95", "currency": "USD",
        "checkout_token": "chk_abc", "browser_ip": "203.0.113.9",
        "client_details": {"user_agent": "Mozilla/5.0 iPhone"},
        "landing_site": "/products/spermfuel?utm_source=fb",
        "note_attributes": [],
        "customer": {"id": 777, "email": "jane.doe@example.com"},
        "billing_address": {"first_name": "Jané", "last_name": "Doe", "city": "Oak Ville",
                            "province_code": "ON", "zip": "L6M 5P6", "country_code": "CA"},
        "shipping_address": {},
        "line_items": [{"product_id": 111, "variant_id": 222, "sku": "SF-1", "quantity": 2, "price": "24.98"}],
    }
    o.update(over)
    return o


# --- hashing ------------------------------------------------------------------

def test_normalisation_matches_meta_rules():
    assert meta_capi.norm_email("  Jane.Doe@Example.COM ") == "jane.doe@example.com"
    assert meta_capi.norm_phone("(647) 555-0199", "CA") == "16475550199"
    assert meta_capi.norm_phone("+44 020 7946 0958", "GB") == "442079460958"
    assert meta_capi.norm_phone("6475550199", "") == ""          # no country: no guessing
    assert meta_capi.norm_zip("90210-1234", "US") == "90210"
    assert meta_capi.norm_zip("L6M 5P6", "CA") == "l6m5p6"
    assert meta_capi.norm_name(" Jané ") == "jané"           # Meta keeps accented letters
    assert meta_capi.norm_city("Tromsø") == "tromsø"
    assert meta_capi.norm_city("Oak Ville") == "oakville"
    ud = meta_capi.build_user_data(emails=["A@B.co", "a@b.co"], fbp="fb.1.1.2", ip="1.2.3.4")
    assert ud["em"] == [sha("a@b.co")]                 # deduped after normalising
    assert ud["fbp"] == "fb.1.1.2" and ud["client_ip_address"] == "1.2.3.4"   # not hashed


# --- order classification & payloads ------------------------------------------

def test_classify_orders():
    assert tracking.classify_order(order()) == "purchase"
    assert tracking.classify_order(order(source_name="subscription_contract_checkout_one")) == "renewal"
    assert tracking.classify_order(order(test=True)) == "test"
    assert tracking.classify_order(order(created_at=iso(8 * 86400), processed_at=iso(8 * 86400))) == "too_old"


def test_purchase_event_uses_order_and_browser_identifiers():
    sess = {"client_id": "shopify-client-1", "fbp": "fb.1.10.99", "fbc": "fb.1.20.CLICK",
            "landing_url": "https://getcoresupps.com/products/spermfuel?fbclid=CLICK"}
    ev = tracking.build_order_event(order(), "purchase", sess)
    ud = ev["user_data"]
    assert ev["event_name"] == "Purchase" and ev["event_id"] == "order_5550001"
    assert ev["action_source"] == "website"
    assert ud["fbc"] == "fb.1.20.CLICK" and ud["fbp"] == "fb.1.10.99"
    assert ud["client_ip_address"] == "203.0.113.9" and ud["client_user_agent"] == "Mozilla/5.0 iPhone"
    assert ud["em"] == [sha("jane.doe@example.com")]
    assert ud["ph"] == [sha("16475550199")]
    assert ud["fn"] == [sha("jané")] and ud["zp"] == [sha("l6m5p6")] and ud["st"] == [sha("on")]
    assert ud["external_id"] == [sha("shopify-client-1"), sha("777")]
    cd = ev["custom_data"]
    assert cd["value"] == 59.95 and cd["currency"] == "USD"
    assert cd["content_ids"] == ["111"] and cd["num_items"] == 2


def test_renewal_is_separate_event_without_stale_click_ids():
    o = order(source_name="subscription_contract_checkout_one", browser_ip=None, client_details={},
              note_attributes=[{"name": "fbc", "value": "fb.1.1.OLDCLICK"}, {"name": "fbp", "value": "fb.1.1.5"}])
    sess, how = tracking.match_session(o)
    ev = tracking.build_order_event(o, "renewal", sess)
    assert ev["event_name"] == "SubscriptionRenewal"
    assert ev["event_id"] == "renewal_5550001"
    assert ev["action_source"] == "system_generated"
    for key in ("fbc", "fbp", "client_ip_address", "client_user_agent"):
        assert key not in ev["user_data"]
    assert "em" in ev["user_data"]


# --- webhook signature --------------------------------------------------------

def test_webhook_hmac():
    body = b'{"id":1}'
    good = base64.b64encode(hmac.new(b"whsec_test", body, hashlib.sha256).digest()).decode()
    assert shopify.verify_webhook(body, good)
    assert not shopify.verify_webhook(body, "bad")
    assert not shopify.verify_webhook(b'{"id":2}', good)


# --- end to end ---------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, meta):
    monkeypatch.setattr(worker, "start", lambda: [])
    pending = []
    monkeypatch.setattr(tracking, "fire_and_forget", lambda coro: pending.append(coro))
    app_module.mcp._session_manager = None        # the MCP session manager runs once per app
    with TestClient(app_module.create_app()) as c:
        c.pending = pending
        yield c


def run_pending(client):
    while client.pending:
        asyncio.run(client.pending.pop(0))


def pixel(client, **payload):
    base = {"id": f"evt{time.time_ns()}", "ts": int(time.time() * 1000),
            "url": "https://getcoresupps.com/products/spermfuel?fbclid=CLICK",
            "cid": "shopify-client-1", "fbp": "fb.1.10.99", "fbc": "fb.1.20.CLICK"}
    base.update(payload)
    return client.post("/collect", content=json.dumps(base),
                       headers={"Content-Type": "text/plain", "X-Forwarded-For": "6.6.6.6, 198.51.100.7",
                                "User-Agent": "Mozilla/5.0 Test"})


def signed_webhook(client, payload):
    body = json.dumps(payload).encode()
    sig = base64.b64encode(hmac.new(b"whsec_test", body, hashlib.sha256).digest()).decode()
    return client.post("/webhooks/shopify", content=body,
                       headers={"X-Shopify-Hmac-Sha256": sig, "X-Shopify-Topic": "orders/create"})


def test_full_flow_pixel_to_purchase(client, meta):
    r = pixel(client, name="page_viewed")
    assert r.status_code == 204
    r = pixel(client, name="product_viewed", custom={
        "value": 29.95, "currency": "USD",
        "items": [{"product_id": "gid://shopify/Product/111", "variant_id": "222", "price": 29.95}]})
    assert r.status_code == 204
    r = pixel(client, name="checkout_started", checkout={"token": "chk_abc", "email": "jane.doe@example.com"},
              custom={"value": 59.95, "currency": "USD", "items": [{"product_id": "111", "quantity": 2}]})
    assert r.status_code == 204
    run_pending(client)
    names = [e["event_name"] for e in meta.events]
    assert names == ["PageView", "ViewContent", "InitiateCheckout"]
    page = meta.events[0]
    assert page["user_data"]["client_ip_address"] == "198.51.100.7"
    assert page["user_data"]["client_user_agent"] == "Mozilla/5.0 Test"
    assert meta.events[1]["custom_data"]["content_ids"] == ["111"]
    assert meta.events[2]["user_data"]["em"] == [sha("jane.doe@example.com")]

    # The order arrives by webhook and is matched to the browser via checkout token.
    assert signed_webhook(client, order(created_at=iso(5), processed_at=iso(5))).status_code == 200
    counts = asyncio.run(tracking.process_pending())
    assert counts == {"sent": 1}
    purchase = meta.events[-1]
    assert purchase["event_name"] == "Purchase" and purchase["event_id"] == "order_5550001"
    assert purchase["user_data"]["fbc"] == "fb.1.20.CLICK"
    assert db.get_order("5550001")["status"] == "sent"

    # Duplicate webhook + poller pickup never double-send.
    signed_webhook(client, order())
    assert asyncio.run(tracking.process_pending()) == {}
    assert sum(1 for e in meta.events if e["event_name"] == "Purchase") == 1


SPERM2_URL = ("https://getcoresupps.com/products/spermfuel?fbclid=fbclid&utm_source=fb&utm_medium=paid_social"
              "&utm_campaign=sperm+2&utm_content=New+Sales+Ad+-+Copy+7&utm_term=NB7-i&campaign_id=120250978399360090"
              "&adset_id=120250978777170090&ad_id=120250978777250090&lp=ranking-listicle")
CART = {"value": 59.95, "currency": "USD", "items": [{"product_id": "111", "quantity": 3}]}


def _sperm2_visit(client, cid, **checkout):
    """A shopper from a sperm 2 ad (Oct 5 2026): the ads' website URL ended in ?fbclid=fbclid, so Meta
    added no click id; the listicle passed the stand-in on and Meta's pixel made a cookie out of it."""
    cookie = f"fb.1.{int(time.time() * 1000)}.fbclid"
    assert pixel(client, name="page_viewed", url=SPERM2_URL, cid=cid, fbc=cookie).status_code == 204
    assert pixel(client, name="product_added_to_cart", url=SPERM2_URL, cid=cid, fbc=cookie, custom=CART).status_code == 204
    assert pixel(client, name="checkout_started", url=SPERM2_URL, cid=cid, fbc=cookie, custom=CART,
                 checkout={"token": checkout.get("token", "chk_" + cid)}).status_code == 204
    run_pending(client)


def _rows(cid):
    return [(r["event_name"], r["status"], r["source"]) for r in db.recent_events(limit=50) if r.get("client_id") == cid]


def test_a_stand_in_click_id_is_never_sent_and_the_campaign_is_named(client, meta):
    import watchdog
    _sperm2_visit(client, "sperm2-a")
    sent = [e for e in meta.events if e["event_name"] == "PageView"]
    assert sent and all("fbc" not in e["user_data"] for e in sent)     # no click id beats a made-up one
    sess = db.get_session("sperm2-a")
    assert not sess["fbc"] and json.loads(sess["ad_params"])["ad_id"] == "120250978777250090"   # still the ad's visit
    # The health panel names the campaign, as information (changed on purpose from a failure: the owner
    # keeps sperm 2's links as they are, and the tracker handles them).
    c = watchdog._stand_in_check(time.time())
    assert c["status"] == "ok" and '"sperm 2" (1 visit)' in c["detail"]
    assert "wait for the shopper's email at checkout" in c["detail"]
    assert "New ads: leave ?fbclid=fbclid out of the website URL." in c["detail"]
    assert watchdog._stand_in_check(time.time() + 2 * 86400)["status"] == "ok"
    # And the last gate: whatever the source, a stand-in fbc never reaches Meta.
    assert "fbc" not in meta_capi.build_user_data(fbc="fb.1.1791179139300.{{fbclid}}")
    for fake in ("ASfbclid", "fbclid123", "%7Bfbclid%7D", "undefined", "{fbclid}", "click_id"):
        assert attribution.stand_in(fake), fake
    for real in ("IwZXh0bgNhZW0BMABhZGlk", "PAZXh0bgNhZW0B", "IwY2xjawUs", "CLICK", "OLD", "99"):
        assert not attribution.stand_in(real), real
    assert meta_capi.build_user_data(fbc="fb.1.20.CLICK")["fbc"] == "fb.1.20.CLICK"


def test_without_a_click_id_add_to_cart_and_checkout_wait_for_the_email_then_go_with_it(client, meta):
    _sperm2_visit(client, "sperm2-b")
    # Held: nothing went to Meta yet, but the funnel has both steps when they happened.
    assert [e["event_name"] for e in meta.events] == ["PageView"]
    assert {("AddToCart", "held", "pixel"), ("InitiateCheckout", "held", "pixel")} <= set(_rows("sperm2-b"))
    steps = {r["event_name"] for r in db.storefront_funnel(time.time() - 600) if r["client_id"] == "sperm2-b"}
    assert {"AddToCart", "InitiateCheckout"} <= steps
    # The shopper types their email at checkout: both go to Meta with it, at the time they happened,
    # so Meta can credit them to the ad the way it credits the sale.
    held_times = {r["event_name"]: r["payload"]["event_time"] for r in db.held_events(0, client_id="sperm2-b")}
    assert pixel(client, name="checkout_contact_info_submitted", url=SPERM2_URL, cid="sperm2-b",
                 checkout={"token": "chk_sperm2-b", "email": "buyer@example.com", "phone": "+16475550199"}).status_code == 204
    run_pending(client)
    out = {e["event_name"]: e for e in meta.events if e["event_name"] in ("AddToCart", "InitiateCheckout")}
    assert set(out) == {"AddToCart", "InitiateCheckout"}
    for name, e in out.items():
        assert e["user_data"]["em"] == [sha("buyer@example.com")] and e["user_data"].get("ph")
        assert "fbc" not in e["user_data"] and e["event_time"] == held_times[name]
    rows = set(_rows("sperm2-b"))
    assert {("AddToCart", "released", "pixel"), ("AddToCart", "sent", "released")} <= rows
    # Once, even when the next checkout event comes in.
    pixel(client, name="checkout_address_info_submitted", url=SPERM2_URL, cid="sperm2-b",
          checkout={"token": "chk_sperm2-b", "email": "buyer@example.com"})
    run_pending(client)
    assert sum(1 for e in meta.events if e["event_name"] == "AddToCart") == 1


def test_held_events_go_with_the_order_or_after_two_hours_without_it(client, meta, monkeypatch):
    # A shopper who pays with no checkout step on record: the order says who they are.
    _sperm2_visit(client, "sperm2-c", token="chk_sperm2-c")
    signed_webhook(client, order(id=5560001, checkout_token="chk_sperm2-c", created_at=iso(5), processed_at=iso(5)))
    asyncio.run(tracking.process_pending())
    names = [e["event_name"] for e in meta.events]
    assert "Purchase" in names and "AddToCart" in names
    atc = next(e for e in meta.events if e["event_name"] == "AddToCart")
    assert atc["user_data"]["em"] == [sha("jane.doe@example.com")]
    # A shopper who never says: after 2 hours they go as they are.
    meta.events.clear()
    _sperm2_visit(client, "sperm2-d")
    assert asyncio.run(tracking.release_expired()) == 0                # too soon
    monkeypatch.setattr(tracking, "HOLD_SECONDS", 0)
    tracking._held_sweep["at"] = 0.0
    assert asyncio.run(tracking.release_expired()) == 2
    late = [e for e in meta.events if e["event_name"] in ("AddToCart", "InitiateCheckout")]
    assert len(late) == 2 and all("em" not in e["user_data"] for e in late)
    # A shopper with a real click is never held.
    meta.events.clear()
    pixel(client, name="product_added_to_cart", cid="real-click", custom=CART)
    run_pending(client)
    assert [e["event_name"] for e in meta.events] == ["AddToCart"] and meta.events[0]["user_data"]["fbc"] == "fb.1.20.CLICK"


# #c4081 (Oct 5 2026): sperm 2's "New Sales Ad - Copy" has no URL parameters of its own, so its link
# carried only Meta's automatic tags (the ids in utm_content / utm_term / utm_campaign, utm_id, no
# utm_source, no ad_id) and the stand-in fbclid=fbclid.
AUTO_TAGS_URL = ("https://getcoresupps.com/products/spermfuel?fbclid=fbclid&utm_medium=paid&utm_id=120250978399360090"
                 "&utm_content=120250979179220090&utm_term=120250978988210090&utm_campaign=120250978399360090"
                 "&lp=ranking-listicle")


def test_a_link_with_only_metas_own_tags_and_a_stand_in_is_still_the_ads_visit(client, meta):
    pixel(client, name="page_viewed", url=AUTO_TAGS_URL, cid="c4081-buyer", fbc="fb.1.1791226190000.fbclid")
    pixel(client, name="checkout_started", url=AUTO_TAGS_URL, cid="c4081-buyer", fbc="fb.1.1791226190000.fbclid",
          checkout={"token": "chk_c4081"}, custom=CART)
    run_pending(client)
    params = json.loads(db.get_session("c4081-buyer")["ad_params"])
    assert (params["ad_id"], params["adset_id"], params["campaign_id"]) == (
        "120250979179220090", "120250978988210090", "120250978399360090")
    assert "fbclid" not in params                              # a visit from the ad, not a click
    signed_webhook(client, order(id=5570001, checkout_token="chk_c4081", created_at=iso(5), processed_at=iso(5)))
    asyncio.run(tracking.process_pending())
    rec = db.get_order("5570001")["attribution"]
    rec = json.loads(rec) if isinstance(rec, str) else rec
    assert rec["meta"] is True and rec["ad_id"] == "120250979179220090" and rec["click"] is False
    assert rec["fbc"] == "" and rec["channel"] != "Direct"
    purchase = next(e for e in meta.events if e["event_name"] == "Purchase")
    assert "fbc" not in purchase["user_data"]                     # Meta never hears of a click there wasn't


def test_a_sale_credited_to_no_ad_while_its_visit_went_unrecorded_is_credited_once_it_is_found(client, meta):
    # The state #c4081 was left in: the pixel's page view (with the link) is on record, but the browser
    # holds no ad visit, so the sale went to Meta without a click and was credited to no ad.
    db.upsert_session("c4081-lost", fbp="fb.1.10.99", checkout_token="chk_lost", ip="198.51.100.7",
                      user_agent="Mozilla/5.0 Test")
    db.record_event("PageView", "PageView_lost1", "pixel", "sent",
                    {"event_name": "PageView", "event_source_url": AUTO_TAGS_URL, "user_data": {}},
                    client_id="c4081-lost")
    signed_webhook(client, order(id=5570002, checkout_token="chk_lost", created_at=iso(5), processed_at=iso(5),
                                 landing_site=None))              # like #c4081: Shopify kept no landing page
    asyncio.run(tracking.process_pending())
    before = db.get_order("5570002")["attribution"]
    before = json.loads(before) if isinstance(before, str) else before
    assert before["ad_id"] is None and before["fbc"] == ""
    sent = len(meta.events)
    # The next start replays the visit once and the check credits the sale to its ad; Meta is sent nothing.
    asyncio.run(tracking.backfill_attribution())
    after = db.get_order("5570002")["attribution"]
    after = json.loads(after) if isinstance(after, str) else after
    assert after["ad_id"] == "120250979179220090" and after["fbc"] == "" and after["realigned"] is True
    assert len(meta.events) == sent
    assert tracking.replay_dropped_arrivals() == 0             # once


# #c4085 (Oct 5 2026, 11:41 PM): the "top" ads had no URL parameters at all, so the listicle had only the
# stand-in to pass on: /products/spermfuel?fbclid=fbclid&lp=ranking-listicle. Nothing in the link names
# the ad; the live ads' links do, by elimination (meta_ads.ad_links, tracking.name_from_ad_links).
BARE_URL = "https://getcoresupps.com/products/spermfuel?fbclid=fbclid&lp=ranking-listicle"
LISTICLE = "https://fertility-supplements-ranked.netlify.app/"
NAMING_TAGS = "utm_source={{site_source_name}}&utm_campaign={{campaign.name}}&ad_id={{ad.id}}"


def _graph_ad(ad_id, name, adset, link, tags, status="ACTIVE"):
    return {"id": ad_id, "name": name, "effective_status": status, "adset": {"id": "set-" + adset, "name": adset},
            "campaign": {"id": "120250978399360090", "name": "sperm 2"},
            "creative": {"url_tags": tags, "object_story_spec": {"link_data": {"link": link}}}}


def _ads_account(monkeypatch, ads, hourly):
    """Meta's Marketing API for one ad account: its ads with their links, and link clicks per hour.
    Returns the list of paths asked (an hourly read is marked ?hourly)."""
    import meta_ads
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    monkeypatch.setattr(config, "META_ADS_TOKEN", "ads-secret")
    calls = []

    def graph(request):
        path = request.url.path
        calls.append(path + ("?hourly" if request.url.params.get("breakdowns") else ""))
        if path.endswith("/act_123"):
            return httpx.Response(200, json={"name": "Core", "currency": "USD", "timezone_name": "America/New_York"})
        if path.endswith("/act_123/ads"):
            return httpx.Response(200, json={"data": ads})
        if path.endswith("/act_123/insights"):
            return httpx.Response(200, json={"data": hourly if request.url.params.get("breakdowns") else []})
        return httpx.Response(400, json={"error": {"message": "not in this fake"}})
    meta_ads.set_http_client(httpx.AsyncClient(transport=httpx.MockTransport(graph)))
    return calls


def _rec(order_id):
    rec = db.get_order(str(order_id))["attribution"]
    return json.loads(rec) if isinstance(rec, str) else rec


def _bare_visit(client, cid, token):
    cookie = f"fb.1.{int(time.time() * 1000)}.fbclid"
    pixel(client, name="page_viewed", url=BARE_URL, cid=cid, fbc=cookie)
    pixel(client, name="checkout_started", url=BARE_URL, cid=cid, fbc=cookie, checkout={"token": token}, custom=CART)
    run_pending(client)


def test_a_sale_whose_link_named_no_ad_is_named_from_the_live_ads_links(client, meta, monkeypatch):
    import meta_ads
    ads = [_graph_ad("111", "New Sales Ad - Copy", "top", LISTICLE + "?fbclid=fbclid", ""),
           _graph_ad("112", "New Sales Ad - Copy 2", "top", LISTICLE + "?fbclid=fbclid", ""),
           _graph_ad("113", "New Sales Ad - Copy 3", "NB6", LISTICLE + "?fbclid=fbclid", NAMING_TAGS),
           _graph_ad("114", "Static 1", "B8", "https://getcoresupps.com/products/spermfuel", "")]
    hourly = []
    calls = _ads_account(monkeypatch, ads, hourly)
    _bare_visit(client, "c4085-buyer", "chk_c4085")
    o = order(id=5580001, checkout_token="chk_c4085", created_at=iso(5), processed_at=iso(5), landing_site=None)
    signed_webhook(client, o)
    asyncio.run(tracking.process_pending())
    rec = _rec(5580001)
    # A Meta ad visit with nothing to name the ad. Two live ads without URL parameters lead to the
    # listicle (the store-link one can't be it: the shopper came through the listicle); Meta hasn't
    # filed the click under either yet, so nothing is guessed and the next check asks again.
    assert rec["meta"] is True and rec["ad_id"] is None and rec["fbc"] == "" and "named_by" not in rec
    assert any(c.endswith("/act_123/ads") for c in calls) and any(c.endswith("?hourly") for c in calls)
    assert asyncio.run(tracking.realign_sent(o, rec)) is False
    # Meta shows the click on one of them in the hour the shopper arrived: that is the ad.
    from zoneinfo import ZoneInfo
    hour = dt.datetime.fromtimestamp(rec["click_at"], ZoneInfo("America/New_York")).hour
    hourly[:] = [{"ad_id": "111", "inline_link_clicks": "1",
                  "hourly_stats_aggregated_by_advertiser_time_zone": f"{hour:02d}:00:00 - {hour:02d}:59:59"}]
    meta_ads._cache.clear()
    assert asyncio.run(tracking.realign_sent(o, rec)) is True
    rec = _rec(5580001)
    assert (rec["ad_id"], rec["ad_name"], rec["adset_name"], rec["campaign_name"]) == (
        "111", "New Sales Ad - Copy", "top", "sperm 2")
    assert rec["named_by"] == "ad_links" and rec["realigned"] is True and rec["ambiguous"] is False
    assert rec["click"] is False and rec["fbc"] == "" and rec["channel"] == "Meta ads" and rec["lp"] == "ranking-listicle"
    # Decided once, and Meta was sent the one Purchase, with no click id.
    assert asyncio.run(tracking.realign_sent(o, rec)) is False
    purchases = [e for e in meta.events if e["event_name"] == "Purchase"]
    assert len(purchases) == 1 and "fbc" not in purchases[0]["user_data"]


def test_the_only_live_ad_without_parameters_is_named_at_once_and_a_tie_is_named_to_its_ad_set_later(client, meta, monkeypatch):
    ads = [_graph_ad("114", "Static 1", "B8", "https://getcoresupps.com/products/spermfuel", ""),
           _graph_ad("111", "New Sales Ad - Copy", "top", LISTICLE + "?fbclid=fbclid", ""),
           _graph_ad("112", "New Sales Ad - Copy 2", "top", LISTICLE + "?fbclid=fbclid", ""),
           _graph_ad("115", "Saved", "NB6-", "http://google.com/", "")]      # a "reject save": never the ad of a sale
    calls = _ads_account(monkeypatch, ads, [])
    # Straight to the store with the stand-in: one live ad with a store link has no parameters.
    direct = "https://getcoresupps.com/products/spermfuel?fbclid=fbclid"
    pixel(client, name="page_viewed", url=direct, cid="direct-buyer", fbc="fb.1.30.fbclid")
    pixel(client, name="checkout_started", url=direct, cid="direct-buyer", fbc="fb.1.30.fbclid",
          checkout={"token": "chk_direct"}, custom=CART)
    run_pending(client)
    signed_webhook(client, order(id=5580002, checkout_token="chk_direct", created_at=iso(5), processed_at=iso(5),
                                 landing_site=None))
    asyncio.run(tracking.process_pending())
    rec = _rec(5580002)
    assert rec["ad_id"] == "114" and rec["ad_name"] == "Static 1" and rec["named_by"] == "ad_links" and rec["lp"] == ""
    assert not any(c.endswith("?hourly") for c in calls)            # one candidate: Meta's clicks aren't needed
    # Through the listicle, two candidates, and Meta never files a click: after SETTLE_SECONDS the sale
    # is credited to their ad set and campaign, marked ambiguous, rather than left as "a Meta ad".
    _bare_visit(client, "late-buyer", "chk_late")
    o = order(id=5580003, checkout_token="chk_late", created_at=iso(5), processed_at=iso(5), landing_site=None)
    signed_webhook(client, o)
    asyncio.run(tracking.process_pending())
    rec = _rec(5580003)
    assert rec["ad_id"] is None and "named_by" not in rec
    monkeypatch.setattr(tracking, "SETTLE_SECONDS", 0)
    assert asyncio.run(tracking.realign_sent(o, rec)) is True
    rec = _rec(5580003)
    assert rec["ad_id"] is None and rec["ad_name"] == "" and rec["ambiguous"] is True
    assert (rec["adset_id"], rec["adset_name"], rec["campaign_name"]) == ("set-top", "top", "sperm 2")
    assert rec["named_by"] == "ad_links"
    assert asyncio.run(tracking.realign_sent(o, rec)) is False       # decided once


def test_a_google_com_placeholder_is_never_the_ad_of_a_sale():
    import meta_ads
    now = time.time()
    meta_ads.note_unnamed("999", {"ad_name": "Saved", "link": "http://google.com/", "campaign_id": "C",
                                  "campaign_name": "sperm 2", "adset_id": "S", "adset_name": "NB6-"}, now - 60, now + 60)
    assert meta_ads.unnamed_ads_at(now) == []                    # on record from before it was known to be one
    assert meta_ads.placeholder_link("https://www.google.com/x") and meta_ads.placeholder_link("http://google.com/")
    assert not meta_ads.placeholder_link("https://notgoogle.com/") and not meta_ads.placeholder_link("")


def test_an_ad_fixed_since_is_still_the_one_for_sales_from_when_its_link_named_no_ad(client, meta, monkeypatch):
    import meta_ads
    # Read while the link named no ad...
    ads = [_graph_ad("111", "New Sales Ad - Copy", "top", LISTICLE + "?fbclid=fbclid", "")]
    _ads_account(monkeypatch, ads, [])
    assert [r["ad_id"] for r in asyncio.run(meta_ads.ad_links())] == ["111"]
    arrived = time.time()
    # ...then fixed and, as Meta does, in review for a while (its old link still serving), then live.
    ads[0]["creative"]["url_tags"], ads[0]["effective_status"] = NAMING_TAGS, "PENDING_REVIEW"
    meta_ads.reset_links()
    asyncio.run(meta_ads.ad_links())
    ads[0]["effective_status"] = "ACTIVE"
    meta_ads.reset_links()
    asyncio.run(meta_ads.ad_links())
    assert [c["ad_id"] for c in meta_ads.unnamed_ads_at(arrived)] == ["111"]
    assert meta_ads.unnamed_ads_at(arrived + meta_ads.LINKS_TTL + 60) == []
    # The one-time seed for the "top" ads of Oct 5 2026 (#c4085) goes on the same record.
    tracking.seed_unnamed_links()
    assert {c["ad_id"] for c in meta_ads.unnamed_ads_at(1791257208)} == {"120250979179220090", "120250979236770090"}
    assert meta_ads.unnamed_ads_at(1791257208)[0]["adset_name"] == "top"
    tracking.seed_unnamed_links()                                    # once


# #c4088 (Oct 6 2026, 12:09 AM): the shopper clicked NB6's ad in the Facebook app, put the 3-pack in the
# cart and reached the checkout there; "Log in with Shop" handed the checkout to Chrome, whose first page
# was the checkout itself with a click id Facebook stamped on the way out and no ad parameters.
FB_ANDROID = ("Mozilla/5.0 (Linux; Android 16; SM-A165F Build/BP4A.251205.006) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Version/4.0 Chrome/154.0.0.0 Mobile Safari/537.36 [FB_IAB/FB4A;FBAV/581.0.0.45.58;IABMV/1;]")
FB_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 26_6_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
             "Mobile/23G90 Safari/604.1 [FBAN/FBIOS;FBAV/581.0.0.64.71;FBBV/123;FBDV/iPhone17,3]")
CHROME_ANDROID = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Mobile Safari/537.36"
NB6_URL = ("https://getcoresupps.com/products/spermfuel?utm_source=fb&utm_medium=paid_social&utm_campaign=sperm+2"
           "&utm_content=New+Sales+Ad+-+Copy+3&utm_term=NB6&campaign_id=120250978399360090&adset_id=120250979320410090"
           "&ad_id=120250979320440090&placement=Facebook_Mobile_Feed&fbclid=IwZXh0bgNhZW0BMABwZG9mBWZkaWQW&lp=ranking-listicle")
HANDOFF_URL = ("https://getcoresupps.com/checkouts/cn/hWNHdxxR4LQ36HJXEE09ohzH/en-gb?_r=AQABdNu2POaj&auto_redirect=false"
               "&fbclid=IwT01FWAUxUdVleHRuA2FlbQIxMABwZG9m&skip_shop_pay=true")
HANDOFF_FBC = "fb.1.1791259285392.IwT01FWAUxUdVleHRuA2FlbQIxMABwZG9m"
HANDOFF_CLICK = ".IwT01FWAUxUdVleHRuA2FlbQIxMABwZG9m"      # the stamp's click id; the tracker re-times the fbc to the arrival
THREE_PACK = {"value": 46.95, "currency": "GBP", "items": [{"product_id": "15350744776957", "quantity": 3, "price": 37.0}]}


def _post(client, ua, ip, **payload):
    base = {"id": f"evt{time.time_ns()}", "ts": int(time.time() * 1000), "cid": "x", "fbp": "fb.1.10.99"}
    base.update(payload)
    return client.post("/collect", content=json.dumps(base),
                       headers={"Content-Type": "text/plain", "X-Forwarded-For": ip, "User-Agent": ua})


def _in_app(client, cid, ua, ip, cart=THREE_PACK):
    assert _post(client, ua, ip, name="page_viewed", url=NB6_URL, cid=cid, fbp="fb.1.30." + cid).status_code == 204
    _post(client, ua, ip, name="product_added_to_cart", url=NB6_URL, cid=cid, fbp="fb.1.30." + cid, custom=cart)
    _post(client, ua, ip, name="checkout_started", url="https://getcoresupps.com/checkouts/cn/OLDCART/en-gb?_r=AQABkrGQ",
          cid=cid, fbp="fb.1.30." + cid, custom=cart, checkout={"token": "chk_" + cid})
    run_pending(client)


def _chrome(client, cid, ip, token, cart=THREE_PACK):
    for name in ("page_viewed", "checkout_started"):
        _post(client, CHROME_ANDROID, ip, name=name, url=HANDOFF_URL, cid=cid, fbp="fb.1.40." + cid, fbc=HANDOFF_FBC,
              custom=cart if name == "checkout_started" else None, checkout={"token": token})
    run_pending(client)


def test_a_checkout_handed_from_the_facebook_app_to_chrome_keeps_the_ad_it_came_from(client, meta):
    _in_app(client, "in-app", FB_ANDROID, "152.233.29.3")
    _chrome(client, "chrome", "152.233.29.1", "chk_chrome")
    o = order(id=5590001, checkout_token="chk_chrome", created_at=iso(5), processed_at=iso(5), landing_site=None)
    signed_webhook(client, o)
    asyncio.run(tracking.process_pending())
    rec = _rec(5590001)
    # The ad by its id (Meta's names come from the catalog in production; the link's two names are what's known here).
    assert rec["ad_id"] == "120250979320440090" and rec["campaign_name"] == "sperm 2"
    assert {rec["ad_name"], rec["adset_name"]} == {"New Sales Ad - Copy 3", "NB6"}
    assert rec["handoff"] is True and rec["click"] is True and rec["source"] == "browser" and rec["lp"] == "ranking-listicle"
    assert rec["fbc"].endswith(".IwZXh0bgNhZW0BMABwZG9mBWZkaWQW")       # the ad click, not Facebook's hand-off stamp
    purchase = next(e for e in meta.events if e["event_name"] == "Purchase")
    assert purchase["user_data"]["fbc"] == rec["fbc"] and purchase["user_data"]["fbp"] == "fb.1.40.chrome"


def test_a_hand_off_seen_late_is_named_afterwards_and_a_stranger_on_the_same_network_is_not(client, meta):
    # The in-app session's events reach the tracker after the sale: sent with Facebook's click id, no ad.
    with pytest.MonkeyPatch.context() as late:
        late.setattr(tracking, "handoff_session", lambda sess, order_id="": None)
        _in_app(client, "in-app2", FB_ANDROID, "152.233.29.3")
        _chrome(client, "chrome2", "152.233.29.1", "chk_chrome2")
        o = order(id=5590002, checkout_token="chk_chrome2", created_at=iso(5), processed_at=iso(5), landing_site=None)
        signed_webhook(client, o)
        asyncio.run(tracking.process_pending())
    rec = _rec(5590002)
    assert rec["ad_id"] is None and rec["fbc"].endswith(HANDOFF_CLICK) and rec["source"] == "browser" and rec["click"] is True
    sent_fbc = rec["fbc"]
    # The hourly identity check names it, keeping the click Meta was sent; once.
    assert asyncio.run(tracking.refresh_identity(o, rec)) is True
    rec = _rec(5590002)
    assert {rec["ad_name"], rec["adset_name"]} == {"New Sales Ad - Copy 3", "NB6"} and rec["fbc"] == sent_fbc
    assert rec["identity_refreshed"] == "handoff" and rec["source"] == "browser"
    assert asyncio.run(tracking.refresh_identity(o, rec)) is False
    assert len([e for e in meta.events if e["event_name"] == "Purchase"]) == 1
    # Someone else on the same mobile network with the same 3-pack, but in the Facebook app on an iPhone:
    # an Android Chrome checkout can't have come from there, so the sale stays "a Meta ad".
    _in_app(client, "iphone", FB_IPHONE, "152.233.29.4")
    _chrome(client, "chrome3", "152.233.29.5", "chk_chrome3")
    o = order(id=5590003, checkout_token="chk_chrome3", created_at=iso(5), processed_at=iso(5), landing_site=None)
    signed_webhook(client, o)
    asyncio.run(tracking.process_pending())
    rec = _rec(5590003)
    assert rec["ad_id"] is None and rec["fbc"].endswith(HANDOFF_CLICK) and "handoff" not in rec
    # And a different cart on the same Android phone's network is not this buyer either.
    _in_app(client, "in-app4", FB_ANDROID, "152.233.29.6",
            cart={"value": 23.95, "currency": "GBP", "items": [{"product_id": "15350744776957", "quantity": 1}]})
    _chrome(client, "chrome4", "152.233.29.7", "chk_chrome4")
    signed_webhook(client, order(id=5590004, checkout_token="chk_chrome4", created_at=iso(5), processed_at=iso(5),
                                 landing_site=None))
    asyncio.run(tracking.process_pending())
    assert _rec(5590004)["ad_id"] is None


def test_purchase_waits_for_pixel_then_sends_without_it(client, meta, monkeypatch):
    signed_webhook(client, order(id=42, checkout_token="unknown", created_at=iso(10)))
    assert asyncio.run(tracking.process_pending()) == {"pending": 1}
    assert meta.events == []
    monkeypatch.setattr(tracking.config, "PURCHASE_GRACE_SECONDS", 0)
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert meta.events[0]["user_data"]["client_ip_address"] == "203.0.113.9"


def test_renewal_order_goes_out_as_subscription_renewal(client, meta):
    signed_webhook(client, order(id=43, source_name="subscription_contract_checkout_one"))
    asyncio.run(tracking.process_pending())
    assert [e["event_name"] for e in meta.events] == ["SubscriptionRenewal"]
    assert db.get_order("43")["kind"] == "renewal"


def test_orders_from_before_first_boot_are_not_resent(meta, monkeypatch):
    monkeypatch.setattr(db, "_conn", None)
    monkeypatch.setattr(db, "DB_PATH", os.path.join(tempfile.mkdtemp(), "first-boot.db"))
    db.init()                                        # brand-new install: no tracking_start yet
    started = tracking.tracking_start()
    assert abs(started - time.time()) < 5
    db.upsert_order(order(id=46, created_at=iso(3600), processed_at=iso(3600)))
    asyncio.run(tracking.process_pending())
    assert meta.events == []
    assert db.get_order("46")["kind"] == "before_start"
    # An explicit manual resend still works.
    row = db.get_order("46")
    assert asyncio.run(tracking.process_order(row, force=True, source="manual")) == "sent"


def test_test_orders_are_skipped(client, meta):
    signed_webhook(client, order(id=44, test=True))
    asyncio.run(tracking.process_pending())
    assert meta.events == [] and db.get_order("44")["status"] == "skipped"


def test_meta_failures_are_retried_then_reported(client, meta):
    meta.responses = [(500, {"error": {"message": "down"}})] * 3 + [
        (400, {"error": {"message": "Invalid parameter", "code": 100}})]
    signed_webhook(client, order(id=45))
    assert asyncio.run(tracking.process_pending()) == {"failed": 1}      # 3 x 5xx, gives up for now
    row = db.get_order("45")
    assert row["status"] == "failed" and "HTTP 500" in row["last_error"]
    tracking._next_try.clear()
    assert asyncio.run(tracking.process_pending()) == {"failed": 1}      # permanent 400, no retry storm
    assert "Invalid parameter" in db.get_order("45")["last_error"]
    tracking._next_try.clear()
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}        # Meta healthy again
    report = worker.build_report()
    assert report["orders_last_7d"]["by_status"] == {"sent": 1}


def test_bad_webhook_signature_rejected(client):
    r = client.post("/webhooks/shopify", content=b"{}", headers={"X-Shopify-Hmac-Sha256": "nope"})
    assert r.status_code == 401


def test_collect_rejects_junk(client):
    assert client.post("/collect", content="not json").status_code == 400
    assert pixel(client, name="checkout_completed_fake").status_code == 400
    assert client.post("/collect", content="x" * 20000).status_code == 413
    assert client.options("/collect").status_code == 204


def test_admin_endpoints_require_token(client):
    assert client.get("/report").status_code == 401
    assert client.get("/report?key=wrong").status_code == 401
    r = client.get("/report", headers={"Authorization": "Bearer admin-test"})
    assert r.status_code == 200 and "problems" in r.json()
    assert client.post("/mcp", json={}).status_code == 401


def test_mcp_lists_tools(client):
    r = client.post("/mcp?key=admin-test",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"tracker_status", "tracker_resend_order", "tracker_send_test_event"} <= names


# --- fixes from the review ------------------------------------------------------

def test_cancelled_manual_and_pos_orders_are_not_purchases():
    assert tracking.classify_order(order(cancelled_at=iso(10))) == "cancelled"
    assert tracking.classify_order(order(financial_status="voided")) == "cancelled"
    assert tracking.classify_order(order(source_name="shopify_draft_order")) == "manual"
    assert tracking.classify_order(order(source_name="pos")) == "manual"
    assert tracking.classify_order(order(source_name="3890849")) == "purchase"   # app/checkout-link order


def test_action_source_reflects_evidence():
    assert tracking.build_order_event(order(), "purchase", {})["action_source"] == "website"
    ev = tracking.build_order_event(order(browser_ip=None, client_details={}), "purchase", {})
    assert ev["action_source"] == "other" and "event_source_url" not in ev
    ev = tracking.build_order_event(order(source_name="pos"), "purchase", {})
    assert ev["action_source"] == "physical_store"


def test_fbc_rebuilt_from_landing_site_when_pixel_missed():
    o = order(landing_site="/products/spermfuel?utm_source=fb&fbclid=IwAR2abcDEFghiJKL")
    ev = tracking.build_order_event(o, "purchase", {})
    assert ev["user_data"]["fbc"].startswith("fb.1.") and ev["user_data"]["fbc"].endswith(".IwAR2abcDEFghiJKL")
    assert "fbc" not in tracking.build_order_event(order(), "purchase", {})["user_data"]


def test_email_match_needs_shopify_browser_corroboration():
    victim = order()
    db.upsert_session("attacker", fbp="fb.1.1.ATTACK", fbc="fb.1.1.ATTACKCLICK", ip="9.9.9.9",
                      user_agent="EvilBot", email="jane.doe@example.com")
    assert tracking.match_session(victim) == ({}, "none")
    db.upsert_session("real", fbp="fb.1.1.REAL", ip="203.0.113.9", user_agent="Mozilla/5.0 iPhone",
                      email="jane.doe@example.com")
    sess, how = tracking.match_session(victim)
    assert how == "email" and sess["fbp"] == "fb.1.1.REAL"


def test_pixel_contact_details_only_trusted_from_checkout_events(client):
    pixel(client, name="page_viewed", customer={"email": "victim@example.com"})
    assert db.get_session("shopify-client-1")["email"] is None
    pixel(client, name="checkout_started", checkout={"token": "chk_x", "email": "buyer@example.com"})
    assert db.get_session("shopify-client-1")["email"] == "buyer@example.com"


def test_failed_orders_are_never_stranded(client, meta):
    meta.responses = [(400, {"error": {"message": "Invalid OAuth access token", "code": 190}})] * 25
    signed_webhook(client, order(id=50))
    for _ in range(25):
        tracking._next_try.clear()
        asyncio.run(tracking.process_pending())
    assert db.get_order("50")["status"] == "failed" and db.get_order("50")["attempts"] >= 20
    tracking._next_try.clear()
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}      # token fixed: drains by itself


def test_one_crashing_order_does_not_block_the_queue(client, meta, monkeypatch):
    signed_webhook(client, order(id=51, checkout_token="a"))
    signed_webhook(client, order(id=52, checkout_token="b"))
    real = tracking.build_order_event
    monkeypatch.setattr(tracking, "build_order_event",
                        lambda o, k, s: (_ for _ in ()).throw(RuntimeError("boom")) if o["id"] == 51 else real(o, k, s))
    monkeypatch.setattr(tracking.config, "PURCHASE_GRACE_SECONDS", 0)
    counts = asyncio.run(tracking.process_pending())
    assert counts == {"failed": 1, "sent": 1}
    assert "internal" in db.get_order("51")["last_error"]


def test_resend_really_resends_and_ignores_start(client, meta, monkeypatch):
    signed_webhook(client, order(id=53, created_at=iso(5)))
    monkeypatch.setattr(tracking.config, "PURCHASE_GRACE_SECONDS", 0)
    asyncio.run(tracking.process_pending())
    # Changed on purpose (F6): no browser was seen at checkout, so an InitiateCheckout goes first.
    assert [e["event_name"] for e in meta.events] == ["InitiateCheckout", "Purchase"]
    async def fake_get(oid): return order(id=53, created_at=iso(3 * 86400))   # now before_start
    monkeypatch.setattr(app_module.shopify, "get_order", fake_get)
    r = client.post("/admin/resend/53", headers={"Authorization": "Bearer admin-test"})
    body = r.json()
    assert body["status"] == "sent" and body["was_sent_before"] is True
    # The resend repeats the Purchase only: the InitiateCheckout is never sent twice.
    assert [e["event_name"] for e in meta.events] == ["InitiateCheckout", "Purchase", "Purchase"]
    assert db.get_order("53")["fbtrace_id"] == "trace123"
    # A forced order that fails transiently keeps being retried under force.
    meta.responses = [(500, {})] * 3
    db.reset_order("53", forced=True)
    assert asyncio.run(tracking.process_pending()) == {"failed": 1}
    tracking._next_try.clear()
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}


def test_test_event_code_reaches_meta(client, meta, monkeypatch):
    monkeypatch.setattr(tracking.config, "META_TEST_EVENT_CODE", "TEST123")
    monkeypatch.setattr(meta_capi.config, "META_TEST_EVENT_CODE", "TEST123")
    seen = []
    meta.handler_bodies = seen
    orig = meta.handler
    def handler(req):
        seen.append(json.loads(req.content)); return orig(req)
    monkeypatch.setattr(meta_capi, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    signed_webhook(client, order(id=54))
    asyncio.run(tracking.process_pending())
    assert seen and seen[-1]["test_event_code"] == "TEST123"


def test_tracking_start_moves_forward_on_test_to_live_switch(monkeypatch):
    db.kv_set("tracking_start", str(1000.0)); db.kv_set("mode", "test")
    monkeypatch.setattr(tracking.config, "META_TEST_EVENT_CODE", "")
    started = tracking.tracking_start()
    assert abs(started - time.time()) < 5 and db.kv_get("mode") == "live"
    assert abs(tracking.tracking_start() - started) < 1                     # stable afterwards
    monkeypatch.setattr(tracking.config, "TRACK_ORDERS_FROM", "2026-09-01T00:00:00Z")
    assert tracking.tracking_start() == 1788220800.0                         # explicit value wins
    monkeypatch.setattr(tracking.config, "TRACK_ORDERS_FROM", "Sep 1 2026")
    assert tracking.tracking_start() == 1788220800.0                         # bad value ignored, not crash


def test_recovered_failure_is_not_reported_as_a_loss(client, meta):
    meta.responses = [(500, {})] * 3
    signed_webhook(client, order(id=55))
    asyncio.run(tracking.process_pending())
    assert any("failed to reach Meta" in p for p in worker.build_report()["problems"])
    tracking._next_try.clear()
    asyncio.run(tracking.process_pending())
    rep = worker.build_report()
    assert rep["events_last_24h"]["Purchase"] == {"sent": 1}
    assert not any("events failed" in p or "failed to reach" in p for p in rep["problems"])


def test_client_ip_uses_trusted_hop_and_rate_limit_holds(client):
    for i in range(240):
        assert pixel(client, name="page_viewed", id=f"e{i}").status_code == 204
    assert pixel(client, name="page_viewed", id="e999").status_code == 429    # spoofed left entries don't help
    r = client.post("/collect", content="{}", headers={"X-Forwarded-For": "not-an-ip"})
    assert r.status_code in (400, 429)


def test_oversized_bodies_rejected_before_reading(client):
    r = client.post("/collect", content=b"{}", headers={"Content-Length": "999999"})
    assert r.status_code == 413
    r = client.post("/webhooks/shopify", content=b"x" * 3_000_000, headers={"X-Shopify-Hmac-Sha256": "x"})
    assert r.status_code == 413


def test_non_object_pixel_fields_are_400_not_500(client):
    assert pixel(client, name="page_viewed", checkout=[1], custom={"items": {}}).status_code in (204, 400)
    assert pixel(client, name="page_viewed", customer="x").status_code in (204, 400)


def test_events_for_order_survives_lots_of_pixel_traffic():
    db.record_event("Purchase", "order_77", "webhook", "sent", {"user_data": {}}, order_id="77")
    for i in range(600):
        db.record_event("PageView", f"pv{i}", "pixel", "sent", {"user_data": {}})
    assert len(db.events_for_order("77")) == 1


def test_retention_prunes_old_rows():
    db.upsert_session("old", fbp="x"); db.record_event("PageView", "old", "pixel", "sent", {"user_data": {}})
    db._c().execute("UPDATE sessions SET last_seen=1"); db._c().execute("UPDATE events SET created_at=1")
    removed = db.prune(time.time(), 30, 30, 180)
    assert removed["sessions"] == 1 and removed["pixel_events"] == 1


def test_poller_follows_pagination_and_reconciler_alerts_only_for_old_misses(monkeypatch, meta):
    pages = {"1": [order(id=60, created_at=iso(30))], "2": [order(id=61, created_at=iso(7200))]}
    calls = []
    def handler(req):
        calls.append(str(req.url))
        page = req.url.params.get("page_info", "1")
        headers = {"Link": '<https://teststore.myshopify.com/admin/api/2024-10/orders.json?page_info=2>; rel="next"'} if page == "1" else {}
        return httpx.Response(200, json={"orders": pages[page]}, headers=headers)
    monkeypatch.setattr(shopify, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    new = asyncio.run(tracking.poll_orders(3 * 86400))
    assert {o["id"] for o in new} == {60, 61} and len(calls) == 2 and "created_at_min" in calls[0]
    alerts = []
    async def fake_alert(msg, key): alerts.append(msg)
    monkeypatch.setattr(worker, "alert", fake_alert)
    monkeypatch.setattr(worker, "_booted_at", time.time() - 3600)
    monkeypatch.setattr(worker.config, "ALERT_WEBHOOK_URL", "")
    db._c().execute("DELETE FROM orders")
    asyncio.run(worker._reconcile())
    assert any("1 order(s)" in a for a in alerts)                            # only the 2h-old one counts


def test_shopify_token_refresh_on_401(monkeypatch):
    monkeypatch.setattr(shopify.config, "SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setattr(shopify.config, "SHOPIFY_CLIENT_SECRET", "sec")
    monkeypatch.setattr(shopify, "_token", "stale"); monkeypatch.setattr(shopify, "_token_expires", float("inf"))
    seen = []
    def handler(req):
        seen.append((req.url.path, req.headers.get("X-Shopify-Access-Token")))
        if req.url.path.endswith("/oauth/access_token"):
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        if req.headers.get("X-Shopify-Access-Token") == "stale":
            return httpx.Response(401, json={"errors": "bad"})
        return httpx.Response(200, json={"shop": {"name": "ok"}})
    monkeypatch.setattr(shopify, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert asyncio.run(shopify.get_shop())["name"] == "ok"
    assert seen[-1][1] == "fresh"


def test_ensure_order_webhook(monkeypatch):
    created = []
    def handler(req):
        if req.method == "POST": created.append(json.loads(req.content)); return httpx.Response(201, json={"webhook": {}})
        return httpx.Response(200, json={"webhooks": [{"topic": "orders/create", "address": "https://x/webhooks/shopify"}]})
    monkeypatch.setattr(shopify, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert asyncio.run(shopify.ensure_order_webhook("https://x/webhooks/shopify")) == "exists"
    assert asyncio.run(shopify.ensure_order_webhook("https://y/webhooks/shopify")) == "created"
    assert created[0]["webhook"]["address"] == "https://y/webhooks/shopify"


def test_access_log_redacts_admin_key():
    rec = logging.LogRecord("uvicorn.access", 20, "", 0, '%s - "%s %s HTTP/1.1" %d',
                            ("1.2.3.4:1", "GET", "/report?key=admin-test&x=1", 200), None)
    app_module.RedactKey().filter(rec)
    assert "admin-test" not in rec.getMessage() and "key=***" in rec.getMessage()


def test_log_level_and_data_dir_validation(monkeypatch):
    import importlib
    monkeypatch.setenv("LOG_LEVEL", "debug"); monkeypatch.setenv("RAILWAY_SERVICE_ID", "svc")
    monkeypatch.setenv("DATA_DIR", "./data"); monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    import config as cfg
    importlib.reload(cfg)
    assert cfg.LOG_LEVEL == "DEBUG" and "not on a Railway volume" in cfg.data_dir_problem()
    monkeypatch.setenv("LOG_LEVEL", "loud"); monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    monkeypatch.setenv("DATA_DIR", "/data/x"); importlib.reload(cfg)
    assert cfg.LOG_LEVEL == "INFO" and cfg.data_dir_problem() == ""
    monkeypatch.delenv("RAILWAY_SERVICE_ID"); monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH")
    monkeypatch.setenv("DATA_DIR", os.environ["DATA_DIR"]); importlib.reload(cfg)


# --- backup pixels ----------------------------------------------------------------

MAIN = "1298114545063437"
BACKUP = {"pixel_id": "1717074239276698", "token": "backup-token", "test_event_code": ""}


@pytest.fixture
def backup(monkeypatch):
    monkeypatch.setattr(config, "EXTRA_PIXELS", [dict(BACKUP)])
    db.kv_set(f"pixel_start:{BACKUP['pixel_id']}", str(time.time() - 3600))
    return BACKUP["pixel_id"]


def test_backup_pixel_gets_the_same_events_with_its_own_token(client, meta, backup, monkeypatch):
    pixel(client, name="page_viewed")
    pixel(client, name="product_added_to_cart", custom={"items": [{"product_id": "111"}]})
    run_pending(client)
    assert meta.names_for(MAIN) == meta.names_for(backup) == ["PageView", "AddToCart"]
    assert {(p, t) for p, t, _ in meta.calls} == {(MAIN, "test-token"), (backup, "backup-token")}
    signed_webhook(client, order(id=70, created_at=iso(5)))
    monkeypatch.setattr(tracking.config, "PURCHASE_GRACE_SECONDS", 0)
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert meta.names_for(MAIN)[-2:] == meta.names_for(backup)[-2:] == ["InitiateCheckout", "Purchase"]
    assert db.get_order("70")["fbtrace_id"] == "trace123"
    rep = worker.build_report()
    assert rep["backup_pixels"][0]["events_last_24h"]["Purchase"] == {"sent": 1}
    assert rep["events_last_24h"]["Purchase"] == {"sent": 1}          # main stats stay per pixel


def test_backup_failure_is_retried_without_resending_to_core_club(client, meta, backup):
    meta.fail_by_pixel[backup] = [(500, {"error": {"message": "down"}})] * 3
    signed_webhook(client, order(id=71))
    assert asyncio.run(tracking.process_pending()) == {"failed": 1}
    row = db.get_order("71")
    assert row["status"] == "failed" and f"[pixel {backup}]" in row["last_error"]
    tracking._next_try.clear()
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    # Changed on purpose (F6): each pixel also got the InitiateCheckout, once, right before its Purchase.
    assert meta.names_for(MAIN) == meta.names_for(backup) == ["InitiateCheckout", "Purchase"]
    assert not any("failed to reach" in p for p in worker.build_report()["problems"])


def test_backup_only_gets_orders_placed_after_it_was_added(client, meta, monkeypatch):
    monkeypatch.setattr(config, "EXTRA_PIXELS", [dict(BACKUP)])
    signed_webhook(client, order(id=72, created_at=iso(300)))        # before the backup existed
    asyncio.run(tracking.process_pending())
    assert meta.names_for(MAIN) == ["InitiateCheckout", "Purchase"] and meta.names_for(BACKUP["pixel_id"]) == []
    assert db.get_order("72")["status"] == "sent"
    db.kv_set(f"pixel_start:{BACKUP['pixel_id']}", str(time.time() - 60))
    signed_webhook(client, order(id=73, checkout_token="t73", created_at=iso(5)))
    monkeypatch.setattr(tracking.config, "PURCHASE_GRACE_SECONDS", 0)
    asyncio.run(tracking.process_pending())
    assert meta.names_for(BACKUP["pixel_id"]) == ["InitiateCheckout", "Purchase"]


def test_old_event_rows_migrate_to_per_pixel_dedup(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "old.db")
    import sqlite3
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_name TEXT NOT NULL,
            event_id TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL, fbtrace_id TEXT,
            error TEXT, match_keys TEXT, order_id TEXT, payload TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE UNIQUE INDEX idx_events_dedup ON events(event_name, event_id, status);
        INSERT INTO events (event_name, event_id, source, status, payload, created_at)
            VALUES ('Purchase', 'order_1', 'webhook', 'sent', '{}', 1);
    """)
    old.commit(); old.close()
    monkeypatch.setattr(db, "_conn", None); monkeypatch.setattr(db, "DB_PATH", path)
    db.init()
    assert db.event_already_sent("Purchase", "order_1")                        # kept for Core Club
    assert not db.event_already_sent("Purchase", "order_1", "1717074239276698")
    db.record_event("Purchase", "order_1", "webhook", "sent", {"user_data": {}}, pixel_id="1717074239276698")
    assert db.event_already_sent("Purchase", "order_1", "1717074239276698")
    assert db._c().execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


def test_backup_pixel_settings_are_validated(monkeypatch):
    monkeypatch.setenv("META_PIXEL_ID_2", "1717074239276698"); monkeypatch.setenv("META_ACCESS_TOKEN_2", "tok")
    monkeypatch.setenv("META_PIXEL_ID_3", "555")                              # token missing
    monkeypatch.setenv("META_PIXEL_ID_4", MAIN); monkeypatch.setenv("META_ACCESS_TOKEN_4", "x")   # duplicate
    pixels, problems = config._extra_pixels()
    assert [p["pixel_id"] for p in pixels] == ["1717074239276698"]
    assert any("META_ACCESS_TOKEN_3" in p for p in problems) and any("twice" in p for p in problems)
    monkeypatch.setattr(config, "EXTRA_PIXEL_PROBLEMS", problems)
    assert any("META_ACCESS_TOKEN_3" in p for p in worker.build_report()["problems"])


def test_send_test_event_can_target_the_backup(client, meta, backup):
    r = client.post("/mcp?key=admin-test", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "tracker_send_test_event",
                               "arguments": {"test_event_code": "TEST1", "pixel_id": backup}}},
                    headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 200 and '\\"ok\\": true' in r.text
    assert meta.calls[-1][:2] == (backup, "backup-token")


def test_content_and_value_field_variants(monkeypatch):
    monkeypatch.setattr(tracking.config, "CONTENT_ID_FIELD", "sku")
    monkeypatch.setattr(tracking.config, "PURCHASE_VALUE_FIELD", "subtotal_price")
    ev = tracking.build_order_event(order(), "purchase", {})
    assert ev["custom_data"]["content_ids"] == ["SF-1"] and ev["custom_data"]["value"] == 49.95
    assert ev["custom_data"]["currency"] == "USD" and abs(ev["event_time"] - time.time()) < 400
    ud = ev["user_data"]
    assert ud["ln"] == [sha("doe")] and ud["ct"] == [sha("oakville")] and ud["country"] == [sha("ca")]


# --- Kaching rebill tags -----------------------------------------------------------

RECURRING = "Kaching Subscription Recurring Order"


@pytest.mark.parametrize("tags, kind", [
    ("Kaching Bundles, Kaching Subscription Recurring Order", "renewal"),   # the tag alone makes a rebill
    ("Kaching Subscription Recurring Order", "renewal"),
    ("  kaching subscription RECURRING order  ,VIP", "renewal"),          # any case, any spacing
    (["VIP", "Kaching Subscription Recurring Order"], "renewal"),         # a list of tags works too
    ("Kaching Subscription First Order", "purchase"),                    # a subscription's first order is a sale
    ("Kaching Bundles, Kaching Subscription First Order", "purchase"),
    # Tags that merely contain the words are not the tag.
    ("Kaching Subscription Recurring Order Skipped", "purchase"),
    ("Not Kaching Subscription Recurring Order", "purchase"),
    ("Kaching Subscription Recurring Orders", "purchase"),
    ("Kaching Subscription Recurring", "purchase"),
    ("Kaching Subscription Recurring Order-2", "purchase"),
    ("", "purchase"), (None, "purchase"),
])
def test_kaching_tags_decide_rebills(tags, kind):
    assert tracking.classify_order(order(tags=tags)) == kind
    assert tracking.is_renewal(order(tags=tags)) is (kind == "renewal")


def test_rebill_by_source_tag_or_both():
    # The source alone, as before.
    assert tracking.classify_order(order(source_name="subscription_contract", tags="")) == "renewal"
    assert tracking.classify_order(order(source_name="subscription_contract_checkout_one")) == "renewal"
    # Either one is enough: a rebill source stays a rebill whatever it is tagged.
    assert tracking.classify_order(order(source_name="subscription_contract_checkout_one",
                                         tags="Kaching Subscription First Order")) == "renewal"
    # Test and cancelled orders are still skipped first.
    assert tracking.classify_order(order(tags=RECURRING, test=True)) == "test"
    assert tracking.classify_order(order(tags=RECURRING, cancelled_at=iso(10))) == "cancelled"


def test_renewal_tags_can_be_changed_or_turned_off(monkeypatch):
    import importlib
    assert config.RENEWAL_TAGS == {"kaching subscription recurring order"}
    assert worker.build_report()["config"]["renewal_tags"] == ["kaching subscription recurring order"]
    try:
        monkeypatch.setenv("RENEWAL_TAGS", " Rebill ,, Kaching Subscription Recurring Order,")
        importlib.reload(config)
        assert config.RENEWAL_TAGS == {"rebill", "kaching subscription recurring order"}
        assert tracking.classify_order(order(tags="REBILL")) == "renewal"
        monkeypatch.setenv("RENEWAL_TAGS", "")
        importlib.reload(config)
        assert config.RENEWAL_TAGS == set()
        assert tracking.classify_order(order(tags=RECURRING)) == "purchase"      # tags ignored
        assert tracking.classify_order(order(source_name="subscription_contract")) == "renewal"
    finally:
        monkeypatch.delenv("RENEWAL_TAGS", raising=False)
        importlib.reload(config)
    assert config.RENEWAL_TAGS == {"kaching subscription recurring order"}


def test_tagged_rebill_goes_out_as_renewal_without_click_ids(client, meta, monkeypatch):
    signed_webhook(client, order(id=80, tags="Kaching Bundles, " + RECURRING,
                                 note_attributes=[{"name": "fbc", "value": "fb.1.1.OLDCLICK"}]))
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    ev = meta.events[-1]
    assert ev["event_name"] == "SubscriptionRenewal" and ev["event_id"] == "renewal_80"
    assert ev["action_source"] == "system_generated"
    for key in ("fbc", "fbp", "client_ip_address", "client_user_agent"):
        assert key not in ev["user_data"]
    row = db.get_order("80")
    assert row["kind"] == "renewal" and row["attribution"] is None           # never credited to an ad
    # The first order of a subscription is a real sale.
    monkeypatch.setattr(tracking.config, "PURCHASE_GRACE_SECONDS", 0)
    signed_webhook(client, order(id=81, checkout_token="t81", tags="Kaching Subscription First Order"))
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert meta.events[-1]["event_name"] == "Purchase" and db.get_order("81")["kind"] == "purchase"


def test_polled_orders_ask_shopify_for_tags(monkeypatch):
    assert "tags" in shopify.ORDER_FIELDS.split(",")
    seen = []

    def handler(req):
        seen.append(req.url.params.get("fields", ""))
        return httpx.Response(200, json={"orders": []})
    monkeypatch.setattr(shopify, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asyncio.run(shopify.list_orders_since(iso(600)))
    assert "tags" in seen[0].split(",")


def test_a_rebill_tag_added_after_the_webhook_is_picked_up_before_sending(client, meta, monkeypatch):
    # Shopify delivers the webhook before Kaching has tagged the order.
    signed_webhook(client, order(id=82, created_at=iso(5), checkout_token="t82", tags="Kaching Bundles"))
    assert asyncio.run(tracking.process_pending()) == {"pending": 1}          # waiting for the pixel
    tagged = order(id=82, created_at=iso(5), checkout_token="t82", tags="Kaching Bundles, " + RECURRING)
    listed = [tagged]

    async def list_orders_since(since):
        return listed
    monkeypatch.setattr(shopify, "list_orders_since", list_orders_since)
    assert asyncio.run(tracking.poll_orders(600)) == []                      # known already, not new
    stored = db.get_order("82")["order_json"]
    assert stored["tags"] == "Kaching Bundles, " + RECURRING and stored["email"] == order()["email"]
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    assert [e["event_name"] for e in meta.events] == ["SubscriptionRenewal"]
    # Once reported, an order keeps what it was reported as.
    listed[:] = [{**tagged, "tags": ""}]
    asyncio.run(tracking.poll_orders(600))
    assert db.get_order("82")["order_json"]["tags"] == "Kaching Bundles, " + RECURRING
    # A listing without the tags field never wipes them.
    signed_webhook(client, order(id=83, created_at=iso(5), checkout_token="t83", tags=RECURRING))
    listed[:] = [{k: v for k, v in order(id=83).items() if k != "tags"}]
    asyncio.run(tracking.poll_orders(600))
    assert db.get_order("83")["order_json"]["tags"] == RECURRING


def test_a_late_tag_never_turns_an_order_sent_somewhere_into_a_renewal(client, meta, backup, monkeypatch):
    meta.fail_by_pixel[backup] = [(500, {"error": {"message": "down"}})] * 3
    signed_webhook(client, order(id=84, tags=""))
    assert asyncio.run(tracking.process_pending()) == {"failed": 1}          # Core Club has the Purchase
    assert meta.names_for(MAIN) == ["InitiateCheckout", "Purchase"] and meta.names_for(backup) == ["InitiateCheckout"]

    async def list_orders_since(since):
        return [order(id=84, tags=RECURRING)]
    monkeypatch.setattr(shopify, "list_orders_since", list_orders_since)
    asyncio.run(tracking.poll_orders(600))
    assert db.get_order("84")["order_json"]["tags"] == ""                    # left as it was reported
    tracking._next_try.clear()
    assert asyncio.run(tracking.process_pending()) == {"sent": 1}
    # The backup gets the same Purchase; no dataset gets the order twice as two different events.
    assert meta.names_for(MAIN) == meta.names_for(backup) == ["InitiateCheckout", "Purchase"]
