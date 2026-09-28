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
<title>Core Tracking Hub</title>
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
  <div class="brand"><span class="mark" aria-hidden="true">C</span><h1>Core Tracking Hub</h1></div>
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
<title>Core Tracking Hub</title>
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

/* header */
.top{display:flex;align-items:center;justify-content:space-between;gap:14px 20px;flex-wrap:wrap;padding-top:22px;padding-bottom:22px}
.brand{display:flex;align-items:center;gap:12px;min-width:0}
.mark{flex:none;width:28px;height:28px;border-radius:8px;background:var(--text);color:#000;display:grid;place-items:center;font-weight:700;font-size:15px;letter-spacing:-.02em}
.brand h1{font-size:15px;font-weight:600;letter-spacing:-.01em}
#store{color:var(--dim);font-size:12.5px;overflow-wrap:anywhere}
#store a{color:var(--dim)}
.actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.apptabs{display:inline-flex;gap:2px;padding:3px;border:1px solid var(--line-2);border-radius:10px;background:var(--raised);margin-right:auto;margin-left:8px}
.apptab{height:30px;padding:0 16px;border:0;border-radius:7px;background:transparent;color:var(--muted);font:inherit;font-size:13px;font-weight:600;cursor:pointer}
.apptab:hover{color:var(--text)}
.apptab.on{background:var(--text);color:#000}
.pnl-app{padding:0 24px 24px}
.pnl-app iframe{display:block;width:100%;height:calc(100vh - 120px);min-height:560px;border:1px solid var(--line);border-radius:12px;background:#000}
#updated{color:var(--dim);font-size:12.5px;margin:0 6px}
.pill{display:inline-flex;align-items:center;gap:8px;height:30px;padding:0 12px 0 11px;border:1px solid var(--line-2);border-radius:999px;background:var(--raised);font-size:12.5px;font-weight:500;cursor:pointer;white-space:nowrap;transition:background .15s,border-color .15s}
.pill:hover{background:var(--card);border-color:var(--line-3)}

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

/* funnel */
.funnel{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.fgrp,.lst{background:var(--raised);border:1px solid var(--line);border-radius:12px;padding:20px;min-width:0}
.lst{margin-top:12px}
.f-h{display:flex;align-items:baseline;gap:8px 10px;flex-wrap:wrap;margin-bottom:6px}
.f-h b{font-weight:600}
.f-h .sub{margin-left:auto}
.sw{width:8px;height:8px;border-radius:2px;flex:none;align-self:center}
.sw.meta,.f-bar.meta{background:var(--text)}
.sw.other,.f-bar.other{background:#595959}
.f-row{display:grid;grid-template-columns:112px minmax(0,1fr) 64px;gap:2px 14px;align-items:center;margin:14px 0}
.f-lab{font-size:13px;color:var(--muted)}
.f-track{height:8px;border-radius:4px;background:var(--card);overflow:hidden}
.f-bar{height:100%;border-radius:4px;min-width:0}
.f-num{text-align:right;font-weight:600;font-size:15px;letter-spacing:-.01em}
.f-conv{grid-column:2/4;color:var(--dim);font-size:12px}
.since{margin:0 0 12px}
.untied{margin:14px 0 0;color:var(--dim)}
.lst .tbl{margin-top:8px}
.lst .sub.small{margin:12px 0 0;color:var(--dim)}

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
.ads tr.grp td{background:#0d0d0d;border-top:1px solid var(--line-2)}
.ads tr.grp b{font-weight:600}
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
.as-head,.as-row{display:grid;grid-template-columns:minmax(0,1.15fr) 112px minmax(0,1.6fr) 88px;column-gap:24px;padding-left:20px;padding-right:20px}
.as-head{padding-top:12px;padding-bottom:12px;border-bottom:1px solid var(--line)}
.as-head span{font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
.as-head span:nth-child(3){padding-left:25px}
.as-head .r,.as-spend,.as-count{text-align:right}
.as-row{padding-top:18px;padding-bottom:18px;transition:background .15s}
.as-row+.as-row{border-top:1px solid var(--line)}
.as-row:hover{background:#0d0d0d}
.as-ad{min-width:0}
.as-where,.as-set{color:var(--dim);font-size:12px;line-height:18px;min-height:18px;overflow-wrap:anywhere}
.as-name,.as-main,.as-cl,.as-n{line-height:24px;margin-top:2px}
.as-name{font-size:16px;font-weight:600;letter-spacing:-.01em;overflow-wrap:anywhere}
.as-main{font-size:15px;font-weight:500;white-space:nowrap}
.as-cls{min-width:0;border-left:1px solid var(--line);padding-left:24px}
.as-cls ul{list-style:none;margin:0;padding:0;display:grid;gap:12px}
.as-cl{overflow-wrap:anywhere}
.as-cn{font-size:15px;font-weight:600}
.as-v{color:var(--muted);white-space:nowrap}
.as-n{font-size:30px;font-weight:600;letter-spacing:-.035em}
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
  .top{padding-top:16px;padding-bottom:16px}
  .sec{margin-top:36px;padding-top:26px;scroll-margin-top:132px}
  .bar{margin-top:36px}
  h2{font-size:18px}
  .sec-h{align-items:flex-start}
  .sec-h>.row{width:100%;flex-wrap:nowrap}
  .sec-h>.row>.seg{flex:1;min-width:0}
  .actions{width:100%;flex-wrap:nowrap}
  #updated{margin:0 auto 0 2px;white-space:nowrap}
  .upd-w{display:none}
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
  .funnel{grid-template-columns:1fr}
  .fgrp,.lst{padding:16px}
  .f-row{grid-template-columns:96px minmax(0,1fr) 56px}
  .camp>summary{display:grid;grid-template-columns:auto minmax(0,1fr);column-gap:10px;row-gap:4px}
  .camp-s{grid-column:2}
  .as-list{background:none;border:0;border-radius:0;overflow:visible}
  .as-head{display:none}
  .as-row{grid-template-columns:minmax(0,1fr) minmax(0,1fr);grid-template-areas:"ad ad" "spend count" "cls cls";row-gap:14px;padding:16px;background:var(--raised);border:1px solid var(--line);border-radius:12px;margin-bottom:10px}
  .as-row+.as-row{border-top:1px solid var(--line)}
  .as-ad{grid-area:ad}
  .as-spend{grid-area:spend;text-align:left}
  .as-count{grid-area:count;text-align:left}
  .as-ad .blank,.as-cls .blank{display:none}
  .as-cls{grid-area:cls;border-left:0;padding-left:0;border-top:1px solid var(--line);padding-top:12px}
  .as-n{font-size:26px}
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
  <div class="brand">
    <span class="mark" aria-hidden="true">C</span>
    <div><h1>Core Tracking Hub</h1><div id="store">Loading your store&hellip;</div></div>
  </div>
  <nav class="apptabs" aria-label="Views">
    <button type="button" class="apptab on" id="tabHub" aria-pressed="true">Tracking</button>
    <button type="button" class="apptab" id="tabPnl" aria-pressed="false">P&amp;L</button>
  </nav>
  <div class="actions">
    <button class="pill" type="button" id="statusPill" aria-label="Tracking status. Go to tracking health."><span class="dot mut" aria-hidden="true"></span>Checking&hellip;</button>
    <span id="updated" aria-live="polite"></span>
    <button class="btn sm" type="button" id="refreshBtn">Refresh</button>
    <a class="btn sm" href="/hub/logout">Log out</a>
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
      <div><h2 id="h-funnel">Shopper funnel</h2><div class="sub">Shoppers from Meta ads next to everyone else, and how far each one got.</div></div>
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

  <section class="sec" id="sec-assists" aria-labelledby="h-assists">
    <div class="sec-h">
      <div><h2 id="h-assists">Assists</h2><div class="sub">Ads buyers clicked before the ad that got the sale, and the videos that closed those sales.</div></div>
      <span class="spin" aria-hidden="true"></span>
    </div>
    <div class="sec-msg"></div>
    <div class="sec-body"></div>
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
<section class="pnl-app" id="pnlApp" hidden aria-label="P&amp;L">
  <iframe id="pnlFrame" title="Your P&amp;L" referrerpolicy="no-referrer"></iframe>
</section>
<footer id="hubFooter">Refreshes every minute while this tab is open.</footer>
<div id="tip" role="tooltip"></div>

<template id="pnlTpl">
  <!-- One compact row. The P&L's own code (below) still fills every id it writes to;
       the ones not shown here sit in the hidden block so its numbers stay 1:1. -->
  <div class="money-strip">
    <div class="m-tile"><div class="m-k">Revenue</div><div class="m-v" id="heroRev">$0</div><div class="m-s">new sales and MRR</div></div>
    <div class="m-tile"><div class="m-k lab" id="expLab">Expenses</div><div class="m-v" id="heroExp">$0</div><div class="m-s">COGS, ads, fees, all</div></div>
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
    const netFinal = net + ship - cb - feeTrue - bills - sw.total;
    renderHero({ net: netFinal, rev: rev + ship, cogs, ads, fees: shopifyFees + feeTrue, provisional });
    renderExpenses({ net: netFinal, rev: rev + ship, cogs, ads, fees: shopifyFees + feeTrue, feeTrue, cb, bills, sw: sw.total });   // hub
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
  // chargebacks + Shopify bills + software, from the same numbers recalc() just used.
  function renderExpenses(v){
    animateTo($('heroExp'), v.rev - v.net, fmtShort);
    const parts = [['COGS', v.cogs], ['ad spend', v.ads], ['fees', v.fees]]
      .concat([['Shopify bills', v.bills], ['software', v.sw], ['chargebacks', v.cb]].filter(p => Math.abs(p[1]) >= 0.005));
    $('heroExpParts').textContent = 'Expenses = ' + parts.map(p => `${p[0]} ${fmtShort(p[1])}`).join(' + ') +
      (Math.abs(v.feeTrue) >= 0.005 ? ` (fees include a ${fmtShort(v.feeTrue)} true-up to Shopify's actual)` : '');
    const lab = document.getElementById('expLab');
    if (lab) lab.setAttribute('data-tip', $('heroExpParts').textContent);   // the breakdown, on hover
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
  var S = {range: 'today', group: 'adset', pnl: 'today', pnlUrl: '', tz: '', seq: {}, lastLoad: 0, timer: null,
           ov: null, rng: null, leaving: false, resent: new Map(), closed: new Set(), props: [], decided: new Map(),
           running: new Map(), wd: null, wdAt: 0, wdP: null};

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
  function loadAll() { return loadMany(Object.keys(SECTIONS)); }

  // One timer at most, and only while the tab is visible.
  function schedule() {
    clearTimeout(S.timer);
    S.timer = null;
    if (S.leaving || document.visibilityState !== 'visible') return;
    S.timer = setTimeout(function () { S.timer = null; loadAll(); },
                         Math.max(1000, REFRESH_MS - (Date.now() - S.lastLoad)));
  }

  // --- ranges, grouping and the URL hash ----------------------------------
  function readHash() {
    var p = new URLSearchParams(location.hash.replace(/^#/, ''));
    var r = p.get('range'), q = p.get('pnl');
    return {range: RANGE_KEYS.indexOf(r) >= 0 ? r : 'today', group: p.get('group') === 'batch' ? 'batch' : 'adset',
            pnl: PNL_PRESETS.indexOf(q) >= 0 ? q : 'today', view: p.get('view') === 'pnl' ? 'pnl' : 'hub'};
  }
  function writeHash() {
    var h = '#range=' + S.range + '&group=' + S.group + '&pnl=' + S.pnl + (S.view === 'pnl' ? '&view=pnl' : '');
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
  function setPnl(p) {
    if (PNL_PRESETS.indexOf(p) < 0 || p === S.pnl) return;
    S.pnl = p;
    writeHash();
    paintControls();
    loadSection('pnl');
  }

  // --- header ---------------------------------------------------------------
  function renderHeader(ov) {
    var st = ov.store || {};
    S.tz = st.timezone || S.tz;
    var dom = String(st.domain || '');
    var shown = dom.replace(/^https?:\/\//, '').replace(/\/+$/, '');
    var link = webUrl(dom)
      ? '<a href="' + esc(dom) + '" target="_blank" rel="noopener noreferrer">' + esc(shown) + '</a>' : esc(shown);
    $('#store').innerHTML = esc(st.name || 'Your store') + (shown ? ' &middot; ' + link : '');
    $('#updated').innerHTML = '<span class="upd-w">Updated </span>' + esc(fmtClock(ov.generated_at || Date.now()));
    var L = lvl((ov.status || {}).level);
    var pill = $('#statusPill');
    pill.innerHTML = '<span class="dot ' + L + '" aria-hidden="true"></span>' + PILL[L];
    pill.setAttribute('aria-label', 'Tracking: ' + PILL[L] + '. Go to tracking health.');
  }
  // The first overview failed: the pill says the check couldn't run instead of "Checking" until a refresh works.
  function headerDown() {
    var pill = $('#statusPill');
    pill.innerHTML = '<span class="dot fail" aria-hidden="true"></span>Couldn\'t check';
    pill.setAttribute('aria-label', 'Tracking: couldn\'t check. Go to tracking health.');
    $('#store').textContent = 'Your store';
  }

  // --- the P&L section (its numbers come from the copied P&L code above) ------
  function renderPnl(d) {
    if (webUrl(d.pnl_url)) S.pnlUrl = d.pnl_url;
    if (!document.getElementById('heroNet')) secBody('pnl').innerHTML = $('#pnlTpl').innerHTML;
    PNL.show(d);
    var notes = [];
    if (d.manual_error) notes.push(d.manual_error);
    if (d.sw_tools_source === 'copy') {
      notes.push('Software costs use a saved copy of your P&L\'s tool list, because the P&L page couldn\'t be read just now.');
    }
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
  // Shoppers from Meta ads: through the listicle vs straight to the product page.
  function listicleBlock(L) {
    var rows = objects(L && L.rows);
    if (!rows.length) return '';
    return '<div class="lst"><div class="f-h"><b>Listicle vs straight to product page</b><span class="sub">Shoppers from Meta ads</span></div>' +
      '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Landed on</th><th class="num">Visitors</th><th class="num">Sales</th>' +
      '<th class="num">Revenue</th><th class="num">Conversion rate</th></tr></thead><tbody>' +
      rows.map(function (r) {
        var label = r.label || (r.key === 'listicle' ? 'Through the listicle' : 'Straight to product page');
        return '<tr>' + td('Landed on', '<b>' + esc(label) + '</b>') + td('Visitors', esc(num(r.visitors)), 'num') +
          td('Sales', esc(num(r.sales)), 'num') + td('Revenue', esc(money(r.revenue)), 'num') +
          td('Conversion', esc(convText(r.conversion)), 'num') + '</tr>';
      }).join('') + '</tbody></table></div>' + (L.note ? '<p class="sub small">' + esc(L.note) + '</p>' : '') + '</div>';
  }

  function renderFunnel(f) {
    var steps = f.steps && f.steps.length ? f.steps : ['Visitors', 'Product views', 'Add to cart', 'Checkout', 'Purchases'];
    var groups = [['Meta ads', 'meta', f.meta || []], ['Everyone else', 'other', f.other || []]];
    var panels = groups.map(function (g) {
      var title = g[0], cls = g[1];
      // null is unknown (Purchases while Shopify can't be read): shown as "-", never as 0.
      var v = steps.map(function (_, i) { return isNum(g[2][i]) ? Math.max(0, +g[2][i]) : null; });
      var top = Math.max.apply(null, [0].concat(v.filter(function (x) { return x !== null; })));
      var rows = steps.map(function (step, i) {
        var w = top && v[i] !== null ? Math.max(v[i] ? 1 : 0, v[i] / top * 100) : 0;
        // Each browser counts at the furthest step it reached, so each step is a share of the one above.
        var conv = i === 0 ? '' : v[i] === null ? 'not available right now'
          : v[i - 1] ? pct(v[i] / v[i - 1] * 100) + ' of the step above' : 'no one reached the step above';
        return '<div class="f-row"><div class="f-lab">' + esc(step) + '</div>' +
          '<div class="f-track" data-tip="' + esc(title + ', ' + step + ': ' + num(v[i]) + (conv ? ' (' + conv + ')' : '')) + '">' +
          '<div class="f-bar ' + cls + '" style="width:' + w.toFixed(2) + '%"></div></div>' +
          '<div class="f-num">' + esc(num(v[i])) + '</div>' + (i ? '<div class="f-conv">' + esc(conv) + '</div>' : '') + '</div>';
      }).join('');
      // Store conversion is usually under 1%, so counts say more than a rounded percent.
      var bought = v[v.length - 1];
      var overall = !v[0] ? 'No visitors yet' : bought === null ? plural(v[0], 'visitor', 'visitors')
        : num(bought) + ' of ' + num(v[0]) + ' visitors bought';
      return '<div class="fgrp"><div class="f-h"><span class="sw ' + cls + '" aria-hidden="true"></span><b>' + esc(title) + '</b>' +
        '<span class="sub">' + esc(overall) + '</span></div>' + rows + '</div>';
    }).join('');
    var note = f.error ? '<div class="note warn">' + esc(f.error) + ' Purchases show a dash until Shopify answers again.</div>' : '';
    // The range starts before the pixel's first shopper on record: earlier visits weren't counted.
    var since = f.counting_since ? '<p class="sub small since">Counting since ' + esc(f.counting_since) +
      ', when the pixel recorded its first shopper.</p>' : '';
    var untied = isNum(f.untied_sales) && +f.untied_sales > 0
      ? '<p class="sub untied">' + esc(plural(f.untied_sales, 'more sale', 'more sales')) + ' we could not tie to a browser.</p>' : '';
    secBody('funnel').innerHTML = note + since + '<div class="funnel">' + panels + '</div>' + untied + listicleBlock(f.listicle) +
      (f.note ? '<p class="sub small foot">' + esc(f.note) + '</p>' : '');
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
    return (isNum(store) ? '<span class="roas-s">' + esc(roas(store)) + '</span>' : '<span class="sub">-</span>') +
      '<span class="roas-m">Meta ' + esc(roas(meta)) + '</span>';
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
    return (+x.meta_purchases || 0) > 0 && isNum(x.meta_click_purchases) && isNum(x.meta_view_purchases);
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
  function assistCell(n, orders) {
    n = +n || 0;
    if (!n || !orders || !orders.length) return '<span class="assist">' + esc(num(n)) + '</span>';
    var tip = 'Assisted ' + plural(n, 'sale', 'sales') + ': ' + listShort(orders, 12);
    return '<span class="assist" data-tip="' + esc(tip) + '">' + esc(num(n)) + '<span class="sr"> ' + esc(tip) + '</span></span>';
  }
  function adRow(a, cur, on) {
    var sold = (+a.store_sales || 0) > 0, metaSold = (+a.meta_purchases || 0) > 0;
    var name = a.ad_name || (a.ad_id ? 'Ad ' + a.ad_id : 'Unnamed ad');
    var bits = [];
    if (S.group === 'batch' && a.adset_name) bits.push('Ad set: ' + a.adset_name);
    if (isNum(a.impressions) && +a.impressions) bits.push(num(a.impressions) + ' impressions');
    if (isNum(a.clicks) && +a.clicks) bits.push(num(a.clicks) + ' clicks');
    if (isNum(a.meta_add_to_carts) && +a.meta_add_to_carts) bits.push(plural(a.meta_add_to_carts, 'add to cart', 'add to carts'));
    var orders = a.orders || [];
    // Store sales whose ad click came through a listicle.
    var via = +a.via_listicle || 0;
    var who = '<div class="ad-name">' + esc(name) + (via ? '<span class="tag">' + esc(num(via)) + ' via listicle</span>' : '') + '</div>' +
      (bits.length ? '<div class="sub small">' + esc(bits.join(' \u00b7 ')) + '</div>' : '') +
      (orders.length ? '<div class="ords">Orders: ' + esc(listShort(orders, 8)) + '</div>' : '');
    return '<tr class="' + (sold ? 'sold' : metaSold ? 'msold' : '') + '">' +
      td('Creative', who) +
      td('Spend', esc(on ? money(a.spend, cur) : '-'), 'num') +
      td('Meta sales', metaSales(a, on, true), 'num') +
      td('Store sales', sold ? '<b>' + esc(num(a.store_sales)) + '</b>' : esc(num(a.store_sales)), 'num') +
      td('Assists', assistCell(a.assists, a.assist_orders), 'num') +
      td('Revenue', esc(money(a.store_revenue, cur)), 'num') +
      td('ROAS', roasCell(a.roas_store, a.roas_meta, on), 'num') + '</tr>';
  }
  // The ads under the spend threshold with no sale and no add to cart, summed
  // in one line so the totals still add up.
  function smallLine(s, cur, on, min) {
    if (!s || !(+s.count > 0) || !isNum(min)) return '';
    return esc('+' + plural(s.count, 'other ad', 'other ads') + ' under ' + money(min, cur, Number.isInteger(+min)) +
      ' with no sales or add to carts' + (on ? ': ' + money(s.spend, cur) + ' spend' : ''));
  }
  function groupRows(g, cur, on, min) {
    var more = smallLine(g.small, cur, on, min);
    return '<tbody><tr class="grp">' +
      td(S.group === 'batch' ? 'Batch' : 'Ad set', '<b>' + esc(g.name || 'Unnamed') + '</b>') +
      td('Spend', esc(on ? money(g.spend, cur) : '-'), 'num') +
      td('Meta sales', metaSales(g, on, false), 'num') +
      td('Store sales', esc(num(g.store_sales)), 'num') +
      td('Assists', assistCell(g.assists, g.assist_orders), 'num') +
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
      h += '<p class="sub small foot">' + (min !== null ? 'An ad gets a row when it spent ' + esc(money(min, cur, Number.isInteger(min))) +
        ' or more ' + esc(RANGE_WORDS[S.range]) + ', or had a sale or an add to cart; the rest are summed in the grey lines. ' : '') +
        'Assists are sales where the buyer clicked this ad earlier, before the ad that got the sale. ' +
        'Ad set, batch and campaign totals count each of these sales once, however many of their ads the buyer clicked. ' +
        'They are never added to sales or revenue. Meta sales in brackets: click means bought within 7 days of clicking ' +
        'the ad, view means bought within 1 day of only seeing it.</p>';
    }

    var u = d.unlabelled || {};
    if ((+u.store_sales || 0) > 0) {
      h += '<div class="note warn">Sales from Meta ad clicks without ad names: <b>' + esc(num(u.store_sales)) + '</b> (' +
        esc(money(u.store_revenue, cur)) + '). Add URL tracking to your ads to see which creative.' +
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
  // One row per ad that assisted a sale: the ad, its spend, the videos that got
  // those sales, and how many it assisted. Every name came from Meta or an ad link: esc() each one.
  function adName(a) { return a.ad_name || (a.ad_id ? 'Ad ' + a.ad_id : 'Unnamed ad'); }

  function asRow(r, cur) {
    var where = [r.adset_name, r.campaign_name].filter(Boolean).join(' \u00b7 ');
    // Every column has a small line on top (ad set, or blank) so the big lines sit level.
    var small = function (cls, text) {
      return text ? '<div class="' + cls + '">' + esc(text) + '</div>' : '<div class="' + cls + ' blank">&nbsp;</div>';
    };
    var closers = objects(r.closers).map(function (c) {
      var n = +c.sales || 0;
      // The value of the sales it closed that this row's ad assisted.
      return '<li>' + small('as-set', c.adset_name) +
        '<div class="as-cl"><span class="as-cn">' + esc(adName(c)) + '</span><span class="as-v">' +
        esc(' - ' + money(c.value, cur) + (n > 1 ? ' x' + n : '')) + '</span></div></li>';
    }).join('');
    return '<div class="as-row">' +
      '<div class="as-ad">' + small('as-where', where) + '<div class="as-name">' + esc(adName(r)) + '</div></div>' +
      '<div class="as-spend"><div class="as-where"><span class="m-lab">Spend</span></div>' +
        '<div class="as-main">' + esc(isNum(r.spend) ? money(r.spend, cur) : '-') + '</div></div>' +
      '<div class="as-cls"><div class="m-lab">Videos that got the sale</div><ul>' + closers + '</ul></div>' +
      '<div class="as-count"><div class="as-where"><span class="m-lab">Assists</span></div>' +
        '<div class="as-n">' + esc(num(r.assists)) + '</div></div></div>';
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
        '<span>Videos that got the sale</span><span class="r">Assists</span></div>' +
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
    return '<span class="badge ' + cls + '">' + esc(label) + '</span>';
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
  function adCell(ad, listicle) {
    var how = HOW[ad.source] || '';
    // Credited from the buyer's first landing page only: no later click was seen.
    var first = ad.source === 'first_visit' || ad.source === 'first_visit_unverified';
    var badge = '<span class="badge b-ad"' + (how ? ' title="' + esc(how) + '"' : '') + '>' + (ad.click ? 'Ad click' : 'Meta ad') + '</span>' +
      (listicle ? ' <span class="badge b-lst" title="The ad click came through the listicle">Listicle</span>' : '');
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
    if (o.ad) return adCell(o.ad, !!o.listicle);
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
  $('#statusPill').addEventListener('click', function () {
    secEl('status').scrollIntoView({behavior: 'smooth', block: 'start'});
  });

  document.addEventListener('click', function (ev) {
    var t = ev.target instanceof Element ? ev.target : ev.target.parentElement;
    if (!t) return;
    var el;
    if ((el = t.closest('[data-range]'))) return setRange(el.dataset.range);
    if ((el = t.closest('[data-preset]'))) return setPnl(el.dataset.preset);
    if ((el = t.closest('[data-group]'))) return setGroup(el.dataset.group);
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

  // --- the two views: Tracking (this page) and the P&L app ---------------
  function showView(view) {
    S.view = view === 'pnl' ? 'pnl' : 'hub';
    var pnl = S.view === 'pnl';
    document.querySelector('main.wrap').hidden = pnl;
    document.getElementById('hubFooter').hidden = pnl;
    document.getElementById('pnlApp').hidden = !pnl;
    [['tabHub', !pnl], ['tabPnl', pnl]].forEach(function (t) {
      var b = document.getElementById(t[0]);
      b.classList.toggle('on', t[1]);
      b.setAttribute('aria-pressed', t[1] ? 'true' : 'false');
    });
    if (pnl) openPnl(0);
    writeHash();
  }
  // The P&L's address comes with the first P&L numbers (the server's PNL_URL); the app loads once
  // and then stays open, so switching back and forth keeps its place.
  function openPnl(tries) {
    var f = document.getElementById('pnlFrame');
    if (f.getAttribute('src')) return;
    if (webUrl(S.pnlUrl)) { f.setAttribute('src', S.pnlUrl); return; }
    if (tries < 60) setTimeout(function () { openPnl(tries + 1); }, 250);
  }
  document.getElementById('tabHub').addEventListener('click', function () { showView('hub'); window.scrollTo(0, 0); });
  document.getElementById('tabPnl').addEventListener('click', function () { showView('pnl'); window.scrollTo(0, 0); });

  window.addEventListener('hashchange', function () {
    var h = readHash();
    if (h.view !== S.view) showView(h.view);
    var rangeChanged = h.range !== S.range, groupChanged = h.group !== S.group, pnlChanged = h.pnl !== S.pnl;
    S.range = h.range;
    S.group = h.group;
    S.pnl = h.pnl;
    paintControls();
    if (rangeChanged) loadMany(RANGED); else if (groupChanged) loadSection('creatives');
    if (pnlChanged) loadSection('pnl');
  });

  var start = readHash();
  S.range = start.range;
  S.group = start.group;
  S.pnl = start.pnl;
  S.view = start.view;
  writeHash();
  if (S.view === 'pnl') showView('pnl');
  paintControls();
  loadAll();
})();
</script>
</body>
</html>
""".replace("<!--fonts-->", FONTS)
