"""The Backend tab's stock (off orders) and subscription take rate."""
import time

import db
import stock

db.init()


def _order(oid, units=3, sub=False, hours_ago=1.0, gift=True, cancelled=False, title="SpermFuel+", variant=None):
    created = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - hours_ago * 3600))
    lines = [{"product_id": 1, "title": title, "variant_title": variant, "price": "23.50", "quantity": units, "requires_shipping": True,
              "discount_allocations": []}]
    if gift:
        lines.append({"product_id": 2, "title": "Swimmer Rebuild App", "price": "82.13", "quantity": 1, "requires_shipping": False,
                      "discount_allocations": [{"amount": "82.13"}]})
    o = {"id": oid, "name": f"#t{oid}", "created_at": created, "total_price": "70.50", "currency": "USD", "line_items": lines,
         "tags": "Kaching Subscription, Kaching Subscription First Order" if sub else "Kaching Bundles",
         "cancelled_at": created if cancelled else None}
    db.upsert_order(o)
    db.run("UPDATE orders SET kind='purchase', status='sent' WHERE order_id=?", (str(oid),))
    return o


def _inv(**kw):
    got = stock.inventory(**kw)
    return {p["product"]: p for p in got["pools"]}, {r["label"]: r for r in got["rows"]}


def test_stock_goes_off_orders_from_the_count_typed_in_and_says_stock_left():
    db.run("DELETE FROM orders")
    db.run("DELETE FROM backend_orders")
    db.kv_set(stock.COUNTS_KEY, "{}")
    _order(91001, units=3, hours_ago=50)                 # before the count: doesn't come off it
    stock.set_count("SpermFuel+", 100, now=time.time() - 30 * 3600)
    _order(91002, units=5, hours_ago=20, sub=True)
    _order(91003, units=3, hours_ago=1)
    _order(91004, units=5, hours_ago=2, cancelled=True)  # cancelled: never shipped
    pools, rows = _inv()
    sf = pools["SpermFuel+"]
    assert sf["count"] == 100 and sf["on_hand"] == 92 and sf["pace"] == round(11 / 7, 1)
    assert sf["days_left"] == round(92 / (11 / 7), 1) and not sf["low"]
    assert rows["SpermFuel+"]["sold"] == 11 and rows["SpermFuel+"]["revenue"] == 11 * 23.5
    assert (rows["SpermFuel+"]["orders"], rows["SpermFuel+"]["subscribed"], rows["SpermFuel+"]["take_rate"]) == (3, 1, 33.3)
    assert list(rows) == ["SpermFuel+", "Swimmer Fuel bags", "Swimmer Fuel sachets", "REBUILD 9", "NRTURE COAT & PROTECT"]
    assert [p["product"] for p in stock.inventory()["pools"]] == ["SpermFuel+", "Core Swimmer Fuel", "REBUILD 9", "NRTURE COAT & PROTECT"]
    assert _inv(days=1.5 / 24)[1]["SpermFuel+"]["sold"] == 3     # the tab's range: the last 90 minutes
    stock.set_count("SpermFuel+", 10)
    assert _inv()[0]["SpermFuel+"]["low"] is True
    stock.set_count("SpermFuel+", "")                     # blank clears it
    assert _inv()[0]["SpermFuel+"]["count"] is None
    db.run("DELETE FROM orders")


def test_take_rate_is_subscribed_new_sales_over_new_sales_per_day_and_bundle():
    db.run("DELETE FROM orders")
    _order(92001, units=3, sub=True, hours_ago=1)
    _order(92002, units=3, sub=False, hours_ago=1.5)
    _order(92003, units=5, sub=True, hours_ago=2)
    _order(92004, units=1, sub=True, hours_ago=2.5, cancelled=True)   # cancelled: left out
    t = stock.take_rates(time.time() - 86400, time.time() + 60)
    assert t["total"] == {"orders": 3, "subscribed": 2, "rate": 66.7}
    assert {b["bundle"]: (b["orders"], b["subscribed"]) for b in t["bundles"]} == {"3": (2, 1), "5": (1, 1)}
    assert sum(d["orders"] for d in t["days"]) == 3
    db.run("DELETE FROM orders")


def test_kachings_late_tag_counts_from_the_backends_latest_read():
    # Oct 23 2026: 11 of 14 new sales subscribed, the tab said 5. Kaching tags the order seconds
    # after it's placed; the tracker's copy is from that first second, the backend re-reads it.
    db.run("DELETE FROM orders")
    db.run("DELETE FROM backend_orders")
    o = _order(93001, units=3, sub=False, hours_ago=1)       # the first copy: no tag yet
    import backend
    backend._store_order({**o, "tags": "Kaching Bundles, Kaching Subscription, Kaching Subscription First Order",
                          "current_total_price": "70.50", "financial_status": "paid"}, time.time())
    assert db.query("SELECT subscribed, bundle FROM backend_orders WHERE order_id='93001'")[0]["subscribed"] == 1
    t = stock.take_rates(time.time() - 86400, time.time() + 60)
    assert t["total"] == {"orders": 1, "subscribed": 1, "rate": 100.0}
    db.run("DELETE FROM orders")
    db.run("DELETE FROM backend_orders")


def test_swimmer_fuel_bags_and_sachets_are_two_rows_on_one_stock():
    # Oct 23 2026: a bag is 30 sachets; loose sachets sell as Testosterone Core Fuel Pack x1 / x2 / x4.
    db.run("DELETE FROM orders")
    db.run("DELETE FROM backend_orders")
    db.kv_set(stock.COUNTS_KEY, "{}")
    stock.set_count("Core Swimmer Fuel", 10 * 30, now=time.time() - 10 * 3600)
    _order(94001, units=3, variant="3", title="CORE Swimmer Fuel", hours_ago=5)                          # 3 bags
    _order(94002, units=1, variant="Buy 1 Get 1 Free · 60 Stick Packs", title="CORE Swimmer Fuel", hours_ago=4)  # 2 bags
    _order(94003, units=1, title="Testosterone Core Fuel Pack x4", hours_ago=3)                           # 4 sachets
    _order(94004, units=2, title="Testosterone Core Fuel Pack", hours_ago=2)                              # 2 sachets
    pools, rows = _inv()
    assert rows["Swimmer Fuel bags"]["sold"] == 5 and rows["Swimmer Fuel sachets"]["sold"] == 6
    sw = pools["Core Swimmer Fuel"]
    assert sw["on_hand"] == 300 - 156 and sw["per_bag"] == 30 and sw["unit"] == "sachets"
    db.run("DELETE FROM orders")
    db.kv_set(stock.COUNTS_KEY, "{}")


def test_units_say_where_they_came_from_ads_mrr_post_purchase_thank_you_organic():
    # Oct 23 2026: a Swimmer Fuel bag sold on the post-purchase page (SAVE 48%), a SpermFuel+ bottle on
    # the thank-you page (SAVE 30%), the rest by the order: an ad, a rebill, organic.
    db.run("DELETE FROM orders")
    db.run("DELETE FROM backend_orders")
    def upsell(oid, title, offer):
        o = _order(oid, units=3, sub=True)
        db.run("DELETE FROM orders WHERE order_id=?", (str(oid),))
        return {**o, "tags": "Kaching Bundles, Kaching Upsell",
        "discount_applications": [{"title": offer}],
        "line_items": [{"title": "SpermFuel+", "quantity": 3, "price": "20", "properties": [{"name": "__kaching_bundles", "value": "{}"}]},
                       {"title": title, "variant_title": "1", "quantity": 1, "price": "45", "properties": [],
                        "discount_allocations": [{"amount": "20", "discount_application_index": 0}]}]}
    for o in (upsell(95001, "CORE Swimmer Fuel", "SAVE 48%"), upsell(95002, "SpermFuel+", "SAVE 30%")):
        db.upsert_order(o)
        db.run("UPDATE orders SET kind='purchase', status='sent' WHERE order_id=?", (str(o["id"]),))
    db.run("UPDATE orders SET attribution=? WHERE order_id='95001'", ('{"meta": true}',))
    db.run("UPDATE orders SET attribution=? WHERE order_id='95002'", ('{"meta": false}',))
    _order(95003, units=1)
    db.run("UPDATE orders SET kind='renewal' WHERE order_id='95003'")
    rows = _inv()[1]
    assert rows["Swimmer Fuel bags"]["from"]["ppu"] == 1
    assert rows["SpermFuel+"]["from"] == {"ads": 3, "mrr": 1, "ppu": 0, "thanks": 1, "email": 0, "organic": 3}
    assert rows["SpermFuel+"]["orders"] == 2              # the upsell bottle isn't a sale it was bought in
    # The upsell joins the order after the tracker saved it: the backend's re-read of the order counts.
    db.run("DELETE FROM orders")
    _order(95010, units=3)
    import backend
    late = {**upsell(95010, "CORE Swimmer Fuel", "SAVE 48%"), "current_total_price": "100", "financial_status": "paid"}
    _order(95010, units=3)                                   # the tracker's copy: no upsell line yet
    backend._store_order(late, time.time())
    assert _inv()[1]["Swimmer Fuel bags"]["from"]["ppu"] == 1
    db.run("DELETE FROM backend_orders")
    assert [s["key"] for s in stock.inventory()["sources"]] == ["ads", "mrr", "ppu", "thanks", "email", "organic"]
    assert stock._upsell(upsell(95020, "CORE Swimmer Fuel", "SAVE 50%"), upsell(95020, "CORE Swimmer Fuel", "SAVE 50%")["line_items"][1]) == "email"
    db.run("DELETE FROM orders")
