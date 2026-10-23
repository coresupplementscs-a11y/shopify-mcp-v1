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

import attribution
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
        out.append({"kind": r.get("kind") or "", "created": created, "order": o, "attribution": r.get("attribution")})
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
# Where the units came from (Oct 23 2026): ads first, organic last. A line Kaching's upsell added
# after checkout (order tagged "Kaching Upsell", line outside the bundle, discount "SAVE n%") is
# told apart by its offer's discount: the post-purchase page's Offer #1 is SAVE 48%, the thank-you
# page's next bottle of SpermFuel+ SAVE 30%; any other offer is the one the order confirmation
# email sends buyers to (the Upsell Block on the order status page).
SOURCES = [("ads", "Ads"), ("mrr", "MRR"), ("ppu", "Post-purchase page"), ("thanks", "Thank-you page"),
           ("email", "Order confirmation email"), ("organic", "Organic")]
PPU_OFFER = "save 48%"
THANKS_OFFER = "save 30%"
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


def _upsell(order: dict, item: dict) -> str:
    """'ppu' or 'thanks' for a line Kaching's upsell added after checkout, else ''."""
    if "kaching upsell" not in _tags(order):
        return ""
    if any(isinstance(p, dict) and p.get("name") == "__kaching_bundles" for p in item.get("properties") or []):
        return ""
    apps = order.get("discount_applications") or []
    titles = set()
    for a in item.get("discount_allocations") or []:
        i = a.get("discount_application_index") if isinstance(a, dict) else None
        if isinstance(i, int) and 0 <= i < len(apps) and isinstance(apps[i], dict):
            titles.add(str(apps[i].get("title") or "").strip().lower())
    offers = {t for t in titles if re.fullmatch(r"save\s+\d+%", t)}
    if not offers:
        return ""
    return "ppu" if PPU_OFFER in offers else "thanks" if THANKS_OFFER in offers else "email"


def _source(x: dict) -> str:
    """Where an order's checkout lines came from: MRR, an ad (the tracker's stored decision, else the
    same resolver on the order), or organic."""
    if x["kind"] == "renewal":
        return "mrr"
    rec = x.get("attribution")
    if not isinstance(rec, dict):
        try:
            rec = attribution.resolve(x["order"])["attribution"]
        except Exception:                                       # noqa: BLE001 (a count, never worth failing over)
            rec = {}
    return "ads" if (rec or {}).get("meta") else "organic"


def stock_lines(order: dict) -> list[list]:
    """The watched lines of an order as [product, units, bag/stick, upsell, paid]: every line that ships,
    free units included. The backend keeps these from its re-reads of the order (backend_orders.
    stock_lines), since an upsell line joins the order after the tracker saved its copy."""
    out = []
    for i in order.get("line_items") or []:
        if not isinstance(i, dict) or i.get("gift_card"):
            continue
        name, n, how = _line(i)
        if name and n > 0:
            out.append([name, n, how, _upsell(order, i), tracking.line_paid(i)])
    return out


# The table's rows: one per thing sold, Swimmer Fuel split into bags and loose sachets (one stock).
ROWS = [("SpermFuel+", "SpermFuel+", ""), ("Swimmer Fuel bags", STICKS, "bag"), ("Swimmer Fuel sachets", STICKS, "stick"),
        ("REBUILD 9", "REBUILD 9", ""), ("NRTURE COAT & PROTECT", "NRTURE COAT & PROTECT", "")]


def inventory(days: float = 30.0, now: Optional[float] = None) -> dict:
    """The Backend tab's stock for its range (`days`). pools: each stock (what's left of the count
    typed in, days left and the reorder date at the last 7 days' pace). rows: each thing sold, with
    the sub take rate of the new sales it was bought in, units sold, revenue and where they sold."""
    now = now or time.time()
    days = max(float(days or 30), 1 / 24)
    have = counts()
    since = min([now - max(days, 7) * 86400] + [float(c.get("at") or now) for c in have.values()])
    fresh: dict[str, list] = {}
    cancelled, subbed = set(), {}
    for r in db.query("SELECT order_id, stock_lines, cancelled_at, subscribed FROM backend_orders WHERE created_at>=?",
                      (since - 86400,)):
        oid = str(r["order_id"])
        if r["cancelled_at"]:
            cancelled.add(oid)
        if r["subscribed"] is not None:
            subbed[oid] = bool(r["subscribed"])
        try:
            if r["stock_lines"]:
                fresh[oid] = json.loads(r["stock_lines"])
        except ValueError:
            pass
    pools = {name: {"d7": 0, "since_count": 0} for name, _ in PRODUCTS}
    label_of = {(pool, how): label for label, pool, how in ROWS}
    rows = {label: {"label": label, "pool": pool, "unit": "bags" if how == "bag" else "sachets" if how == "stick" else "bottles",
                    "sold": 0, "revenue": 0.0, "orders": 0, "subscribed": 0, "from": {k: 0 for k, _ in SOURCES}}
            for label, pool, how in ROWS}
    currency = ""
    for x in _orders(since):
        o = x["order"]
        oid = str(o.get("id"))
        if o.get("cancelled_at") or oid in cancelled:
            continue
        recent = x["created"] >= now - days * 86400
        src = _source(x) if recent else ""
        sub = SUB_TAG in _tags(o) or subbed.get(oid, False)
        lines = fresh.get(oid)
        counted = set()
        for ln in (lines if lines is not None else stock_lines(o)):
            name, n, how, up = ln[0], int(ln[1] or 0), ln[2], ln[3]
            paid = float(ln[4]) if len(ln) > 4 and ln[4] is not None else 0.0
            if name not in pools or n <= 0:
                continue
            p = pools[name]
            if x["created"] >= now - 7 * 86400:
                p["d7"] += n
            c = have.get(name)
            if c and x["created"] >= float(c.get("at") or now):
                p["since_count"] += n
            label = label_of.get((name, how))
            if not recent or not label:
                continue
            r = rows[label]
            units = n // BAG if how == "bag" else n
            r["sold"] += units
            r["revenue"] += paid
            r["from"][up or src] += units
            currency = currency or str(o.get("currency") or "")
            if x["kind"] == "purchase" and not up and paid > 0 and label not in counted:
                counted.add(label)                      # a new sale it was bought in (not a gift, not an upsell)
                r["orders"] += 1
                r["subscribed"] += sub
    out_pools = []
    for name, _ in PRODUCTS:
        p, c = pools[name], have.get(name)
        pace = p["d7"] / 7
        on_hand = (int(c["count"]) - p["since_count"]) if c else None
        days_left = round(on_hand / pace, 1) if on_hand is not None and pace > 0 else None
        out_pools.append({"product": name, "unit": "sachets" if name == STICKS else "bottles", "per_bag": BAG if name == STICKS else None,
                          "count": int(c["count"]) if c else None, "count_at": float(c["at"]) if c else None,
                          "on_hand": on_hand, "pace": round(pace, 1), "days_left": days_left,
                          "reorder_by": (now + max(0.0, days_left - LOW_DAYS) * 86400) if days_left is not None else None,
                          "low": on_hand is not None and (on_hand <= 0 or (days_left is not None and days_left < LOW_DAYS))})
    out_rows = []
    for label, _, _ in ROWS:
        r = rows[label]
        out_rows.append({**r, "revenue": round(r["revenue"], 2),
                         "take_rate": round(100 * r["subscribed"] / r["orders"], 1) if r["orders"] else None})
    return {"pools": out_pools, "rows": out_rows, "sources": [{"key": k, "label": label} for k, label in SOURCES],
            "low_days": LOW_DAYS, "currency": currency or "USD",
            "note": "teemdrop doesn't send stock to Shopify, so this goes off orders: type what's on hand once, every unit "
                    "shipped after that comes off it (new sales, MRR, upsells, free gifts). Stock left and the reorder date "
                    "are at the last 7 days' pace; reorder by leaves " + str(LOW_DAYS) + " days of stock. Swimmer Fuel bags "
                    "and sachets share one stock, counted in sachets (a bag is " + str(BAG) + ")."}


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
