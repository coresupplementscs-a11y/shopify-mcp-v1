"""The Agents page: how Marwan's three AI agents perform and whether each one
is getting better.

The agents push; the hub never reaches into his PC. POST /api/agents/report
(bearer AGENT_REPORT_TOKEN) stores one report per key in agent_reports:

  video-editor / movie  video_shipped   one per shipped video (key: its video_id)
                        model_audit     keeper rate by model and shot (key: its date)
  strategist            nightly_run     calls made and graded (key: its date)

A resend with the same key replaces the stored report, so an agent can fix a
report by sending it again.

GET /hub/api/agents (the hub's login) turns the reports into the page: a card
per agent, weekly trends, last 30 days vs the 30 before, each video's ad
results and each strategist call's results. Results are the hub's own per-ad
numbers (Meta's delivery, the tracker's store sales: one sale, one row, an
assist never a sale), the same numbers the Core Creative Hub syncs into its
ad_daily. A video's ads are its ad_ids when it sends them, else its exact ad
names; a call's ads are the ads whose names contain its SC-ID. Each ad links
to the Creative Hub test that holds it (that app's tests list their Meta ad
ids). Read-only towards Meta and Shopify: nothing here changes an ad.
"""
import datetime as dt
import hmac
import json
import logging
import re
import time
from typing import Any, Optional

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

import config
import db

log = logging.getLogger("tracker.agents")

AGENTS = {"strategist": "Creative strategist", "video-editor": "Video editor", "movie": "Movie agent"}
VIDEO_AGENTS = ("video-editor", "movie")
EVENTS = {"strategist": ("nightly_run",), "video-editor": ("video_shipped", "model_audit"),
          "movie": ("video_shipped", "model_audit")}
MAX_BODY = 64_000
MAX_ID = 200
RESULTS_DAYS = 30                       # the hub's per-ad numbers reach back this far (hub.LEARN_DAYS)
WEEKS = 12
SC_ID = re.compile(r"\bSC-\d{4}-\d{1,3}\b", re.I)
GRADES = ("won", "lost", "open")
HUB_TESTS_TTL = 600

_client = httpx.AsyncClient(timeout=15)
_hub_tests: dict[str, Any] = {"at": 0.0, "by_ad": {}, "error": ""}


# --- the report endpoint ------------------------------------------------------------

def _authed(request: Request) -> bool:
    token = config.AGENT_REPORT_TOKEN
    auth = request.headers.get("authorization", "")
    if not token or not auth.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(auth[7:].strip().encode(), token.encode())


def _day(v: Any) -> Optional[str]:
    """A report's date as YYYY-MM-DD: the first 10 characters of an ISO date or time."""
    s = str(v or "").strip()[:10]
    try:
        return dt.date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def check(payload: Any) -> tuple[Optional[str], str]:
    """(the report's key, '') when the payload is a report this page takes, else (None, why not)."""
    if not isinstance(payload, dict):
        return None, "The body must be a JSON object."
    agent, event = payload.get("agent"), payload.get("event")
    if agent not in AGENTS:
        return None, f"agent must be one of: {', '.join(AGENTS)}."
    if event not in EVENTS[agent]:
        return None, f"{agent} sends: {', '.join(EVENTS[agent])}."
    day = _day(payload.get("date"))
    if not day:
        return None, "date must be a date (YYYY-MM-DD)."
    if event == "video_shipped":
        vid = str(payload.get("video_id") or "").strip()
        if not vid or len(vid) > MAX_ID:
            return None, f"video_id is required (at most {MAX_ID} characters)."
        names = payload.get("ad_names", [])
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            return None, "ad_names must be a list of ad names."
        return f"{agent}|{event}|{vid}", ""
    if event == "nightly_run":
        for field in ("calls_made", "calls_graded"):
            if not isinstance(payload.get(field, []), list):
                return None, f"{field} must be a list."
    return f"{agent}|{event}|{day}", ""


async def report(request: Request) -> JSONResponse:
    """POST /api/agents/report: store one agent report (see the module docstring)."""
    if not config.AGENT_REPORT_TOKEN:
        return JSONResponse({"ok": False, "error": "AGENT_REPORT_TOKEN isn't set on the server."}, 503)
    if not _authed(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, 401)
    body = await request.body()
    if len(body) > MAX_BODY:
        return JSONResponse({"ok": False, "error": f"The report is over {MAX_BODY // 1000} KB."}, 413)
    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse({"ok": False, "error": "The body isn't JSON."}, 400)
    key, why = check(payload)
    if not key:
        return JSONResponse({"ok": False, "error": why}, 400)
    db.save_agent_report(key, payload["agent"], payload["event"], _day(payload["date"]), json.dumps(payload))
    log.info("agent report %s", key)
    return JSONResponse({"ok": True, "key": key})


# --- numbers from the reports -------------------------------------------------------

def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _rate(v: Any) -> Optional[float]:
    """A rate as 0-1 whether the agent sent 0.9 or 90."""
    x = _num(v)
    if x is None:
        return None
    return x / 100 if x > 1 else x


def _mean(vals: list) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _week(day: str) -> str:
    d = dt.date.fromisoformat(day)
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def _video_stats(videos: list[dict]) -> dict:
    """The per-video averages the cards, trends and the 30-day comparison use."""
    n = len(videos)
    p = [v["p"] for v in videos]
    return {
        "videos": n,
        "keeper_rate": _mean([_rate(x.get("first_try_keeper_rate")) for x in p]),
        "redos": _mean([_num(x.get("redos")) for x in p]),
        "usd": _mean([_num(x.get("usd_spent")) for x in p]),
        "usd_wasted": _mean([_num(x.get("usd_wasted_on_redos")) for x in p]),
        "notes": _mean([_num(x.get("marwan_notes")) for x in p]),
        "rules_auto": sum(_num(x.get("rules_learned_auto")) or 0 for x in p) if n else None,
        "rules_marwan": sum(_num(x.get("rules_learned_marwan")) or 0 for x in p) if n else None,
        "minutes": _mean([_num(x.get("build_minutes")) for x in p]),
    }


def _calls(runs: list[dict]) -> dict[str, dict]:
    """Every strategist call by its SC-ID: the day it was made and its latest grade."""
    calls: dict[str, dict] = {}
    for r in runs:                                          # oldest run first: a later grade wins
        for cid in r["p"].get("calls_made") or []:
            cid = str(cid).strip().upper()
            if SC_ID.fullmatch(cid):
                calls.setdefault(cid, {"id": cid, "made": r["day"], "result": "open", "why": "", "graded": None})
        for g in r["p"].get("calls_graded") or []:
            if not isinstance(g, dict):
                continue
            cid = str(g.get("id") or "").strip().upper()
            if not SC_ID.fullmatch(cid):
                continue
            c = calls.setdefault(cid, {"id": cid, "made": None, "result": "open", "why": "", "graded": None})
            result = str(g.get("result") or "").lower()
            c.update(result=result if result in GRADES else "open", why=str(g.get("why") or "")[:500],
                     graded=r["day"])
    return calls


def _hit(calls: list[dict]) -> dict:
    won = sum(1 for c in calls if c["result"] == "won")
    lost = sum(1 for c in calls if c["result"] == "lost")
    return {"won": won, "lost": lost, "open": sum(1 for c in calls if c["result"] == "open"),
            "hit_rate": won / (won + lost) if won + lost else None}


def _between(day: Optional[str], first: dt.date, last: dt.date) -> bool:
    return bool(day) and first <= dt.date.fromisoformat(day) <= last


def _periods(today: dt.date) -> tuple[tuple, tuple]:
    last30 = (today - dt.timedelta(days=29), today)
    prior30 = (today - dt.timedelta(days=59), today - dt.timedelta(days=30))
    return last30, prior30


def _compare(label: str, now: Optional[float], before: Optional[float], higher_is_better: bool,
             fmt: str) -> dict:
    """One line of the 'Is it improving?' panel."""
    out = {"label": label, "last30": now, "prior30": before, "format": fmt, "direction": None, "better": None}
    if now is None or before is None:
        return out
    if abs(now - before) < 1e-9:
        out["direction"] = "flat"
        return out
    out["direction"] = "up" if now > before else "down"
    out["better"] = (now > before) == higher_is_better
    return out


def _weekly_video(videos: list[dict], weeks: list[str]) -> list[dict]:
    by: dict[str, list] = {}
    for v in videos:
        by.setdefault(_week(v["day"]), []).append(v)
    out = []
    for w in weeks:
        s = _video_stats(by.get(w, []))
        out.append({"week": w, **s})
    return out


def _weekly_calls(calls: list[dict], weeks: list[str]) -> list[dict]:
    by: dict[str, list] = {}
    for c in calls:
        if c["graded"] and c["result"] in ("won", "lost"):
            by.setdefault(_week(c["graded"]), []).append(c)
    return [{"week": w, **_hit(by.get(w, []))} for w in weeks]


def _weeks(today: dt.date) -> list[str]:
    monday = today - dt.timedelta(days=today.weekday())
    return [(monday - dt.timedelta(weeks=i)).isoformat() for i in range(WEEKS - 1, -1, -1)]


# --- results: the hub's per-ad numbers and the Creative Hub's tests ----------------

async def _ad_rows(since: str, until: str) -> tuple[list[dict], str]:
    """Every ad's numbers for [since, until] from the hub's creatives table (all ads)."""
    import hub
    query = f"since={since}&until={until}&all=1".encode()
    req = Request({"type": "http", "method": "GET", "path": "/hub/api/creatives", "query_string": query,
                   "headers": []})
    body = await hub.api_creatives(req)
    rows = [a for c in body.get("campaigns") or [] for g in c.get("groups") or [] for a in g.get("ads") or []]
    return rows, body.get("error") or ""


async def _creative_hub_tests() -> dict[str, dict]:
    """{Meta ad id: {"id", "name"}}: the Creative Hub test each ad belongs to. Cached 10 minutes."""
    if time.time() - _hub_tests["at"] < HUB_TESTS_TTL:
        return _hub_tests["by_ad"]
    by_ad: dict[str, dict] = {}
    try:
        r = await _client.get(f"{config.CREATIVE_HUB_API}/api/creative-tracker")
        r.raise_for_status()
        for t in r.json():
            label = " ".join(str(x) for x in (t.get("test_number"), t.get("name")) if x) or f"Test {t.get('id')}"
            for ad_id in t.get("ad_ids") or []:
                by_ad.setdefault(str(ad_id), {"id": t.get("id"), "name": label})
        _hub_tests.update(error="")
    except Exception as e:
        log.warning("agents: the Creative Hub's tests couldn't be read: %s", e)
        _hub_tests.update(error="The Creative Hub's tests couldn't be read, so its links are missing for now.")
    _hub_tests.update(at=time.time(), by_ad=by_ad)
    return by_ad


_CALL_ID = re.compile(r"^(SC|TOF)-\d{4}-[A-Z]?\d+", re.I)


def _call_id(ad_name: Any) -> str:
    """The strategist call an ad name starts with ('SC-1119-1a Buff Doge' -> 'SC-1119-1'), else ''."""
    m = _CALL_ID.match(str(ad_name or "").strip())
    return m.group(0).upper() if m else ""


def _call_link(call_id: str) -> str:
    # CREATIVE_URL already ends in /tracker
    return f"{config.CREATIVE_URL}?call={call_id}"


def _hub_link(test_id: Any) -> str:
    return f"{config.CREATIVE_URL}?test={test_id}" if test_id is not None else ""


def _results(ads: list[dict], tests: dict[str, dict]) -> dict:
    """The matched ads' numbers added up, and the Creative Hub tests they're in."""
    spend = sum(a.get("spend") or 0 for a in ads)
    imps = sum(a.get("impressions") or 0 for a in ads)
    links = sum(a.get("link_clicks") or 0 for a in ads)
    revenue = sum(a.get("store_revenue") or 0 for a in ads)
    seen, hub_tests = set(), []
    for a in ads:
        cid = _call_id(a.get("ad_name"))
        if cid:                                 # new Tracker: one link per strategist call
            if cid not in seen:
                seen.add(cid)
                hub_tests.append({"name": cid, "url": _call_link(cid)})
            continue
        t = tests.get(str(a.get("ad_id") or ""))
        if t and t["id"] not in seen:
            seen.add(t["id"])
            hub_tests.append({"name": t["name"], "url": _hub_link(t["id"])})
    return {
        "ads": [{"ad_id": a.get("ad_id"), "ad_name": a.get("ad_name"), "adset_name": a.get("adset_name")} for a in ads],
        "spend": round(spend, 2), "impressions": imps, "link_clicks": links,
        "ctr": round(links / imps * 100, 2) if imps else None,
        "cpc": round(spend / links, 2) if links else None,
        "add_to_carts": sum(a.get("meta_add_to_carts") or 0 for a in ads),
        "checkouts": sum(a.get("meta_checkouts") or 0 for a in ads),
        "purchases": sum(a.get("store_sales") or 0 for a in ads),
        "revenue": round(revenue, 2),
        "roas": round(revenue / spend, 2) if spend else None,
        "creative_hub": hub_tests,
    }


def _video_ads(p: dict, rows: list[dict]) -> list[dict]:
    """A video's ads: the ad ids it sent first, then every ad with one of its exact names."""
    ids = {str(x).strip() for x in p.get("ad_ids") or [] if str(x).strip()}
    names = {str(n).strip() for n in p.get("ad_names") or [] if str(n).strip()}
    return [a for a in rows if str(a.get("ad_id") or "") in ids or str(a.get("ad_name") or "").strip() in names]


# --- the page -----------------------------------------------------------------------

async def api(request: Request) -> dict:
    import hub
    today = hub._today()
    last30, prior30 = _periods(today)
    weeks = _weeks(today)
    reports = []
    for r in db.agent_reports():
        try:
            reports.append({**r, "p": json.loads(r["payload"])})
        except ValueError:
            continue
    since = (today - dt.timedelta(days=RESULTS_DAYS - 1)).isoformat()
    rows, rows_err = ([], "")
    if reports:
        try:
            rows, rows_err = await _ad_rows(since, today.isoformat())
        except Exception:
            log.exception("agents: the ad numbers couldn't be read")
            rows_err = "The ad numbers couldn't be read just now."
    tests = await _creative_hub_tests() if rows else {}

    agents, improving, trends = [], [], {}
    for key, name in AGENTS.items():
        mine = [r for r in reports if r["agent"] == key]
        if key == "strategist":
            runs = [r for r in mine if r["event"] == "nightly_run"]
            calls = list(_calls(runs).values())
            now = _hit([c for c in calls if _between(c["graded"], *last30)])
            before = _hit([c for c in calls if _between(c["graded"], *prior30)])
            lines = [_compare("Call hit rate", now["hit_rate"], before["hit_rate"], True, "pct"),
                     _compare("Calls won", now["won"] if runs else None, before["won"] if runs else None, True, "int"),
                     _compare("Calls lost", now["lost"] if runs else None, before["lost"] if runs else None, False, "int")]
            last_run = runs[-1] if runs else None
            agents.append({"key": key, "name": name, "reports": len(runs),
                           "last_report": last_run["day"] if last_run else None,
                           "headline": {"label": "Call hit rate, last 30 days", "value": now["hit_rate"],
                                        "format": "pct", "compare": lines[0]},
                           "stats": [{"label": "Won", "value": now["won"], "format": "int"},
                                     {"label": "Lost", "value": now["lost"], "format": "int"},
                                     {"label": "Open", "value": _hit(calls)["open"], "format": "int"}],
                           "top_angle": str((last_run or {"p": {}})["p"].get("top_angle") or "")[:120],
                           "reports_links": {k: v for k, v in ((last_run or {"p": {}})["p"].get("report_links") or {}).items()
                                             if k in ("daily", "weekly") and str(v).startswith("https://")}})
            trends[key] = _weekly_calls(calls, weeks)
            calls_out = []
            for c in sorted(calls, key=lambda c: (c["made"] or c["graded"] or "", c["id"]), reverse=True):
                built = [a for a in rows if c["id"].lower() in str(a.get("ad_name") or "").lower()]
                calls_out.append({**c, "results": _results(built, tests)})
        else:
            videos = [r for r in mine if r["event"] == "video_shipped"]
            audits = [r for r in mine if r["event"] == "model_audit"]
            now = _video_stats([v for v in videos if _between(v["day"], *last30)])
            before = _video_stats([v for v in videos if _between(v["day"], *prior30)])
            lines = [_compare("First-try keeper rate", now["keeper_rate"], before["keeper_rate"], True, "pct"),
                     _compare("$ per video", now["usd"], before["usd"], False, "usd"),
                     _compare("Marwan's notes per video", now["notes"], before["notes"], False, "num"),
                     _compare("Redos per video", now["redos"], before["redos"], False, "num"),
                     _compare("$ wasted on redos per video", now["usd_wasted"], before["usd_wasted"], False, "usd")]
            agents.append({"key": key, "name": name, "reports": len(videos),
                           "last_report": max((r["day"] for r in mine), default=None),
                           "headline": {"label": "First-try keeper rate, last 30 days", "value": now["keeper_rate"],
                                        "format": "pct", "compare": lines[0]},
                           "stats": [{"label": "$ per video", "value": now["usd"], "format": "usd", "compare": lines[1]},
                                     {"label": "Notes per video", "value": now["notes"], "format": "num",
                                      "compare": lines[2]},
                                     {"label": "Videos shipped", "value": now["videos"], "format": "int"}],
                           "audit": ({"day": audits[-1]["day"],
                                      "keeper_rate_by_model_and_shot": audits[-1]["p"].get("keeper_rate_by_model_and_shot") or {},
                                      "usd_per_keeper_by_model": audits[-1]["p"].get("usd_per_keeper_by_model") or {}}
                                     if audits else None)})
            trends[key] = _weekly_video(videos, weeks)
        improving.append({"key": key, "name": name, "lines": lines})

    video_rows = []
    for r in reversed([r for r in reports if r["event"] == "video_shipped"]):
        p = r["p"]
        video_rows.append({"agent": r["agent"], "agent_name": AGENTS[r["agent"]], "day": r["day"],
                           "video_id": p.get("video_id"), "title": str(p.get("title") or "")[:200],
                           "angle": str(p.get("angle") or "")[:40], "ad_names": p.get("ad_names") or [],
                           "results": _results(_video_ads(p, rows), tests)})
    return {
        "agents": agents, "improving": improving, "trends": trends, "weeks": weeks,
        "videos": video_rows, "calls": calls_out if reports else [],
        "results_since": since,
        "configured": bool(config.AGENT_REPORT_TOKEN),
        "error": " ".join(e for e in (rows_err, _hub_tests["error"] if rows else "") if e),
    }
