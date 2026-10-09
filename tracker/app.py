#!/usr/bin/env python3
"""
Meta tracker server.

  POST /collect            storefront custom pixel events (public, CORS)
  POST /webhooks/shopify   orders/create webhook (HMAC-verified)
  GET  /health             liveness for Railway
  GET  /report             tracking health report        (ADMIN_TOKEN)
  POST /admin/resend/{id}  force-resend one order         (ADMIN_TOKEN)
  GET  /admin/backup       a snapshot of the database     (ADMIN_TOKEN, Bearer header only)
  GET  /hub                the owner's dashboard          (ADMIN_TOKEN login; see hub.py)
  /mcp                     MCP server for Claude           (ADMIN_TOKEN)

ADMIN_TOKEN goes in an "Authorization: Bearer <token>" header, or ?key=<token>
for clients that can only set a URL (the Claude connector). The key is
scrubbed from the access log either way.
"""
import asyncio
import contextlib
import datetime as dt
import hmac
import ipaddress
import json
import logging
import os
import re
import tempfile
import time
from typing import Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route

import config
import database
import db
import hub
import meta_capi
import shopify
import tracking
import worker

logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# httpx logs every request URL at INFO. Some URLs carry secrets (the alert
# webhook, older Graph links), so only its warnings reach the log.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
log = logging.getLogger("tracker")


class RedactKey(logging.Filter):
    """Keep ?key=... out of uvicorn's access log (and Railway's log store)."""
    _pat = re.compile(r"([?&]key=)[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self._pat.sub(r"\1***", a) if isinstance(a, str) else a
                                for a in record.args)
        if isinstance(record.msg, str):
            record.msg = self._pat.sub(r"\1***", record.msg)
        return True


logging.getLogger("uvicorn.access").addFilter(RedactKey())

MAX_COLLECT_BYTES = 16_384
MAX_WEBHOOK_BYTES = 2_000_000          # orders/create payloads are well under 1 MB
RATE_LIMIT_PER_MIN = 240
CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
}


# --- auth / helpers -----------------------------------------------------------

def _token_from(headers, query) -> str:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return query.get("key", "")


def _authorized(headers, query) -> bool:
    token = _token_from(headers, query)
    return bool(config.ADMIN_TOKEN) and hmac.compare_digest(token, config.ADMIN_TOKEN)


def _client_ip(request: Request) -> str:
    """The address our trusted proxy saw. Proxies append to X-Forwarded-For,
    so the caller controls the leftmost entries; take the one TRUSTED_PROXY_HOPS
    from the right and insist it is an IP address."""
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    if hops:
        cand = hops[-config.TRUSTED_PROXY_HOPS] if len(hops) >= config.TRUSTED_PROXY_HOPS else hops[0]
    else:
        cand = request.client.host if request.client else ""
    try:
        return str(ipaddress.ip_address(cand))
    except ValueError:
        return ""


_hits: dict[str, list[float]] = {}


def _rate_limited(ip: str) -> bool:
    now = time.time()
    window = _hits.setdefault(ip, [now, 0])
    if now - window[0] > 60:
        window[0], window[1] = now, 0
    window[1] += 1
    if len(_hits) > 50_000:
        _hits.clear()
    return window[1] > RATE_LIMIT_PER_MIN


class BodyTooLarge(Exception):
    pass


async def _read_body(request: Request, limit: int) -> bytes:
    """Read the body without ever buffering more than `limit` bytes."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise BodyTooLarge()
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise BodyTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


# --- routes -----------------------------------------------------------------

async def health(request: Request) -> Response:
    # Which commit is live (Railway sets it for GitHub deploys), to check a deploy landed.
    return JSONResponse({"status": "ok", "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:7]})


async def collect(request: Request) -> Response:
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=CORS)
    ip = _client_ip(request)
    if _rate_limited(ip or "unknown"):
        return Response(status_code=429, headers=CORS)
    try:
        body = await _read_body(request, MAX_COLLECT_BYTES)
    except BodyTooLarge:
        return Response(status_code=413, headers=CORS)
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        event = tracking.ingest_pixel_event(payload, ip, request.headers.get("user-agent", ""))
    except (ValueError, TypeError, AttributeError) as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=400, headers=CORS)
    cid = tracking.pixel_client_id(payload)
    if event:
        tracking.fire_and_forget(tracking.send_pixel_event(event, cid))
    if tracking.carries_contact(payload.get("name")):
        tracking.fire_and_forget(tracking.release_held(cid))
    return Response(status_code=204, headers=CORS)


async def quiz(request: Request) -> Response:
    """One step of a quiz taker (the quiz page posts them): stored for the Database tab."""
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=CORS)
    ip = _client_ip(request)
    if _rate_limited(ip or "unknown"):
        return Response(status_code=429, headers=CORS)
    try:
        body = await _read_body(request, MAX_COLLECT_BYTES)
    except BodyTooLarge:
        return Response(status_code=413, headers=CORS)
    try:
        payload = json.loads(body)
        database.record_quiz(payload, request.headers.get("user-agent", ""), time.time())
    except (ValueError, TypeError) as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=400, headers=CORS)
    return Response(status_code=204, headers=CORS)


async def shopify_webhook(request: Request) -> Response:
    try:
        body = await _read_body(request, MAX_WEBHOOK_BYTES)
    except BodyTooLarge:
        return Response(status_code=413)
    if not shopify.verify_webhook(body, request.headers.get("x-shopify-hmac-sha256", "")):
        return JSONResponse({"error": "invalid signature"}, status_code=401)
    topic = request.headers.get("x-shopify-topic", "")
    if topic == "orders/create":
        try:
            order = json.loads(body)
            if not isinstance(order, dict) or "id" not in order:
                raise ValueError("not an order")
        except ValueError:
            return JSONResponse({"error": "bad json"}, status_code=400)
        if tracking.ingest_order(order):
            log.info("Webhook queued order %s", order.get("name"))
    return Response(status_code=200)


async def report(request: Request) -> Response:
    if not _authorized(request.headers, request.query_params):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(worker.build_report())


async def resend(request: Request) -> Response:
    if not _authorized(request.headers, request.query_params):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(await tracking.resend_order(request.path_params["order_id"]))


def _bearer_only(headers) -> bool:
    """ADMIN_TOKEN in an Authorization header: not ?key= (URLs end up in
    browser history and logs) and not the hub's cookie."""
    auth = headers.get("authorization", "")
    return (bool(config.ADMIN_TOKEN) and auth.lower().startswith("bearer ")
            and hmac.compare_digest(auth[7:].strip().encode(), config.ADMIN_TOKEN.encode()))


async def backup(request: Request) -> Response:
    """The whole database as one consistent SQLite file, to keep off the volume."""
    if not _bearer_only(request.headers):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    fd, path = tempfile.mkstemp(prefix="tracker-backup-", suffix=".db")
    os.close(fd)
    try:
        await asyncio.to_thread(db.backup_to, path)
    except Exception:
        os.remove(path)
        log.exception("backup failed")
        return JSONResponse({"error": "The backup couldn't be made. The details are in the server log."},
                            status_code=500)
    name = dt.datetime.now(config.store_tz()).strftime("tracker-%Y%m%d-%H%M.db")
    log.info("Database backup downloaded (%d bytes)", os.path.getsize(path))
    return FileResponse(path, media_type="application/octet-stream", filename=name,
                        headers={"Cache-Control": "no-store"}, background=BackgroundTask(os.remove, path))


# --- MCP tools ----------------------------------------------------------------

mcp = FastMCP("meta_tracker", host="0.0.0.0", stateless_http=True, json_response=True)


def _j(data) -> str:
    return json.dumps(data, indent=2, default=str)


@mcp.tool(name="tracker_status", annotations={"readOnlyHint": True})
async def tracker_status() -> str:
    """Health report: problems found, mode (test/live), pixel activity, events sent/failed
    in the last 24h, Purchase match-key coverage, and order delivery status for 7 days."""
    return _j(worker.build_report())


@mcp.tool(name="tracker_recent_events", annotations={"readOnlyHint": True})
async def tracker_recent_events(limit: int = 20, event_name: Optional[str] = None,
                                failed_only: bool = False) -> str:
    """Most recent events sent to Meta (newest first), optionally only failures or one event name."""
    rows = db.recent_events(limit=max(1, min(limit, 200)), event_name=event_name,
                            status="failed" if failed_only else None)
    return _j(rows)


@mcp.tool(name="tracker_order", annotations={"readOnlyHint": True})
async def tracker_order(order_id: str) -> str:
    """Delivery status of one Shopify order (numeric id) and the Meta events sent for it."""
    oid = shopify.numeric_id(order_id)
    row = db.get_order(oid)
    if not row:
        return _j({"order_id": oid, "status": "not seen yet"})
    row.pop("order_json", None)
    return _j({"order": row, "events": db.events_for_order(oid)})


@mcp.tool(name="tracker_resend_order", annotations={"readOnlyHint": False})
async def tracker_resend_order(order_id: str) -> str:
    """Re-fetch an order from Shopify and send it to Meta now, even if it was sent before
    (same event_id, so Meta dedupes) or was skipped as older than the tracking start.
    An order placed before go-live was already sent by WeTracked under its own event id:
    it is still sent, but the reply carries a `warning` that Meta will count it twice."""
    return _j(await tracking.resend_order(order_id))


@mcp.tool(name="tracker_send_test_event", annotations={"readOnlyHint": False})
async def tracker_send_test_event(test_event_code: str, pixel_id: Optional[str] = None) -> str:
    """Send one PageView tagged with a Test Events code from Events Manager > Test events,
    to confirm the token and pixel work. It shows only in Test Events, not in ads reporting.
    pixel_id picks a backup pixel; the main dataset is used when it is omitted."""
    return _j(await tracking.send_test_event(test_event_code, pixel_id))


class RequireAdmin:
    """Gate the MCP endpoint behind ADMIN_TOKEN."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope)
            if not _authorized(request.headers, request.query_params):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


# --- app ----------------------------------------------------------------------

@contextlib.asynccontextmanager
async def lifespan(app):
    db.init()
    started = tracking.tracking_start()
    log.info("Mode: %s. Reporting orders created after %s",
             "TEST (Events Manager > Test events only)" if config.META_TEST_EVENT_CODE else "LIVE",
             time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(started)))
    for pixel in config.EXTRA_PIXELS:
        log.info("Backup pixel %s: gets every event, and orders created after %s", pixel["pixel_id"],
                 time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(tracking.pixel_start(pixel["pixel_id"]))))
    for problem in config.EXTRA_PIXEL_PROBLEMS:
        log.error(problem)
    missing = config.missing_required()
    if missing:
        log.error("Missing settings: %s", ", ".join(missing))
    storage = config.data_dir_problem()
    if storage:
        log.error(storage)
    tasks = worker.start() if not missing else []
    if config.PUBLIC_URL and config.SHOPIFY_WEBHOOK_SECRET and not missing:
        try:
            result = await shopify.ensure_order_webhook(f"{config.PUBLIC_URL}/webhooks/shopify")
            log.info("orders/create webhook: %s", result)
        except Exception as e:
            log.warning("Could not register webhook (poller still covers orders): %s", e)
    async with mcp.session_manager.run():
        yield
    for t in tasks:
        t.cancel()


def create_app() -> Starlette:
    mcp_app = mcp.streamable_http_app()
    starlette_app = Starlette(
        routes=[
            Route("/", health),
            Route("/health", health),
            Route("/collect", collect, methods=["POST", "OPTIONS"]),
            Route("/quiz", quiz, methods=["POST", "OPTIONS"]),
            Route("/webhooks/shopify", shopify_webhook, methods=["POST"]),
            Route("/report", report),
            Route("/admin/resend/{order_id}", resend, methods=["POST"]),
            Route("/admin/backup", backup, methods=["GET"]),
            *hub.routes,
            Mount("/", app=RequireAdmin(mcp_app)),
        ],
        lifespan=lifespan,
    )
    # The hub's login throttle needs the real caller, found the same way as for /collect.
    starlette_app.state.client_ip = _client_ip
    return starlette_app


app = create_app()

if __name__ == "__main__":
    # proxy_headers with the default trusted list keeps request.client honest;
    # _client_ip does its own X-Forwarded-For parsing.
    uvicorn.run(app, host="0.0.0.0", port=config.PORT, proxy_headers=True)
