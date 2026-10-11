"""
The hub's two pages, served by hub.py under /hub.

HUB_HTML is the whole dashboard: inline CSS and vanilla JS. The one thing it
loads from elsewhere is the Inter font from Google Fonts (system-ui stands in
when that is blocked). It only talks to the /hub/api/* JSON endpoints, and
every string that came from Meta, Shopify, the P&L or a URL goes through esc()
before it touches the page (the P&L block writes with textContent instead).

The top section is the P&L app's own page code (pnl-server public/index.html),
copied with its names and formulas unchanged and run on the numbers
/hub/api/pnl reads from the P&L, so the hub and the P&L never disagree. The
copy is marked where it starts and ends; every change in it is marked "hub:".

LOGIN_HTML is the sign-in form. hub.py swaps the <!--error--> placeholder for
a short message after a wrong token.
"""

FONTS = """<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&amp;display=swap">"""

LOGIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<meta name="color-scheme" content="dark">
<title>Core HQ</title>
<!--fonts-->
<style>
:root{--bg:#000;--raised:#0a0a0a;--line:#1f1f1f;--line-2:#262626;--text:#fafafa;--muted:#a3a3a3;--dim:#737373;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px;background:var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
.box{width:100%;max-width:380px;background:var(--raised);border:1px solid var(--line);border-radius:12px;padding:32px 28px}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:8px}
.mark{flex:none;width:28px;height:28px;border-radius:8px;background:var(--text);color:#000;display:grid;place-items:center;font-weight:700;font-size:15px;letter-spacing:-.02em}
h1{font-size:17px;font-weight:600;letter-spacing:-.01em;margin:0}
p{color:var(--muted);margin:0 0 24px;font-size:13.5px}
label{display:block;font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:8px}
input{width:100%;height:42px;background:var(--bg);border:1px solid var(--line-2);color:var(--text);border-radius:8px;padding:0 12px;font:inherit;font-size:16px;transition:border-color .15s}
input:focus{outline:none;border-color:var(--text)}
button{margin-top:16px;width:100%;height:42px;background:var(--text);color:#000;border:0;border-radius:8px;font:inherit;font-weight:600;cursor:pointer;transition:background .15s}
button:hover{background:#e5e5e5}
button:focus-visible{outline:2px solid var(--text);outline-offset:2px}
.err{border:1px solid rgba(239,68,68,.4);background:rgba(239,68,68,.06);color:#fca5a5;border-radius:8px;padding:10px 12px;margin-bottom:16px;font-size:13px}
.err:empty{display:none}
.hint{margin:20px 0 0;font-size:12.5px;color:var(--dim)}
</style>
</head>
<body>
<main class="box">
  <div class="brand"><span class="mark" aria-hidden="true">C</span><h1>Core HQ</h1></div>
  <p>Sign in to see your store's profit, tracking and ad sales.</p>
  <form method="post" action="/hub/login">
    <div class="err" role="alert"><!--error--></div>
    <label for="token">Admin token</label>
    <input id="token" name="token" type="password" autocomplete="current-password" required autofocus>
    <button type="submit">Sign in</button>
  </form>
  <p class="hint">It's the ADMIN_TOKEN value in Railway &gt; tracker &gt; Variables. You stay signed in on this device for 90 days.</p>
</main>
</body>
</html>
""".replace("<!--fonts-->", FONTS)


HUB_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<meta name="color-scheme" content="dark">
<title>Core HQ</title>
<!--fonts-->
<style>
:root{--bg:#000;--raised:#0a0a0a;--card:#111;--line:#1f1f1f;--line-2:#262626;--line-3:#363636;
  --text:#fafafa;--muted:#a3a3a3;--dim:#737373;--faint:#525252;
  --ok:#22c55e;--warn:#f59e0b;--fail:#ef4444;
  /* The P&L code colours the MRR-at-risk meter with these: grey, lighter grey, and red once over 15% is failing. */
  --s-good:#737373;--s-warn:#d4d4d4;--s-crit:#ef4444;
  --font:Inter,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color-scheme:dark}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 var(--font);font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
a{color:var(--text);text-decoration:underline;text-decoration-color:var(--line-3);text-underline-offset:3px;transition:text-decoration-color .15s}
a:hover{text-decoration-color:var(--text)}
h1,h2,h3{margin:0;font-weight:600;letter-spacing:-.015em}
h2{font-size:20px}
h3{font-size:15px}
button{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--text);outline-offset:2px}
.wrap{max-width:1200px;margin:0 auto;padding:0 32px}
.sub{color:var(--muted);font-size:13px}
.small{font-size:12px}
.lab{font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
[data-tip].lab{cursor:help}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.foot{margin-top:16px}

/* header: our mark, the tabs, and Shopify's all-time order count on flip cards */
.top{display:flex;align-items:center;gap:14px;padding-top:18px;padding-bottom:18px}
.mark{flex:none;width:30px;height:30px;border-radius:8px;background:var(--text);color:#000;display:grid;place-items:center;font-weight:700;font-size:15px;letter-spacing:-.02em;cursor:default}
.psel{height:32px;padding:0 10px;border:1px solid var(--line-2);border-radius:10px;background:var(--raised);color:var(--text);color-scheme:dark;font:inherit;font-size:13px;font-weight:500;cursor:pointer;max-width:220px}
.psel:hover{border-color:var(--line-3)}
.apptabs{display:inline-flex;gap:2px;padding:3px;border:1px solid var(--line-2);border-radius:10px;background:var(--raised)}
.apptab{display:inline-flex;align-items:center;justify-content:center;gap:7px;height:30px;padding:0 16px;border:0;border-radius:7px;background:transparent;color:var(--muted);font:inherit;font-size:13px;font-weight:600;white-space:nowrap;cursor:pointer;transition:color .15s,background .15s}
.apptab:hover{color:var(--text)}
.apptab.on{background:var(--text);color:#000}
.apptab .dot{width:7px;height:7px}
.ocount{margin-left:auto;display:flex;align-items:center;gap:10px;cursor:default}
.oc-bag{flex:none;display:block;width:21px;height:24px}
.oc-tiles{display:flex;gap:3px;padding-bottom:2px}
.oc-lab{font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
.scount{display:flex;align-items:center;gap:6px;margin-left:8px;padding-left:18px;border-left:1px solid var(--line-2);cursor:default}
.sc-cur{font-size:20px;font-weight:700;color:var(--text);line-height:30px;padding-bottom:2px}
.sc-goal{margin-left:4px;font-size:13px;font-weight:600;color:var(--dim);white-space:nowrap}
/* The sales count needs the room of a desktop window; narrower, the order count stays on its own. */
@media (max-width:1000px){.scount{display:none}}
/* A flip card, like a desk calendar's: paper halves with a fold between them. A new digit drops in
   as two flaps, the old top half falling away, then the new bottom half landing. */
.fc{position:relative;display:block;width:21px;height:30px;perspective:160px;color:#0a0a0a;font-size:20px;font-weight:700;line-height:30px;text-align:center;letter-spacing:-.02em}
.fc.g{margin-left:5px}
.fc::before{content:"";position:absolute;left:2px;right:2px;bottom:-2px;height:2px;border-radius:0 0 3px 3px;background:#9b9b9b}
.fc::after{content:"";position:absolute;z-index:3;left:0;right:0;top:15px;height:1px;margin-top:-.5px;background:rgba(0,0,0,.34)}
.fc>span{position:absolute;left:0;right:0;height:15px;overflow:hidden;backface-visibility:hidden}
.fc>span>b{display:block;height:30px;font-weight:inherit}
.fc-t,.fc-ft{top:0;border-radius:5px 5px 0 0;background:#fafafa}
.fc-b,.fc-fb{bottom:0;border-radius:0 0 5px 5px;background:#ebebeb}
.fc-b>b,.fc-fb>b{margin-top:-15px}
.fc-ft{z-index:2;transform-origin:50% 100%;animation:fcTop .24s ease-in forwards}
.fc-fb{z-index:2;transform-origin:50% 0;transform:rotateX(90deg);animation:fcBot .24s ease-out forwards}
@keyframes fcTop{to{transform:rotateX(-90deg);filter:brightness(.7)}}
@keyframes fcBot{from{transform:rotateX(90deg);filter:brightness(.7)}to{transform:rotateX(0);filter:none}}
/* the agent: a slim ask bar on the Tracking page, a full chat in its own tab */
.askbar{display:flex;align-items:center;gap:10px;margin-top:36px;padding:6px 6px 6px 14px;border:1px solid var(--line-2);border-radius:12px;background:var(--raised);transition:border-color .15s}
.askbar:focus-within{border-color:var(--line-3)}
.askbar-k{font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);white-space:nowrap}
.askbar input{flex:1;min-width:0;height:34px;border:0;background:transparent;color:var(--text);font:inherit;font-size:14px;outline:none}
.askbar input::placeholder{color:var(--faint)}
.agent-app{padding:0 24px 24px}
.ag-wrap{max-width:860px;margin:0 auto;display:flex;flex-direction:column;min-height:calc(100vh - 120px)}
.ag-head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;padding:24px 0 16px;border-bottom:1px solid var(--line)}
.ag-head .sub{margin-top:6px}
.ag-log{flex:1;display:flex;flex-direction:column;gap:22px;padding:22px 0}
.ag-log:empty{display:none}
.ag-q{align-self:flex-end;max-width:78%;background:var(--card);border:1px solid var(--line-2);border-radius:14px 14px 4px 14px;padding:10px 14px;white-space:pre-wrap;overflow-wrap:anywhere}
.ag-a{align-self:stretch;line-height:1.6;overflow-wrap:anywhere}
.ag-a p{margin:0 0 10px}.ag-a p:last-child{margin-bottom:0}
.ag-a h4{margin:14px 0 6px;font-size:14px}
.ag-a ul,.ag-a ol{margin:6px 0 10px;padding-left:20px}.ag-a li{margin:3px 0}
.ag-a code{font:12.5px var(--mono);background:var(--raised);border:1px solid var(--line-2);border-radius:5px;padding:1px 5px}
.ag-a .tbl-wrap{margin:8px 0 10px}
.ag-a table{border-collapse:collapse;width:auto;min-width:50%;font-size:13px}
.ag-a th,.ag-a td{padding:7px 12px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
.ag-a th{font-size:11px;font-weight:500;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)}
.ag-a td.n,.ag-a th.n{text-align:right}
.ag-meta{margin-top:8px;color:var(--dim);font-size:12px}
.ag-err{color:var(--text);border:1px solid var(--line-3);background:var(--raised);border-radius:10px;padding:10px 14px}
.ag-wait{color:var(--muted);font-size:13px;display:flex;align-items:center;gap:8px}
.ag-wait .spin{display:inline-block}
.ag-start{display:flex;flex-wrap:wrap;gap:8px;padding:22px 0}
.ag-start[hidden]{display:none}
.ag-chip{height:32px;padding:0 13px;border:1px solid var(--line-2);border-radius:999px;background:var(--raised);color:var(--muted);font:inherit;font-size:13px;cursor:pointer;transition:color .15s,border-color .15s}
.ag-chip:hover{color:var(--text);border-color:var(--line-3)}
.ag-form{position:sticky;bottom:12px;display:flex;align-items:flex-end;gap:8px;padding:8px 8px 8px 14px;border:1px solid var(--line-2);border-radius:14px;background:var(--raised);box-shadow:0 0 0 6px var(--bg)}
.ag-form:focus-within{border-color:var(--line-3)}
.ag-form textarea{flex:1;min-width:0;max-height:180px;resize:none;border:0;background:transparent;color:var(--text);font:inherit;font-size:14px;line-height:1.5;padding:7px 0;outline:none}
.ag-form textarea::placeholder{color:var(--faint)}
.ag-send{height:34px;padding:0 16px;border:0;border-radius:9px;background:var(--text);color:#000;font:inherit;font-size:13px;font-weight:600;cursor:pointer}
.ag-send:disabled{opacity:.4;cursor:default}
.ag-foot{margin-top:10px;color:var(--dim);font-size:12px;text-align:center;min-height:16px}
.pnl-app{padding:0 24px 24px}
.pnl-app iframe{display:block;width:100%;height:calc(100vh - 98px);min-height:560px;border:1px solid var(--line);border-radius:12px;background:#000}
/* the Backend tab: deliveries, refunds and chargebacks */
.be-app{padding:0 24px 40px}
.be-wrap{max-width:1200px;margin:0 auto}
.be-head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;padding:24px 0 16px;border-bottom:1px solid var(--line)}
.be-head .sub{margin-top:6px;max-width:680px}
.be-body{display:flex;flex-direction:column;gap:20px;padding-top:20px}
.be-body .lst,.be-body .kpis{margin-top:0}
.be-att{list-style:none;margin:8px 0 0;padding:0}
.be-att li{display:flex;align-items:flex-start;gap:10px;padding:10px 0;border-top:1px solid var(--line)}
.be-att li:first-child{border-top:0;padding-top:4px}
.be-att .dot{margin-top:7px}
.be-att .when{margin-left:auto;white-space:nowrap;color:var(--dim);font-size:12px;padding-top:2px}
.be-bar{height:6px;border-radius:3px;background:var(--line-2);overflow:hidden;min-width:70px}
.be-bar i{display:block;height:100%;background:var(--text);border-radius:3px}
.be-bar.warn i{background:var(--warn)}
.be-two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}
.be-hist{display:grid;grid-template-columns:110px minmax(0,1fr) 44px;gap:8px 12px;align-items:center;margin-top:10px}
.be-hist .n{text-align:right;color:var(--muted)}
.be-sync{color:var(--dim);font-size:12.5px;display:flex;gap:6px 14px;align-items:center;flex-wrap:wrap}
.be-cell-sub{display:block;color:var(--dim);font-size:12px;margin-top:2px;overflow-wrap:anywhere}
.tabn{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;padding:0 5px;border-radius:999px;background:var(--fail);color:#fff;font-size:11px;font-weight:600;line-height:1}
.k-val.warn{color:var(--warn)}.k-val.fail{color:var(--fail)}
.be-reasons{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
/* the Database tab: who buys, from where, on what, and the quiz */
.db-filters{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.db-filters select{height:30px;padding:0 8px;border:1px solid var(--line-2);border-radius:8px;background:var(--raised);color:var(--text);color-scheme:dark;font:inherit;font-size:12.5px;cursor:pointer;max-width:200px}
.db-tiles{grid-template-columns:repeat(6,minmax(0,1fr))}
.db-two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}
.db-three{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}
.db-trends{list-style:none;margin:8px 0 0;padding:0}
.db-trends li{padding:8px 0;border-top:1px solid var(--line);color:var(--text)}
.db-trends li:first-child{border-top:0;padding-top:4px}
.db-pie{display:flex;align-items:center;gap:20px;margin-top:10px}
.db-pie svg{width:120px;height:120px;flex:none}
.db-legend{list-style:none;margin:0;padding:0;min-width:0;flex:1}
.db-legend li{display:flex;align-items:center;gap:8px;padding:5px 0;font-size:13px}
.db-legend i{width:10px;height:10px;border-radius:2px;flex:none}
.db-legend .n{margin-left:auto;color:var(--muted);white-space:nowrap}
.db-hbars{display:grid;grid-template-columns:minmax(90px,auto) minmax(0,1fr) auto;gap:7px 12px;align-items:center;margin-top:10px;font-size:13px}
.db-hbars .n{color:var(--muted);white-space:nowrap;text-align:right}
.db-heat{display:grid;grid-template-columns:38px repeat(24,minmax(0,1fr));gap:2px;margin-top:12px;font-size:11px;color:var(--dim)}
.db-heat .d{align-self:center}
.db-heat .c{aspect-ratio:1;border-radius:2px;background:var(--line)}
.db-heat .h{text-align:center;font-size:10px}
.db-q{border-top:1px solid var(--line);padding:12px 0}
.db-q:first-child{border-top:0}
.db-q-h{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.db-q-h b{min-width:0}
.db-q-h .sub{margin-left:auto;white-space:nowrap}
.db-q-h .quit{color:var(--warn)}
.db-q-h .quit.worst{color:var(--fail)}
.db-ans{display:grid;grid-template-columns:minmax(120px,1fr) minmax(0,2fr) auto auto;gap:5px 12px;align-items:center;margin-top:8px;font-size:12.5px;color:var(--muted)}
.db-ans .n{text-align:right;white-space:nowrap}
.db-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
#updated{color:var(--dim);font-size:12.5px;margin:0 6px}

/* controls */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;height:34px;padding:0 14px;border:1px solid var(--line-2);border-radius:8px;background:transparent;color:var(--text);font-size:13px;font-weight:500;cursor:pointer;white-space:nowrap;text-decoration:none;transition:background .15s,border-color .15s}
.btn:hover{background:var(--card);border-color:var(--line-3)}
.btn:disabled{opacity:.5;cursor:default}
.btn.primary{background:var(--text);border-color:var(--text);color:#000}
.btn.primary:hover{background:#e5e5e5;border-color:#e5e5e5}
.btn.sm{height:28px;padding:0 10px;font-size:12.5px;border-radius:7px}
.seg{display:inline-flex;gap:2px;padding:3px;background:var(--raised);border:1px solid var(--line);border-radius:10px}
.seg button{height:28px;padding:0 12px;border:0;border-radius:7px;background:none;color:var(--muted);font-size:13px;font-weight:500;cursor:pointer;white-space:nowrap;transition:color .15s,background .15s}
.seg button:hover{color:var(--text);background:var(--card)}
.seg button[aria-pressed="true"],.seg button[aria-pressed="true"]:hover{background:var(--text);color:#000}

/* sections */
.sec{border-top:1px solid var(--line);margin-top:44px;padding-top:32px;scroll-margin-top:72px}
.sec-h{display:flex;align-items:flex-end;justify-content:space-between;gap:14px 20px;flex-wrap:wrap;margin-bottom:20px}
.sec-h .sub{margin-top:6px;max-width:680px}
.sec-body{transition:opacity .2s}
.busy .sec-body{opacity:.5}
.spin{display:none;width:14px;height:14px;border:2px solid var(--line-3);border-top-color:var(--text);border-radius:50%;animation:spin .8s linear infinite}
.busy .spin{display:inline-block}
@keyframes spin{to{transform:rotate(360deg)}}
.skel{height:14px;border-radius:6px;margin:10px 0;background:var(--card);animation:pulse 1.4s ease-in-out infinite}
.skel.tall{height:120px;border-radius:12px}
@keyframes pulse{50%{opacity:.45}}
@media (prefers-reduced-motion:reduce){.spin,.skel{animation:none}}
.errbox{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;border:1px solid rgba(239,68,68,.35);background:rgba(239,68,68,.06);color:#fca5a5;border-radius:12px;padding:12px 14px;margin-bottom:12px}
.note{border:1px solid var(--line);background:var(--raised);color:var(--muted);border-radius:12px;padding:12px 14px;margin:12px 0}
.note.warn{border-color:var(--line-3);color:var(--text)}
.latest{font-size:12px;color:var(--dim);margin:10px 0 14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.latest b{color:var(--text);font-weight:600}
.note.warn::before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--fail);margin-right:9px;vertical-align:2px}
.empty{color:var(--muted);padding:28px 16px;text-align:center;border:1px dashed var(--line-2);border-radius:12px}
.err-t{color:#f87171}
.ok-t{color:var(--text)}
.mono{font-family:var(--mono);font-size:12px}
.down{display:flex;flex-direction:column;align-items:flex-start;gap:10px;padding:32px 28px;background:var(--raised);border:1px solid var(--line);border-radius:12px}
.down-t{display:flex;align-items:center;gap:10px;font-size:20px;font-weight:600;letter-spacing:-.02em}
.down .row{margin-top:6px}

/* dots and badges */
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--dim);flex:none}
.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}.dot.fail{background:var(--fail)}.dot.mut{background:var(--faint)}
.badge{display:inline-flex;align-items:center;gap:6px;height:22px;padding:0 9px;border-radius:999px;border:1px solid var(--line-2);font-size:12px;font-weight:500;white-space:nowrap;color:var(--muted)}
.b-new,.b-ad,.b-live{color:var(--text);border-color:var(--line-3)}
.b-sub{color:#000;background:var(--text);border-color:var(--text)}
.b-rebill,.b-ch{color:var(--muted)}
.b-ch{white-space:normal;height:auto;min-height:22px;padding:2px 9px;overflow-wrap:anywhere}
.b-lst{color:var(--text);border-color:var(--text)}
.b-skip{color:var(--dim);white-space:normal;height:auto;min-height:22px;padding:2px 9px}
.b-warn{color:var(--warn);border-color:rgba(245,158,11,.4)}
.b-prov{height:20px;padding:0 8px;font-size:10px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--text);border-color:var(--line-3);cursor:help}
.tag,.tag-view{display:inline-block;font-size:11px;font-weight:500;line-height:18px;color:var(--muted);border:1px solid var(--line-2);border-radius:999px;padding:0 7px;white-space:nowrap}
.tag-view{margin-top:4px;cursor:help}

/* approvals */
.approvals{margin-top:4px;background:var(--raised);border:1px solid var(--line-3);border-radius:12px;padding:20px}
.ap-h{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.ap-h h2{font-size:16px}
.ap-list{display:grid;gap:10px}
.ap-card{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 20px;align-items:center;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.ap-t{font-weight:600;overflow-wrap:anywhere}
.ap-d{grid-column:1;color:var(--muted);font-size:13px;overflow-wrap:anywhere}
.ap-a{grid-column:2;grid-row:1/3;display:flex;gap:8px}
.ap-r{grid-column:1/-1;font-size:13px;margin-top:6px}
.ap-r:empty{display:none}

/* P&L */
.hero{display:grid;grid-template-columns:minmax(min-content,1fr) auto;align-items:end;gap:28px 48px;background:var(--raised);border:1px solid var(--line);border-radius:12px;padding:28px}
.hero-label{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:11px;font-weight:600;letter-spacing:.1em;color:var(--muted)}
.hero-range{color:var(--dim);font-weight:500;letter-spacing:.02em}
.hero-net{font-size:clamp(40px,7vw,68px);font-weight:600;letter-spacing:-.04em;line-height:1;margin:16px 0 12px;white-space:nowrap}
.hero-sub{color:var(--muted);font-size:13px}
.hero-breakdown{display:grid;grid-template-columns:repeat(5,auto);gap:10px 32px}
.hero-item{display:flex;flex-direction:column;gap:4px;min-width:0}
.hero-k{font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
.hero-v{font-size:22px;font-weight:600;letter-spacing:-.025em;line-height:1.15}
.hero-item.neg .hero-v{color:var(--muted)}
.hero-item.sum{padding-left:32px;border-left:1px solid var(--line-2)}
.hero-item.sum .hero-v{color:var(--text)}
.hero-parts{grid-column:1/-1;color:var(--dim);font-size:12px;padding-top:12px;border-top:1px solid var(--line);max-width:640px}
.hero-parts:empty{display:none}
.money-strip{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));background:var(--raised);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.m-tile{padding:14px 16px;min-width:0;border-left:1px solid var(--line)}
.m-tile:first-child{border-left:0}
.m-tile.net{background:#111}
.m-k{font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);display:flex;align-items:center;gap:6px;white-space:nowrap}
.m-v,.m-tile .hero-net{font-size:22px;font-weight:600;letter-spacing:-.025em;line-height:1.2;margin:6px 0 2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.m-s{color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@media (max-width:1100px){.money-strip{grid-template-columns:repeat(4,minmax(0,1fr))}.m-tile{border-top:1px solid var(--line)}.m-tile:nth-child(-n+4){border-top:0}.m-tile:nth-child(4n+1){border-left:0}}
@media (max-width:640px){.money-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.m-tile{border-left:1px solid var(--line);border-top:1px solid var(--line)}.m-tile:nth-child(-n+2){border-top:0}.m-tile:nth-child(2n+1){border-left:0}}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:12px}
.kpi{display:flex;flex-direction:column;min-width:0;background:var(--raised);border:1px solid var(--line);border-radius:12px;padding:18px 18px 14px;transition:border-color .15s}
.kpi:hover{border-color:var(--line-3)}
.kpi.wide{grid-column:span 2}
.k-label{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
.k-val{font-size:28px;font-weight:600;letter-spacing:-.035em;line-height:1.1;margin-top:12px;overflow-wrap:anywhere}
.k-sub{color:var(--muted);font-size:12px;margin-top:6px}
.k-warn{color:var(--text);margin-top:4px}
.spark{height:38px;margin-top:auto;padding-top:12px;box-sizing:content-box}
.spark svg{display:block;overflow:visible}
.spark polyline{stroke:#595959}
.spark circle{fill:var(--text);stroke:var(--raised)}
.kpi-meter{height:4px;border-radius:2px;background:var(--line-2);margin-top:16px;overflow:hidden}
.kpi-meter i{display:block;height:100%;border-radius:2px;transition:width .4s ease,background .3s}

/* range bar */
.bar{position:sticky;top:0;z-index:20;margin-top:44px;background:rgba(0,0,0,.86);-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.bar-in{display:flex;align-items:center;gap:10px 16px;flex-wrap:wrap;padding:12px 0}
#rangeText{color:var(--text);font-size:13px;font-weight:500}
.bar-hint{margin-left:auto;color:var(--dim);font-size:12px}
.bar+.sec{border-top:0;margin-top:0}

/* funnel: five step cards in one row, the share of the step above on the arrow between them */
.fsteps{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px 44px}
.fstep{position:relative;min-width:0;background:var(--raised);border:1px solid var(--line);border-radius:12px;padding:18px 18px 16px}
.fstep.end{border-color:var(--line-3)}
.fs-k{font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fs-v{font-size:30px;font-weight:600;letter-spacing:-.035em;line-height:1.1;margin-top:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fs-rate{position:absolute;top:50%;left:-45px;width:44px;transform:translateY(-50%);display:flex;flex-direction:column;align-items:center;font-size:12.5px;font-weight:600;line-height:1.25;white-space:nowrap}
.fs-arrow{color:var(--faint);font-size:15px;font-weight:400}
.fs-of{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.fs-bought{margin:16px 0 0;color:var(--muted)}
.fs-bought b{color:var(--text);font-weight:600}
.f-notes{margin:6px 0 0;color:var(--dim)}
.lst{background:var(--raised);border:1px solid var(--line);border-radius:12px;padding:20px;min-width:0;margin-top:20px}
.f-h{display:flex;align-items:baseline;gap:8px 10px;flex-wrap:wrap;margin-bottom:6px}
.f-h b{font-weight:600}
.f-h .sub{margin-left:auto}
.lst .tbl{margin-top:8px}
.lst .sub.small{margin:12px 0 0;color:var(--dim)}
/* Under 1024px the cards wrap, 3 to a row (2 on phones), and the share sits inside each card. */
@media (max-width:1023px){
  .fsteps{grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
  .fs-rate{position:static;transform:none;width:auto;display:block;margin-top:8px;font-weight:500;color:var(--muted)}
  .fs-arrow{display:none}
  .fs-of{position:static;width:auto;height:auto;overflow:visible;clip:auto}
}
@media (max-width:640px){
  .fsteps{grid-template-columns:repeat(2,minmax(0,1fr))}
  .fstep{padding:14px}
  .fs-v{font-size:24px}
}

/* creatives */
.kstrip{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));background:var(--raised);border:1px solid var(--line);border-radius:12px;margin-bottom:16px}
.kcell{padding:16px 18px;min-width:0}
.kcell+.kcell{border-left:1px solid var(--line)}
.kcell:nth-child(4){border-left-color:var(--line-3)}
.kcell .v{font-size:22px;font-weight:600;letter-spacing:-.03em;line-height:1.2;margin-top:8px;overflow-wrap:anywhere}
.kcell .sub{font-size:12px;margin-top:3px;color:var(--dim)}
.camp{margin-top:12px;background:var(--raised);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.camp>summary{cursor:pointer;padding:16px 18px;display:flex;flex-wrap:wrap;gap:4px 16px;align-items:baseline;list-style:none;transition:background .15s}
.camp>summary:hover{background:#0e0e0e}
.camp>summary::-webkit-details-marker{display:none}
.camp>summary::before{content:"\25B8";color:var(--muted);font-size:12px;transition:transform .15s}
.camp[open]>summary::before{transform:rotate(90deg)}
.camp[open]>summary{border-bottom:1px solid var(--line)}
.camp-n{font-weight:600;font-size:15px;overflow-wrap:anywhere}
.camp-s{color:var(--muted);font-size:12.5px}
.tbl-wrap{overflow-x:auto}
.tbl{width:100%;border-collapse:collapse}
.tbl th{font-size:11px;font-weight:500;letter-spacing:.07em;text-transform:uppercase;color:var(--dim);text-align:left;padding:11px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
.tbl td{padding:12px;border-bottom:1px solid var(--line);vertical-align:top;transition:background .15s}
.tbl td .v{min-width:0;overflow-wrap:anywhere}
.tbl tbody:last-child tr:last-child td{border-bottom:0}
.tbl tbody tr:hover td{background:#0d0d0d}
.tbl .num,.tbl th.num{text-align:right;white-space:nowrap}
.ads{table-layout:fixed;min-width:820px}
.ads th:nth-child(2),.ads th:nth-child(6){width:104px}
.ads th:nth-child(3){width:128px}
.ads th:nth-child(4){width:92px}
.ads th:nth-child(5){width:80px}
.ads th:nth-child(7){width:156px}
.ads tr.grp td{background:none;border-top:1px solid var(--line-2);color:var(--muted);font-size:13px;padding-top:10px;padding-bottom:10px}
.ads tr.grp b{font-weight:600;color:var(--text)}
.q{color:var(--faint)}
.ads tr.sold td:first-child{box-shadow:inset 2px 0 0 var(--text)}
.ads tr.msold td:first-child{box-shadow:inset 2px 0 0 var(--faint)}
.ads tr.more td,.ads tr.more:hover td{background:none;color:var(--dim);font-size:12px;padding-top:8px;padding-bottom:12px}
.camp-more{margin:0;padding:12px 18px 14px;color:var(--dim);font-size:12px}
.tbl-wrap+.camp-more{border-top:1px solid var(--line)}
.ad-name{font-weight:600;overflow-wrap:anywhere}
.ad-name .tag{margin-left:6px;vertical-align:1px}
.ords{color:var(--dim);font-size:12px;margin-top:4px;overflow-wrap:anywhere}
.msplit{display:block;white-space:normal;color:var(--dim);font-size:11.5px;margin-top:2px}
.assist{color:var(--muted)}
.assist[data-tip]{border-bottom:1px dotted var(--dim);cursor:help}
.roas-s{font-weight:600}
.roas-m{display:block;color:var(--dim);font-size:12px;margin-top:2px}
.setup{border:1px solid var(--line-2);background:var(--raised);border-radius:12px;padding:18px 20px;margin-bottom:16px}
.setup ol{margin:12px 0;padding-left:20px;color:var(--muted)}
.setup li{margin:6px 0}
details.mini{margin-top:18px;border-top:1px solid var(--line);padding-top:14px}
details.mini>summary{cursor:pointer;color:var(--muted);font-weight:500;font-size:13px;transition:color .15s}
details.mini>summary:hover{color:var(--text)}
.copyrow{display:flex;gap:8px;align-items:flex-start;margin:10px 0}
.code{flex:1;min-width:0;display:block;background:var(--raised);border:1px solid var(--line-2);border-radius:8px;padding:10px 12px;font:12.5px/1.55 var(--mono);color:var(--muted);word-break:break-all;-webkit-user-select:all;user-select:all}
.urlbox p{margin:10px 0}

/* assists */
.as-list{background:var(--raised);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.as-head,.as-row{display:grid;grid-template-columns:minmax(0,1fr) 96px minmax(0,1.7fr) 64px;column-gap:20px;padding-left:18px;padding-right:18px}
.as-head{padding-top:10px;padding-bottom:10px;border-bottom:1px solid var(--line)}
.as-head span{font-size:10.5px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
.as-head .r,.as-spend,.as-count{text-align:right}
.as-row{padding-top:11px;padding-bottom:11px;align-items:center;transition:background .15s}
.as-row+.as-row{border-top:1px solid var(--line)}
.as-row:hover{background:#0d0d0d}
.as-ad{min-width:0}
.as-where{color:var(--dim);font-size:11.5px;line-height:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.as-where.blank{display:none}
.as-name{font-size:14px;font-weight:600;line-height:20px;letter-spacing:-.005em;overflow-wrap:anywhere}
.as-main{font-size:13.5px;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
.as-cls{min-width:0}
.as-cls ul{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:6px}
.as-chip{display:inline-flex;align-items:baseline;gap:7px;max-width:100%;padding:4px 10px;border:1px solid var(--line-2);border-radius:999px;background:#0a0a0a;font-size:12.5px;line-height:18px}
.as-set{color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:140px}
.as-set:empty{display:none}
.as-cn{font-weight:600;overflow-wrap:anywhere}
.as-v{color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}
.as-n{font-size:20px;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.m-lab{display:none}

/* tracking health */
.status-top{display:flex;align-items:flex-start;gap:14px;margin-bottom:20px}
.light{flex:none;width:12px;height:12px;border-radius:50%;margin-top:12px;background:var(--dim)}
.light.ok{background:var(--ok)}.light.warn{background:var(--warn)}.light.fail{background:var(--fail)}
.status-text{min-width:0;overflow-wrap:anywhere}
.headline{font-size:28px;font-weight:600;letter-spacing:-.03em;line-height:1.25}
.reasons{margin:8px 0 0;padding-left:18px;color:var(--muted)}
.reasons li{margin:3px 0}
.checks{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(260px,100%),1fr));gap:10px}
.check{display:flex;gap:12px;background:var(--raised);border:1px solid var(--line);border-radius:12px;padding:14px;min-width:0;transition:border-color .15s}
.check:hover{border-color:var(--line-3)}
.check.fail{border-color:rgba(239,68,68,.4)}
.check-t{min-width:0;flex:1}
.check-n{display:flex;gap:8px;align-items:baseline}
.check-n b{font-weight:600}
.check .sub{overflow-wrap:anywhere;margin-top:4px;font-size:12.5px}
.check .dot{margin-top:7px}
.word{margin-left:auto;font-size:10.5px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;flex:none;color:var(--dim)}
.word.warn{color:var(--text)}.word.fail{color:var(--fail)}
.wd{margin-top:22px}
.wd-h{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:13px;margin-bottom:10px}
.legend{display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap;color:var(--dim)}
.legend .dot{margin-left:8px}
.strip{position:relative;height:32px;background:var(--raised);border:1px solid var(--line);border-radius:8px}
.tick{position:absolute;top:7px;bottom:7px;width:3px;margin-left:-1.5px;border-radius:2px;background:var(--ok);cursor:pointer}
.tick::before{content:"";position:absolute;inset:-7px -3px}
.tick.warn{background:var(--warn)}
.tick.fail{background:var(--fail)}
.tick:hover{box-shadow:0 0 0 2px var(--raised),0 0 0 3px var(--text)}
.strip-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--dim);font-size:12.5px}
.strip-axis{display:flex;justify-content:space-between;color:var(--faint);font-size:11.5px;margin-top:6px}
.hist{list-style:none;margin:10px 0 0;padding:0}
.hist li{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);font-size:13px}
.hist li .dot{margin-top:6px}
.run{margin-top:14px;background:var(--raised);border:1px solid var(--line);border-radius:12px;padding:14px}
.run-h{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px}
#runMsg:empty{display:none}
#runMsg{margin-top:12px}
#modeLine .badge{margin-right:6px}

/* match quality */
.q-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr));gap:12px}
.qcard{background:var(--raised);border:1px solid var(--line);border-radius:12px;padding:20px;min-width:0;transition:border-color .15s}
.qcard:hover{border-color:var(--line-3)}
.q-h{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.q-h b{font-weight:600}
.q-score{display:flex;align-items:flex-end;gap:16px;margin:18px 0 6px}
.score{font-size:52px;font-weight:600;line-height:1;letter-spacing:-.045em}
.score .of{font-size:16px;color:var(--dim);font-weight:500;margin-left:3px;letter-spacing:0}
.q-word{display:inline-flex;align-items:center;gap:7px;font-weight:600}
.q-sec{margin-top:16px}
.q-sec>.sub{margin-bottom:8px}
.chips{display:flex;flex-wrap:wrap;gap:4px}
.chip{display:inline-block;font-size:11.5px;padding:1px 8px;border-radius:6px;border:1px solid var(--line);color:var(--faint);white-space:nowrap}
.chip.on{color:var(--text);border-color:var(--line-3)}
.meter-row{display:grid;grid-template-columns:84px minmax(0,1fr) 42px;gap:12px;align-items:center;margin:9px 0;font-size:13px;color:var(--muted)}
.meter{height:4px;border-radius:2px;background:var(--line-2);overflow:hidden}
.meter i{display:block;height:100%;background:var(--text);border-radius:2px}
.meter-row .num{text-align:right;color:var(--text)}
.sp{position:relative;height:36px;margin-top:10px;color:var(--text)}
.sp svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.sp-a{fill:currentColor;opacity:.06}
.sp-l{fill:none;stroke:currentColor;stroke-width:1.5;stroke-linejoin:round;stroke-linecap:round}
.sp .end{position:absolute;width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 0 2px var(--raised);transform:translate(-50%,-50%);pointer-events:none}
.sp .pt{position:absolute;width:5px;height:5px;border-radius:50%;background:currentColor;transform:translate(-50%,-50%);pointer-events:none}
.sp .hit{position:absolute;top:-6px;bottom:-6px}
.sp .hit:hover::after{content:"";position:absolute;top:6px;bottom:6px;left:var(--c);border-left:1px solid rgba(250,250,250,.35)}

/* orders */
.orders .items{color:var(--dim);font-size:12.5px;margin-top:3px;max-width:280px;overflow-wrap:anywhere}
.orders .when{display:block;color:var(--dim);font-size:12.5px;white-space:nowrap;margin-top:1px}
.px{display:block;white-space:nowrap;font-size:12.5px}
.px .i{display:inline-block;width:15px;font-weight:600}
.px.yes .i{color:var(--text)}
.px.no .i{color:var(--fail)}
.px.wait .i{color:var(--warn)}
.px.na{color:var(--dim)}
.adpath{font-size:12.5px;margin-top:5px;max-width:260px;overflow-wrap:anywhere;color:var(--muted)}
.adpath b{color:var(--text);font-weight:600}
.adpath .gt{color:var(--faint)}
.adpath.where{color:var(--dim);margin-top:2px}
.helped{color:var(--text)}
.helped[data-tip]{border-bottom:1px dotted var(--dim);cursor:help}
.orders .chips{max-width:210px}
.st{display:inline-flex;align-items:center;gap:7px;font-weight:500;white-space:nowrap}
.resend{margin-top:8px}
.rs{font-size:12px;margin-top:5px;max-width:240px}
.rs:empty{display:none}
.err-small{font-size:12px;margin-top:4px;max-width:260px;overflow-wrap:anywhere}

/* tools */
.tool{background:var(--raised);border:1px solid var(--line);border-radius:12px;padding:20px}
.tool h3{margin-bottom:6px}
.tool .sub{max-width:680px}
.form{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;margin-top:16px}
.form label{display:flex;flex-direction:column;gap:7px;font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);min-width:0}
.form input,.form select{height:36px;background:var(--bg);border:1px solid var(--line-2);color:var(--text);border-radius:8px;padding:0 10px;font:inherit;font-size:13px;letter-spacing:0;text-transform:none;min-width:0;max-width:100%;transition:border-color .15s}
.form input:focus,.form select:focus{outline:none;border-color:var(--text)}
.form select{min-width:220px}
.out{margin-top:12px}
.out:empty{display:none}

#tip{position:fixed;z-index:50;left:0;top:0;pointer-events:none;background:var(--card);border:1px solid var(--line-3);color:var(--text);font-size:12px;padding:7px 9px;border-radius:8px;box-shadow:0 10px 30px rgba(0,0,0,.6);max-width:280px;opacity:0;transition:opacity .08s}
#tip.on{opacity:1}
footer{color:var(--faint);font-size:12px;text-align:center;padding:40px 16px 32px}
footer a,.linkbtn{color:var(--dim)}
.linkbtn{padding:0;border:0;background:none;font:inherit;cursor:pointer;text-decoration:underline;text-decoration-color:var(--line-3);text-underline-offset:3px}
.linkbtn:hover{color:var(--text)}
.linkbtn:disabled{opacity:.5;cursor:default}
.foot-note{margin-top:6px}

/* The net profit number keeps a row of its own before the breakdown would squeeze it. */
@media (max-width:1120px){
  .hero{grid-template-columns:1fr}
  .hero-breakdown{grid-template-columns:repeat(5,minmax(0,1fr));gap:10px 20px}
  .hero-item.sum{padding-left:20px}
}
@media (max-width:980px){
  .kstrip{grid-template-columns:repeat(3,minmax(0,1fr))}
  .kcell:nth-child(4){border-left:0}
  .kcell:nth-child(n+4){border-top:1px solid var(--line)}
}
@media (max-width:720px){
  .wrap{padding:0 16px}
  .agent-app{padding:0 16px 16px}
  .be-app{padding:0 16px 16px}
  .be-two{grid-template-columns:1fr}
  .be-att .when{display:none}
  .be-hist{grid-template-columns:90px minmax(0,1fr) 40px}
  .db-two,.db-three{grid-template-columns:1fr}
  .db-tiles{grid-template-columns:repeat(2,minmax(0,1fr))}
  .db-heat{grid-template-columns:30px repeat(24,minmax(0,1fr))}
  .db-heat .h{font-size:8px}
  .db-ans{grid-template-columns:1fr auto}
  .db-ans .bar-cell{display:none}
  .ag-q{max-width:92%}
  .askbar-k{display:none}
  .top{flex-wrap:wrap;gap:12px;padding-top:14px;padding-bottom:14px}
  .apptabs{order:3;width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}
  .apptab{flex:0 0 auto;padding:0 10px}
  .pnl-app iframe{height:calc(100vh - 132px)}
  .sec{margin-top:36px;padding-top:26px;scroll-margin-top:132px}
  .bar{margin-top:36px}
  h2{font-size:18px}
  .sec-h{align-items:flex-start}
  .sec-h>.row{width:100%;flex-wrap:nowrap}
  .sec-h>.row>.seg{flex:1;min-width:0}
  .seg{display:flex;width:100%}
  .seg button{flex:1 1 0;min-width:0;padding:0 4px}
  .hero{padding:22px 20px;gap:22px}
  .hero-breakdown{grid-template-columns:repeat(2,minmax(0,1fr));gap:14px 16px}
  .hero-item.sum{grid-column:1/-1;padding-left:0;border-left:0;padding-top:14px;border-top:1px solid var(--line)}
  .hero-v{font-size:20px}
  .kpis{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
  .kpi{padding:16px 14px 12px}
  .k-val{font-size:24px}
  .kstrip{grid-template-columns:repeat(2,minmax(0,1fr))}
  .kcell{padding:14px}
  .kcell+.kcell,.kcell:nth-child(4){border-left:0}
  .kcell:nth-child(even){border-left:1px solid var(--line)}
  .kcell:nth-child(n+3){border-top:1px solid var(--line)}
  .kcell .v{font-size:20px}
  .lst{padding:16px}
  .camp>summary{display:grid;grid-template-columns:auto minmax(0,1fr);column-gap:10px;row-gap:4px}
  .camp-s{grid-column:2}
  .as-list{background:none;border:0;border-radius:0;overflow:visible}
  .as-head{display:none}
  .as-row{grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"ad count" "cls cls";row-gap:10px;padding:14px;background:var(--raised);border:1px solid var(--line);border-radius:12px;margin-bottom:8px}
  .as-row+.as-row{border-top:1px solid var(--line)}
  .as-ad{grid-area:ad}
  .as-spend{display:none}
  .as-count{grid-area:count}
  .as-cls{grid-area:cls}
  .m-lab{display:block;font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
  .as-cls>.m-lab{margin-bottom:6px}
  .ap-card{grid-template-columns:1fr}
  .ap-a{grid-column:1;grid-row:auto;margin-top:8px}
  .headline{font-size:23px}
  .bar-hint{margin-left:0}
  .form input,.form select{font-size:16px}
  .form label,.form .btn{width:100%}
  .form select{min-width:0}
  .tbl-wrap{overflow:visible}
  .tbl thead{display:none}
  .tbl,.tbl tbody,.tbl tr,.tbl td{display:block;width:100%}
  .tbl tr{background:var(--raised);border:1px solid var(--line);border-radius:12px;padding:6px 14px;margin:0 0 10px}
  .tbl td{display:grid;grid-template-columns:92px minmax(0,1fr);gap:10px;padding:9px 0;border-bottom:1px solid var(--line);text-align:left}
  .tbl tr td:last-child{border-bottom:0}
  .tbl tbody tr:hover td{background:none}
  .tbl td::before{content:attr(data-label);color:var(--dim);font-size:11px;font-weight:500;letter-spacing:.06em;text-transform:uppercase;padding-top:2px}
  .tbl .num{text-align:left;white-space:normal}
  .lst .tbl tr{background:var(--bg)}
  .camp .tbl{padding:10px 10px 0}
  .camp .tbl tr{background:var(--bg)}
  .ads{min-width:0}
  .ads tr.grp td{background:none}
  .ads tr.grp{border-color:var(--line-3)}
  .ads tr.sold{box-shadow:inset 2px 0 0 var(--text)}
  .ads tr.sold td:first-child,.ads tr.msold td:first-child{box-shadow:none}
  .ads tr.more{background:none;border:0;padding:0 4px;margin:-2px 0 10px}
  .ads tr.more td{display:block;padding:0;border:0}
  .ads tr.more td::before{content:none}
  .orders .items,.adpath,.orders .chips,.rs,.err-small{max-width:none}
}
@media (max-width:480px){
  #presets{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))}
}
</style>
</head>
<body>
<header class="wrap top">
  <h1 class="sr">Core HQ</h1>
  <span class="mark" id="mark" title="Core HQ" aria-hidden="true">C</span>
  <nav class="apptabs" aria-label="Views">
    <button type="button" class="apptab on" id="tabHub" aria-pressed="true" title="Tracking: checking"><span class="dot mut" id="statusDot" aria-hidden="true"></span>Tracking</button>
    <button type="button" class="apptab" id="tabPnl" aria-pressed="false">P&amp;L</button>
    <button type="button" class="apptab" id="tabCoreHub" aria-pressed="false">Core Hub</button>
    <button type="button" class="apptab" id="tabCreative" aria-pressed="false">Creatives</button>
    <button type="button" class="apptab" id="tabDatabase" aria-pressed="false">Database</button>
    <button type="button" class="apptab" id="tabBackend" aria-pressed="false">Backend<span class="tabn" id="beBadge" hidden></span></button>
    <button type="button" class="apptab" id="tabLoom" aria-pressed="false">Loom</button>
    <button type="button" class="apptab" id="tabAgent" aria-pressed="false">Agent</button>
  </nav>
  <div class="ocount" id="ocount" hidden>
    <svg class="oc-bag" viewBox="0 0 24 28" aria-hidden="true" focusable="false"><path d="M6.9 8.2C7 4.4 8.7 1.6 11 1.6c1.7 0 2.8 1.4 3.3 3.5" fill="none" stroke="#fafafa" stroke-width="1.7" stroke-linecap="round"/><path d="M2.6 7.7L16.2 5.9 18.4 26.4.9 24.9z" fill="#fafafa"/><path d="M16.2 5.9l3.4 1.1 3 17.6-4.2 1.8z" fill="#8c8c8c"/><text x="12.4" y="21.2" text-anchor="middle" font-size="13" font-weight="800" fill="#000" transform="skewX(-8)">S</text></svg>
    <span class="oc-tiles" id="ocTiles" aria-hidden="true"></span>
    <span class="oc-lab" aria-hidden="true">orders</span>
    <span class="sr" id="ocText"></span>
  </div>
  <div class="scount" id="scount" hidden>
    <span class="sc-cur" aria-hidden="true">$</span>
    <span class="oc-tiles" id="scTiles" aria-hidden="true"></span>
    <span class="sc-goal" id="scGoal" aria-hidden="true"></span>
    <span class="sr" id="scText"></span>
  </div>
</header>

<noscript><div class="wrap"><div class="note warn">The hub needs JavaScript turned on.</div></div></noscript>

<main class="wrap">
  <section class="approvals" id="sec-approvals" aria-labelledby="h-approvals" hidden>
    <div class="ap-h"><span class="dot warn" aria-hidden="true"></span><h2 id="h-approvals">Waiting for your OK</h2>
      <span class="sub" id="apCount"></span><span class="spin" aria-hidden="true"></span></div>
    <div class="sec-msg"></div>
    <div class="sec-body"></div>
  </section>

  <section class="sec" id="sec-pnl" aria-labelledby="h-pnl">
    <div class="sec-h">
      <div><h2 id="h-pnl">Sales and profit</h2><div class="sub" id="pnlMeta">From your P&amp;L</div></div>
      <div class="row">
        <span class="spin" aria-hidden="true"></span>
        <div class="seg" id="presets" role="group" aria-label="P&amp;L date range">
          <button type="button" class="preset-chip active" data-preset="today" aria-pressed="true">Today</button>
          <button type="button" class="preset-chip" data-preset="yesterday" aria-pressed="false">Yesterday</button>
          <button type="button" class="preset-chip" data-preset="7" aria-pressed="false">7D</button>
          <button type="button" class="preset-chip" data-preset="30" aria-pressed="false">30D</button>
          <button type="button" class="preset-chip" data-preset="mtd" aria-pressed="false">MTD</button>
          <button type="button" class="preset-chip" data-preset="all" aria-pressed="false">All</button>
        </div>
      </div>
    </div>
    <div class="sec-msg"></div>
    <div class="sec-body"></div>
  </section>

  <nav class="bar" aria-label="Date range for the sections below">
    <div class="bar-in">
      <div class="seg" role="group" aria-label="Date range">
        <button type="button" data-range="today" aria-pressed="true">Today</button>
        <button type="button" data-range="yesterday" aria-pressed="false">Yesterday</button>
        <button type="button" data-range="7d" aria-pressed="false">7 days</button>
        <button type="button" data-range="30d" aria-pressed="false">30 days</button>
      </div>
      <span id="rangeText"></span>
      <span class="bar-hint">Funnel, creatives, assists and orders</span>
    </div>
  </nav>

  <section class="sec" id="sec-funnel" aria-labelledby="h-funnel">
    <div class="sec-h">
      <div><h2 id="h-funnel">Shopper funnel</h2><div class="sub">How far shoppers got, from visiting the store to buying.</div></div>
      <div class="row">
        <span class="spin" aria-hidden="true"></span>
        <select id="funnelProduct" class="psel" aria-label="Product" hidden>
          <option value="">All products</option>
        </select>
        <div class="seg" role="group" aria-label="Which shoppers">
          <button type="button" data-funnel="meta" aria-pressed="true">Meta ads</button>
          <button type="button" data-funnel="other" aria-pressed="false" id="notMetaBtn" aria-describedby="notMetaTip"
            data-tip="Shoppers who did not come from a Meta ad in the 7 days before: typed the site in, Google, email, the Shop app, returning customers.">Not from Meta</button>
          <button type="button" data-funnel="all" aria-pressed="false">All</button>
        </div>
        <span class="sr" id="notMetaTip">Shoppers who did not come from a Meta ad in the 7 days before: typed the site in, Google, email, the Shop app, returning customers.</span>
      </div>
    </div>
    <div class="sec-msg"></div>
    <div class="sec-body"></div>
  </section>

  <form class="askbar" id="askBar" autocomplete="off" aria-label="Ask the agent">
    <span class="askbar-k" aria-hidden="true">Agent</span>
    <input id="askInput" type="text" maxlength="2000" placeholder="Ask about your ads: what got sales today? what spent the most?" aria-label="Your question">
    <button class="btn sm" type="submit">Ask</button>
  </form>

  <section class="sec" id="sec-assists" aria-labelledby="h-assists">
    <div class="sec-h">
      <div><h2 id="h-assists">Assists</h2><div class="sub">Ads buyers clicked earlier, before the creative that got the sale.</div></div>
      <span class="spin" aria-hidden="true"></span>
    </div>
    <div class="sec-msg"></div>
    <div class="sec-body"></div>
  </section>

  <section class="sec" id="sec-creatives" aria-labelledby="h-creatives">
    <div class="sec-h">
      <div><h2 id="h-creatives">Creatives that sold</h2><div class="sub">Spend and sales per ad. Store sales are real Shopify orders the tracker tied to the last ad the buyer clicked.</div></div>
      <div class="row">
        <span class="spin" aria-hidden="true"></span>
        <div class="seg" role="group" aria-label="Group creatives">
          <button type="button" data-group="adset" aria-pressed="true">By ad set</button>
          <button type="button" data-group="batch" aria-pressed="false">By batch</button>
        </div>
      </div>
    </div>
    <div class="sec-msg"></div>
    <div class="sec-body"></div>
    <details class="mini urlbox">
      <summary>URL tracking for your ads</summary>
      <p class="sub">This tells the tracker which ad each sale came from. In Ads Manager, select your ads, click Edit, scroll to Tracking and paste this into URL parameters:</p>
      <div class="copyrow">
        <code class="code" id="urlParams">utm_source=facebook&amp;utm_medium=paid&amp;utm_campaign={{campaign.name}}&amp;utm_term={{adset.name}}&amp;utm_content={{ad.name}}&amp;campaign_id={{campaign.id}}&amp;adset_id={{adset.id}}&amp;ad_id={{ad.id}}</code>
        <button class="btn sm" type="button" data-copy="urlParams">Copy</button>
      </div>
      <p class="sub" id="urlCounts"></p>
    </details>
  </section>

  <section class="sec" id="sec-status" aria-labelledby="h-status">
    <div class="sec-h">
      <div><h2 id="h-status">Tracking health</h2><div class="sub" id="modeLine"></div></div>
      <div class="row"><span class="spin" aria-hidden="true"></span><button class="btn" type="button" id="runChecks">Run checks now</button></div>
    </div>
    <div class="sec-msg"></div>
    <div class="sec-body"></div>
    <div id="runMsg" class="sub" aria-live="polite"></div>
    <div id="runDetail"></div>
    <details class="mini" id="wdHistory">
      <summary>Problems in the last 24 hours</summary>
      <div id="wdList" class="sub">Loading&hellip;</div>
    </details>
  </section>

  <section class="sec" id="sec-quality" aria-labelledby="h-quality">
    <div class="sec-h">
      <div><h2 id="h-quality">Match quality</h2><div class="sub">How well Meta can tie your events to real people. Meta scores it out of 10; 7 or more is good.</div></div>
      <span class="spin" aria-hidden="true"></span>
    </div>
    <div class="sec-msg"></div>
    <div class="sec-body"></div>
  </section>

  <section class="sec" id="sec-orders" aria-labelledby="h-orders">
    <div class="sec-h">
      <div><h2 id="h-orders">Orders</h2><div class="sub" id="ordersSub">Newest first, with what the tracker sent to Meta for each one.</div></div>
      <span class="spin" aria-hidden="true"></span>
    </div>
    <div class="sec-msg"></div>
    <div class="sec-body"></div>
  </section>

  <section class="sec" id="sec-tools" aria-labelledby="h-tools">
    <div class="sec-h"><div><h2 id="h-tools">Tools</h2></div></div>
    <form id="testForm" class="tool" autocomplete="off">
      <h3>Send a test event</h3>
      <p class="sub">Proves a pixel's connection works. In Meta Events Manager, open the pixel, go to Test events and copy the test code (it looks like TEST12345). The event shows only there, never in ad reporting.</p>
      <div class="form">
        <label>Pixel<select id="testPixel"><option value="">Main pixel</option></select></label>
        <label>Test events code<input id="testCode" placeholder="TEST12345" maxlength="64" spellcheck="false" autocapitalize="characters"></label>
        <button class="btn primary" type="submit" id="testBtn">Send test event</button>
      </div>
      <div id="testOut" class="out" aria-live="polite"></div>
    </form>
  </section>
</main>
<section class="pnl-app" id="creativeApp" hidden aria-label="Creative tracker">
  <iframe id="creativeFrame" title="Creative tracker" referrerpolicy="no-referrer"></iframe>
</section>
<section class="pnl-app" id="loomApp" hidden aria-label="Core Loom">
  <iframe id="loomFrame" title="Core Loom" referrerpolicy="no-referrer" allow="display-capture; camera; microphone; fullscreen; picture-in-picture; autoplay; clipboard-read; clipboard-write"></iframe>
</section>
<section class="pnl-app" id="pnlApp" hidden aria-label="P&amp;L">
  <iframe id="pnlFrame" title="Your P&amp;L" referrerpolicy="no-referrer"></iframe>
</section>
<section class="pnl-app" id="coreHubApp" hidden aria-label="Core Hub">
  <iframe id="coreHubFrame" title="Core Hub" referrerpolicy="no-referrer" allow="clipboard-read; clipboard-write"></iframe>
</section>
<section class="be-app" id="databaseApp" hidden aria-label="Database">
  <div class="be-wrap">
    <div class="be-head">
      <div><h2>Database</h2><div class="sub">Who buys, from where, on what, and what the quiz says. Every sale, every abandoned checkout, every quiz taker.</div></div>
      <div class="row">
        <div class="seg" id="dbRange" role="group" aria-label="Orders from the last">
          <button type="button" data-range="today" aria-pressed="false">Today</button>
          <button type="button" data-range="7d" aria-pressed="false">7 days</button>
          <button type="button" data-range="30d" aria-pressed="true">30 days</button>
          <button type="button" data-range="90d" aria-pressed="false">90 days</button>
          <button type="button" data-range="180d" aria-pressed="false">180 days</button>
          <button type="button" data-range="365d" aria-pressed="false">1 year</button>
        </div>
      </div>
    </div>
    <div class="db-filters" style="padding-top:14px">
      <select id="dbProduct" aria-label="Product"><option value="">Every product</option></select>
      <select id="dbCountry" aria-label="Country"><option value="">Every country</option></select>
      <select id="dbLanding" aria-label="Landing page"><option value="">Every landing page</option><option value="direct">Product page</option><option value="listicle">Listicle</option><option value="quiz">Quiz</option></select>
      <select id="dbKind" aria-label="Kind of sale"><option value="">New sales and MRR</option><option value="new">New sales only</option><option value="mrr">MRR only</option></select>
      <span class="sub small" style="margin-left:auto">Download CSV:</span>
      <a class="btn sm" id="dbCsvSales" href="/hub/api/database/export?what=sales&amp;range=30d">Sales</a>
      <a class="btn sm" id="dbCsvAbandoned" href="/hub/api/database/export?what=abandoned&amp;range=30d">Abandoned</a>
      <a class="btn sm" id="dbCsvQuiz" href="/hub/api/database/export?what=quiz&amp;range=30d">Quiz</a>
    </div>
    <div class="sec-msg" id="dbMsg"></div>
    <div class="be-body" id="dbBody"></div>
  </div>
</section>
<section class="be-app" id="backendApp" hidden aria-label="Backend">
  <div class="be-wrap">
    <div class="be-head">
      <div><h2>Backend</h2><div class="sub">After the sale: where every parcel is, refunds and chargebacks. Shopify and 17TRACK, read every half hour.</div></div>
      <div class="row">
        <div class="seg" id="beRange" role="group" aria-label="Orders from the last">
          <button type="button" data-range="today" aria-pressed="false">Today</button>
          <button type="button" data-range="7d" aria-pressed="false">7 days</button>
          <button type="button" data-range="30d" aria-pressed="true">30 days</button>
          <button type="button" data-range="90d" aria-pressed="false">90 days</button>
          <button type="button" data-range="180d" aria-pressed="false">180 days</button>
          <button type="button" data-range="365d" aria-pressed="false">1 year</button>
        </div>
        <button class="btn sm" type="button" id="beSync">Sync now</button>
      </div>
    </div>
    <div class="sec-msg" id="beMsg"></div>
    <div class="be-body" id="beBody"></div>
  </div>
</section>
<section class="agent-app" id="agentApp" hidden aria-label="Agent">
  <div class="ag-wrap">
    <div class="ag-head">
      <div><h2>Agent</h2><div class="sub">Ask anything about your store and your ads. It reads the same numbers as this hub, and it can't change anything.</div></div>
      <button class="btn sm" type="button" id="agNew">New chat</button>
    </div>
    <div class="ag-log" id="agLog" aria-live="polite"></div>
    <div class="ag-start" id="agStart">
      <button type="button" class="ag-chip">What got sales today?</button>
      <button type="button" class="ag-chip">What spent the most today?</button>
      <button type="button" class="ag-chip">How is today compared to yesterday?</button>
      <button type="button" class="ag-chip">Best creatives in the last 7 days by ROAS</button>
      <button type="button" class="ag-chip">Which ads spent over $10 with no sale this week?</button>
      <button type="button" class="ag-chip">How did each product do yesterday?</button>
    </div>
    <form class="ag-form" id="agForm" autocomplete="off">
      <textarea id="agInput" rows="1" maxlength="2000" placeholder="Ask about sales, spend, creatives, the funnel, profit..." aria-label="Your question"></textarea>
      <button class="ag-send" type="submit" id="agSend" aria-label="Send">Send</button>
    </form>
    <div class="ag-foot" id="agFoot"></div>
  </div>
</section>
<footer id="hubFooter"><span id="updated">Loading&hellip;</span> &middot; <button class="linkbtn" type="button" id="refreshBtn">Refresh</button> &middot; <a href="/hub/logout">Log out</a><div class="foot-note">Refreshes every minute while this tab is open.</div></footer>
<div id="tip" role="tooltip"></div>

<template id="pnlTpl">
  <!-- One compact row. The P&L's own code (below) still fills every id it writes to;
       the ones not shown here sit in the hidden block so its numbers stay 1:1. -->
  <div class="money-strip">
    <div class="m-tile"><div class="m-k">Revenue</div><div class="m-v" id="heroRev">$0</div><div class="m-s">new sales and MRR</div></div>
    <div class="m-tile"><div class="m-k lab" id="expLab">Expenses</div><div class="m-v" id="heroExp">$0</div><div class="m-s" id="expSub">COGS, ads, fees, all</div></div>
    <div class="m-tile net"><div class="m-k">Net profit <span id="heroProv" class="badge b-prov" style="display:none" data-tip="Some products in this range have no COGS yet, so this is an estimate. Your P&amp;L marks it the same way.">est.</span></div><div class="hero-net" id="heroNet">$0</div><div class="m-s"><span id="heroMargin">-</span> margin</div></div>
    <div class="m-tile"><div class="m-k">Orders</div><div class="m-v" id="kpiOrders">0</div><div class="m-s" id="ordersShort"></div></div>
    <div class="m-tile"><div class="m-k">Meta spend</div><div class="m-v" id="kpiMeta">$0</div><div class="m-s" id="kpiRoas"></div></div>
    <div class="m-tile"><div class="m-k">MRR sales</div><div class="m-v" id="kpiMrrSales">$0</div><div class="m-s" id="kpiMrrSalesSub"></div></div>
    <div class="m-tile"><div class="m-k">MRR net</div><div class="m-v" id="kpiMrrNet">$0</div><div class="m-s" id="kpiMrrNetSub"></div></div>
  </div>
  <div id="pnlNotes"></div>
  <div hidden aria-hidden="true">
    <span id="heroRange"></span><span id="heroDays"></span><span id="heroPerDay"></span>
    <span id="heroCogs"></span><span id="heroAds"></span><span id="heroFees"></span><span id="heroExpParts"></span>
    <span id="kpiProvisional"></span><span id="kpiRev"></span><span id="kpiRevSub"></span>
    <span id="kpiProfit"></span><span id="kpiMargin"></span><span id="kpiCacSub"></span><span id="kpiOrdersSub"></span>
    <span id="kpiMrr"></span><span id="kpiMrrSub"></span><span id="kpiRisk"></span><span id="kpiRiskNote"></span>
    <i id="kpiRiskMeter"></i><span id="kpiRiskMeterNote"></span>
  </div>
</template>

<script>
/* The P&L section. From "copied from pnl-server public/index.html" to "end of
   the copy" this is the P&L page's own code with its names and formulas
   unchanged, so the hub shows exactly what the P&L shows. Changes are marked
   "hub:": dashes instead of the P&L's em dashes, "MRR" for "recurring" in
   labels, no blended ROAS, and the parts of the page the hub doesn't have
   (statement table, waterfall, product switcher, date inputs) left out. */
var PNL = (function () {
  'use strict';

  // copied from pnl-server public/index.html
  const $ = id => document.getElementById(id);
  const fmt = n => (n<0?'-$':'$')+Math.abs(n||0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  const fmtShort = n => { n=n||0; const a=Math.abs(n),s=n<0?'-$':'$'; return a>=1e6?s+(a/1e6).toFixed(2)+'M':a>=1e3?s+(a/1e3).toFixed(1)+'k':s+a.toLocaleString('en-US',{maximumFractionDigits:0}); };
  const fmtX = v => (v==null||!isFinite(v)) ? '-' : v.toFixed(2)+'x';
  const pct = v => (v==null||!isFinite(v)) ? '-' : (v*100).toFixed(0)+'%';
  const r2 = v => Math.round((v||0)*100)/100;
  const uid = () => Math.random().toString(36).slice(2,9);
  // safe nested getter: g(block,'revenue.new') -> number (0 when missing)
  const g = (o, path, d=0) => { const v = path.split('.').reduce((a,k)=>(a!=null&&a[k]!=null)?a[k]:undefined, o); return v==null?d:v; };
  // tiny DOM builder: every API string goes through createTextNode, never innerHTML
  function h(tag, attrs, ...kids){
    const el = document.createElement(tag);
    for(const [k,v] of Object.entries(attrs||{})){
      if(k==='class') el.className = v;
      else if(k==='text') el.textContent = v;
      else if(k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if(k==='style') el.style.cssText = v;
      else if(v!=null && v!==false) el.setAttribute(k, v);
    }
    for(const kid of kids.flat()){
      if(kid==null || kid===false) continue;
      el.appendChild(typeof kid==='string' || typeof kid==='number' ? document.createTextNode(String(kid)) : kid);
    }
    return el;
  }
  const ET_TZ = 'America/New_York';
  const etTime = (t) => new Date(t).toLocaleTimeString('en-US',{timeZone:ET_TZ,hour:'2-digit',minute:'2-digit'}) + ' ET';
  const etToday = () => new Date().toLocaleDateString('en-CA',{timeZone:ET_TZ});

  const BUCKETS=['revenue','cogs','ads','opex'];
  const emptyBuckets = () => ({revenue:[],cogs:[],ads:[],opex:[]});
  let state = {manual: {all: emptyBuckets()}};        // hub: state.manual.all comes from /hub/api/pnl
  const scope = () => 'all';                           // hub: always every product
  function scopeManual(){ const s=scope(); if(!state.manual[s]) state.manual[s]=emptyBuckets(); BUCKETS.forEach(b=>{ if(!Array.isArray(state.manual[s][b])) state.manual[s][b]=[]; }); return state.manual[s]; }

  let lines = emptyBuckets();          // what the bucket cards show for the current scope
  // A hand-added line is a one-off amount, not a rate, so it must only count inside the period it
  // belongs to. Undated legacy lines count only when the range reaches back far enough to contain them.
  function lineInRange(r){
    if (r.auto) return true;
    const {from,to} = curRange();
    const d = r.date && /^\d{4}-\d{2}-\d{2}$/.test(r.date) ? r.date : null;
    if (d) return d >= from && d <= to;
    return from <= '2001-01-01';          // undated => all-time only
  }
  const sumBucket = b => lines[b].reduce((a,r)=>a+(lineInRange(r) ? (parseFloat(r.val)||0) : 0),0);

  let _pnl = null;                      // whole /api/pnl payload, cached client-side
  const productById = id => (_pnl && _pnl.products || []).find(p=>String(p.product_id)===String(id)) || null;
  function emptyBlock(){
    const z=()=>({new:0,recurring:0,total:0});
    return {orders:z(),packs:z(),units:z(),revenue:{...z(),gross_sales:0,discounts:0,refunds:0},cogs:{...z(),coverage:1,covered_revenue:0,uncovered_revenue:0,uncovered_units:0},
      fees:{processing:z(),conversion:z(),total:z()},gross:z(),spend:0,net:{value:0,provisional:false},roas:{new:null,blended:null},cac:null,be_roas:null,aov:z(),
      refunds:{orders_excluded:0,partial_orders:0,partial_amount:0},by_day:[],by_currency:[],by_variant:[],campaigns:[]};
  }
  function curBlock(){
    if(!_pnl) return emptyBlock();
    const s=scope();
    if(s==='all') return _pnl.all || emptyBlock();
    if(s==='unattributed') return _pnl.unattributed || emptyBlock();
    return productById(s) || emptyBlock();
  }

  /* AUTO LINES for the selected scope (hub: the 'all' scope only) */
  function buildAutoLines(){
    const out=emptyBuckets(), s=scope(), blk=curBlock();
    const A=(b,name,val)=>{ if(Math.abs(val||0)>=0.005) out[b].push({id:uid(),name,val:r2(val),auto:true}); };
    const revLines=(p,title)=>{ A('revenue',`${title} \u00b7 new (${g(p,'orders.new')} orders)`,g(p,'revenue.new')); A('revenue',`${title} \u00b7 recurring (${g(p,'orders.recurring')} orders)`,g(p,'revenue.recurring')); };
    const cogsLines=(p,title)=>(p.by_variant||[]).forEach(v=>{ if(v.cogs>0) A('cogs',`${title} - ${v.variant_title||'default'} x ${v.packs||0} packs`,v.cogs); });
    const adLines=p=>(p.campaigns||[]).forEach(c=>A('ads',`Meta - ${c.campaign_name||c.campaign_id}`,c.spend));
    if(s==='all'){
      (_pnl.products||[]).forEach(p=>{ revLines(p,p.title); cogsLines(p,p.title); adLines(p); });
      const u=_pnl.unattributed||{};
      A('revenue',`Unattributed \u00b7 ${(u.products||[]).length} products`,g(u,'revenue.total'));
      A('cogs','Unattributed COGS',g(u,'cogs.total'));
      const unmapped=(u.campaigns||[]).filter(c=>c.status==='unmapped');
      A('ads',`Meta - unmapped (${unmapped.length} campaigns)`,unmapped.reduce((t,c)=>t+(c.spend||0),0));
    }
    A('opex','Shopify processing fees (by region)',g(blk,'fees.processing.total'));
    A('opex','Intl currency conversion + payout',g(blk,'fees.conversion.total'));
    return out;
  }
  /* contract field reader: flat headline fields (block.kpi.* / block.*) first, nested detail object as fallback */
  function K(blk, key, nested, d){
    const kp = blk && blk.kpi;
    if(kp && kp[key]!=null) return kp[key];
    if(blk && typeof blk[key]==='number') return blk[key];
    if(blk && blk[key]===null && d===undefined) return null;
    return nested ? g(blk, nested, d===undefined?0:d) : (d===undefined?0:d);
  }

  /* HERO NET COUNTER: counts from the previous value to the new one, respects prefers-reduced-motion */
  const _heroPrev = {};
  let _heroRaf = null;
  function animateTo(el, to, fmtFn){
    const from = _heroPrev[el.id] ?? to;
    _heroPrev[el.id] = to;
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || from === to || Math.abs(to - from) < 0.005){ el.textContent = fmtFn(to); return; }
    const t0 = performance.now(), dur = 420;
    const step = (now) => {
      const p = Math.min(1, (now - t0) / dur);
      const e = 1 - Math.pow(1 - p, 3);            // ease-out cubic
      el.textContent = fmtFn(from + (to - from) * e);
      if (p < 1) el._raf = requestAnimationFrame(step);
    };
    if (el._raf) cancelAnimationFrame(el._raf);
    el._raf = requestAnimationFrame(step);
  }
  function renderHero(v){
    const {from,to} = curRange();
    // "All" starts at 2000-01-01, so measure from the first day that actually has data.
    const bd = (_pnl && _pnl.by_day) || [];
    const start = (from <= '2001-01-01' && bd.length) ? bd[0].date : from;
    const days = Math.max(1, Math.round((new Date(to) - new Date(start)) / 86400000) + 1);
    const chip = document.querySelector('#presets .preset-chip.active');
    // hub: + today and yesterday, the hub's two extra chips
    $('heroRange').textContent = chip ? ({ 'today':'today','yesterday':'yesterday','7':'last 7 days','30':'last 30 days','90':'last 90 days','180':'last 180 days','mtd':'month to date','all':'all time' }[chip.dataset.preset] || `${from} \u2192 ${to}`) : `${from} \u2192 ${to}`;
    const net = $('heroNet');
    net.className = 'hero-net ' + (v.net >= 0 ? 'pos' : 'neg');
    animateTo(net, v.net, n => fmt(n));
    animateTo($('heroRev'),  v.rev,  fmtShort);
    animateTo($('heroCogs'), v.cogs, fmtShort);
    animateTo($('heroAds'),  v.ads,  fmtShort);
    animateTo($('heroFees'), v.fees, fmtShort);
    $('heroMargin').textContent = v.rev ? (v.net / v.rev * 100).toFixed(1) + '%' : '-';
    $('heroDays').textContent = days + (days === 1 ? ' day' : ' days');
    $('heroPerDay').textContent = fmt(v.net / days);
    $('heroProv').style.display = v.provisional ? '' : 'none';
  }

  /* RECALC: KPIs (hub: the hero and the cards; the statement table and waterfall aren't on this page) */
  function recalc(){
    const blk=curBlock(), s=scope();
    const rev=sumBucket('revenue'),cogs=sumBucket('cogs'),ads=sumBucket('ads'),opex=sumBucket('opex')+swToolsTotal();
    const shopifyFees=sumBucket('opex');
    const gross=rev-cogs; let net=gross-ads-shopifyFees;
    const grossM=rev?gross/rev*100:0, netM=rev?net/rev*100:0;
    const revNew=K(blk,'revenue_new','revenue.new'), revRec=K(blk,'revenue_recurring','revenue.recurring');
    const revFs=K(blk,'revenue_first_sub','revenue.first_sub'), revOo=K(blk,'revenue_one_off','revenue.one_off');
    const ordNew=K(blk,'orders_new','orders.new'), ordRec=K(blk,'orders_recurring','orders.recurring');
    const ordFs=K(blk,'orders_first_sub','orders.first_sub'), ordOo=K(blk,'orders_one_off','orders.one_off');
    const newSubs=K(blk,'new_subs','subs.new_in_range'), mrr=K(blk,'mrr_runrate','subs.mrr_runrate'), activeSubs=K(blk,'active_subs','subs.active');
    // ratios follow the edited ad-spend line so manual lines stay consistent with the statement
    const roasNew=ads?revNew/ads:null, roasBl=ads?rev/ads:null, cac=ads&&ordNew?ads/ordNew:null, cacSub=ads&&newSubs?ads/newSubs:null;
    const coverage=K(blk,'cogs_coverage','cogs.coverage',1), provisional=!!g(blk,'net.provisional',false)||coverage<0.999;

    // Store-level money that belongs to the order rather than any product line: shipping the customer
    // paid, the gap between the modelled fee and Shopify's real one, and chargebacks.
    // At 'all' the share is 1 and nothing changes.
    const st = (_pnl && _pnl.store) || {};
    const allRev = g(_pnl && _pnl.all, 'revenue.total', 0);
    const share = (s !== 'all' && allRev > 0) ? Math.max(0, Math.min(1, g(blk, 'revenue.total', 0) / allRev)) : 1;
    const cut = v => r2((v || 0) * share);
    const ship = cut(st.shipping_revenue), cb = cut(st.chargebacks), feeTrue = cut(st.fee_adjustment);
    const bills = cut(st.platform_bills);
    const sw0 = swToolsForRange();
    const sw = { ...sw0, total: cut(sw0.total) };
    const netFinal = net + ship - cb - feeTrue - bills;   // hub: software left out here on purpose (the main P&L still counts it)
    renderHero({ net: netFinal, rev: rev + ship, cogs, ads, fees: shopifyFees + feeTrue, provisional });
    renderExpenses({ net: netFinal, rev: rev + ship, cogs, ads, fees: shopifyFees + feeTrue, feeTrue, cb, bills });   // hub
    net = netFinal;
    $('kpiProvisional').style.display = provisional && _pnl ? '' : 'none';

    $('kpiRev').textContent=fmtShort(rev); $('kpiRevSub').textContent=`new ${fmtShort(revNew)} \u00b7 MRR ${fmtShort(revRec)}`;
    // Headline MRR is what is actually collecting; failing payments are called out beside it.
    const atRisk = K(blk,'mrr_at_risk','subs.mrr_at_risk', 0), failedSubs = K(blk,'overdue_subs','subs.overdue_subs', 0);
    $('kpiMrr').textContent=fmtShort(mrr);
    $('kpiMrrSub').innerHTML = '';
    $('kpiMrrSub').appendChild(document.createTextNode(`${activeSubs} active sub${activeSubs===1?'':'s'} \u00b7 ${fmtShort(revRec)} MRR in range`));
    if (atRisk > 0) {
      const w = h('div',{class:'k-warn'}, `${fmtShort(atRisk)} at risk \u00b7 ${failedSubs} failing payment`);
      $('kpiMrrSub').appendChild(w);
    }
    $('kpiProfit').textContent=fmtShort(net); $('kpiProfit').className='k-val '+(net>=0?'pos':'neg');
    $('kpiMargin').textContent=netM.toFixed(1)+'% margin';
    $('kpiMeta').textContent=fmtShort(ads);
    $('kpiRoas').textContent=`ROAS (new) ${fmtX(roasNew)}`;      // hub: blended ROAS stays off the page
    $('kpiCacSub').textContent=`CAC ${cac!=null?fmt(cac):'-'} / new order \u00b7 ${cacSub!=null?fmt(cacSub):'-'} / new sub`;
    $('kpiOrders').textContent=(ordNew+ordRec).toLocaleString();
    $('kpiOrdersSub').textContent=`${ordNew} new (${ordFs} first-sub \u00b7 ${ordOo} one-off) \u00b7 ${ordRec} MRR`;
    // Failed payments are upstream of churn: still listed as active, but their cards are declining.
    const riskAmt = K(blk,'mrr_at_risk','subs.mrr_at_risk', 0);
    const riskSubs = K(blk,'overdue_subs','subs.overdue_subs', 0);
    const mrrTotal = (mrr||0) + riskAmt;
    $('kpiRisk').textContent = fmtShort(riskAmt);
    $('kpiRiskNote').textContent = riskAmt>0
      ? `${n0(riskSubs)} subscriber${riskSubs===1?'':'s'} failing payment`
      : 'every subscription is collecting';
    const rmeter=$('kpiRiskMeter');
    if(rmeter){
      const frac = mrrTotal>0 ? Math.min(1, riskAmt/mrrTotal) : 0;
      rmeter.style.width = (frac*100).toFixed(0)+'%';
      rmeter.style.background = frac>0.15 ? 'var(--s-crit)' : frac>0.05 ? 'var(--s-warn,#f5b945)' : 'var(--s-good)';
      $('kpiRiskMeterNote').textContent = mrrTotal>0
        ? `${(frac*100).toFixed(0)}% of ${fmtShort(mrrTotal)} MRR \u00b7 recovering half is ${fmtShort(riskAmt/2)}/mo`
        : 'share of MRR not collecting';
    }
    // hub: MRR net, the range's MRR revenue less its COGS
    const cogsRec = K(blk,'cogs_recurring','cogs.recurring');
    $('kpiMrrNet').textContent = fmtShort(revRec - cogsRec);
    $('kpiMrrNetSub').textContent = `after ${fmtShort(cogsRec)} COGS`;
    // hub: MRR sales (the range's MRR revenue and orders) and a short orders line
    $('kpiMrrSales').textContent = fmtShort(revRec);
    $('kpiMrrSalesSub').textContent = `${n0(ordRec)} MRR order${ordRec===1?'':'s'}`;
    $('ordersShort').textContent = `${n0(ordNew)} new \u00b7 ${n0(ordRec)} MRR`;
  }

  /* SOFTWARE OPEX: tools billed OUTSIDE Shopify, in USD */
  let SW_TOOLS = [];                   // hub: a const in the P&L; filled from the live P&L page by /hub/api/pnl
  let DAYS_PER_MONTH = 30.44;          // hub: likewise
  // A monthly subscription has to be prorated to whatever window is on screen.
  function swToolsForRange(){
    const {from,to} = curRange();
    const bd = (_pnl && _pnl.by_day) || [];
    const start = (from <= '2001-01-01' && bd.length) ? bd[0].date : from;
    const days = Math.max(1, Math.round((new Date(to) - new Date(start)) / 86400000) + 1);
    const perMonth = SW_TOOLS.reduce((s,t)=>s+(t.monthly||0),0);
    return { total: r2(perMonth * days / DAYS_PER_MONTH), perMonth: r2(perMonth), days };
  }
  let opexView = 'monthly';
  function swToolsTotal(){
    const yearly = opexView==='yearly';
    return SW_TOOLS.reduce((sum,t)=>{
      return sum + (yearly ? (t.freq==='annual' ? t.annual : t.monthly*12) : t.monthly);
    }, 0);
  }
  const n0 = v => (v==null||!isFinite(v)) ? '-' : Number(v).toLocaleString('en-US',{maximumFractionDigits:0});

  /* SPARKLINES */
  let _byDay = [];
  const dayNew = d => d.revenue_new ?? d.revenue ?? 0, dayRec = d => d.revenue_recurring ?? 0, dayRev = d => d.revenue ?? (dayNew(d)+dayRec(d));
  function fillDays(raw){
    if(!raw || raw.length < 2) return (raw||[]).slice();
    const start = new Date(raw[0].date+'T00:00:00Z'), end = new Date(raw[raw.length-1].date+'T00:00:00Z');
    const span  = Math.round((end-start)/86400000);
    if(span > 370 || span < 1) return raw.slice();
    const map = {}; raw.forEach(r => map[r.date] = r);
    const out = [];
    for(let t = +start; t <= +end; t += 86400000){
      const k = new Date(t).toISOString().slice(0,10);
      out.push(map[k] || {date:k, revenue:0, revenue_new:0, revenue_recurring:0, cogs:0, fees:0, orders:0, units:0, spend:0, spend_account:0});
    }
    return out;
  }
  function renderSparks(){
    const days = fillDays(_byDay);
    if(days.length < 2){ ['sparkRev','sparkMrr','sparkNet','sparkMeta'].forEach(id=>{ const el=$(id); if(el) el.innerHTML=''; }); return; }
    const netVals = days.map(d => dayRev(d) - (d.cogs||0) - (d.fees||0) - (d.spend||0));
    sparkline('sparkRev',  days.map(dayRev), 'var(--s-rev)');
    sparkline('sparkMrr',  days.map(dayRec), 'var(--accent-2)');
    sparkline('sparkNet',  netVals, netVals[netVals.length-1] >= 0 ? 'var(--s-good)' : 'var(--s-crit)');
    sparkline('sparkMeta', days.map(d => d.spend||0), 'var(--s-spend)');
  }
  let _rsT;
  window.addEventListener('resize', () => { clearTimeout(_rsT); _rsT = setTimeout(() => { renderSparks(); }, 150); });
  function sparkline(id, vals, endColor){
    const el = $(id); if(!el) return;
    if(!vals || vals.length < 2){ el.innerHTML=''; return; }
    if(vals.length > 30) vals = vals.slice(-30);
    const W = el.clientWidth || 150, H = 38, P = 4;
    const mn = Math.min(...vals, 0), mx = Math.max(...vals);
    const rng = (mx - mn) || 1;
    const sx = i => P + (W-2*P) * i / (vals.length-1);
    const sy = v => P + (H-2*P) * (1 - (v-mn)/rng);
    const pts = vals.map((v,i) => `${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(' ');
    el.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
    <polyline points="${pts}" fill="none" stroke="#47474f" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${sx(vals.length-1)}" cy="${sy(vals[vals.length-1])}" r="3" fill="${endColor}" stroke="#0e0e12" stroke-width="2"/>
  </svg>`;
  }
  // end of the copy

  // --- hub: what the P&L page does around that code, for this page ---------------
  // The P&L reads its range from its date inputs; the hub from the chips (the range the server answered for).
  let RANGE = {from: etToday(), to: etToday()};
  const curRange = () => RANGE;

  // Expenses: hero revenue less net profit, so COGS + ad spend + fees (with the fee true-up) +
  // chargebacks + Shopify bills, from the same numbers recalc() just used. No software: that stays in the main P&L.
  function renderExpenses(v){
    animateTo($('heroExp'), v.rev - v.net, fmtShort);
    const parts = [['COGS', v.cogs], ['ad spend', v.ads], ['fees', v.fees]]
      .concat([['Shopify bills', v.bills], ['chargebacks', v.cb]].filter(p => Math.abs(p[1]) >= 0.005));
    $('heroExpParts').textContent = 'Expenses = ' + parts.map(p => `${p[0]} ${fmtShort(p[1])}`).join(' + ') +
      (Math.abs(v.feeTrue) >= 0.005 ? ` (fees include a ${fmtShort(v.feeTrue)} true-up to Shopify's actual)` : '');
    const lab = document.getElementById('expLab');
    if (lab) lab.setAttribute('data-tip', $('heroExpParts').textContent);   // the breakdown, on hover
    // COGS (per item sold, from the P&L's product costs) and ad spend, shown under the total.
    const sub = document.getElementById('expSub');
    if (sub) sub.textContent = `COGS ${fmtShort(v.cogs)} \u00b7 ads ${fmtShort(v.ads)} \u00b7 fees ${fmtShort(v.fees)}`;
  }

  // The P&L's chip handler (7D is today less 7 days, like the P&L), plus Today and Yesterday.
  function dayBefore(d){ const t = new Date(d + 'T12:00:00Z'); t.setUTCDate(t.getUTCDate() - 1); return t.toISOString().slice(0, 10); }
  function rangeFor(p){
    const today = etToday();
    const toStr = d => d.toLocaleDateString('en-CA',{timeZone:ET_TZ});
    let from, to = today;
    if(p === 'today') from = today;
    else if(p === 'yesterday') from = to = dayBefore(today);
    else if(p === 'all') from = '2000-01-01';
    else if(p === 'mtd') from = today.slice(0,8)+'01';
    else from = toStr(new Date(Date.now() - parseInt(p)*86400000));
    return {from, to};
  }

  // One /hub/api/pnl answer onto the section: the P&L's renderScope() for the 'all' scope.
  function show(d){
    const r = d.range || {};
    RANGE = {from: r.from || etToday(), to: r.to || etToday()};
    _pnl = d.pnl && typeof d.pnl === 'object' ? d.pnl : null;
    state.manual.all = d.manual && typeof d.manual === 'object' ? d.manual : emptyBuckets();
    SW_TOOLS = Array.isArray(d.sw_tools) ? d.sw_tools.filter(t => t && typeof t === 'object') : [];
    DAYS_PER_MONTH = +d.days_per_month || 30.44;
    const auto = _pnl ? buildAutoLines() : emptyBuckets(), man = scopeManual();
    BUCKETS.forEach(b=>{ lines[b]=[...auto[b], ...man[b]]; });
    recalc();
    _byDay = (_pnl && curBlock().by_day) || (_pnl && _pnl.by_day) || [];
    renderSparks();
  }

  return {show, rangeFor, etTime, etToday};
})();

(function () {
  'use strict';

  var RANGE_KEYS = ['today', 'yesterday', '7d', '30d'];
  var RANGE_LABEL = {today: 'Today', yesterday: 'Yesterday', '7d': 'Last 7 days', '30d': 'Last 30 days'};
  var RANGE_WORDS = {today: 'today', yesterday: 'yesterday', '7d': 'in the last 7 days', '30d': 'in the last 30 days'};
  var PNL_PRESETS = ['today', 'yesterday', '7', '30', 'mtd', 'all'];
  // The funnel's switch: which shoppers the step cards count.
  var FUNNEL_KEYS = ['meta', 'other', 'all'];
  var FUNNEL_STEPS = ['Visitors', 'Product views', 'Add to cart', 'Checkout', 'Purchases'];
  // A Meta ad the tracker knows sold (or helped) but can't name: no ad id, no name.
  var UNNAMED_AD = 'Meta ad (name unknown)';
  var HEAD = {ok: 'All good', warn: 'Needs a look', fail: 'Something is broken'};
  var PILL = {ok: 'All good', warn: 'Needs a look', fail: 'Broken'};
  var WORD = {ok: 'OK', warn: 'Look', fail: 'Broken'};
  var RANK = {ok: 0, warn: 1, fail: 2};
  var REFRESH_MS = 60000;
  // One API call feeds these page sections.
  var SECTIONS = {overview: ['status', 'quality'], proposals: ['approvals'], pnl: ['pnl'], funnel: ['funnel'],
                  creatives: ['creatives'], assists: ['assists'], orders: ['orders']};
  // The range tabs drive these. The P&L has its own chips; health and match quality have no range.
  var RANGED = ['funnel', 'creatives', 'assists', 'orders'];
  var S = {range: 'today', group: 'adset', funnel: 'meta', fdata: null, pnl: 'today', pnlUrl: '', tz: '', seq: {},
           lastLoad: 0, timer: null, ov: null, rng: null, leaving: false, resent: new Map(), closed: new Set(),
           props: [], decided: new Map(), running: new Map(), wd: null, wdAt: 0, wdP: null, beRange: '30d', be: null, dbRange: '30d', db: null,
           dbCountries: false, dbProducts: false};

  function $(sel) { return document.querySelector(sel); }
  function secEl(id) { return document.getElementById('sec-' + id); }
  function secBody(id) { return secEl(id).querySelector('.sec-body'); }

  // Everything from Meta, Shopify, the P&L or a URL passes through esc() before innerHTML.
  var ESC = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
  function esc(v) { return v == null ? '' : String(v).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }
  function lvl(s) { return s === 'ok' || s === 'warn' || s === 'fail' ? s : 'warn'; }
  function isNum(v) { return v !== null && v !== undefined && v !== '' && typeof v !== 'boolean' && isFinite(v); }
  function objects(list) {
    return (Array.isArray(list) ? list : []).filter(function (x) { return x && typeof x === 'object'; });
  }

  // --- formatting ---------------------------------------------------------
  var moneyFmt = {};
  function money(v, cur, whole) {
    if (!isNum(v)) return '-';
    var code = String(cur || 'USD').toUpperCase(), key = code + (whole ? '0' : '');
    if (!moneyFmt[key]) {
      var o = {style: 'currency', currency: code};
      if (whole) { o.minimumFractionDigits = 0; o.maximumFractionDigits = 0; }
      try { moneyFmt[key] = new Intl.NumberFormat('en-US', o); }
      catch (e) { o.currency = 'USD'; moneyFmt[key] = new Intl.NumberFormat('en-US', o); }
    }
    return moneyFmt[key].format(+v);
  }
  function num(v) {
    if (!isNum(v)) return '-';
    return (+v).toLocaleString('en-US', {maximumFractionDigits: Number.isInteger(+v) ? 0 : 1});
  }
  function roas(v) { return isNum(v) ? (+v).toFixed(2) + 'x' : '-'; }
  function pct(v) {
    if (!isNum(v)) return '-';
    return +v > 0 && +v < 1 ? '<1%' : Math.round(+v) + '%';
  }
  function ratio(a, b) { return isNum(a) && isNum(b) && +b !== 0 ? +a / +b : null; }
  function plural(n, one, many) { return num(n) + ' ' + (+n === 1 ? one : many); }
  function listShort(items, max) {
    items = (items || []).map(String);
    return items.length > max ? items.slice(0, max).join(', ') + ' +' + (items.length - max) + ' more' : items.join(', ');
  }
  function trunc(s, n) { s = s == null ? '' : String(s); return s.length > n ? s.slice(0, n - 1) + '\u2026' : s; }
  function sentence(s) { s = String(s || '').trim(); return /[.!?]$/.test(s) ? s : s + '.'; }
  function webUrl(u) { return /^https?:\/\/[^\s"'<>]+$/.test(String(u || '')) ? String(u) : ''; }

  // Watchdog and Meta times arrive as epoch seconds; generated_at as ISO.
  function toMs(v) {
    if (v == null || v === '') return null;
    if (typeof v === 'number') return v > 1e12 ? v : v * 1000;
    if (/^\d+(\.\d+)?$/.test(String(v))) return toMs(parseFloat(v));
    var t = Date.parse(v);
    return isNaN(t) ? null : t;
  }
  function fmtIn(ms, opts) {
    try { return new Intl.DateTimeFormat('en-US', Object.assign({timeZone: S.tz || undefined}, opts)).format(ms); }
    catch (e) { return new Intl.DateTimeFormat('en-US', opts).format(ms); }
  }
  function fmtClock(v, withDay) {
    var ms = toMs(v);
    if (ms == null) return '';
    var o = {hour: 'numeric', minute: '2-digit'};
    if (withDay) { o.month = 'short'; o.day = 'numeric'; }
    return fmtIn(ms, o);
  }
  function fmtDate(v) { var ms = toMs(v); return ms == null ? '' : fmtIn(ms, {month: 'short', day: 'numeric'}); }
  // 'YYYY-MM-DD' is already a store-local day: build it locally so no timezone shifts it.
  function dayLabel(d) {
    var p = String(d || '').split('-');
    if (p.length !== 3) return String(d || '');
    return new Date(+p[0], +p[1] - 1, +p[2]).toLocaleDateString('en-US', {month: 'short', day: 'numeric'});
  }
  function agoText(v) {
    var ms = toMs(v);
    if (ms == null) return 'never';
    var s = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (s < 90) return 'just now';
    if (s < 5400) return Math.floor(s / 60) + ' min ago';
    if (s < 172800) return Math.floor(s / 3600) + ' h ago';
    return Math.floor(s / 86400) + ' days ago';
  }

  // --- API ----------------------------------------------------------------
  function api(path, body) {
    var init = {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}};
    if (body !== undefined) {
      init.method = 'POST';
      init.headers['Content-Type'] = 'application/json';
      init.headers['X-Hub-Request'] = '1';
      init.body = JSON.stringify(body);
    }
    return fetch(path, init).catch(function () {
      throw new Error("Couldn't reach the tracker. Check your connection.");
    }).then(function (resp) {
      if (resp.status === 401) {
        // Signed out or the session was revoked: back to the login page.
        S.leaving = true;
        location.href = '/hub';
        var gone = new Error('Signed out');
        gone.leaving = true;
        throw gone;
      }
      return resp.json().catch(function () { return null; }).then(function (data) {
        if (!resp.ok) {
          var msg = data && data.error ? String(data.error)
            : resp.status === 429 ? 'Too many requests. Wait a minute and try again.'
            : 'The tracker answered with an error (HTTP ' + resp.status + ').';
          throw new Error(msg);
        }
        if (!data || typeof data !== 'object') throw new Error('The tracker sent back something unexpected.');
        return data;
      });
    });
  }

  // --- section states -----------------------------------------------------
  function skeleton() { return '<div class="skel tall"></div><div class="skel"></div><div class="skel" style="width:70%"></div>'; }
  function keyFor(name) {
    if (name === 'creatives') return S.range + '|' + S.group;
    if (name === 'pnl') { var r = PNL.rangeFor(S.pnl); return S.pnl + '|' + r.from + '|' + r.to; }
    return RANGED.indexOf(name) >= 0 ? S.range : name;
  }
  function retryBtn(name) { return '<button class="btn sm" type="button" data-retry="' + esc(name) + '">Try again</button>'; }

  function markLoading(id, key) {
    var s = secEl(id);
    // Different range or grouping: never show the old numbers under the new label.
    if (s.dataset.key !== key) { secBody(id).innerHTML = skeleton(); s.dataset.key = ''; }
    s.querySelector('.sec-msg').innerHTML = '';
    s.classList.add('busy');
    s.setAttribute('aria-busy', 'true');
  }
  function markDone(id, key) {
    var s = secEl(id);
    s.dataset.key = key;
    s.classList.remove('busy');
    s.removeAttribute('aria-busy');
  }
  function markError(id, key, msg, name) {
    var s = secEl(id);
    s.classList.remove('busy');
    s.removeAttribute('aria-busy');
    if (key !== null && s.dataset.key === key) {
      s.querySelector('.sec-msg').innerHTML = '<div class="errbox"><span>Couldn\'t refresh: ' + esc(sentence(msg)) +
        ' Showing the last numbers that loaded.</span>' + retryBtn(name) + '</div>';
    } else {
      s.dataset.key = '';
      secBody(id).innerHTML = id === 'pnl' ? pnlDown(msg)
        : '<div class="errbox"><span>Couldn\'t load this: ' + esc(msg) + '</span>' + retryBtn(name) + '</div>';
      if (id === 'pnl') pnlMeta(null);
    }
  }

  var URLS = {
    overview: function () { return '/hub/api/overview?range=today'; },
    proposals: function () { return '/hub/api/proposals'; },
    pnl: function () { var r = PNL.rangeFor(S.pnl); return '/hub/api/pnl?from=' + r.from + '&to=' + r.to; },
    orders: function () { return '/hub/api/orders?range=' + S.range + '&limit=100'; },
    creatives: function () { return '/hub/api/creatives?range=' + S.range + '&group=' + S.group; },
    assists: function () { return '/hub/api/assists?range=' + S.range; },
    funnel: function () { return '/hub/api/funnel?range=' + S.range; }
  };
  var RENDER = {status: renderStatus, quality: renderQuality, approvals: renderApprovals, pnl: renderPnl,
                orders: renderOrders, creatives: renderCreatives, assists: renderAssists, funnel: renderFunnel};
  // A crash on the server comes back as HTTP 200 holding only an `error`, and so does a P&L that
  // didn't answer. A real answer always has its section's main field, even when it carries a note.
  var MAIN_KEY = {overview: 'status', proposals: 'proposals', pnl: 'pnl', orders: 'orders', creatives: 'campaigns',
                  assists: 'rows', funnel: 'steps'};

  function loadSection(name) {
    if (!SECTIONS[name]) return Promise.resolve();
    var my = S.seq[name] = (S.seq[name] || 0) + 1;
    var key = keyFor(name);
    SECTIONS[name].forEach(function (id) { markLoading(id, key); });
    return api(URLS[name]()).then(function (data) {
      if (name === 'pnl' && my === S.seq[name] && webUrl(data.pnl_url)) S.pnlUrl = data.pnl_url;
      if (name === 'pnl' && webUrl(data.creative_url)) S.creativeUrl = data.creative_url;
      if (name === 'pnl' && webUrl(data.core_hub_url)) S.coreHubUrl = data.core_hub_url;
      if (data.error && !(MAIN_KEY[name] in data)) throw new Error(String(data.error));
      return data;
    }).then(function (data) {
      if (my !== S.seq[name]) return;               // a newer request for this section won
      if (name === 'overview') {
        S.ov = data;
        try { renderHeader(data); fillPixelSelect(data.quality || []); } catch (e) { console.error(e); }
      }
      if (RANGED.indexOf(name) >= 0 && data.range && data.range.key === S.range) {
        S.rng = data.range;
        $('#rangeText').textContent = rangeText();
      }
      SECTIONS[name].forEach(function (id) {
        try { RENDER[id](data); markDone(id, key); }
        catch (e) { console.error(e); markError(id, null, 'part of this could not be shown.', name); }
      });
    }, function (e) {
      if (my !== S.seq[name] || e.leaving) return;
      SECTIONS[name].forEach(function (id) { markError(id, key, e.message, name); });
      if (name === 'overview' && !S.ov) headerDown();
    });
  }

  function loadMany(names) {
    clearTimeout(S.timer);
    S.timer = null;
    S.lastLoad = Date.now();
    var btn = $('#refreshBtn');
    btn.disabled = true;
    var done = function () { btn.disabled = false; schedule(); };
    return Promise.all(names.map(loadSection)).then(done, done);
  }
  function loadAll() {
    loadBeAlerts();                                   // the count on the Backend tab, cheap, every minute
    if (S.view === 'backend') loadBackend();
    if (S.view === 'database') loadDatabase();
    return loadMany(Object.keys(SECTIONS));
  }

  // One timer at most, and only while the tab is visible.
  function schedule() {
    clearTimeout(S.timer);
    S.timer = null;
    if (S.leaving || document.visibilityState !== 'visible') return;
    S.timer = setTimeout(function () { S.timer = null; loadAll(); },
                         Math.max(1000, REFRESH_MS - (Date.now() - S.lastLoad)));
  }

  // --- ranges, grouping and the URL hash ----------------------------------
  var VIEWS = ['creative', 'loom', 'pnl', 'corehub', 'database', 'backend', 'agent'];   // besides 'hub', the tabs a link can open
  function readHash() {
    var p = new URLSearchParams(location.hash.replace(/^#/, ''));
    var r = p.get('range'), q = p.get('pnl');
    var fk = p.get('funnel');
    return {range: RANGE_KEYS.indexOf(r) >= 0 ? r : 'today', group: p.get('group') === 'batch' ? 'batch' : 'adset',
            funnel: FUNNEL_KEYS.indexOf(fk) >= 0 ? fk : 'meta',
            pnl: PNL_PRESETS.indexOf(q) >= 0 ? q : 'today', view: VIEWS.indexOf(p.get('view')) >= 0 ? p.get('view') : 'hub'};
  }
  function writeHash() {
    var h = '#range=' + S.range + '&group=' + S.group + '&funnel=' + S.funnel + '&pnl=' + S.pnl +
      (S.view !== 'hub' ? '&view=' + S.view : '');
    if (location.hash === h) return;
    try { history.replaceState(null, '', h); } catch (e) { location.hash = h; }
  }
  function rangeText() {
    var r = S.rng;
    if (r && r.key === S.range && r.since) {
      return (r.label || RANGE_LABEL[S.range]) + ': ' + dayLabel(r.since) +
        (r.until && r.until !== r.since ? ' to ' + dayLabel(r.until) : '');
    }
    return RANGE_LABEL[S.range];
  }
  function paintControls() {
    document.querySelectorAll('[data-range]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.range === S.range)); });
    document.querySelectorAll('[data-group]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.group === S.group)); });
    document.querySelectorAll('[data-funnel]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.funnel === S.funnel)); });
    document.querySelectorAll('[data-preset]').forEach(function (b) {
      var on = b.dataset.preset === S.pnl;
      b.classList.toggle('active', on);           // the P&L's own code reads the active chip for its label
      b.setAttribute('aria-pressed', String(on));
    });
    $('#rangeText').textContent = rangeText();
  }
  function setRange(r) {
    if (RANGE_KEYS.indexOf(r) < 0 || r === S.range) return;
    S.range = r;
    writeHash();
    paintControls();
    loadMany(RANGED);
  }
  function setGroup(g) {
    if ((g !== 'adset' && g !== 'batch') || g === S.group) return;
    S.group = g;
    writeHash();
    paintControls();
    loadSection('creatives');
  }
  // The funnel's reply holds all three groups: switching only redraws the cards.
  function setFunnel(k) {
    if (FUNNEL_KEYS.indexOf(k) < 0 || k === S.funnel) return;
    S.funnel = k;
    writeHash();
    paintControls();
    if (funnelShown()) paintFunnel();
  }
  function setPnl(p) {
    if (PNL_PRESETS.indexOf(p) < 0 || p === S.pnl) return;
    S.pnl = p;
    writeHash();
    paintControls();
    loadSection('pnl');
  }

  // --- header: the status dot on the Tracking tab, and the all-time order count -------
  function renderHeader(ov) {
    var st = ov.store || {};
    S.tz = st.timezone || S.tz;
    var dom = String(st.domain || '').replace(/^https?:\/\//, '').replace(/\/+$/, '');
    $('#mark').setAttribute('title', 'Core HQ' + (st.name ? ' \u00b7 ' + st.name : '') + (dom ? ' (' + dom + ')' : ''));
    $('#updated').textContent = 'Updated ' + fmtClock(ov.generated_at || Date.now());
    var L = lvl((ov.status || {}).level);
    setStatus(L, PILL[L]);
    renderOrderCount(ov.orders_all_time);
    renderSales(ov.sales_all_time, ov.sales_goal);
  }
  // The dot on the Tracking tab is the tracking status; its words are in the tab's tooltip.
  function setStatus(L, words) {
    $('#statusDot').className = 'dot ' + L;
    var tab = $('#tabHub');
    tab.setAttribute('title', 'Tracking: ' + words);
    tab.setAttribute('aria-label', 'Tracking. Status: ' + words + '.');
  }
  // The first overview failed: the dot says the check couldn't run instead of "checking" until a refresh works.
  function headerDown() { setStatus('fail', 'couldn\'t check'); }

  // Shopify's all-time order count on flip cards. A new order turns the digits that changed, right
  // to left, like a desk calendar; the first count flips in from blank cards.
  var OC = {digits: ''};
  function ocCards(s, blank) {
    return s.split('').map(function (d, i) {
      var shown = blank ? '' : d;
      return '<span class="fc' + (i > 0 && (s.length - i) % 3 === 0 ? ' g' : '') + '" data-d="' + shown + '">' +
        '<span class="fc-t"><b>' + shown + '</b></span><span class="fc-b"><b>' + shown + '</b></span></span>';
    }).join('');
  }
  function stillMotion() {
    return !window.matchMedia || window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }
  function ocFlip(card, d, delay) {
    var old = card.getAttribute('data-d') || '';
    if (old === d) return;
    card.setAttribute('data-d', d);
    Array.prototype.forEach.call(card.querySelectorAll('.fc-ft,.fc-fb'), function (x) { x.parentNode.removeChild(x); });
    var top = card.querySelector('.fc-t b'), bottom = card.querySelector('.fc-b b');
    var fall = document.createElement('span'), land = document.createElement('span');
    fall.className = 'fc-ft';
    land.className = 'fc-fb';
    fall.appendChild(document.createElement('b')).textContent = bottom.textContent;
    land.appendChild(document.createElement('b')).textContent = d;
    fall.style.animationDelay = delay + 'ms';
    land.style.animationDelay = (delay + 240) + 'ms';
    top.textContent = d;                       // waits behind the falling flap
    card.appendChild(fall);
    card.appendChild(land);
    // Done when the flap lands, or on the clock: a page that isn't being drawn never ends an animation.
    var done = false;
    function finish() {
      if (done) return;
      done = true;
      if (card.getAttribute('data-d') === d) bottom.textContent = d;
      [fall, land].forEach(function (x) { if (x.parentNode) x.parentNode.removeChild(x); });
    }
    land.addEventListener('animationend', finish);
    setTimeout(finish, delay + 700);
  }
  function renderOrderCount(n) {
    var box = $('#ocount');
    if (!isNum(n) || n < 0) { box.hidden = true; return; }
    n = Math.floor(Number(n));
    var s = String(n), tiles = $('#ocTiles'), still = stillMotion();
    var words = n.toLocaleString('en-US') + ' orders in Shopify, all time';
    $('#ocText').textContent = words;
    box.setAttribute('title', words);
    box.hidden = false;
    flipTo(OC, tiles, s, still);
  }
  // Turn a row of flip cards to the digits in s: the ones that changed flip, right to left.
  function flipTo(state, tiles, s, still) {
    if (s === state.digits) return;
    if (s.length !== state.digits.length) tiles.innerHTML = ocCards(s, !still);
    state.digits = s;
    if (still) return;
    var cards = tiles.querySelectorAll('.fc');
    for (var i = 0; i < cards.length; i++) ocFlip(cards[i], s.charAt(i), (cards.length - 1 - i) * 90);
  }
  // Every dollar the store has taken, all time, on the same cards, toward the goal ($ / $1,000,000).
  var SC = {digits: ''};
  function renderSales(v, goal) {
    var box = $('#scount');
    if (!isNum(v) || v < 0) { box.hidden = true; return; }
    var n = Math.floor(Number(v)), g = isNum(goal) && +goal > 0 ? Math.floor(Number(goal)) : 1000000;
    var words = '$' + n.toLocaleString('en-US') + ' in sales of the $' + g.toLocaleString('en-US') + ' goal, all time';
    $('#scText').textContent = words;
    $('#scGoal').textContent = '/ $' + g.toLocaleString('en-US');
    box.setAttribute('title', words);
    box.hidden = false;
    flipTo(SC, $('#scTiles'), String(n), stillMotion());
  }

  // --- the P&L section (its numbers come from the copied P&L code above) ------
  function renderPnl(d) {
    if (webUrl(d.pnl_url)) S.pnlUrl = d.pnl_url;
    if (!document.getElementById('heroNet')) secBody('pnl').innerHTML = $('#pnlTpl').innerHTML;
    PNL.show(d);
    var notes = [];
    if (d.manual_error) notes.push(d.manual_error);
    document.getElementById('pnlNotes').innerHTML = notes.map(function (n) { return '<div class="note">' + esc(n) + '</div>'; }).join('');
    pnlMeta(d);
  }
  function pnlLink(cls) {
    return S.pnlUrl ? '<a' + (cls ? ' class="' + cls + '"' : '') + ' href="' + esc(S.pnlUrl) +
      '" target="_blank" rel="noopener noreferrer">Open full P&amp;L</a>' : '';
  }
  function pnlMeta(d) {
    var bits = ['From your P&amp;L'];
    if (d && d.fetched_at) {
      var ls = d.last_sync || {};
      var tip = [['Shopify orders', ls.shopify], ['Meta spend', ls.meta]].filter(function (x) { return x[1] && x[1].ran_at; })
        .map(function (x) { return x[0] + ' last synced ' + PNL.etTime(x[1].ran_at); }).join(', ');
      bits.push('<span' + (tip ? ' data-tip="' + esc('In your P&L: ' + tip + '.') + '"' : '') + '>synced ' +
        esc(PNL.etTime(d.fetched_at)) + '</span>');
    }
    var link = pnlLink('');
    if (link) bits.push(link);
    $('#pnlMeta').innerHTML = bits.join(' &middot; ');
  }
  // The P&L didn't answer: say so, never a page of zeros.
  function pnlDown(msg) {
    return '<div class="down"><div class="down-t"><span class="dot fail" aria-hidden="true"></span>' + esc(msg) + '</div>' +
      '<div class="sub">Your profit numbers show here again as soon as it answers.</div>' +
      '<div class="row">' + retryBtn('pnl') + pnlLink('btn sm') + '</div></div>';
  }

  // --- waiting for your OK --------------------------------------------------
  function renderApprovals(d) {
    S.props = objects(d.proposals);
    paintApprovals();
  }
  function paintApprovals() {
    // Pending ones, the ones being approved or dismissed right now (the server stops calling them
    // pending before it is done), and the ones just decided here (with what happened) until the next refresh.
    var mine = function (id) { return S.decided.has(id) || S.running.has(id); };
    var shown = S.props.filter(function (p) { return p.status === 'pending' || mine(String(p.id)); });
    [S.running, S.decided].forEach(function (m) {
      m.forEach(function (v, id) {
        if (!shown.some(function (p) { return String(p.id) === id; })) shown.push(v.p);
      });
    });
    secEl('approvals').hidden = !shown.length;
    var waiting = shown.filter(function (p) { return !mine(String(p.id)); }).length;
    $('#apCount').textContent = waiting ? plural(waiting, 'suggestion', 'suggestions') + ' from the watchdog. Nothing happens until you say so.' : '';
    secBody('approvals').innerHTML = '<div class="ap-list">' + shown.map(apCard).join('') + '</div>';
  }
  function apCard(p) {
    var id = String(p.id), done = S.decided.get(id), run = S.running.get(id);
    var info = !!p.informational;
    // Still working on it: both buttons off, the one pressed saying so.
    var off = run ? ' disabled' : '';
    var acts = done ? '' : '<div class="ap-a"><button class="btn primary sm" type="button" data-approve="' + esc(id) + '"' + off + '>' +
      esc(run && run.how === 'approve' ? 'Working\u2026' : info ? 'Got it' : (p.approve_label || 'Approve')) + '</button>' +
      (!info && p.can_dismiss !== false ? '<button class="btn sm" type="button" data-dismiss="' + esc(id) + '"' + off + '>' +
        (run && run.how === 'dismiss' ? 'Dismissing\u2026' : 'Dismiss') + '</button>' : '') +
      '</div>';
    return '<div class="ap-card" data-pid="' + esc(id) + '"><div class="ap-t">' + esc(p.title) + '</div>' +
      '<div class="ap-d">' + esc(p.detail) + '</div>' + acts +
      '<div class="ap-r" aria-live="polite">' + (done ? done.html : '') + '</div></div>';
  }
  function decide(btn, how) {
    var id = String(btn.dataset[how] || '');
    var p = S.props.filter(function (x) { return String(x.id) === id; })[0] || {id: id, title: '', detail: ''};
    var card = btn.closest('.ap-card');
    (card ? card.querySelectorAll('button') : []).forEach(function (b) { b.disabled = true; });
    btn.textContent = how === 'approve' ? 'Working\u2026' : 'Dismissing\u2026';
    S.running.set(id, {p: p, how: how});          // a reload while this runs keeps the card as it is
    api('/hub/api/proposals/' + encodeURIComponent(id) + '/' + how, {}).then(function (r) {
      var q = r.proposal || {};
      var text = r.error || q.result || (r.ok ? 'Done.' : 'It couldn\'t be done.');
      S.decided.set(id, {p: p, html: '<span class="' + (r.ok ? 'ok-t' : 'err-t') + '">' + esc(text) + '</span>'});
    }, function (e) {
      if (e.leaving) return;
      S.decided.set(id, {p: p, html: '<span class="err-t">Couldn\'t do it: ' + esc(e.message) + '</span>'});
    }).then(function () {
      S.running.delete(id);
      if (S.leaving) return;
      paintApprovals();
      // Long enough to read what happened, then the list, the orders and the health checks catch up.
      setTimeout(function () {
        S.decided.delete(id);
        loadSection('proposals');
        if (how === 'approve') { loadSection('orders'); loadSection('overview'); }
      }, 4000);
    });
  }

  // --- sparkline (inline SVG), for the match quality trend -------------------
  // The SVG stretches to the card; the stroke stays thin and the end dot is an
  // HTML element so it stays round at any width.
  function spark(vals, tips, opts) {
    opts = opts || {};
    var pts = vals.map(function (v) { return isNum(v) ? +v : null; });
    var real = pts.filter(function (v) { return v !== null; });
    if (!real.length) return '';
    var lo = opts.fit ? Math.min.apply(null, real) : Math.min.apply(null, [0].concat(real));
    var hi = Math.max.apply(null, real);
    if (opts.fit) {
      var pad = Math.max((hi - lo) * 0.2, 0.3);
      lo = Math.max(opts.floor != null ? opts.floor : -Infinity, lo - pad);
      hi = Math.min(opts.ceil != null ? opts.ceil : Infinity, hi + pad);
    }
    if (hi <= lo) hi = lo + 1;
    var n = pts.length, W = 100, H = 30, P = 3, base = H - P;
    var x = function (i) { return n === 1 ? W / 2 : i / (n - 1) * W; };
    var y = function (v) { return P + (1 - (v - lo) / (hi - lo)) * (H - 2 * P); };
    var xy = function (p) { return x(p[0]).toFixed(2) + ' ' + y(p[1]).toFixed(2); };
    var line = '', area = '', seg = [], lone = [];
    var flush = function () {
      if (!seg.length) return;
      if (seg.length === 1) lone.push(seg[0]);       // a day between two gaps has no line to sit on
      line += 'M' + seg.map(xy).join('L');
      area += 'M' + x(seg[0][0]).toFixed(2) + ' ' + base + 'L' + seg.map(xy).join('L') +
              'L' + x(seg[seg.length - 1][0]).toFixed(2) + ' ' + base + 'Z';
      seg = [];
    };
    pts.forEach(function (v, i) { if (v === null) flush(); else seg.push([i, v]); });
    flush();
    var last = -1;
    pts.forEach(function (v, i) { if (v !== null) last = i; });
    var at = function (p, cls) {
      return '<span class="' + cls + '" style="left:' + x(p[0]).toFixed(2) + '%;top:' + (y(p[1]) / H * 100).toFixed(2) + '%"></span>';
    };
    var dot = lone.filter(function (p) { return p[0] !== last; }).map(function (p) { return at(p, 'pt'); }).join('') +
              at([last, pts[last]], 'end');
    var w = n === 1 ? 100 : 100 / (n - 1);
    var hits = pts.map(function (v, i) {
      var a = Math.max(0, x(i) - w / 2), b = Math.min(100, x(i) + w / 2);
      var c = b > a ? (x(i) - a) / (b - a) * 100 : 50;
      return '<span class="hit" style="left:' + a.toFixed(2) + '%;width:' + (b - a).toFixed(2) + '%;--c:' + c.toFixed(1) +
             '%" data-tip="' + esc(tips[i] || '') + '"></span>';
    }).join('');
    return '<div class="sp"><svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" aria-hidden="true">' +
      '<path class="sp-a" d="' + area + '"/><path class="sp-l" d="' + line + '" vector-effect="non-scaling-stroke"/></svg>' +
      dot + hits + '</div>';
  }

  // --- tracking health ----------------------------------------------------------
  function tile(c) {
    var L = lvl(c.status);
    return '<div class="check ' + L + '"><span class="dot ' + L + '" aria-hidden="true"></span><div class="check-t">' +
      '<div class="check-n"><b>' + esc(c.name) + '</b><span class="word ' + L + '">' + WORD[L] + '</span></div>' +
      '<div class="sub">' + esc(c.detail) + '</div></div></div>';
  }
  function bySeverity(list) {
    return (list || []).slice().sort(function (a, b) { return RANK[lvl(b.status)] - RANK[lvl(a.status)]; });
  }

  function strip(timeline, nowMs) {
    var start = nowMs - 86400000;
    var counts = {ok: 0, warn: 0, fail: 0};
    var runs = (timeline || []).map(function (r) { return {at: r.at, ms: toMs(r.at), s: lvl(r.status)}; })
      .filter(function (r) { return r.ms != null && r.ms >= start - 60000; })
      // Problems are drawn last so they stay on top where ticks overlap.
      .sort(function (a, b) { return RANK[a.s] - RANK[b.s]; });
    var ticks = runs.map(function (r) {
      counts[r.s]++;
      var left = Math.min(100, Math.max(0, (r.ms - start) / 864000));
      return '<i class="tick ' + r.s + '" style="left:' + left.toFixed(2) + '%" data-run="' + esc(r.at) +
        '" data-tip="' + esc(fmtClock(r.at, true) + ': ' + HEAD[r.s] + '. Click or tap for details.') + '"></i>';
    }).join('');
    var label = runs.length + ' checks in 24 hours: ' + counts.ok + ' all good, ' + counts.warn + ' needed a look, ' + counts.fail + ' broken';
    return '<div class="strip" role="img" aria-label="' + esc(label) + '">' +
      (ticks || '<span class="strip-empty">No checks recorded in the last 24 hours yet</span>') + '</div>' +
      '<div class="strip-axis"><span>24 h ago</span><span>Now</span></div>';
  }

  function renderStatus(ov) {
    var st = ov.status || {};
    var L = lvl(st.level);
    var reasons = (st.reasons || []).filter(Boolean);
    $('#modeLine').innerHTML = st.mode === 'test'
      ? '<span class="badge b-warn">Test mode</span> Events only show in Events Manager &gt; Test events, not in ads reporting.'
      : '<span class="badge b-live"><span class="dot ok" aria-hidden="true"></span>Live</span> Sending your store\'s events to Meta for real.';
    var nowMs = toMs(ov.generated_at) || Date.now();
    secBody('status').innerHTML =
      '<div class="status-top"><span class="light ' + L + '" role="img" aria-label="' + HEAD[L] + '"></span><div class="status-text">' +
        '<div class="headline">' + esc(st.headline || HEAD[L]) + '</div>' +
        (reasons.length
          ? '<ul class="reasons">' + reasons.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') + '</ul>'
          : '<div class="sub">Every link from your store to Meta checked out.</div>') +
      '</div></div>' +
      '<div class="checks">' + (bySeverity(st.checks).map(tile).join('') || '<div class="sub">No checks have run yet.</div>') + '</div>' +
      '<div class="wd"><div class="wd-h"><span>Watchdog checks every 5 minutes, last check ' + esc(st.checked_ago || 'never') + '.</span>' +
        '<span class="legend"><span class="dot ok"></span>All good<span class="dot warn"></span>Needs a look<span class="dot fail"></span>Broken</span></div>' +
        strip(st.timeline, nowMs) + '</div>';
  }

  // Cached for a minute and shared while in flight, so the history and a tick click make one call.
  function watchdogRuns(force) {
    if (!force && S.wd && Date.now() - S.wdAt < 60000) return Promise.resolve(S.wd);
    if (!force && S.wdP) return S.wdP;
    var p = S.wdP = api('/hub/api/watchdog').then(function (d) {
      S.wd = d.runs || [];
      S.wdAt = Date.now();
      return S.wd;
    });
    var clear = function () { if (S.wdP === p) S.wdP = null; };
    p.then(clear, clear);
    return p;
  }

  function showRun(at) {
    var box = $('#runDetail');
    box.innerHTML = '<div class="run sub">Loading that check&hellip;</div>';
    watchdogRuns(false).then(function (runs) {
      var t = toMs(at), best = null, gap = Infinity;
      runs.forEach(function (r) { var g = Math.abs(toMs(r.at) - t); if (g < gap) { gap = g; best = r; } });
      if (!best) { box.innerHTML = '<div class="run sub">That check is no longer stored.</div>'; return; }
      var L = lvl(best.status);
      box.innerHTML = '<div class="run"><div class="run-h"><b>Check at ' + esc(fmtClock(best.at, true)) + ': ' + HEAD[L] + '</b>' +
        '<button class="btn sm" type="button" data-close-run>Close</button></div>' +
        '<div class="checks">' + bySeverity(best.checks).map(tile).join('') + '</div></div>';
    }, function (e) {
      if (!e.leaving) box.innerHTML = '<div class="errbox"><span>Couldn\'t load that check: ' + esc(e.message) + '</span></div>';
    });
  }

  function renderHistory(force) {
    var box = $('#wdList');
    watchdogRuns(force).then(function (runs) {
      var bad = runs.filter(function (r) { return lvl(r.status) !== 'ok'; });
      if (!runs.length) { box.textContent = 'No checks recorded in the last 24 hours yet.'; return; }
      if (!bad.length) { box.textContent = 'All ' + runs.length + ' checks in the last 24 hours passed.'; return; }
      box.innerHTML = '<ul class="hist">' + bad.slice(0, 60).map(function (r) {
        var L = lvl(r.status);
        var what = (r.checks || []).filter(function (c) { return lvl(c.status) !== 'ok'; })
          .map(function (c) { return c.name + ': ' + c.detail; }).join('; ');
        return '<li><span class="dot ' + L + '"></span><div><b>' + esc(fmtClock(r.at, true)) + '</b> ' + HEAD[L] +
          '<div class="sub">' + esc(what) + '</div></div></li>';
      }).join('') + '</ul>' + (bad.length > 60 ? '<div class="sub">Showing the latest 60.</div>' : '');
    }, function (e) {
      if (!e.leaving) box.innerHTML = '<span class="err-t">Couldn\'t load the history: ' + esc(e.message) + '</span>';
    });
  }

  // --- shopper funnel ---------------------------------------------------------------
  function convText(c) {
    if (!isNum(c)) return '-';
    var p = +c * 100;
    return (p === 0 ? '0' : p < 10 ? p.toFixed(2) : p.toFixed(1)) + '%';
  }
  // The share of the step above, on the arrow between two cards: whole numbers, one decimal under 10%.
  function stepPct(v, above) {
    if (!isNum(v) || !isNum(above) || +above <= 0) return '-';
    var p = +v / +above * 100;
    if (p === 0 || p >= 10) return Math.round(p) + '%';
    return (p < 0.05 ? '<0.1' : p.toFixed(1)) + '%';
  }
  // Shoppers from Meta ads: straight to the product page vs through the listicle.
  function listicleBlock(L, product) {
    var rows = objects(L && L.rows);
    if (!rows.length) return '';
    return '<div class="lst"><div class="f-h"><b>Product page vs listicle' + (product ? ' \u00b7 ' + esc(product) : '') +
      '</b><span class="sub">Shoppers from Meta ads</span></div>' +
      '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Landed on</th><th class="num">Visitors</th><th class="num">Sales</th>' +
      '<th class="num">Revenue</th><th class="num">Conversion rate</th>' +
      '<th class="num" data-tip="Of the quiz sales, the ones that went through the listicle (counted in both rows)">Assists</th></tr></thead><tbody>' +
      rows.map(function (r) {
        var label = r.label || (r.key === 'listicle' ? 'Listicle' : r.key === 'quiz' ? 'Quiz Funnel' : 'Product page');
        var helped = isNum(r.assists) ? num(r.assists) + (+r.assists && isNum(r.assist_revenue) ? ' \u00b7 ' + money(r.assist_revenue) : '') : '-';
        var tip = r.key === 'quiz' ? ' class="helped" data-tip="Everyone who started in the quiz, straight to the product page ' +
          'or on through the listicle, and every sale they made. The ones who bought through the listicle are the assists; ' +
          'those sales count in the Listicle row too."' : '';
        return '<tr>' + td('Landed on', '<b' + tip + '>' + esc(label) + '</b>') + td('Visitors', esc(num(r.visitors)), 'num') +
          td('Sales', esc(num(r.sales)), 'num') + td('Revenue', esc(money(r.revenue)), 'num') +
          td('Conversion', esc(convText(r.conversion)), 'num') + td('Assists', esc(helped), 'num') + '</tr>';
      }).join('') + '</tbody></table></div>' + (L.note ? '<p class="sub small">' + esc(L.note) + '</p>' : '') + '</div>';
  }

  function renderFunnel(f) {
    S.fdata = f;
    // The server's words for "Not from Meta" follow its attribution window.
    var other = objects(f.groups).filter(function (g) { return g.key === 'other'; })[0];
    if (other && other.tip) {
      var btn = document.getElementById('notMetaBtn');
      if (btn) btn.setAttribute('data-tip', String(other.tip));
      var tipEl = document.getElementById('notMetaTip');
      if (tipEl) tipEl.textContent = String(other.tip);
    }
    // The product switch: every product shoppers viewed or bought in the range, most visitors first.
    var sel = document.getElementById('funnelProduct');
    var prods = objects(f.products);
    if (sel) {
      if (S.fprod && !prods.some(function (p) { return p.key === S.fprod; })) S.fprod = '';
      sel.innerHTML = '<option value="">All products</option>' + prods.map(function (p) {
        return '<option value="' + esc(p.key) + '"' + (p.key === S.fprod ? ' selected' : '') + '>' + esc(p.label) + '</option>';
      }).join('');
      sel.hidden = prods.length < 2;
    }
    paintFunnel();
  }
  var psel = document.getElementById('funnelProduct');
  if (psel) psel.addEventListener('change', function () { S.fprod = psel.value; if (S.fdata) paintFunnel(); });
  // Only numbers that belong to the range on show are redrawn when the switch changes.
  function funnelShown() { return !!S.fdata && secEl('funnel').dataset.key === keyFor('funnel'); }

  // Five step cards for the shoppers the switch picks, the share of the step above on the arrow between
  // two cards, then how many of the visitors bought.
  function paintFunnel() {
    var all = S.fdata || {};
    var bp = all.by_product && typeof all.by_product === 'object' ? all.by_product : {};
    var product = S.fprod && bp[S.fprod] ? S.fprod : '';
    var f = product ? Object.assign({}, all, bp[product]) : all;
    var steps = f.steps && f.steps.length ? f.steps : FUNNEL_STEPS;
    var key = FUNNEL_KEYS.indexOf(S.funnel) >= 0 ? S.funnel : 'meta';
    var raw = Array.isArray(f[key]) ? f[key] : [];
    // null is unknown (Purchases while Shopify can't be read): shown as "-", never as 0.
    var v = steps.map(function (_, i) { return isNum(raw[i]) ? Math.max(0, +raw[i]) : null; });
    var cards = steps.map(function (step, i) {
      var rate = i ? '<div class="fs-rate"><span class="fs-arrow" aria-hidden="true">\u2192</span><span>' +
        esc(stepPct(v[i], v[i - 1])) + '</span><span class="fs-of"> of the step above</span></div>' : '';
      return '<div class="fstep' + (i === steps.length - 1 ? ' end' : '') + '"><div class="fs-k">' + esc(step) + '</div>' +
        '<div class="fs-v">' + esc(num(v[i])) + '</div>' + rate + '</div>';
    }).join('');
    // Store conversion is usually under 1%, so it keeps two decimals there.
    var visitors = v[0], bought = v[v.length - 1];
    var line = !visitors ? 'No visitors yet' : bought === null ? esc(plural(visitors, 'visitor', 'visitors'))
      : '<b>' + esc(convText(bought / visitors)) + '</b> of visitors bought';
    var note = f.error ? '<div class="note warn">' + esc(f.error) + ' Purchases show a dash until Shopify answers again.</div>' : '';
    var small = [];
    if (isNum(f.untied_sales) && +f.untied_sales > 0) {
      small.push(plural(f.untied_sales, 'more sale', 'more sales') + ' we could not tie to a browser.');
    }
    // The range starts before the pixel's first shopper on record: earlier visits weren't counted.
    if (f.counting_since) small.push('Counting since ' + f.counting_since + ', when the pixel recorded its first shopper.');
    secBody('funnel').innerHTML = note + '<div class="fsteps">' + cards + '</div>' +
      '<p class="fs-bought">' + line + '</p>' +
      small.map(function (t) { return '<p class="sub small f-notes">' + esc(t) + '</p>'; }).join('') +
      listicleBlock(f.listicle, product) + (f.note ? '<p class="sub small foot">' + esc(f.note) + '</p>' : '');
  }

  // --- creatives that sold ------------------------------------------------------------
  function td(label, html, cls) {
    return '<td data-label="' + esc(label) + '"' + (cls ? ' class="' + cls + '"' : '') + '><div class="v">' + html + '</div></td>';
  }
  function kcell(label, value, sub, tip) {
    return '<div class="kcell"><div class="lab"' + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + esc(label) +
      (tip ? '<span class="sr"> ' + esc(tip) + '</span>' : '') + '</div><div class="v">' + esc(value) + '</div>' +
      (sub ? '<div class="sub">' + esc(sub) + '</div>' : '') + '</div>';
  }
  // Product, Ad and Meta ROAS for the range, then the totals they come from.
  function roasStrip(t, cur, on, failed) {
    return '<div class="kstrip">' +
      kcell('Product ROAS', roas(t.product_roas), 'Advertised products',
            'Sales of the products you are advertising / ad spend. MRR is left out because no ad drove it.') +
      kcell('Ad ROAS', roas(t.ad_roas), 'Traced to ad clicks', 'Sales your store traced to an ad click / ad spend') +
      kcell('Meta ROAS', roas(t.meta_roas), 'Ads Manager', 'What Ads Manager reports') +
      kcell('Ad spend', on ? money(t.spend, cur) : failed ? '-' : 'Not connected', on ? 'Meta ads' : '') +
      kcell('Meta sales', on ? num(t.meta_purchases) : '-',
            on ? money(t.meta_value, cur) + ' in Ads Manager' + (hasSplit(t) ? ' ' + splitText(t) : '') : '') +
      kcell('Store sales', num(t.store_sales), money(t.store_revenue, cur) + ' in real orders') + '</div>';
  }
  // Without a full ads connection the spend next to it shows "-", so its ROAS
  // does too: a ratio on part of the spend would look like the real one.
  function roasCell(store, meta, on) {
    if (!on) { store = null; meta = null; }
    return (isNum(store) ? '<span class="roas-s">' + esc(roas(store)) + '</span>' : '<span class="q">-</span>') +
      (isNum(meta) ? '<span class="roas-m">Meta ' + esc(roas(meta)) + '</span>' : '');
  }
  function setupCard(err) {
    return '<div class="setup"><h3>Connect ad spend to see ROAS per creative</h3>' +
      (err ? '<p class="sub">' + esc(err) + '</p>' : '') +
      '<ol>' +
      '<li>In Meta Business Settings, open Users &gt; System users. Pick a system user or add one, then click Add assets &gt; Ad accounts &gt; the leggings account (1537379363921450) and turn on "View performance".</li>' +
      '<li>Click Generate new token for that system user, tick the ads_read permission and copy the token.</li>' +
      '<li>In Railway, open tracker &gt; Variables and add META_ADS_TOKEN = the token and META_AD_ACCOUNT_IDS = 1537379363921450. Then click Deploy.</li>' +
      '</ol><p class="sub">Until then, this shows only the sales the store could tie to a Meta ad, without spend.</p></div>';
  }
  // Meta's own sales, split into clicks (bought within 7 days of clicking the
  // ad) and views (within a day of only seeing it) when Meta reported the split.
  function hasSplit(x) {
    // Only worth a line when some were views: "(3 click, 0 view)" says nothing new.
    return (+x.meta_purchases || 0) > 0 && isNum(x.meta_click_purchases) && (+x.meta_view_purchases || 0) > 0;
  }
  function splitText(x) { return '(' + num(x.meta_click_purchases) + ' click, ' + num(x.meta_view_purchases) + ' view)'; }
  function metaSales(x, on, isAd) {
    if (!on) return '-';
    var h = esc(num(x.meta_purchases));
    if (!hasSplit(x)) return h;
    h += ' <span class="msplit">' + esc(splitText(x)) + '</span>';
    if (isAd && +x.meta_view_purchases > +x.meta_click_purchases) {
      h += '<span class="tag-view" data-tip="Meta counts more sales from people who only saw this ad than from people who clicked it.">mostly view</span>';
    }
    return h;
  }
  // Sales where the buyer clicked this ad earlier, before the ad that got the
  // sale. On an ad set or batch row, each such sale once however many of its ads helped.
  // The tip names the creative that got each assisted sale ("ad set \u00b7 ad", xN when it got several).
  function assistCell(n, closers) {
    n = +n || 0;
    if (!n || !closers || !closers.length) return '<span class="assist">' + esc(num(n)) + '</span>';
    var counts = {}, order = [];
    closers.forEach(function (c) { c = String(c); if (!counts[c]) order.push(c); counts[c] = (counts[c] || 0) + 1; });
    var named = order.map(function (c) { return counts[c] > 1 ? c + ' \u00d7' + counts[c] : c; });
    var tip = 'Assisted ' + plural(n, 'sale', 'sales') + ' that ' + (n === 1 ? 'was' : 'were') + ' closed by: ' + listShort(named, 12);
    return '<span class="assist" data-tip="' + esc(tip) + '">' + esc(num(n)) + '<span class="sr"> ' + esc(tip) + '</span></span>';
  }
  function adRow(a, cur, on) {
    var sold = (+a.store_sales || 0) > 0, metaSold = (+a.meta_purchases || 0) > 0;
    var name = a.ad_name || (a.ad_id ? 'Ad ' + a.ad_id : UNNAMED_AD);
    var bits = [];
    if (S.group === 'batch' && a.adset_name) bits.push('Ad set: ' + a.adset_name);
    if (isNum(a.impressions) && +a.impressions) bits.push(num(a.impressions) + ' impressions');
    if (isNum(a.clicks) && +a.clicks) bits.push(num(a.clicks) + ' clicks');
    if (isNum(a.meta_add_to_carts) && +a.meta_add_to_carts) bits.push(plural(a.meta_add_to_carts, 'add to cart', 'add to carts'));
    var orders = a.orders || [];
    // Store sales whose ad click came through a listicle, or straight through the quiz.
    var via = +a.via_listicle || 0, quiz = +a.via_quiz || 0;
    var who = '<div class="ad-name">' + esc(name) + (via ? '<span class="tag">' + esc(num(via)) + ' via listicle</span>' : '') +
      (quiz ? '<span class="tag">' + esc(num(quiz)) + ' via quiz</span>' : '') + '</div>' +
      (bits.length ? '<div class="sub small">' + esc(bits.join(' \u00b7 ')) + '</div>' : '') +
      (orders.length ? '<div class="ords">Orders: ' + esc(listShort(orders, 8)) + '</div>' : '');
    return '<tr class="' + (sold ? 'sold' : metaSold ? 'msold' : '') + '">' +
      td('Creative', who) +
      td('Spend', esc(on ? money(a.spend, cur) : '-'), 'num') +
      td('Meta sales', metaSales(a, on, true), 'num') +
      td('Store sales', sold ? '<b>' + esc(num(a.store_sales)) + '</b>' : esc(num(a.store_sales)), 'num') +
      td('Assists', assistCell(a.assists, a.assist_closers || a.assist_orders), 'num') +
      td('Revenue', sold ? esc(money(a.store_revenue, cur)) : '<span class="q">-</span>', 'num') +
      td('ROAS', roasCell(sold ? a.roas_store : null, metaSold ? a.roas_meta : null, on), 'num') + '</tr>';
  }
  // The ads with no sale, summed in one line so the totals still add up.
  function smallLine(s, cur, on, min) {
    if (!s || !(+s.count > 0)) return '';
    return esc('+' + plural(s.count, 'other ad', 'other ads') + ' with no sales' +
      (on ? ': ' + money(s.spend, cur) + ' spend' : ''));
  }
  function groupRows(g, cur, on, min) {
    var more = smallLine(g.small, cur, on, min);
    return '<tbody><tr class="grp">' +
      td(S.group === 'batch' ? 'Batch' : 'Ad set', '<b>' + esc(g.name || 'Unnamed') + '</b>') +
      td('Spend', esc(on ? money(g.spend, cur) : '-'), 'num') +
      td('Meta sales', metaSales(g, on, false), 'num') +
      td('Store sales', esc(num(g.store_sales)), 'num') +
      td('Assists', assistCell(g.assists, g.assist_closers || g.assist_orders), 'num') +
      td('Revenue', esc(money(g.store_revenue, cur)), 'num') +
      td('ROAS', roasCell(g.roas_store, g.roas_meta, on), 'num') + '</tr>' +
      (g.ads || []).map(function (a) { return adRow(a, cur, on); }).join('') +
      (more ? '<tr class="more"><td colspan="7">' + more + '</td></tr>' : '') + '</tbody>';
  }

  function renderCreatives(d) {
    var cur = d.currency || 'USD', t = d.totals || {}, on = !!d.connected;
    var min = isNum(d.min_ad_spend) ? +d.min_ad_spend : null;
    // Set up but not readable just now: say so, instead of the setup steps.
    var failed = !on && !!d.configured;
    var h = on ? (d.error ? '<div class="note warn">' + esc(d.error) + '</div>' : '')
      : failed ? '<div class="note warn">Couldn\'t read ad spend from Meta just now, so spend and ROAS show a dash. ' +
          esc(d.error) + '</div>'
      : setupCard(d.error);
    h += roasStrip(t, cur, on, failed);
    // The newest sale in the range, one quiet line: when, ad set > ad, money, and the ads that assisted it.
    var L = d.latest;
    if (L) {
      var who = L.ad_name ? (L.adset_name ? L.adset_name + ' \u203a ' : '') + L.ad_name : 'Ad not named';
      h += '<div class="latest" title="' + esc((L.order || '') + (L.campaign_name ? ' \u00b7 ' + L.campaign_name : '')) + '">' +
        '<b>Latest sale</b> \u00b7 ' + esc(L.time_local || '') + ' \u00b7 ' + esc(who) + ' \u00b7 ' + esc(money(L.revenue, cur)) +
        ' \u00b7 ' + ((L.assisted_by || []).length ? 'assist: ' + esc(L.assisted_by.join(', ')) : 'no assist') + '</div>';
    }

    var head = '<thead><tr><th>' + (S.group === 'batch' ? 'Batch and creative' : 'Ad set and creative') + '</th>' +
      '<th class="num">Spend</th><th class="num">Meta sales</th><th class="num">Store sales</th><th class="num">Assists</th>' +
      '<th class="num">Revenue</th><th class="num">ROAS (store / Meta)</th></tr></thead>';
    var camps = d.campaigns || [];
    if (!camps.length) h += '<div class="empty">No ad spend or Meta sales ' + esc(RANGE_WORDS[S.range]) + '.</div>';
    camps.forEach(function (c) {
      var key = String(c.campaign_id || c.campaign_name || '');
      // Without an ads connection Meta's own numbers are unknown, not zero.
      var summary = (on ? ['Spend ' + money(c.spend, cur),
                           'Meta sales ' + num(c.meta_purchases) + (hasSplit(c) ? ' ' + splitText(c) : '')] : [])
        .concat(['Store sales ' + num(c.store_sales), 'Revenue ' + money(c.store_revenue, cur)])
        .concat((+c.assists || 0) > 0 ? ['Assists ' + num(c.assists)] : [])
        .concat(on ? ['ROAS ' + roas(c.roas_store) + ' (Meta ' + roas(c.roas_meta) + ')'] : []).join(' \u00b7 ');
      var groups = (c.groups || []).map(function (g) { return groupRows(g, cur, on, min); }).join('');
      // Ad sets where no ad spent enough for a row of its own.
      var more = smallLine(c.small, cur, on, min);
      h += '<details class="camp" data-camp="' + esc(key) + '"' + (S.closed.has(key) ? '' : ' open') + '>' +
        '<summary><span class="camp-n">' + esc(c.campaign_name || 'Unknown campaign') + '</span>' +
        '<span class="camp-s">' + esc(summary) + '</span></summary>' +
        (groups ? '<div class="tbl-wrap"><table class="tbl ads">' + head + groups + '</table></div>' : '') +
        (more ? '<p class="camp-more">' + more + '</p>' : '') + '</details>';
    });
    if (camps.length) {
      h += '<p class="sub small foot">An ad gets a row when it got a sale ' + esc(RANGE_WORDS[S.range]) +
        ', in the store or in Ads Manager; the rest are summed in the grey lines. ' +
        'Assists are sales where the buyer clicked this ad earlier, before the ad that got the sale. ' +
        'Ad set, batch and campaign totals count each of these sales once, however many of their ads the buyer clicked. ' +
        'They are never added to sales or revenue. Meta sales in brackets: click means bought within 7 days of clicking ' +
        'the ad, view means bought within 1 day of only seeing it.</p>';
    }

    var u = d.unlabelled || {};
    if ((+u.store_sales || 0) > 0) {
      h += '<div class="note warn">Sales from Meta ad clicks without ad names: <b>' + esc(num(u.store_sales)) + '</b> (' +
        esc(money(u.store_revenue, cur)) + '). Every ad link carries its tags; these buyers came back later without the ad link ' +
        '(a bio link, a typed address, another device), so only the Meta click id arrived. ' +
        'Core HQ names the ad from the buyer visits when it can, else from the Meta report within 2 days.' +
        ((u.orders || []).length ? '<div class="ords">Orders: ' + esc(listShort(u.orders, 12)) + '</div>' : '') + '</div>';
    }
    secBody('creatives').innerHTML = h;

    var ut = d.url_tracking || {};
    $('#urlCounts').textContent = (+ut.meta_orders || 0) > 0
      ? 'Of ' + plural(ut.meta_orders, 'sale', 'sales') + ' from Meta ads ' + RANGE_WORDS[S.range] + ', ' + num(ut.tagged_orders || 0) +
        ' carried the ad name. The rest came from ads without these parameters.'
      : 'No sales from Meta ads ' + RANGE_WORDS[S.range] + ' yet.';
  }

  // --- assists ----------------------------------------------------------------------
  // One row per ad that assisted a sale: the ad, its spend, the creatives that got
  // those sales, and how many it assisted. Every name came from Meta or an ad link: esc() each one.
  function adName(a) { return a.ad_name || (a.ad_id ? 'Ad ' + a.ad_id : UNNAMED_AD); }

  function asRow(r, cur) {
    var where = [r.adset_name, r.campaign_name].filter(Boolean).join(' \u00b7 ');
    // A chip per creative that got the sale: its ad set (small), its name, and the
    // value of the sales this row's ad assisted, with xN when more than one.
    var closers = objects(r.closers).map(function (c) {
      var n = +c.sales || 0;
      return '<li class="as-chip"><span class="as-set">' + esc(c.adset_name || '') + '</span>' +
        '<span class="as-cn">' + esc(adName(c)) + '</span><span class="as-v">' +
        esc(money(c.value, cur) + (n > 1 ? ' \u00d7' + n : '')) + '</span></li>';
    }).join('');
    return '<div class="as-row">' +
      '<div class="as-ad"><div class="as-where' + (where ? '' : ' blank') + '">' + (where ? esc(where) : '&nbsp;') + '</div>' +
        '<div class="as-name">' + esc(adName(r)) + '</div></div>' +
      '<div class="as-spend"><div class="as-main">' + esc(isNum(r.spend) ? money(r.spend, cur) : '-') + '</div></div>' +
      '<div class="as-cls"><div class="m-lab">Creatives that got the sale</div><ul>' + closers + '</ul></div>' +
      '<div class="as-count"><div class="as-n">' + esc(num(r.assists)) + '</div></div></div>';
  }

  function renderAssists(d) {
    var cur = d.currency || 'USD';
    var rows = objects(d.rows);
    var h = d.error ? '<div class="note warn">' + esc(d.error) + '</div>' : '';
    if (!rows.length && !d.error) {
      h += '<div class="empty">No assisted sales yet. Assists count from Sep 27, 2026, when click history started.</div>';
    }
    if (rows.length) {
      h += '<div class="as-list"><div class="as-head" aria-hidden="true"><span>Assisting ad</span><span class="r">Spend</span>' +
        '<span>Creatives that got the sale</span><span class="r">Assists</span></div>' +
        rows.map(function (r) { return asRow(r, cur); }).join('') + '</div>';
    }
    var none = d.sales_without_assists;
    if (isNum(none) && (rows.length || +none > 0)) {
      // Sales from before click history started aren't counted: their earlier clicks aren't known.
      var since = d.sales_without_assists_since ? ' since ' + d.sales_without_assists_since : '';
      h += '<p class="sub foot">' + esc(plural(none, 'sale', 'sales') + since) + ' had no earlier ad click.</p>';
    }
    if (rows.length && d.note) h += '<p class="sub small">' + esc(d.note) + '</p>';
    secBody('assists').innerHTML = h;
  }

  // --- match quality ------------------------------------------------------------------
  var KEY_NAMES = new Map([
    ['email', 'Email'], ['em', 'Email'], ['phone', 'Phone'], ['ph', 'Phone'],
    ['ip_address', 'IP'], ['client_ip_address', 'IP'], ['user_agent', 'Browser'], ['client_user_agent', 'Browser'],
    ['fbc', 'Ad click'], ['click_id', 'Ad click'], ['fbp', 'Browser ID'], ['browser_id', 'Browser ID'],
    ['external_id', 'Customer ID'], ['first_name', 'First name'], ['fn', 'First name'], ['last_name', 'Last name'],
    ['ln', 'Last name'], ['city', 'City'], ['ct', 'City'], ['state', 'State'], ['st', 'State'], ['zip', 'Zip'],
    ['zp', 'Zip'], ['zip_code', 'Zip'], ['country', 'Country'], ['date_of_birth', 'Birthday'], ['db', 'Birthday'],
    ['gender', 'Gender'], ['ge', 'Gender']
  ]);
  function keyName(k) { return KEY_NAMES.get(String(k).toLowerCase()) || String(k).replace(/_/g, ' '); }
  var COVERAGE = [['em', 'Email'], ['ph', 'Phone'], ['client_ip_address', 'IP'], ['client_user_agent', 'Browser'],
                  ['fbc', 'Ad click'], ['fbp', 'Browser ID']];

  function pixelName(p) { return p.name || (p.role === 'main' ? 'Main pixel' : 'Backup pixel'); }

  function renderQuality(ov) {
    var q = ov.quality || [], cov = ov.coverage || {};
    var cards = q.map(function (p) {
      var e = p.emq || {};
      var sc = isNum(e.score) ? +e.score : null;
      var L = sc === null ? 'none' : sc >= 7 ? 'ok' : sc >= 5 ? 'warn' : 'fail';
      var word = {ok: 'Good', warn: 'Fair', fail: 'Poor', none: 'Not scored yet'}[L];
      var ev = e.event || 'Purchase';
      var hist = (p.emq_history || []).filter(function (x) { return isNum(x.score); });
      var trend = hist.length >= 2
        ? spark(hist.map(function (x) { return x.score; }),
                hist.map(function (x) { return fmtDate(x.at) + ': ' + (+x.score).toFixed(1) + ' out of 10'; }),
                {fit: true, floor: 0, ceil: 10})
        : '<div class="sub small">The trend appears once Meta has scored it a few times.</div>';
      var keys = Object.keys(p.meta_keys || {}).map(function (k) { return [k, p.meta_keys[k]]; })
        .sort(function (a, b) { return (+b[1] || 0) - (+a[1] || 0); });
      var chips = keys.length ? keys.map(function (kv) {
        return '<span class="chip' + ((+kv[1] || 0) > 0 ? ' on' : '') + '">' + esc(keyName(kv[0])) +
          (isNum(kv[1]) ? ' ' + esc(pct(kv[1])) : '') + '</span>';
      }).join('') : '<span class="sub small">Meta hasn\'t shared which details it receives yet.</span>';
      var e24 = p.events_24h || {};
      var failed = +e24.failed || 0;
      return '<div class="qcard"><div class="q-h"><b>' + esc(pixelName(p)) + '</b>' +
          '<span class="badge ' + (p.role === 'main' ? 'b-new' : 'b-skip') + '">' + (p.role === 'main' ? 'Main' : 'Backup') + '</span>' +
          '<span class="sub mono">' + esc(p.pixel_id) + '</span></div>' +
        '<div class="q-score"><div class="score">' + (sc === null ? '-' : esc(sc.toFixed(1))) + '<span class="of">/10</span></div>' +
          '<div><div class="q-word"><span class="dot ' + (L === 'none' ? 'mut' : L) + '" aria-hidden="true"></span>' + word + '</div>' +
          '<div class="sub">' + esc(ev) + ' events' + (e.taken_at ? ', scored ' + esc(agoText(e.taken_at)) : '') + '</div></div></div>' +
        '<div class="q-sec"><div class="sub">Last 30 days</div>' + trend + '</div>' +
        '<div class="q-sec"><div class="sub">What Meta receives on ' + esc(ev) + ' events</div><div class="chips">' + chips + '</div></div>' +
        '<div class="q-sec sub">' + esc(plural(e24.sent || 0, 'event', 'events')) + ' sent in 24 h, ' +
          (failed ? '<span class="err-t">' + esc(num(failed)) + ' rejected</span>' : 'none rejected') +
          '. Last sent ' + esc(p.last_sent_ago || 'never') + '.</div></div>';
    }).join('');

    var n = +cov.purchases || 0;
    var meters = COVERAGE.map(function (k) {
      var v = isNum(cov[k[0]]) ? Math.max(0, Math.min(100, +cov[k[0]])) : 0;
      return '<div class="meter-row"><span>' + k[1] + '</span><div class="meter" role="img" aria-label="' + k[1] + ' ' + esc(pct(cov[k[0]])) + '">' +
        '<i style="width:' + v.toFixed(1) + '%"></i></div><span class="num">' + esc(pct(cov[k[0]])) + '</span></div>';
    }).join('');
    var covCard = '<div class="qcard"><b>Details your tracker sends on sales</b>' +
      '<div class="sub">' + (n ? 'Across ' + esc(plural(n, 'sale', 'sales')) + ' sent to the main pixel in the last 7 days.'
                             : 'No sales sent to the main pixel in the last 7 days yet.') + '</div>' +
      (n ? '<div class="q-sec">' + meters + '</div>' : '') + '</div>';

    secBody('quality').innerHTML = '<div class="q-grid">' + (cards || '<div class="empty">No pixels set up.</div>') + covCard + '</div>';
  }

  function fillPixelSelect(q) {
    var sel = $('#testPixel');
    var keep = sel.value;
    if (!q.length) return;
    sel.innerHTML = q.map(function (p) {
      return '<option value="' + esc(p.pixel_id) + '">' + esc(pixelName(p)) + ' (' + (p.role === 'main' ? 'main' : 'backup') + ')</option>';
    }).join('');
    if (keep && q.some(function (p) { return p.pixel_id === keep; })) sel.value = keep;
  }

  // --- orders ---------------------------------------------------------------------------
  var DETAILS = [['email', 'Email'], ['phone', 'Phone'], ['ip', 'IP'], ['browser', 'Browser'],
                 ['ad_click_id', 'Click ID'], ['browser_id', 'Browser ID']];
  var STATUS = {sent: ['Sent', 'ok'], pending: ['Pending', 'warn'], failed: ['Failed', 'fail'],
                skipped: ['Skipped', 'mut'], not_seen: ['Not seen yet', 'warn']};
  // How the tracker knew which ad it was (attribution.resolve's sources).
  var HOW = {browser: 'Seen by the storefront pixel', shopify_last_visit: 'From Shopify\'s record of the buyer\'s last visit',
             order_note: 'From the old tracker\'s note on the order', click_id: 'Meta click ID only',
             first_visit: 'From the first page the buyer landed on',
             first_visit_unverified: 'From the first page the buyer landed on; when they clicked is unknown'};

  // The API's type stays "rebill"; the owner calls those orders MRR.
  function typeBadge(o) {
    var cls = o.type === 'new_sale' ? 'b-new' : o.type === 'rebill' ? 'b-rebill' : 'b-skip';
    var label = o.type_label || (o.type === 'new_sale' ? 'New sale' : o.type === 'rebill' ? 'MRR' : 'Skipped');
    return '<span class="badge ' + cls + '">' + esc(label) + '</span>' +
      // The first order of a subscription: a new sale, whose renewals will be MRR.
      (o.type === 'new_sale' && o.subscription === true
        ? ' <span class="badge b-sub" data-tip="First order of a subscription. Its renewals count as MRR.">Sub</span>' : '');
  }
  function pixelCell(o) {
    var ps = o.pixels || [];
    if (!ps.length) return '<span class="sub">-</span>';
    return ps.map(function (p) {
      var cls, icon, word;
      if (p.sent) { cls = 'yes'; icon = '\u2713'; word = 'sent'; }
      else if (o.tracker_status === 'pending') { cls = 'wait'; icon = '\u25cb'; word = 'waiting to send'; }
      else if (o.tracker_status === 'failed' || o.tracker_status === 'not_seen') { cls = 'no'; icon = '\u2717'; word = 'not sent'; }
      // Skipped orders, and backups added after the order was placed, are left out on purpose.
      else { cls = 'na'; icon = '\u2717'; word = 'not sent, on purpose'; }
      return '<span class="px ' + cls + '" title="' + esc(pixelName(p) + ': ' + word) + '"><span class="i" aria-hidden="true">' + icon + '</span>' +
        esc(pixelName(p)) + '<span class="sr"> ' + word + '</span></span>';
    }).join('');
  }
  // The ad that got the sale (the last one the buyer clicked), then the ads
  // they clicked before it. Every name came from an ad link: esc() each one.
  function adCell(ad, listicle, o) {
    var how = HOW[ad.source] || '';
    o = o || {};
    // Credited from the buyer's first landing page only: no later click was seen.
    var first = ad.source === 'first_visit' || ad.source === 'first_visit_unverified';
    var badge = '<span class="badge b-ad"' + (how ? ' title="' + esc(how) + '"' : '') + '>' + (ad.click ? 'Ad click' : 'Meta ad') + '</span>' +
      (o.landing === 'quiz' ? ' <span class="badge b-lst" title="The ad click came straight through the quiz funnel">Quiz Funnel</span>' :
        listicle ? ' <span class="badge b-lst" title="The ad click came through the listicle">Listicle</span>' : '') +
      (o.quiz_assist ? ' <span class="badge b-lst" title="The shopper started in the quiz, then went through the listicle">Quiz assist</span>' : '');
    var where = [ad.adset_name, ad.campaign_name].filter(Boolean);
    var helped = (Array.isArray(ad.assists) ? ad.assists : []).filter(function (a) { return a && typeof a === 'object'; });
    if (!ad.ad_name && !where.length && !helped.length) {
      return badge + '<div class="sub small">Ad name unknown: this ad has no URL tracking yet.</div>';
    }
    var names = helped.map(function (a) {
      var path = [a.adset_name, a.campaign_name].filter(Boolean).join(' \u203a ');
      return '<span class="helped"' + (path ? ' data-tip="' + esc(path) + '"' : '') + '>' + esc(a.ad_name || 'an unnamed ad') + '</span>';
    });
    return badge +
      '<div class="adpath">' + (first ? 'First came from ' : 'Sold by ') + (ad.ad_name ? '<b>' + esc(ad.ad_name) + '</b>' : 'an ad without a name') +
        (names.length ? ' \u00b7 assisted by ' + names.join(', ') : '') + '</div>' +
      (where.length ? '<div class="adpath where">' + where.map(esc).join(' <span class="gt">\u203a</span> ') + '</div>' : '');
  }
  // A sale no Meta ad got shows where it came from instead ('Shop app ads', 'Direct', 'Google').
  function sourceCell(o) {
    if (o.ad) return adCell(o.ad, !!o.listicle, o);
    if (o.channel) return '<span class="badge b-ch">' + esc(o.channel) + '</span>';
    return '<span class="sub">-</span>';
  }
  function detailCell(d) {
    d = d || {};
    return '<div class="chips">' + DETAILS.map(function (k) {
      return '<span class="chip' + (d[k[0]] ? ' on' : '') + '">' + (d[k[0]] ? '\u2713 ' : '') + k[1] +
        '<span class="sr">' + (d[k[0]] ? ' present' : ' missing') + '</span></span>';
    }).join('') + '</div>';
  }
  function statusCell(o) {
    var st = STATUS[o.tracker_status] || [o.tracker_status || 'Unknown', 'mut'];
    var h = '<div class="st"><span class="dot ' + st[1] + '"></span>' + esc(st[0]) + '</div>';
    if (o.error) h += '<div class="err-t err-small" title="' + esc(o.error) + '">' + esc(trunc(o.error, 180)) + '</div>';
    // Never for orders placed before go-live: WeTracked sent those, and a resend would count twice.
    if (o.can_resend) {
      h += '<div class="resend"><button class="btn sm" type="button" data-resend="' + esc(o.id) + '" data-name="' + esc(o.name) + '">Resend</button>' +
        '<div class="rs" aria-live="polite">' + (S.resent.get(String(o.id)) || '') + '</div></div>';
    }
    return h;
  }

  function renderOrders(d) {
    var list = d.orders || [];
    var count = isNum(d.count) ? +d.count : list.length;
    $('#ordersSub').textContent = plural(count, 'order', 'orders') + ' ' + RANGE_WORDS[S.range] + ', newest first.' +
      (count > list.length ? ' Showing the latest ' + list.length + '.' : '');
    var h = d.error ? '<div class="note warn">' + esc(d.error) + '</div>' : '';
    if (!list.length) {
      secBody('orders').innerHTML = h + '<div class="empty">' + (S.range === 'today' ? 'No orders yet today.'
        : 'No orders ' + esc(RANGE_WORDS[S.range]) + '.') + '</div>';
      return;
    }
    h += '<div class="tbl-wrap"><table class="tbl orders"><thead><tr><th>Order</th><th class="num">Total</th><th>Type</th>' +
      '<th>Sent to</th><th>Came from</th><th>Details sent</th><th>Status</th></tr></thead><tbody>' +
      list.map(function (o) {
        var who = '<b>' + esc(o.name || o.id) + '</b> <span class="when">' + esc(o.time_local) + '</span>' +
          (o.items ? '<div class="items">' + esc(o.items) + '</div>' : '');
        return '<tr>' + td('Order', who) + td('Total', esc(money(o.total, o.currency)), 'num') + td('Type', typeBadge(o)) +
          td('Sent to', pixelCell(o)) + td('Came from', sourceCell(o)) + td('Details', detailCell(o.details)) +
          td('Status', statusCell(o)) + '</tr>';
      }).join('') + '</tbody></table></div>';
    secBody('orders').innerHTML = h;
  }

  function resendMsg(r) {
    if (!r || r.error) return '<span class="err-t">Couldn\'t resend: ' + esc((r && r.error) || 'no answer') + '</span>';
    // The server says why in plain words (for example "sending MRR to Meta is switched off").
    var text = r.message || {
      sent: 'Sent to Meta.',
      failed: 'Meta did not accept it yet. The tracker keeps retrying.',
      skipped: 'Not sent. Orders older than 7 days, test orders and cancelled orders are left out on purpose.',
      pending: 'Queued. It goes out within a minute.'
    }[r.status] || ('Done: ' + r.status);
    // The message already covers repeats, so the note only backs up a missing one.
    return '<span class="' + (r.status === 'sent' ? 'ok-t' : r.status === 'failed' ? 'err-t' : '') + '">' + esc(text) + '</span>' +
      (r.note && !r.message ? ' <span class="sub">' + esc(r.note) + '</span>' : '');
  }

  function resend(btn) {
    var id = btn.dataset.resend, name = btn.dataset.name || id;
    if (!window.confirm('Resend order ' + name + ' to Meta now?\n\nMeta ignores repeats of the same order, so it will not be counted twice.')) return;
    var out = btn.closest('.resend').querySelector('.rs');
    btn.disabled = true;
    btn.textContent = 'Resending\u2026';
    api('/hub/api/resend/' + encodeURIComponent(id), {}).then(function (r) {
      S.resent.set(String(id), resendMsg(r));
    }, function (e) {
      if (e.leaving) return;
      S.resent.set(String(id), '<span class="err-t">Couldn\'t resend: ' + esc(e.message) + '</span>');
    }).then(function () {
      if (S.leaving) return;
      if (out) out.innerHTML = S.resent.get(String(id)) || '';
      btn.disabled = false;
      btn.textContent = 'Resend';
      loadSection('orders');
    });
  }

  // --- tools and small helpers -------------------------------------------------------------
  function copyText(btn) {
    var src = document.getElementById(btn.dataset.copy);
    if (!src) return;
    var text = src.textContent.trim();
    var shown = function (ok) {
      btn.textContent = ok ? 'Copied' : 'Select it and copy';
      setTimeout(function () { btn.textContent = 'Copy'; }, 2000);
    };
    var fallback = function () {
      var range = document.createRange();
      range.selectNodeContents(src);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      shown(ok);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { shown(true); }, fallback);
    } else {
      fallback();
    }
  }

  $('#testForm').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var out = $('#testOut'), btn = $('#testBtn');
    var code = $('#testCode').value.trim();
    if (!code) {
      out.innerHTML = '<span class="err-t">Paste the code from Events Manager &gt; Test events first.</span>';
      return;
    }
    btn.disabled = true;
    btn.textContent = 'Sending\u2026';
    out.innerHTML = '';
    api('/hub/api/test-event', {test_event_code: code, pixel_id: $('#testPixel').value || null}).then(function (r) {
      out.innerHTML = r.ok
        ? '<span class="ok-t">\u2713 Sent.</span> ' + esc(r.next || 'Check Events Manager > Test events.') +
          (r.fbtrace_id ? ' <span class="sub mono">Meta trace ' + esc(r.fbtrace_id) + '</span>' : '')
        : '<span class="err-t">Meta said no: ' + esc(r.error || 'unknown error') + '</span>';
    }, function (e) {
      if (!e.leaving) out.innerHTML = '<span class="err-t">Couldn\'t send it: ' + esc(e.message) + '</span>';
    }).then(function () {
      btn.disabled = false;
      btn.textContent = 'Send test event';
    });
  });

  $('#runChecks').addEventListener('click', function () {
    var b = $('#runChecks'), msg = $('#runMsg');
    b.disabled = true;
    b.textContent = 'Checking\u2026';
    msg.innerHTML = '';
    api('/hub/api/watchdog/run', {}).then(function (r) {
      if (r.error) {                                // the run itself failed: there is no verdict to report
        msg.innerHTML = '<span class="err-t">' + esc(r.error) + '</span>';
        return;
      }
      var L = lvl(r.status);
      msg.innerHTML = '<span class="dot ' + L + '"></span> Checks finished: ' + HEAD[L] + '.';
      S.wd = null;
      loadSection('overview');
      if ($('#wdHistory').open) renderHistory(true);
    }, function (e) {
      if (!e.leaving) msg.innerHTML = '<span class="err-t">Couldn\'t run the checks: ' + esc(e.message) + '</span>';
    }).then(function () {
      b.disabled = false;
      b.textContent = 'Run checks now';
    });
  });

  $('#refreshBtn').addEventListener('click', function () { loadAll(); });

  document.addEventListener('click', function (ev) {
    var t = ev.target instanceof Element ? ev.target : ev.target.parentElement;
    if (!t) return;
    var el;
    if ((el = t.closest('[data-range]'))) return setRange(el.dataset.range);
    if ((el = t.closest('[data-preset]'))) return setPnl(el.dataset.preset);
    if ((el = t.closest('[data-group]'))) return setGroup(el.dataset.group);
    if ((el = t.closest('[data-funnel]'))) return setFunnel(el.dataset.funnel);
    if ((el = t.closest('[data-retry]'))) return loadSection(el.dataset.retry);
    if ((el = t.closest('[data-run]'))) return showRun(el.dataset.run);
    if ((el = t.closest('[data-resend]'))) return resend(el);
    if ((el = t.closest('[data-approve]'))) return decide(el, 'approve');
    if ((el = t.closest('[data-dismiss]'))) return decide(el, 'dismiss');
    if ((el = t.closest('[data-copy]'))) return copyText(el);
    if (t.closest('[data-close-run]')) $('#runDetail').innerHTML = '';
  });

  // toggle doesn't bubble, so listen in the capture phase.
  document.addEventListener('toggle', function (ev) {
    var el = ev.target;
    if (el.id === 'wdHistory' && el.open) renderHistory(false);
    if (el.classList && el.classList.contains('camp')) {
      if (el.open) S.closed.delete(el.dataset.camp); else S.closed.add(el.dataset.camp);
    }
  }, true);

  // One shared tooltip. Its text goes in with textContent, never as HTML.
  var tip = $('#tip'), tipTimer = null;
  function showTip(el, x, y) {
    var text = el.getAttribute('data-tip');
    if (!text) { hideTip(); return; }
    tip.textContent = text;
    tip.classList.add('on');
    var r = tip.getBoundingClientRect();
    var left = x + 12, top = y - r.height - 10;
    if (left + r.width > window.innerWidth - 8) left = x - r.width - 12;
    if (left < 8) left = 8;
    if (top < 8) top = y + 16;
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  }
  function hideTip() { tip.classList.remove('on'); }
  document.addEventListener('pointermove', function (ev) {
    if (ev.pointerType !== 'mouse') return;
    var el = ev.target instanceof Element ? ev.target.closest('[data-tip]') : null;
    if (el) showTip(el, ev.clientX, ev.clientY); else hideTip();
  });
  document.addEventListener('pointerdown', function (ev) {
    if (ev.pointerType === 'mouse') return;
    var el = ev.target instanceof Element ? ev.target.closest('[data-tip]') : null;
    clearTimeout(tipTimer);
    if (!el) { hideTip(); return; }
    showTip(el, ev.clientX, ev.clientY);
    tipTimer = setTimeout(hideTip, 2500);
  });
  window.addEventListener('scroll', hideTip, {passive: true});

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState !== 'visible') { clearTimeout(S.timer); S.timer = null; return; }
    if (Date.now() - S.lastLoad >= REFRESH_MS) loadAll(); else schedule();
  });

  // --- the views: Tracking (this page), the creative tracker, the P&L app and the agent ---
  function showView(view) {
    S.view = VIEWS.indexOf(view) >= 0 ? view : 'hub';
    var pnl = S.view === 'pnl', ag = S.view === 'agent', hub = S.view === 'hub', cr = S.view === 'creative';
    var ch = S.view === 'corehub', be = S.view === 'backend', dbv = S.view === 'database', lm = S.view === 'loom';
    document.querySelector('main.wrap').hidden = !hub;
    document.getElementById('hubFooter').hidden = !hub;
    document.getElementById('creativeApp').hidden = !cr;
    document.getElementById('loomApp').hidden = !lm;
    document.getElementById('pnlApp').hidden = !pnl;
    document.getElementById('coreHubApp').hidden = !ch;
    document.getElementById('backendApp').hidden = !be;
    document.getElementById('databaseApp').hidden = !dbv;
    document.getElementById('agentApp').hidden = !ag;
    [['tabHub', hub], ['tabCreative', cr], ['tabLoom', lm], ['tabPnl', pnl], ['tabCoreHub', ch], ['tabDatabase', dbv], ['tabBackend', be], ['tabAgent', ag]].forEach(function (t) {
      var b = document.getElementById(t[0]);
      b.classList.toggle('on', t[1]);
      b.setAttribute('aria-pressed', t[1] ? 'true' : 'false');
    });
    if (cr) openCreative(0);
    if (lm) openLoom(0);
    if (pnl) openPnl(0);
    if (ch) openCoreHub(0);
    if (be) loadBackend();
    if (dbv) loadDatabase();
    if (ag) openAgent();
    writeHash();
  }
  // The P&L's address comes with the first P&L numbers (the server's PNL_URL); the app loads once
  // and then stays open, so switching back and forth keeps its place.
  function openPnl(tries) {
    var f = document.getElementById('pnlFrame');
    if (f.getAttribute('src')) return;
    if (webUrl(S.pnlUrl)) { f.setAttribute('src', embedUrl(S.pnlUrl)); return; }
    if (tries < 60) setTimeout(function () { openPnl(tries + 1); }, 250);
  }
  // The P&L asked for its embedded look: no title row of its own, Core HQ's black, Core HQ's column.
  function embedUrl(u) { return u + (u.indexOf('?') < 0 ? '?' : '&') + 'embed=1'; }
  // The creative tracker (its own app; the server's CREATIVE_URL, which comes with the P&L numbers):
  // loads once, then keeps its place.
  function openCreative(tries) {
    var f = document.getElementById('creativeFrame');
    if (f.getAttribute('src')) return;
    if (webUrl(S.creativeUrl)) { f.setAttribute('src', S.creativeUrl); return; }
    if (tries < 60) setTimeout(function () { openCreative(tries + 1); }, 250);
  }
  // Core Loom (our own Loom) lives in the creative tracker's app at /loom. Recording needs the
  // screen, camera and mic, so the frame allows them; Core Loom opens its recorder in its own tab.
  function openLoom(tries) {
    var f = document.getElementById('loomFrame');
    if (f.getAttribute('src')) return;
    var u = webUrl(S.creativeUrl);
    if (u) { f.setAttribute('src', u.replace(/^(https?:\/\/[^\/]+).*$/, '$1') + '/loom'); return; }
    if (tries < 60) setTimeout(function () { openLoom(tries + 1); }, 250);
  }
  // Core Hub (the downloader and transcriber; the server's CORE_HUB_URL): asks for its access key the
  // first time, then remembers it inside this tab; loads once and keeps its place.
  function openCoreHub(tries) {
    var f = document.getElementById('coreHubFrame');
    if (f.getAttribute('src')) return;
    if (webUrl(S.coreHubUrl)) { f.setAttribute('src', S.coreHubUrl); return; }
    if (tries < 60) setTimeout(function () { openCoreHub(tries + 1); }, 250);
  }
  document.getElementById('tabHub').addEventListener('click', function () { showView('hub'); window.scrollTo(0, 0); });
  document.getElementById('tabCreative').addEventListener('click', function () { showView('creative'); window.scrollTo(0, 0); });
  document.getElementById('tabLoom').addEventListener('click', function () { showView('loom'); window.scrollTo(0, 0); });
  document.getElementById('tabPnl').addEventListener('click', function () { showView('pnl'); window.scrollTo(0, 0); });
  document.getElementById('tabCoreHub').addEventListener('click', function () { showView('corehub'); window.scrollTo(0, 0); });
  document.getElementById('tabDatabase').addEventListener('click', function () { showView('database'); window.scrollTo(0, 0); });
  document.getElementById('tabBackend').addEventListener('click', function () { showView('backend'); window.scrollTo(0, 0); });
  document.getElementById('tabAgent').addEventListener('click', function () { showView('agent'); window.scrollTo(0, 0); });

  // --- the Backend tab: deliveries, refunds and chargebacks -------------------------------
  var BE_RANGES = ['today', '7d', '30d', '90d', '180d', '365d'];
  var COUNTRY = {EU: 'Europe (not UK)', US: 'United States', GB: 'United Kingdom', CA: 'Canada', AU: 'Australia', NZ: 'New Zealand',
                 IE: 'Ireland', DE: 'Germany', FR: 'France', NL: 'Netherlands', SE: 'Sweden', SG: 'Singapore',
                 AE: 'United Arab Emirates', SA: 'Saudi Arabia', ZA: 'South Africa', '??': 'Unknown'};
  var regionNames = null;
  try { regionNames = new Intl.DisplayNames(['en'], {type: 'region'}); } catch (e) { /* older browser: the short list */ }
  function country(c) {
    c = String(c || '??').toUpperCase();
    if (COUNTRY[c]) return COUNTRY[c];
    try { var n = regionNames && /^[A-Z]{2}$/.test(c) ? regionNames.of(c) : ''; return n && n !== c ? n : c; } catch (e) { return c; }
  }
  // One word and one colour for a parcel, from the server's state.
  var SHIP_LEVEL = {delivered: 'ok', transit: 'mut', stuck: 'warn', late: 'warn', unscanned: 'warn', failed: 'fail', untracked: 'mut'};
  var SHIP_WORD = {delivered: 'Delivered', transit: 'On its way', stuck: 'Stuck', late: 'Late', unscanned: 'Not scanned',
                   failed: 'Problem', untracked: 'Not tracked'};
  var DISPUTE_LEVEL = {open: 'warn', won: 'ok', lost: 'fail', other: 'mut'};
  var DISPUTE_WORD = {needs_response: 'Needs a response', under_review: 'Under review', won: 'Won', lost: 'Lost',
                      accepted: 'Accepted (lost)', prevented: 'Prevented', charge_refunded: 'Refunded'};
  function dotLevel(l) { return l === 'ok' || l === 'warn' || l === 'fail' ? l : 'mut'; }
  function pill(level, text) {
    return '<span class="badge"><span class="dot ' + dotLevel(level) + '" aria-hidden="true"></span>' + esc(text) + '</span>';
  }
  function dayText(v, suffix) {
    if (!isNum(v)) return '-';
    return (+v < 1 ? '<1' : (Math.round(+v * 10) / 10).toLocaleString('en-US', {maximumFractionDigits: 1})) + ' d' + (suffix || '');
  }
  function rateText(v) {
    if (!isNum(v)) return '-';
    var p = +v * 100;
    return (p > 0 && p < 10 ? String(+p.toFixed(1)) : Math.round(p)) + '%';
  }
  function beTile(label, value, sub, level) {
    return '<div class="kpi"><div class="k-label">' + esc(label) + '</div><div class="k-val' + (level ? ' ' + level : '') + '">' +
      esc(value) + '</div>' + (sub ? '<div class="k-sub">' + esc(sub) + '</div>' : '') + '</div>';
  }
  function barCell(v, max, warn) {
    var w = isNum(v) && isNum(max) && +max > 0 ? Math.max(2, Math.round(+v / +max * 100)) : 0;
    return '<div class="be-bar' + (warn ? ' warn' : '') + '"><i style="width:' + w + '%"></i></div>';
  }
  function beCard(title, sub, body, note) {
    return '<div class="lst"><div class="f-h"><b>' + esc(title) + '</b>' + (sub ? '<span class="sub">' + esc(sub) + '</span>' : '') +
      '</div>' + body + (note ? '<p class="sub small">' + esc(note) + '</p>' : '') + '</div>';
  }
  var ATTENTION_SHOWN = 12;
  function attentionBlock(items, all) {
    items = objects(items);
    if (!items.length) return beCard('Needs attention', '', '<p class="sub" style="margin:6px 0 0">Nothing needs a hand right now.</p>');
    var shown = all ? items : items.slice(0, ATTENTION_SHOWN);
    var more = items.length - shown.length;
    return beCard('Needs attention', plural(items.length, 'item', 'items'), '<ul class="be-att">' + shown.map(function (i) {
      return '<li><span class="dot ' + dotLevel(i.level) + '" aria-hidden="true"></span><span>' + esc(i.text) + '</span>' +
        (i.since ? '<span class="when">' + esc(fmtDate(i.since)) + '</span>' : '') + '</li>';
    }).join('') + '</ul>' + (more > 0 ? '<button type="button" class="btn sm" id="beMore" style="margin-top:10px">Show all ' + esc(num(items.length)) + '</button>' : ''),
      'Worst first: disputes to answer, parcels that stopped moving or were never scanned, orders paid and not shipped after ' +
      '3 days. Not limited to the range above.');
  }
  function tilesBlock(t, cur) {
    t = t || {};
    var stuck = +t.stuck || 0, open = +t.disputes_open || 0, failed = +t.failed || 0;
    return '<div class="kpis">' +
      beTile('Orders', num(t.orders), money(t.revenue, cur) + ' in sales') +
      beTile('Shipped', rateText(t.shipped_rate), (isNum(t.days_to_ship) ? dayText(t.days_to_ship) + ' to ship on average' : 'no shipping times yet') +
        (+t.unfulfilled ? ' \u00b7 ' + plural(t.unfulfilled, 'order', 'orders') + ' not shipped' : '')) +
      beTile('Delivered', rateText(t.delivered_rate), (isNum(t.days_to_deliver) ? dayText(t.days_to_deliver) + ' in transit on average' : 'no deliveries confirmed yet') +
        ' \u00b7 ' + plural(t.delivered, 'parcel', 'parcels')) +
      beTile('On their way', num(t.in_transit), (+t.untracked ? plural(t.untracked, 'parcel', 'parcels') + ' not tracked' : 'every parcel tracked')) +
      beTile('Stuck', num(stuck + failed), (stuck + failed ? plural(stuck, 'parcel', 'parcels') + ' quiet or late' + (failed ? ', ' + plural(failed, 'problem', 'problems') : '') : 'no parcel stuck'),
        failed ? 'fail' : stuck ? 'warn' : '') +
      beTile('Refunds', num(t.refunds) + (isNum(t.refund_rate) ? ' \u00b7 ' + rateText(t.refund_rate) : ''), money(t.refunded, cur) + ' refunded') +
      beTile('Chargebacks', num(t.chargebacks) + (isNum(t.chargeback_rate) ? ' \u00b7 ' + rateText(t.chargeback_rate) : ''), money(t.charged_back, cur) + ' disputed') +
      beTile('Open disputes', num(open), 'won ' + num(t.disputes_won) + ' \u00b7 lost ' + num(t.disputes_lost), open ? 'fail' : '') +
      '</div>';
  }
  var COUNTRIES_SHOWN = 8;
  function countriesBlock(rows, cur, all) {
    rows = objects(rows);
    if (!rows.length) return '';
    var total = rows.length;
    if (!all) rows = rows.slice(0, COUNTRIES_SHOWN);
    var maxDays = Math.max.apply(null, rows.map(function (r) { return isNum(r.avg_days) ? +r.avg_days : 0; }).concat([0]));
    return beCard('By country', 'orders in the range', '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Country</th><th class="num">Orders</th>' +
      '<th class="num">Sales</th><th class="num">Delivered</th><th>Avg days to deliver</th><th class="num">On the way</th><th class="num">Stuck</th>' +
      '<th class="num">Refunds</th><th class="num">Chargebacks</th></tr></thead><tbody>' + rows.map(function (r) {
        var stuck = (+r.stuck || 0) + (+r.failed || 0);
        return '<tr>' + td('Country', '<b>' + esc(country(r.key)) + '</b>') + td('Orders', esc(num(r.orders)), 'num') +
          td('Sales', esc(money(r.revenue, cur)), 'num') + td('Delivered', esc(num(r.delivered)), 'num') +
          td('Avg days', '<div class="row" style="flex-wrap:nowrap"><span style="min-width:40px">' + esc(dayText(r.avg_days)) + '</span>' + barCell(r.avg_days, maxDays, isNum(r.avg_days) && +r.avg_days > 21) + '</div>') +
          td('On the way', esc(num(r.transit)), 'num') + td('Stuck', stuck ? '<b>' + esc(num(stuck)) + '</b>' : '0', 'num') +
          td('Refunds', esc(num(r.refunds)) + (isNum(r.refund_rate) ? ' <span class="sub">' + esc(rateText(r.refund_rate)) + '</span>' : ''), 'num') +
          td('Chargebacks', esc(num(r.chargebacks)) + (isNum(r.chargeback_rate) ? ' <span class="sub">' + esc(rateText(r.chargeback_rate)) + '</span>' : ''), 'num') + '</tr>';
      }).join('') + '</tbody></table></div>' + (total > rows.length ? '<button type="button" class="btn sm" id="beAllCountries" style="margin-top:12px">Show all ' +
        esc(num(total)) + ' countries</button>' : ''));
  }
  function transitBlock(hist, t) {
    hist = objects(hist);
    var total = hist.reduce(function (a, h) { return a + (+h.n || 0); }, 0);
    if (!total) return beCard('Delivery times', '', '<p class="sub" style="margin:6px 0 0">No delivered parcel with a transit time yet.</p>');
    var max = Math.max.apply(null, hist.map(function (h) { return +h.n || 0; }));
    return beCard('Delivery times', 'shipped to delivered, ' + plural(total, 'parcel', 'parcels'), '<div class="be-hist">' + hist.map(function (h) {
      return '<span>' + esc(h.label) + '</span>' + barCell(h.n, max, /Over|22/.test(String(h.label))) + '<span class="n">' + esc(num(h.n)) + '</span>';
    }).join('') + '</div>');
  }
  function carriersBlock(rows) {
    rows = objects(rows);
    if (!rows.length) return '';
    return beCard('By carrier', '', '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Carrier</th><th class="num">Parcels</th>' +
      '<th class="num">Delivered</th><th class="num">Avg days</th><th class="num">On the way</th><th class="num">Stuck</th></tr></thead><tbody>' +
      rows.map(function (r) {
        var n = isNum(r.parcels) ? +r.parcels : (+r.delivered || 0) + (+r.transit || 0) + (+r.stuck || 0) + (+r.failed || 0) + (+r.untracked || 0);
        return '<tr>' + td('Carrier', '<b>' + esc(r.key) + '</b>') + td('Parcels', esc(num(n)), 'num') + td('Delivered', esc(num(r.delivered)), 'num') +
          td('Avg days', esc(dayText(r.avg_days)), 'num') + td('On the way', esc(num(r.transit)), 'num') +
          td('Stuck', esc(num((+r.stuck || 0) + (+r.failed || 0))), 'num') + '</tr>';
      }).join('') + '</tbody></table></div>');
  }
  function shipmentsBlock(rows) {
    rows = objects(rows);
    if (!rows.length) return beCard('Parcels', '', '<p class="sub" style="margin:6px 0 0">No parcel shipped in the range yet.</p>');
    return beCard('Parcels', 'newest first', '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Order</th><th>To</th><th>Shipped</th>' +
      '<th>Carrier</th><th>Status</th><th class="num">Days</th><th>Last update</th></tr></thead><tbody>' + rows.map(function (s) {
        var st = String(s.state || 'untracked'), word = SHIP_WORD[st] || st;
        var last = s.last_event ? esc(s.last_event) + (s.last_event_at ? '<span class="be-cell-sub">' + esc(fmtClock(s.last_event_at, true)) + '</span>' : '')
          : (s.error ? '<span class="sub">' + esc(s.error) + '</span>' : '<span class="sub">' + (st === 'untracked' ? 'no status from 17TRACK yet' : 'no scan yet') + '</span>');
        return '<tr>' + td('Order', '<b>' + esc(s.order_name) + '</b><span class="be-cell-sub">' + esc(s.tracking_number) + '</span>') +
          td('To', esc(country(s.country)) + (s.city ? '<span class="be-cell-sub">' + esc(s.city) + '</span>' : '')) +
          td('Shipped', esc(fmtDate(s.shipped_at))) + td('Carrier', esc(s.carrier || '-')) + td('Status', pill(SHIP_LEVEL[st], word)) +
          td('Days', esc(dayText(s.days, st === 'delivered' ? '' : ' so far')), 'num') + td('Last update', last) + '</tr>';
      }).join('') + '</tbody></table></div>');
  }
  function refundsBlock(rows, reasons, cur) {
    rows = objects(rows); reasons = objects(reasons);
    var chips = reasons.length ? '<div class="be-reasons">' + reasons.map(function (r) {
      return '<span class="badge">' + esc(r.kind) + ' \u00b7 ' + esc(num(r.n)) + ' \u00b7 ' + esc(money(r.amount, cur)) + '</span>';
    }).join('') + '</div>' : '';
    if (!rows.length) return beCard('Refunds', '', '<p class="sub" style="margin:6px 0 0">No refund in the range.</p>');
    return beCard('Refunds', plural(rows.length, 'refund', 'refunds'), chips + '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Order</th>' +
      '<th>Date</th><th class="num">Amount</th><th>To</th><th>Reason</th></tr></thead><tbody>' + rows.map(function (r) {
        return '<tr>' + td('Order', '<b>' + esc(r.order_name) + '</b>') + td('Date', esc(fmtDate(r.at))) +
          td('Amount', esc(money(r.amount, r.currency || cur)), 'num') + td('To', esc(country(r.country))) +
          td('Reason', esc(r.kind) + (r.note && r.kind !== 'No note' ? '<span class="be-cell-sub">' + esc(r.note) + '</span>' : '')) + '</tr>';
      }).join('') + '</tbody></table></div>', 'Refunds marked "Chargeback alert" were given before a chargeback could be filed (Ethoca). They count as chargebacks avoided, not as disputes.');
  }
  function disputesBlock(rows, cur) {
    rows = objects(rows);
    if (!rows.length) return beCard('Chargebacks', '', '<p class="sub" style="margin:6px 0 0">No chargeback or inquiry in the range.</p>');
    return beCard('Chargebacks', plural(rows.length, 'dispute', 'disputes'), '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Order</th>' +
      '<th>Opened</th><th class="num">Amount</th><th>Reason</th><th>Status</th><th>Respond by</th></tr></thead><tbody>' + rows.map(function (d) {
        var st = String(d.status || ''), b = String(d.bucket || 'other');
        var due = d.due_by && b === 'open' ? esc(fmtDate(d.due_by)) + (isNum(d.days_left) ? '<span class="be-cell-sub">' +
          (+d.days_left < 0 ? 'overdue' : esc(dayText(Math.max(0, +d.days_left), ' left'))) + '</span>' : '') : '-';
        return '<tr>' + td('Order', '<b>' + esc(d.order_name || d.order_id) + '</b>' + (d.type && d.type !== 'chargeback' ? '<span class="be-cell-sub">' + esc(d.type) + '</span>' : '')) +
          td('Opened', esc(fmtDate(d.opened_at))) + td('Amount', esc(money(d.amount, d.currency || cur)), 'num') +
          td('Reason', esc(String(d.reason || '').replace(/_/g, ' ') || '-')) + td('Status', pill(DISPUTE_LEVEL[b], DISPUTE_WORD[st] || st)) +
          td('Respond by', due) + '</tr>';
      }).join('') + '</tbody></table></div>');
  }
  function syncLine(s) {
    s = s || {};
    var bits = [];
    bits.push(s.running ? 'Syncing now\u2026' : s.synced_at ? 'Synced ' + agoText(s.synced_at) : 'Not synced yet');
    if (s.track17 === 'on') bits.push('17TRACK on \u00b7 ' + plural(s.tracked || 0, 'parcel', 'parcels') + ' tracked');
    else if (s.track17 === 'off' && +s.pushed > 0) bits.push('Parcel statuses from the 17TRACK app \u00b7 ' + plural(s.pushed, 'parcel', 'parcels') + ' in 30 days');
    else if (s.track17 === 'off') bits.push('No parcel statuses yet: turn on Order Status Auto-push in the 17TRACK app (Settings > Shopify Status)');
    else if (s.track17) bits.push(String(s.track17));
    if (s.error) bits.push(String(s.error));
    return '<div class="be-sync">' + bits.map(function (b) { return '<span>' + esc(b) + '</span>'; }).join('') + '</div>';
  }
  var beOpen = {attention: false, countries: false};          // "Show all" stays open across refreshes
  function renderBackend(d) {
    S.be = d;
    var cur = d.currency || 'USD';
    document.getElementById('beBody').innerHTML = attentionBlock(d.attention, beOpen.attention) + tilesBlock(d.tiles, cur) +
      countriesBlock(d.countries, cur, beOpen.countries) + '<div class="be-two">' + transitBlock(d.transit_histogram, d.tiles) + carriersBlock(d.carriers) + '</div>' +
      shipmentsBlock(d.shipments) + refundsBlock(d.refunds, d.refund_reasons, cur) + disputesBlock(d.disputes, cur) + syncLine(d.sync);
    setBeBadge({count: objects(d.attention).length, urgent: objects(d.attention).filter(function (i) { return i.level === 'fail'; }).length});
  }
  function setBeBadge(a) {
    var b = document.getElementById('beBadge');
    var n = a && isNum(a.count) ? +a.count : 0;
    b.hidden = !n;
    b.textContent = n > 99 ? '99+' : String(n);
    b.setAttribute('title', n ? plural(n, 'thing needs', 'things need') + ' a hand in Backend' : '');
  }
  function loadBeAlerts() {
    return api('/hub/api/backend/alerts').then(setBeBadge).catch(function () { /* the badge keeps its last count */ });
  }
  var beSeq = 0;
  function loadBackend() {
    var seq = ++beSeq, msg = document.getElementById('beMsg');
    return api('/hub/api/backend?range=' + encodeURIComponent(S.beRange)).then(function (d) {
      if (seq !== beSeq) return;
      msg.innerHTML = '';
      renderBackend(d);
    }).catch(function (e) {
      if (seq !== beSeq || (e && e.leaving)) return;
      msg.innerHTML = '<div class="errbox"><span>Couldn\'t load the backend: ' + esc(sentence(e && e.message ? e.message : 'unknown error')) + '</span></div>';
    });
  }
  document.getElementById('beBody').addEventListener('click', function (ev) {
    var b = ev.target instanceof Element ? ev.target.closest('button') : null;
    if (!b || !S.be) return;
    if (b.id === 'beMore') { beOpen.attention = true; renderBackend(S.be); }
    if (b.id === 'beAllCountries') { beOpen.countries = true; renderBackend(S.be); }
  });
  document.getElementById('beRange').addEventListener('click', function (ev) {
    var b = ev.target instanceof Element ? ev.target.closest('button[data-range]') : null;
    if (!b || BE_RANGES.indexOf(b.getAttribute('data-range')) < 0) return;
    S.beRange = b.getAttribute('data-range');
    document.querySelectorAll('#beRange button').forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
    loadBackend();
  });
  document.getElementById('beSync').addEventListener('click', function () {
    var btn = document.getElementById('beSync');
    btn.disabled = true;
    btn.textContent = 'Syncing\u2026';
    api('/hub/api/backend/sync', {}).then(function () {
      setTimeout(loadBackend, 6000);
      setTimeout(function () { loadBackend(); btn.disabled = false; btn.textContent = 'Sync now'; }, 30000);
    }).catch(function () { btn.disabled = false; btn.textContent = 'Sync now'; });
  });

  // --- the Database tab: who buys, from where, on what, and the quiz -----------------------
  var PIE_FILLS = ['#fafafa', '#8c8c8c', '#404040', '#262626'];
  function pieSvg(slices) {
    var total = slices.reduce(function (a, s) { return a + (+s.n || 0); }, 0);
    if (!total) return '<svg viewBox="0 0 42 42" aria-hidden="true"><circle cx="21" cy="21" r="15.9" fill="none" stroke="#1f1f1f" stroke-width="6"></circle></svg>';
    var off = 25, parts = slices.map(function (s, i) {
      var pct = (+s.n || 0) / total * 100;
      var el = '<circle cx="21" cy="21" r="15.9" fill="none" stroke="' + PIE_FILLS[i % PIE_FILLS.length] + '" stroke-width="6" ' +
        'stroke-dasharray="' + pct.toFixed(2) + ' ' + (100 - pct).toFixed(2) + '" stroke-dashoffset="' + off.toFixed(2) + '"></circle>';
      off -= pct;
      return el;
    });
    return '<svg viewBox="0 0 42 42" aria-hidden="true">' + parts.join('') + '</svg>';
  }
  function pieBlock(title, slices, cur, note) {
    slices = objects(slices);
    var total = slices.reduce(function (a, s) { return a + (+s.n || 0); }, 0);
    var legend = '<ul class="db-legend">' + slices.map(function (s, i) {
      return '<li><i style="background:' + PIE_FILLS[i % PIE_FILLS.length] + '"></i><span>' + esc(s.label || s.key) + '</span>' +
        '<span class="n">' + esc(num(s.n)) + (total ? ' \u00b7 ' + esc(rateText(s.share != null ? s.share : (+s.n || 0) / total)) : '') +
        (isNum(s.revenue) ? ' \u00b7 ' + esc(money(s.revenue, cur)) : '') + '</span></li>';
    }).join('') + '</ul>';
    return beCard(title, total ? plural(total, 'sale', 'sales') : '', total ? '<div class="db-pie">' + pieSvg(slices) + legend + '</div>'
      : '<p class="sub" style="margin:6px 0 0">No sale in the range.</p>', note);
  }
  function hbars(rows, cur, labelOf) {
    rows = objects(rows);
    if (!rows.length) return '<p class="sub" style="margin:6px 0 0">Nothing yet.</p>';
    var max = Math.max.apply(null, rows.map(function (r) { return +r.n || 0; }));
    return '<div class="db-hbars">' + rows.map(function (r) {
      return '<span>' + esc(labelOf ? labelOf(r) : (r.label || r.key)) + '</span>' + barCell(r.n, max) +
        '<span class="n">' + esc(num(r.n)) + (isNum(r.share) ? ' \u00b7 ' + esc(rateText(r.share)) : '') + '</span>';
    }).join('') + '</div>';
  }
  function trendsBlock(lines) {
    lines = (Array.isArray(lines) ? lines : []).map(String);
    return beCard('What moved', lines.length ? 'against the period before' : '',
      lines.length ? '<ul class="db-trends">' + lines.map(function (l) { return '<li>' + esc(l) + '</li>'; }).join('') + '</ul>'
        : '<p class="sub" style="margin:6px 0 0">Nothing to compare yet.</p>');
  }
  function dbTiles(t, cur) {
    t = t || {};
    return '<div class="kpis db-tiles">' + beTile('New sales', num(t.sales), money(t.revenue, cur)) +
      beTile('Avg order', money(isNum(t.sales) && +t.sales ? +t.revenue / +t.sales : null, cur), 'per new sale') +
      beTile('MRR', num(t.mrr), 'subscription rebills') + beTile('Countries', num(t.countries), 'with a sale') +
      beTile('Quiz takers', num(t.quiz_takers), 'started the quiz') + beTile('Abandoned', num(t.abandoned), 'checkouts left') + '</div>';
  }
  var DB_STEP = 5;                                             // rows shown at first, and added per "Show 5 more"
  var dbShown = {countries: DB_STEP, records: DB_STEP};        // kept across refreshes
  function moreButton(id, total, shown) {
    return total > shown ? '<button type="button" class="btn sm" id="' + id + '" style="margin-top:12px">Show ' +
      num(Math.min(DB_STEP, total - shown)) + ' more (' + num(total - shown) + ' left)</button>' : '';
  }
  function dbCountries(rows, cur) {
    rows = objects(rows);
    if (!rows.length) return '';
    var total = rows.length;
    rows = rows.slice(0, dbShown.countries);
    var max = Math.max.apply(null, rows.map(function (r) { return +r.n || 0; }));
    return beCard('By country', 'new sales \u00b7 Europe without the UK counts as one', '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Country</th><th>Share</th>' +
      '<th class="num">Sales</th><th class="num">Revenue</th><th class="num">Avg order</th><th class="num">MRR</th>' +
      '<th class="num">1 / 3 / 5 bottles</th><th class="num">Partner buying</th></tr></thead><tbody>' + rows.map(function (r) {
        var b = r.bundles || {};
        var members = Array.isArray(r.members) ? r.members.map(String) : [];
        return '<tr>' + td('Country', '<b>' + esc(country(r.key)) + '</b>' + (members.length ? '<span class="be-cell-sub">' +
          esc(members.map(country).join(', ')) + '</span>' : '')) + td('Share', '<div class="row" style="flex-wrap:nowrap"><span style="min-width:40px">' +
          esc(rateText(r.share)) + '</span>' + barCell(r.n, max) + '</div>') + td('Sales', esc(num(r.n)), 'num') +
          td('Revenue', esc(money(r.revenue, cur)), 'num') + td('Avg order', esc(money(r.aov, cur)), 'num') + td('MRR', esc(num(r.mrr)), 'num') +
          td('Bottles', esc(num(b['1'] || 0) + ' / ' + num(b['3'] || 0) + ' / ' + num(b['5'] || 0)), 'num') +
          td('Partner buying', esc(isNum(r.her) ? rateText(r.her) : '-'), 'num') + '</tr>';
      }).join('') + '</tbody></table></div>' + moreButton('dbMoreCountries', total, rows.length));
  }
  var DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  function heatBlock(heat) {
    heat = Array.isArray(heat) ? heat : [];
    var max = 0, total = 0;
    heat.forEach(function (row) { (row || []).forEach(function (v) { max = Math.max(max, +v || 0); total += +v || 0; }); });
    if (!total) return beCard('When they buy', '', '<p class="sub" style="margin:6px 0 0">No sale in the range.</p>');
    var cells = '<span></span>' + Array.apply(null, Array(24)).map(function (_, h) { return '<span class="h">' + (h % 6 === 0 ? (h % 12 || 12) + (h < 12 ? 'a' : 'p') : '') + '</span>'; }).join('');
    for (var d = 0; d < 7; d++) {
      cells += '<span class="d">' + DAYS[d] + '</span>';
      for (var h = 0; h < 24; h++) {
        var v = +((heat[d] || [])[h]) || 0;
        cells += '<span class="c" title="' + esc(DAYS[d] + ' ' + (h % 12 || 12) + (h < 12 ? ' AM' : ' PM') + ': ' + plural(v, 'sale', 'sales')) + '"' +
          (v ? ' style="background:rgba(250,250,250,' + (0.12 + 0.88 * v / max).toFixed(2) + ')"' : '') + '></span>';
      }
    }
    return beCard('When they buy', 'store time, new sales', '<div class="db-heat">' + cells + '</div>');
  }
  function abandonedBlock(a, cur) {
    a = a || {};
    if (!+a.n) return beCard('Abandoned checkouts', '', '<p class="sub" style="margin:6px 0 0">No abandoned checkout in the range.</p>');
    var STEP = {cart: 'Left at the cart', shipping: 'Left at shipping', payment: 'Left at payment'};
    return beCard('Abandoned checkouts', plural(a.n, 'checkout', 'checkouts') + ' \u00b7 ' + money(a.value, cur) +
      (isNum(a.rate) ? ' \u00b7 ' + rateText(a.rate) + ' of all checkouts' : '') + (a.recovered ? ' \u00b7 ' + num(a.recovered) + ' came back and bought' : ''),
      '<div class="db-three">' + '<div><div class="lab">Where they left</div>' + hbars(a.steps, cur, function (r) { return STEP[r.key] || r.key; }) + '</div>' +
      '<div><div class="lab">Country</div>' + hbars(a.countries, cur, function (r) { return country(r.key); }) + '</div>' +
      '<div><div class="lab">Landing page</div>' + hbars(a.landing, cur, function (r) { return ({direct: 'Product page', listicle: 'Listicle', quiz: 'Quiz'})[r.key] || r.key; }) + '</div></div>',
      'Payment means they chose shipping and stopped at the card. Shipping means they typed an address and stopped there.');
  }
  function quizBlock(q, cur) {
    q = q || {};
    if (!+q.takers) return beCard('Quiz', '', '<p class="sub" style="margin:6px 0 0">' + esc(q.note || 'No quiz taker recorded yet.') +
      ' The quiz starts sending its steps here once the tracked quiz page is live.</p>');
    var head = '<div class="kpis">' + beTile('Started', num(q.takers), '') + beTile('Finished', rateText(q.finish_rate), num(q.finished) + ' takers') +
      beTile('Revealed the card', rateText(q.reveal_rate), num(q.revealed) + ' takers') + beTile('Bought', rateText(q.buy_rate), num(q.bought) + ' takers') + '</div>';
    var qs = objects(q.questions).map(function (row) {
      var worst = q.worst_question && row.question === q.worst_question && +row.quit_before > 0;
      var ans = objects(row.answers);
      var maxA = Math.max.apply(null, ans.map(function (x) { return +x.n || 0; }).concat([0]));
      return '<div class="db-q"><div class="db-q-h"><b>' + esc(num(row.n) + '. ' + row.question) + '</b>' +
        '<span class="sub">' + esc(num(row.reached)) + ' reached' + (+row.quit_before ? ' \u00b7 <span class="quit' + (worst ? ' worst' : '') + '">' + esc(num(row.quit_before)) + ' quit before it</span>' : '') +
        (isNum(row.median_s) ? ' \u00b7 ' + esc(num(row.median_s)) + 's typical' : '') + '</span></div>' +
        (ans.length ? '<div class="db-ans">' + ans.map(function (x) {
          return '<span>' + esc(x.answer) + '</span><span class="bar-cell">' + barCell(x.n, maxA) + '</span><span class="n">' + esc(num(x.n)) + '</span>' +
            '<span class="n">' + (+x.bought ? esc(num(x.bought)) + ' bought \u00b7 ' + esc(rateText(x.rate)) : '\u2013') + '</span>';
        }).join('') + '</div>' : '') + '</div>';
    }).join('');
    var DEST = {listicle: 'Listicle', product: 'Product page', pdp: 'Product page'};
    var tail = '<div class="db-three" style="margin-top:16px"><div><div class="lab">Went on to</div>' + hbars(q.destinations, cur, function (r) { return DEST[r.key] || r.dest || r.key; }) + '</div>' +
      '<div><div class="lab">Country</div>' + hbars(q.countries, cur, function (r) { return country(r.key); }) + '</div>' +
      '<div><div class="lab">Device</div>' + hbars(q.devices, cur, function (r) { return ({iphone: 'iPhone', android: 'Android', desktop: 'Desktop'})[r.key] || r.key; }) + '</div></div>';
    var bots = +q.bots ? ' \u00b7 ' + plural(q.bots, 'bot', 'bots') + ' left out' : '';
    return beCard('Quiz', plural(q.takers, 'taker', 'takers') + bots, head + '<div style="margin-top:16px">' + qs + '</div>' + tail, q.note);
  }
  function recordsBlock(rows, cur) {
    rows = objects(rows);
    if (!rows.length) return beCard('Sales', '', '<p class="sub" style="margin:6px 0 0">No sale in the range.</p>');
    var total = rows.length;
    rows = rows.slice(0, dbShown.records);
    var DEV = {iphone: 'iPhone', android: 'Android', desktop: 'Desktop'}, APP = {facebook: 'Facebook app', instagram: 'Instagram app', browser: 'browser'};
    var LAND = {direct: 'Product page', listicle: 'Listicle', quiz: 'Quiz'};
    return beCard('Sales', 'newest first \u00b7 ' + plural(total, 'sale', 'sales') + (total >= 400 ? ' (the latest 400)' : ''), '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Order</th><th>When</th>' +
      '<th class="num">Amount</th><th>Where</th><th>On</th><th>Bought</th><th>Ad</th><th>Landed on</th></tr></thead><tbody>' + rows.map(function (r) {
        var ad = r.campaign ? esc(r.campaign) + ' \u203a ' + esc(r.adset || '-') + ' \u203a ' + esc(r.ad || '-') : '<span class="sub">not from an ad</span>';
        return '<tr>' + td('Order', '<b style="white-space:nowrap">' + esc(r.order_name) + '</b>' + (r.kind === 'mrr' ? '<span class="be-cell-sub">MRR</span>' : '')) +
          td('When', esc(fmtClock(r.at, true))) + td('Amount', esc(money(r.total, r.currency || cur)), 'num') +
          td('Where', esc(country(r.country)) + (r.city ? '<span class="be-cell-sub">' + esc(r.city) + '</span>' : '')) +
          td('On', esc((DEV[r.device] || r.device || '-') + (r.app && r.app !== 'browser' ? ' \u00b7 ' + (APP[r.app] || r.app) : ''))) +
          td('Bought', esc((isNum(r.qty) ? num(r.qty) + ' \u00d7 ' : '') + (r.product || '-'))) + td('Ad', ad) +
          td('Landed on', esc(LAND[r.landing] || (r.landing ? r.landing : '-')) + (r.quiz ? '<span class="be-cell-sub">started in the quiz</span>' : '')) + '</tr>';
      }).join('') + '</tbody></table></div>' + moreButton('dbMoreRecords', total, rows.length));
  }
  function renderDatabase(d) {
    S.db = d;
    var cur = d.currency || 'USD';
    var sel = document.getElementById('dbCountry');
    if (sel && !S.dbCountries) {                                 // the country list, once, from the first unfiltered reply
      var opts = objects(d.countries).map(function (c) { return '<option value="' + esc(c.key) + '">' + esc(country(c.key)) + '</option>'; }).join('');
      if (opts) { sel.innerHTML = '<option value="">Every country</option>' + opts; S.dbCountries = true; }
    }
    var psel = document.getElementById('dbProduct');
    if (psel && !S.dbProducts) {                                 // the products, like the P&L's switch, with their sales
      var popts = objects(d.products).map(function (p) { return '<option value="' + esc(p.key) + '">' + esc(p.key + ' (' + num(p.n) + ')') + '</option>'; }).join('');
      if (popts) { psel.innerHTML = '<option value="">Every product</option>' + popts; S.dbProducts = true; }
    }
    document.getElementById('dbBody').innerHTML = trendsBlock(d.trends) + dbTiles(d.tiles, cur) +
      '<div class="db-two">' + pieBlock('Who buys', d.who, cur, 'From the first name on the order: a woman\u2019s name means a partner buying for him.') +
      pieBlock('Muslim vs other buyers', d.faith, cur, 'An estimate from first and last names, totals only.') + '</div>' +
      dbCountries(d.countries, cur) +
      '<div class="db-three">' + beCard('Device', '', hbars(d.devices, cur)) + beCard('App', '', hbars(d.apps, cur)) + beCard('Landing page', '', hbars(d.landing, cur)) + '</div>' +
      '<div class="db-two">' + beCard('Bundles', '', hbars(d.bundles, cur, function (r) { return r.label && r.label !== r.key ? r.label : plural(r.key, 'bottle', 'bottles'); })) +
      beCard('Cities', 'top 12', hbars(d.cities, cur)) + '</div>' +
      heatBlock(d.heatmap) + abandonedBlock(d.abandoned, cur) + quizBlock(d.quiz, cur) + recordsBlock(d.records, cur) +
      (d.note ? '<p class="sub small">' + esc(d.note) + '</p>' : '');
  }
  var dbSeq = 0;
  function dbQuery() {
    var q = '?range=' + encodeURIComponent(S.dbRange);
    ['product', 'country', 'landing', 'kind'].forEach(function (k) {
      var v = document.getElementById('db' + k.charAt(0).toUpperCase() + k.slice(1)).value;
      if (v) q += '&' + k + '=' + encodeURIComponent(v);
    });
    return q;
  }
  function loadDatabase() {
    var seq = ++dbSeq, msg = document.getElementById('dbMsg');
    ['Sales', 'Abandoned', 'Quiz'].forEach(function (w) {
      document.getElementById('dbCsv' + w).setAttribute('href', '/hub/api/database/export?what=' + w.toLowerCase() + '&range=' + encodeURIComponent(S.dbRange));
    });
    return api('/hub/api/database' + dbQuery()).then(function (d) {
      if (seq !== dbSeq) return;
      msg.innerHTML = '';
      renderDatabase(d);
    }).catch(function (e) {
      if (seq !== dbSeq || (e && e.leaving)) return;
      msg.innerHTML = '<div class="errbox"><span>Couldn\'t load the database: ' + esc(sentence(e && e.message ? e.message : 'unknown error')) + '</span></div>';
    });
  }
  document.getElementById('dbRange').addEventListener('click', function (ev) {
    var b = ev.target instanceof Element ? ev.target.closest('button[data-range]') : null;
    if (!b || BE_RANGES.indexOf(b.getAttribute('data-range')) < 0) return;
    S.dbRange = b.getAttribute('data-range');
    document.querySelectorAll('#dbRange button').forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
    loadDatabase();
  });
  ['dbProduct', 'dbCountry', 'dbLanding', 'dbKind'].forEach(function (id) { document.getElementById(id).addEventListener('change', loadDatabase); });
  document.getElementById('dbBody').addEventListener('click', function (ev) {
    var b = ev.target instanceof Element ? ev.target.closest('button') : null;
    if (!b || !S.db) return;
    if (b.id === 'dbMoreCountries') { dbShown.countries += DB_STEP; renderDatabase(S.db); }
    if (b.id === 'dbMoreRecords') { dbShown.records += DB_STEP; renderDatabase(S.db); }
  });

  // --- the agent ---------------------------------------------------------------------
  // The chat lives on the server (append-only, so the model always reads its own turns back
  // exactly); this tab keeps the chat id and what it showed, for this browser session only.
  var AG = {chat: '', busy: false, turns: [], checked: false};
  try {
    var saved = JSON.parse(sessionStorage.getItem('core_agent') || 'null');
    if (saved && typeof saved.chat === 'string' && Array.isArray(saved.turns)) { AG.chat = saved.chat; AG.turns = saved.turns.slice(-60); }
  } catch (e) { /* storage blocked: start fresh */ }
  function agSave() {
    try { sessionStorage.setItem('core_agent', JSON.stringify({chat: AG.chat, turns: AG.turns.slice(-60)})); } catch (e) { /* ignore */ }
  }
  // A small, safe Markdown: everything is escaped first, then **bold**, `code`, headings,
  // lists and pipe tables are turned into markup.
  function agInline(t) {
    return esc(t).replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
  }
  function agMd(src) {
    var lines = String(src || '').replace(/\r/g, '').split('\n'), out = [], i = 0;
    var cells = function (l) { return l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(function (c) { return c.trim(); }); };
    var numeric = function (c) { return /^[-+]?[$]?[\d,.]+[%x]?$/.test(c.replace(/\s/g, '')); };
    while (i < lines.length) {
      var l = lines[i];
      if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
        var head = cells(l), rows = [];
        i += 2;
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(cells(lines[i])); i++; }
        var num = head.map(function (_, k) { return rows.length > 0 && rows.every(function (r) { return !r[k] || numeric(r[k]); }); });
        out.push('<div class="tbl-wrap"><table><thead><tr>' + head.map(function (h, k) {
          return '<th' + (num[k] ? ' class="n"' : '') + '>' + agInline(h) + '</th>'; }).join('') + '</tr></thead><tbody>' +
          rows.map(function (r) { return '<tr>' + head.map(function (_, k) {
            return '<td' + (num[k] ? ' class="n"' : '') + '>' + agInline(r[k] || '') + '</td>'; }).join('') + '</tr>'; }).join('') +
          '</tbody></table></div>');
        continue;
      }
      if (/^\s*[-*] /.test(l) || /^\s*\d+[.)] /.test(l)) {
        var ordered = /^\s*\d/.test(l), items = [];
        while (i < lines.length && (ordered ? /^\s*\d+[.)] /.test(lines[i]) : /^\s*[-*] /.test(lines[i]))) {
          items.push('<li>' + agInline(lines[i].replace(/^\s*(?:[-*]|\d+[.)]) /, '')) + '</li>'); i++;
        }
        out.push((ordered ? '<ol>' : '<ul>') + items.join('') + (ordered ? '</ol>' : '</ul>'));
        continue;
      }
      if (/^#{1,4} /.test(l)) { out.push('<h4>' + agInline(l.replace(/^#+ /, '')) + '</h4>'); i++; continue; }
      if (!l.trim()) { i++; continue; }
      var para = [];
      while (i < lines.length && lines[i].trim() && !/^\s*\|/.test(lines[i]) && !/^\s*[-*] /.test(lines[i]) &&
             !/^\s*\d+[.)] /.test(lines[i]) && !/^#{1,4} /.test(lines[i])) { para.push(agInline(lines[i])); i++; }
      out.push('<p>' + para.join('<br>') + '</p>');
    }
    return out.join('');
  }
  function agTurnHtml(t) {
    if (t.q !== undefined) return '<div class="ag-q">' + esc(t.q) + '</div>';
    if (t.err) return '<div class="ag-a"><div class="ag-err">' + esc(t.err) + '</div></div>';
    return '<div class="ag-a">' + agMd(t.a) +
      (t.checked && t.checked.length ? '<div class="ag-meta">Checked: ' + esc(t.checked.join(' \u00b7 ')) + '</div>' : '') + '</div>';
  }
  function agPaint(waiting) {
    var log = document.getElementById('agLog');
    log.innerHTML = AG.turns.map(agTurnHtml).join('') +
      (waiting ? '<div class="ag-wait"><span class="spin" aria-hidden="true"></span>Reading your data\u2026</div>' : '');
    document.getElementById('agStart').hidden = AG.turns.length > 0 || waiting;
    document.getElementById('agSend').disabled = AG.busy;
    if (waiting || AG.turns.length) window.scrollTo(0, document.body.scrollHeight);
  }
  function agFoot(st) {
    var f = document.getElementById('agFoot');
    if (!st) { f.textContent = ''; return; }
    if (!st.configured) { f.textContent = 'Not switched on yet: the tracker needs ANTHROPIC_API_KEY in Railway.'; return; }
    f.textContent = st.cap ? 'Today: $' + (+st.spent_today || 0).toFixed(2) + ' of the $' + (+st.cap).toFixed(2) + ' daily agent budget'
                           : 'Today: $' + (+st.spent_today || 0).toFixed(2) + ' on the agent';
  }
  function openAgent() {
    agPaint(AG.busy);
    if (!AG.checked) {
      AG.checked = true;
      api('/hub/api/agent/status').then(agFoot, function () { /* the first question says what's wrong */ });
    }
    setTimeout(function () { document.getElementById('agInput').focus(); }, 0);
  }
  function agAsk(question) {
    question = String(question || '').trim();
    if (!question || AG.busy) return;
    AG.busy = true;
    AG.turns.push({q: question});
    agSave();
    agPaint(true);
    api('/hub/api/agent', {chat_id: AG.chat, question: question}).then(function (r) {
      if (r.error) { AG.turns.push({err: r.error}); return; }
      AG.chat = r.chat_id || AG.chat;
      AG.turns.push({a: r.answer || '', checked: r.checked || []});
      agFoot({configured: true, spent_today: r.spent_today, cap: r.cap});
    }, function (e) {
      if (!e.leaving) AG.turns.push({err: e.message});
    }).then(function () {
      AG.busy = false;
      agSave();
      agPaint(false);
    });
  }
  var agInput = document.getElementById('agInput');
  function agGrow() { agInput.style.height = 'auto'; agInput.style.height = Math.min(agInput.scrollHeight, 180) + 'px'; }
  agInput.addEventListener('input', agGrow);
  agInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); document.getElementById('agForm').requestSubmit(); }
  });
  document.getElementById('agForm').addEventListener('submit', function (e) {
    e.preventDefault();
    var q = agInput.value;
    if (!q.trim() || AG.busy) return;
    agInput.value = '';
    agGrow();
    agAsk(q);
  });
  document.getElementById('agStart').addEventListener('click', function (e) {
    var b = e.target.closest('.ag-chip');
    if (b) agAsk(b.textContent);
  });
  document.getElementById('agNew').addEventListener('click', function () {
    if (AG.busy) return;
    AG.chat = '';
    AG.turns = [];
    agSave();
    agPaint(false);
    agInput.focus();
  });
  document.getElementById('askBar').addEventListener('submit', function (e) {
    e.preventDefault();
    var inp = document.getElementById('askInput'), q = inp.value.trim();
    if (!q) return;
    inp.value = '';
    showView('agent');
    agAsk(q);
  });

  window.addEventListener('hashchange', function () {
    var h = readHash();
    if (h.view !== S.view) showView(h.view);
    var rangeChanged = h.range !== S.range, groupChanged = h.group !== S.group, pnlChanged = h.pnl !== S.pnl;
    var funnelChanged = h.funnel !== S.funnel;
    S.range = h.range;
    S.group = h.group;
    S.funnel = h.funnel;
    S.pnl = h.pnl;
    paintControls();
    if (rangeChanged) loadMany(RANGED); else if (groupChanged) loadSection('creatives');
    if (funnelChanged && !rangeChanged && funnelShown()) paintFunnel();
    if (pnlChanged) loadSection('pnl');
  });

  var start = readHash();
  S.range = start.range;
  S.group = start.group;
  S.funnel = start.funnel;
  S.pnl = start.pnl;
  S.view = start.view;
  writeHash();
  if (S.view !== 'hub') showView(S.view);
  paintControls();
  loadAll();
})();
</script>
</body>
</html>
""".replace("<!--fonts-->", FONTS)
