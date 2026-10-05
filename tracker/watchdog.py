"""
The watchdog: re-checks every link of the tracking chain every few minutes
and records the verdict, so the hub can say "all good" with proof.

Each check is ok, warn (worth a look, nothing lost) or fail (data at risk).
It also suggests fixes (proposals) that only happen once the owner approves
them in the hub: resending an order a pixel never got (to that pixel only),
counting a rebill-looking tag as MRR, and a heads-up about a landing page
that drops the ad ids.
"""
import datetime as dt
import json
import logging
import math
import time
from typing import Any, Optional

import config
import db
import meta_ads
import meta_capi
import pnl
import shopify
import tracking

log = logging.getLogger("tracker.watchdog")

RANK = {"ok": 0, "warn": 1, "fail": 2}
EMQ_REFRESH_SECONDS = 6 * 3600
# Shopify's visit history is asked about with a real order this often when no
# sale has asked recently.
JOURNEY_PROBE_SECONDS = 6 * 3600
# Warn when more of the week's new sales than this were credited from the
# buyer's first visit only (the tracker saw no later click).
FIRST_VISIT_SHARE_WARN = 0.20
# The P&L re-reads Shopify hourly, so an order is held up to it once it is this old.
PNL_SETTLE_SECONDS = 2 * 3600
# Statuses the P&L takes out of revenue or reduces by a refund: not compared.
PNL_SKIP_STATUSES = ("refunded", "partially_refunded", "voided")
# A failed order is worth a "send it?" proposal after this many tries (or 30 minutes).
RESEND_PROPOSAL_ATTEMPTS = 3
# webhook_good: the last answer Shopify actually gave, kept through a failed re-check.
_state = {"emq_at": 0.0, "webhook_at": 0.0, "webhook": None, "webhook_good": None}


def ago(ts: Optional[float]) -> str:
    if not ts:
        return "never"
    s = max(0, int(time.time() - ts))
    if s < 90:
        return "just now"
    if s < 5400:
        return f"{s // 60} min ago"
    if s < 172800:
        return f"{s // 3600} h ago"
    return f"{s // 86400} days ago"


def _c(cid: str, name: str, status: str, detail: str) -> dict:
    return {"id": cid, "name": name, "status": status, "detail": detail}


def worst(checks: list[dict]) -> str:
    return max((c["status"] for c in checks), key=lambda s: RANK[s], default="ok")


def pixel_name(pixel_id: str) -> str:
    """What the owner calls a dataset: META_PIXEL_NAME(_n) when set, else
    Meta's own name for it, else the default for its id (Core Club, Eczema),
    else its id."""
    main = pixel_id == config.META_PIXEL_ID
    return (config.PIXEL_NAME_OVERRIDES.get(pixel_id) or db.kv_get(f"pixel_name:{pixel_id}")
            or config.DEFAULT_PIXEL_NAMES.get(pixel_id) or ("Main pixel" if main else f"Pixel {pixel_id}"))


def pixel_label(pixel_id: str) -> str:
    return f"{pixel_name(pixel_id)} ({'main' if pixel_id == config.META_PIXEL_ID else 'backup'})"


def _score(entry: Any) -> Optional[float]:
    s = entry.get("score") if isinstance(entry, dict) else None
    ok = isinstance(s, (int, float)) and not isinstance(s, bool) and math.isfinite(s)
    return float(s) if ok else None


def emq_event(scores: Any) -> Optional[str]:
    """The event whose match quality speaks for a pixel: Purchase once Meta
    scores it, else the best-scored event, else None (nothing scored yet).
    The hub's quality card and this watchdog check both use it, so they agree."""
    scored = {ev: _score(v) for ev, v in (scores.items() if isinstance(scores, dict) else [])}
    scored = {ev: v for ev, v in scored.items() if v is not None}
    if not scored:
        return None
    return "Purchase" if "Purchase" in scored else max(scored, key=lambda ev: scored[ev])


async def _webhook_check() -> dict:
    if not config.PUBLIC_URL:
        return _c("webhook", "Instant order notifications", "warn",
                  "PUBLIC_URL is not set; new orders arrive by polling within a minute instead.")
    if _state["webhook"] is None or time.time() - _state["webhook_at"] > 3600:
        address = f"{config.PUBLIC_URL}/webhooks/shopify"
        try:
            resp = await shopify._request("GET", "webhooks.json", params={"topic": "orders/create", "limit": 250})
            found = any(w.get("address") == address for w in resp.json().get("webhooks", []))
            _state["webhook"] = _c("webhook", "Instant order notifications", "ok" if found else "warn",
                                   "Shopify notifies the tracker the moment an order is placed." if found else
                                   "Shopify isn't notifying the tracker; orders still arrive by polling within a minute.")
            _state["webhook_good"] = _state["webhook"]
            _state["webhook_at"] = time.time()
        except Exception as e:
            # One network blip must not pin this at "warn" for an hour: keep the
            # last real answer, if there is one, and ask again on the next run.
            _state["webhook"] = _state["webhook_good"] or _c(
                "webhook", "Instant order notifications", "warn",
                f"Couldn't check with Shopify ({type(e).__name__}).")
            _state["webhook_at"] = time.time() - 3600 + config.WATCHDOG_INTERVAL_SECONDS
    return _state["webhook"]


async def _orders_check(now: float) -> dict:
    name = "Every order accounted for"
    try:
        since = dt.datetime.fromtimestamp(now - 86400, dt.timezone.utc).isoformat(timespec="seconds")
        orders = await shopify.list_orders_since(since)
    except Exception as e:
        return _c("orders", name, "warn", f"Couldn't list today's orders from Shopify ({type(e).__name__}).")
    start = float(db.kv_get("tracking_start") or 0)
    stored = db.orders_by_id([str(o["id"]) for o in orders])
    missing, failed, stuck = [], [], []
    counts = {"sent": 0, "skipped": 0, "pending": 0}
    for o in orders:
        created = tracking._parse_time(o.get("created_at")) or now
        row = stored.get(str(o["id"]))
        if row is None:
            if created >= start and now - created > 900:
                missing.append(o.get("name") or str(o["id"]))
            continue
        if row["status"] == "failed":
            failed.append(row["order_name"] or row["order_id"])
        elif row["status"] == "pending" and now - row["received_at"] > 1800:
            stuck.append(row["order_name"] or row["order_id"])
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    if missing or failed or stuck:
        parts = []
        if missing:
            parts.append(f"not picked up: {', '.join(missing[:5])}")
        if failed:
            parts.append(f"failed to reach Meta (retrying): {', '.join(failed[:5])}")
        if stuck:
            parts.append(f"waiting over 30 min: {', '.join(stuck[:5])}")
        return _c("orders", name, "fail", "; ".join(parts))
    if not orders:
        return _c("orders", name, "ok", "No orders in the last 24 hours.")
    return _c("orders", name, "ok",
              f"{len(orders)} orders in 24 h: {counts['sent']} sent to Meta, {counts['skipped']} skipped on purpose"
              + (f", {counts['pending']} being processed" if counts.get("pending") else "") + ".")


async def _pnl_revenue_check(now: float) -> dict:
    """Every order of the last day, once the P&L has had time to read it, must
    be counted in the P&L at what the customer paid for the goods (Shopify's
    subtotal: after discounts, before shipping and tax). Bundle discounts once
    sat in a field the P&L didn't read and it counted every bundle order at full
    price for days. A mismatch fails the check and asks the P&L to re-read
    Shopify (at most hourly); its sync then sets each order to Shopify's subtotal."""
    name = "P&L revenue matches Shopify"
    try:
        since = dt.datetime.fromtimestamp(now - 86400, dt.timezone.utc).isoformat(timespec="seconds")
        orders = await shopify.list_orders_since(since)
        counted = await pnl.order_revenue()
    except pnl.PnlError as e:
        return _c("pnl_revenue", name, "warn", f"Couldn't read the P&L to compare: {e}")
    except Exception as e:
        return _c("pnl_revenue", name, "warn", f"Couldn't list the day's orders from Shopify ({type(e).__name__}).")
    off, missing, checked = [], [], 0
    for o in orders:
        created = tracking._parse_time(o.get("created_at")) or now
        if o.get("test") or o.get("cancelled_at") or o.get("financial_status") in PNL_SKIP_STATUSES                 or now - created < PNL_SETTLE_SECONDS or created <= counted["covers_since"]:
            continue
        label = o.get("name") or str(o.get("id"))
        try:
            shop = round(float(o.get("subtotal_price")), 2)
        except (TypeError, ValueError):
            continue
        ours = counted["orders"].get(label)
        checked += 1
        if ours is None:
            missing.append(label)
        elif abs(ours - shop) >= 0.02:
            off.append((label, ours, shop))
    if not off and not missing:
        if not checked:
            return _c("pnl_revenue", name, "ok", "No order old enough to compare yet.")
        return _c("pnl_revenue", name, "ok",
                  f"All {checked} orders of the last day are in the P&L at exactly Shopify's amount.")
    asked = await pnl.resync_shopify(dt.datetime.fromtimestamp(now - 2 * 86400, config.store_tz()).date().isoformat())
    parts = []
    if off:
        gap = sum(ours - shop for _, ours, shop in off)
        parts.append(f"{len(off)} order(s) counted at a different amount than Shopify "
                     f"({'over' if gap > 0 else 'under'} by ${abs(gap):,.2f}): "
                     + ", ".join(f"{n} ${a:,.2f} vs ${b:,.2f}" for n, a, b in off[:5]))
    if missing:
        parts.append(f"not in the P&L: {', '.join(missing[:5])}")
    parts.append({"sent": "asked the P&L to re-read Shopify", "recent": "the P&L was asked to re-read Shopify within the hour",
                  "failed": "couldn't reach the P&L to ask it to re-read Shopify"}[asked])
    return _c("pnl_revenue", name, "fail", "; ".join(parts) + ".")


def _delivery_check(pixel: dict, now: float) -> dict:
    pid = pixel["pixel_id"]
    stats = db.event_stats(now - 86400, pid)["by_event"]
    sent = sum(v.get("sent", 0) for v in stats.values())
    failed = sum(v.get("failed", 0) for v in stats.values())
    label = pixel_label(pid)
    last = db.last_sent_at(pid)
    if failed and failed > 0.05 * (failed + sent):
        return _c(f"pixel:{pid}", label, "fail",
                  f"{failed} of {failed + sent} events were rejected in 24 h. Check this pixel's access token.")
    if failed:
        return _c(f"pixel:{pid}", label, "warn", f"{sent} events accepted, {failed} rejected in 24 h.")
    if not sent:
        return _c(f"pixel:{pid}", label, "warn", "Nothing sent in the last 24 hours.")
    return _c(f"pixel:{pid}", label, "ok", f"{sent} events accepted by Meta in 24 h, last {ago(last)}.")


def _emq_check(pixel: dict) -> Optional[dict]:
    pid = pixel["pixel_id"]
    raw = db.kv_get(f"emq:{pid}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    scores = data.get("scores") or {}
    event = emq_event(scores)
    if event is None:
        return _c(f"emq:{pid}", f"Match quality · {pixel_label(pid)}", "ok",
                  "Meta hasn't scored this pixel yet.")
    score = _score(scores[event])
    status = "ok" if score >= 7 else "warn" if score >= 5 else "fail"
    return _c(f"emq:{pid}", f"Match quality · {pixel_label(pid)}", status,
              f"{event} scored {score:.1f}/10 by Meta ({ago(data.get('taken_at'))}).")


def _stripped_arrivals(now: float) -> tuple[int, list[dict]]:
    """(Meta ad arrivals in 24 h, those whose utm tags came without any ad id).
    Only real ad clicks count as stripped: a tagged link with no click id was
    typed or shared by hand, not an ad whose landing page lost the ids."""
    arrivals = db.ad_arrivals(now - 86400)
    return len(arrivals), [a for a in arrivals if a.get("ids_stripped") and a.get("click")]


def _where(stripped: list[dict]) -> list[str]:
    """The sites that sent the stripped arrivals, most first (when the pixel knows)."""
    hosts: dict[str, int] = {}
    for a in stripped:
        if a.get("ref"):
            hosts[a["ref"]] = hosts.get(a["ref"], 0) + 1
    return sorted(hosts, key=lambda h: -hosts[h])


def _stripped_check(now: float) -> dict:
    name = "Ad tags without an ad ID"
    total, stripped = _stripped_arrivals(now)
    if not stripped:
        return _c("stripped", name, "ok",
                  f"None of the {total} visits from Meta ads in 24 h lost their ad ID on the way." if total else
                  "No visit from a Meta ad in the last 24 hours.")
    hosts = _where(stripped)
    where = f" through {', '.join(hosts[:2])}" if hosts else ""
    return _c("stripped", name, "warn",
              f"{len(stripped)} of {total} visits from Meta ads in 24 h came{where} with the ad's name tags "
              "but no ad ID. Their sales are matched to ads by name, which is less exact. The landing page "
              "between the ad and the store should pass every link setting on.")


async def _journey_check(now: float) -> dict:
    name = "Shopify visit history"
    st = tracking.journey_status()
    if not st or now - float(st.get("at") or 0) > JOURNEY_PROBE_SECONDS:
        # No sale asked recently: ask about the newest order on record.
        recent = db.orders_since(now - 7 * 86400, ("sent", "skipped", "pending", "failed"))
        if recent:
            await tracking.journey_for(recent[-1]["order_json"])
            st = tracking.journey_status()
    if not st:
        return _c("journey", name, "ok", "Not checked yet: it is read with the next order.")
    if st.get("ok"):
        return _c("journey", name, "ok", "Shopify shares each buyer's visits, so a sale gets its last ad click "
                                         f"even when the storefront pixel missed it. Last read {ago(st.get('at'))}.")
    return _c("journey", name, "warn", str(st.get("detail") or "Shopify's visit history couldn't be read."))


def _first_visit_check(now: float) -> dict:
    name = "Sales credited from a first visit only"
    sales = [r for r in db.orders_since(now - 7 * 86400, ("sent",)) if r["kind"] == "purchase"]
    if not sales:
        return _c("first_visit", name, "ok", "No new sale sent to Meta in the last 7 days yet.")
    first = sum(1 for r in sales if (r["attribution"] or {}).get("source") in ("first_visit", "first_visit_unverified"))
    share = first / len(sales)
    detail = (f"{first} of {len(sales)} new sales in 7 days ({round(100 * share)}%) were credited from the "
              "buyer's first visit only, because no later ad click was seen.")
    if share > FIRST_VISIT_SHARE_WARN:
        return _c("first_visit", name, "warn", detail + " Check that the storefront pixel is connected and that "
                                                        "Shopify visit history can be read.")
    return _c("first_visit", name, "ok", detail)


def _meta_vs_store_check(rows: list[dict], now: float) -> dict:
    """Informational: on how many ads today Meta's purchase count and the
    store's confirmed sales (new sales the tracker credited to that ad) differ."""
    name = "Meta vs store sales per ad"
    midnight = dt.datetime.combine(dt.datetime.now(config.store_tz()).date(), dt.time(),
                                   tzinfo=config.store_tz()).timestamp()
    store: dict[str, int] = {}
    for r in db.orders_since(midnight - 3 * 86400, ("sent", "skipped", "pending", "failed")):
        o, credit = r["order_json"], r["attribution"] or {}
        if (tracking._parse_time(o.get("created_at")) or 0) >= midnight and credit.get("meta") \
                and credit.get("ad_id") and tracking.is_new_sale(o, r["reported"] or ""):
            store[str(credit["ad_id"])] = store.get(str(credit["ad_id"]), 0) + 1
    meta: dict[str, float] = {}
    for row in rows:
        if row.get("ad_id"):
            meta[str(row["ad_id"])] = meta.get(str(row["ad_id"]), 0.0) + (row.get("meta_purchases") or 0.0)
    ads = {a for a, v in meta.items() if v} | set(store)
    differ = [a for a in ads if round(meta.get(a, 0.0)) != store.get(a, 0)]
    if not ads:
        return _c("meta_vs_store", name, "ok", "No ad sales today yet, in Meta or in the store.")
    if not differ:
        return _c("meta_vs_store", name, "ok",
                  f"Today Meta's purchase count matches the store's confirmed sales on all {len(ads)} ads with sales.")
    return _c("meta_vs_store", name, "ok",
              f"Today {len(differ)} of {len(ads)} ads with sales show a different purchase count in Meta than the "
              "store confirmed. Meta also counts view and estimated sales, so some difference is normal.")


async def run_checks() -> list[dict]:
    now = time.time()
    checks = []

    problem = config.data_dir_problem()
    checks.append(_c("storage", "Storage", "fail" if problem else "ok",
                     problem or "History is saved on the Railway volume and survives restarts."))

    missing = config.missing_required()
    settings = missing + config.EXTRA_PIXEL_PROBLEMS
    checks.append(_c("settings", "Settings", "fail" if missing else "warn" if settings else "ok",
                     "; ".join(settings) if settings else
                     ("Live: sending to Meta for real." if not config.META_TEST_EVENT_CODE
                      else "Test mode: events only show in Events Manager > Test events.")))

    last = db.last_pixel_seen()
    if last is None:
        checks.append(_c("pixel", "Storefront pixel", "fail", "The storefront has never reported a visit."))
    else:
        age = now - last
        status = "ok" if age <= 2 * 3600 else "warn" if age <= 6 * 3600 else "fail"
        checks.append(_c("pixel", "Storefront pixel", status, f"Last shopper activity {ago(last)}."))

    last_poll = float(db.kv_get("last_poll_ok") or 0)
    checks.append(_c("shopify", "Shopify connection", "ok" if now - last_poll <= 900 else "fail",
                     f"Order list read {ago(last_poll)}." if last_poll else "Shopify has never answered."))

    checks.append(await _webhook_check())
    checks.append(await _orders_check(now))
    checks.append(await _pnl_revenue_check(now))

    for pixel in meta_capi.destinations():
        checks.append(_delivery_check(pixel, now))

    bad = db.renewal_orders_sent_as_purchase(now - 7 * 86400)
    # The owner calls subscription rebills MRR; the check id stays "renewals".
    checks.append(_c("renewals", "MRR kept out of sales", "fail" if bad else "ok",
                     f"Sent as Purchase by mistake: {', '.join(bad[:5])}" if bad else
                     "MRR orders go to Meta as SubscriptionRenewal, never as Purchase."))

    keys = db.purchase_match_keys(now - 7 * 86400)
    if not keys:
        checks.append(_c("details", "Customer details on sales", "ok", "No live sale in the last 7 days yet."))
    else:
        pct = {k: round(100 * sum(1 for m in keys if k in m.split(",")) / len(keys))
               for k in ("em", "ph", "client_ip_address", "client_user_agent", "fbc")}
        weak = [k for k in ("em", "client_ip_address", "client_user_agent") if pct[k] < 90]
        checks.append(_c("details", "Customer details on sales", "warn" if weak else "ok",
                         f"Of {len(keys)} sales in 7 days: email {pct['em']}%, phone {pct['ph']}%, "
                         f"IP {pct['client_ip_address']}%, browser {pct['client_user_agent']}%, "
                         f"ad click {pct['fbc']}%."))

    for pixel in meta_capi.destinations():
        emq = _emq_check(pixel)
        if emq:
            checks.append(emq)

    checks.append(_stripped_check(now))
    checks.append(await _journey_check(now))
    checks.append(_first_visit_check(now))

    if config.META_AD_ACCOUNT_IDS:
        today = dt.datetime.now(config.store_tz()).date().isoformat()
        ins = await meta_ads.ad_insights(today, today)
        checks.append(_c("ads", "Ad spend connection", "ok" if ins["connected"] else "warn",
                         f"Reading spend and sales per ad from {len(config.META_AD_ACCOUNT_IDS)} ad account(s)."
                         if ins["connected"] else ins["error"]))
        if ins["connected"]:
            checks.append(_meta_vs_store_check(ins["rows"], now))
    else:
        checks.append(_c("ads", "Ad spend connection", "warn",
                         "Not connected yet: add META_ADS_TOKEN and META_AD_ACCOUNT_IDS to see spend and ROAS."))
    return checks


# --- proposals ---------------------------------------------------------------------

def _tag_as_written(order: dict, tag: str) -> str:
    """A lower-cased tag as the order spells it, for the owner to recognise."""
    raw = order.get("tags")
    parts = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    return next((str(t).strip() for t in parts if str(t).strip().lower() == tag), tag)


async def propose(now: float) -> int:
    """Suggest fixes, each once (by key); nothing happens until the owner
    approves one in the hub. Returns how many are new."""
    made = 0
    go_live = tracking.go_live_at()
    week = db.orders_since(now - 7 * 86400, ("sent", "skipped", "pending", "failed"))

    # 1) An order this tracker owns that a pixel hasn't got after its retries.
    for r in week:
        if r["status"] not in ("failed", "pending"):
            continue
        created = tracking._parse_time(r["order_json"].get("created_at")) or r["received_at"]
        if created < go_live or now - created > meta_capi.MAX_EVENT_AGE_SECONDS - 3600:
            continue                            # WeTracked's, or too old for Meta anyway
        failing = r["status"] == "failed" and (r["attempts"] >= RESEND_PROPOSAL_ATTEMPTS
                                               or now - r["received_at"] > 1800)
        stuck = r["status"] == "pending" and now - r["received_at"] > 1800
        if not (failing or stuck):
            continue
        missing = tracking.missing_datasets(r)
        if not missing:
            continue                            # every pixel has it: nothing to send
        name = r["order_name"] or r["order_id"]
        # Some pixels have it: name the ones that don't, since only they get it.
        partly = len(missing) < len(tracking.order_destinations(r["order_json"]))
        who = ", ".join(pixel_label(p["pixel_id"]) for p in missing) if partly else "Meta"
        verb = "haven't" if partly and len(missing) > 1 else "hasn't"
        why = (f"{who} {verb} accepted it after {r['attempts']} tries" if failing else
               "It has been waiting to be sent for over 30 minutes")
        if partly:
            why += ". The other pixels already have it and don't get it again"
        last = f" Meta's last answer: {str(r['last_error'])[:200]}" if failing and r["last_error"] else ""
        made += db.add_proposal(f"resend:{r['order_id']}", "resend", f"Send order {name} to {who}",
                                f"{why}. The tracker keeps retrying on its own; approving sends it right now.{last}",
                                {"type": "resend_order", "order_id": r["order_id"]})
    for p in db.pending_proposals("resend"):
        row = db.get_order(str(p["action"].get("order_id") or ""))
        if row and row["status"] == "sent":
            db.decide_proposal(p["id"], "done", "Sent on its own. Nothing else to do.")
        elif row and row["status"] == "skipped":
            reason = tracking.SKIP_REASONS.get(row.get("kind") or "", "it is skipped on purpose")
            db.decide_proposal(p["id"], "done", f"Not needed any more: {reason}. Nothing was sent.")

    # 2) A tag that reads like a subscription rebill, on orders counted as new sales.
    tags: dict[str, list] = {}
    for r in week:
        o = r["order_json"]
        if tracking.is_renewal(o):
            continue
        for t in tracking.rebill_like_tags(o):
            tags.setdefault(t, [0, _tag_as_written(o, t)])[0] += 1
    for tag, (n, shown) in tags.items():
        made += db.add_proposal(
            f"renewal_tag:{tag}", "renewal_tag", f"Treat orders tagged {shown} as MRR?",
            f"{n} order{'s' if n != 1 else ''} in the last 7 days carr{'y' if n != 1 else 'ies'} the tag \"{shown}\", "
            "which reads like a subscription rebill. Right now they count as new sales and go to Meta as a Purchase. "
            "Approving counts future orders with this tag as MRR (sent to Meta as SubscriptionRenewal). Orders "
            "already sent stay as they were.",
            {"type": "add_renewal_tag", "tag": tag})

    # 3) A landing page dropping the ad ids: a heads-up, nothing to run.
    _, stripped = _stripped_arrivals(now)
    if stripped:
        hosts = _where(stripped) or [""]
        for host in hosts[:3]:
            n = sum(1 for a in stripped if (a.get("ref") or "") == host) if host else len(stripped)
            made += db.add_proposal(
                f"stripped:{host or 'unknown'}", "stripped_ids",
                f"The page at {host} is dropping the ad IDs" if host else "A landing page is dropping the ad IDs",
                f"In the last 24 hours {n} shopper{'s' if n != 1 else ''} came from Meta ads through "
                f"{host or 'the page between the ad and the store'} with the ad's name tags but without its ad ID, "
                "ad set ID and campaign ID. Their sales are matched to ads by name, which fails when two ads share a "
                "name. Have that page pass every link setting on to the store.",
                {"type": "info"})
    return made


async def refresh_emq() -> None:
    """Snapshot Meta's match-quality scores and dataset names for every pixel."""
    for pixel in meta_capi.destinations():
        pid = pixel["pixel_id"]
        name = await meta_ads.dataset_name(pid, pixel["token"])
        if name:
            db.kv_set(f"pixel_name:{pid}", name)
        try:
            scores = await meta_ads.dataset_quality(pid, pixel["token"])
        except meta_ads.MetaReadError as e:
            log.info("match quality for %s unavailable: %s", pid, e)
            continue
        db.add_emq_snapshot(pid, {ev: v.get("score") for ev, v in scores.items()})
        db.kv_set(f"emq:{pid}", json.dumps({"scores": scores, "taken_at": time.time()}))


async def tick() -> dict:
    if time.time() - _state["emq_at"] > EMQ_REFRESH_SECONDS:
        _state["emq_at"] = time.time()
        await refresh_emq()
    checks = await run_checks()
    status = worst(checks)
    db.add_watchdog_run(status, checks)
    try:
        await propose(time.time())
    except Exception:                           # a suggestion failing must not stop the checks
        log.exception("watchdog: proposals failed")
    previous = db.kv_get("watchdog_status")
    db.kv_set("watchdog_status", status)
    if status == "fail" and previous != "fail":
        import worker                           # late import: worker starts this loop
        failing = "; ".join(f"{c['name']}: {c['detail']}" for c in checks if c["status"] == "fail")
        await worker.alert(f"Watchdog: {failing}", key="watchdog")
    return {"status": status, "checks": checks}
