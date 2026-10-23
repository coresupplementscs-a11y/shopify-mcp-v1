"""
The Backend tab's stock and subscriptions, read from the orders the tracker keeps
(db.orders: every order Shopify sent, kept ORDER_RETENTION_DAYS).

Inventory: teemdrop doesn't report stock to Shopify (the products don't track
inventory there), so stock goes off orders: the owner types how many units are on
hand for a product, once, and every unit ordered after that comes off it (new sales
and MRR alike, both ship; cancelled and test orders don't). Units per day over the
last 7 days give the days of stock left. Swimmer Fuel counts in sticks (a bag is 30).

Take rate: of the new sales of a day, how many started a subscription (Kaching
tags the first order "Kaching Subscription First Order"), per day and in total,
and by bundle size.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from typing import Any, Optional

import config
import db
import tracking

COUNTS_KEY = "inventory_counts"
LOW_DAYS = 14                     # under two weeks of stock left: reorder now
SUB_TAG = "kaching subscription first order"


def _tags(order: dict) -> set[str]:
    raw = order.get("tags")
    parts = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    return {str(t).strip().lower() for t in parts if str(t).strip()}


def _orders(since: float) -> list[dict]:
    """Real orders created since `since`: kind (purchase / renewal / ...), created time, the order."""
    out = []
    for r in db.orders_since(since - 2 * 86400, ("sent", "skipped", "pending", "failed")):
        o = r.get("order_json") or {}
        created = tracking._parse_time(o.get("created_at"))
        if created is None or created < since or o.get("test") or r.get("kind") == "test":
            continue
        out.append({"kind": r.get("kind") or "", "created": created, "order": o})
    return out


def _day(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, config.store_tz()).date().isoformat()


# --- inventory ----------------------------------------------------------------------

def counts() -> dict:
    try:
        got = json.loads(db.kv_get(COUNTS_KEY) or "{}")
    except ValueError:
        got = {}
    return got if isinstance(got, dict) else {}


def set_count(product: str, count: Any, now: Optional[float] = None) -> dict:
    """The units on hand for a product right now (typed in the Backend tab); blank clears it."""
    name = str(product or "").strip()[:200]
    if name not in [n for n, _ in PRODUCTS]:
        raise ValueError("Which product?")
    all_ = counts()
    if count in (None, ""):
        all_.pop(name, None)
    else:
        try:
            n = int(float(count))
        except (TypeError, ValueError):
            raise ValueError("Type a whole number of units.")
        if n < 0 or n > 10_000_000:
            raise ValueError("Type a whole number of units.")
        all_[name] = {"count": n, "at": now or time.time()}
    db.kv_set(COUNTS_KEY, json.dumps(all_))
    return all_


# The products whose stock is watched, in this order (Oct 23 2026, Marwan); every other product
# is left out. Each lists the order-line titles that are it (lower case, start of the title).
# Swimmer Fuel is one pile of stick packs: a bag is 30 sticks ("CORE Swimmer Fuel", the order
# quantity is bags; the "60 Stick Packs" deal is two bags), and loose sticks sell as
# "Testosterone Core Fuel Pack" / "x2" / "x4". Both come off the same sticks.
PRODUCTS = [
    ("SpermFuel+", ("spermfuel+",)),
    ("Core Swimmer Fuel", ("core swimmer fuel", "testosterone core fuel pack")),
    ("REBUILD 9", ("rebuild 9",)),
    ("NRTURE COAT & PROTECT", ("nrture coat & protect",)),
]
STICKS = "Core Swimmer Fuel"
BAG = 30                                    # sticks in a bag


def _line(item: dict) -> tuple[str, int, str]:
    """The watched product a line belongs to ('' when none), units it takes off stock (bottles, or
    sticks for Swimmer Fuel), and whether it sold as a bag or loose sticks."""
    t = str(item.get("title") or item.get("name") or "").strip().lower()
    v = str(item.get("variant_title") or "").strip().lower()
    try:
        qty = max(int(item.get("quantity") or 0), 0)
    except (TypeError, ValueError):
        qty = 0
    for name, prefixes in PRODUCTS:
        if not any(t.startswith(p) for p in prefixes):
            continue
        if name != STICKS:
            return name, qty, ""
        if t.startswith("core swimmer fuel"):
            m = re.search(r"(\d+)\s*stick", v)
            return name, qty * (int(m.group(1)) if m else BAG), "bag"
        m = re.search(r"\bx\s*(\d{1,3})$", t)
        return name, qty * (int(m.group(1)) if m else 1), "stick"
    return "", 0, ""


def _units(order: dict) -> dict[str, dict]:
    """Per watched product in one order: units off stock, and for Swimmer Fuel the sticks that went
    out in bags and loose. Every line that ships, free units included."""
    out: dict[str, dict] = {}
    for i in order.get("line_items") or []:
        if not isinstance(i, dict) or i.get("gift_card"):
            continue
        name, n, how = _line(i)
        if name and n > 0:
            u = out.setdefault(name, {"units": 0, "bag": 0, "stick": 0})
            u["units"] += n
            if how:
                u[how] += n
    return out


def inventory(days: float = 30.0, now: Optional[float] = None) -> dict:
    """The watched products: what sold in the last `days` (the Backend tab's range) and per day,
    what's left of the count typed in, and days left at the last 7 days' pace."""
    now = now or time.time()
    days = max(float(days or 30), 1 / 24)
    have = counts()
    since = min([now - max(days, 7) * 86400] + [float(c.get("at") or now) for c in have.values()])
    per = {name: {"product": name, "unit": "sticks" if name == STICKS else "bottles", "per_bag": BAG if name == STICKS else None,
                  "sold": 0, "bags": 0, "sticks": 0, "d7": 0, "since_count": 0} for name, _ in PRODUCTS}
    for x in _orders(since):
        o = x["order"]
        if o.get("cancelled_at"):
            continue
        for name, u in _units(o).items():
            p = per[name]
            if x["created"] >= now - days * 86400:
                p["sold"] += u["units"]
                p["bags"] += u["bag"] // BAG
                p["sticks"] += u["stick"]
            if x["created"] >= now - 7 * 86400:
                p["d7"] += u["units"]
            c = have.get(name)
            if c and x["created"] >= float(c.get("at") or now):
                p["since_count"] += u["units"]
    rows = []
    for name, _ in PRODUCTS:
        p, c = per[name], have.get(name)
        pace = p["d7"] / 7
        on_hand = (int(c["count"]) - p["since_count"]) if c else None
        days_left = round(on_hand / pace, 1) if on_hand is not None and pace > 0 else None
        rows.append({**p, "per_day": round(p["sold"] / max(days, 1), 1), "pace": round(pace, 1),
                     "count": int(c["count"]) if c else None, "count_at": float(c["at"]) if c else None,
                     "on_hand": on_hand, "days_left": days_left,
                     "low": on_hand is not None and (on_hand <= 0 or (days_left is not None and days_left < LOW_DAYS))})
    return {"rows": rows, "low_days": LOW_DAYS,
            "note": "teemdrop doesn't send stock to Shopify, so this goes off orders: type what's on hand once, every unit "
                    "shipped after that comes off it (new sales and MRR). Days left is at the last 7 days' pace."}


# --- subscription take rate -----------------------------------------------------------

def _bundle(order: dict) -> str:
    """The bundle size: units of the order's biggest paid product."""
    best, units = "", 0
    for i in order.get("line_items") or []:
        if isinstance(i, dict) and tracking.line_paid(i) > 0:
            try:
                q = int(i.get("quantity") or 0)
            except (TypeError, ValueError):
                q = 0
            if q > units:
                best, units = str(i.get("title") or ""), q
    return str(units) if best else ""


def take_rates(start: float, end: float) -> dict:
    """New sales in [start, end) per day: how many started a subscription. The tag comes from the
    backend's latest read of the order (Kaching adds it seconds after the sale), else the order's own copy."""
    days: dict[str, dict] = {}
    bundles: dict[str, dict] = {}
    fresh = {str(r["order_id"]): r["subscribed"] for r in db.query(
        "SELECT order_id, subscribed FROM backend_orders WHERE created_at>=? AND created_at<? AND subscribed IS NOT NULL",
        (start - 86400, end + 86400))}
    for x in _orders(start):
        if x["created"] >= end or x["kind"] != "purchase" or x["order"].get("cancelled_at"):
            continue
        o = x["order"]
        sub = SUB_TAG in _tags(o) or bool(fresh.get(str(o.get("id"))))
        d = days.setdefault(_day(x["created"]), {"day": _day(x["created"]), "orders": 0, "subscribed": 0})
        d["orders"] += 1
        d["subscribed"] += sub
        b = _bundle(o) or "?"
        bb = bundles.setdefault(b, {"bundle": b, "orders": 0, "subscribed": 0})
        bb["orders"] += 1
        bb["subscribed"] += sub
    rate = lambda r: round(100 * r["subscribed"] / r["orders"], 1) if r["orders"] else None   # noqa: E731
    rows = sorted(({**d, "rate": rate(d)} for d in days.values()), key=lambda d: d["day"], reverse=True)
    total = {"orders": sum(d["orders"] for d in rows), "subscribed": sum(d["subscribed"] for d in rows)}
    total["rate"] = rate(total)
    by_bundle = sorted(({**b, "rate": rate(b)} for b in bundles.values()),
                       key=lambda b: (b["bundle"] == "?", int(b["bundle"]) if b["bundle"].isdigit() else 0))
    return {"days": rows, "total": total, "bundles": by_bundle}
