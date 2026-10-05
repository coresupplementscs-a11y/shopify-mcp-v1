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
