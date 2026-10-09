"""The Database tab: who buys, from where, on what, the abandoned checkouts
and the quiz. Shopify is mocked; the quiz posts to /quiz like the page does."""
import asyncio
import datetime as dt
import json
import re
import time

import httpx
import pytest

import backend
import config
import database
import db
from test_hub import API, client, fresh, meta, shop, make_order  # noqa: F401  (fixtures)

DAY = 86400


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


def sale(oid, created, first, last, country="GB", city="Leeds", qty=1, ua="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Instagram 300.0",
         total="59.95", **over):
    o = make_order(oid, created, total_price=total, **over)
    o["customer"] = {"first_name": first, "last_name": last, "email": "x@example.com"}
    o["shipping_address"] = {"first_name": first, "last_name": last, "address1": "1 Oak St", "city": city, "country_code": country,
                             "zip": "L6M 5P6", "phone": "555-0199"}
    o["client_details"] = {"user_agent": ua, "browser_ip": "203.0.113.9"}
    o["line_items"] = [{"title": "SpermFuel+", "quantity": qty, "price": "29.99"}]
    o["fulfillments"], o["refunds"] = [], []
    return o


@pytest.fixture(autouse=True)
def quiet(fresh, monkeypatch):
    monkeypatch.setattr(config, "TRACK17_KEY", "")
    backend._state.update(last=0.0, error="", running=False, track17="")


def test_names_and_browsers_say_who_buys_and_on_what():
    assert database.who_from_name("Sarah") == "her" and database.who_from_name("mohammed") == "him" and database.who_from_name("Xq") == ""
    assert database.faith_from_names("Mohammed", "Smith") == "muslim" and database.faith_from_names("John", "Khan") == "muslim"
    assert database.faith_from_names("John", "Smith") == "other" and database.faith_from_names("", "") == ""
    assert database.device_of("Mozilla/5.0 (Linux; Android 14) [FB_IAB/FB4A;FBAV/5]") == "android"
    assert database.app_of("Mozilla/5.0 (Linux; Android 14) [FB_IAB/FB4A;FBAV/5]") == "facebook"
    assert database.app_of("Mozilla/5.0 (iPhone) Instagram 300.0") == "instagram" and database.device_of("Mozilla/5.0 (Windows NT 10.0)") == "desktop"
    assert database.checkout_step({"shipping_lines": [{}]}) == "payment" and database.checkout_step({"email": "a@b.c"}) == "shipping"
    assert database.checkout_step({}) == "cart"


def test_sales_abandoned_checkouts_and_the_quiz_fill_the_tab_without_personal_data(client, shop, monkeypatch):
    now = time.time()
    shop.orders = [sale(9701, now - 2 * DAY, "Sarah", "Khan", qty=3, total="91.85"),
                   sale(9702, now - 3 * DAY, "John", "Smith", country="US", city="Austin", qty=1, ua="Mozilla/5.0 (Linux; Android 14) [FB_IAB/FB4A;FBAV/5]"),
                   sale(9703, now - 4 * DAY, "Ahmed", "Ali", country="US", city="Austin", qty=5, total="124.75"),
                   sale(9704, now - 5 * DAY, "Ahmed", "Ali", country="US", qty=1, source_name="subscription_contract", total="34.99")]
    # The quiz-to-store tie: the tracker credited #c9701 to an ad, with the quiz session on its link.
    db.upsert_order(shop.orders[0])
    db.mark_order("9701", "sent", kind="purchase")
    db.set_order_attribution("9701", {"meta": True, "source": "browser", "click": True, "ad_id": "A1", "ad_name": "Quiz ad",
                                      "adset_name": "top", "campaign_name": "sperm 2", "lp": "ranking-listicle", "via": "quiz",
                                      "qs": "sess-aaaaaaaa", "fbc": "fb.1.2.X"})
    checkouts = [{"id": 1, "token": "tok-pay", "created_at": iso(now - DAY), "updated_at": iso(now - DAY), "completed_at": None,
                  "total_price": "87.95", "currency": "USD", "email": "jane@example.com", "landing_site": "/products/spermfuel?lp=quiz&ad_id=A1&utm_source=facebook",
                  "shipping_address": {"first_name": "Jane", "city": "Calgary", "country_code": "CA"}, "shipping_lines": [{"title": "Express"}],
                  "line_items": [{"title": "SpermFuel+", "quantity": 3, "price": "29.99"}], "client_details": {"user_agent": "Mozilla/5.0 (iPhone)"}},
                 {"id": 2, "token": "tok-cart", "created_at": iso(now - 2 * DAY), "updated_at": iso(now - 2 * DAY), "completed_at": None,
                  "total_price": "29.99", "currency": "USD", "email": None, "landing_site": "/", "shipping_address": None, "shipping_lines": [],
                  "line_items": [{"title": "SpermFuel+", "quantity": 1, "price": "29.99"}], "client_details": {}},
                 {"id": 3, "token": "tok-back", "created_at": iso(now - 3 * DAY), "updated_at": iso(now - DAY), "completed_at": iso(now - DAY),
                  "total_price": "59.95", "currency": "USD", "email": "a@b.c", "landing_site": "/products/spermfuel?lp=ranking-listicle&ad_id=A2&utm_source=facebook",
                  "shipping_address": {"city": "Leeds", "country_code": "GB"}, "shipping_lines": [{}], "line_items": [], "client_details": {}}]
    real = shop.handler

    def handler(request):
        if request.url.path.endswith("/checkouts.json"):
            assert request.url.params.get("limit") == "250"
            return httpx.Response(200, json={"checkouts": checkouts})
        return real(request)
    import shopify
    monkeypatch.setattr(shopify, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    # The quiz page posts its steps.
    def post(body):
        return client.post("/quiz", content=json.dumps(body), headers={"Content-Type": "application/json",
                                                                       "User-Agent": "Mozilla/5.0 (iPhone) Instagram 300.0"})
    for sess, answers, dest, reveal in (("sess-aaaaaaaa", ["Me, I'm the one trying", "6 to 12 months"], "listicle", True),
                                       ("sess-bbbbbbbb", ["My partner, I'm here for him", "Over a year"], "product", False),
                                       ("sess-cccccccc", ["Me, I'm the one trying"], "", False)):
        assert post({"session": sess, "kind": "start", "step": 0, "tz": "Europe/London"}).status_code == 204
        for i, a in enumerate(answers):
            q = ["Who's this for?", "How long have you been trying?"][i]
            assert post({"session": sess, "kind": "answer", "step": i + 1, "question": q, "answer": a, "ms": 4000 + i * 1000, "tz": "Europe/London"}).status_code == 204
        if reveal:
            post({"session": sess, "kind": "reveal", "step": 9})
        if dest:
            post({"session": sess, "kind": "finish", "step": 9, "dest": dest})
    assert post({"session": "x", "kind": "answer"}).status_code == 400                   # junk is refused
    assert post({"session": "sess-dddddddd", "kind": "dance"}).status_code == 400
    assert client.options("/quiz").status_code == 204
    asyncio.run(backend.sync())

    body = client.get("/hub/api/database?range=30d", headers=API).json()
    t = body["tiles"]
    assert (t["sales"], t["revenue"], t["mrr"], t["countries"], t["quiz_takers"], t["abandoned"]) == (3, 276.55, 1, 2, 3, 3)
    who = {w["key"]: w["n"] for w in body["who"]}
    assert who == {"her": 1, "him": 2}
    faith = {w["key"]: w["n"] for w in body["faith"]}
    assert faith == {"muslim": 2, "other": 1}
    us = next(c for c in body["countries"] if c["key"] == "US")
    assert (us["n"], us["mrr"], us["bundles"], us["aov"]) == (2, 1, {"1": 1, "3": 0, "5": 1}, 92.35)   # (59.95 + 124.75) / 2
    assert {d["key"]: d["n"] for d in body["devices"]} == {"iphone": 2, "android": 1}
    assert {d["key"]: d["n"] for d in body["apps"]} == {"instagram": 2, "facebook": 1}
    assert {d["key"]: d["n"] for d in body["bundles"]} == {"1": 1, "3": 1, "5": 1}
    assert {d["key"]: d["n"] for d in body["landing"]} == {"listicle": 1, "not from an ad": 2}
    assert sum(sum(r) for r in body["heatmap"]) == 3 and len(body["heatmap"]) == 7 and len(body["heatmap"][0]) == 24
    a = body["abandoned"]
    assert (a["n"], a["value"], a["recovered"]) == (3, 177.89, 1)
    assert {s["key"]: s["n"] for s in a["steps"]} == {"payment": 2, "cart": 1}
    assert {s["key"]: s["n"] for s in a["landing"]} == {"quiz": 1, "direct": 1, "listicle": 1}
    q = body["quiz"]
    assert (q["takers"], q["finished"], q["revealed"], q["bought"]) == (3, 2, 1, 1)
    assert [x["question"] for x in q["questions"]] == ["Who's this for?", "How long have you been trying?"]
    assert (q["questions"][1]["reached"], q["questions"][1]["quit_before"], q["worst_question"]) == (2, 1, "How long have you been trying?")
    first = {x["answer"]: x for x in q["questions"][0]["answers"]}
    assert (first["Me, I'm the one trying"]["n"], first["Me, I'm the one trying"]["bought"], first["Me, I'm the one trying"]["revenue"]) == (2, 1, 91.85)
    assert q["questions"][0]["median_s"] == 4.0 and {d["dest"]: d["n"] for d in q["destinations"]} == {"listicle": 1, "product": 1}
    assert q["countries"][0] == {"key": "GB", "n": 3} and q["devices"][0]["key"] == "iphone"
    rec = next(r for r in body["records"] if r["order_name"] == "#c9701")
    assert (rec["campaign"], rec["ad"], rec["landing"], rec["quiz"], rec["device"], rec["app"], rec["qty"]) == ("sperm 2", "Quiz ad", "listicle", True, "iphone", "instagram", 3)
    assert any("started in the quiz" in l for l in body["trends"]) and any("abandoned" in l for l in body["trends"])
    dump = json.dumps(body)
    for pii in ("Sarah", "Khan", "John", "Smith", "Ahmed", "Ali\"", "Jane", "example.com", "555-0199", "Oak St", "L6M", "203.0.113"):
        assert pii not in dump, pii
    # Filters narrow everything: one country, one landing page, one kind.
    us_only = client.get("/hub/api/database?range=30d&country=US", headers=API).json()
    assert us_only["tiles"]["sales"] == 2 and us_only["filters"]["country"] == "US" and len(us_only["records"]) == 3
    assert client.get("/hub/api/database?range=30d&landing=listicle", headers=API).json()["tiles"]["sales"] == 1
    assert client.get("/hub/api/database?range=30d&kind=mrr", headers=API).json()["tiles"]["sales"] == 0
    # The product switch, like the P&L's: every product with its sales, and the tab narrowed to one.
    assert body["products"] == [{"key": "SpermFuel+", "n": 4}]
    by_product = client.get("/hub/api/database?range=30d&product=SpermFuel%2B", headers=API).json()
    assert by_product["tiles"]["sales"] == 3 and by_product["filters"]["product"] == "SpermFuel+" and by_product["abandoned"]["n"] == 2
    assert client.get("/hub/api/database?range=30d&product=Other", headers=API).json()["tiles"]["sales"] == 0
    # The CSVs: sales, abandoned checkouts, quiz takers; signed in only; nothing personal.
    for what, head in (("sales", "order,created,kind"), ("abandoned", "created,country,city"), ("quiz", "session,started,country")):
        r = client.get(f"/hub/api/database/export?what={what}&range=30d", headers=API)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and r.text.startswith(head)
        assert 'attachment; filename="' in r.headers["content-disposition"] and "-30d.csv" in r.headers["content-disposition"]
        for pii in ("Sarah", "Khan", "example.com", "555-0199"):
            assert pii not in r.text
    assert "#c9701,"in client.get("/hub/api/database/export?what=sales&range=30d", headers=API).text
    assert client.get("/hub/api/database/export?what=sales").status_code == 401
    assert client.get("/hub/api/database").status_code == 401


def test_quiz_events_come_from_the_page_only_within_limits(client):
    long = "a" * 400
    assert client.post("/quiz", content=json.dumps({"session": "sess-eeeeeeee", "kind": "answer", "step": 1, "question": long, "answer": "x y@z.com 555-0199 ok", "tz": "Australia/Sydney"}),
                       headers={"Content-Type": "application/json"}).status_code == 204
    row = db.query("SELECT * FROM quiz_events")[0]
    assert len(row["question"]) == 160 and row["answer"] == "x   ok" and row["country"] == "AU"
    assert client.post("/quiz", content="[]", headers={"Content-Type": "application/json"}).status_code == 400
    assert client.post("/quiz", content="not json", headers={"Content-Type": "application/json"}).status_code == 400


def test_europe_without_the_uk_is_one_market(client, shop, monkeypatch):
    now = time.time()
    shop.orders = [sale(9801, now - DAY, "Lars", "Berg", country="SE"), sale(9802, now - DAY, "Pierre", "Martin", country="FR"),
                   sale(9803, now - DAY, "John", "Smith", country="GB"), sale(9804, now - 2 * DAY, "Ahmed", "Ali", country="US")]
    asyncio.run(backend.sync())
    body = client.get("/hub/api/database?range=30d", headers=API).json()
    rows = {c["key"]: c for c in body["countries"]}
    assert set(rows) == {"EU", "GB", "US"} and rows["EU"]["n"] == 2 and rows["EU"]["members"] == ["FR", "SE"]
    assert rows["GB"]["members"] == [] and body["tiles"]["countries"] == 3
    assert any(l.startswith("Europe (not UK) is 50%") for l in body["trends"])
    eu = client.get("/hub/api/database?range=30d&country=EU", headers=API).json()
    assert eu["tiles"]["sales"] == 2 and {r["country"] for r in eu["records"]} == {"SE", "FR"}
