"""The hub's HTML: both pages exist, the login form posts the right field, and
the dashboard calls every hub API path and nothing outside this service.
With Node.js installed, the page script itself runs against a stub DOM."""
import html
import json
import re
import shutil
import subprocess

import pytest

import hub_page

API_PATHS = ("/hub/api/overview", "/hub/api/orders", "/hub/api/creatives", "/hub/api/funnel",
             "/hub/api/watchdog", "/hub/api/watchdog/run", "/hub/api/resend/", "/hub/api/test-event")


def test_login_page_has_the_token_form_and_error_placeholder():
    page = hub_page.LOGIN_HTML
    assert "<!--error-->" in page
    assert 'name="token"' in page and 'type="password"' in page
    assert 'method="post"' in page and 'action="/hub/login"' in page


def test_hub_page_calls_every_api_path():
    page = hub_page.HUB_HTML
    for path in API_PATHS:
        assert path in page, path
    # POSTs carry the CSRF header; fetches send the session cookie.
    assert "'X-Hub-Request'" in page and "same-origin" in page
    assert "location.href = '/hub'" in page and "/hub/logout" in page


def test_hub_page_is_self_contained_and_plain():
    for page in (hub_page.HUB_HTML, hub_page.LOGIN_HTML):
        assert not re.search(r"<script[^>]+src=|<link\b|@import|url\(", page)
        assert "http://" not in page and "https://" not in page.replace("https?:", "")
        assert chr(0x2014) not in page                     # no em dashes in owner-facing copy
        assert page.isascii()


def test_url_tracking_parameters_are_exact():
    shown = html.unescape(hub_page.HUB_HTML)
    assert ("utm_source=facebook&utm_medium=paid&utm_campaign={{campaign.name}}&utm_term={{adset.name}}"
            "&utm_content={{ad.name}}&campaign_id={{campaign.id}}&adset_id={{adset.id}}&ad_id={{ad.id}}") in shown


def _fn(name: str) -> str:
    """One function of the page script, up to the next one."""
    page = hub_page.HUB_HTML
    start = page.index(f"  function {name}(")
    return page[start:page.index("\n  function ", start + 1)]


def test_sections_say_what_failed_instead_of_showing_zeros():
    page = hub_page.HUB_HTML
    # A Shopify failure is a note on the sales cards and the funnel, not a quiet 0.
    assert "ov.error" in _fn("renderCards") and "f.error" in _fn("renderFunnel")
    assert "isNum(g[2][i])" in _fn("renderFunnel")
    # A crash reply (HTTP 200 holding only `error`) goes to the error box with Try again.
    assert "MAIN_KEY[name] in data" in _fn("loadSection")
    # "Connect ad spend" is only for an account that isn't set up.
    assert "c.ads_configured" in _fn("renderCards") and "d.configured" in _fn("renderCreatives")
    start = page.index("$('#runChecks').addEventListener")
    run = page[start:page.index("$('#refreshBtn').addEventListener", start)]
    assert run.index("r.error") < run.index("Checks finished")
    assert "r.message ||" in _fn("resendMsg")


# Loads the page script with a stub DOM, renders each scenario and prints the HTML.
RUNNER = r"""
const fs = require('fs');
let src = fs.readFileSync(process.argv[2], 'utf8');
const scenarios = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const hook = '  var start = readHash();';
if (src.indexOf(hook) < 0) { console.error('hook line not found'); process.exit(2); }
src = src.replace(hook, '  globalThis.H = {renderCards: renderCards, renderFunnel: renderFunnel, ' +
  'renderCreatives: renderCreatives, renderOrders: renderOrders, loadSection: loadSection, resendMsg: resendMsg, ' +
  'secBody: secBody};\n' + hook);
const els = {};
function el(key) {
  return els[key] || (els[key] = {
    innerHTML: '', textContent: '', dataset: {}, value: '', disabled: false,
    classList: {add() {}, remove() {}}, setAttribute() {}, removeAttribute() {},
    addEventListener(type, fn) { this['on' + type] = fn; },
    querySelector(sel) { return el(key + ' ' + sel); }, querySelectorAll() { return []; }});
}
globalThis.document = {querySelector: el, querySelectorAll: () => [], getElementById: (id) => el('#' + id),
                       addEventListener() {}, visibilityState: 'hidden'};
globalThis.window = {addEventListener() {}};
globalThis.location = {hash: '', href: '/hub'};
globalThis.history = {replaceState() {}};
globalThis.Element = function () {};
let answers = {};
globalThis.fetch = (url) => {
  const k = Object.keys(answers).find((p) => url.indexOf(p) === 0);
  return k ? Promise.resolve({ok: true, status: 200, json: () => Promise.resolve(answers[k])})
           : new Promise(() => {});
};
eval(src);
const wait = () => new Promise((r) => setTimeout(r, 20));
const run = {
  cards: (d) => { H.renderCards(d); return H.secBody('cards').innerHTML; },
  creatives: (d) => { H.renderCreatives(d); return H.secBody('creatives').innerHTML; },
  orders: (d) => { H.renderOrders(d); return H.secBody('orders').innerHTML; },
  funnel: (d) => { H.renderFunnel(d); return H.secBody('funnel').innerHTML; },
  resend: (d) => H.resendMsg(d),
  load: (d) => { answers = {['/hub/api/' + d[0]]: d[2]}; return H.loadSection(d[0]).then(() => H.secBody(d[1]).innerHTML); },
  checks: (d) => { answers = {'/hub/api/watchdog/run': d}; el('#runChecks').onclick(); return wait().then(() => el('#runMsg').innerHTML); },
};
(async () => {
  const out = [];
  for (const [kind, data] of scenarios) out.push(await run[kind](data));
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""

NODE = shutil.which("node")


def _render(tmp_path, scenarios, raw=False) -> list[str]:
    """Run the page script under Node on each scenario; the rendered HTML,
    unescaped (or as the browser would get it, with `raw`)."""
    script = tmp_path / "hub.js"
    script.write_text(re.search(r"<script>(.*)</script>", hub_page.HUB_HTML, re.S).group(1), encoding="utf-8")
    (tmp_path / "runner.js").write_text(RUNNER, encoding="utf-8")
    (tmp_path / "scenarios.json").write_text(json.dumps(scenarios), encoding="utf-8")
    r = subprocess.run([NODE, str(tmp_path / "runner.js"), str(script), str(tmp_path / "scenarios.json")],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    return out if raw else [html.unescape(h) for h in out]
DAYS = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-26", "2026-09-27"]


def _overview(error="", shop_ok=True, **cards):
    known = [0, 0, 0, 0, 0, 0, 10]
    series = {k: known if shop_ok else [None] * 7 for k in ("new_revenue", "rebill_revenue", "new_sales", "rebills")}
    base = {"currency": "USD", "new_sales": {"count": 1, "revenue": 10}, "rebills": {"count": 0, "revenue": 0},
            "total_revenue": 10, "orders": 1, "aov": 10, "spend": None, "true_roas": None, "cost_per_sale": None,
            "meta_roas": None, "meta_purchases": None, "ads_connected": False, "ads_configured": False,
            "ads_error": ""}
    return {"error": error, "cards": {**base, **cards}, "series": {"days": DAYS, "spend": [None] * 7, **series}}


@pytest.mark.skipif(NODE is None, reason="needs Node.js to run the page script")
def test_page_script_shows_failures_plainly(tmp_path):
    shop_err = "Couldn't load orders from Shopify (Shopify answered 403)."
    unknown = {"count": None, "revenue": None}
    creatives = {"currency": "USD", "totals": {}, "campaigns": [], "unlabelled": {}, "url_tracking": {}}
    scenarios = [
        ["cards", _overview(shop_err, shop_ok=False, new_sales=unknown, rebills=unknown, total_revenue=None,
                            orders=None, aov=None, spend=40, true_roas=None, meta_roas=2.25, meta_purchases=3,
                            ads_connected=True, ads_configured=True)],
        ["cards", _overview(ads_configured=True, ads_error="act_123: network: ConnectError")],
        ["cards", _overview(ads_error="Not connected yet: add META_ADS_TOKEN and META_AD_ACCOUNT_IDS in Railway.")],
        ["creatives", {**creatives, "connected": False, "configured": True, "error": "act_123: network: ReadTimeout"}],
        ["creatives", {**creatives, "connected": False, "configured": False, "error": "Not connected yet"}],
        ["funnel", {"steps": ["Visitors", "Product views", "Add to cart", "Checkout", "Purchases"],
                    "meta": [10, 5, 2, 1, None], "other": [20, 8, 3, 1, None], "error": shop_err, "note": ""}],
        ["resend", {"status": "skipped", "message": "Not sent: rebills are switched off.", "note": ""}],
        ["load", ["overview", "status",
                  {"error": "This part of the hub couldn't be loaded. The details are in the server log."}]],
        ["checks", {"status": "warn", "checks": [], "error": "Couldn't run the checks (RuntimeError)."}],
        ["checks", {"status": "ok", "checks": []}],
    ]
    (shop_down, ads_failed, not_set_up, cr_failed, cr_not_set_up, funnel, resend, crash, run_crash,
     run_ok) = _render(tmp_path, scenarios)

    # Shopify down: the error shows, sales and True ROAS are "-", never 0 or 0.00x.
    assert "note warn" in shop_down and "Shopify answered 403" in shop_down
    assert "0.00x" not in shop_down and "All revenue" not in shop_down
    assert re.search(r'True ROAS</div><div class="c-value">-<', shop_down)
    assert "2.25x" in shop_down                                  # Meta's own numbers still show
    # Ads set up but unreadable: a plain reason, not the setup prompt.
    assert "Connect ad spend" not in ads_failed
    assert "Couldn't read ad spend from Meta just now" in ads_failed and "ConnectError" in ads_failed
    assert "Connect ad spend" in not_set_up and "All revenue" in not_set_up
    assert "ReadTimeout" in cr_failed and "Generate new token" not in cr_failed
    assert "Generate new token" in cr_not_set_up
    # Funnel: the error shows and unknown purchases are "-".
    assert "Shopify answered 403" in funnel and '<div class="f-num">-</div>' in funnel
    assert "0 of 10 visitors bought" not in funnel and "10 visitors" in funnel
    # Resend says the server's reason.
    assert "rebills are switched off" in resend and "older than 7 days" not in resend
    # A crash reply is an error with Try again, not an "all good" page.
    assert "couldn't be loaded" in crash and 'data-retry="overview"' in crash
    assert "Every link from your store" not in crash and "Live" not in crash
    # A crashed "Run checks now" reports the failure, not a verdict.
    assert "RuntimeError" in run_crash and "Checks finished" not in run_crash
    assert "Checks finished: All good." in run_ok



@pytest.mark.skipif(NODE is None, reason="needs Node.js to run the page script")
def test_creative_rows_show_no_roas_while_spend_is_unknown(tmp_path):
    # One ad account read, another failed: the rows that were read carry spend
    # and ROAS, but the page shows spend as "-", so ROAS must not stand alone.
    ad = {"ad_id": "AD1", "ad_name": "B2 Statics - Ad 3", "adset_name": "Broad", "spend": 40, "impressions": 0,
          "clicks": 0, "meta_purchases": 2, "meta_value": 120, "store_sales": 1, "store_revenue": 59.95,
          "roas_meta": 3.0, "roas_store": 1.5, "orders": ["#c101"]}
    group = {k: ad[k] for k in ("spend", "meta_purchases", "meta_value", "store_sales", "store_revenue",
                                "roas_meta", "roas_store")}
    camp = {"campaign_id": "C1", "campaign_name": "Leggings CBO", **group,
            "groups": [{"key": "id:AS1", "name": "Broad", **group, "ads": [ad]}]}
    base = {"currency": "USD", "totals": {}, "campaigns": [camp], "unlabelled": {}, "url_tracking": {}}
    failed, connected = _render(tmp_path, [
        ["creatives", {**base, "connected": False, "configured": True, "error": "act_456: (#200) Missing ads_read"}],
        ["creatives", {**base, "connected": True, "configured": True, "error": ""}],
    ])
    assert "#c101" in failed and "$59.95" in failed                # store-confirmed sales still show
    assert "1.50x" not in failed and "3.00x" not in failed
    assert "1.50x" in connected and "Meta 3.00x" in connected


def _text(h: str) -> str:
    """What the owner reads: the HTML without its tags, spaces collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h))


def _row(h: str, needle: str) -> str:
    """The table row (<tr>) holding `needle`."""
    return next(r for r in re.findall(r"<tr[^>]*>.*?</tr>", h, re.S) if needle in r)


def _creative(**over):
    ad = {"ad_id": "AD1", "ad_name": "Ad", "adset_name": "Broad", "spend": 40, "impressions": 0, "clicks": 0,
          "meta_purchases": 0, "meta_value": 0, "meta_click_purchases": 0, "meta_view_purchases": 0,
          "meta_click_value": 0, "meta_view_value": 0, "store_sales": 0, "store_revenue": 0,
          "roas_meta": None, "roas_store": None, "orders": [], "assists": 0, "assist_orders": []}
    ad.update(over)
    return ad


def _creatives(ads, connected=True, **group_over):
    group = {"key": "id:AS1", "name": "Broad", "spend": 120, "meta_purchases": 11, "meta_value": 500,
             "meta_click_purchases": None, "meta_view_purchases": None, "store_sales": 1, "store_revenue": 59.95,
             "assists": 2, "assist_orders": ["#c7", "#c9"], "roas_meta": None, "roas_store": None, "ads": ads,
             **group_over}
    camp = {"campaign_id": "C1", "campaign_name": "Leggings CBO", "groups": [group],
            **{k: group[k] for k in group if k not in ("key", "name", "ads")}}
    totals = {"spend": 120, "meta_purchases": 9, "meta_value": 500, "meta_click_purchases": 4,
              "meta_view_purchases": 5, "store_sales": 1, "store_revenue": 59.95, "true_roas": 0.5, "meta_roas": 4.17}
    return {"currency": "USD", "connected": connected, "configured": True, "error": "", "totals": totals,
            "campaigns": [camp], "unlabelled": {}, "url_tracking": {}}


@pytest.mark.skipif(NODE is None, reason="needs Node.js to run the page script")
def test_creatives_show_assists_and_metas_click_view_split(tmp_path):
    views = _creative(ad_id="AD2", ad_name="Views win", meta_purchases=5, meta_click_purchases=1,
                      meta_view_purchases=4, assists=2, assist_orders=["#c7", "#c9"])
    clicks = _creative(ad_id="AD1", ad_name="Clicks win", meta_purchases=4, meta_click_purchases=3,
                       meta_view_purchases=1, store_sales=1, store_revenue=59.95, orders=["#c7"])
    unsplit = _creative(ad_id="AD3", ad_name="No split", meta_purchases=2, meta_click_purchases=None,
                        meta_view_purchases=None)
    older = {k: v for k, v in _creative(ad_id="AD4", ad_name="Older reply").items()
             if not k.startswith("meta_click") and not k.startswith("meta_view") and not k.startswith("assist")}
    on, off = _render(tmp_path, [["creatives", _creatives([views, clicks, unsplit, older])],
                                 ["creatives", _creatives([views, clicks, unsplit], connected=False)]])
    assert '<th class="num">Assists</th>' in on
    # Meta sales as "N (C click, V view)", and only the view-heavy ad is marked.
    assert "5 (1 click, 4 view)" in _text(_row(on, "Views win"))
    assert "4 (3 click, 1 view)" in _text(_row(on, "Clicks win"))
    assert on.count(">mostly view<") == 1 and "mostly view" in _row(on, "Views win")
    # No split from Meta: the total alone, never "0 click".
    assert "click" not in _text(_row(on, "No split")) and "click" not in _text(_row(on, "Older reply"))
    assert "click" not in _text(_row(on, 'class="grp"'))                # the group's split is unknown
    assert "(4 click, 5 view)" in _text(on)                              # the stat tile, from the totals
    # Assists: a muted count whose tooltip lists the orders; assists in the campaign line.
    assert 'data-tip="Assisted 2 sales: #c7, #c9"' in _row(on, "Views win")
    assert '<span class="assist">0</span>' in _row(on, "Clicks win")
    assert 'data-tip="Assisted 2 sales: #c7, #c9"' in _row(on, 'class="grp"')     # the ad set's sales, each once
    assert "Assists 2" in _text(on) and "Assists are sales where the buyer clicked this ad earlier" in _text(on)
    assert "campaign totals count each of these sales once" in _text(on)
    # Without an ads connection Meta's numbers are "-", so no split and no tag either.
    assert "click," not in _text(off) and "mostly view" not in off
    assert '<span class="assist" data-tip="Assisted 2 sales: #c7, #c9">' in off      # assists are store data


def _order(oid, ad):
    return {"id": oid, "name": f"#c{oid}", "time_local": "Sep 27, 9:00 AM", "total": 59.95, "currency": "USD",
            "items": "SpermFuel+ x1", "type": "new_sale", "type_label": "New sale", "tracker_status": "sent",
            "error": None, "pixels": [], "ad": ad, "details": {}, "can_resend": False}


@pytest.mark.skipif(NODE is None, reason="needs Node.js to run the page script")
def test_order_rows_say_which_ad_sold_and_which_assisted(tmp_path):
    seller = {"click": True, "ad_name": "B2 Statics - Ad 3", "adset_name": "Broad", "campaign_name": "Leggings CBO",
              "ad_id": "AD1", "source": "browser"}
    helped = {**seller, "assists": [
        {"ad_name": "Hook test - v2", "adset_name": "Interests", "campaign_name": "Leggings CBO"},
        {"ad_name": "B2 Statics - Ad 7", "adset_name": "Broad", "campaign_name": "Leggings CBO"}]}
    untracked = {"click": True, "ad_name": "", "adset_name": "", "campaign_name": "", "ad_id": "", "source": "click_id"}
    helped_untracked = {**untracked, "assists": [{"ad_name": "Hook test - v2", "adset_name": "", "campaign_name": ""}]}
    feed = {"orders": [_order("1101", helped), _order("1102", seller), _order("1103", untracked),
                       _order("1104", helped_untracked), _order("1105", None)], "count": 5, "error": ""}
    (h,) = _render(tmp_path, [["orders", feed]])
    assert ("Sold by B2 Statics - Ad 3 · assisted by Hook test - v2, B2 Statics - Ad 7"
            in _text(_row(h, "#c1101")))
    assert 'data-tip="Interests › Leggings CBO"' in _row(h, "#c1101")
    assert "Sold by B2 Statics - Ad 3" in _text(_row(h, "#c1102")) and "assisted" not in _row(h, "#c1102")
    assert "Broad › Leggings CBO" in _text(_row(h, "#c1102"))
    assert "Ad name unknown" in _row(h, "#c1103") and "Sold by" not in _row(h, "#c1103")
    assert "Sold by an ad without a name · assisted by Hook test - v2" in _text(_row(h, "#c1104"))
    assert "No ad" in _row(h, "#c1105")


@pytest.mark.skipif(NODE is None, reason="needs Node.js to run the page script")
def test_ad_names_from_links_are_escaped_everywhere(tmp_path):
    # Ad names come from URL parameters anyone can put in a link to the store.
    evil = '"><img src=x onerror=alert(1)><script>alert(2)</script>'
    ad = _creative(ad_name=evil, adset_name=evil, meta_purchases=3, meta_click_purchases=1, meta_view_purchases=2,
                   assists=1, assist_orders=[evil], orders=[evil])
    creatives = _creatives([ad], name=evil, assist_orders=[evil])
    creatives["campaigns"][0]["campaign_name"] = evil
    order_ad = {"click": True, "ad_name": evil, "adset_name": evil, "campaign_name": evil, "ad_id": evil,
                "source": "browser", "assists": [{"ad_name": evil, "adset_name": evil, "campaign_name": evil}]}
    shown = _render(tmp_path, [["creatives", creatives], ["orders", {"orders": [_order("1", order_ad)], "count": 1}]],
                    raw=True)
    for h in shown:
        assert "<img" not in h and "<script" not in h
        assert "&quot;&gt;&lt;img src=x" in h                      # shown as text, attributes not broken out of
