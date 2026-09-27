"""
Background loops: send queued orders, poll Shopify for new orders, reconcile
the last few days, and raise alerts when something drifts. Also builds the
health report shown at /report and through the MCP tools.
"""
import asyncio
import logging
import time
from typing import Any

import httpx

import config
import db
import tracking

log = logging.getLogger("tracker.worker")


async def _loop(name: str, interval: float, fn) -> None:
    while True:
        try:
            await fn()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s loop failed", name)
        await asyncio.sleep(interval)


async def _send_pending() -> None:
    await tracking.process_pending()


async def _poll() -> None:
    new = await tracking.poll_orders(max(600, config.POLL_INTERVAL_SECONDS * 5))
    db.kv_set("last_poll_ok", str(time.time()))
    if new:
        log.info("Poller queued %d order(s)", new)


async def _reconcile() -> None:
    """Every order in the lookback window must end up sent or deliberately skipped."""
    recovered = await tracking.poll_orders(config.RECONCILE_LOOKBACK_HOURS * 3600)
    db.kv_set("last_reconcile_ok", str(time.time()))
    if recovered:
        await alert(f"Reconciler found {recovered} order(s) the webhook and poller missed; "
                    "they are queued for sending.", key="recovered")
    report = build_report()
    for problem in report["problems"]:
        await alert(problem, key=problem[:40])


async def alert(message: str, key: str) -> None:
    """Log always; post to ALERT_WEBHOOK_URL at most once an hour per key."""
    log.warning("ALERT: %s", message)
    if not config.ALERT_WEBHOOK_URL:
        return
    last = float(db.kv_get(f"alert:{key}") or 0)
    if time.time() - last < 3600:
        return
    db.kv_set(f"alert:{key}", str(time.time()))
    text = f"[Meta tracker] {message}"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(config.ALERT_WEBHOOK_URL, json={"text": text, "content": text})
    except httpx.HTTPError as e:
        log.warning("alert delivery failed: %s", e)


def build_report() -> dict[str, Any]:
    now = time.time()
    day = now - 86400
    events = db.event_stats(day)
    orders = db.order_summary(now - 7 * 86400)
    last_pixel = db.last_pixel_seen()
    problems: list[str] = []

    missing = config.missing_required()
    if missing:
        problems.append("Missing settings: " + ", ".join(missing))
    if last_pixel is None:
        problems.append("The storefront pixel has never reported. Is the custom pixel installed "
                        "and connected in Shopify > Settings > Customer events?")
    elif now - last_pixel > config.PIXEL_SILENCE_ALERT_MINUTES * 60:
        problems.append(f"No pixel events for {int((now - last_pixel) / 60)} minutes.")
    last_poll = float(db.kv_get("last_poll_ok") or 0)
    if now - last_poll > max(900, config.POLL_INTERVAL_SECONDS * 10):
        problems.append("Shopify order polling hasn't succeeded recently; check the Shopify token.")
    if orders["failed"]:
        problems.append(f"{len(orders['failed'])} order(s) failed to reach Meta: "
                        + "; ".join(f"{o['order_name']}: {o['last_error']}" for o in orders["failed"][:3]))
    if orders["pending_over_30_min"]:
        problems.append(f"{len(orders['pending_over_30_min'])} order(s) waiting over 30 minutes.")
    failed_24h = sum(v.get("failed", 0) for v in events["by_event"].values())
    sent_24h = sum(v.get("sent", 0) for v in events["by_event"].values())
    if failed_24h and failed_24h > 0.05 * (failed_24h + sent_24h):
        problems.append(f"{failed_24h} of {failed_24h + sent_24h} events failed in the last 24h.")

    purchases = [r for r in events["purchase_match_keys"]]
    total_p = sum(r["n"] for r in purchases) or 0
    coverage: dict[str, str] = {}
    if total_p:
        for key in ("em", "ph", "fn", "ln", "zp", "country", "external_id",
                    "client_ip_address", "client_user_agent", "fbp", "fbc"):
            n = sum(r["n"] for r in purchases if key in (r["match_keys"] or "").split(","))
            coverage[key] = f"{round(100 * n / total_p)}%"

    return {
        "ok": not problems,
        "problems": problems,
        "pixel_last_seen_minutes_ago": None if last_pixel is None else int((now - last_pixel) / 60),
        "events_last_24h": events["by_event"],
        "purchase_match_key_coverage_24h": coverage,
        "orders_last_7d": orders,
        "config": {
            "pixel_id": config.META_PIXEL_ID,
            "store": config.SHOPIFY_STORE,
            "test_event_code_active": bool(config.META_TEST_EVENT_CODE),
            "renewal_event": config.RENEWAL_EVENT_NAME or "(renewals not sent)",
            "renewal_source_names": sorted(config.RENEWAL_SOURCE_NAMES),
        },
    }


def start() -> list[asyncio.Task]:
    loop = asyncio.get_running_loop()
    return [
        loop.create_task(_loop("send", 10, _send_pending)),
        loop.create_task(_loop("poll", config.POLL_INTERVAL_SECONDS, _poll)),
        loop.create_task(_loop("reconcile", config.RECONCILE_INTERVAL_SECONDS, _reconcile)),
    ]
