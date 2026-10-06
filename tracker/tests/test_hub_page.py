"""The hub's HTML: both pages exist, the login form posts the right field, and
the dashboard calls every hub API path and nothing outside this service but
the Inter font. With Node.js installed, the page script itself runs against a
stub DOM."""
import datetime as dt
import html
import json
import re
import shutil
import subprocess
import time
from zoneinfo import ZoneInfo

import pytest

import hub_page

API_PATHS = ("/hub/api/pnl", "/hub/api/overview", "/hub/api/orders", "/hub/api/creatives", "/hub/api/assists",
             "/hub/api/funnel", "/hub/api/proposals", "/hub/api/watchdog", "/hub/api/watchdog/run", "/hub/api/resend/",
             "/hub/api/test-event")
PAGE = hub_page.HUB_HTML
CSS = re.search(r"<style>(.*?)</style>", PAGE, re.S).group(1)


def _phone_css() -> str:
    """The rules for phones (the 720px breakpoint)."""
    phone = CSS[CSS.index("@media (max-width:720px)"):]
    return phone[:phone.index("\n}")]


def test_login_page_has_the_token_form_and_error_placeholder():
    page = hub_page.LOGIN_HTML
    assert "<!--error-->" in page
    assert 'name="token"' in page and 'type="password"' in page
    assert 'method="post"' in page and 'action="/hub/login"' in page


def test_hub_page_calls_every_api_path():
    for path in API_PATHS:
        assert path in PAGE, path
    # Approve and dismiss are POSTs to /hub/api/proposals/{id}/approve or /dismiss.
    assert "'/hub/api/proposals/' + encodeURIComponent(id) + '/' + how" in PAGE
    assert "decide(el, 'approve')" in PAGE and "decide(el, 'dismiss')" in PAGE
    # POSTs carry the CSRF header; fetches send the session cookie.
    assert "'X-Hub-Request'" in PAGE and "same-origin" in PAGE
    assert "location.href = '/hub'" in PAGE and "/hub/logout" in PAGE


def test_hub_page_loads_nothing_but_the_inter_font():
    # Changed on purpose (batch 4B, P0): Inter comes from Google Fonts. Nothing else is loaded.
    assert "family=Inter:" in hub_page.FONTS and "fonts.googleapis.com" in hub_page.FONTS
    for page in (hub_page.HUB_HTML, hub_page.LOGIN_HTML):
        assert page.count(hub_page.FONTS) == 1
        rest = page.replace(hub_page.FONTS, "")
        assert not re.search(r"<script[^>]+src=|<link\b|@import|url\(", rest)
        assert "http://" not in rest and "https://" not in rest.replace("https?:", "")
        assert chr(0x2014) not in page                     # no em dashes in owner-facing copy
        assert page.isascii()
        assert "Inter,system-ui" in page                   # the system font stands in when Inter is blocked


def test_url_tracking_parameters_are_exact():
    shown = html.unescape(PAGE)
    assert ("utm_source=facebook&utm_medium=paid&utm_campaign={{campaign.name}}&utm_term={{adset.name}}"
            "&utm_content={{ad.name}}&campaign_id={{campaign.id}}&adset_id={{adset.id}}&ad_id={{ad.id}}") in shown


def _fn(name: str) -> str:
    """One function of the page script, up to the next one."""
    start = PAGE.index(f"  function {name}(")
    return PAGE[start:PAGE.index("\n  function ", start + 1)]


def test_sections_say_what_failed_instead_of_showing_zeros():
    # A Shopify failure is a note on the funnel, not a quiet 0.
    assert "f.error" in _fn("paintFunnel") and "isNum(raw[i])" in _fn("paintFunnel")
    # A crash reply (HTTP 200 holding only `error`) goes to the error box with Try again; so
    # does a P&L that didn't answer ({ok: false, error}, no `pnl`), in its own words.
    assert "MAIN_KEY[name] in data" in _fn("loadSection") and "pnl: 'pnl'" in PAGE
    assert "pnlDown(msg)" in _fn("markError")
    # "Connect ad spend" is only for an account that isn't set up.
    assert "d.configured" in _fn("renderCreatives")
    start = PAGE.index("$('#runChecks').addEventListener")
    run = PAGE[start:PAGE.index("$('#refreshBtn').addEventListener", start)]
    assert run.index("r.error") < run.index("Checks finished")
    assert "r.message ||" in _fn("resendMsg")


# Loads the page script with a stub DOM, renders each scenario and prints the result.
RUNNER = r"""
const fs = require('fs');
let src = fs.readFileSync(process.argv[2], 'utf8');
const scenarios = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const hook = '  var start = readHash();';
if (src.indexOf(hook) < 0) { console.error('hook line not found'); process.exit(2); }
src = src.replace(hook, '  globalThis.H = {renderPnl: renderPnl, renderFunnel: renderFunnel, ' +
  'renderCreatives: renderCreatives, renderOrders: renderOrders, renderAssists: renderAssists, ' +
  'renderApprovals: renderApprovals, renderHeader: renderHeader, decide: decide, S: S, secEl: secEl, ' +
  'loadSection: loadSection, resendMsg: resendMsg, secBody: secBody};\n' + hook);
const els = {};
function node(key) {
  const n = {key, id: key.charAt(0) === '#' ? key.slice(1) : '', dataset: {}, value: '', disabled: false, hidden: false,
    className: '', style: {}, children: [], clientWidth: 150, _html: '', _text: '', attrs: {},
    classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }},
    setAttribute(k, v) { this.attrs[k] = String(v); }, removeAttribute() {},
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    addEventListener(type, fn) { this['on' + type] = fn; },
    appendChild(c) { this.children.push(c); return c; }, closest() { return null; },
    querySelector(sel) { return el(key + ' ' + sel); }, querySelectorAll() { return []; }};
  Object.defineProperty(n, 'innerHTML', {get() { return this._html; }, set(v) { this._html = String(v); this.children = []; }});
  Object.defineProperty(n, 'textContent', {get() { return this._text; }, set(v) { this._text = String(v); this.children = []; }});
  return n;
}
function el(key) { return els[key] || (els[key] = node(key)); }
function text(n) { return n.text != null ? n.text : n._text + n.children.map(text).join(''); }
globalThis.document = {querySelector: el, querySelectorAll: () => [], getElementById: (id) => el('#' + id),
                       createElement: (tag) => node(tag), createTextNode: (t) => ({text: String(t)}),
                       addEventListener() {}, visibilityState: 'hidden'};
globalThis.window = {addEventListener() {}, matchMedia: () => ({matches: true})};
globalThis.location = {hash: '', href: '/hub'};
globalThis.history = {replaceState() {}};
globalThis.Element = function () {};
let answers = {}, calls = [];
globalThis.fetch = (url, init) => {
  calls.push([url, init || {}]);
  const k = Object.keys(answers).find((p) => url.indexOf(p) === 0);
  return k ? Promise.resolve({ok: true, status: 200, json: () => Promise.resolve(answers[k])})
           : new Promise(() => {});
};
eval(src);
const wait = () => new Promise((r) => setTimeout(r, 20));
function snap() {
  const out = {};
  for (const [k, n] of Object.entries(els)) {
    if (/^#[\w-]+$/.test(k)) out[k.slice(1)] = {text: text(n), html: n._html, display: n.style.display || '',
                                               width: n.style.width || '', background: n.style.background || ''};
  }
  return out;
}
const run = {
  pnl: (d) => { el('#presets .preset-chip.active').dataset.preset = d._preset || 'today'; H.renderPnl(d); return snap(); },
  ranges: (d) => d.map((p) => [p, PNL.rangeFor(p)]),
  creatives: (d) => { H.renderCreatives(d); return H.secBody('creatives').innerHTML; },
  orders: (d) => { H.renderOrders(d); return H.secBody('orders').innerHTML; },
  assists: (d) => { H.renderAssists(d); return H.secBody('assists').innerHTML; },
  funnel: (d) => { H.S.funnel = 'meta'; H.renderFunnel(d); return H.secBody('funnel').innerHTML; },
  // The switch picks the group: [key, reply]. Switching redraws from the reply it already has.
  funnelView: (d) => { H.S.funnel = d[0]; H.renderFunnel(d[1]); return H.secBody('funnel').innerHTML; },
  approvals: (d) => { H.renderApprovals(d); return {html: H.secBody('approvals').innerHTML, hidden: !!H.secEl('approvals').hidden,
                                                    count: el('#apCount').textContent}; },
  decide: (d) => {
    answers = {[d.url]: d.answer}; calls = []; H.S.props = d.props;
    H.decide({dataset: {[d.how]: d.id}, closest: () => null, textContent: ''}, d.how);
    return wait().then(() => ({html: H.secBody('approvals').innerHTML, hidden: !!H.secEl('approvals').hidden,
      calls: calls.map((c) => [c[0], c[1].method || 'GET', (c[1].headers || {})['X-Hub-Request'] || ''])}));
  },
  header: (d) => { H.renderHeader(d); return {dot: el('#statusDot').className, title: el('#tabHub').attrs.title || '',
    label: el('#tabHub').attrs['aria-label'] || '', mark: el('#mark').attrs.title || '', updated: el('#updated').textContent,
    cards: el('#ocTiles').innerHTML, count: el('#ocText').textContent, hidden: !!el('#ocount').hidden}; },
  firstOverview: (d) => { answers = {'/hub/api/overview': d}; H.S.ov = null; return H.loadSection('overview').then(() => (
    {dot: el('#statusDot').className, label: el('#tabHub').attrs['aria-label'] || ''})); },
  // A POST that hasn't answered yet (no answer set), then the proposals list reloading meanwhile.
  running: (d) => {
    answers = {}; calls = []; H.S.props = d.props;
    H.decide({dataset: {[d.how]: d.id}, closest: () => null, textContent: ''}, d.how);
    return d.reloads.map((list) => { H.renderApprovals({proposals: list});
      return {html: H.secBody('approvals').innerHTML, hidden: !!H.secEl('approvals').hidden, count: el('#apCount').textContent}; });
  },
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
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run the page script")


def _render(tmp_path, scenarios, raw=False) -> list:
    """Run the page script under Node on each scenario; the rendered HTML,
    unescaped (or as the browser would get it, with `raw`). Scenarios that
    return data (the P&L's elements, the approvals box) come back as is."""
    script = tmp_path / "hub.js"
    script.write_text(re.search(r"<script>(.*)</script>", PAGE, re.S).group(1), encoding="utf-8")
    (tmp_path / "runner.js").write_text(RUNNER, encoding="utf-8")
    (tmp_path / "scenarios.json").write_text(json.dumps(scenarios), encoding="utf-8")
    r = subprocess.run([NODE, str(tmp_path / "runner.js"), str(script), str(tmp_path / "scenarios.json")],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    return out if raw else [html.unescape(h) if isinstance(h, str) else h for h in out]


def _text(h: str) -> str:
    """What the owner reads: the HTML without its tags, spaces collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h))


def _words(h: str) -> list:
    """The words the owner reads, a tag between two words counting as a space."""
    return re.sub(r"<[^>]+>", " ", h).split()


def _row(h: str, needle: str) -> str:
    """The table row (<tr>) holding `needle`."""
    return next(r for r in re.findall(r"<tr[^>]*>.*?</tr>", h, re.S) if needle in r)


@needs_node
def test_page_script_shows_failures_plainly(tmp_path):
    shop_err = "Couldn't load orders from Shopify (Shopify answered 403)."
    creatives = {"currency": "USD", "totals": {}, "campaigns": [], "unlabelled": {}, "url_tracking": {}}
    pnl_down = {"ok": False, "error": "The P&L server did not answer (it said 502)", "pnl_url": "https://pnl.example.com",
                "range": {"from": "2026-09-27", "to": "2026-09-27"}}
    scenarios = [
        ["creatives", {**creatives, "connected": False, "configured": True, "error": "act_123: network: ReadTimeout"}],
        ["creatives", {**creatives, "connected": False, "configured": False, "error": "Not connected yet"}],
        ["funnel", {"steps": ["Visitors", "Product views", "Add to cart", "Checkout", "Purchases"],
                    "meta": [10, 5, 2, 1, None], "other": [20, 8, 3, 1, None], "untied_sales": None,
                    "listicle": {"rows": [{"key": "listicle", "label": "Listicle", "visitors": 4, "sales": None,
                                           "revenue": None, "conversion": None}], "note": ""},
                    "error": shop_err, "note": ""}],
        ["resend", {"status": "skipped", "message": "Not sent: rebills are switched off.", "note": ""}],
        ["load", ["overview", "status",
                  {"error": "This part of the hub couldn't be loaded. The details are in the server log."}]],
        ["load", ["pnl", "pnl", pnl_down]],
        ["checks", {"status": "warn", "checks": [], "error": "Couldn't run the checks (RuntimeError)."}],
        ["checks", {"status": "ok", "checks": []}],
    ]
    (cr_failed, cr_not_set_up, funnel, resend, crash, pnl, run_crash, run_ok) = _render(tmp_path, scenarios)

    # Ads set up but unreadable: a plain reason, not the setup steps.
    assert "ReadTimeout" in cr_failed and "Generate new token" not in cr_failed
    assert "Generate new token" in cr_not_set_up
    # Funnel: the error shows and unknown purchases and listicle sales are "-".
    assert "Shopify answered 403" in funnel and '<div class="fs-k">Purchases</div><div class="fs-v">-</div>' in funnel
    assert "of visitors bought" not in funnel and "10 visitors" in funnel
    assert "could not tie" not in funnel                      # unknown, not "0 more sales"
    assert _words(_row(funnel, "Listicle")) == ["Listicle", "4", "-", "-", "-", "-"]
    # Resend says the server's reason.
    assert "rebills are switched off" in resend and "older than 7 days" not in resend
    # A crash reply is an error with Try again, not an "all good" page.
    assert "couldn't be loaded" in crash and 'data-retry="overview"' in crash
    assert "Every link from your store" not in crash and "Live" not in crash
    # The P&L not answering says so in its words, with Try again and the P&L link; never a $0.
    assert "The P&L server did not answer (it said 502)" in pnl and 'data-retry="pnl"' in pnl
    assert 'href="https://pnl.example.com"' in pnl and "Open full P&L" in pnl
    assert "$" not in _text(pnl)
    # A crashed "Run checks now" reports the failure, not a verdict.
    assert "RuntimeError" in run_crash and "Checks finished" not in run_crash
    assert "Checks finished: All good." in run_ok


@needs_node
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


def _creative(**over):
    ad = {"ad_id": "AD1", "ad_name": "Ad", "adset_name": "Broad", "spend": 40, "impressions": 0, "clicks": 0,
          "meta_purchases": 0, "meta_value": 0, "meta_click_purchases": 0, "meta_view_purchases": 0,
          "meta_click_value": 0, "meta_view_value": 0, "store_sales": 0, "store_revenue": 0,
          "roas_meta": None, "roas_store": None, "orders": [], "assists": 0, "assist_orders": [], "via_listicle": 0}
    ad.update(over)
    return ad


def _creatives(ads, connected=True, **group_over):
    group = {"key": "id:AS1", "name": "Broad", "spend": 120, "meta_purchases": 11, "meta_value": 500,
             "meta_click_purchases": None, "meta_view_purchases": None, "store_sales": 1, "store_revenue": 59.95,
             "assists": 2, "assist_orders": ["#c7", "#c9"], "assist_closers": ["B1 Rips · 2", "MOF 3 · Sperm UGC 3"],
             "roas_meta": None, "roas_store": None, "ads": ads,
             **group_over}
    camp = {"campaign_id": "C1", "campaign_name": "Leggings CBO", "groups": [group],
            **{k: group[k] for k in group if k not in ("key", "name", "ads")}}
    totals = {"spend": 120, "meta_purchases": 9, "meta_value": 500, "meta_click_purchases": 4,
              "meta_view_purchases": 5, "store_sales": 1, "store_revenue": 59.95, "true_roas": 0.5, "meta_roas": 4.17}
    return {"currency": "USD", "connected": connected, "configured": True, "error": "", "totals": totals,
            "campaigns": [camp], "unlabelled": {}, "url_tracking": {}, "min_ad_spend": 15.0 if connected else None}


@needs_node
def test_creatives_show_assists_and_metas_click_view_split(tmp_path):
    views = _creative(ad_id="AD2", ad_name="Views win", meta_purchases=5, meta_click_purchases=1,
                      meta_view_purchases=4, assists=2, assist_orders=["#c7", "#c9"],
                      assist_closers=["B1 Rips · 2", "B1 Rips · 2"])
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
    assert "(4 click, 5 view)" in _text(on)                              # the strip, from the totals
    # Changed on purpose: the tooltip names the creative that got each assisted sale ("ad set · ad", xN),
    # never the order numbers.
    assert 'data-tip="Assisted 2 sales that were closed by: B1 Rips · 2 ×2"' in _row(on, "Views win")
    assert "#c7" not in _row(on, "Views win").split("data-tip=")[1].split('"')[1]
    assert '<span class="assist">0</span>' in _row(on, "Clicks win")
    assert ('data-tip="Assisted 2 sales that were closed by: B1 Rips · 2, MOF 3 · Sperm UGC 3"'
            in _row(on, 'class="grp"'))                                    # the ad set's sales, each once
    assert "Assists 2" in _text(on) and "Assists are sales where the buyer clicked this ad earlier" in _text(on)
    assert "campaign totals count each of these sales once" in _text(on)
    # Without an ads connection Meta's numbers are "-", so no split and no tag either.
    assert "click," not in _text(off) and "mostly view" not in off
    assert '<span class="assist" data-tip="Assisted 2 sales that were closed by: B1 Rips' in off   # store data


@needs_node
def test_creatives_open_with_the_roas_strip_and_fold_small_ads_into_one_line(tmp_path):
    # Replaces the old "Sales and ROAS" cards test: Product, Ad and Meta ROAS now sit in one strip here (P1, P4).
    big = _creative(ad_id="AD1", ad_name="Big spender", spend=80, store_sales=2, store_revenue=119.9,
                    orders=["#c1", "#c2"], via_listicle=2, roas_store=1.5, roas_meta=2.0)
    meta_only = _creative(ad_id="AD2", ad_name="Meta only", spend=30, meta_purchases=1, meta_value=60)
    small = {"count": 3, "spend": 21.5, "store_sales": 1, "store_revenue": 59.95, "meta_purchases": 2, "meta_value": 100}
    d = _creatives([big, meta_only], small=small)
    d["totals"].update(product_roas=1.5, ad_roas=0.42, meta_roas=2.1)
    d["campaigns"][0]["small"] = {"count": 1, "spend": 4.2, "store_sales": 0, "store_revenue": 0, "meta_purchases": 0,
                                  "meta_value": 0}
    none_small = _creatives([big], small={"count": 0, "spend": 0, "store_sales": 0, "store_revenue": 0,
                                          "meta_purchases": 0, "meta_value": 0})
    unread = _creatives([big], connected=False, small=small)          # spend not fully read: every ad has a row
    on, quiet, off = _render(tmp_path, [["creatives", d], ["creatives", none_small], ["creatives", unread]])

    strip = on[on.index('<div class="kstrip">'):on.index('<details class="camp"')]
    labels = re.findall(r'<div class="lab"[^>]*>([^<]+)', strip)
    assert labels == ["Product ROAS", "Ad ROAS", "Meta ROAS", "Ad spend", "Meta sales", "Store sales"]
    assert ["1.50x", "0.42x", "2.10x", "$120.00", "9", "1"] == re.findall(r'<div class="v">([^<]+)</div>', strip)
    assert "Sales your store traced to an ad click / ad spend" in strip   # what Ad ROAS means, on hover
    # Changed on purpose: ads with no sale are one grey line per ad set, and one per campaign for its
    # ad sets with no seller. Spend never earns a row.
    assert "+3 other ads with no sales: $21.50 spend" in _text(_row(on, 'class="more"'))
    assert '<p class="camp-more">+1 other ad with no sales: $4.20 spend</p>' in on
    assert "An ad gets a row when it got a sale today, in the store or in Ads Manager" in _text(on)
    assert "other ad" not in quiet                                       # nothing folded, no line
    assert "+3 other ads with no sales" in off and "$21.50" not in off   # spend not read: no spend on the line
    assert "under $" not in on and "add to cart" not in _text(on).split("Assists are")[0]
    # A row with store sales gets the white bar; its listicle sales are counted.
    assert _row(on, "Big spender").startswith('<tr class="sold">') and "2 via listicle" in _row(on, "Big spender")
    assert _row(on, "Meta only").startswith('<tr class="msold">') and "via listicle" not in _row(on, "Meta only")


def _order(oid, ad, **over):
    o = {"id": oid, "name": f"#c{oid}", "time_local": "Sep 27, 9:00 AM", "total": 59.95, "currency": "USD",
         "items": "SpermFuel+ x1", "type": "new_sale", "type_label": "New sale", "tracker_status": "sent",
         "error": None, "pixels": [], "ad": ad, "channel": "Meta ads" if ad else "Direct", "listicle": False,
         "details": {}, "can_resend": False}
    o.update(over)
    return o


@needs_node
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
    # Changed on purpose (P8): a sale no ad got shows its channel, not "No ad".
    assert "Direct" in _text(_row(h, "#c1105")) and "No ad" not in h


@needs_node
def test_orders_show_the_channel_the_listicle_first_visits_and_no_resend_before_go_live(tmp_path):
    first = {"click": True, "ad_name": "Hook B", "adset_name": "TOF 1", "campaign_name": "sperm", "ad_id": "1",
             "source": "first_visit_unverified"}
    seller = {**first, "ad_name": "UGC 3", "source": "browser"}
    feed = {"orders": [
        _order("2001", first),
        _order("2002", seller, listicle=True, can_resend=True),
        _order("2003", None, channel="Shop app ads"),
        _order("2004", None, channel=None, type="rebill", type_label="MRR"),
        _order("2005", seller, type="before_go_live", type_label="Before go-live, WeTracked sent this",
               tracker_status="skipped"),
    ], "count": 5, "error": ""}
    (h,) = _render(tmp_path, [["orders", feed]])
    assert "<th>Came from</th>" in h
    # Credited from the first landing page only: "First came from", never "Sold by".
    assert "First came from Hook B" in _text(_row(h, "#c2001")) and "Sold by" not in _row(h, "#c2001")
    assert "Sold by UGC 3" in _text(_row(h, "#c2002")) and '<span class="badge b-lst"' in _row(h, "#c2002")
    assert "Listicle" not in _row(h, "#c2001")
    assert "Shop app ads" in _text(_row(h, "#c2003"))
    assert "Came from</td>" not in _row(h, "#c2004") and '<span class="sub">-</span>' in _row(h, "#c2004")
    # WeTracked sent the pre-go-live order: no Resend button. The live one has one.
    assert "Before go-live, WeTracked sent this" in _row(h, "#c2005") and "data-resend" not in _row(h, "#c2005")
    assert 'data-resend="2002"' in _row(h, "#c2002")


@needs_node
def test_ad_names_from_links_are_escaped_everywhere(tmp_path):
    # Ad names come from URL parameters anyone can put in a link to the store.
    evil = '"><img src=x onerror=alert(1)><script>alert(2)</script>'
    ad = _creative(ad_name=evil, adset_name=evil, meta_purchases=3, meta_click_purchases=1, meta_view_purchases=2,
                   assists=1, assist_orders=[evil], orders=[evil])
    creatives = _creatives([ad], name=evil, assist_orders=[evil])
    creatives["campaigns"][0]["campaign_name"] = evil
    order_ad = {"click": True, "ad_name": evil, "adset_name": evil, "campaign_name": evil, "ad_id": evil,
                "source": "browser", "assists": [{"ad_name": evil, "adset_name": evil, "campaign_name": evil}]}
    assists = {"rows": [{"ad_id": evil, "ad_name": evil, "adset_name": evil, "campaign_name": evil, "spend": 1,
                         "assists": 1, "closers": [{"ad_id": evil, "ad_name": evil, "adset_name": evil, "sales": 1,
                                                    "value": 5}]}], "sales_without_assists": 0, "error": ""}
    shown = _render(tmp_path, [["creatives", creatives],
                               ["orders", {"orders": [_order("1", order_ad, channel=evil)], "count": 1}],
                               ["orders", {"orders": [_order("2", None, channel=evil)], "count": 1}],
                               ["assists", assists],
                               ["approvals", {"proposals": [{"id": evil, "title": evil, "detail": evil,
                                                             "status": "pending", "approve_label": evil}]}]],
                    raw=True)
    for h in shown:
        h = h["html"] if isinstance(h, dict) else h
        assert "<img" not in h and "<script" not in h
        assert "&quot;&gt;&lt;img src=x" in h                      # shown as text, attributes not broken out of


def test_page_order_design_and_words():
    # Changed on purpose: header, suggestions, P&L, the range tabs, funnel, assists (now above
    # creatives), creatives, tracking health, match quality, orders, tools.
    order = ['id="ocount"', 'id="sec-approvals"', 'id="sec-pnl"', 'class="bar"', 'id="sec-funnel"',
             'id="sec-assists"', 'id="sec-creatives"', 'id="sec-status"', 'id="sec-quality"', 'id="sec-orders"',
             'id="sec-tools"']
    at = [PAGE.index(x) for x in order]
    assert at == sorted(at)
    # The range tabs drive the four sections under them; the P&L has its own chips.
    assert "var RANGED = ['funnel', 'creatives', 'assists', 'orders'];" in PAGE and "loadMany(RANGED)" in _fn("setRange")
    for gone in ("True ROAS", "Rebill", "All revenue", "Average order", "Cost per new sale", "Meta-reported ROAS",
                 "Sales and ROAS", "blended ${", "No ad<", "No ad'"):
        assert gone not in PAGE, gone
    assert "'MRR'" in PAGE and "ROAS (new) ${fmtX(roasNew)}" in PAGE
    # Phones: 16px gutters, tables become cards, and an assists row stacks: ad, then spend and
    # assists side by side, then the closers.
    phone = _phone_css()
    assert ".wrap{padding:0 16px}" in phone and ".tbl thead{display:none}" in phone
    assert 'grid-template-areas:"ad count" "cls cls"' in phone


def test_design_is_black_and_white():
    # P0: black, near-black surfaces, hairlines, white text. Colour only for the status dots
    # (green, amber, red) and real failures (red).
    root = CSS[:CSS.index("}")]
    for token in ("--bg:#000", "--raised:#0a0a0a", "--card:#111", "--line:#1f1f1f", "--line-2:#262626",
                  "--text:#fafafa"):
        assert token in root, token
    status = {"22c55e", "f59e0b", "ef4444", "fca5a5", "f87171"}
    for hexa in re.findall(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", CSS):
        full = hexa.lower() if len(hexa) == 6 else "".join(c * 2 for c in hexa.lower())
        assert full in status or full[0:2] == full[2:4] == full[4:6], hexa        # greys only
    for rgba in re.findall(r"rgba\((\d+),(\d+),(\d+),", CSS):
        assert len(set(rgba)) == 1 or rgba in (("239", "68", "68"), ("245", "158", "11")), rgba
    assert "gradient" not in CSS and "glow" not in CSS
    # Inverted when on: active chips and primary buttons.
    assert '.seg button[aria-pressed="true"],.seg button[aria-pressed="true"]:hover{background:var(--text);color:#000}' in CSS
    assert ".btn.primary{background:var(--text);border-color:var(--text);color:#000}" in CSS
    assert "font-variant-numeric:tabular-nums" in CSS and "border-radius:12px" in CSS
    assert ".tbl-wrap{overflow:visible}" in _phone_css()                  # no sideways scroll on a phone
    # The login page wears the same clothes.
    assert "--bg:#000" in hub_page.LOGIN_HTML and "Inter,system-ui" in hub_page.LOGIN_HTML


def test_pnl_section_is_the_pnl_pages_own_code():
    # P1: copied from the P&L page, names and formulas unchanged.
    copy = PAGE[PAGE.index("// copied from pnl-server public/index.html"):PAGE.index("// end of the copy")]
    for name in ("const fmt =", "const fmtShort =", "const fmtX =", "const pct =", "const r2 =", "const g =",
                 "function K(", "function animateTo(", "function renderHero(", "function lineInRange(",
                 "const sumBucket =", "function buildAutoLines(", "function swToolsForRange(", "function recalc(",
                 "function renderSparks(", "function sparkline(", "const n0 ="):
        assert name in copy, name
    # Changed on purpose: the hub leaves software out of its profit; the main P&L still counts it.
    for formula in ("const netFinal = net + ship - cb - feeTrue - bills;   // hub: software left out",
                    "renderHero({ net: netFinal, rev: rev + ship, cogs, ads, fees: shopifyFees + feeTrue, provisional });",
                    "const gross=rev-cogs; let net=gross-ads-shopifyFees;",
                    "const coverage=K(blk,'cogs_coverage','cogs.coverage',1), provisional=!!g(blk,'net.provisional',false)||coverage<0.999;",
                    "return { total: r2(perMonth * days / DAYS_PER_MONTH), perMonth: r2(perMonth), days };",
                    "if(s==='all'){"):
        assert formula in copy, formula
    # Its range comes from the chips, and SW_TOOLS / DAYS_PER_MONTH from the proxy.
    assert "let SW_TOOLS = [];" in copy and "let DAYS_PER_MONTH = 30.44;" in copy
    assert "const curRange = () => RANGE;" in PAGE
    assert re.findall(r'data-preset="(\w+)"', PAGE) == ["today", "yesterday", "7", "30", "mtd", "all"]


def _pnl_answer(coverage=1, **over):
    """A /hub/api/pnl answer for Sep 27. By the P&L's own formulas: revenue 1,000 + shipping 20; COGS
    250; ad spend 300; fees 30 + 10 + the owner's own 60 line + a 5 true-up; chargebacks 15, Shopify
    bills 30. Software (1 for the day) is left out on the hub. Net 320, expenses 700."""
    day = {"date": "2026-09-27", "revenue": 1000, "revenue_new": 800, "revenue_recurring": 200, "cogs": 250,
           "fees": 40, "spend": 300, "orders": 14}
    kpi = {"revenue_new": 800, "revenue_recurring": 200, "revenue_first_sub": 300, "revenue_one_off": 500,
           "orders_new": 10, "orders_recurring": 4, "orders_first_sub": 3, "orders_one_off": 7, "new_subs": 3,
           "mrr_runrate": 5000, "active_subs": 120, "cogs_recurring": 50, "cogs_coverage": coverage,
           "mrr_at_risk": 400, "overdue_subs": 6}

    def z(new, rec):
        return {"new": new, "recurring": rec, "total": new + rec}
    pnl = {"version": 3, "period": {"from": "2026-09-27", "to": "2026-09-27"}, "generated_at": "2026-09-27T21:00:00Z",
           "by_day": [day],
           "all": {"kpi": kpi, "revenue": z(800, 200), "cogs": {**z(200, 50), "coverage": coverage},
                   "orders": z(10, 4), "net": {"value": 0, "provisional": False},
                   "fees": {"processing": {"total": 30}, "conversion": {"total": 10}, "total": z(32, 8)},
                   "by_day": [day], "mrr_at_risk": 400, "overdue_subs": 6, "spend": 300},
           "products": [{"product_id": "1", "title": "SpermFuel+", "revenue": z(800, 200), "orders": z(10, 4),
                         "by_variant": [{"variant_title": "3", "packs": 5, "cogs": 250}],
                         "campaigns": [{"campaign_id": "c1", "campaign_name": "sperm", "spend": 300}]}],
           "unattributed": {"revenue": {"total": 0}, "cogs": {"total": 0}, "products": [], "campaigns": []},
           "store": {"shipping_revenue": 20, "chargebacks": 15, "fee_adjustment": 5, "fees_source": "payouts",
                     "platform_bills": 30, "platform_bill_count": 1, "platform_bills_cad": 41}}
    answer = {"ok": True, "error": "", "pnl_url": "https://pnl.example.com",
              "range": {"from": "2026-09-27", "to": "2026-09-27"}, "pnl": pnl,
              "manual": {"revenue": [{"id": "old", "name": "Old Store", "val": 4639}], "cogs": [], "ads": [],
                         "opex": [{"id": "va", "name": "VA", "val": 60, "date": "2026-09-27"}]},
              "manual_error": "", "sw_tools": [{"name": "Tools", "monthly": 30.44, "freq": "monthly"}],
              "days_per_month": 30.44, "sw_tools_source": "pnl",
              "last_sync": {"shopify": {"ran_at": "2026-09-27T20:58:00Z", "status": "ok"}, "meta": None,
                            "products": None},
              "fetched_at": "2026-09-27T21:05:00Z", "meta_sync_started": False, "_preset": "today"}
    answer.update(over)
    return answer


@needs_node
def test_pnl_section_shows_the_pnls_numbers(tmp_path):
    today, every, fallback = _render(tmp_path, [
        ["pnl", _pnl_answer()],
        # All: the undated "Old Store" line counts (only all-time ranges reach it); COGS coverage under 100%.
        ["pnl", _pnl_answer(coverage=0.8, range={"from": "2000-01-01", "to": "2026-09-27"}, _preset="all")],
        # The owner's lines unreadable, and the software list from the copy (no note: software isn't counted here).
        ["pnl", _pnl_answer(manual=None, manual_error="Your own P&L lines couldn't be read just now, so they are left out.",
                            sw_tools_source="copy")],
    ])
    t = {k: v["text"] for k, v in today.items()}
    assert (t["heroNet"], t["heroRev"], t["heroCogs"], t["heroAds"], t["heroFees"]) == (
        "$320.00", "$1.0k", "$250", "$300", "$105")
    assert (t["heroRange"], t["heroMargin"], t["heroDays"], t["heroPerDay"]) == ("today", "31.4%", "1 day", "$320.00")
    assert today["heroProv"]["display"] == "none" and today["kpiProvisional"]["display"] == "none"
    # Expenses: hero revenue less net profit, and what it is made of.
    assert t["heroExp"] == "$700"
    assert t["heroExpParts"] == ("Expenses = COGS $250 + ad spend $300 + fees $105 + Shopify bills $30 + "
                                 "chargebacks $15 (fees include a $5 true-up to Shopify's actual)")
    # The P&L's cards, with "MRR" where the P&L says "recurring", and no blended ROAS.
    assert (t["kpiRev"], t["kpiRevSub"]) == ("$1.0k", "new $800 · MRR $200")
    assert (t["kpiProfit"], t["kpiMargin"]) == ("$320", "35.0% margin")
    assert (t["kpiMeta"], t["kpiRoas"]) == ("$300", "ROAS (new) 2.67x")
    assert t["kpiCacSub"] == "CAC $30.00 / new order · $100.00 / new sub"
    assert (t["kpiOrders"], t["kpiOrdersSub"]) == ("14", "10 new (3 first-sub · 7 one-off) · 4 MRR")
    assert t["kpiMrr"] == "$5.0k" and t["kpiMrrSub"] == "120 active subs · $200 MRR in range$400 at risk · 6 failing payment"
    assert (t["kpiMrrNet"], t["kpiMrrNetSub"]) == ("$150", "after $50 COGS")
    # The compact row: MRR sales and a short orders line beside the P&L's own numbers.
    assert (t["kpiMrrSales"], t["kpiMrrSalesSub"], t["ordersShort"]) == ("$200", "4 MRR orders", "10 new · 4 MRR")
    assert (t["kpiRisk"], t["kpiRiskNote"]) == ("$400", "6 subscribers failing payment")
    assert today["kpiRiskMeter"]["width"] == "7%" and t["kpiRiskMeterNote"] == "7% of $5.4k MRR · recovering half is $200/mo"
    for v in t.values():
        assert "recurring" not in v and "blended" not in v and chr(0x2014) not in v
    meta = html.unescape(today["pnlMeta"]["html"])
    assert meta.startswith("From your P&L · <span data-tip=\"In your P&L: Shopify orders last synced ")
    assert "synced 05:05 PM ET" in meta and 'href="https://pnl.example.com"' in meta and "Open full P&L" in meta
    assert today["pnlNotes"]["html"] == ""

    a = {k: v["text"] for k, v in every.items()}
    assert (a["heroNet"], a["heroRange"], a["heroRev"]) == ("$4,959.00", "all time", "$5.7k")
    assert every["heroProv"]["display"] == "" and every["kpiProvisional"]["display"] == ""

    f = {k: v["text"] for k, v in fallback.items()}
    assert f["heroNet"] == "$380.00"
    notes = html.unescape(fallback["pnlNotes"]["html"])
    assert "Your own P&L lines couldn't be read just now" in notes and "tool list" not in notes


@needs_node
def test_pnl_chips_ask_for_the_pnls_own_ranges(tmp_path):
    def expected():
        et = ZoneInfo("America/New_York")
        now = time.time()
        day = lambda secs_back: dt.datetime.fromtimestamp(now - secs_back, et).date().isoformat()
        today = day(0)
        return {"today": [today, today],
                "yesterday": [(dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()] * 2,
                "7": [day(7 * 86400), today], "30": [day(30 * 86400), today],
                "mtd": [today[:8] + "01", today], "all": ["2000-01-01", today]}
    before = expected()
    (got,) = _render(tmp_path, [["ranges", ["today", "yesterday", "7", "30", "mtd", "all"]]])
    after = expected()
    got = {p: [r["from"], r["to"]] for p, r in got}
    assert got in (before, after)                     # the same as the P&L's chips (7D is today less 7 days)


@needs_node
def test_approvals_box_waits_for_the_owners_ok(tmp_path):
    send = {"id": 7, "kind": "resend", "title": "Send order #c3711 to Meta", "detail": "Core Club hasn't accepted it.",
            "status": "pending", "informational": False, "approve_label": "Send it", "can_dismiss": True, "result": ""}
    info = {"id": 8, "kind": "stripped_ids", "title": "A landing page is dropping the ad IDs", "detail": "See the listicle.",
            "status": "pending", "informational": True, "approve_label": "Got it", "can_dismiss": False, "result": ""}
    done = {**send, "id": 9, "status": "done", "title": "Old one"}
    result = "Sent to Meta. Meta ignores repeats of the same order, so nothing is counted twice."
    box, empty, decided, failed = _render(tmp_path, [
        ["approvals", {"proposals": [send, info, done]}],
        ["approvals", {"proposals": [done]}],
        ["decide", {"props": [send, info], "id": "7", "how": "approve", "url": "/hub/api/proposals/7/approve",
                    "answer": {"ok": True, "proposal": {**send, "status": "done", "result": result}}}],
        ["decide", {"props": [send], "id": "7", "how": "dismiss", "url": "/hub/api/proposals/7/dismiss",
                    "answer": {"ok": False, "error": "This was already approved."}}],
    ])
    assert not box["hidden"] and box["count"].startswith("2 suggestions from the watchdog")
    h = html.unescape(box["html"])
    assert h.count('<div class="ap-card"') == 2 and "Old one" not in h
    assert "Send order #c3711 to Meta" in h and "Core Club hasn't accepted it." in h
    assert '<button class="btn primary sm" type="button" data-approve="7">Send it</button>' in h
    assert 'data-dismiss="7"' in h
    # Information only: "Got it", nothing to dismiss.
    assert 'data-approve="8">Got it</button>' in h and 'data-dismiss="8"' not in h
    assert empty["hidden"]                                              # nothing pending: no box
    # Approve posts with the hub's header and shows what happened in place of the buttons.
    assert decided["calls"] == [["/hub/api/proposals/7/approve", "POST", "1"]]
    d = html.unescape(decided["html"])
    assert result in d and 'data-approve="7"' not in d and 'data-approve="8"' in d
    assert "This was already approved." in html.unescape(failed["html"]) and 'class="err-t"' in failed["html"]


@needs_node
def test_a_suggestion_being_approved_stays_put_when_the_list_reloads(tmp_path):
    send = {"id": 7, "kind": "resend", "title": "Send order #c3711 to Meta", "detail": "Core Club hasn't accepted it.",
            "status": "pending", "informational": False, "approve_label": "Send it", "can_dismiss": True, "result": ""}
    other = {**send, "id": 8, "title": "Send order #c3712 to Meta"}
    # The server marks it approved before the resend is done; a list read before the click still says pending.
    (approving, dismissing) = _render(tmp_path, [
        ["running", {"props": [send, other], "id": "7", "how": "approve",
                     "reloads": [[{**send, "status": "approved"}, other], [other], [send, other]]}],
        ["running", {"props": [send], "id": "7", "how": "dismiss", "reloads": [[]]}],
    ])
    for box in approving:
        h = html.unescape(box["html"])
        assert not box["hidden"] and h.count('<div class="ap-card"') == 2
        # Buttons off, the pressed one saying it is working; the other card is untouched.
        assert '<button class="btn primary sm" type="button" data-approve="7" disabled>Working…</button>' in h
        assert '<button class="btn sm" type="button" data-dismiss="7" disabled>Dismiss</button>' in h
        assert '<button class="btn primary sm" type="button" data-approve="8">Send it</button>' in h
        assert box["count"].startswith("1 suggestion from the watchdog")
    (box,) = dismissing
    h = html.unescape(box["html"])
    assert not box["hidden"] and 'data-dismiss="7" disabled>Dismissing…</button>' in h
    assert 'data-approve="7" disabled>Send it</button>' in h and box["count"] == ""


def _cards(h: str) -> list:
    """The funnel's step cards, left to right: (label, number, share of the step above or None)."""
    out = []
    for card in re.split(r'<div class="fstep(?: end)?">', h)[1:]:
        label = re.search(r'<div class="fs-k">([^<]+)</div>', card).group(1)
        value = re.search(r'<div class="fs-v">([^<]+)</div>', card).group(1)
        rate = re.search(r'<div class="fs-rate">.*?</span><span>([^<]+)</span>', card, re.S)
        out.append((label, value, rate.group(1) if rate else None))
    return out


@needs_node
def test_funnel_is_five_step_cards_for_the_shoppers_the_switch_picks(tmp_path):
    # Changed on purpose (F1): step cards with the share of the step above, not two bar charts.
    base = {"steps": ["Visitors", "Product views", "Add to cart", "Checkout", "Purchases"],
            "meta": [100, 86, 10, 5, 2], "other": [50, 20, 4, 2, 1], "all": [150, 106, 14, 7, 3], "error": "",
            "note": "Each shopper's browser counts once.",
            "groups": [{"key": "meta", "label": "Meta ads", "tip": ""},
                       {"key": "other", "label": "Not from Meta", "tip": "Shoppers who did not come from a Meta ad "
                        "in the 3 days before: typed the site in, Google, email, the Shop app, returning customers."},
                       {"key": "all", "label": "All", "tip": ""}]}
    # Changed on purpose (F2): "Product page" first, then "Listicle".
    lst = {"rows": [{"key": "direct", "label": "Product page", "visitors": 40, "sales": 0, "revenue": 0,
                     "conversion": 0},
                    {"key": "listicle", "label": "Listicle", "visitors": 60, "sales": 2, "revenue": 119.9,
                     "conversion": 0.0333}],
           "note": "Shoppers from Meta ads by the page their ad click landed on, and the share of them who bought."}
    full, plain, other, every, empty = _render(tmp_path, [
        ["funnel", {**base, "untied_sales": 3, "counting_since": "Sep 27, 6:25 PM", "listicle": lst}],
        ["funnel", {**base, "untied_sales": 0, "counting_since": "", "listicle": lst}],
        ["funnelView", ["other", {**base, "untied_sales": 0, "counting_since": "", "listicle": lst}]],
        ["funnelView", ["all", {**base, "untied_sales": 0, "counting_since": "", "listicle": lst}]],
        ["funnelView", ["meta", {**base, "meta": [0, 0, 0, 0, 0], "untied_sales": 0, "counting_since": ""}]],
    ])
    # Meta ads by default: five cards left to right, each step a share of the one above it.
    assert _cards(full) == [("Visitors", "100", None), ("Product views", "86", "86%"), ("Add to cart", "10", "12%"),
                            ("Checkout", "5", "50%"), ("Purchases", "2", "40%")]
    assert full.count('<span class="fs-arrow" aria-hidden="true">→</span>') == 4
    assert full.count("of the step above") == 4                          # said in words for screen readers and phones
    assert "<b>2.00%</b> of visitors bought" in full and "Everyone else" not in full
    # The two grey notes stay, small.
    assert '<p class="sub small f-notes">3 more sales we could not tie to a browser.</p>' in full
    assert ('<p class="sub small f-notes">Counting since Sep 27, 6:25 PM, when the pixel recorded its first '
            'shopper.</p>') in full
    assert "could not tie" not in plain and "Counting since" not in plain
    # The switch: Not from Meta and All, from the same reply.
    assert [c[1] for c in _cards(other)] == ["50", "20", "4", "2", "1"] and "<b>2.00%</b> of visitors bought" in other
    assert [c[2] for c in _cards(other)] == [None, "40%", "20%", "50%", "50%"]
    assert [c[1] for c in _cards(every)] == ["150", "106", "14", "7", "3"] and "<b>2.00%</b> of visitors bought" in every
    assert [c[2] for c in _cards(empty)] == [None, "-", "-", "-", "-"] and "No visitors yet" in empty
    # Product page first, then the listicle: visitors, sales, revenue, conversion rate.
    assert "Product page vs listicle" in full and "<th>Landed on</th>" in full
    assert full.index(">Product page<") < full.index(">Listicle<")
    assert _words(_row(full, ">Listicle<")) == ["Listicle", "60", "2", "$119.90", "3.33%", "-"]
    # (Oct 6 2026) The quiz funnel's row: its own sales, and the listicle sales it assisted.
    qz = {**lst, "rows": [*lst["rows"], {"key": "quiz", "label": "Quiz Funnel", "visitors": 9, "sales": 1, "revenue": 59.95,
                                        "conversion": 0.1111, "assists": 2, "assist_revenue": 119.9}]}
    quiz = _render(tmp_path, [["funnel", {**base, "untied_sales": 0, "counting_since": "", "listicle": qz}]])[0]
    assert _words(_row(quiz, ">Quiz Funnel<")) == ["Quiz", "Funnel", "9", "1", "$59.95", "11.1%", "2", "·", "$119.90"]
    assert quiz.index(">Listicle<") < quiz.index(">Quiz Funnel<")
    assert _words(_row(full, ">Product page<"))[-5:] == ["40", "0", "$0.00", "0%", "-"]


def test_funnel_switch_and_step_card_layout():
    # The switch sits at the top right of the section: Meta ads | Not from Meta | All, Meta ads first.
    head = PAGE[PAGE.index('id="sec-funnel"'):PAGE.index('<div class="sec-msg">', PAGE.index('id="sec-funnel"'))]
    assert re.findall(r'data-funnel="(\w+)" aria-pressed="(\w+)"', head) == [
        ("meta", "true"), ("other", "false"), ("all", "false")]
    assert re.findall(r'data-funnel="\w+"[^>]*>([^<]+)</button>', head) == ["Meta ads", "Not from Meta", "All"]
    tip = ("Shoppers who did not come from a Meta ad in the 7 days before: typed the site in, Google, email, "
           "the Shop app, returning customers.")
    assert f'data-tip="{tip}">Not from Meta</button>' in head and f'<span class="sr" id="notMetaTip">{tip}</span>' in head
    assert "everyone else" not in PAGE.lower()
    # Remembered in the URL hash like the other controls; switching redraws, it doesn't reload.
    assert "'&funnel=' + S.funnel" in _fn("writeHash") and "FUNNEL_KEYS.indexOf(fk)" in _fn("readHash")
    assert "paintFunnel()" in _fn("setFunnel") and "loadSection" not in _fn("setFunnel")
    assert "setFunnel(el.dataset.funnel)" in PAGE and "S.funnel = start.funnel;" in PAGE
    # One row of five from 1024px up; three, then two, to a row below that, never sideways.
    assert ".fsteps{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));" in CSS
    narrow = CSS[CSS.index("@media (max-width:1023px){"):]
    assert ".fsteps{grid-template-columns:repeat(3,minmax(0,1fr));" in narrow[:narrow.index("\n}")]
    phone = CSS[CSS.index("@media (max-width:640px){\n  .fsteps"):]
    assert ".fsteps{grid-template-columns:repeat(2,minmax(0,1fr))}" in phone[:phone.index("\n}")]


def _assist_rows():
    return [{"ad_id": "120002", "ad_name": "Hook B", "adset_name": "B1 Rips", "campaign_name": "sperm", "spend": 42.1,
             "assists": 3, "closers": [
                 {"ad_id": "120001", "ad_name": "Sperm UGC 3", "adset_name": "MOF 3", "sales": 2, "value": 119.9},
                 {"ad_id": "", "ad_name": "", "adset_name": "", "sales": 1, "value": 59.95}]},
            {"ad_id": "120003", "ad_name": "Static 4", "adset_name": "", "campaign_name": "", "spend": None,
             "assists": 1, "closers": [
                 {"ad_id": "120001", "ad_name": "Sperm UGC 3", "adset_name": "MOF 3", "sales": 1, "value": 59.95}]}]


@needs_node
def test_assists_section_lists_each_assisting_ad_and_the_creatives_that_got_the_sale(tmp_path):
    # Changed on purpose (P3): one row per assisting ad, its spend, the closing creatives, its assist count.
    base = {"currency": "USD", "error": "", "note": "Each row is an ad a buyer clicked before the Meta ad that got the sale."}
    full, empty, empty_but_sold, down, since = _render(tmp_path, [
        ["assists", {**base, "rows": _assist_rows(), "sales_without_assists": 1}],
        ["assists", {**base, "rows": [], "sales_without_assists": 0}],
        ["assists", {**base, "rows": [], "sales_without_assists": 3}],
        ["assists", {**base, "rows": [], "sales_without_assists": None,
                     "error": "Couldn't load orders from Shopify (Shopify answered 403)."}],
        ["assists", {**base, "rows": _assist_rows(), "sales_without_assists": 4,
                     "sales_without_assists_since": "Sep 27, 6:25 PM"}],
    ])
    # Changed on purpose (F4): "Creatives that got the sale", not "Videos".
    assert _words(full[:full.index('<div class="as-row">')]) == [
        "Assisting", "ad", "Spend", "Creatives", "that", "got", "the", "sale", "Assists"]
    assert "Videos" not in full and "videos" not in PAGE.lower()
    rows = full.split('<div class="as-row">')[1:]
    assert len(rows) == 2
    first = rows[0]
    # The assisting ad: ad set and campaign small on top, its name big; then its spend, the
    # closing creatives (ad set, name, value and xN), and the assist count, in that order.
    assert '<div class="as-where">B1 Rips · sperm</div><div class="as-name">Hook B</div>' in first
    assert first.index("as-ad") < first.index("as-spend") < first.index("as-cls") < first.index("as-count")
    assert "$42.10" in first[first.index("as-spend"):first.index("as-cls")]
    # Changed on purpose: each closer is a compact chip, ad set small, name, value and xN.
    assert ('<li class="as-chip"><span class="as-set">MOF 3</span><span class="as-cn">Sperm UGC 3</span>'
            '<span class="as-v">$119.90 ×2</span></li>') in first
    # Changed on purpose (F5): a closer with no id and no name is a Meta ad whose name is unknown.
    assert '<span class="as-cn">Meta ad (name unknown)</span><span class="as-v">$59.95</span>' in first
    assert "Unnamed ad" not in PAGE
    assert "sperm" not in first[first.index("as-cls"):]                  # no campaign for the closers
    assert '<div class="as-n">3</div>' in first
    # Ads not connected: spend is "-".
    assert '<div class="as-main">-</div>' in rows[1] and '<div class="as-n">1</div>' in rows[1]
    assert re.search(r'<div class="as-where blank">.</div><div class="as-name">Static 4</div>', rows[1])
    assert "1 sale had no earlier ad click." in _text(full) and base["note"] in full
    assert "4 sales since Sep 27, 6:25 PM had no earlier ad click." in _text(since)
    assert _text(empty).strip() == "No assisted sales yet. Assists count from Sep 27, 2026, when click history started."
    assert "No assisted sales yet." in empty_but_sold and "3 sales had no earlier ad click." in _text(empty_but_sold)
    assert "Shopify answered 403" in down and "No assisted sales yet" not in down and "earlier ad click" not in down


@needs_node
def test_orders_say_mrr_and_creatives_show_ad_roas(tmp_path):
    rebill = {**_order("1901", None), "type": "rebill", "type_label": "MRR", "channel": None}
    older = {**_order("1902", None), "type": "rebill", "type_label": "", "channel": None}   # no label: the page's own word
    creatives = _creatives([_creative(ad_name="Clicks win", store_sales=1, store_revenue=59.95, orders=["#c7"])])
    creatives["totals"]["ad_roas"] = 0.5
    orders, cr, crash = _render(tmp_path, [
        ["orders", {"orders": [rebill, older], "count": 2, "error": ""}],
        ["creatives", creatives],
        ["load", ["assists", "assists", {"error": "This part of the hub couldn't be loaded."}]]])
    assert orders.count('<span class="badge b-rebill">MRR</span>') == 2 and "Rebill" not in orders
    assert "Ad ROAS" in cr and "0.50x" in cr and "Sales your store traced to an ad click / ad spend" in cr
    assert "True ROAS" not in cr and "All new sales" not in cr
    assert "couldn't be loaded" in crash and 'data-retry="assists"' in crash


# Changed on purpose (Oct 5 2026, the owner's redesign): the header is our mark, the tabs and
# Shopify's all-time order count. The tracking status is a dot on the Tracking tab (its words in
# the tooltip); Updated, Refresh and Log out moved to the footer.
@needs_node
def test_header_dot_on_the_tracking_tab_says_how_tracking_is(tmp_path):
    ov = {"store": {"name": "Core Supplements", "domain": "https://getcoresupps.com", "timezone": "America/New_York"},
          "generated_at": "2026-09-27T17:05:00-04:00"}
    ok, warn, fail = _render(tmp_path, [["header", {**ov, "status": {"level": lvl}}] for lvl in ("ok", "warn", "fail")])
    assert (ok["dot"], ok["title"], ok["label"]) == ("dot ok", "Tracking: All good", "Tracking. Status: All good.")
    assert (warn["dot"], warn["title"]) == ("dot warn", "Tracking: Needs a look")
    assert (fail["dot"], fail["title"]) == ("dot fail", "Tracking: Broken")
    assert ok["mark"] == "Core HQ \u00b7 Core Supplements (getcoresupps.com)" and ok["updated"] == "Updated 5:05 PM"
    tab = PAGE[PAGE.index('id="tabHub"'):PAGE.index("</button>", PAGE.index('id="tabHub"'))]
    assert '<span class="dot mut" id="statusDot" aria-hidden="true"></span>Tracking' in tab
    # Only the mark, the tabs and the count up top; the rest sits in the footer.
    header = PAGE[PAGE.index('<header class="wrap top">'):PAGE.index("</header>")]
    for gone in ('id="refreshBtn"', 'href="/hub/logout"', 'id="updated"', "statusPill", 'id="store"'):
        assert gone not in header, gone
    footer = PAGE[PAGE.index('<footer id="hubFooter">'):PAGE.index("</footer>")]
    assert 'id="updated"' in footer and 'id="refreshBtn"' in footer and 'href="/hub/logout"' in footer


@needs_node
def test_header_dot_says_it_couldnt_check_when_the_first_load_fails(tmp_path):
    (down,) = _render(tmp_path, [["firstOverview", {"error": "This part of the hub couldn't be loaded."}]])
    assert down == {"dot": "dot fail", "label": "Tracking. Status: couldn't check."}


@needs_node
def test_header_counts_every_shopify_order_on_flip_cards(tmp_path):
    ov = {"store": {"name": "Core Supplements"}, "status": {"level": "ok"}}
    shown, more, missing = _render(tmp_path, [["header", {**ov, "orders_all_time": 3071}],
                                              ["header", {**ov, "orders_all_time": 13072}],
                                              ["header", {**ov, "orders_all_time": None}]])
    card = lambda d, g="": (f'<span class="fc{g}" data-d="{d}"><span class="fc-t"><b>{d}</b></span>'
                            f'<span class="fc-b"><b>{d}</b></span></span>')
    # One paper card per digit, a gap at the thousands; screen readers get the words.
    assert shown["cards"] == card("3") + card("0", " g") + card("7") + card("1")
    assert shown["count"] == "3,071 orders in Shopify, all time" and shown["hidden"] is False
    assert more["cards"] == card("1") + card("3") + card("0", " g") + card("7") + card("2")
    assert missing["hidden"] is True                       # never read from Shopify: no count, not a 0
    bag = PAGE[PAGE.index('<svg class="oc-bag"'):PAGE.index("</svg>", PAGE.index('<svg class="oc-bag"'))]
    assert 'fill="#fafafa"' in bag and 'fill="#000"' in bag and ">S</text>" in bag    # the bag, in black and white
    # A new order flips only the digits that changed, and every flip ends even when the page isn't drawn.
    flip = _fn("ocFlip")
    assert "if (old === d) return;" in flip and "setTimeout(finish, delay + 700);" in flip
    assert "prefers-reduced-motion: reduce" in _fn("stillMotion")


def test_layout_holds_on_narrow_windows_and_phones():
    # The net profit number stays on one line and gets a row of its own below 1120px; above it,
    # its column never gets narrower than the number, so it can't run into the breakdown.
    net = CSS[CSS.index(".hero-net{"):]
    net = net[:net.index("}")]
    assert "white-space:nowrap" in net and "overflow-wrap" not in net
    assert ".hero{display:grid;grid-template-columns:minmax(min-content,1fr) auto;" in CSS
    wide = CSS[CSS.index("@media (max-width:1120px){"):]
    assert ".hero{grid-template-columns:1fr}" in wide[:wide.index("\n}")]
    # A long "Other referral (...)" channel wraps instead of pushing the page sideways.
    assert ".b-ch{white-space:normal;" in CSS
    phone = _phone_css()
    # Tapping the status pill clears the range bar at its tallest (7 or 30 days, three rows).
    assert "scroll-margin-top:132px" in phone
    # A long campaign name sits beside its triangle.
    assert ".camp>summary{display:grid;grid-template-columns:auto minmax(0,1fr);" in phone and ".camp-s{grid-column:2}" in phone


def test_the_pnl_tab_opens_the_real_pnl_app_inside_the_hub():
    page = hub_page.HUB_HTML
    assert 'id="tabHub"' in page and 'id="tabPnl"' in page and '>P&amp;L</button>' in page
    # The P&L app itself, framed; its address comes from the server (PNL_URL), not the page.
    assert '<iframe id="pnlFrame" title="Your P&amp;L" referrerpolicy="no-referrer"></iframe>' in page
    assert "f.setAttribute('src', embedUrl(S.pnlUrl))" in page and "'&view=' + S.view" in page
    # Framed, the P&L takes its embedded look (no title row of its own, the hub's black and column).
    assert "function embedUrl(u) { return u + (u.indexOf('?') < 0 ? '?' : '&') + 'embed=1'; }" in page


def test_the_creative_tracker_has_its_own_tab_between_tracking_and_the_pnl():
    page = hub_page.HUB_HTML
    assert '<button type="button" class="apptab" id="tabCreative" aria-pressed="false">Creatives</button>' in page
    assert page.index('id="tabHub"') < page.index('id="tabCreative"') < page.index('id="tabPnl"')
    # The creative tracker app itself, framed and loaded on first open; a link can open it (view=creative).
    assert '<iframe id="creativeFrame" title="Creative tracker" referrerpolicy="no-referrer"></iframe>' in page
    assert "f.setAttribute('src', S.creativeUrl)" in page and "S.creativeUrl = data.creative_url" in page
    assert "var VIEWS = ['creative', 'pnl', 'corehub', 'agent'];" in page
    # (Oct 6 2026) Core Hub, the downloader and transcriber, after the P&L: its own frame, the server's address.
    assert '<button type="button" class="apptab" id="tabCoreHub" aria-pressed="false">Core Hub</button>' in page
    assert page.index('id="tabPnl"') < page.index('id="tabCoreHub"') < page.index('id="tabAgent"')
    assert ('<iframe id="coreHubFrame" title="Core Hub" referrerpolicy="no-referrer" '
            'allow="clipboard-read; clipboard-write"></iframe>') in page
    assert "f.setAttribute('src', S.coreHubUrl)" in page and "S.coreHubUrl = data.core_hub_url" in page
    assert "showView('corehub');" in page and "['tabCoreHub', ch]" in page
    i = page.index("getElementById('tabCreative').addEventListener")
    assert "showView('creative');" in page[i:i + 120]


def test_the_agent_has_its_own_tab_and_a_slim_ask_bar_above_assists():
    page = hub_page.HUB_HTML
    assert '<button type="button" class="apptab" id="tabAgent" aria-pressed="false">Agent</button>' in page
    assert page.index('id="tabPnl"') < page.index('id="tabAgent"')
    # The ask bar sits between the funnel and Assists, one line tall, and opens the Agent tab.
    assert page.index('id="sec-funnel"') < page.index('id="askBar"') < page.index('id="sec-assists"')
    i = page.index("getElementById('askBar').addEventListener")
    assert "showView('agent');" in page[i:i + 400]
    # Everything the model writes is escaped before any markup is added.
    assert "return esc(t).replace(" in page
    # Questions go out as hub POSTs (X-Hub-Request), to the agent route.
    assert "api('/hub/api/agent', {chat_id: AG.chat, question: question})" in page
