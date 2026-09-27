"""
Background loops: send queued orders, poll Shopify for new orders, reconcile
the last few days, prune old rows, and raise alerts when something drifts.
Also builds the health report shown at /report and through the MCP tools.
"""
import asyncio
import logging
import time
from typing import Any

import httpx

import config
import db
import tracking
import watchdog

log = logging.getLogger("tracker.worker")

_booted_at = time.time()


async def _loop(name: str, interval: float, fn) -> None:
    while True:
        try:
            await fn()
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as e:       # a bad token every minute is one line, not a traceback
            log.error("%s loop: Shopify answered %s %s", name, e.response.status_code,
                      e.response.text[:200])
        except httpx.HTTPError as e:
            log.error("%s loop: Shopify request failed: %s", name, e)
        except Exception:
            log.exception("%s loop failed", name)
        await asyncio.sleep(interval)


async def _send_pending() -> None:
    await tracking.process_pending()


async def _poll() -> None:
    new = await tracking.poll_orders(max(600, config.POLL_INTERVAL_SECONDS * 5))
    db.kv_set("last_poll_ok", str(time.time()))
    if new:
        log.info("Poller queued %d order(s)", len(new))


async def _reconcile() -> None:
    """Every order in the lookback window must end up sent or deliberately skipped."""
    recovered = await tracking.poll_orders(config.RECONCILE_LOOKBACK_HOURS * 3600)
    db.kv_set("last_reconcile_ok", str(time.time()))
    # Orders the poller should already have caught (older than its window) are
    # the ones worth an alert; a freshly booted service has simply not polled yet.
    poll_window = max(600, config.POLL_INTERVAL_SECONDS * 5)
    missed = [o for o in recovered
              if time.time() - (tracking._parse_time(o.get("created_at")) or time.time()) > poll_window]
    if missed and time.time() - _booted_at > poll_window:
        await alert(f"Reconciler found {len(missed)} order(s) the webhook and poller missed; "
                    "they are queued for sending.", key="recovered")
    report = build_report()
    for problem in report["problems"]:
        await alert(problem, key=_alert_key(problem))


async def _retain() -> None:
    removed = db.prune(time.time(), config.SESSION_RETENTION_DAYS,
                       config.PIXEL_EVENT_RETENTION_DAYS, config.ORDER_RETENTION_DAYS)
    if any(removed.values()):
        log.info("Retention removed %s", removed)


def _alert_key(problem: str) -> str:
    """Stable key per problem type, so throttling works even as numbers change."""
    return "".join(ch for ch in problem[:40] if ch.isalpha() or ch == " ").strip()


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
    storage = config.data_dir_problem()
    if storage:
        problems.append(storage)
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

    problems.extend(config.EXTRA_PIXEL_PROBLEMS)
    extra_pixels = []
    for pixel in config.EXTRA_PIXELS:
        pid = pixel["pixel_id"]
        stats = db.event_stats(day, pid)["by_event"]
        sent = sum(v.get("sent", 0) for v in stats.values())
        failed = sum(v.get("failed", 0) for v in stats.values())
        if failed and failed > 0.05 * (failed + sent):
            problems.append(f"Backup pixel {pid}: {failed} of {failed + sent} events failed in the "
                            "last 24h. Check its access token.")
        extra_pixels.append({
            "pixel_id": pid,
            "mode": "test" if pixel.get("test_event_code") else "live",
            "receiving_orders_created_after": time.strftime(
                "%Y-%m-%d %H:%M:%S UTC", time.gmtime(tracking.pixel_start(pid))),
            "events_last_24h": stats,
        })

    purchases = list(events["purchase_match_keys"])
    total_p = sum(r["n"] for r in purchases)
    coverage: dict[str, str] = {}
    if total_p:
        for key in ("em", "ph", "fn", "ln", "zp", "country", "external_id",
                    "client_ip_address", "client_user_agent", "fbp", "fbc"):
            n = sum(r["n"] for r in purchases if key in (r["match_keys"] or "").split(","))
            coverage[key] = f"{round(100 * n / total_p)}%"

    tracking_start = db.kv_get("tracking_start")
    return {
        "ok": not problems,
        "problems": problems,
        "mode": "test (events go to Events Manager > Test events only)" if config.META_TEST_EVENT_CODE else "live",
        "reporting_orders_created_after": (
            time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(float(tracking_start))) if tracking_start else None),
        "pixel_last_seen_minutes_ago": None if last_pixel is None else int((now - last_pixel) / 60),
        "events_last_24h": events["by_event"],
        "purchase_match_key_coverage_24h": coverage,
        "orders_last_7d": orders,
        "backup_pixels": extra_pixels,
        "config": {
            "pixel_id": config.META_PIXEL_ID,
            "backup_pixel_ids": [p["pixel_id"] for p in config.EXTRA_PIXELS],
            "store": config.SHOPIFY_STORE,
            "renewal_event": config.RENEWAL_EVENT_NAME or "(renewals not sent)",
            "renewal_source_names": sorted(config.RENEWAL_SOURCE_NAMES),
            "skipped_source_names": sorted(config.SKIP_SOURCE_NAMES),
            "purchase_value_field": config.PURCHASE_VALUE_FIELD,
            "content_id_field": config.CONTENT_ID_FIELD,
        },
    }


def start() -> list[asyncio.Task]:
    loop = asyncio.get_running_loop()
    return [
        loop.create_task(_loop("send", 10, _send_pending)),
        loop.create_task(_loop("poll", config.POLL_INTERVAL_SECONDS, _poll)),
        loop.create_task(_loop("reconcile", config.RECONCILE_INTERVAL_SECONDS, _reconcile)),
        loop.create_task(_loop("retention", 3600, _retain)),
        loop.create_task(_loop("watchdog", config.WATCHDOG_INTERVAL_SECONDS, watchdog.tick)),
    ]
