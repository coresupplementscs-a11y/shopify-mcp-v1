"""
The Backend tab's stock and subscriptions, read from the orders the tracker keeps
(db.orders: every order Shopify sent, kept ORDER_RETENTION_DAYS).

Inventory: teemdrop doesn't report stock to Shopify (the products don't track
inventory there), so stock goes off orders: the owner types how many units are on
hand for a product, once, and every unit ordered after that comes off it (new sales
and MRR alike, both ship; cancelled and test orders don't). Bottles per day over the
last 7 days give the days of stock left.

Take rate: of the new sales of a day, how many started a subscription (Kaching
tags the first order "Kaching Subscription First Order"), per day and in total,
and by bundle size.
"""
from __future__ import annotations

import datetime as dt
import json
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
    if not name:
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


def _units(order: dict) -> dict[str, int]:
    """Units per product title in one order: every line that ships, free units of a product included."""
    out: dict[str, int] = {}
    for i in order.get("line_items") or []:
        if not isinstance(i, dict) or i.get("requires_shipping") is False or i.get("gift_card"):
            continue
        title = str(i.get("title") or i.get("name") or "").strip()[:200]
        try:
            qty = int(i.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        if title and qty > 0:
            out[title] = out.get(title, 0) + qty
    return out


def inventory(now: Optional[float] = None) -> dict:
    now = now or time.time()
    today0 = dt.datetime.combine(dt.datetime.fromtimestamp(now, config.store_tz()).date(), dt.time(),
                                 tzinfo=config.store_tz()).timestamp()
    have = counts()
    since = min([now - 30 * 86400] + [float(c.get("at") or now) for c in have.values()])
    per: dict[str, dict] = {}
    for x in _orders(since):
        o = x["order"]
        if o.get("cancelled_at"):
            continue
        for title, n in _units(o).items():
            p = per.setdefault(title, {"product": title, "today": 0, "d7": 0, "d30": 0, "since_count": 0})
            if x["created"] >= today0:
                p["today"] += n
            if x["created"] >= now - 7 * 86400:
                p["d7"] += n
            if x["created"] >= now - 30 * 86400:
                p["d30"] += n
            c = have.get(title)
            if c and x["created"] >= float(c.get("at") or now):
                p["since_count"] += n
    for title in have:
        per.setdefault(title, {"product": title, "today": 0, "d7": 0, "d30": 0, "since_count": 0})
    rows = []
    for p in per.values():
        c = have.get(p["product"])
        per_day = round(p["d7"] / 7, 1)
        on_hand = (int(c["count"]) - p["since_count"]) if c else None
        days_left = round(on_hand / per_day, 1) if on_hand is not None and per_day > 0 else None
        rows.append({**p, "per_day": per_day, "count": int(c["count"]) if c else None,
                     "count_at": float(c["at"]) if c else None, "on_hand": on_hand, "days_left": days_left,
                     "low": on_hand is not None and (on_hand <= 0 or (days_left is not None and days_left < LOW_DAYS))})
    rows.sort(key=lambda r: (r["count"] is None, -r["d30"], r["product"].lower()))
    return {"rows": rows, "low_days": LOW_DAYS,
            "note": "teemdrop doesn't send stock to Shopify, so this goes off orders: type what's on hand once, "
                    "every unit ordered after that comes off it."}


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
    """New sales in [start, end) per day: how many started a subscription."""
    days: dict[str, dict] = {}
    bundles: dict[str, dict] = {}
    for x in _orders(start):
        if x["created"] >= end or x["kind"] != "purchase" or x["order"].get("cancelled_at"):
            continue
        o = x["order"]
        sub = SUB_TAG in _tags(o)
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
