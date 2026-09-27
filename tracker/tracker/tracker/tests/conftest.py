import os
import sys
import tempfile

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
