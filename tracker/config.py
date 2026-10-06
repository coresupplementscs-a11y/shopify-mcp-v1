"""
Central configuration for the Meta tracking server. Every value comes from the
environment so the same image runs locally and on Railway.
"""
import math
import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        v = float(_env(name, str(default)))
    except ValueError:
        return default
    return v if math.isfinite(v) else default


def _set(name: str, default: str) -> set[str]:
    return {s.strip() for s in _env(name, default).split(",") if s.strip()}


# --- Meta -------------------------------------------------------------------
META_PIXEL_ID      = _env("META_PIXEL_ID")                 # Dataset / Pixel ID
META_ACCESS_TOKEN  = _env("META_ACCESS_TOKEN")             # CAPI system-user token
META_API_VERSION   = _env("META_API_VERSION", "v21.0")
META_TEST_EVENT_CODE = _env("META_TEST_EVENT_CODE")        # Only while testing in Events Manager


def _extra_pixels() -> tuple[list[dict], list[str]]:
    """Backup datasets that get a copy of every event: META_PIXEL_ID_2 with
    META_ACCESS_TOKEN_2 (and optionally META_TEST_EVENT_CODE_2), then _3 ...
    Each needs its own token: a Conversions API token belongs to one dataset."""
    pixels, problems = [], []
    for n in range(2, 10):
        pid, token = _env(f"META_PIXEL_ID_{n}"), _env(f"META_ACCESS_TOKEN_{n}")
        if not pid and not token:
            continue
        if not (pid and token):
            problems.append(f"META_PIXEL_ID_{n} and META_ACCESS_TOKEN_{n} must both be set; "
                            f"pixel {pid or '?'} is not receiving events.")
            continue
        if pid == META_PIXEL_ID or any(p["pixel_id"] == pid for p in pixels):
            problems.append(f"META_PIXEL_ID_{n}={pid} is listed twice; ignoring the repeat.")
            continue
        pixels.append({"pixel_id": pid, "token": token,
                       "test_event_code": _env(f"META_TEST_EVENT_CODE_{n}")})
    return pixels, problems


EXTRA_PIXELS, EXTRA_PIXEL_PROBLEMS = _extra_pixels()

# What the hub and the watchdog call each dataset. Meta's own dataset name
# wins over these defaults once it has been read; META_PIXEL_NAME (the main
# pixel) and META_PIXEL_NAME_<n> (the backup set as META_PIXEL_ID_<n>) win over both.
DEFAULT_PIXEL_NAMES = {"1298114545063437": "Core Club", "1717074239276698": "Eczema"}


def _pixel_name_overrides() -> dict[str, str]:
    names = {}
    if META_PIXEL_ID and _env("META_PIXEL_NAME"):
        names[META_PIXEL_ID] = _env("META_PIXEL_NAME")[:100]
    for n in range(2, 10):
        pid, name = _env(f"META_PIXEL_ID_{n}"), _env(f"META_PIXEL_NAME_{n}")
        if pid and name:
            names[pid] = name[:100]
    return names


PIXEL_NAME_OVERRIDES = _pixel_name_overrides()

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
RAILWAY_VOLUME_MOUNT_PATH = _env("RAILWAY_VOLUME_MOUNT_PATH")
DATA_DIR         = _env("DATA_DIR") or RAILWAY_VOLUME_MOUNT_PATH or "./data"
DB_PATH          = os.path.join(DATA_DIR, "tracker.db")
ON_RAILWAY       = bool(_env("RAILWAY_SERVICE_ID") or _env("RAILWAY_ENVIRONMENT_NAME")
                        or _env("RAILWAY_PROJECT_ID") or RAILWAY_VOLUME_MOUNT_PATH)
# Only for local development: silence the "data is not on a volume" problem.
ALLOW_EPHEMERAL_DATA = _env("ALLOW_EPHEMERAL_DATA", "false").lower() == "true"
LOG_LEVEL        = _env("LOG_LEVEL", "INFO").upper()
if LOG_LEVEL not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
    LOG_LEVEL = "INFO"
# X-Forwarded-For hops appended by proxies we trust (Railway's edge = 1; set 2
# if Cloudflare or another proxy is ever put in front).
TRUSTED_PROXY_HOPS = max(1, _int("TRUSTED_PROXY_HOPS", 1))

# --- Behaviour --------------------------------------------------------------
# Which order field becomes the Purchase value Meta optimises on.
PURCHASE_VALUE_FIELD   = _env("PURCHASE_VALUE_FIELD", "total_price")   # or subtotal_price
# A new sale that matches no storefront session and has no Shopify visit
# record yet waits for one: matching is retried at MATCH_RETRY_SECONDS after
# the order was placed, and at PURCHASE_GRACE_SECONDS (the longest a Purchase
# waits) it goes out with the best data found. 0 sends at once.
PURCHASE_GRACE_SECONDS = _int("PURCHASE_GRACE_SECONDS", 300)
MATCH_RETRY_SECONDS = sorted({int(s) for s in _set("MATCH_RETRY_SECONDS", "60,120") if s.isdigit()})
# Shopify is polled for new orders this often, so a dropped webhook costs at
# most one interval of delay.
POLL_INTERVAL_SECONDS = _int("POLL_INTERVAL_SECONDS", 60)
# Orders created before the tracker went live were already reported by the
# previous tracking app; sending them again would double-count them in Meta.
# Set an ISO time here only to deliberately backfill from that moment. It is
# authoritative whenever set.
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
RENEWAL_SOURCE_NAMES = _set("RENEWAL_SOURCE_NAMES",
                            "subscription_contract,subscription_contract_checkout_one")
# Belt and braces: Kaching Subscriptions also tags every rebill it bills. An
# order carrying one of these tags (the whole tag, any case) is a renewal too,
# whatever its source_name. "Kaching Subscription First Order" is not listed:
# the first order of a subscription is a real new sale and stays a Purchase.
RENEWAL_TAGS = {t.lower() for t in _set("RENEWAL_TAGS", "Kaching Subscription Recurring Order")}
RENEWAL_EVENT_NAME = _env("RENEWAL_EVENT_NAME", "SubscriptionRenewal")  # "" = don't send renewals
# Back-office orders (manual invoices, POS, the merchant's mobile app) were not
# driven by an ad and carry no browser data; they are skipped, not reported.
SKIP_SOURCE_NAMES = _set("SKIP_SOURCE_NAMES", "shopify_draft_order,pos,iphone,android")
# Retention: rows older than this are pruned hourly so the volume never fills.
SESSION_RETENTION_DAYS = _int("SESSION_RETENTION_DAYS", 30)
PIXEL_EVENT_RETENTION_DAYS = _int("PIXEL_EVENT_RETENTION_DAYS", 30)
ORDER_RETENTION_DAYS = _int("ORDER_RETENTION_DAYS", 180)

# --- Alerts (optional) ------------------------------------------------------
ALERT_WEBHOOK_URL = _env("ALERT_WEBHOOK_URL")              # Slack / Discord incoming webhook

# --- Hub ----------------------------------------------------------------------
# Days and "today" in the hub follow the store's clock.
STORE_TIMEZONE = _env("STORE_TIMEZONE", "America/New_York")
# Reading ad spend needs a token with ads_read on the ad accounts; the
# Conversions API token is tried when this is empty.
META_ADS_TOKEN = _env("META_ADS_TOKEN")
META_AD_ACCOUNT_IDS = [a.strip().removeprefix("act_") for a in _env("META_AD_ACCOUNT_IDS").split(",")
                       if a.strip()]
# A sale is credited to the last Meta ad clicked within this window.
ATTRIBUTION_WINDOW_DAYS = _int("ATTRIBUTION_WINDOW_DAYS", 7)
# Shopify's record of the buyer's visits (customerJourneySummary) gets this
# long per order; it never holds a Purchase up for longer.
JOURNEY_TIMEOUT_SECONDS = _int("JOURNEY_TIMEOUT_SECONDS", 6)
# How often the watchdog re-checks everything.
WATCHDOG_INTERVAL_SECONDS = _int("WATCHDOG_INTERVAL_SECONDS", 300)
# Creatives that sold: an ad gets its own row from this much spend in the
# range; the ones below are summed in one line per campaign and ad set.
HUB_MIN_AD_SPEND = max(0.0, _float("HUB_MIN_AD_SPEND", 15.0))
# The owner's P&L app. The hub's top section shows its numbers, read from
# its API; PNL_API_KEY is sent as a Bearer token once the P&L asks for one.
PNL_URL = _env("PNL_URL", "https://pnl-server-production.up.railway.app").rstrip("/")
# The creative tracker app (core-operation-hub2), framed in the hub's Creatives tab.
CREATIVE_URL = _env("CREATIVE_URL", "https://core-operation-hub2.vercel.app/tracker").rstrip("/")
# The Core Hub tab: the owner's downloader and transcriber. Its access key is not kept here (this
# repo is public): it is typed once in the tab, which remembers it, or put in CORE_HUB_URL in Railway
# as ...?key=<key>.
CORE_HUB_URL = _env("CORE_HUB_URL", "https://core-hub-production-0d82.up.railway.app/")

# The hub's agent (agent.py): Claude answering questions about the store and the ads.
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
AGENT_MODEL = _env("AGENT_MODEL", "claude-sonnet-5-5")
AGENT_DAILY_CAP_USD = max(0.0, _float("AGENT_DAILY_CAP_USD", 3.0))
PNL_API_KEY = _env("PNL_API_KEY")


def store_tz():
    """The store's timezone (UTC if STORE_TIMEZONE is not a known zone)."""
    import datetime
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(STORE_TIMEZONE)
    except Exception:
        return datetime.timezone.utc


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


def data_dir_problem() -> str:
    """Non-empty when the database would not survive a redeploy."""
    if ALLOW_EPHEMERAL_DATA or not ON_RAILWAY:
        return ""
    data = os.path.abspath(DATA_DIR)
    if RAILWAY_VOLUME_MOUNT_PATH and data.startswith(os.path.abspath(RAILWAY_VOLUME_MOUNT_PATH)):
        return ""
    if os.path.ismount(data):
        return ""
    return (f"DATA_DIR={DATA_DIR} is not on a Railway volume: order history and the "
            "go-live timestamp are lost on every redeploy. Add a volume and set DATA_DIR "
            "to its mount path.")
