"""The Backend tab: Shopify's orders after the sale (parcels, refunds,
disputes) and 17TRACK's word on where each parcel is. Shopify and 17TRACK are
mocked; nothing here ever sends anything."""
import asyncio
import datetime as dt
import json
import re
import time

import httpx
import pytest

import backend
import config
import db
from test_hub import API, POST, client, fresh, meta, shop, make_order  # noqa: F401  (fixtures)

DAY = 86400


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


def shipped(oid, created, tracking, carrier="WanbExpress", shipped_at=None, country="GB", city="Leeds", **over):
    o = make_order(oid, created, **over)
    o["shipping_address"] = {"first_name": "Jané", "last_name": "Doe", "address1": "1 Oak St", "city": city,
                             "country_code": country, "zip": "L6M 5P6", "phone": "555-0199"}
    o["fulfillments"] = [{"id": oid * 10, "status": "success", "created_at": iso(shipped_at or created + DAY),
                          "tracking_company": carrier, "tracking_number": tracking, "tracking_numbers": [tracking]}]
    o["refunds"] = []
    return o


class FakeTrack17:
    def __init__(self):
        self.registered, self.calls, self.info = [], [], {}
        self.reject = {}            # number -> error code

    def handler(self, request: httpx.Request):
        assert request.headers.get("17token") == "t17-test-key"
        body = json.loads(request.content)
        assert len(body) <= 40
        self.calls.append((request.url.path.split("/")[-1], body))
        if request.url.path.endswith("/register"):
            acc, rej = [], []
            for item in body:
                if item["number"] in self.reject:
                    rej.append({"number": item["number"], "error": {"code": self.reject[item["number"]], "message": "nope"}})
                else:
                    self.registered.append(item)
                    acc.append({"number": item["number"], "carrier": item.get("carrier") or 190086})
            return httpx.Response(200, json={"code": 0, "data": {"accepted": acc, "rejected": rej}})
        if request.url.path.endswith("/gettrackinfo"):
            acc = [{"number": i["number"], "track_info": self.info[i["number"]]} for i in body if i["number"] in self.info]
            rej = [{"number": i["number"], "error": {"code": -18019902, "message": "not registered"}}
                   for i in body if i["number"] not in self.info]
            return httpx.Response(200, json={"code": 0, "data": {"accepted": acc, "rejected": rej}})
        return httpx.Response(404, json={})


def track_info(status, event="Arrived at facility", event_at=None, delivered_at=None, transit=None):
    info = {"latest_status": {"status": status, "sub_status": status + "_Other"},
            "latest_event": {"description": event, "time_iso": iso(event_at or time.time() - 3600), "location": "Leeds"},
            "time_metrics": {"days_of_transit": transit}, "milestone": []}
    if delivered_at:
        info["milestone"].append({"key_stage": "Delivered", "time_iso": iso(delivered_at)})
    return info


@pytest.fixture
def t17(monkeypatch):
    fake = FakeTrack17()
    monkeypatch.setattr(config, "TRACK17_KEY", "t17-test-key")
    monkeypatch.setattr(backend, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    monkeypatch.setattr(backend, "TRACK17_PAUSE", 0)
    return fake


@pytest.fixture(autouse=True)
def quiet(fresh):
    backend._state.update(last=0.0, error="", running=False, track17="")


def test_sync_keeps_orders_parcels_refunds_and_disputes_without_any_personal_data(client, shop, t17):
    now = time.time()
    o1 = shipped(9001, now - 10 * DAY, "WNB001", shipped_at=now - 9 * DAY)
    o1["refunds"] = [{"id": 501, "created_at": iso(now - 2 * DAY), "note": "Ethoca Alert jane.doe@example.com 555-0199",
                      "transactions": [{"kind": "refund", "status": "success", "amount": "20.00"}]}]
    o2 = make_order(9002, now - 3 * DAY)                      # paid, not shipped
    o2["shipping_address"] = {"country_code": "us", "city": "Austin", "name": "Jane Doe"}
    o3 = shipped(9003, now - 100 * DAY, "OLD001", country="AU")  # before the window: read, not registered
    o3["created_at"] = iso(now - 100 * DAY)
    o4 = make_order(9004, now - DAY, test=True)
    shop.orders = [o1, o2, o3, o4]
    shop.disputes = [{"id": 777, "order_id": 9001, "type": "chargeback", "amount": "59.95", "currency": "USD",
                      "reason": "product_not_received", "network_reason_code": "13.1", "status": "needs_response",
                      "evidence_due_by": iso(now + 2 * DAY), "evidence_sent_on": None, "finalized_on": None,
                      "initiated_at": iso(now - DAY)}]
    t17.info["WNB001"] = track_info("InTransit", event_at=now - 8 * DAY)
    st = asyncio.run(backend.sync())
    assert st["track17"] == "on" and st["tracked"] == 1 and st["error"] == ""
    assert shop.params[0]["created_at_min"] and "updated_at_min" not in shop.params[0]   # the first sync: 90 days back
    assert [i["number"] for i in t17.registered] == ["WNB001"]                            # OLD001 is past the backfill
    assert t17.registered[0]["carrier"] == 190086                                          # Shopify's "WanbExpress" named for 17TRACK
    rows = {r["order_id"]: r for r in db.query("SELECT * FROM backend_orders")}
    assert rows["9001"]["country"] == "GB" and rows["9001"]["city"] == "Leeds" and rows["9001"]["fulfilled_at"]
    assert rows["9002"]["country"] == "US" and rows["9002"]["fulfilled_at"] is None and rows["9004"]["test"] == 1
    for table in ("backend_orders", "shipments", "refunds", "disputes"):
        dump = json.dumps(db.query(f"SELECT * FROM {table}"))
        for pii in ("jane", "Jané", "Doe", "example.com", "555", "Oak St", "L6M"):
            assert pii not in dump, (table, pii)
    r = db.query("SELECT * FROM refunds")[0]
    assert (r["amount"], r["note"]) == (20.0, "Ethoca Alert")
    s = db.query("SELECT * FROM shipments WHERE tracking_number='WNB001'")[0]
    assert (s["registered"], s["status"], s["last_event"]) == (1, "InTransit", "Arrived at facility")
    # The next sync reads only what changed since, and leaves 17TRACK alone for 45 minutes.
    calls = len(t17.calls)
    asyncio.run(backend.sync())
    assert "updated_at_min" in shop.params[-1] and len(t17.calls) == calls

    body = client.get("/hub/api/backend?range=30d", headers=API).json()
    t = body["tiles"]
    assert (t["orders"], t["shipped"], t["unfulfilled"], t["in_transit"], t["stuck"]) == (2, 1, 1, 0, 1)
    assert (t["refunds"], t["refunded"], t["refund_rate"]) == (1, 20.0, 0.5)
    assert (t["chargebacks"], t["disputes_open"], t["chargeback_rate"]) == (1, 1, 0.5)
    assert [c["key"] for c in body["countries"]] == ["GB", "US"]
    assert body["refund_reasons"] == [{"kind": "Chargeback alert", "n": 1, "amount": 20.0}]
    assert body["shipments"][0]["state"] == "stuck" and body["shipments"][0]["city"] == "Leeds"
    assert body["disputes"][0]["bucket"] == "open" and 1.5 < body["disputes"][0]["days_left"] <= 2
    kinds = [(a["kind"], a["level"]) for a in body["attention"]]
    assert kinds == [("dispute", "fail"), ("parcel", "warn"), ("unfulfilled", "warn")]
    assert "respond within 2 days" in body["attention"][0]["text"]
    assert "no carrier update for 8 days" in body["attention"][1]["text"] and "#c9001 to GB" in body["attention"][1]["text"]
    assert body["attention"][2]["text"] == "#c9002: paid 3 days ago and not shipped yet."
    dump = json.dumps(body)
    for pii in ("jane", "Jané", "Doe", "example.com", "555-0199", "Oak St"):
        assert pii not in dump
    assert client.get("/hub/api/backend/alerts", headers=API).json() == {"count": 3, "urgent": 1}
    assert client.get("/hub/api/backend?range=7d", headers=API).json()["tiles"]["orders"] == 1    # 9002 only
    assert client.get("/hub/api/backend").status_code == 401


def test_parcels_delivered_late_unscanned_and_failed(client, shop, t17):
    now = time.time()
    shop.orders = [shipped(9101, now - 30 * DAY, "A1", shipped_at=now - 29 * DAY),        # delivered in 12 days
                   shipped(9102, now - 25 * DAY, "A2", shipped_at=now - 24 * DAY),        # late: 24 days, still moving
                   shipped(9103, now - 8 * DAY, "A3", shipped_at=now - 7 * DAY),          # never scanned
                   shipped(9104, now - 6 * DAY, "A4", shipped_at=now - 5 * DAY, country="CA"),   # failed
                   shipped(9105, now - 2 * DAY, "A5", shipped_at=now - DAY, carrier="Mystery Post")]   # refused
    t17.info["A1"] = track_info("Delivered", "Delivered to mailbox", event_at=now - 17 * DAY, delivered_at=now - 17 * DAY, transit=12)
    t17.info["A2"] = track_info("InTransit", "In transit", event_at=now - 2 * DAY)
    t17.info["A3"] = track_info("NotFound", "", event_at=None)
    t17.info["A3"]["latest_event"] = {}
    t17.info["A4"] = track_info("DeliveryFailure", "Address incomplete", event_at=now - DAY)
    t17.reject["A5"] = -18019903
    asyncio.run(backend.sync())
    body = client.get("/hub/api/backend?range=90d", headers=API).json()
    states = {s["order_name"]: (s["state"], s["status"], s["days"]) for s in body["shipments"]}
    assert states["#c9101"] == ("delivered", "Delivered", 12.0)
    assert states["#c9102"][0] == "late" and states["#c9103"][0] == "unscanned"
    assert states["#c9104"] == ("failed", "DeliveryFailure", 5.0)
    assert states["#c9105"][0] == "untracked" and states["#c9105"][1] == "Not tracked"
    assert body["shipments"][0]["order_name"] == "#c9105" and body["shipments"][0]["error"] == "nope"   # newest first
    t = body["tiles"]
    assert (t["delivered"], t["days_to_deliver"], t["stuck"], t["failed"], t["untracked"], t["in_transit"]) == (1, 12.0, 2, 1, 1, 0)
    assert t["delivered_rate"] == 0.2
    assert [h["n"] for h in body["transit_histogram"]] == [0, 1, 0, 0, 0]
    texts = [a["text"] for a in body["attention"]]
    assert texts[0] == "#c9104 to CA: carrier reports delivery failure (Address incomplete)."
    assert "#c9102 to GB: shipped 24 days ago and still not delivered." in texts
    assert "#c9103 to GB: shipped 7 days ago, the carrier hasn't scanned it yet." in texts
    assert [a["level"] for a in body["attention"]] == ["fail", "warn", "warn"]
    by = {c["key"]: c for c in body["carriers"]}
    assert by["WanbExpress"]["delivered"] == 1 and by["WanbExpress"]["avg_days"] == 12.0 and by["Mystery Post"]["delivered"] == 0
    # Delivered parcels are left alone; the rest are asked about again once 45 minutes pass.
    db.run("UPDATE shipments SET checked_at=checked_at-3600")
    before = len(t17.calls)
    asyncio.run(backend.sync())
    asked = [i["number"] for c in t17.calls[before:] if c[0] == "gettrackinfo" for i in c[1]]
    assert sorted(asked) == ["A2", "A3", "A4"]


def test_without_a_17track_key_shopify_alone_still_fills_the_tab(client, shop, monkeypatch):
    monkeypatch.setattr(config, "TRACK17_KEY", "")
    now = time.time()
    shop.orders = [shipped(9201, now - 4 * DAY, "B1", shipped_at=now - 3 * DAY)]
    st = asyncio.run(backend.sync())
    assert st["track17"] == "off" and st["tracked"] == 0
    body = client.get("/hub/api/backend?range=30d", headers=API).json()
    assert body["shipments"][0]["state"] == "untracked" and body["tiles"]["untracked"] == 1
    assert body["sync"]["track17"] == "off" and body["attention"] == []


def test_disputes_that_shopify_wont_show_dont_stop_the_parcels(client, shop, monkeypatch):
    monkeypatch.setattr(config, "TRACK17_KEY", "")
    now = time.time()
    shop.orders = [shipped(9301, now - 4 * DAY, "C1")]
    real = shop.handler

    def handler(request):
        if request.url.path.endswith("/disputes.json"):
            return httpx.Response(403, json={"errors": "scope"})
        return real(request)
    monkeypatch.setattr(shop, "handler", handler)
    import shopify
    monkeypatch.setattr(shopify, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    st = asyncio.run(backend.sync())
    assert st["error"] == "Shopify would not show the disputes (403)." and db.kv_get("backend_synced_at")
    assert db.query("SELECT COUNT(*) AS n FROM shipments")[0]["n"] == 1


def test_sync_now_starts_a_read_and_needs_the_hub_header(client, shop, monkeypatch):
    runs = []

    async def fake_sync():
        runs.append(1)
        return backend.status()
    monkeypatch.setattr(backend, "sync", fake_sync)
    assert client.post("/hub/api/backend/sync", headers=API).status_code == 403
    r = client.post("/hub/api/backend/sync", headers=POST).json()
    assert r["started"] is True and "track17" in r


def test_refund_kinds_and_clean_notes():
    assert backend.refund_kind("Ethoca Alert") == "Chargeback alert"
    assert backend.refund_kind("customer cancelled before shipping") == "Cancelled"
    assert backend.refund_kind("Parcel lost, not received") == "Not received"
    assert backend.refund_kind("") == "No note" and backend.refund_kind("goodwill") == "Other"
    assert backend.clean_note("call me +1 (647) 555-0199 or jane@example.com please") == "call me or please"
    assert backend.carrier_code("China Post") == 3011 and backend.carrier_code("HUA_HAN") == 190003
    assert backend.carrier_code("TDPacket") == 191829 and backend.carrier_code("Royal Mail") is None


def test_ship_state_rules():
    now = time.time()
    base = {"registered": 1, "fulfilled_at": now - 2 * DAY, "status": "InTransit", "last_event_at": now - DAY}
    assert backend.ship_state(base, now) == "transit"
    assert backend.ship_state({**base, "status": "Delivered"}, now) == "delivered"
    assert backend.ship_state({**base, "registered": 0}, now) == "untracked"
    assert backend.ship_state({**base, "last_event_at": now - 8 * DAY}, now) == "stuck"
    assert backend.ship_state({**base, "fulfilled_at": now - 21 * DAY}, now) == "late"
    assert backend.ship_state({**base, "status": "NotFound", "fulfilled_at": now - 6 * DAY, "last_event_at": None}, now) == "unscanned"
    assert backend.ship_state({**base, "status": "NotFound", "fulfilled_at": now - 2 * DAY, "last_event_at": None}, now) == "transit"
    assert backend.ship_state({**base, "status": "Exception"}, now) == "failed"


def test_a_dispute_on_an_old_order_reads_that_order_and_one_under_review_needs_no_hand(client, shop, monkeypatch):
    monkeypatch.setattr(config, "TRACK17_KEY", "")
    now = time.time()
    old = shipped(9401, now - 200 * DAY, "OLD9", country="CA", city="Calgary")
    shop.orders = [old]
    shop.disputes = [{"id": 801, "order_id": 9401, "type": "chargeback", "amount": "33.24", "currency": "USD",
                      "reason": "fraudulent", "status": "under_review", "evidence_due_by": iso(now - 20 * DAY),
                      "evidence_sent_on": iso(now - 22 * DAY), "finalized_on": None, "initiated_at": iso(now - 25 * DAY)}]
    reads = []
    real = shop.handler

    def handler(request):
        if re.search(r"/orders/\d+\.json$", request.url.path):
            reads.append(request.url.path)
        if request.url.path.endswith("/orders.json"):
            return httpx.Response(200, json={"orders": []})           # the listing is the last 90 days: not this one
        return real(request)
    import shopify
    monkeypatch.setattr(shopify, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asyncio.run(backend.sync())
    assert len(reads) == 1 and db.query("SELECT country FROM backend_orders WHERE order_id='9401'")[0]["country"] == "CA"
    body = client.get("/hub/api/backend?range=90d", headers=API).json()
    assert body["disputes"][0]["order_name"] == "#c9401" and body["disputes"][0]["bucket"] == "open"
    assert body["attention"] == [] and body["tiles"]["disputes_open"] == 1
    asyncio.run(backend.sync())                                        # the old order stays while its dispute exists
    assert db.query("SELECT COUNT(*) AS n FROM backend_orders")[0]["n"] == 1
