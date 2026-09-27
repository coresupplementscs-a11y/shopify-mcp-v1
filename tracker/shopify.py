"""
Shopify Admin API access for the tracker: token handling (static token or
client credentials, same as server.py), order polling and webhook signatures.
"""
import asyncio
import base64
import hashlib
import hmac
import logging
import re
import time
from typing import Any, Optional

import httpx

import config

log = logging.getLogger("tracker.shopify")

ORDER_FIELDS = (
    "id,name,email,phone,created_at,processed_at,cancelled_at,test,source_name,"
    "financial_status,total_price,subtotal_price,currency,presentment_currency,"
    "checkout_token,cart_token,browser_ip,client_details,landing_site,referring_site,"
    "note_attributes,customer,billing_address,shipping_address,line_items"
)

_token = config.SHOPIFY_ACCESS_TOKEN
_token_expires = float("inf") if _token else 0.0
_token_lock = asyncio.Lock()
_client: Optional[httpx.AsyncClient] = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


def set_http_client(client: httpx.AsyncClient) -> None:
    global _client
    _client = client


def _base() -> str:
    return f"https://{config.SHOPIFY_STORE}.myshopify.com/admin/api/{config.SHOPIFY_API_VERSION}"


async def _get_token(force: bool = False) -> str:
    global _token, _token_expires
    if _token and not force and time.time() < _token_expires - 1800:
        return _token
    if not (config.SHOPIFY_CLIENT_ID and config.SHOPIFY_CLIENT_SECRET):
        if _token:
            return _token
        raise RuntimeError("No Shopify credentials configured")
    async with _token_lock:
        if _token and not force and time.time() < _token_expires - 1800:
            return _token
        resp = await _http().post(
            f"https://{config.SHOPIFY_STORE}.myshopify.com/admin/oauth/access_token",
            data={"grant_type": "client_credentials", "client_id": config.SHOPIFY_CLIENT_ID,
                  "client_secret": config.SHOPIFY_CLIENT_SECRET},
        )
        resp.raise_for_status()
        data = resp.json()
        _token = data["access_token"]
        _token_expires = time.time() + int(data.get("expires_in", 86399))
        log.info("Shopify token refreshed")
        return _token


async def _request(method: str, path: str, *, params: Optional[dict] = None,
                   json: Optional[dict] = None, url: Optional[str] = None) -> httpx.Response:
    force_refresh, refreshed = False, False
    for attempt in range(4):
        headers = {"X-Shopify-Access-Token": await _get_token(force=force_refresh)}
        force_refresh = False
        resp = await _http().request(method, url or f"{_base()}/{path}", params=params,
                                     json=json, headers=headers)
        if resp.status_code == 429 or resp.status_code >= 500:
            await asyncio.sleep(float(resp.headers.get("Retry-After", 2 ** attempt)))
            continue
        if resp.status_code == 401 and config.SHOPIFY_CLIENT_ID and not refreshed:
            force_refresh = refreshed = True
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp

_NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


async def list_orders_since(created_at_min: str) -> list[dict]:
    """All orders created at/after the ISO timestamp, following pagination."""
    orders: list[dict] = []
    resp = await _request("GET", "orders.json", params={
        "status": "any", "limit": 250, "created_at_min": created_at_min, "fields": ORDER_FIELDS})
    while True:
        orders.extend(resp.json().get("orders", []))
        m = _NEXT_LINK.search(resp.headers.get("Link", ""))
        if not m:
            return orders
        resp = await _request("GET", "", url=m.group(1))


async def get_order(order_id: str) -> dict:
    resp = await _request("GET", f"orders/{order_id}.json", params={"fields": ORDER_FIELDS})
    return resp.json()["order"]


async def get_shop() -> dict:
    resp = await _request("GET", "shop.json")
    return resp.json()["shop"]


async def ensure_order_webhook(address: str) -> str:
    """Make sure orders/create is delivered to `address`. Returns what it did."""
    resp = await _request("GET", "webhooks.json", params={"topic": "orders/create", "limit": 250})
    for wh in resp.json().get("webhooks", []):
        if wh.get("address") == address:
            return "exists"
    await _request("POST", "webhooks.json", json={"webhook": {
        "topic": "orders/create", "address": address, "format": "json"}})
    return "created"


def verify_webhook(body: bytes, hmac_header: str) -> bool:
    if not config.SHOPIFY_WEBHOOK_SECRET or not hmac_header:
        return False
    digest = hmac.new(config.SHOPIFY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), hmac_header.strip())


def numeric_id(value: Any) -> str:
    """'gid://shopify/Order/123' or 123 -> '123'."""
    m = re.search(r"(\d+)$", str(value or ""))
    return m.group(1) if m else ""
