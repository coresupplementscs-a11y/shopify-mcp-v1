"""
Reads from the owner's P&L app (PNL_URL) for the hub's top section. That
section runs the P&L page's own code on the P&L's own numbers, so the hub and
the P&L never disagree:

  report(from, to)   GET /api/pnl for the range (all products), trimmed to the
                     fields that code reads: totals per store, product and day.
                     No orders, no customers.
  manual_lines()     the owner's own P&L lines (state.manual.all of GET /api/state)
  software()         SW_TOOLS and DAYS_PER_MONTH as the live P&L page lists them
                     (read from its HTML), else the last list read from it,
                     else the copy below
  meta_sync_due()    like the P&L page on load: when the range includes today and
  sync_meta()        the P&L's Meta spend is over 15 minutes old, ask it to sync
  sync_from()        the range's last week at most (never "All" back to 2000)

Cached briefly. Nothing here reaches Meta or Shopify. The only write is
sync_meta's POST /api/sync/meta, the one the P&L page itself sends.
PNL_API_KEY, when set, travels in an Authorization header.
"""
import datetime as dt
import logging
import math
import re
import time
from typing import Any, Callable, Optional

import httpx

import config

log = logging.getLogger("tracker.pnl")

TIMEOUT = 30.0
REPORT_TTL = 60                    # /api/pnl, per range
STATE_TTL = 60                     # /api/state
PAGE_TTL = 600                     # the P&L page's software list
PAGE_RETRY_TTL = 60                # the page couldn't be read: the copy, asked again in a minute
SYNC_STALE = 15 * 60               # the P&L's Meta spend counts as fresh this long
SYNC_EVERY = 15 * 60               # this process asks the P&L to sync at most this often
SYNC_DAYS = 7                      # a sync reaches back at most this far, like the P&L's own cron (sync.js)
TIMEZONE = "America/New_York"      # the P&L's days (its ET_TZ)
FIRST_DAY = "2000-01-01"           # the P&L's "All"
UNAVAILABLE = "The P&L server did not answer"
BUCKETS = ("revenue", "cogs", "ads", "opex")

# Copied from the live P&L page (SW_TOOLS, DAYS_PER_MONTH) on Sep 27, 2026,
# after We Tracked was cancelled: $205.11 a month. Only used when the page
# can't be read and no list was read from it since this process started; the
# page's own list wins.
SW_TOOLS_COPY = [
    {"name": "Luxury Tools (1 acct)", "monthly": 29.53, "freq": "monthly"},
    {"name": "Netlify", "monthly": 6.33, "freq": "monthly"},
    {"name": "Heygen", "monthly": 28.12, "freq": "monthly"},
    {"name": "Zoho", "monthly": 1.23, "annual": 14.76, "freq": "annual"},
    {"name": "Software", "monthly": 139.90, "freq": "monthly"},
]
DAYS_PER_MONTH_COPY = 30.44


class PnlError(Exception):
    """The P&L couldn't be read; the message is for the owner."""


_client: Optional[httpx.AsyncClient] = None
_cache: dict[str, tuple[float, Any]] = {}
_last_manual: dict[str, Any] = {"value": None}     # the last lines read, for a failed read
_last_software: dict[str, Any] = {"value": None}   # the last software list read from the page, likewise
_sync = {"at": 0.0}                                # when this process last asked the P&L to sync


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=TIMEOUT)
    return _client


def reset() -> None:
    """Forget everything cached (tests)."""
    _cache.clear()
    _last_manual["value"] = None
    _last_software["value"] = None
    _sync["at"] = 0.0


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.PNL_API_KEY}"} if config.PNL_API_KEY else {}


def _cached(key: str) -> Any:
    hit = _cache.get(key)
    return hit[1] if hit and hit[0] > time.time() else None


def _store(key: str, value: Any, ttl: float) -> Any:
    # Keys carry dates, so without this the cache grows for as long as the process runs.
    now = time.time()
    for k in [k for k, (exp, _) in _cache.items() if exp < now]:
        del _cache[k]
    _cache[key] = (now + ttl, value)
    return value


async def _get(path: str, params: Optional[dict] = None) -> httpx.Response:
    resp = await _http().get(f"{config.PNL_URL}{path}", params=params, headers=_headers(), timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


def today() -> str:
    """Today in the P&L's clock (New York)."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(TIMEZONE)
    except Exception:
        tz = config.store_tz()
    return dt.datetime.now(tz).date().isoformat()


def _iso(value: Any) -> Optional[str]:
    """A P&L timestamp ("2026-09-28 01:05:02" is UTC, as SQLite writes it) as
    ISO 8601 UTC, or None."""
    ts = _epoch(value)
    return None if ts is None else dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _epoch(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


# --- the whitelist: only the fields the copied page code reads ------------------------

def _num(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v if math.isfinite(v) else None
    return None


def _text(v: Any) -> Optional[str]:
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        return None
    return str(v)[:300]


def _amount(v: Any) -> float:
    """A manual line's value: a number, or a number typed as text (the page parseFloats it)."""
    try:
        x = float(str(v).replace("$", "").replace(",", "").strip()) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if math.isfinite(x) else 0.0


def _pick(v: Any, spec: Any) -> Any:
    """`v` cut down to `spec`: a function cleans one value, a list spec keeps a
    list of its one element spec, a dict spec keeps its keys (and, with "*", any
    other key whose value is a plain number)."""
    if callable(spec):
        return spec(v)
    if isinstance(spec, list):
        return [_pick(x, spec[0]) for x in v] if isinstance(v, list) else None
    if not isinstance(v, dict):
        return None
    out = {k: _pick(v[k], sub) for k, sub in spec.items() if k != "*" and k in v}
    if "*" in spec:
        for k, x in v.items():
            if k not in out and isinstance(k, str) and (x is None or isinstance(x, (int, float))):
                out[k] = _num(x)
    return out


_NUMS = {"*": _num}                                 # {new, recurring, total, ...}
_DAY = {"date": _text, "*": _num}
_BLOCK = {"*": _num, "kpi": _NUMS, "revenue": _NUMS, "cogs": _NUMS, "orders": _NUMS, "units": _NUMS,
          "packs": _NUMS, "gross": _NUMS, "aov": _NUMS, "subs": _NUMS, "refunds": _NUMS, "roas": _NUMS,
          "net": {"value": _num, "provisional": _num},
          "fees": {"processing": _NUMS, "conversion": _NUMS, "total": _NUMS},
          "by_day": [_DAY]}
_PRODUCT = {"product_id": _text, "title": _text, "revenue": _NUMS, "orders": _NUMS,
            "by_variant": [{"variant_title": _text, "packs": _num, "cogs": _num}],
            "campaigns": [{"campaign_id": _text, "campaign_name": _text, "spend": _num}]}
_UNATTRIBUTED = {"revenue": _NUMS, "cogs": _NUMS, "products": [{"product_id": _text}],
                 "campaigns": [{"campaign_id": _text, "campaign_name": _text, "status": _text, "spend": _num}]}
_STORE = {"shipping_revenue": _num, "chargebacks": _num, "fee_adjustment": _num, "fees_source": _text,
          "platform_bills": _num, "platform_bill_count": _num, "platform_bills_cad": _num}
REPORT_SPEC = {"version": _num, "period": {"from": _text, "to": _text}, "generated_at": _text,
               "all": _BLOCK, "products": [_PRODUCT], "unattributed": _UNATTRIBUTED, "store": _STORE,
               "by_day": [_DAY]}
_LINE = {"id": _text, "name": _text, "val": _amount, "date": _text}


def _sync_info(raw: Any) -> dict:
    """The P&L's last sync per source, times as ISO UTC."""
    out = {}
    for source in ("shopify", "meta", "products"):
        s = raw.get(source) if isinstance(raw, dict) else None
        out[source] = ({"ran_at": _iso(s.get("ran_at")), "status": _text(s.get("status")) or ""}
                       if isinstance(s, dict) else None)
    return out


# --- reads ------------------------------------------------------------------------

async def report(frm: str, to: str) -> dict:
    """The P&L for the days [frm, to], all products: {"pnl": the payload
    trimmed to REPORT_SPEC, "last_sync", "fetched_at"}. Cached a minute per
    range. Raises PnlError, never returns zeros for a P&L that didn't answer."""
    key = f"pnl:{frm}:{to}"
    hit = _cached(key)
    if hit is not None:
        return hit
    try:
        data = (await _get("/api/pnl", {"from": frm, "to": to, "product": "all"})).json()
    except httpx.HTTPStatusError as e:
        raise PnlError(f"{UNAVAILABLE} (it said {e.response.status_code})")
    except (httpx.HTTPError, ValueError) as e:
        log.info("P&L unreachable: %s", type(e).__name__)
        raise PnlError(UNAVAILABLE)
    # The P&L page refuses anything older than its second payload version too.
    if not isinstance(data, dict) or not isinstance(data.get("all"), dict) or (_num(data.get("version")) or 0) < 2:
        raise PnlError(f"{UNAVAILABLE} with its numbers")
    fetched = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    out = {"pnl": _pick(data, REPORT_SPEC), "last_sync": _sync_info(data.get("last_sync")), "fetched_at": fetched}
    return _store(key, out, REPORT_TTL)


async def manual_lines() -> tuple[Optional[dict], str]:
    """The owner's own lines ({revenue, cogs, ads, opex}: [{id, name, val,
    date}]) and "", or the last lines read (None before any) and why they
    couldn't be read now. Cached a minute."""
    hit = _cached("state")
    if hit is not None:
        return hit, ""
    try:
        data = (await _get("/api/state")).json()
    except (httpx.HTTPError, ValueError) as e:
        log.info("P&L state unreadable: %s", type(e).__name__)
        if _last_manual["value"] is not None:
            return _last_manual["value"], ""
        return None, "Your own P&L lines couldn't be read just now, so they are left out."
    state = data.get("state") if isinstance(data, dict) else None      # null until the P&L saves one
    manual = (state or {}).get("manual") if isinstance(state, dict) else None
    mine = manual.get("all") if isinstance(manual, dict) else None
    mine = mine if isinstance(mine, dict) else {}
    lines = {b: [_pick(r, _LINE) for r in mine.get(b) or [] if isinstance(r, dict)]
             if isinstance(mine.get(b), list) else [] for b in BUCKETS}
    _last_manual["value"] = lines
    return _store("state", lines, STATE_TTL), ""


_SW_BLOCK = re.compile(r"const\s+SW_TOOLS\s*=\s*\[(.*?)\]\s*;", re.S)
_DPM = re.compile(r"const\s+DAYS_PER_MONTH\s*=\s*(\d+(?:\.\d+)?)\s*;")
_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _js_field(entry: str, name: str, parse: Callable) -> Any:
    m = re.search(rf"(?<![\w$]){name}\s*:\s*(?:'([^']*)'|\"([^\"]*)\"|(-?\d+(?:\.\d+)?))", entry)
    if not m:
        return None
    raw = m.group(1) if m.group(1) is not None else m.group(2) if m.group(2) is not None else m.group(3)
    try:
        return parse(raw)
    except ValueError:
        return None


def parse_software(html: str) -> Optional[tuple[list[dict], float]]:
    """(SW_TOOLS, DAYS_PER_MONTH) as the P&L page's script declares them, or
    None when either can't be read cleanly."""
    block, dpm = _SW_BLOCK.search(html or ""), _DPM.search(html or "")
    if not block or not dpm:
        return None
    tools = []
    for entry in re.findall(r"\{([^{}]*)\}", _COMMENTS.sub("", block.group(1))):
        name, monthly = _js_field(entry, "name", str), _js_field(entry, "monthly", float)
        if not name or monthly is None or not math.isfinite(monthly) or not 0 <= monthly < 100_000:
            return None
        tool = {"name": name[:100], "monthly": monthly, "freq": _js_field(entry, "freq", str) or "monthly"}
        annual = _js_field(entry, "annual", float)
        if annual is not None and math.isfinite(annual) and annual >= 0:
            tool["annual"] = annual
        tools.append(tool)
    days = float(dpm.group(1))
    if not 1 <= len(tools) <= 50 or not 28 <= days <= 31:
        return None
    return tools, days


async def software() -> dict:
    """{"sw_tools", "days_per_month", "sw_tools_source"}: 'pnl' when read from
    the live P&L page (kept 10 minutes) or, when a read fails, the list read
    from it last time; 'copy' when none was ever read and the copy above
    stands in. A failed read is tried again after a minute."""
    hit = _cached("software")
    if hit is not None:
        return hit
    parsed = None
    try:
        parsed = parse_software((await _get("/")).text)
    except httpx.HTTPError as e:
        log.info("P&L page unreadable: %s", type(e).__name__)
    if parsed:
        live = {"sw_tools": parsed[0], "days_per_month": parsed[1], "sw_tools_source": "pnl"}
        _last_software["value"] = live
        return _store("software", live, PAGE_TTL)
    if _last_software["value"] is not None:
        return _store("software", _last_software["value"], PAGE_RETRY_TTL)
    return _store("software", {"sw_tools": [dict(t) for t in SW_TOOLS_COPY], "days_per_month": DAYS_PER_MONTH_COPY,
                               "sw_tools_source": "copy"}, PAGE_RETRY_TTL)


# --- keeping the P&L's Meta spend fresh --------------------------------------------------

def meta_sync_due(frm: str, to: str, last_sync: Optional[dict], now: Optional[float] = None) -> bool:
    """Whether to ask the P&L to sync Meta spend for [frm, to], like its page's
    loadServerData: the range includes today and its last Meta sync is over 15
    minutes old (or unknown). This process asks at most once per 15 minutes; a
    yes claims that slot."""
    now = time.time() if now is None else now
    if not frm <= today() <= to:
        return False
    ran = _epoch(((last_sync or {}).get("meta") or {}).get("ran_at"))
    if ran is not None and now - ran < SYNC_STALE:
        return False
    if now - _sync["at"] < SYNC_EVERY:
        return False
    _sync["at"] = now
    return True


def sync_from(frm: str) -> str:
    """Where a Meta sync for a range starting at `frm` starts: never more than
    SYNC_DAYS before today. Only recent spend changes, and "All" would ask the
    P&L for every month since 2000, most of them older than Meta keeps."""
    week = (dt.date.fromisoformat(today()) - dt.timedelta(days=SYNC_DAYS)).isoformat()
    return max(frm, week)


async def sync_meta(frm: str, to: str) -> str:
    """POST /api/sync/meta {from, to}, unless the P&L says Meta is rate
    limiting it (meta_rate_limited_until in the future). Run in the
    background, never awaited by a page load. Never raises; returns
    'sent', 'rate_limited' or 'failed'."""
    try:
        until = (await _get("/api/last-sync")).json().get("meta_rate_limited_until")
    except Exception as e:                      # unknown, like on the P&L page: its own guard still applies
        log.info("P&L last sync unreadable: %s", type(e).__name__)
        until = None
    limited = _epoch(until)
    if limited is not None and limited > time.time():
        return "rate_limited"
    try:
        resp = await _http().post(f"{config.PNL_URL}/api/sync/meta", json={"from": frm, "to": to},
                                  headers=_headers(), timeout=TIMEOUT)
    except httpx.TimeoutException:
        return "sent"                           # the P&L keeps syncing after we stop waiting
    except Exception as e:
        log.info("P&L Meta sync request failed: %s", type(e).__name__)
        return "failed"
    return "sent" if resp.status_code < 400 else "failed"
