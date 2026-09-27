import asyncio
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import tempfile
import time

import httpx
import pytest
from starlette.testclient import TestClient

import app as app_module
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

    def handler(self, request: httpx.Request):
        if self.responses:
            status, body = self.responses.pop(0)
            return httpx.Response(status, json=body)
        body = json.loads(request.content)
        self.events.extend(body["data"])
        return httpx.Response(200, json={"events_received": len(body["data"]), "fbtrace_id": "trace123"})


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "tracker.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "_conn", None)
    db.init()
    db.kv_set("tracking_start", str(time.time() - 86400))
    tracking._next_try.clear()
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
    assert meta_capi.norm_phone("+44 020 7946 0958", "GB") == "4402079460958"
    assert meta_capi.norm_zip("90210-1234", "US") == "90210"
    assert meta_capi.norm_zip("L6M 5P6", "CA") == "l6m5p6"
    assert meta_capi.norm_name(" Jané ") == "jane"
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
    sess = {"client_id": "fb.1.10.99", "fbp": "fb.1.10.99", "fbc": "fb.1.20.CLICK",
            "landing_url": "https://getcoresupps.com/products/spermfuel?fbclid=CLICK"}
    ev = tracking.build_order_event(order(), "purchase", sess)
    ud = ev["user_data"]
    assert ev["event_name"] == "Purchase" and ev["event_id"] == "order_5550001"
    assert ev["action_source"] == "website"
    assert ud["fbc"] == "fb.1.20.CLICK" and ud["fbp"] == "fb.1.10.99"
    assert ud["client_ip_address"] == "203.0.113.9" and ud["client_user_agent"] == "Mozilla/5.0 iPhone"
    assert ud["em"] == [sha("jane.doe@example.com")]
    assert ud["ph"] == [sha("16475550199")]
    assert ud["fn"] == [sha("jane")] and ud["zp"] == [sha("l6m5p6")] and ud["st"] == [sha("on")]
    assert ud["external_id"] == [sha("fb.1.10.99"), sha("777")]
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
            "cid": "fb.1.10.99", "fbp": "fb.1.10.99", "fbc": "fb.1.20.CLICK"}
    base.update(payload)
    return client.post("/collect", content=json.dumps(base),
                       headers={"Content-Type": "text/plain", "X-Forwarded-For": "198.51.100.7, 10.0.0.1",
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
