"""Evoria (evoria.online): its own pixel, webhooks and tab, never mixed into Core's."""
import asyncio
import base64
import hashlib
import hmac
import json
import time

import pytest
from starlette.testclient import TestClient

import app as app_module
import attribution
import config
import db
import evoria
import hub
import meta_capi

db.init()
evoria.init()

SECRET = "evoria-test-secret"


@pytest.fixture
def sent(monkeypatch):
    """Every event that would reach Meta: (event, test_event_code)."""
    out = []

    async def post(events, test_event_code="", pixel=None):
        for e in events:
            out.append((e, test_event_code or (pixel or {}).get("test_event_code") or ""))
        return {"events_received": len(events), "fbtrace_id": "TRACE"}
    monkeypatch.setattr(meta_capi, "_post", post)
    monkeypatch.setattr(config, "EVORIA_WEBHOOK_SECRET", SECRET)
    for t in ("evoria_orders", "evoria_sessions", "evoria_events"):
        db.run(f"DELETE FROM {t}")
    return out


def lasso_order(oid=5400, **over):
    """An order Lasso writes to Evoria's Shopify (channel LP-FUNNEL), like #5270 on 19 Jul."""
    now = time.time()
    o = {"id": oid, "name": f"#{oid}", "email": "buyer@evoria-buyer.net", "phone": "+16042020140",
         "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now)), "test": False,
         "source_name": "8833925", "financial_status": "paid", "total_price": "124.02", "currency": "USD", "tags": "",
         "shipping_address": {"first_name": "Debby", "last_name": "J", "city": "Woodview", "zip": "K0L 3E0", "country_code": "CA"},
         "line_items": [{"title": "SpermFuel+", "variant_title": "Buy 3 Get 2 Free", "quantity": 3, "price": "41.34",
                         "product_id": 8466598527161},
                        {"title": "SpermFuel+", "variant_title": "Try it first", "quantity": 2, "price": "0.00",
                         "product_id": 8466598527161}],
         "note_attributes": [{"name": "fbc", "value": attribution.make_fbc("IwZXh0bgNhZW0BMABhZGlkAasevoriaclick", now - 300)},
                             {"name": "fbp", "value": "fb.1.1784475917887.1262836516"},
                             {"name": "utm_source", "value": "facebook"}, {"name": "utm_medium", "value": "paid_social"},
                             {"name": "utm_content", "value": "New Sales Ad - Copy 4"}, {"name": "utm_term", "value": "NB9-b"},
                             # what the cart drawer saves on the cart (Lasso drops these from its own link)
                             {"name": "ad_id", "value": "120251099013870090"}, {"name": "adset_id", "value": "120251098021230090"},
                             {"name": "lp", "value": "8-reasons-listicle-2"}, {"name": "core_cid", "value": "evo-browser-1"}]}
    o.update(over)
    return o


def signed(body: bytes, secret=SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def test_an_evoria_order_sends_one_purchase_to_core_club_and_stays_out_of_cores_tables(sent):
    # The buyer's browser opened the product page first (Evoria's pixel).
    evoria.ingest_pixel({"name": "page_viewed", "id": "v0", "cid": "evo-browser-1", "fbp": "fb.1.1784475917887.1262836516",
                         "url": "https://evoria.online/products/spermfuel?lp=8-reasons-listicle-2&ad_id=120251099013870090"},
                        "203.0.113.9", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148")
    body = json.dumps(lasso_order()).encode()
    client = TestClient(app_module.app)          # no lifespan: the app's background loops stay off
    r = client.post("/evoria/webhooks", content=body, headers={"x-shopify-topic": "orders/create",
                                                                "x-shopify-hmac-sha256": signed(body)})
    assert r.status_code == 200
    assert client.post("/evoria/webhooks", content=body, headers={"x-shopify-topic": "orders/create",
                                                                   "x-shopify-hmac-sha256": "bad"}).status_code == 401
    asyncio.run(evoria.process_order("5400"))                 # the background send, again: never twice
    purchases = [e for e, _ in sent if e["event_name"] == "Purchase"]
    assert purchases[0]["action_source"] == "website" and purchases[0]["user_data"]["client_user_agent"]
    assert len(purchases) == 1
    p = purchases[0]
    assert p["event_id"] == "evoria_order_5400" and p["custom_data"]["store"] == "evoria"
    assert p["custom_data"]["value"] == 124.02 and p["custom_data"]["num_items"] == 5
    assert p["user_data"]["fbc"].endswith("IwZXh0bgNhZW0BMABhZGlkAasevoriaclick") and p["user_data"]["em"]
    assert p["event_source_url"].startswith("https://evoria.online/")
    # The decision: the ad the cart saved, through the Evoria listicle.
    rec = json.loads(db.query("SELECT attribution FROM evoria_orders WHERE order_id='5400'")[0]["attribution"])
    assert rec["meta"] is True and rec["ad_id"] == "120251099013870090" and rec["lp"] == "8-reasons-listicle-2"
    # Core's tables never hold an Evoria row.
    assert not db.query("SELECT 1 FROM orders WHERE order_id='5400'")
    assert not db.query("SELECT 1 FROM events WHERE event_id LIKE 'evoria_%'")
    assert db.query("SELECT COUNT(*) AS n FROM evoria_events WHERE event_id='evoria_order_5400' AND status='sent'")[0]["n"] == 1


def test_an_evoria_rebill_goes_as_a_renewal_never_a_purchase(sent):
    o = lasso_order(5401, tags="Subscription Renewal", total_price="59.95")
    assert evoria.store_order(o, "orders/create") is True
    asyncio.run(evoria.process_order("5401"))
    (e, _), = sent
    assert e["event_name"] == config.RENEWAL_EVENT_NAME and e["event_id"] == "evoria_renewal_5401"
    assert "fbc" not in e["user_data"]
    f = evoria.facts(0, time.time() + 60)
    assert [x["type"] for x in f] == ["rebill"]


def test_the_evoria_pixel_keeps_its_own_shoppers_and_sends_with_evoria_ids(sent):
    url = ("https://evoria.online/products/spermfuel?utm_source=facebook&utm_content=New+Sales+Ad&utm_term=NB9-b"
           "&ad_id=120251099013870090&lp=8-reasons-listicle-2&fbclid=IwZXh0bgNhZW0BMABhZGlkAasevoriaclick")
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
    ev = evoria.ingest_pixel({"name": "page_viewed", "id": "p1", "url": url, "cid": "evo-browser-1",
                              "fbp": "fb.1.1.2"}, "203.0.113.9", ua)
    assert ev["event_name"] == "PageView" and ev["event_id"] == "evoria_page_viewed_p1"
    assert ev["custom_data"]["store"] == "evoria" and ev["user_data"]["fbc"].endswith("asevoriaclick")
    vc = evoria.ingest_pixel({"name": "product_viewed", "id": "p2", "url": url, "cid": "evo-browser-1",
                              "custom": {"value": 59.95, "currency": "USD", "content_name": "SpermFuel+",
                                         "items": [{"product_id": 8466598527161, "quantity": 1}]}}, "203.0.113.9", ua)
    assert vc["event_name"] == "ViewContent" and vc["custom_data"]["content_ids"] == ["8466598527161"]
    with pytest.raises(ValueError):
        evoria.ingest_pixel({"name": "page_viewed", "id": "x", "url": "https://getcoresupps.com/", "cid": "c"}, "", ua)
    s = evoria._session("evo-browser-1")
    assert json.loads(s["ad_params"])["ad_id"] == "120251099013870090" and s["views"] == 1
    assert not db.query("SELECT 1 FROM sessions WHERE client_id='evo-browser-1'")      # never Core's
    # The order the same browser places later finds it through the cart's core_cid.
    assert evoria.match_session(lasso_order())["client_id"] == "evo-browser-1"
    assert evoria.funnel(0, time.time() + 60) == {"visitors": 1, "product_views": 1, "add_to_carts": 0}


def test_a_core_test_link_sends_only_test_events_and_keeps_them_off_the_tab(sent):
    url = "https://evoria.online/products/spermfuel?core_test=TEST4242&fbclid=IwZXh0bgNhZW0BMABhZGlkAasevoriaclick"
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0"
    ev = evoria.ingest_pixel({"name": "page_viewed", "id": "t1", "url": url, "cid": "evo-test"}, "198.51.100.2", ua)
    asyncio.run(evoria.send_pixel_event(ev, "evo-test"))
    o = lasso_order(5402)
    o["note_attributes"] = [a for a in o["note_attributes"] if a["name"] != "core_cid"] + [{"name": "core_cid", "value": "evo-test"}]
    evoria.store_order(o, "orders/create")
    asyncio.run(evoria.process_order("5402"))
    assert [code for _, code in sent] == ["TEST4242", "TEST4242"]
    f = evoria.facts(0, time.time() + 60)                         # a test purchase never counts as a sale...
    assert [(x["type"], x["reason"]) for x in f] == [("skipped", "Test")]
    assert f[0]["test_credit"]["ad_id"] == "120251099013870090"   # ...but its click is listed, to prove the run


def test_core_never_gets_evorias_ads_and_evoria_gets_only_its_own(monkeypatch):
    import meta_ads
    rows = [{"ad_id": "CORE1", "spend": 10.0, "meta_purchases": 1.0}, {"ad_id": "EVO1", "spend": 7.0, "meta_purchases": 2.0}]

    async def insights(since, until):
        return {"connected": True, "rows": [dict(r) for r in rows], "currency": "USD"}
    monkeypatch.setattr(meta_ads, "ad_insights", insights)
    db.run("DELETE FROM meta_kv WHERE key IN ('evoria:campaigns','evoria:ads')")
    monkeypatch.setattr(meta_ads, "cached_links", lambda: [
        {"ad_id": "CORE1", "link": "https://why-still-one-line.netlify.app/"},
        {"ad_id": "EVO1", "link": "https://8reaonse.netlify.app/?x=1"}])
    assert evoria.ad_ids() == {"EVO1"}
    core = asyncio.run(hub._ads("2026-10-19", "2026-10-19"))
    evo = asyncio.run(hub._ads("2026-10-19", "2026-10-19", store="evoria"))
    assert [r["ad_id"] for r in core["rows"]] == ["CORE1"] and [r["ad_id"] for r in evo["rows"]] == ["EVO1"]
    # An Evoria ad stays Evoria's once seen, even when it drops out of the live links.
    monkeypatch.setattr(meta_ads, "cached_links", lambda: [])
    assert [r["ad_id"] for r in asyncio.run(hub._ads("2026-10-19", "2026-10-19"))["rows"]] == ["CORE1"]
    # No Evoria ads known at all: Core's rows are exactly Meta's, as before.
    db.run("DELETE FROM meta_kv WHERE key IN ('evoria:campaigns','evoria:ads')")
    assert len(asyncio.run(hub._ads("2026-10-19", "2026-10-19"))["rows"]) == 2


def test_a_new_evoria_listicle_copy_is_learned_from_its_own_page_views(monkeypatch):
    db.run("DELETE FROM meta_kv WHERE key='evoria:landing_hosts'")
    evoria.note_listicle("SpermFuel+ Ranking Listicle", "https://fertility-supplements-ranked.netlify.app/")
    evoria.note_listicle("SpermFuel+ 8 Reasons Listicle 2", "https://evoria-copy.netlify.app/?fbclid=x")
    evoria.note_listicle("SpermFuel+ Ranking Listicle 2", "https://ranked-evoria.netlify.app/?fbclid=x")
    assert "evoria-copy.netlify.app" in evoria.landing_hosts()
    assert "ranked-evoria.netlify.app" in evoria.landing_hosts()
    assert "fertility-supplements-ranked.netlify.app" not in evoria.landing_hosts()
    db.run("DELETE FROM meta_kv WHERE key='evoria:landing_hosts'")


def test_shopifys_sample_order_is_a_test_and_never_reaches_meta(sent):
    sample = lasso_order(820982911946154508, email="jon@example.com")
    assert evoria.store_order(sample, "orders/create") is False
    assert sent == [] and db.query("SELECT kind FROM evoria_orders WHERE order_id='820982911946154508'")[0]["kind"] == "test"


def test_a_whole_evoria_campaign_is_evorias_even_its_paused_ads_and_an_odd_link(monkeypatch):
    # whop sperm (Oct 19): five ads to the Evoria listicle, one to a Core page, two paused today.
    import meta_ads
    db.run("DELETE FROM meta_kv WHERE key IN ('evoria:campaigns','evoria:ads')")
    links = [{"ad_id": "W1", "campaign_id": "WHOP", "link": "https://8reaonse.netlify.app/"},
             {"ad_id": "W2", "campaign_id": "WHOP", "link": "https://8reaonse.netlify.app/?a=1"},
             {"ad_id": "W3", "campaign_id": "WHOP", "link": "https://weranked.netlify.app/"},
             {"ad_id": "C1", "campaign_id": "SPERM2", "link": "https://weranked.netlify.app/"},
             {"ad_id": "C2", "campaign_id": "SPERM2", "link": "https://why-still-one-line.netlify.app/"}]
    monkeypatch.setattr(meta_ads, "cached_links", lambda: links)
    assert evoria.campaign_ids() == {"WHOP"} and evoria.ad_ids() == {"W1", "W2", "W3"}
    rows = [{"ad_id": "W1", "campaign_id": "WHOP", "spend": 4.56}, {"ad_id": "W9", "campaign_id": "WHOP", "spend": 20.0},
            {"ad_id": "C1", "campaign_id": "SPERM2", "spend": 50.0}]

    async def insights(since, until):
        return {"connected": True, "rows": [dict(r) for r in rows], "currency": "USD"}
    monkeypatch.setattr(meta_ads, "ad_insights", insights)
    # W9 was paused before the links were read: its campaign still makes it Evoria's.
    evo = asyncio.run(hub._ads("2026-10-19", "2026-10-19", store="evoria"))
    core = asyncio.run(hub._ads("2026-10-19", "2026-10-19"))
    assert sorted(r["ad_id"] for r in evo["rows"]) == ["W1", "W9"] and [r["ad_id"] for r in core["rows"]] == ["C1"]
    # Remembered: the next read without those ads still keeps them Evoria's.
    monkeypatch.setattr(meta_ads, "cached_links", lambda: [])
    assert "WHOP" in evoria.campaign_ids() and "W3" in evoria.ad_ids()
    # Core's 7-day charts lose Evoria's spend too.
    daily, days = hub._core_days({"2026-10-19": 61.41}, {"2026-10-19": [{"campaign_id": "WHOP", "spend": 61.41 - 50},
                                                                       {"campaign_id": "SPERM2", "spend": 50.0}]})
    assert daily == {"2026-10-19": 50.0} and [c["campaign_id"] for c in days["2026-10-19"]] == ["SPERM2"]
    db.run("DELETE FROM meta_kv WHERE key IN ('evoria:campaigns','evoria:ads')")


def test_the_evoria_tab_says_what_a_purchase_gave_meta_to_match_names_only(sent):
    evoria.store_order(lasso_order(5410), "orders/create")
    asyncio.run(evoria.process_order("5410"))
    m = evoria.match_keys("5410")
    assert m["click"] is True and {"em", "ph", "fbc"} <= set(m["keys"]) and 0 <= m["click_to_order_min"] <= 10
    assert "buyer@evoria-buyer.net" not in json.dumps(m)


def test_lassos_bare_fbclid_is_the_ad_click_and_finds_the_browser(sent):
    # #5313 (Oct 19): Lasso kept only fbclid + utm tags, no fbc, fbp or our cid.
    click = "IwZXh0bgNhZW0BMABhZGlkAassLassoOnlyClick"
    evoria.ingest_pixel({"name": "page_viewed", "id": "l1", "cid": "evo-lasso", "fbp": "fb.1.1.99",
                         "url": "https://evoria.online/products/spermfuel?fbclid=" + click + "&utm_source=fb&ad_id=120251240484590090"},
                        "203.0.113.7", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148")
    o = lasso_order(5420)
    o["note_attributes"] = [{"name": "fbclid", "value": click}, {"name": "utm_source", "value": "fb"},
                            {"name": "utm_content", "value": "6 - Copy 2"}, {"name": "shopify-cart-token", "value": "x"}]
    assert evoria.match_session(o)["client_id"] == "evo-lasso"
    evoria.store_order(o, "orders/create")
    asyncio.run(evoria.process_order("5420"))
    (p, _), = [x for x in sent if x[0]["event_name"] == "Purchase"]
    assert p["user_data"]["fbc"].endswith("." + click) and p["user_data"]["fbp"] == "fb.1.1.99"
    assert p["user_data"]["client_ip_address"] and p["user_data"]["client_user_agent"]
    m = evoria.match_keys("5420")
    assert m["click"] and {"fbc", "fbp", "client_ip_address"} <= set(m["keys"])
    # No browser on record: the click alone, stamped at the order.
    o2 = lasso_order(5421)
    o2["note_attributes"] = [{"name": "fbclid", "value": click + "Two"}]
    evoria.store_order(o2, "orders/create")
    asyncio.run(evoria.process_order("5421"))
    p2 = [x[0] for x in sent if x[0]["event_id"] == "evoria_order_5421"][0]
    assert p2["user_data"]["fbc"].endswith("." + click + "Two")


def test_an_update_to_an_older_order_is_kept_never_sent_and_never_a_sale(sent):
    # #5238 (Oct 19): placed days before tracking, first heard of through orders/updated.
    old = lasso_order(5238, created_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 3 * 86400)))
    assert evoria.store_order(old, "orders/updated") is False
    rebill = lasso_order(5283, tags="Subscription Renewal",
                         created_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 5 * 3600)))
    assert evoria.store_order(rebill, "orders/updated") is False
    assert asyncio.run(evoria.process_order("5238")) == "skipped" and sent == []
    f = evoria.facts(0, time.time() + 60)
    assert {x["type"] for x in f} == {"skipped"} and {x["reason"] for x in f} == {"Before Tracking"}
    # A brand-new order whose create webhook was missed still goes, from its first update.
    new = lasso_order(5440)
    assert evoria.store_order(new, "orders/updated") is True
