"""Server copies of the landing pages' own Meta events (the listicles and the quiz).

The pages fire these from the visitor's browser with an eventID, and post the
same event here; the tracker sends it to Core Club through the Conversions API
with that same event_id, so Meta merges the two (one event, never two). Ad
blockers and iPhone privacy settings drop some browser events; the server
copy still arrives, so the "read the listicle" audience and Meta's picture of
who reads are fuller.

Only event names and neutral fields ever leave: no quiz answers, scores or
anything about the visitor's health (the build checklist's rule 5). Test
copies of the pages (localhost, ?jump=, ?noredirect=) never send.
"""
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

import attribution
import meta_capi

# The events a landing page may send. ViewContent stays the store's own product
# view: the pages fire ListicleView / QuizView instead.
EVENTS = {"ListicleView", "QuizView", "QuizStart", "ReportRead", "AdvertorialClickout", "QuizComplete"}
NO_CUSTOM = {"QuizComplete"}                  # its labels were health results: nothing rides along
TEST_PAGE = re.compile(r"[?&](jump|noredirect)=")
LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "[::1]", "::1"}
MAX_AGE = 7 * 86400                           # Meta refuses website events older than 7 days


def _s(v: Any, limit: int) -> str:
    return str(v if v is not None else "").strip()[:limit]


def test_copy(url: str) -> bool:
    """A local preview or a ?jump= test link: its events never reach Meta."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return True
    return host in LOCAL_HOSTS or host.endswith(".localhost") or bool(TEST_PAGE.search(url))


def _fbc(p: dict, url: str, now: float) -> str:
    """The browser's _fbc cookie, else one made from the page link's fbclid;
    never a stand-in or the Facebook browser's link tag."""
    cookie = attribution.real_fbc(_s(p.get("fbc"), 300))
    if cookie:
        return cookie
    click = attribution.fbclid_of(url)
    if click and not attribution.stand_in(click) and not attribution.link_tag(click):
        return attribution.make_fbc(click, now)
    return ""


def build_event(p: dict, ip: str, user_agent: str, now: Optional[float] = None) -> Optional[dict]:
    """The Meta event for one landing-page post; None for a test copy.
    Raises ValueError on payloads we refuse."""
    now = now or time.time()
    name = _s(p.get("name"), 64)
    if name not in EVENTS:
        raise ValueError(f"unknown event {name!r}")
    event_id = _s(p.get("id"), 100)
    if not event_id:
        raise ValueError("missing event id")
    url = _s(p.get("url"), 1000)
    if not url.startswith("https://"):
        raise ValueError("missing page url")
    if test_copy(url):
        return None
    ts = p.get("ts")
    at = float(ts) / 1000 if isinstance(ts, (int, float)) and ts > 0 else now
    at = now if at > now + 60 or at < now - MAX_AGE else at
    fbp = _s(p.get("fbp"), 100)
    event: dict[str, Any] = {
        "event_name": name,
        "event_time": int(at),
        "event_id": event_id,                 # the browser's eventID: Meta merges the pair
        "action_source": "website",
        "event_source_url": url,
        "user_data": meta_capi.build_user_data(ip=_s(ip, 64), user_agent=_s(user_agent, 400),
                                               fbp=fbp, fbc=_fbc(p, url, now)),
    }
    if name not in NO_CUSTOM:
        custom: dict[str, Any] = {}
        if _s(p.get("content_name"), 120):
            custom["content_name"] = _s(p["content_name"], 120)
        try:
            pct = int(p.get("percent"))
            if 0 < pct <= 100:
                custom["percent"] = pct
        except (TypeError, ValueError):
            pass
        if custom:
            event["custom_data"] = custom
    return event


async def send(event: dict, client_id: str = "") -> None:
    """To Core Club only: the pages' browser pixel is Core Club, so that is where
    the pair meets. A failure is recorded like any other and never retried in a loop."""
    try:
        await meta_capi.send_event(event, source="landing", attempts=2,
                                   pixel=meta_capi.primary_pixel(), client_id=client_id)
    except meta_capi.MetaError:
        pass
