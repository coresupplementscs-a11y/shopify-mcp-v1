"""The Backend tab's stock (off orders) and subscription take rate."""
import time

import db
import stock

db.init()


def _order(oid, units=3, sub=False, hours_ago=1.0, gift=True, cancelled=False, title="SpermFuel+"):
    created = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - hours_ago * 3600))
    lines = [{"product_id": 1, "title": title, "price": "23.50", "quantity": units, "requires_shipping": True,
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
    assert sf["count"] == 100 and sf["on_hand"] == 92 and sf["d7"] == 11 and sf["d30"] == 11
    assert sf["per_day"] == round(11 / 7, 1) and sf["days_left"] == round(92 / sf["per_day"], 1) and not sf["low"]
    assert "Swimmer Rebuild App" not in inv               # a digital gift doesn't ship
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
