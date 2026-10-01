"""The hub's agent: Claude answers questions about the store and its Meta ad
account from the hub's own data ("what got sales today", "what spent the
most", "today vs yesterday").

It can only read. Its tools call the same handlers the hub's sections use, so
its numbers are the hub's numbers, and like every hub API they carry no
customer details (no emails, phones, addresses, names or IPs). Questions about
anything else are turned down.

Each conversation is stored whole and only ever appended to (agent_chats), so
the model's own blocks go back to it exactly as it wrote them. Spend is
counted per store day against AGENT_DAILY_CAP_USD.
"""
import datetime as dt
import json
import logging
import re
import time
import uuid
from typing import Any, Optional

import anthropic

import config
import db
import hub
import meta_ads

log = logging.getLogger("tracker.agent")

MAX_QUESTION = 2000
MAX_TOOL_ROUNDS = 8
MAX_MESSAGES = 160                  # a chat this long starts over (each turn re-reads the whole chat)
MAX_RESULT_CHARS = 60_000
CHAT_ID = re.compile(r"^[a-f0-9]{32}$")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# $ per million tokens: input, output, cache read, cache write (5 minutes).
PRICES = {"claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.50), "claude-sonnet-5": (2.0, 10.0, 0.20, 2.50),
          "claude-opus-5-5": (4.0, 20.0, 0.20, 5.00)}
NO_KEY = ("The agent isn't switched on yet: it needs an Anthropic API key. Add ANTHROPIC_API_KEY to the "
          "tracker's Variables in Railway, then deploy.")
CAPPED = ("Today's agent budget (${cap:.2f}) is used up, so I'm stopping here. It resets at midnight New York "
          "time, or raise AGENT_DAILY_CAP_USD in Railway.")
OFF_TOPIC = "I only answer questions about your store and your ads."

SYSTEM = """You are the analyst inside Core HQ, the dashboard for Core Supplements (getcoresupps.com), a Shopify store that sells supplements with Meta (Facebook and Instagram) ads. The owner and his business partner ask you about their sales, ads, ad account, tracking and profit. You answer from the dashboard's own data through your tools.

What you answer: anything about this store's sales, orders, products, Meta ads, ad spend, creatives, ad sets, campaigns, the shopper funnel, assists, the landing pages (product page or listicle), tracking health, and profit and loss. Anything else (general knowledge, writing, coding, other businesses, advice unrelated to this store's data) gets exactly this one line and nothing more: "I only answer questions about your store and your ads."

How to answer:
- Every number comes from a tool call made for this question. Never guess or estimate a number, and never reuse a number from your memory of an earlier day.
- Pick the dates yourself from the question and today's date (given with each message, in New York time, which is the store's time). Today is still running, so say so when you compare it with a full day. Tools reach back 30 days, through today.
- Lead with the answer in one or two sentences, then a small table when you list several ads, days or products. Keep it short: the owner reads this on a dashboard, not a report.
- Money in US dollars with two decimals ($61.28). ROAS as 2.34x. Name ads the way the data names them, with their ad set when it helps tell them apart.
- Never use em dashes. Call subscription rebills "MRR".
- If a tool returns an error, say plainly what couldn't be read and answer with what could.
- You can only read. You cannot change ads, budgets, campaigns, orders or settings. If asked to, say so in one line.

What the data means:
- Store sales: real Shopify orders the tracker tied to the last Meta ad the buyer clicked within 7 days. Meta sales: what Ads Manager reports (7-day click plus 1-day view); they often differ from store sales, and that is normal.
- Assists: ads a buyer clicked earlier, before the ad that got the sale. An assist never adds a sale or revenue. "The creative that got the sale" is the last ad clicked before buying.
- MRR: subscription rebills. They are never counted as ad sales and never sent to Meta as purchases.
- Product ROAS: sales of the advertised products divided by ad spend. Ad ROAS: store sales traced to ad clicks divided by spend. Meta ROAS: Ads Manager's own number.
- Landing page: "Product page" means the ad went straight to the store; "Listicle" means it went through the article page first.
- Ad names: campaigns are named after the product (for example "sperm" for SpermFuel+). Ad sets are creative batches such as "B1 VSL" (video), "B2 Statics" (images), "b3 - natvies - f" (native-style images), "B5 Statics - Her 2 Him". Ad names such as "TOF 1", "MOF 2 - Copy 4" and "BOF" mean top, middle and bottom of funnel; "Copy N" is a duplicate.
- Orders labelled "before go-live" were placed before 27 Sep 2026, 3:37 PM, when the old tracker (WeTracked) still sent them to Meta. Click history, and with it assists, starts on 27 Sep 2026.

Money and the P&L (profit_and_loss gives exactly the numbers on the P&L tab):
- Net profit = product sales + shipping charged - COGS - Meta ad spend - Shopify and currency fees - Shopify's own bills - software (monthly tools prorated to the dates) - chargebacks, plus or minus the owner's own lines that fall inside the dates. Margin is net profit over revenue.
- COGS is the product cost per pack sold. When some products have no COGS entered the P&L is an estimate; say so when the tool flags it.
- Break-even ROAS is the ROAS at which ads stop losing money after COGS and fees. Cost per new order is ad spend divided by new orders.
- MRR run rate is what active subscriptions bill per month; MRR at risk is subscriptions whose payments are failing. MRR net is MRR revenue after its COGS.
- Per product, "profit before fees and overheads" is the product's revenue minus its COGS and its campaigns' spend.
- The P&L reaches back to the store's first sale; the other tools reach 30 days. For "this month", "last month" or "all time" money questions use profit_and_loss.
- Spend can differ slightly between ad_performance (live from Meta) and the P&L (synced from Meta every few minutes). For money and profit, quote the P&L."""


def _range_props() -> dict:
    return {"since": {"type": "string", "description": "First day, YYYY-MM-DD, New York time"},
            "until": {"type": "string", "description": "Last day, YYYY-MM-DD, New York time (can equal since)"}}


def _tool(name: str, description: str, props: Optional[dict] = None) -> dict:
    props = _range_props() if props is None else props
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": props, "required": list(props),
                             "additionalProperties": False}}


TOOLS = [
    _tool("ad_performance",
          "Every Meta ad with delivery in the dates: campaign, ad set, ad, spend, impressions, clicks, CTR, CPC, "
          "add to carts, Meta sales (click and view), store sales and revenue, ROAS (store and Meta), assists and "
          "the order numbers it sold. Also the totals. Use it for what got sales, what spent most, best or worst "
          "creatives, ad set or campaign questions."),
    _tool("orders",
          "Every order placed in the dates: number, time, total, items, type (new sale, MRR, skipped), the ad "
          "that got the sale, the ads that assisted, landing page, and whether it started a subscription."),
    _tool("daily_breakdown",
          "Day by day for the dates: Meta ad spend, new sales and their revenue, MRR orders and revenue, and the "
          "ROAS of the day. Use it for trends and for comparing days."),
    _tool("funnel",
          "The shopper funnel for the dates (visitors, product views, add to cart, checkout, purchases) for "
          "shoppers from Meta ads, everyone else, and all, overall and per product, plus product page vs "
          "listicle visitors, sales and conversion rate."),
    _tool("assists",
          "For the dates: each ad that assisted a sale (clicked earlier, before the ad that got the sale), its "
          "spend, how many sales it assisted, and the creatives that got those sales."),
    _tool("profit_and_loss",
          "The P&L for the dates, the same numbers as the P&L tab: revenue (new, MRR, shipping), every expense "
          "(COGS, Meta ad spend, Shopify and currency fees, Shopify bills, software, chargebacks, the owner's own "
          "lines), net profit and margin, ROAS, break-even ROAS, cost per new order and per new subscription, "
          "AOV, orders and units, subscriptions (active, new, MRR run rate, MRR at risk from failing payments), "
          "each product's revenue, COGS, ad spend and profit before overheads, and day by day. Any dates from "
          "the store's first sale through today (use since 2000-01-01 for all time)."),
    _tool("tracking_health",
          "The tracker's health checks right now: whether sales reach Meta, the pixel, Shopify, match quality, "
          "and anything that needs a look.", {}),
]
TOOL_LABELS = {"ad_performance": "Ad performance", "orders": "Orders", "daily_breakdown": "Day by day",
               "funnel": "Funnel", "assists": "Assists", "profit_and_loss": "P&L",
               "tracking_health": "Tracking health"}


class _Query:
    """The part of a Request the hub's handlers read."""

    def __init__(self, **params: Any):
        self.query_params = {k: str(v) for k, v in params.items() if v is not None}


class ToolError(Exception):
    pass


def _dates(args: dict) -> tuple[str, str]:
    rng = hub.custom_range(args.get("since"), args.get("until"))
    if not rng:
        today = hub._today()
        raise ToolError(f"Dates must be YYYY-MM-DD, since on or before until, between "
                        f"{today - dt.timedelta(days=hub.LEARN_DAYS - 1)} and {today}.")
    return rng["since"], rng["until"]


def _pick(d: dict, keys: tuple) -> dict:
    return {k: d.get(k) for k in keys if k in d}


def _ratio(a: Any, b: Any, digits: int = 4) -> Optional[float]:
    try:
        return round(float(a) / float(b), digits) if b else None
    except (TypeError, ValueError):
        return None


AD_KEYS = ("spend", "impressions", "clicks", "meta_add_to_carts", "meta_purchases", "meta_click_purchases",
           "meta_view_purchases", "meta_value", "store_sales", "store_revenue", "roas_store", "roas_meta",
           "assists", "orders", "via_listicle")


async def _ad_performance(args: dict) -> dict:
    since, until = _dates(args)
    d = await hub.api_creatives(_Query(since=since, until=until, all="1"))
    ads = []
    for c in d.get("campaigns") or []:
        for g in c.get("groups") or []:
            for a in g.get("ads") or []:
                row = {"campaign": c.get("campaign_name"), "ad_set": g.get("name"), "ad": a.get("ad_name"),
                       "ad_id": a.get("ad_id"), **_pick(a, AD_KEYS)}
                row["ctr"] = _ratio(a.get("clicks"), a.get("impressions"))
                row["cpc"] = _ratio(a.get("spend"), a.get("clicks"), 2)
                ads.append(row)
    ads.sort(key=lambda r: -(r.get("spend") or 0))
    return {"dates": [since, until], "ads_connected": d.get("connected"), "currency": d.get("currency"),
            "totals": d.get("totals"), "ads": ads, "sales_without_an_ad_name": d.get("unlabelled"),
            "error": d.get("error") or ""}


async def _orders(args: dict) -> dict:
    since, until = _dates(args)
    d = await hub.api_orders(_Query(since=since, until=until, limit=200))
    rows = []
    for o in d.get("orders") or []:
        ad = o.get("ad") or {}
        rows.append({"order": o.get("name"), "time": o.get("time_local"), "total": o.get("total"),
                     "items": o.get("items"), "type": o.get("type_label"), "channel": o.get("channel"),
                     "ad": ad.get("ad_name") or None, "ad_set": ad.get("adset_name") or None,
                     "campaign": ad.get("campaign_name") or None,
                     "assisted_by": [a.get("ad_name") for a in ad.get("assists") or []],
                     "listicle": o.get("listicle"), "subscription": o.get("subscription"),
                     "sent_to_meta": o.get("tracker_status") == "sent"})
    return {"dates": [since, until], "count": d.get("count"), "orders": rows, "error": d.get("error") or ""}


async def _daily_breakdown(args: dict) -> dict:
    since, until = _dates(args)
    orders, err = await hub._credited_orders(hub._listing_start())
    try:
        spend = await meta_ads.daily_spend(since, until)
    except Exception as e:
        log.warning("agent: daily spend failed: %s", type(e).__name__)
        spend = None
    days, d = [], dt.date.fromisoformat(since)
    while d <= dt.date.fromisoformat(until):
        rng = hub.custom_range(d.isoformat(), d.isoformat())
        facts = hub._facts(orders, rng["start"], rng["end"])
        new = [f for f in facts if f["type"] == "new_sale"]
        mrr = [f for f in facts if f["type"] == "rebill"]
        s = None if spend is None else round(float(spend.get(d.isoformat()) or 0), 2)
        rev = round(sum(f["revenue"] for f in new), 2)
        days.append({"day": d.isoformat(), "weekday": d.strftime("%A"), "ad_spend": s, "new_sales": len(new),
                     "new_sales_revenue": rev, "mrr_orders": len(mrr),
                     "mrr_revenue": round(sum(f["revenue"] for f in mrr), 2),
                     "roas": _ratio(rev, s, 2) if s else None})
        d += dt.timedelta(days=1)
    notes = []
    if spend is None:
        notes.append("Ad spend couldn't be read from Meta just now.")
    if err:
        notes.append(err)
    return {"dates": [since, until], "days": days, "error": " ".join(notes)}


async def _funnel(args: dict) -> dict:
    since, until = _dates(args)
    d = await hub.api_funnel(_Query(since=since, until=until))
    return {"dates": [since, until], "steps": d.get("steps"), "from_meta_ads": d.get("meta"),
            "not_from_meta": d.get("other"), "all": d.get("all"), "landing_pages": (d.get("listicle") or {}).get("rows"),
            "per_product": {k: {"from_meta_ads": v.get("meta"), "all": v.get("all"),
                                "landing_pages": (v.get("listicle") or {}).get("rows")}
                            for k, v in (d.get("by_product") or {}).items()},
            "sales_not_tied_to_a_browser": d.get("untied_sales"), "error": d.get("error") or ""}


async def _assists(args: dict) -> dict:
    since, until = _dates(args)
    d = await hub.api_assists(_Query(since=since, until=until))
    rows = [{"ad": r.get("ad_name"), "ad_set": r.get("adset_name"), "campaign": r.get("campaign_name"),
             "spend": r.get("spend"), "sales_assisted": r.get("assists"),
             "creatives_that_got_the_sale": [{"ad": c.get("ad_name"), "ad_set": c.get("adset_name"),
                                              "sales": c.get("sales"), "value": c.get("value")}
                                             for c in r.get("closers") or []]}
            for r in d.get("rows") or []]
    return {"dates": [since, until], "assisting_ads": rows,
            "sales_with_no_earlier_ad_click": d.get("sales_without_assists"), "error": d.get("error") or ""}


def _g(obj: Any, path: str, default: Any = 0) -> Any:
    """The P&L page's g(): a dotted path into nested dicts."""
    for k in path.split("."):
        if not isinstance(obj, dict) or obj.get(k) is None:
            return default
        obj = obj[k]
    return obj


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _r2(v: float) -> float:
    return round(v + 0.0, 2)


def _pnl_dates(args: dict) -> tuple[str, str]:
    since, until = str(args.get("since") or ""), str(args.get("until") or "")
    today = hub._today().isoformat()
    try:
        ok = (dt.date.fromisoformat(since) and dt.date.fromisoformat(until)
              and "2000-01-01" <= since <= until <= today)
    except ValueError:
        ok = False
    if not ok:
        raise ToolError(f"Dates must be YYYY-MM-DD, since on or before until, from 2000-01-01 to {today}.")
    return since, until


def pnl_statement(d: dict, since: str, until: str) -> dict:
    """The P&L tab's headline for every product, worked out exactly as the
    page's own recalc() does (hub_page.py, a copy of the P&L app's code):
    product lines and the owner's dated lines per bucket, Shopify fees, then
    the store-level money (shipping, chargebacks, the fee true-up, Shopify's
    bills) and software prorated to the dates."""
    p = d.get("pnl") or {}
    blk = p.get("all") or {}
    kpi = blk.get("kpi") or {}

    def K(key: str, nested: str, default: Any = 0) -> Any:
        if kpi.get(key) is not None:
            return kpi[key]
        if isinstance(blk.get(key), (int, float)) and not isinstance(blk.get(key), bool):
            return blk[key]
        return _g(blk, nested, default)

    buckets: dict[str, list] = {"revenue": [], "cogs": [], "ads": [], "opex": []}

    def add(bucket: str, name: str, val: Any) -> None:
        v = _num(val)
        if abs(v) >= 0.005:
            buckets[bucket].append({"line": name, "amount": _r2(v)})

    products = []
    for x in p.get("products") or []:
        title = x.get("title") or x.get("product_id") or "Unknown product"
        add("revenue", f"{title} new ({_g(x, 'orders.new')} orders)", _g(x, "revenue.new"))
        add("revenue", f"{title} MRR ({_g(x, 'orders.recurring')} orders)", _g(x, "revenue.recurring"))
        cogs = 0.0
        for v in x.get("by_variant") or []:
            if _num(v.get("cogs")) > 0:
                add("cogs", f"{title} {v.get('variant_title') or 'default'} x {v.get('packs') or 0} packs", v.get("cogs"))
                cogs += _num(v.get("cogs"))
        spend = 0.0
        for c in x.get("campaigns") or []:
            add("ads", f"Meta {c.get('campaign_name') or c.get('campaign_id')}", c.get("spend"))
            spend += _num(c.get("spend"))
        rev = _num(_g(x, "revenue.new")) + _num(_g(x, "revenue.recurring"))
        if rev or cogs or spend:
            products.append({"product": title, "revenue": _r2(rev), "revenue_new": _r2(_num(_g(x, "revenue.new"))),
                             "revenue_mrr": _r2(_num(_g(x, "revenue.recurring"))),
                             "orders_new": _g(x, "orders.new"), "orders_mrr": _g(x, "orders.recurring"),
                             "cogs": _r2(cogs), "ad_spend": _r2(spend),
                             "profit_before_fees_and_overheads": _r2(rev - cogs - spend),
                             "roas": _ratio(rev, spend, 2)})
    u = p.get("unattributed") or {}
    add("revenue", f"Unattributed ({len(u.get('products') or [])} products)", _g(u, "revenue.total"))
    add("cogs", "Unattributed COGS", _g(u, "cogs.total"))
    unmapped = [c for c in u.get("campaigns") or [] if c.get("status") == "unmapped"]
    add("ads", f"Meta, campaigns not mapped to a product ({len(unmapped)})", sum(_num(c.get("spend")) for c in unmapped))
    add("opex", "Shopify processing fees", _g(blk, "fees.processing.total"))
    add("opex", "Currency conversion and payout fees", _g(blk, "fees.conversion.total"))
    # The owner's own lines count only inside their dates (undated ones only for all time).
    manual = d.get("manual") if isinstance(d.get("manual"), dict) else {}
    own = []
    for bucket in buckets:
        for r in manual.get(bucket) or []:
            if not isinstance(r, dict):
                continue
            day = str(r.get("date") or "")
            inside = (since <= day <= until) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else since <= "2001-01-01"
            if inside:
                add(bucket, f"{r.get('name') or 'Own line'} (your own line)", r.get("val"))
                own.append({"bucket": bucket, "line": r.get("name"), "amount": _r2(_num(r.get("val"))), "date": day or None})
    total = {b: _r2(sum(x["amount"] for x in rows)) for b, rows in buckets.items()}
    rev, cogs, ads, fees = total["revenue"], total["cogs"], total["ads"], total["opex"]
    st = p.get("store") or {}
    ship, cb = _r2(_num(st.get("shipping_revenue"))), _r2(_num(st.get("chargebacks")))
    fee_true, bills = _r2(_num(st.get("fee_adjustment"))), _r2(_num(st.get("platform_bills")))
    tools = [t for t in d.get("sw_tools") or [] if isinstance(t, dict)]
    by_day = p.get("by_day") or []
    start = by_day[0].get("date") if since <= "2001-01-01" and by_day else since
    days = max(1, (dt.date.fromisoformat(until) - dt.date.fromisoformat(start)).days + 1)
    per_month = sum(_num(t.get("monthly")) for t in tools)
    software = _r2(per_month * days / (_num(d.get("days_per_month")) or 30.44))
    net = rev - cogs - ads - fees + ship - cb - fee_true - bills - software
    revenue = rev + ship
    rev_new, rev_mrr = _num(K("revenue_new", "revenue.new")), _num(K("revenue_recurring", "revenue.recurring"))
    ord_new, ord_mrr = _num(K("orders_new", "orders.new")), _num(K("orders_recurring", "orders.recurring"))
    new_subs = _num(K("new_subs", "subs.new_in_range"))
    coverage = _num(K("cogs_coverage", "cogs.coverage", 1))
    cogs_mrr = _num(K("cogs_recurring", "cogs.recurring"))
    expenses = {"cogs": cogs, "meta_ad_spend": ads, "shopify_and_currency_fees": _r2(fees + fee_true),
                "shopify_bills": bills, "software": software, "chargebacks": cb}
    return {
        "dates": [since, until], "days": days,
        "headline": {"revenue": _r2(revenue), "expenses": _r2(revenue - net), "net_profit": _r2(net),
                     "margin_pct": round(net / revenue * 100, 1) if revenue else None,
                     "net_per_day": _r2(net / days), "orders": int(ord_new + ord_mrr),
                     "estimate_because_some_cogs_missing": bool(_g(blk, "net.provisional", False)) or coverage < 0.999},
        "revenue": {"product_sales": rev, "new_sales": _r2(rev_new), "mrr": _r2(rev_mrr),
                    "first_subscription_orders": _r2(_num(K("revenue_first_sub", "revenue.first_sub"))),
                    "one_off_orders": _r2(_num(K("revenue_one_off", "revenue.one_off"))),
                    "shipping_charged": ship},
        "expenses": expenses,
        "fee_true_up_included": fee_true,
        "ratios": {"roas_new": _ratio(rev_new, ads, 2), "roas_blended": _ratio(rev, ads, 2),
                   "break_even_roas": K("be_roas", "be_roas", None),
                   "cost_per_new_order": _ratio(ads, ord_new, 2) if ord_new else None,
                   "cost_per_new_subscription": _ratio(ads, new_subs, 2) if new_subs else None,
                   "aov_new": K("aov_new", "aov.new", None), "aov_mrr": K("aov_recurring", "aov.recurring", None),
                   "gross_margin_pct": round((rev - cogs) / rev * 100, 1) if rev else None},
        "orders": {"new": int(ord_new), "mrr": int(ord_mrr),
                   "first_subscription": K("orders_first_sub", "orders.first_sub"),
                   "one_off": K("orders_one_off", "orders.one_off"), "units": K("units", "units.total")},
        "subscriptions": {"active": K("active_subs", "subs.active"), "new_in_dates": int(new_subs),
                          "mrr_run_rate_per_month": K("mrr_runrate", "subs.mrr_runrate"),
                          "mrr_at_risk_failing_payments": K("mrr_at_risk", "subs.mrr_at_risk"),
                          "subscribers_failing_payment": K("overdue_subs", "subs.overdue_subs"),
                          "mrr_net_after_cogs": _r2(rev_mrr - cogs_mrr)},
        "per_product": products,
        "lines": buckets,
        "your_own_lines": own,
        "software_tools_per_month": [{"tool": t.get("name"), "monthly": t.get("monthly"), "billed": t.get("freq")}
                                     for t in tools],
        "by_day": [{"day": x.get("date"), "revenue": _r2(_num(x.get("revenue"))), "cogs": _r2(_num(x.get("cogs"))),
                    "fees": _r2(_num(x.get("fees"))), "ad_spend": _r2(_num(x.get("spend"))),
                    "orders": x.get("orders"),
                    "profit_before_overheads": _r2(_num(x.get("revenue")) - _num(x.get("cogs")) - _num(x.get("fees"))
                                                    - _num(x.get("spend")))}
                   for x in by_day[-62:]],
    }


async def _profit_and_loss(args: dict) -> dict:
    since, until = _pnl_dates(args)
    d = await hub.api_pnl(_Query(**{"from": since, "to": until}))
    if not d.get("ok"):
        return {"dates": [since, until], "error": d.get("error") or "The P&L couldn't be read."}
    return {**pnl_statement(d, since, until), "error": d.get("manual_error") or ""}


async def _tracking_health(args: dict) -> dict:
    st = await hub._status()
    return {"headline": st.get("headline"), "level": st.get("level"), "reasons": st.get("reasons"),
            "checks": [_pick(c, ("name", "status", "detail")) for c in st.get("checks") or []]}


RUNNERS = {"ad_performance": _ad_performance, "orders": _orders, "daily_breakdown": _daily_breakdown,
           "funnel": _funnel, "assists": _assists, "profit_and_loss": _profit_and_loss,
           "tracking_health": _tracking_health}


async def run_tool(name: str, args: Any) -> tuple[str, bool]:
    """(the JSON result for the model, whether it is an error)."""
    fn = RUNNERS.get(name)
    if fn is None:
        return json.dumps({"error": f"No tool called {name}."}), True
    try:
        out = await fn(args if isinstance(args, dict) else {})
    except ToolError as e:
        return json.dumps({"error": str(e)}), True
    except Exception as e:
        log.exception("agent tool %s failed", name)
        return json.dumps({"error": f"This data couldn't be read just now ({type(e).__name__})."}), True
    text = json.dumps(out, default=str, separators=(",", ":"))
    if len(text) > MAX_RESULT_CHARS and isinstance(out.get("ads"), list):
        kept = out["ads"][:60]
        out = {**out, "ads": kept, "note": f"Showing the {len(kept)} ads with the most spend of {len(out['ads'])}."}
        text = json.dumps(out, default=str, separators=(",", ":"))
    return text[:MAX_RESULT_CHARS], False


# --- the conversation -----------------------------------------------------------------

_client: Optional[anthropic.AsyncAnthropic] = None


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=120.0, max_retries=2)
    return _client


def _store_day() -> str:
    return hub._today().isoformat()


def spent_today() -> float:
    try:
        return float(db.kv_get(f"agent_spend:{_store_day()}") or 0)
    except ValueError:
        return 0.0


def cost(usage: Any, model: str) -> float:
    inp, out, read, write = PRICES.get(model, PRICES["claude-sonnet-5-5"])
    g = lambda k: float(getattr(usage, k, 0) or 0)            # noqa: E731
    return (g("input_tokens") * inp + g("output_tokens") * out + g("cache_read_input_tokens") * read
            + g("cache_creation_input_tokens") * write) / 1_000_000


def _now_line() -> str:
    now = dt.datetime.now(config.store_tz())
    return f"[Now: {now:%A} {now:%b} {now.day}, {now:%Y}, {now:%I:%M %p} New York time. Today is {now.date()}.]"


def _blocks(content: Any) -> list:
    out = []
    for b in content or []:
        out.append(b.to_dict() if hasattr(b, "to_dict") else dict(b))
    return out


def _text(content: Any) -> str:
    return "\n\n".join(b.text for b in content or [] if getattr(b, "type", "") == "text" and b.text).strip()


async def ask(chat_id: Optional[str], question: str) -> dict:
    question = (question or "").strip()[:MAX_QUESTION]
    if not question:
        return {"error": "Type a question first."}
    if not config.ANTHROPIC_API_KEY:
        return {"error": NO_KEY}
    cap = config.AGENT_DAILY_CAP_USD
    if cap and spent_today() >= cap:
        return {"error": CAPPED.format(cap=cap)}
    chat_id = chat_id if chat_id and CHAT_ID.match(chat_id) else None
    messages = (db.agent_chat(chat_id) if chat_id else None) or []
    if len(messages) > MAX_MESSAGES:
        messages, chat_id = [], None
    chat_id = chat_id or uuid.uuid4().hex
    messages.append({"role": "user", "content": [{"type": "text", "text": f"{_now_line()}\n\n{question}"}]})
    used: list[str] = []
    model = config.AGENT_MODEL
    answer, stopped = "", ""
    for _ in range(MAX_TOOL_ROUNDS + 1):
        if cap and spent_today() >= cap:
            stopped = CAPPED.format(cap=cap)
            break
        try:
            resp = await client().beta.messages.create(
                model=model, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=messages,
                output_config={"effort": "medium"}, cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA], fallbacks="default")
        except anthropic.AuthenticationError:
            return {"error": "Anthropic didn't accept the API key. Check ANTHROPIC_API_KEY in Railway."}
        except anthropic.RateLimitError:
            return {"error": "Too many questions at once. Try again in a minute."}
        except anthropic.APIStatusError as e:
            log.warning("agent: Anthropic answered %s: %s", e.status_code, str(e.message)[:300])
            if e.status_code == 400 and "credit" in str(e.message).lower():
                return {"error": "The Anthropic account is out of credits. Add some under Billing at "
                                 "console.anthropic.com."}
            return {"error": "The agent couldn't answer just now. Try again in a minute."}
        except anthropic.APIConnectionError:
            return {"error": "Couldn't reach Anthropic just now. Try again in a minute."}
        db.add_agent_spend(_store_day(), cost(resp.usage, model))
        content = _blocks(resp.content)
        if resp.stop_reason == "refusal":
            messages.append({"role": "assistant", "content": content or [{"type": "text", "text": OFF_TOPIC}]})
            answer = OFF_TOPIC
            break
        messages.append({"role": "assistant", "content": content})
        if resp.stop_reason != "tool_use":
            answer = _text(resp.content)
            if resp.stop_reason == "max_tokens":
                answer += "\n\n(The answer was cut off. Ask for a shorter version.)"
            break
        results = []
        for b in resp.content:
            if getattr(b, "type", "") != "tool_use":
                continue
            used.append(b.name)
            text, is_error = await run_tool(b.name, b.input)
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": text, "is_error": is_error})
        messages.append({"role": "user", "content": results})
    else:
        stopped = "That question needed more digging than one answer allows. Try asking it in smaller parts."
    if stopped and messages[-1]["role"] == "user" and any(
            c.get("type") == "tool_result" for c in messages[-1]["content"]):
        # The chat ends on tool results the model never read; the next question continues after them.
        messages.append({"role": "assistant", "content": [{"type": "text", "text": stopped}]})
    db.save_agent_chat(chat_id, messages)
    labels = list(dict.fromkeys(TOOL_LABELS.get(n, n) for n in used))
    return {"chat_id": chat_id, "answer": answer or stopped, "checked": labels,
            "spent_today": round(spent_today(), 4), "cap": cap, "error": ""}


# --- routes (hub.py mounts these behind the hub's login) -------------------------------

async def api_agent(request: Any) -> dict:
    try:
        body = json.loads(await hub._read_capped(request, 16_384) or b"{}")
    except (ValueError, TypeError):
        body = {}
    body = body if isinstance(body, dict) else {}
    return await ask(str(body.get("chat_id") or ""), str(body.get("question") or ""))


async def api_status(request: Any) -> dict:
    return {"configured": bool(config.ANTHROPIC_API_KEY), "model": config.AGENT_MODEL,
            "spent_today": round(spent_today(), 4), "cap": config.AGENT_DAILY_CAP_USD,
            "time": time.time()}
