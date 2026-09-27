#!/usr/bin/env python3
"""
Meta tracker server.

  POST /collect            storefront custom pixel events (public, CORS)
  POST /webhooks/shopify   orders/create webhook (HMAC-verified)
  GET  /health             liveness for Railway
  GET  /report             tracking health report        (ADMIN_TOKEN)
  POST /admin/resend/{id}  force-resend one order         (ADMIN_TOKEN)
  /mcp                     MCP server for Claude           (ADMIN_TOKEN)

ADMIN_TOKEN goes in an "Authorization: Bearer <token>" header or ?key=<token>.
"""
import contextlib
import hmac
import json
import logging
import time
from typing import Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

import config
import db
import meta_capi
import shopify
import tracking
import worker

logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("tracker")

MAX_COLLECT_BYTES = 16_384
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
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


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


# --- routes -----------------------------------------------------------------

async def health(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


async def collect(request: Request) -> Response:
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=CORS)
    ip = _client_ip(request)
    if _rate_limited(ip):
        return Response(status_code=429, headers=CORS)
    body = await request.body()
    if len(body) > MAX_COLLECT_BYTES:
        return Response(status_code=413, headers=CORS)
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        event = tracking.ingest_pixel_event(payload, ip, request.headers.get("user-agent", ""))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400, headers=CORS)
    if event:
        tracking.fire_and_forget(tracking.send_pixel_event(event))
    return Response(status_code=204, headers=CORS)


async def shopify_webhook(request: Request) -> Response:
    body = await request.body()
    if not shopify.verify_webhook(body, request.headers.get("x-shopify-hmac-sha256", "")):
        return JSONResponse({"error": "invalid signature"}, status_code=401)
    topic = request.headers.get("x-shopify-topic", "")
    if topic == "orders/create":
        try:
            order = json.loads(body)
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
    return JSONResponse(await resend_order(request.path_params["order_id"]))


async def resend_order(order_id: str) -> dict:
    oid = shopify.numeric_id(order_id)
    if not oid:
        return {"error": "order_id must be the numeric Shopify order id"}
    try:
        order = await shopify.get_order(oid)
    except Exception as e:
        return {"order_id": oid, "error": f"Could not load the order from Shopify: {e}"}
    db.upsert_order(order)
    db.reset_order(oid)
    row = db.get_order(oid)
    row["order_json"] = order
    status = await tracking.process_order(row, force=True, source="manual")
    return {"order_id": oid, "order_name": order.get("name"), "status": status,
            "order": {k: v for k, v in (db.get_order(oid) or {}).items() if k != "order_json"}}


# --- MCP tools ----------------------------------------------------------------

mcp = FastMCP("meta_tracker", host="0.0.0.0", stateless_http=True, json_response=True)


def _j(data) -> str:
    return json.dumps(data, indent=2, default=str)


@mcp.tool(name="tracker_status", annotations={"readOnlyHint": True})
async def tracker_status() -> str:
    """Health report: problems found, pixel activity, events sent/failed in the last 24h,
    Purchase match-key coverage, and order delivery status for the last 7 days."""
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
    events = [e for e in db.recent_events(limit=500) if e.get("order_id") == oid]
    return _j({"order": row, "events": events})


@mcp.tool(name="tracker_resend_order", annotations={"readOnlyHint": False})
async def tracker_resend_order(order_id: str) -> str:
    """Re-fetch an order from Shopify and send it to Meta now (same event_id, so Meta dedupes)."""
    return _j(await resend_order(order_id))


@mcp.tool(name="tracker_send_test_event", annotations={"readOnlyHint": False})
async def tracker_send_test_event(test_event_code: str) -> str:
    """Send one PageView tagged with a Test Events code from Events Manager > Test events,
    to confirm the token and pixel work. It shows only in Test Events, not in ads reporting."""
    event = {
        "event_name": "PageView",
        "event_time": int(time.time()),
        "event_id": f"test_{int(time.time() * 1000)}",
        "action_source": "website",
        "event_source_url": config.STORE_URL or f"https://{config.SHOPIFY_STORE}.myshopify.com",
        "user_data": meta_capi.build_user_data(ip="127.0.0.1", user_agent="meta-tracker-test"),
    }
    try:
        trace = await meta_capi.send_event(event, source="test", test_event_code=test_event_code,
                                           attempts=1)
        return _j({"ok": True, "fbtrace_id": trace,
                   "next": "Check Events Manager > Test events for a PageView from this server."})
    except meta_capi.MetaError as e:
        return _j({"ok": False, "error": str(e)})


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
    log.info("Reporting orders created after %s", time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(started)))
    missing = config.missing_required()
    if missing:
        log.error("Missing settings: %s", ", ".join(missing))
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
    return Starlette(
        routes=[
            Route("/", health),
            Route("/health", health),
            Route("/collect", collect, methods=["POST", "OPTIONS"]),
            Route("/webhooks/shopify", shopify_webhook, methods=["POST"]),
            Route("/report", report),
            Route("/admin/resend/{order_id}", resend, methods=["POST"]),
            Mount("/", app=RequireAdmin(mcp_app)),
        ],
        lifespan=lifespan,
    )


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.PORT, proxy_headers=True,
                forwarded_allow_ips="*")
