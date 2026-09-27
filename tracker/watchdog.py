"""
The watchdog: re-checks every link of the tracking chain every few minutes
and records the verdict, so the hub can say "all good" with proof.

Each check is ok, warn (worth a look, nothing lost) or fail (data at risk).
"""
import datetime as dt
import json
import logging
import math
import time
from typing import Any, Optional

import config
import db
import meta_ads
import meta_capi
import shopify
import tracking

log = logging.getLogger("tracker.watchdog")

RANK = {"ok": 0, "warn": 1, "fail": 2}
EMQ_REFRESH_SECONDS = 6 * 3600
# webhook_good: the last answer Shopify actually gave, kept through a failed re-check.
_state = {"emq_at": 0.0, "webhook_at": 0.0, "webhook": None, "webhook_good": None}


def ago(ts: Optional[float]) -> str:
    if not ts:
        return "never"
    s = max(0, int(time.time() - ts))
    if s < 90:
        return "just now"
    if s < 5400:
        return f"{s // 60} min ago"
    if s < 172800:
        return f"{s // 3600} h ago"
    return f"{s // 86400} days ago"


def _c(cid: str, name: str, status: str, detail: str) -> dict:
    return {"id": cid, "name": name, "status": status, "detail": detail}


def worst(checks: list[dict]) -> str:
    return max((c["status"] for c in checks), key=lambda s: RANK[s], default="ok")


def pixel_label(pixel_id: str) -> str:
    name = db.kv_get(f"pixel_name:{pixel_id}") or ""
    if pixel_id == config.META_PIXEL_ID:
        return f"{name or 'Main pixel'} (main)"
    return f"{name or pixel_id} (backup)"


def _score(entry: Any) -> Optional[float]:
    s = entry.get("score") if isinstance(entry, dict) else None
    ok = isinstance(s, (int, float)) and not isinstance(s, bool) and math.isfinite(s)
    return float(s) if ok else None


def emq_event(scores: Any) -> Optional[str]:
    """The event whose match quality speaks for a pixel: Purchase once Meta
    scores it, else the best-scored event, else None (nothing scored yet).
    The hub's quality card and this watchdog check both use it, so they agree."""
    scored = {ev: _score(v) for ev, v in (scores.items() if isinstance(scores, dict) else [])}
    scored = {ev: v for ev, v in scored.items() if v is not None}
    if not scored:
        return None
    return "Purchase" if "Purchase" in scored else max(scored, key=lambda ev: scored[ev])


async def _webhook_check() -> dict:
    if not config.PUBLIC_URL:
        return _c("webhook", "Instant order notifications", "warn",
                  "PUBLIC_URL is not set; new orders arrive by polling within a minute instead.")
    if _state["webhook"] is None or time.time() - _state["webhook_at"] > 3600:
        address = f"{config.PUBLIC_URL}/webhooks/shopify"
        try:
            resp = await shopify._request("GET", "webhooks.json", params={"topic": "orders/create", "limit": 250})
            found = any(w.get("address") == address for w in resp.json().get("webhooks", []))
            _state["webhook"] = _c("webhook", "Instant order notifications", "ok" if found else "warn",
                                   "Shopify notifies the tracker the moment an order is placed." if found else
                                   "Shopify isn't notifying the tracker; orders still arrive by polling within a minute.")
            _state["webhook_good"] = _state["webhook"]
            _state["webhook_at"] = time.time()
        except Exception as e:
            # One network blip must not pin this at "warn" for an hour: keep the
            # last real answer, if there is one, and ask again on the next run.
            _state["webhook"] = _state["webhook_good"] or _c(
                "webhook", "Instant order notifications", "warn",
                f"Couldn't check with Shopify ({type(e).__name__}).")
            _state["webhook_at"] = time.time() - 3600 + config.WATCHDOG_INTERVAL_SECONDS
    return _state["webhook"]


async def _orders_check(now: float) -> dict:
    name = "Every order accounted for"
    try:
        since = dt.datetime.fromtimestamp(now - 86400, dt.timezone.utc).isoformat(timespec="seconds")
        orders = await shopify.list_orders_since(since)
    except Exception as e:
        return _c("orders", name, "warn", f"Couldn't list today's orders from Shopify ({type(e).__name__}).")
    start = float(db.kv_get("tracking_start") or 0)
    stored = db.orders_by_id([str(o["id"]) for o in orders])
    missing, failed, stuck = [], [], []
    counts = {"sent": 0, "skipped": 0, "pending": 0}
    for o in orders:
        created = tracking._parse_time(o.get("created_at")) or now
        row = stored.get(str(o["id"]))
        if row is None:
            if created >= start and now - created > 900:
                missing.append(o.get("name") or str(o["id"]))
            continue
        if row["status"] == "failed":
            failed.append(row["order_name"] or row["order_id"])
        elif row["status"] == "pending" and now - row["received_at"] > 1800:
            stuck.append(row["order_name"] or row["order_id"])
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    if missing or failed or stuck:
        parts = []
        if missing:
            parts.append(f"not picked up: {', '.join(missing[:5])}")
        if failed:
            parts.append(f"failed to reach Meta (retrying): {', '.join(failed[:5])}")
        if stuck:
            parts.append(f"waiting over 30 min: {', '.join(stuck[:5])}")
        return _c("orders", name, "fail", "; ".join(parts))
    if not orders:
        return _c("orders", name, "ok", "No orders in the last 24 hours.")
    return _c("orders", name, "ok",
              f"{len(orders)} orders in 24 h: {counts['sent']} sent to Meta, {counts['skipped']} skipped on purpose"
              + (f", {counts['pending']} being processed" if counts.get("pending") else "") + ".")


def _delivery_check(pixel: dict, now: float) -> dict:
    pid = pixel["pixel_id"]
    stats = db.event_stats(now - 86400, pid)["by_event"]
    sent = sum(v.get("sent", 0) for v in stats.values())
    failed = sum(v.get("failed", 0) for v in stats.values())
    label = pixel_label(pid)
    last = db.last_sent_at(pid)
    if failed and failed > 0.05 * (failed + sent):
        return _c(f"pixel:{pid}", label, "fail",
                  f"{failed} of {failed + sent} events were rejected in 24 h. Check this pixel's access token.")
    if failed:
        return _c(f"pixel:{pid}", label, "warn", f"{sent} events accepted, {failed} rejected in 24 h.")
    if not sent:
        return _c(f"pixel:{pid}", label, "warn", "Nothing sent in the last 24 hours.")
    return _c(f"pixel:{pid}", label, "ok", f"{sent} events accepted by Meta in 24 h, last {ago(last)}.")


def _emq_check(pixel: dict) -> Optional[dict]:
    pid = pixel["pixel_id"]
    raw = db.kv_get(f"emq:{pid}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    scores = data.get("scores") or {}
    event = emq_event(scores)
    if event is None:
        return _c(f"emq:{pid}", f"Match quality · {pixel_label(pid)}", "ok",
                  "Meta hasn't scored this pixel yet.")
    score = _score(scores[event])
    status = "ok" if score >= 7 else "warn" if score >= 5 else "fail"
    return _c(f"emq:{pid}", f"Match quality · {pixel_label(pid)}", status,
              f"{event} scored {score:.1f}/10 by Meta ({ago(data.get('taken_at'))}).")


async def run_checks() -> list[dict]:
    now = time.time()
    checks = []

    problem = config.data_dir_problem()
    checks.append(_c("storage", "Storage", "fail" if problem else "ok",
                     problem or "History is saved on the Railway volume and survives restarts."))

    missing = config.missing_required()
    settings = missing + config.EXTRA_PIXEL_PROBLEMS
    checks.append(_c("settings", "Settings", "fail" if missing else "warn" if settings else "ok",
                     "; ".join(settings) if settings else
                     ("Live: sending to Meta for real." if not config.META_TEST_EVENT_CODE
                      else "Test mode: events only show in Events Manager > Test events.")))

    last = db.last_pixel_seen()
    if last is None:
        checks.append(_c("pixel", "Storefront pixel", "fail", "The storefront has never reported a visit."))
    else:
        age = now - last
        status = "ok" if age <= 2 * 3600 else "warn" if age <= 6 * 3600 else "fail"
        checks.append(_c("pixel", "Storefront pixel", status, f"Last shopper activity {ago(last)}."))

    last_poll = float(db.kv_get("last_poll_ok") or 0)
    checks.append(_c("shopify", "Shopify connection", "ok" if now - last_poll <= 900 else "fail",
                     f"Order list read {ago(last_poll)}." if last_poll else "Shopify has never answered."))

    checks.append(await _webhook_check())
    checks.append(await _orders_check(now))

    for pixel in meta_capi.destinations():
        checks.append(_delivery_check(pixel, now))

    bad = db.renewal_orders_sent_as_purchase(now - 7 * 86400)
    checks.append(_c("renewals", "Rebills kept out of sales", "fail" if bad else "ok",
                     f"Sent as Purchase by mistake: {', '.join(bad[:5])}" if bad else
                     "Subscription rebills go to Meta as SubscriptionRenewal, never as Purchase."))

    keys = db.purchase_match_keys(now - 7 * 86400)
    if not keys:
        checks.append(_c("details", "Customer details on sales", "ok", "No live sale in the last 7 days yet."))
    else:
        pct = {k: round(100 * sum(1 for m in keys if k in m.split(",")) / len(keys))
               for k in ("em", "ph", "client_ip_address", "client_user_agent", "fbc")}
        weak = [k for k in ("em", "client_ip_address", "client_user_agent") if pct[k] < 90]
        checks.append(_c("details", "Customer details on sales", "warn" if weak else "ok",
                         f"Of {len(keys)} sales in 7 days: email {pct['em']}%, phone {pct['ph']}%, "
                         f"IP {pct['client_ip_address']}%, browser {pct['client_user_agent']}%, "
                         f"ad click {pct['fbc']}%."))

    for pixel in meta_capi.destinations():
        emq = _emq_check(pixel)
        if emq:
            checks.append(emq)

    if config.META_AD_ACCOUNT_IDS:
        today = dt.datetime.now(config.store_tz()).date().isoformat()
        ins = await meta_ads.ad_insights(today, today)
        checks.append(_c("ads", "Ad spend connection", "ok" if ins["connected"] else "warn",
                         f"Reading spend and sales per ad from {len(config.META_AD_ACCOUNT_IDS)} ad account(s)."
                         if ins["connected"] else ins["error"]))
    else:
        checks.append(_c("ads", "Ad spend connection", "warn",
                         "Not connected yet: add META_ADS_TOKEN and META_AD_ACCOUNT_IDS to see spend and ROAS."))
    return checks


async def refresh_emq() -> None:
    """Snapshot Meta's match-quality scores and dataset names for every pixel."""
    for pixel in meta_capi.destinations():
        pid = pixel["pixel_id"]
        name = await meta_ads.dataset_name(pid, pixel["token"])
        if name:
            db.kv_set(f"pixel_name:{pid}", name)
        try:
            scores = await meta_ads.dataset_quality(pid, pixel["token"])
        except meta_ads.MetaReadError as e:
            log.info("match quality for %s unavailable: %s", pid, e)
            continue
        db.add_emq_snapshot(pid, {ev: v.get("score") for ev, v in scores.items()})
        db.kv_set(f"emq:{pid}", json.dumps({"scores": scores, "taken_at": time.time()}))


async def tick() -> dict:
    if time.time() - _state["emq_at"] > EMQ_REFRESH_SECONDS:
        _state["emq_at"] = time.time()
        await refresh_emq()
    checks = await run_checks()
    status = worst(checks)
    db.add_watchdog_run(status, checks)
    previous = db.kv_get("watchdog_status")
    db.kv_set("watchdog_status", status)
    if status == "fail" and previous != "fail":
        import worker                           # late import: worker starts this loop
        failing = "; ".join(f"{c['name']}: {c['detail']}" for c in checks if c["status"] == "fail")
        await worker.alert(f"Watchdog: {failing}", key="watchdog")
    return {"status": status, "checks": checks}
