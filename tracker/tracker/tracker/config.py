"""
Central configuration for the Meta tracking server. Every value comes from the
environment so the same image runs locally and on Railway.
"""
import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


# --- Meta -------------------------------------------------------------------
META_PIXEL_ID      = _env("META_PIXEL_ID")                 # Dataset / Pixel ID
META_ACCESS_TOKEN  = _env("META_ACCESS_TOKEN")             # CAPI system-user token
META_API_VERSION   = _env("META_API_VERSION", "v21.0")
META_TEST_EVENT_CODE = _env("META_TEST_EVENT_CODE")        # Only while testing in Events Manager

# --- Shopify ----------------------------------------------------------------
SHOPIFY_STORE          = _env("SHOPIFY_STORE")             # "my-store" (before .myshopify.com)
SHOPIFY_ACCESS_TOKEN   = _env("SHOPIFY_ACCESS_TOKEN")      # shpat_... (static custom-app token)
SHOPIFY_CLIENT_ID      = _env("SHOPIFY_CLIENT_ID")         # or client credentials, like server.py
SHOPIFY_CLIENT_SECRET  = _env("SHOPIFY_CLIENT_SECRET")
# Signs orders/create webhooks. Optional: without it webhooks are rejected and
# the poller alone picks orders up (within POLL_INTERVAL_SECONDS).
SHOPIFY_WEBHOOK_SECRET = _env("SHOPIFY_WEBHOOK_SECRET") or SHOPIFY_CLIENT_SECRET
SHOPIFY_API_VERSION    = _env("SHOPIFY_API_VERSION", "2024-10")
# Public storefront URL used as event_source_url, e.g. https://getcoresupps.com
STORE_URL              = _env("STORE_URL").rstrip("/")

# --- Server -----------------------------------------------------------------
PORT             = _int("PORT", 8000)
PUBLIC_URL       = _env("PUBLIC_URL").rstrip("/")          # https://xxx.up.railway.app
ADMIN_TOKEN      = _env("ADMIN_TOKEN")                     # Protects /mcp, /report, /admin/*
DATA_DIR         = _env("DATA_DIR") or _env("RAILWAY_VOLUME_MOUNT_PATH") or "./data"
DB_PATH          = os.path.join(DATA_DIR, "tracker.db")
LOG_LEVEL        = _env("LOG_LEVEL", "INFO")

# --- Behaviour --------------------------------------------------------------
# Which order field becomes the Purchase value Meta optimises on.
PURCHASE_VALUE_FIELD   = _env("PURCHASE_VALUE_FIELD", "total_price")   # or subtotal_price
# Give the pixel this long to report checkout_completed before we send a
# Purchase without its browser identifiers.
PURCHASE_GRACE_SECONDS = _int("PURCHASE_GRACE_SECONDS", 90)
# Shopify is polled for new orders this often, so a dropped webhook costs at
# most one interval of delay.
POLL_INTERVAL_SECONDS = _int("POLL_INTERVAL_SECONDS", 60)
# Orders created before the tracker first started were already reported by
# the previous tracking app; sending them again would double-count them in
# Meta. Set an ISO time here only to deliberately backfill from that moment.
TRACK_ORDERS_FROM = _env("TRACK_ORDERS_FROM")
# Must match the id your Meta catalog uses: product_id, variant_id or sku.
CONTENT_ID_FIELD = _env("CONTENT_ID_FIELD", "product_id")
# How far back the reconciler looks for orders Meta never received.
RECONCILE_LOOKBACK_HOURS = _int("RECONCILE_LOOKBACK_HOURS", 72)
RECONCILE_INTERVAL_SECONDS = _int("RECONCILE_INTERVAL_SECONDS", 600)
# Alert if the pixel has been silent for this long (store traffic should be steady).
PIXEL_SILENCE_ALERT_MINUTES = _int("PIXEL_SILENCE_ALERT_MINUTES", 120)
# Skip Shopify test orders unless explicitly allowed.
SEND_TEST_ORDERS = _env("SEND_TEST_ORDERS", "false").lower() == "true"
# Recurring subscription billing creates orders no ad drove. Sending them as
# Purchase inflates Ads Manager ROAS and trains Meta on the wrong buyers, so
# orders from these Shopify source_names go out as RENEWAL_EVENT_NAME instead.
RENEWAL_SOURCE_NAMES = {
    s.strip() for s in _env(
        "RENEWAL_SOURCE_NAMES",
        "subscription_contract,subscription_contract_checkout_one",
    ).split(",") if s.strip()
}
RENEWAL_EVENT_NAME = _env("RENEWAL_EVENT_NAME", "SubscriptionRenewal")  # "" = don't send renewals

# --- Alerts (optional) ------------------------------------------------------
ALERT_WEBHOOK_URL = _env("ALERT_WEBHOOK_URL")              # Slack / Discord incoming webhook


def missing_required() -> list[str]:
    required = {
        "META_PIXEL_ID": META_PIXEL_ID,
        "META_ACCESS_TOKEN": META_ACCESS_TOKEN,
        "SHOPIFY_STORE": SHOPIFY_STORE,
        "SHOPIFY_ACCESS_TOKEN or SHOPIFY_CLIENT_ID+SHOPIFY_CLIENT_SECRET":
            SHOPIFY_ACCESS_TOKEN or (SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET),
        "ADMIN_TOKEN": ADMIN_TOKEN,
    }
    return [k for k, v in required.items() if not v]
