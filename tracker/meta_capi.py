"""
Meta Conversions API: normalise + hash customer data to Meta's spec and send
events. Every send is recorded in the events table with Meta's fbtrace_id.
"""
import asyncio
import hashlib
import logging
import re
import unicodedata
from typing import Any, Iterable, Optional

import httpx
import phonenumbers

import config
import db

log = logging.getLogger("tracker.meta")

GRAPH_URL = "https://graph.facebook.com"
MAX_EVENT_AGE_SECONDS = 7 * 24 * 3600          # Meta rejects older website events


# --- normalisation (https://developers.facebook.com/docs/marketing-api/conversions-api/parameters/customer-information-parameters)
# Meta hashes the UTF-8 text, so accented letters are kept (José -> "josé"),
# and only whitespace, punctuation and digits are removed, like Meta's own SDKs.

def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def norm_email(v: Optional[str]) -> str:
    v = (v or "").strip().lower()
    return v if "@" in v else ""


def norm_phone(v: Optional[str], country: Optional[str] = "") -> str:
    """E.164 digits without the plus (Meta's format). The buyer's country
    resolves national numbers; trunk prefixes like the UK's leading 0 are
    dropped, which a plain digit-strip would get wrong."""
    raw = (v or "").strip()
    if not raw:
        return ""
    region = (country or "").upper() or None
    try:
        parsed = phonenumbers.parse(raw, region)
        if phonenumbers.is_possible_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164).lstrip("+")
    except phonenumbers.NumberParseException:
        pass
    # No usable country: only an explicit international number can be trusted.
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") and len(digits) >= 8:
        return digits
    return ""


def norm_name(v: Optional[str]) -> str:
    v = unicodedata.normalize("NFC", (v or "").strip().lower())
    return re.sub(r"[\W\d_]", "", v)


def norm_city(v: Optional[str]) -> str:
    v = unicodedata.normalize("NFC", (v or "").strip().lower())
    return re.sub(r"[\W\d_]", "", v)


def norm_state(v: Optional[str]) -> str:
    return re.sub(r"[^a-z]", "", (v or "").lower())


def norm_zip(v: Optional[str], country: Optional[str] = "") -> str:
    z = re.sub(r"[\s\-]", "", (v or "").lower())
    if (country or "").upper() == "US":
        z = z[:5]
    return z


def norm_country(v: Optional[str]) -> str:
    v = (v or "").strip().lower()
    return v if len(v) == 2 else ""


def _hashed(values: Iterable[str]) -> list[str]:
    out = []
    for v in values:
        if v and v not in out:
            out.append(v)
    return [_sha(v) for v in out]


def build_user_data(*, emails: Iterable[str] = (), phones: Iterable[str] = (),
                    first_name: str = "", last_name: str = "", city: str = "",
                    state: str = "", zip_code: str = "", country: str = "",
                    external_ids: Iterable[str] = (), ip: str = "", user_agent: str = "",
                    fbp: str = "", fbc: str = "") -> dict:
    """Return Meta user_data with PII normalised and hashed, identifiers raw."""
    ud: dict[str, Any] = {}
    em = _hashed(norm_email(e) for e in emails)
    ph = _hashed(norm_phone(p, country) for p in phones)
    if em: ud["em"] = em
    if ph: ud["ph"] = ph
    for key, raw in (("fn", norm_name(first_name)), ("ln", norm_name(last_name)),
                     ("ct", norm_city(city)), ("st", norm_state(state)),
                     ("zp", norm_zip(zip_code, country)), ("country", norm_country(country))):
        if raw:
            ud[key] = [_sha(raw)]
    xid = _hashed(str(x).strip() for x in external_ids if x)
    if xid: ud["external_id"] = xid
    if ip: ud["client_ip_address"] = ip
    if user_agent: ud["client_user_agent"] = user_agent
    if fbp: ud["fbp"] = fbp
    if fbc: ud["fbc"] = fbc
    return ud


# --- sending ----------------------------------------------------------------

class MetaError(Exception):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


_client: Optional[httpx.AsyncClient] = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=20.0)
    return _client


def set_http_client(client: httpx.AsyncClient) -> None:
    """Tests inject a mock transport here."""
    global _client
    _client = client


async def _post(events: list[dict], test_event_code: str = "") -> dict:
    url = f"{GRAPH_URL}/{config.META_API_VERSION}/{config.META_PIXEL_ID}/events"
    body: dict[str, Any] = {"data": events, "access_token": config.META_ACCESS_TOKEN}
    code = test_event_code or config.META_TEST_EVENT_CODE
    if code:
        body["test_event_code"] = code
    try:
        resp = await _http().post(url, json=body)
    except httpx.HTTPError as e:
        raise MetaError(f"network: {type(e).__name__}: {e}", retryable=True)
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text[:500]}
    if resp.status_code >= 500 or resp.status_code == 429:
        raise MetaError(f"HTTP {resp.status_code}: {data}", retryable=True)
    if resp.status_code >= 400:
        err = data.get("error", {}) if isinstance(data, dict) else {}
        # Code 1/2 and "is_transient" are Meta's temporary failures.
        transient = bool(err.get("is_transient")) or err.get("code") in (1, 2, 4, 17, 341)
        msg = err.get("error_user_msg") or err.get("message") or str(data)
        raise MetaError(f"HTTP {resp.status_code}: {msg}", retryable=transient)
    if not isinstance(data, dict):
        raise MetaError(f"unexpected response: {str(data)[:300]}", retryable=False)
    return data


def _record(event: dict, source: str, status: str, order_id: str, **kw) -> None:
    """Bookkeeping must never turn an accepted send into a retry storm."""
    try:
        db.record_event(event["event_name"], event["event_id"], source, status, event,
                        order_id=order_id, **kw)
    except Exception:
        log.exception("could not record %s %s (%s)", event["event_name"], event["event_id"], status)


async def send_event(event: dict, *, source: str, order_id: str = "",
                     test_event_code: str = "", attempts: int = 3) -> str:
    """Send one event (with retries on transient errors). Returns fbtrace_id.
    Raises MetaError when it ultimately fails; the failure is recorded."""
    name, eid = event["event_name"], event["event_id"]
    delay = 2.0
    last: Optional[MetaError] = None
    for i in range(attempts):
        try:
            data = await _post([event], test_event_code)
            trace = str(data.get("fbtrace_id", ""))
            try:
                received = int(data.get("events_received", 0))
            except (TypeError, ValueError):
                received = 0
            if received < 1:
                raise MetaError(f"Meta accepted 0 events: {data}", retryable=False)
            _record(event, source, "sent", order_id, fbtrace_id=trace)
            return trace
        except MetaError as e:
            last = e
            if not e.retryable or i == attempts - 1:
                break
            await asyncio.sleep(delay)
            delay *= 3
    assert last is not None
    _record(event, source, "failed", order_id, error=str(last)[:1000])
    log.warning("Meta %s %s failed: %s", name, eid, last)
    raise last
