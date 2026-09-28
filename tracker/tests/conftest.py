import os
import sys
import tempfile

import httpx
import pytest

# Settings must exist before the tracker modules import config.
os.environ.update({
    "META_PIXEL_ID": "1298114545063437",
    "META_ACCESS_TOKEN": "test-token",
    "SHOPIFY_STORE": "teststore",
    "SHOPIFY_ACCESS_TOKEN": "shpat_test",
    "SHOPIFY_WEBHOOK_SECRET": "whsec_test",
    "ADMIN_TOKEN": "admin-test",
    "STORE_URL": "https://getcoresupps.com",
    "DATA_DIR": tempfile.mkdtemp(prefix="tracker-test-"),
    "META_TEST_EVENT_CODE": "",
    "ALERT_WEBHOOK_URL": "",
})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _offline(request: httpx.Request):
    raise httpx.ConnectError(f"tests never reach {request.url.host}", request=request)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """A test that forgets to mock Meta, Shopify or the P&L app fails like a
    network outage instead of reaching the live store, ad account or P&L."""
    import meta_ads
    import meta_capi
    import pnl
    import shopify
    for module in (meta_capi, meta_ads, shopify, pnl):
        monkeypatch.setattr(module, "_client", httpx.AsyncClient(transport=httpx.MockTransport(_offline)))
