"""
Which Meta ad a visit or a sale came from.

Ads carry URL parameters (utm_* and ad_id/adset_id/campaign_id, set once in
Ads Manager); an fbclid alone proves an ad click but not which ad. Today's
URL templates put the ad set's name in utm_content and the ad's in utm_term,
older ones the other way round, so names are matched both ways against
Meta's own (resolve_names) and never trusted for which is which.

One resolver, resolve(), decides a sale's last click once: the Purchase sent
to Meta carries that click and the hub shows the same stored record. Its
candidates, the newest click inside ATTRIBUTION_WINDOW_DAYS winning:
  a) the buyer's storefront session: the pixel's newest ad arrival,
  b) Shopify's record of the buyer's last visit (customerJourneySummary),
  c) the old tracker's note_attributes: fbc with its real click time, utm names,
  d) the order's landing_site, only when nothing else exists. Shopify keeps
     the buyer's FIRST landing page for about two weeks, so it is the first
     touch, not necessarily the visit that led to the purchase.
Like Triple Whale's last click with assists: the other ads the buyer clicked
in the window, and the first-visit ad, are listed as assists; they never
take the credit. A sale with no Meta click gets a channel instead.
"""
import datetime as dt
import hashlib
import json
import math
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import config

AD_KEYS = ("ad_id", "adset_id", "campaign_id", "utm_source", "utm_medium",
           "utm_campaign", "utm_term", "utm_content", "utm_id", "lp")
ID_KEYS = ("ad_id", "adset_id", "campaign_id", "utm_id")
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")
META_SOURCES = {"facebook", "fb", "meta", "instagram", "ig", "an", "msg", "threads"}
HISTORY_MAX = 20                   # Meta ad arrivals remembered per browser
REPEAT_SECONDS = 30 * 60           # the same ad again this soon is the same visit
ASSISTS_MAX = HISTORY_MAX - 1      # every other ad of a full history: all of them, not a top few
VISIT_KEYS = ("ad_id", "ad_name", "adset_name", "campaign_name")
# Utm tags without any ad id (and no lp) came through the old listicle, which
# forwarded only fbclid, gclid, ttclid and the utm_* tags.
LISTICLE = "listicle"
# Stored on every decision; the startup backfill re-resolves older records
# (never one the tracker sent: those carry the fbc Meta got). 3: the first
# landing page no longer sells unverified when a known click proves it old.
# 4: a landing page Shopify cut short names the ad of the same click.
RESOLVER_VERSION = 6
# A click stamped this much after the order (clocks differ) still came before it.
CLOCK_SKEW = 30
FBCLID_RE = re.compile(r"[A-Za-z0-9_-]{10,400}")
# A stand-in where Meta's click id belongs: typed into an ad's website URL
# (?fbclid=fbclid, {{fbclid}}, {fbclid}) or written by a landing page script
# that had none (undefined, null). Meta only adds its own fbclid to a link that
# has none, so an ad whose URL carries one of these never gets a real click id,
# and Meta's pixel still builds an _fbc cookie from it ("sperm 2", Oct 2026).
STAND_IN_FBCLID = re.compile(r"[\s{\[(<$%]*(?:fbclid|fbc|click_?id|undefined|null|none|nan)[\s}\])>%]*", re.I)
# Also a stand-in: anything with "fbclid" or "undefined" in it, or a bracket, space or $ % sign
# (?fbclid=ASfbclid on a sperm 2 ad in Ads Manager). Meta's own ids are base64-like letters,
# digits, - and _, so they never hold those.
STAND_IN_PART = re.compile(r"fbclid|undefined|[\s{}\[\]<>$%]", re.I)
# Shopify cuts landing_site at 255 characters, so the fbclid at its end can
# lose its tail ("IwZXh0bgNhZW0BMABwZ"). Only a landing page this long that
# ends with its fbclid can have been cut. A cut fbclid is the same click as a
# longer one it begins only when the two moments are this close.
LANDING_SITE_CUT_AT = 250
CUT_CLICK_SECONDS = 30 * 60
# Every fbclid begins with a field header ("IwZXh0bgNhZW0BMAB" then a field
# name, "wZ..." or "hZGlk...") that says nothing about the click itself: a cut
# no longer than 17 characters could be anyone's click, and one shorter than
# CUT_FBCLID_UNTIMED characters matches a click only by time, so both moments
# must be known. #c3711 kept 19 characters.
CUT_FBCLID_MIN = 18
CUT_FBCLID_UNTIMED = 40
_ENDS_WITH_FBCLID = re.compile(r"[?&]fbclid=[A-Za-z0-9_-]+$")
# Same moment, several records of one click: the richest one speaks for it.
SOURCE_RANK = {"browser": 0, "shopify_last_visit": 1, "order_note": 2, "click_id": 3}

# Channels for sales no Meta click got (R8).
SHOP_APP_SOURCES = {"shop_campaigns"}
EMAIL_SMS = {"klaviyo", "postscript", "attentive", "email", "sms"}
GOOGLE_SOURCES = {"google", "googleads", "adwords", "youtube"}
TIKTOK_SOURCES = {"tiktok", "tiktokads"}
META_CHANNEL = "Meta ads"


def _low(s: Any) -> str:
    return str(s or "").strip().lower()


def _float(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def parse_time(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def _query(url: Any) -> dict[str, str]:
    try:
        q = parse_qs(urlparse(str(url or "")).query)
    except (TypeError, ValueError):
        return {}
    return {k: (v[0] if v else "") for k, v in q.items()}


def _host(url: Any) -> str:
    try:
        host = urlparse(str(url or "")).hostname or ""
    except (TypeError, ValueError):
        return ""
    return host.lower().removeprefix("www.")[:100]


# --- ad links -------------------------------------------------------------------

# Links Instagram and Facebook tag themselves on organic posts and profiles
# (Instagram's bio link adds utm_source=ig&utm_medium=social&utm_content=link_in_bio
# and an fbclid). They are free traffic, not an ad that lost its ids.
ORGANIC_CONTENT = {"link_in_bio"}
ORGANIC_MEDIUMS = {"social", "organic", "organic_social", "bio"}


def is_organic(params: Any) -> bool:
    """Meta's own tags on an organic link (no ad id anywhere)."""
    if not isinstance(params, dict) or any(params.get(k) for k in ID_KEYS):
        return False
    return (_low(params.get("utm_content")) in ORGANIC_CONTENT or _low(params.get("utm_term")) in ORGANIC_CONTENT
            or _low(params.get("utm_medium")) in ORGANIC_MEDIUMS)


def ad_params_from_url(url: Any) -> dict:
    """The Meta ad identifiers in a landing URL, or {} when it isn't a Meta ad
    link (an organic link Meta tagged itself is not one)."""
    q = _query(url)
    params = {k: q[k].strip()[:300] for k in AD_KEYS if q.get(k, "").strip()}
    if is_organic(params):
        return {}
    raw = q.get("fbclid", "").strip()
    click = bool(raw) and not stand_in(raw)
    params = utm_ids(params)                    # ids Meta's own tags carry count as the ad's ids
    source = params.get("utm_source", "").lower()
    # Any fbclid, even a stand-in typed into the ad's website URL, marks a Meta
    # ad's link; only a real one is a click. sperm 2's ads without URL
    # parameters carry nothing else (#c4081: fbclid=fbclid, utm_id and the ids
    # in utm_content/utm_term/utm_campaign, no utm_source, no ad_id).
    if not (raw or params.get("ad_id") or source in META_SOURCES):
        return {}
    if click:
        params["fbclid"] = "1"                      # presence only; the value lives in fbc
    elif not params:
        params["stand_in"] = "1"                    # the stand-in alone: the ad's link, with nothing to name it by
    return params


def fbclid_of(url: Any) -> str:
    """The fbclid a URL carries, when it looks like one Meta would accept."""
    v = _query(url).get("fbclid", "").strip()
    return v if FBCLID_RE.fullmatch(v) else ""


def stand_in(fbclid: Any) -> bool:
    """Whether a value where Meta's click id belongs is a stand-in, not a click."""
    v = str(fbclid or "").strip()
    return bool(STAND_IN_FBCLID.fullmatch(v) or (v and STAND_IN_PART.search(v)))


def stand_in_fbclid(url: Any) -> str:
    """The stand-in a link carries for Meta's click id (?fbclid=fbclid), else ''."""
    v = _query(url).get("fbclid", "").strip()
    return v[:40] if v and stand_in(v) else ""


def real_fbc(fbc: Any) -> str:
    """An fbc as it may go to Meta: '' when its click id is a stand-in, since
    Meta can't match it to any click and flags the value."""
    fbc = str(fbc or "").strip()
    return "" if fbc and stand_in(fbc_fbclid(fbc) or fbc.rsplit(".", 1)[-1]) else fbc


def click_time(fbc: Any) -> Optional[float]:
    """When the ad was clicked, from an fbc of the form fb.1.<ms>.<fbclid>."""
    parts = str(fbc or "").split(".")
    if len(parts) < 4:
        return None
    try:
        return int(parts[2]) / 1000
    except ValueError:
        return None


def fbc_fbclid(fbc: Any) -> str:
    parts = str(fbc or "").split(".", 3)
    return parts[3] if len(parts) == 4 and parts[0] == "fb" else ""


def ends_with_fbclid(url: Any) -> bool:
    """Whether a URL's last parameter is its fbclid, so that cutting the end
    off the string (Shopify does, at 255 characters) cuts the fbclid."""
    return bool(_ENDS_WITH_FBCLID.search(str(url or "").strip()))


def cut_short(url: Any) -> bool:
    """Whether Shopify can have cut a landing page's fbclid short: the page
    ends with its fbclid (Meta adds it last) and is as long as Shopify keeps."""
    return ends_with_fbclid(url) and len(str(url or "").strip()) >= LANDING_SITE_CUT_AT


def _cut_of(cut: str, full: str) -> bool:
    """Whether `cut` can be `full` with its tail cut off. Never on the start
    that many fbclids share (CUT_FBCLID_MIN)."""
    return len(cut) >= CUT_FBCLID_MIN and len(full) > len(cut) and full.startswith(cut)


def _ad_names(ad: dict) -> tuple:
    """The names a record's link gave its ad and ad set, lower case and in
    either order (link templates swapped utm_content and utm_term)."""
    pair = ad.get("pair") or (ad.get("adset_name") or "", ad.get("ad_name") or "")
    return tuple(sorted(n for n in (_low(x) for x in pair) if n))


def other_ad(a: dict, b: dict) -> bool:
    """Whether two records name two different ads: by ad id when both carry
    one, else by their link's names (same_ad, both ways round like
    resolve_names). Unknown (False) when either names none."""
    ia, ib = str(a.get("ad_id") or ""), str(b.get("ad_id") or "")
    if ia and ib:
        return ia != ib
    na, nb = _ad_names(a), _ad_names(b)
    if not na or not nb:
        return False
    if len(na) == len(nb):
        return na != nb
    one, both = (na, nb) if len(na) < len(nb) else (nb, na)
    return one[0] not in both                   # a single name is the ad's or its ad set's


def same_click(a: dict, b: dict) -> bool:
    """Whether two candidates are records of one ad click. Meta gives every
    click its own fbclid, so the same fbclid is the same click. A landing page
    Shopify cut short (`cut`, cut_short) is also the same click as a longer
    fbclid it begins, unless the two records name two different ads
    (other_ad), or the moments are more than CUT_CLICK_SECONDS apart. A cut
    shorter than CUT_FBCLID_UNTIMED is only the header every fbclid starts
    with, so it needs both moments known."""
    fa, fb = a.get("fbclid") or "", b.get("fbclid") or ""
    if not fa or not fb:
        return False
    if fa == fb:
        return True
    short, full = (a, b) if len(fa) < len(fb) else (b, a)
    if not short.get("cut") or not _cut_of(short["fbclid"], full["fbclid"]):
        return False
    ta, tb = short.get("at"), full.get("at")
    if ta is None or tb is None:
        if len(short["fbclid"]) < CUT_FBCLID_UNTIMED:
            return False
    elif abs(ta - tb) > CUT_CLICK_SECONDS:
        return False
    return not other_ad(short.get("ad") or {}, full.get("ad") or {})


def make_fbc(fbclid: str, at: float) -> str:
    return f"fb.1.{int(at * 1000)}.{fbclid}"


def click_key(fbclid: str) -> str:
    """A short fingerprint of an fbclid, to recognise the same click later
    without storing its value a second time."""
    return hashlib.sha256(fbclid.encode()).hexdigest()[:16] if fbclid else ""


def first_arrival(history: Any, fbclid: str) -> Optional[float]:
    """When a click (by its fbclid) first reached the store, from a browser's
    click history, or None when it isn't in it. Meta gives every ad click its
    own fbclid, so one seen before is an old click coming back, never a new one."""
    key = click_key(fbclid)
    return next((v["at"] for v in ad_history(history) if key and v.get("click") == key), None)


def newer_fbc(stored: Any, incoming: Any) -> str:
    """The browser's current click: an fbc cookie only replaces the one on
    record when it is a different, newer click. The same click again keeps the
    moment it first arrived."""
    stored, incoming = str(stored or ""), str(incoming or "")
    if not incoming or not stored or incoming == stored:
        return stored or incoming
    if fbc_fbclid(stored) and fbc_fbclid(stored) == fbc_fbclid(incoming):
        return stored
    new, old = click_time(incoming), click_time(stored)
    if new is None:
        return stored
    return incoming if old is None or new > old else stored


def landing_page(params: dict) -> tuple[str, bool]:
    """(lp, ids_stripped) for an ad arrival: its lp parameter, else 'listicle'
    with ids_stripped when a Meta ad click (it carried Meta's click id) arrived
    with utm tags but no ad, ad set or campaign id (the old listicle forwarded
    utm_source=fb/ig and dropped the ids). Tags without a click id (a link
    someone typed or shared by hand) and other sources' tags (Shopify's own
    links, email) are not a listicle and lost nothing."""
    params = utm_ids(params)
    if is_organic(params):
        return "", False                            # stored before organic links were told apart
    lp = str(params.get("lp") or "").strip()[:100]
    stripped = bool(params.get("ids_stripped"))
    if lp:
        return lp, stripped
    if (params.get("fbclid") == "1" and _low(params.get("utm_source")) in META_SOURCES
            and any(params.get(k) for k in UTM_KEYS) and not any(params.get(k) for k in ID_KEYS)):
        return LISTICLE, True
    return "", False


def with_landing(params: dict) -> dict:
    """An arrival's ad parameters plus the landing page it came through."""
    lp, stripped = landing_page(params)
    out = dict(params)
    if lp:
        out["lp"] = lp
    if stripped:
        out["ids_stripped"] = True
    return out


def is_meta_id(s: Any) -> bool:
    """A Meta object id (15 to 20 digits today), not a name that happens to be
    a number, like an ad called "2"."""
    s = str(s or "").strip()
    return s.isdigit() and len(s) >= 10


def utm_ids(params: dict) -> dict:
    """A link whose URL template put the ids in the utm tags
    (utm_content={{ad.id}}, utm_term={{adset.id}}, utm_campaign={{campaign.id}},
    the listicle campaign's ads) with those ids under their own names, so the
    ad is known by its id (and named from Meta) instead of looking like tags
    that lost their ids. Anything else is returned as it is."""
    if not isinstance(params, dict) or params.get("ad_id"):
        return params
    content, term, camp = (str(params.get(k) or "").strip() for k in ("utm_content", "utm_term", "utm_campaign"))
    if not (is_meta_id(content) and (is_meta_id(term) or is_meta_id(camp))):
        return params
    out = {k: v for k, v in params.items() if k not in ("utm_content", "utm_term", "ids_stripped")}
    out["ad_id"] = content
    if is_meta_id(term) and not out.get("adset_id"):
        out["adset_id"] = term
    if is_meta_id(camp):
        out.pop("utm_campaign", None)
        out.setdefault("campaign_id", camp)
    return out


def link_names(params: dict) -> dict:
    """The ad and ad set names an ad link carried, for when Meta's aren't
    known. Today's templates put the ad in utm_term and its ad set in
    utm_content; the oldest put the ad in utm_content and the ad set's id in
    utm_term. A single name is taken as the ad's."""
    content = str(params.get("utm_content") or "").strip()[:300]
    term = str(params.get("utm_term") or "").strip()[:300]
    if content and term and not is_meta_id(term):
        return {"ad_name": term, "adset_name": content}
    return {"ad_name": content or ("" if is_meta_id(term) else term), "adset_name": ""}


# --- click history ----------------------------------------------------------------

def ad_visit(params: dict, at: float, fbclid: str = "") -> Optional[dict]:
    """One Meta ad arrival for a browser's click history, or None when the link
    doesn't say which ad (an fbclid alone): that can't be named as an assist."""
    params = utm_ids(params)
    visit = {"ad_id": params.get("ad_id", ""), **link_names(params),
             "campaign_name": params.get("utm_campaign", ""), "at": float(at)}
    if not (visit["ad_id"] or visit["ad_name"]):
        return None
    lp, stripped = landing_page(params)
    if lp:
        visit["lp"] = lp
    if stripped:
        visit["ids_stripped"] = True
    if params.get("ref"):
        visit["ref"] = str(params["ref"])[:100]
    if fbclid:
        visit["click"] = click_key(fbclid)
    return visit


def ad_history(raw: Any) -> list[dict]:
    """A stored click history (JSON text or a list), oldest first. A malformed
    entry is dropped rather than breaking the attribution of a sale."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw else []
        except ValueError:
            return []
    out = []
    for v in raw if isinstance(raw, list) else []:
        if not isinstance(v, dict):
            continue
        at = _float(v.get("at"))
        if at is None:
            continue
        visit = {**{k: str(v.get(k) or "")[:300] for k in VISIT_KEYS}, "at": at}
        for k in ("lp", "ref", "click"):
            if v.get(k):
                visit[k] = str(v[k])[:100]
        if not visit["ad_id"] and _low(visit["ad_name"]) in ORGANIC_CONTENT:
            continue                                # an organic bio link stored as an ad arrival
        if not visit["ad_id"] and is_meta_id(visit["ad_name"]):
            # Written before utm_ids: the ad's id was kept as its name (and the ad set's as nothing).
            visit["ad_id"], visit["ad_name"] = visit["ad_name"], ""
            if is_meta_id(visit["campaign_name"]):
                visit["campaign_name"] = ""
        elif v.get("ids_stripped"):
            visit["ids_stripped"] = True
        out.append(visit)
    out.sort(key=lambda v: v["at"])
    return out


def same_ad(a: dict, b: dict) -> bool:
    """By ad id when both carry one, else by ad name (ignoring case)."""
    if a.get("ad_id") and b.get("ad_id"):
        return str(a["ad_id"]) == str(b["ad_id"])
    name = str(a.get("ad_name") or "").strip().lower()
    return bool(name) and name == str(b.get("ad_name") or "").strip().lower()


def add_ad_visit(raw: Any, visit: dict) -> list[dict]:
    """The click history with one more arrival, newest HISTORY_MAX kept. The
    same ad again within REPEAT_SECONDS (a reload, the next page, several
    pixel events for one page) is the same visit and is not added twice."""
    history = ad_history(raw)
    if any(same_ad(v, visit) and abs(visit["at"] - v["at"]) < REPEAT_SECONDS for v in history):
        return history
    history.append(visit)
    history.sort(key=lambda v: v["at"])
    return history[-HISTORY_MAX:]


# --- Meta's names (R7) --------------------------------------------------------------

_index: list = [None, {}]                       # [the catalog list, its indexes]


def _indexes(catalog: Optional[list]) -> dict:
    """The catalog's ads by id, by campaign id and by campaign name. The hub
    resolves hundreds of orders against one catalog, so it is indexed once."""
    if not catalog:
        return {"by_id": {}, "by_cid": {}, "by_cname": {}, "cname": {}}
    if _index[0] is not catalog:
        ix: dict = {"by_id": {}, "by_cid": {}, "by_cname": {}, "cname": {}}
        for r in catalog:
            cid, name = str(r.get("campaign_id") or ""), _low(r.get("campaign_name"))
            if r.get("ad_id"):
                ix["by_id"][str(r["ad_id"])] = r
            if cid:
                ix["by_cid"].setdefault(cid, []).append(r)
                ix["cname"].setdefault(cid, name)
            if name:
                ix["by_cname"].setdefault(name, []).append(r)
        _index[:] = [catalog, ix]
    return _index[1]


def _in_campaign(catalog: list[dict], campaign_id: Any, campaign_name: Any) -> list[dict]:
    """The catalog's ads in a link's campaign: by its id, and by its name for
    ads known without one (named by id only). The whole catalog without either."""
    ix = _indexes(catalog)
    cid, cname = str(campaign_id or "").strip(), _low(campaign_name)
    if cid and not cname:
        cname = ix["cname"].get(cid, "")
    if not (cid or cname):
        return catalog
    out = list(ix["by_cid"].get(cid, [])) if cid else []
    out += [r for r in ix["by_cname"].get(cname, []) if not cid or not r.get("campaign_id")]
    return out


def resolve_names(ad: dict, catalog: Optional[list]) -> Optional[dict]:
    """The one Meta ad a names-only link means, or None. Inside the link's
    campaign (by id, else by name), it tries ad set = the first name with ad =
    the second, and the swap; it resolves only when exactly one ad matches."""
    if not catalog:
        return None
    first, second = ad.get("pair") or ("", "")
    if not (first or second):
        return None
    rows = _in_campaign(catalog, ad.get("campaign_id"), ad.get("campaign_name"))
    if ad.get("adset_id"):
        rows = [r for r in rows if str(r.get("adset_id") or "") == str(ad["adset_id"])]
    found: dict[str, dict] = {}
    for adset, name in ((first, second), (second, first)):
        for r in rows:
            if not r.get("ad_id") or not (adset or name):
                continue
            if name and _low(r.get("ad_name")) != _low(name):
                continue
            if adset and _low(r.get("adset_name")) != _low(adset):
                continue
            found[str(r["ad_id"])] = r
    return next(iter(found.values())) if len(found) == 1 else None


def _ad_from_params(p: dict) -> dict:
    p = utm_ids(p)
    content = str(p.get("utm_content") or "").strip()[:300]
    term = str(p.get("utm_term") or "").strip()[:300]
    legacy = is_meta_id(term)                # the oldest links carried the ad set's id in utm_term
    lp, stripped = landing_page(p)
    return {"ad_id": str(p.get("ad_id") or ""), "adset_id": str(p.get("adset_id") or (term if legacy else "")),
            "campaign_id": str(p.get("campaign_id") or p.get("utm_id") or ""),
            **link_names(p), "campaign_name": str(p.get("utm_campaign") or "")[:300],
            "pair": ("", content) if legacy else (content, term), "lp": lp, "ids_stripped": stripped}


def _ad_from_visit(v: dict) -> dict:
    return {"ad_id": v.get("ad_id") or "", "adset_id": "", "campaign_id": "", "ad_name": v.get("ad_name") or "",
            "adset_name": v.get("adset_name") or "", "campaign_name": v.get("campaign_name") or "",
            "pair": (v.get("adset_name") or "", v.get("ad_name") or ""), "lp": v.get("lp") or "",
            "ids_stripped": bool(v.get("ids_stripped"))}


def identify(ad: dict, catalog: Optional[list]) -> dict:
    """An ad's ids and names as Meta knows them: by its id, else by its link's
    names (resolve_names). Unknown ids are None; names Meta couldn't pin to
    one ad are kept as the link gave them, with ambiguous set."""
    out = {k: ad.get(k) or "" for k in ("ad_id", "adset_id", "campaign_id", "ad_name", "adset_name",
                                         "campaign_name")}
    row = _indexes(catalog)["by_id"].get(out["ad_id"]) if out["ad_id"] else resolve_names(ad, catalog)
    if row:
        for k in out:
            out[k] = str(row.get(k) or "") or out[k]
    named = any(ad.get("pair") or ())
    out["ambiguous"] = not out["ad_id"] and named
    for k in ("ad_id", "adset_id", "campaign_id"):
        out[k] = out[k] or None
    return out


# --- candidates ---------------------------------------------------------------------

def _cand(source: str, at: Optional[float], ad: dict, fbclid: str = "", fbc: str = "") -> dict:
    return {"source": source, "at": at, "ad": ad, "fbclid": fbclid,
            "fbc": fbc or (make_fbc(fbclid, at) if fbclid and at is not None else "")}


def _params(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        p = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return p if isinstance(p, dict) else {}


def _session_candidates(sess: dict) -> list[dict]:
    """The storefront session's ad clicks: its current one (the newest), the
    arrivals in its click history, and an fbc cookie that belongs to neither."""
    fbc = str(sess.get("fbc") or "")
    fbclid = fbc_fbclid(fbc)
    fbc_at = click_time(fbc)
    key = click_key(fbclid)
    out, tied = [], False
    params = _params(sess.get("ad_params"))
    if is_organic(params):
        params = {}                                 # an organic bio link stored as the current click
    seen = _float(sess.get("ad_seen_at"))
    current = None
    history = ad_history(sess.get("ad_history"))
    if params and seen is not None:
        # The same arrival set both (tracking.ingest_pixel_event). Its history
        # entry knows which click it was: another click minutes later (#c3744:
        # ad 6, then BOF 7 minutes on, whose page view the pixel reported only
        # after the sale) is not it. Older rows only have the moments to go by.
        own = next((v for v in history if abs(v["at"] - seen) < 0.001 and v.get("click")), None)
        same = (params.get("fbclid") == "1" and fbc_at is not None and abs(fbc_at - seen) <= REPEAT_SECONDS
                and (own is None or own["click"] == key))
        tied = tied or same
        current = _cand("browser", seen, _ad_from_params(params), fbclid if same else "", fbc if same else "")
        out.append(current)
    for v in history:
        if current and abs(v["at"] - seen) < 0.001 and same_ad(v, current["ad"]):
            continue                                    # the current click's own entry, already listed
        same = bool(key) and v.get("click") == key
        tied = tied or same
        out.append(_cand("browser", v["at"], _ad_from_visit(v), fbclid if same else "", fbc if same else ""))
    if fbclid and not tied:
        out.append(_cand("click_id", fbc_at, _ad_from_params({}), fbclid, fbc))
    return out


def _journey_candidate(visit: Any) -> Optional[dict]:
    """Shopify's last visit, when it came from a Meta ad."""
    if not isinstance(visit, dict):
        return None
    url = visit.get("landingPage") or ""
    params = ad_params_from_url(url)
    utm = visit.get("utmParameters") if isinstance(visit.get("utmParameters"), dict) else {}
    tags = {f"utm_{k}": str(utm[k]).strip()[:300] for k in ("source", "medium", "campaign", "content", "term")
            if utm.get(k)}
    if not params and _low(tags.get("utm_source")) not in META_SOURCES:
        return None
    params = {**tags, **params}
    at = parse_time(visit.get("occurredAt"))
    return _cand("shopify_last_visit", at, _ad_from_params(params), fbclid_of(url))


def note_attributes(order: dict) -> dict:
    return {str(a.get("name") or "").strip()[:64]: str(a.get("value") or "").strip()[:300]
            for a in (order.get("note_attributes") or []) if isinstance(a, dict)}


def _note_candidate(order: dict) -> Optional[dict]:
    """The last click the old tracker wrote on the order, when it was a Meta ad."""
    attrs = note_attributes(order)
    fbc = attrs.get("fbc", "")
    fbclid = fbc_fbclid(fbc) if FBCLID_RE.fullmatch(fbc_fbclid(fbc)) else ""
    params = {k: attrs[k] for k in AD_KEYS if attrs.get(k)}
    if fbclid:
        params["fbclid"] = "1"                      # presence only, like ad_params_from_url
    if not (fbclid or params.get("ad_id") or _low(params.get("utm_source")) in META_SOURCES):
        return None
    return _cand("order_note", click_time(fbc) if fbclid else None, _ad_from_params(params), fbclid,
                 fbc if fbclid else "")


def _same_page(landing: Any, other: Any) -> bool:
    fa, fb = fbclid_of(landing), fbclid_of(other)
    if fa or fb:
        # Shopify's landing_site can end with a cut fbclid; the full page begins with it.
        return fa == fb or (bool(fa) and cut_short(landing) and _cut_of(fa, fb))
    try:
        pa, pb = urlparse(str(landing or "")), urlparse(str(other or ""))
    except ValueError:
        return False
    return pa.path.rstrip("/") == pb.path.rstrip("/") and parse_qs(pa.query) == parse_qs(pb.query)


def _first_visit(order: dict, journey: dict, sess: dict, cands: list[dict]) -> Optional[dict]:
    """The buyer's first landing page, when it was a Meta ad, with when it
    happened if Shopify's visit record, the pixel or another record of the
    same click (same_click) knows. When Shopify cut the page's fbclid short
    and a record of the same click has all of it, the whole one is kept."""
    first = journey.get("firstVisit") if isinstance(journey.get("firstVisit"), dict) else {}
    landing = str(order.get("landing_site") or "")
    url = landing or str(first.get("landingPage") or "")
    params = ad_params_from_url(url)
    if not params:
        return None
    c = _cand("first_visit_unverified", None, _ad_from_params(params), fbclid_of(url))
    c["cut"] = bool(c["fbclid"]) and cut_short(url)
    c["from"] = "landing_site" if landing else "shopify_first_visit"
    fbclid = c["fbclid"]
    at = None
    if first.get("occurredAt") and (not landing or _same_page(landing, first.get("landingPage"))):
        at = parse_time(first["occurredAt"])
    twin = None                                 # another record of this click
    if fbclid and sess:
        fbc = str(sess.get("fbc") or "")
        mine = {"fbclid": fbc_fbclid(fbc), "at": click_time(fbc)}
        # The browser's click names its ad when the pixel saw that ad's link.
        mine["ad"] = next((x["ad"] for x in cands if mine["fbclid"] and x["fbclid"] == mine["fbclid"]), {})
        if same_click({**c, "at": at}, mine):
            twin, at = mine, (at if at is not None else mine["at"])
        elif at is None:
            at = next((v["at"] for v in ad_history(sess.get("ad_history")) if v.get("click") == click_key(fbclid)),
                      None)
    if fbclid and (at is None or twin is None):
        # The old tracker's note or Shopify's last visit can carry this very click, with its time.
        x = next((x for x in cands if x["at"] is not None and same_click({**c, "at": at}, x)), None)
        if x is not None:
            twin, at = twin or x, (at if at is not None else x["at"])
    if twin is not None and len(twin["fbclid"]) > len(fbclid):
        c.update(fbclid=twin["fbclid"], cut=False)
    if at is not None:
        c.update(source="first_visit", at=at, fbc=make_fbc(c["fbclid"], at) if c["fbclid"] else "")
    return c


# --- channels (R8) ----------------------------------------------------------------------

def _own_host(host: str) -> bool:
    store = _host(config.STORE_URL)
    return (bool(store) and host == store) or host.endswith("myshopify.com")


# The in-app browsers of Meta's apps (Facebook on Android and iOS, Instagram).
IN_APP_BROWSERS = ("FB_IAB", "FBAN/", "FBAV/", "FBIOS", "Instagram")


def in_app_browser(user_agent: Any) -> bool:
    ua = str(user_agent or "")
    return any(m in ua for m in IN_APP_BROWSERS)


def os_family(user_agent: Any) -> str:
    """'android', 'ios' or '' from a browser's user agent."""
    ua = str(user_agent or "").lower()
    if "android" in ua:
        return "android"
    if "iphone" in ua or "ipad" in ua or "ipod" in ua:
        return "ios"
    return ""


def ip_block(ip: Any) -> str:
    """The network an address sits in, as a prefix: a /24 for IPv4, a /64 for
    IPv6 ('' for anything else). A phone's address changes within it."""
    ip = str(ip or "").strip()
    if ip.count(".") == 3 and ":" not in ip:
        return ip.rsplit(".", 1)[0] + "."
    if ":" in ip:
        parts = ip.split("::")[0].split(":")
        return ":".join(parts[:4]) + ":" if len(parts) >= 4 else ""
    return ""


def referrer_host(url: Any) -> str:
    """The site that sent a shopper (host only, never a path), or "" for the store itself."""
    host = _host(url)
    return "" if not host or _own_host(host) else host


def _channel(q: dict, referrer: Any = "") -> str:
    """The channel one visit came from, or "" when it says nothing (or was a
    Meta ad, which the candidates already weighed)."""
    src, med = _low(q.get("utm_source")), _low(q.get("utm_medium"))
    host = _host(referrer)
    if src in SHOP_APP_SOURCES:
        return "Shop app ads"
    if q.get("gclid") or q.get("gbraid") or q.get("wbraid") or src in GOOGLE_SOURCES or re.search(
            r"(^|\.)google\.", host):
        return "Google"
    if q.get("ttclid") or src in TIKTOK_SOURCES or re.search(r"(^|\.)tiktok\.com$", host):
        return "TikTok"
    if src in EMAIL_SMS or med in EMAIL_SMS:
        return "Email or SMS"
    if q.get("fbclid") or src in META_SOURCES:
        return ""
    if host and not _own_host(host):
        return f"Other referral ({host})"
    if src:
        return f"Other referral ({src[:60]})"
    return ""


def channel_of(order: dict, journey: Optional[dict] = None) -> str:
    """Where a sale no Meta click got came from: the newest visit that says,
    Shopify's last visit first, then the old tracker's note, then the first
    landing page. 'Direct' when none does."""
    journey = journey if isinstance(journey, dict) else {}
    last = journey.get("lastVisit") if isinstance(journey.get("lastVisit"), dict) else {}
    first = journey.get("firstVisit") if isinstance(journey.get("firstVisit"), dict) else {}
    utm = last.get("utmParameters") if isinstance(last.get("utmParameters"), dict) else {}
    seen = [
        ({**_query(last.get("landingPage")), **{f"utm_{k}": v for k, v in utm.items() if v}}, last.get("referrerUrl")),
        (note_attributes(order), ""),
        (_query(order.get("landing_site")), order.get("referring_site")),
        (_query(first.get("landingPage")), first.get("referrerUrl")),
    ]
    for q, ref in seen:
        found = _channel(q, ref)
        if found:
            return found
    return "Direct"


# --- the resolver ---------------------------------------------------------------------

def order_time(order: dict) -> float:
    return parse_time(order.get("created_at")) or time.time()


def _visit_of(c: dict, catalog: Optional[list]) -> Optional[dict]:
    """A candidate as an assist entry, or None when it names no ad."""
    ad = identify(c["ad"], catalog)
    visit = {"ad_id": ad["ad_id"] or "", "ad_name": ad["ad_name"], "adset_name": ad["adset_name"],
             "campaign_name": ad["campaign_name"], "at": c["at"]}
    if not (visit["ad_id"] or visit["ad_name"]):
        return None
    if c["ad"].get("lp"):
        visit["lp"] = c["ad"]["lp"]
    return visit


def resolve(order: dict, sess: Optional[dict] = None, journey: Optional[dict] = None,
            catalog: Optional[list] = None) -> dict:
    """Decide a sale's last click. Returns {"attribution": the record stored on
    the order, "fbc": the click Meta is sent (or "")}.

    `sess` is the buyer's storefront session (or {}), `journey` Shopify's
    customerJourneySummary for the order (or None), `catalog` the Meta ads
    names are matched against (meta_ads.ad_catalog)."""
    order_ts = order_time(order)
    window = config.ATTRIBUTION_WINDOW_DAYS * 86400
    sess = sess if isinstance(sess, dict) else {}
    journey = journey if isinstance(journey, dict) else {}
    cands = [c for c in (*_session_candidates(sess), _journey_candidate(journey.get("lastVisit")),
                         _note_candidate(order)) if c]
    first = _first_visit(order, journey, sess, cands)

    def in_window(c: dict) -> bool:
        return c["at"] is not None and order_ts - window <= c["at"] <= order_ts + CLOCK_SKEW
    timed = sorted((c for c in cands if in_window(c)), key=lambda c: (-c["at"], SOURCE_RANK[c["source"]]))
    untimed = [c for c in cands if c["at"] is None]
    # A click known to come before the sale, even one older than the window:
    # the first landing page came before it, so it is at least that old.
    before_sale = any(c["at"] is not None and c["at"] <= order_ts + CLOCK_SKEW for c in cands)
    if timed:
        winner = timed[0]
    elif first and in_window(first):
        winner = first
    elif untimed:
        winner = untimed[0]
    elif first and first["at"] is None and not before_sale:
        winner = first                           # nothing else at all: the first visit, unverified
    else:
        winner = None
    won_ad = winner["ad"] if winner else {}
    if winner and winner["fbclid"] and not identify(won_ad, catalog)["ad_id"]:
        # Another record of the same click can know the ad the winner didn't:
        # the first landing page too, when it carries the same fbclid (or
        # Shopify's cut of it). Never when the winner's own names find its ad.
        won_ad = next((c["ad"] for c in [*cands, *([first] if first else [])]
                       if c["ad"]["ad_id"] and same_click(c, winner)), won_ad)

    # The click Meta hears about: the winner's, else the newest real click in
    # the window, else an older one with its real time (a match key; Meta
    # applies its own window). Never the first visit stamped "now" unless it won.
    fbc = ""
    if winner and winner["fbclid"]:
        fbc = winner["fbc"] or make_fbc(winner["fbclid"], winner["at"] if winner["at"] is not None else order_ts)
    else:
        clicked = [c for c in timed if c["fbclid"]]
        older = sorted((c for c in [*cands, *([first] if first else [])]
                        if c["fbclid"] and c["at"] is not None and c["at"] <= order_ts + CLOCK_SKEW),
                       key=lambda c: -c["at"])
        pick = clicked[0] if clicked else (older[0] if older else None)
        fbc = pick["fbc"] if pick else ""

    record: dict[str, Any] = {"v": RESOLVER_VERSION, "meta": bool(winner),
                              "source": winner["source"] if winner else "",
                              "click": bool(winner and winner["fbclid"])}
    if winner:
        record.update(identify(won_ad, catalog), click_at=winner["at"], lp=won_ad["lp"],
                      ids_stripped=won_ad["ids_stripped"], channel=META_CHANNEL)
    else:
        record.update({k: None for k in ("ad_id", "adset_id", "campaign_id")}, ad_name="", adset_name="",
                      campaign_name="", ambiguous=False, click_at=None, lp="", ids_stripped=False,
                      channel=channel_of(order, journey))
    # A link without ids (an old landing page, Shopify's first visit) names its
    # ad the way the ad's link did then; when another click of this buyer's
    # carried the same link names and the ad's id, it is that ad. #c3737:
    # "MOF 3 - Copy" in B1 VSL is BOF, renamed since, so BOF can't assist itself.
    by_link: dict[tuple, str] = {}
    for c in [*cands, *([first] if first else [])]:
        if c["ad"].get("ad_id") and (c["ad"].get("ad_name") or c["ad"].get("adset_name")):
            by_link.setdefault((_low(c["ad"].get("adset_name")), _low(c["ad"].get("ad_name"))), str(c["ad"]["ad_id"]))

    def with_id(ad: dict) -> dict:
        found = "" if ad.get("ad_id") else by_link.get((_low(ad.get("adset_name")), _low(ad.get("ad_name"))), "")
        return {**ad, "ad_id": found} if found else ad
    record["first_touch"] = None
    if first:
        record["first_touch"] = {**identify(with_id(first["ad"]), catalog), "at": first["at"], "lp": first["ad"]["lp"],
                                 "ids_stripped": first["ad"]["ids_stripped"], "from": first["from"]}

    # Assists: the other ads clicked in the window before the winning click,
    # newest first, each once; then the first-visit ad when it isn't the seller.
    latest = winner["at"] if winner and winner["at"] is not None else order_ts
    seller = record if winner else {}
    helped: list[dict] = []
    for c in sorted((c for c in cands if c is not winner and in_window(c) and c["at"] <= latest),
                    key=lambda c: -c["at"]):
        if winner and winner["fbclid"] and c["source"] != "browser" and same_click(c, winner):
            continue                                # Shopify's or the old tracker's record of the winning click
        v = _visit_of({**c, "ad": with_id(c["ad"])}, catalog)
        if v and not same_ad(v, seller) and not any(same_ad(v, h) for h in helped):
            helped.append(v)
    ft = record["first_touch"]
    extra = None
    if ft and (ft["ad_id"] or ft["ad_name"]) and winner is not first and not same_ad(ft, seller):
        extra = {"ad_id": ft["ad_id"] or "", "ad_name": ft["ad_name"], "adset_name": ft["adset_name"],
                 "campaign_name": ft["campaign_name"], "at": ft["at"], "first_touch": True}
        helped = [h for h in helped if not same_ad(h, extra)]
    helped = helped[:ASSISTS_MAX - (1 if extra else 0)] + ([extra] if extra else [])
    record["assists"] = helped
    return {"attribution": record, "fbc": fbc}


# --- naming a click the tracker already sent (#c3711) ----------------------------------

# What names a sale's ad. Nothing here reaches Meta: the Purchase carried only the fbc.
IDENTITY_KEYS = ("ad_id", "adset_id", "campaign_id", "ad_name", "adset_name", "campaign_name", "lp",
                 "ids_stripped")


def needs_identity(rec: Any) -> bool:
    """A sale the tracker sent to Meta (its record keeps the fbc) as a bare
    click: the browser kept only the _fbc cookie, so the record names no ad."""
    return (isinstance(rec, dict) and bool(rec.get("meta")) and bool(rec.get("fbc"))
            and rec.get("source") in ("click_id", "browser") and not rec.get("ad_id") and not rec.get("ad_name"))


def sent_click_identity(rec: dict, order: dict, journey: Optional[dict] = None,
                        catalog: Optional[list] = None) -> Optional[dict]:
    """The ad of the click the tracker already sent, when another record proves
    it is that very click: Shopify's last visit or the order's landing page
    carrying the same fbclid, or a landing page Shopify cut short that passes
    same_click's strict rules. Returns only the IDENTITY_KEYS (and
    identity_refreshed), or None. The fbc, source and click time stay what
    Meta was sent."""
    if not needs_identity(rec):
        return None
    fbc = str(rec["fbc"])
    sent = {"fbclid": fbc_fbclid(fbc), "at": click_time(fbc), "ad": {}}
    if not sent["fbclid"]:
        return None
    journey = journey if isinstance(journey, dict) else {}
    cands = [c for c in (_journey_candidate(journey.get("lastVisit")),) if c]
    first = _first_visit(order, journey, {}, cands)
    for c in [*cands, *([first] if first else [])]:
        if not c["fbclid"]:
            continue
        if c["fbclid"] != sent["fbclid"] and not (c.get("cut") and same_click(c, sent)):
            continue
        ad = identify(c["ad"], catalog)
        if not (ad["ad_id"] or ad["ad_name"]):
            continue
        return {**{k: ad[k] for k in IDENTITY_KEYS[:6]}, "lp": c["ad"]["lp"],
                "ids_stripped": c["ad"]["ids_stripped"], "identity_refreshed": True}
    return None


_SEPARATORS = " -\u2013\u2014|:_"             # space, hyphen, en/em dash, pipe, colon, underscore
# The variant word must start a word ((?<![^\W_]): not right after a letter or
# digit), so "UGC Brad 2" stays whole instead of becoming "UGC Br" + "ad 2".
_VARIANT = re.compile(r"[\s\-\u2013\u2014|:_]*(?:(?<![^\W_])(?:ad|v|var|variation|version)|#)\s*\d+\s*$",
                      re.IGNORECASE)


def family(ad_name: str) -> str:
    """Group creatives by batch: 'B2 Statics - Ad 3' -> 'B2 Statics'."""
    name = _VARIANT.sub("", ad_name or "").strip(_SEPARATORS)
    return name or (ad_name or "")
