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


def test_stock_goes_off_orders_from_the_count_typed_in_and_says_days_left():
    db.run("DELETE FROM orders")
    db.kv_set(stock.COUNTS_KEY, "{}")
    _order(91001, units=3, hours_ago=50)                 # before the count: doesn't come off it
    stock.set_count("SpermFuel+", 100, now=time.time() - 30 * 3600)
    _order(91002, units=5, hours_ago=20)
    _order(91003, units=3, hours_ago=1)
    _order(91004, units=5, hours_ago=2, cancelled=True)  # cancelled: never shipped
    inv = {r["product"]: r for r in stock.inventory()["rows"]}
    sf = inv["SpermFuel+"]
    assert sf["count"] == 100 and sf["on_hand"] == 92 and sf["d7"] == 11 and sf["sold"] == 11
    assert sf["pace"] == round(11 / 7, 1) and sf["days_left"] == round(92 / (11 / 7), 1) and not sf["low"]
    assert list(inv) == ["SpermFuel+", "Core Swimmer Fuel", "REBUILD 9", "NRTURE COAT & PROTECT"]   # only these, in order
    assert stock.inventory(days=1.5 / 24)["rows"][0]["sold"] == 3   # the tab's range: the last 90 minutes
    stock.set_count("SpermFuel+", 10)
    assert {r["product"]: r for r in stock.inventory()["rows"]}["SpermFuel+"]["low"] is True
    stock.set_count("SpermFuel+", "")                     # blank clears it
    assert {r["product"]: r for r in stock.inventory()["rows"]}["SpermFuel+"]["count"] is None
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


def test_swimmer_fuel_is_one_pile_of_sticks_bags_and_loose_packs_both_come_off_it():
    # Oct 23 2026: a bag is 30 sticks; loose sticks sell as Testosterone Core Fuel Pack x1 / x2 / x4.
    db.run("DELETE FROM orders")
    db.kv_set(stock.COUNTS_KEY, "{}")
    stock.set_count("Core Swimmer Fuel", 10 * 30, now=time.time() - 10 * 3600)
    _order(94001, units=3, variant="3", title="CORE Swimmer Fuel", hours_ago=5)                          # 3 bags
    _order(94002, units=1, variant="Buy 1 Get 1 Free · 60 Stick Packs", title="CORE Swimmer Fuel", hours_ago=4)  # 2 bags
    _order(94003, units=1, title="Testosterone Core Fuel Pack x4", hours_ago=3)                           # 4 sticks
    _order(94004, units=2, title="Testosterone Core Fuel Pack", hours_ago=2)                              # 2 sticks
    sw = {r["product"]: r for r in stock.inventory()["rows"]}["Core Swimmer Fuel"]
    assert (sw["bags"], sw["sticks"], sw["sold"]) == (5, 6, 156) and sw["unit"] == "sticks" and sw["per_bag"] == 30
    assert sw["on_hand"] == 300 - 156
    db.run("DELETE FROM orders")
    db.kv_set(stock.COUNTS_KEY, "{}")
