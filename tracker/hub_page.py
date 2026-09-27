"""
The hub's two pages, served by app.py under /hub.

HUB_HTML is the whole dashboard: inline CSS and vanilla JS, no external
scripts, fonts or images, so nothing else has to be hosted or trusted. It only
talks to the /hub/api/* JSON endpoints, and every string that came from Meta,
Shopify or a URL goes through esc() before it touches the page.

LOGIN_HTML is the sign-in form. app.py swaps the <!--error--> placeholder for
a short message after a wrong token.
"""

LOGIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<meta name="color-scheme" content="dark">
<title>Core Tracking Hub</title>
<style>
:root{--bg:#0b0d12;--panel:#12151c;--raised:#171b24;--border:#232838;--text:#e8ebf2;--muted:#8a93a6;--accent:#6d8cff;--fail:#ef4444;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
.box{width:100%;max-width:380px;background:var(--panel);border:1px solid var(--border);border-radius:16px;padding:28px 24px}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.mark{width:12px;height:12px;border-radius:50%;background:var(--accent);box-shadow:0 0 14px rgba(109,140,255,.8)}
h1{font-size:20px;margin:0;letter-spacing:-.01em}
p{color:var(--muted);margin:0 0 20px;font-size:14px}
label{display:block;font-size:13px;color:var(--muted);margin-bottom:6px}
input{width:100%;background:var(--raised);border:1px solid var(--border);color:var(--text);border-radius:10px;padding:11px 12px;font:inherit;font-size:16px}
input:focus{outline:2px solid var(--accent);outline-offset:1px}
button{margin-top:14px;width:100%;background:var(--accent);color:#0b0d12;border:0;border-radius:10px;padding:11px;font:inherit;font-weight:700;cursor:pointer}
button:focus-visible{outline:2px solid #fff;outline-offset:2px}
.err{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.4);color:#fecaca;border-radius:10px;padding:9px 12px;margin-bottom:14px;font-size:14px}
.err:empty{display:none}
.hint{margin:16px 0 0;font-size:12.5px}
</style>
</head>
<body>
<main class="box">
  <div class="brand"><span class="mark" aria-hidden="true"></span><h1>Core Tracking Hub</h1></div>
  <p>Sign in to see your store's tracking, sales and ROAS.</p>
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
"""


HUB_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<meta name="color-scheme" content="dark">
<title>Core Tracking Hub</title>
<style>
:root{--bg:#0b0d12;--panel:#12151c;--raised:#171b24;--border:#232838;--line:#2e3548;--text:#e8ebf2;--muted:#8a93a6;--dim:#5d6578;--accent:#6d8cff;--ok:#22c55e;--warn:#f59e0b;--fail:#ef4444;color-scheme:dark}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
h1,h2,h3{margin:0;font-weight:700;letter-spacing:-.01em}
h2{font-size:16px}
h3{font-size:15px}
button{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.wrap{max-width:1200px;margin:0 auto;padding:0 16px}
.sub{color:var(--muted);font-size:13px}
.small{font-size:12px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}

/* header */
.top{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding-top:18px;padding-bottom:12px}
.brand{display:flex;align-items:center;gap:12px;min-width:0}
.mark{width:12px;height:12px;border-radius:50%;background:var(--accent);box-shadow:0 0 14px rgba(109,140,255,.8);flex:none}
.brand h1{font-size:20px}
#store{color:var(--muted);font-size:13px;overflow-wrap:anywhere}
.actions{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:13px}
.bar{position:sticky;top:0;z-index:20;background:rgba(11,13,18,.9);-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px);border-bottom:1px solid var(--border)}
.bar .wrap{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding-top:10px;padding-bottom:10px}
#rangeText{color:var(--muted);font-size:13px}
.seg{display:inline-flex;gap:2px;padding:3px;background:var(--panel);border:1px solid var(--border);border-radius:10px}
.seg button{background:none;border:0;color:var(--muted);padding:6px 12px;border-radius:7px;font-weight:600;cursor:pointer;white-space:nowrap}
.seg button:hover{color:var(--text)}
.seg button[aria-pressed="true"]{background:var(--raised);color:var(--text);box-shadow:inset 0 0 0 1px var(--line),inset 0 -2px 0 var(--accent)}
.btn{display:inline-flex;align-items:center;gap:6px;background:var(--raised);border:1px solid var(--border);color:var(--text);border-radius:9px;padding:7px 12px;font-weight:600;cursor:pointer;white-space:nowrap}
.btn:hover{border-color:var(--line);background:#1c2130}
.btn:disabled{opacity:.6;cursor:default}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#0b0d12}
.btn.sm{padding:4px 10px;font-size:12.5px;border-radius:8px}

/* sections */
.sec{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:18px;margin:16px 0}
.sec-h{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.sec-h .sub{margin-top:3px;max-width:680px}
.sec-body{transition:opacity .2s}
.busy .sec-body{opacity:.55}
.spin{display:none;width:14px;height:14px;border:2px solid var(--line);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite}
.busy .spin{display:inline-block}
@keyframes spin{to{transform:rotate(360deg)}}
.skel{height:14px;border-radius:6px;margin:10px 0;background:linear-gradient(90deg,var(--raised),#1f2432,var(--raised));background-size:200% 100%;animation:sh 1.3s linear infinite}
.skel.tall{height:56px}
@keyframes sh{to{background-position:-200% 0}}
.errbox{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;border:1px solid rgba(239,68,68,.4);background:rgba(239,68,68,.08);color:#fecaca;border-radius:10px;padding:10px 12px;margin-bottom:12px}
.note{background:var(--raised);border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin:12px 0}
.note.warn{border-color:rgba(245,158,11,.4);background:rgba(245,158,11,.07)}
.empty{color:var(--muted);padding:18px;text-align:center;border:1px dashed var(--border);border-radius:12px;margin-top:12px}
.err-t{color:#fca5a5}
.ok-t{color:#86efac}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}

/* status */
.status-top{display:flex;align-items:center;gap:22px;margin-bottom:16px}
.light{flex:none;width:64px;height:64px;border-radius:50%;background:radial-gradient(circle at 35% 30%,rgba(255,255,255,.55),rgba(255,255,255,0) 45%),var(--lc);box-shadow:0 0 0 6px var(--lr),0 0 38px 8px var(--lg);animation:glow 3s ease-in-out infinite}
.light.ok{--lc:var(--ok);--lr:rgba(34,197,94,.12);--lg:rgba(34,197,94,.45)}
.light.warn{--lc:var(--warn);--lr:rgba(245,158,11,.12);--lg:rgba(245,158,11,.45)}
.light.fail{--lc:var(--fail);--lr:rgba(239,68,68,.14);--lg:rgba(239,68,68,.55)}
@keyframes glow{50%{box-shadow:0 0 0 6px var(--lr),0 0 22px 3px var(--lg)}}
@media (prefers-reduced-motion:reduce){.light,.spin,.skel{animation:none}}
.status-text{min-width:0;overflow-wrap:anywhere}
.headline{font-size:26px;font-weight:750;letter-spacing:-.015em}
.reasons{margin:6px 0 0;padding-left:18px}
.reasons li{margin:2px 0}
.checks{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(250px,100%),1fr));gap:10px}
.check{display:flex;gap:10px;background:var(--raised);border:1px solid var(--border);border-radius:12px;padding:11px 12px;min-width:0}
.check.warn{border-color:rgba(245,158,11,.35)}
.check.fail{border-color:rgba(239,68,68,.5)}
.check-t{min-width:0;flex:1}
.check-n{display:flex;gap:8px;align-items:baseline}
.check .sub{overflow-wrap:anywhere;margin-top:2px}
.word{margin-left:auto;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;flex:none}
.word.ok{color:var(--ok)}.word.warn{color:var(--warn)}.word.fail{color:var(--fail)}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--muted);flex:none}
.check .dot{margin-top:5px}
.dot.ok{background:var(--ok);box-shadow:0 0 8px rgba(34,197,94,.6)}
.dot.warn{background:var(--warn);box-shadow:0 0 8px rgba(245,158,11,.6)}
.dot.fail{background:var(--fail);box-shadow:0 0 8px rgba(239,68,68,.7)}
.dot.mut{background:var(--dim)}
.wd{margin-top:16px}
.wd-h{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:13px;margin-bottom:8px}
.legend{display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap}
.legend .dot{box-shadow:none;margin-left:6px}
.strip{position:relative;height:30px;background:var(--raised);border:1px solid var(--border);border-radius:8px}
.tick{position:absolute;top:6px;bottom:6px;width:3px;margin-left:-1.5px;border-radius:2px;background:var(--ok);cursor:pointer}
.tick::before{content:"";position:absolute;inset:-6px -3px}
.tick.warn{background:var(--warn)}
.tick.fail{background:var(--fail)}
.tick:hover{box-shadow:0 0 0 2px var(--raised),0 0 0 3px var(--text)}
.strip-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:12.5px}
.strip-axis{display:flex;justify-content:space-between;color:var(--dim);font-size:11.5px;margin-top:4px}
details.mini{margin-top:12px;border-top:1px solid var(--border);padding-top:10px}
details.mini>summary{cursor:pointer;color:var(--muted);font-weight:600;font-size:13px}
details.mini>summary:hover{color:var(--text)}
.hist{list-style:none;margin:10px 0 0;padding:0}
.hist li{display:flex;gap:10px;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px}
.hist li .dot{margin-top:5px}
.run{margin-top:12px;background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:12px}
.run-h{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:10px}
#runMsg:empty{display:none}
#runMsg{margin-top:10px}

/* cards */
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.card{display:flex;flex-direction:column;min-width:0;background:var(--raised);border:1px solid var(--border);border-radius:12px;padding:14px}
.card.hero{grid-column:span 2;border-color:rgba(109,140,255,.45);background:linear-gradient(180deg,rgba(109,140,255,.12),rgba(109,140,255,0) 70%),var(--raised)}
.c-label{color:var(--muted);font-size:13px;font-weight:600}
.c-value{font-size:26px;font-weight:700;margin-top:4px;letter-spacing:-.01em;font-variant-numeric:normal;overflow-wrap:anywhere}
.hero .c-value{font-size:54px;line-height:1.05}
.c-sub{color:var(--muted);font-size:12.5px;margin-top:3px}
.card.dim .c-value{color:#b7bdcb}
.connect{font-size:18px;color:var(--muted);font-weight:650}
.hero .connect{font-size:24px}
.foot{margin-top:12px}
.sp{position:relative;height:36px;margin-top:auto;color:var(--accent)}
.card .sp{margin-top:14px}
.sp svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.sp-a{fill:currentColor;opacity:.1}
.sp-l{fill:none;stroke:currentColor;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.sp .end{position:absolute;width:8px;height:8px;border-radius:50%;background:currentColor;box-shadow:0 0 0 2px var(--raised);transform:translate(-50%,-50%);pointer-events:none}
.sp .pt{position:absolute;width:5px;height:5px;border-radius:50%;background:currentColor;transform:translate(-50%,-50%);pointer-events:none}
.sp .hit{position:absolute;top:-6px;bottom:-6px}
.sp .hit:hover::after{content:"";position:absolute;top:6px;bottom:6px;left:var(--c);border-left:1px solid rgba(232,235,242,.4)}

/* creatives */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(170px,100%),1fr));gap:10px;margin-bottom:6px}
.stat{background:var(--raised);border:1px solid var(--border);border-radius:12px;padding:11px 12px;min-width:0}
.stat .v{font-size:21px;font-weight:700;margin-top:2px;font-variant-numeric:normal}
.stat.hero{border-color:rgba(109,140,255,.45)}
.stat.hero .v{color:#c9d4ff}
.stat.good .v{color:#86efac}
.stat.dim .v{color:#b7bdcb}
.camp{margin-top:12px;background:var(--raised);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.camp>summary{cursor:pointer;padding:12px 14px;display:flex;flex-wrap:wrap;gap:4px 14px;align-items:baseline;list-style:none}
.camp>summary::-webkit-details-marker{display:none}
.camp>summary::before{content:"\25B8";color:var(--muted);transition:transform .15s}
.camp[open]>summary::before{transform:rotate(90deg)}
.camp-n{font-weight:700;overflow-wrap:anywhere}
.camp-s{color:var(--muted);font-size:12.5px}
.tbl-wrap{overflow-x:auto}
.tbl{width:100%;border-collapse:collapse}
.tbl th{color:var(--muted);font-weight:600;font-size:12px;text-align:left;padding:8px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
.tbl td{padding:9px 10px;border-bottom:1px solid var(--border);vertical-align:top}
.tbl td .v{min-width:0;overflow-wrap:anywhere}
.tbl tbody:last-child tr:last-child td{border-bottom:0}
.tbl .num{text-align:right;white-space:nowrap}
.ads{table-layout:fixed;min-width:800px}
.ads th:nth-child(2),.ads th:nth-child(6){width:100px}
.ads th:nth-child(3){width:124px}
.ads th:nth-child(4){width:90px}
.ads th:nth-child(5){width:76px}
.ads th:nth-child(7){width:150px}
.msplit{display:block;white-space:normal;color:var(--muted);font-size:11.5px;margin-top:2px}
.tag-view{display:inline-block;margin-top:4px;font-size:11px;font-weight:650;color:#fcd34d;background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.35);border-radius:999px;padding:0 7px;white-space:nowrap;cursor:help}
.assist{color:var(--muted)}
.assist[data-tip]{border-bottom:1px dotted var(--muted);cursor:help}
.tbl th.num{text-align:right}
.ads tr.grp td{background:rgba(109,140,255,.06);border-top:1px solid var(--line)}
.ads tr.sold td{background:rgba(34,197,94,.07)}
.ads tr.sold td:first-child{box-shadow:inset 3px 0 0 var(--ok)}
.ads tr.msold td:first-child{box-shadow:inset 3px 0 0 rgba(109,140,255,.6)}
.ad-name{font-weight:600;overflow-wrap:anywhere}
.ords{color:var(--muted);font-size:12px;margin-top:3px;overflow-wrap:anywhere}
.roas-s{display:inline-block;font-weight:700;color:#c9d4ff;background:rgba(109,140,255,.14);border-radius:6px;padding:0 6px}
.roas-m{display:block;color:var(--muted);font-size:11.5px;margin-top:2px}
.setup{border:1px solid rgba(109,140,255,.4);background:rgba(109,140,255,.07);border-radius:12px;padding:14px 16px;margin-bottom:14px}
.setup ol{margin:10px 0;padding-left:20px}
.setup li{margin:6px 0}
.copyrow{display:flex;gap:8px;align-items:flex-start;margin:8px 0}
.code{flex:1;min-width:0;display:block;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:9px 10px;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;word-break:break-all;-webkit-user-select:all;user-select:all}
.urlbox p{margin:8px 0}

/* funnel */
.funnel{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.fgrp{background:var(--raised);border:1px solid var(--border);border-radius:12px;padding:14px;min-width:0}
.f-h{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px}
.f-h .sub{margin-left:auto}
.sw{width:10px;height:10px;border-radius:3px;flex:none}
.sw.meta,.f-bar.meta{background:var(--accent)}
.sw.other,.f-bar.other{background:#8a93a6}
.f-row{display:grid;grid-template-columns:104px minmax(0,1fr) 64px;gap:2px 10px;align-items:center;margin:10px 0}
.f-lab{font-size:13px}
.f-track{height:18px;border-radius:0 4px 4px 0}
.f-bar{height:100%;border-radius:0 4px 4px 0;min-width:0}
.f-num{text-align:right;font-weight:650}
.f-conv{grid-column:2/4;color:var(--muted);font-size:12px}

/* match quality */
.q-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr));gap:12px}
.qcard{background:var(--raised);border:1px solid var(--border);border-radius:12px;padding:14px;min-width:0}
.q-h{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.q-score{display:flex;align-items:center;gap:14px;margin:12px 0 4px}
.score{font-size:48px;font-weight:750;line-height:1;font-variant-numeric:normal;letter-spacing:-.02em}
.score .of{font-size:16px;color:var(--muted);font-weight:600;margin-left:2px}
.score.ok,.q-word.ok{color:var(--ok)}
.score.warn,.q-word.warn{color:var(--warn)}
.score.fail,.q-word.fail{color:var(--fail)}
.score.none,.q-word.none{color:var(--muted)}
.q-word{font-weight:700}
.q-sec{margin-top:12px}
.q-sec>.sub{margin-bottom:6px}
.chips{display:flex;flex-wrap:wrap;gap:4px}
.chip{display:inline-block;font-size:11.5px;padding:2px 7px;border-radius:6px;border:1px solid var(--border);color:var(--dim);white-space:nowrap}
.chip.on{color:var(--text);border-color:rgba(109,140,255,.45);background:rgba(109,140,255,.12)}
.meter-row{display:grid;grid-template-columns:84px minmax(0,1fr) 42px;gap:10px;align-items:center;margin:7px 0;font-size:13px}
.meter{height:8px;border-radius:4px;background:#232a3b;overflow:hidden}
.meter i{display:block;height:100%;background:var(--accent);border-radius:0 4px 4px 0}
.meter-row .num{text-align:right}

/* orders */
.badge{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:650;padding:2px 8px;border-radius:999px;border:1px solid;white-space:nowrap}
.b-new{color:#86efac;background:rgba(34,197,94,.12);border-color:rgba(34,197,94,.35)}
.b-rebill,.b-ad{color:#b4c3ff;background:rgba(109,140,255,.12);border-color:rgba(109,140,255,.35)}
.b-skip{color:var(--muted);background:rgba(138,147,166,.08);border-color:rgba(138,147,166,.3);white-space:normal}
.b-warn{color:#fcd34d;background:rgba(245,158,11,.12);border-color:rgba(245,158,11,.4)}
.orders .items{color:var(--muted);font-size:12.5px;margin-top:2px;max-width:280px;overflow-wrap:anywhere}
.orders .when{color:var(--muted);font-size:12.5px;white-space:nowrap}
.px{display:block;white-space:nowrap;font-size:12.5px}
.px .i{display:inline-block;width:14px;font-weight:700}
.px.yes .i{color:var(--ok)}
.px.no .i{color:var(--fail)}
.px.wait .i{color:var(--warn)}
.px.na{color:var(--muted)}
.adpath{font-size:12.5px;margin-top:4px;max-width:240px;overflow-wrap:anywhere}
.adpath .gt{color:var(--dim)}
.adpath.where{color:var(--muted);margin-top:2px}
.helped{font-weight:600}
.helped[data-tip]{border-bottom:1px dotted var(--dim);cursor:help}
.orders .chips{max-width:210px}
.st{display:inline-flex;align-items:center;gap:6px;font-weight:650;white-space:nowrap}
.st .dot{box-shadow:none}
.resend{margin-top:6px}
.rs{font-size:12px;margin-top:4px;max-width:240px}
.rs:empty{display:none}
.err-small{font-size:12px;margin-top:3px;max-width:260px;overflow-wrap:anywhere}

/* tools */
.tool h3{margin-bottom:4px}
.form{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-top:12px}
.form label{display:flex;flex-direction:column;gap:5px;font-size:12.5px;color:var(--muted);min-width:0}
.form input,.form select{background:var(--raised);border:1px solid var(--border);color:var(--text);border-radius:9px;padding:8px 10px;font:inherit;min-width:0;max-width:100%}
.form select{min-width:220px}
.out{margin-top:10px}
.out:empty{display:none}

#tip{position:fixed;z-index:50;left:0;top:0;pointer-events:none;background:#1e2330;border:1px solid var(--line);color:var(--text);font-size:12px;padding:6px 8px;border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.45);max-width:260px;opacity:0;transition:opacity .08s}
#tip.on{opacity:1}
footer{color:var(--dim);font-size:12px;text-align:center;padding:10px 16px 30px}

@media (max-width:980px){
  .cards{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media (max-width:720px){
  .sec{padding:14px;margin:12px 0}
  .top{padding-top:14px}
  .bar .seg{display:flex;width:100%}
  .bar .seg button{flex:1;padding:7px 4px}
  .status-top{gap:16px;align-items:flex-start}
  .light{width:48px;height:48px;margin-top:4px}
  .headline{font-size:22px}
  .hero .c-value{font-size:44px}
  .funnel{grid-template-columns:1fr}
  .f-row{grid-template-columns:92px minmax(0,1fr) 56px}
  .form input,.form select{font-size:16px}
  .form label,.form .btn{width:100%}
  .form select{min-width:0}
  .tbl-wrap{overflow:visible}
  .tbl thead{display:none}
  .tbl,.tbl tbody,.tbl tr,.tbl td{display:block;width:100%}
  .tbl tr{background:var(--raised);border:1px solid var(--border);border-radius:12px;padding:6px 12px;margin:0 0 10px}
  .tbl td{display:grid;grid-template-columns:86px minmax(0,1fr);gap:10px;padding:7px 0;border-bottom:1px solid var(--border);text-align:left}
  .tbl tr td:last-child{border-bottom:0}
  .tbl td::before{content:attr(data-label);color:var(--muted);font-size:12px;padding-top:1px}
  .tbl .num{text-align:left;white-space:normal}
  .camp .tbl{padding:0 10px}
  .ads{min-width:0}
  .ads tr.grp td,.ads tr.sold td{background:none}
  .ads tr.grp{background:rgba(109,140,255,.08);border-color:var(--line)}
  .ads tr.sold{border-color:rgba(34,197,94,.45)}
  .ads tr.sold td:first-child,.ads tr.msold td:first-child{box-shadow:none}
  .orders .items,.adpath,.orders .chips,.rs,.err-small{max-width:none}
}
@media (max-width:380px){
  .cards{grid-template-columns:1fr}
  .card.hero{grid-column:auto}
}
</style>
</head>
<body>
<header class="wrap top">
  <div class="brand">
    <span class="mark" aria-hidden="true"></span>
    <div><h1>Core Tracking Hub</h1><div id="store">Loading your store&hellip;</div></div>
  </div>
  <div class="actions">
    <span id="updated" aria-live="polite"></span>
    <button class="btn sm" type="button" id="refreshBtn">Refresh</button>
    <a href="/hub/logout">Log out</a>
  </div>
</header>

<nav class="bar" aria-label="Date range">
  <div class="wrap">
    <div class="seg" role="group" aria-label="Date range">
      <button type="button" data-range="today" aria-pressed="true">Today</button>
      <button type="button" data-range="yesterday" aria-pressed="false">Yesterday</button>
      <button type="button" data-range="7d" aria-pressed="false">7 days</button>
      <button type="button" data-range="30d" aria-pressed="false">30 days</button>
    </div>
    <span id="rangeText"></span>
  </div>
</nav>

<noscript><div class="wrap"><div class="note warn">The hub needs JavaScript turned on.</div></div></noscript>

<main class="wrap">
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

  <section class="sec" id="sec-cards" aria-labelledby="h-cards">
    <div class="sec-h">
      <div><h2 id="h-cards">Sales and ROAS</h2><div class="sub">True ROAS counts new sales from Shopify only. Subscription rebills are left out because no ad drove them.</div></div>
      <span class="spin" aria-hidden="true"></span>
    </div>
    <div class="sec-msg"></div>
    <div class="sec-body"></div>
  </section>

  <section class="sec" id="sec-creatives" aria-labelledby="h-creatives">
    <div class="sec-h">
      <div><h2 id="h-creatives">Creatives that sold</h2><div class="sub">Spend and sales per ad. Store-confirmed sales are real Shopify orders the tracker tied to the last ad the buyer clicked.</div></div>
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

  <section class="sec" id="sec-funnel" aria-labelledby="h-funnel">
    <div class="sec-h">
      <div><h2 id="h-funnel">Shopper funnel</h2><div class="sub">Browsers from Meta ads next to everyone else, and how many move on at each step.</div></div>
      <span class="spin" aria-hidden="true"></span>
    </div>
    <div class="sec-msg"></div>
    <div class="sec-body"></div>
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
<footer>Refreshes every minute while this tab is open.</footer>
<div id="tip" role="tooltip"></div>

<script>
(function () {
  'use strict';

  var RANGE_KEYS = ['today', 'yesterday', '7d', '30d'];
  var RANGE_LABEL = {today: 'Today', yesterday: 'Yesterday', '7d': 'Last 7 days', '30d': 'Last 30 days'};
  var RANGE_WORDS = {today: 'today', yesterday: 'yesterday', '7d': 'in the last 7 days', '30d': 'in the last 30 days'};
  var HEAD = {ok: 'All good', warn: 'Needs a look', fail: 'Something is broken'};
  var WORD = {ok: 'OK', warn: 'Look', fail: 'Broken'};
  var RANK = {ok: 0, warn: 1, fail: 2};
  var REFRESH_MS = 60000;
  // One API call feeds these page sections.
  var SECTIONS = {overview: ['status', 'cards', 'quality'], orders: ['orders'], creatives: ['creatives'], funnel: ['funnel']};
  var S = {range: 'today', group: 'adset', tz: '', seq: {}, lastLoad: 0, timer: null, ov: null,
           leaving: false, resent: new Map(), closed: new Set(), wd: null, wdAt: 0, wdP: null};

  function $(sel) { return document.querySelector(sel); }
  function secEl(id) { return document.getElementById('sec-' + id); }
  function secBody(id) { return secEl(id).querySelector('.sec-body'); }

  // Everything from Meta, Shopify or a URL passes through esc() before innerHTML.
  var ESC = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
  function esc(v) { return v == null ? '' : String(v).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }
  function lvl(s) { return s === 'ok' || s === 'warn' || s === 'fail' ? s : 'warn'; }
  function isNum(v) { return v !== null && v !== undefined && v !== '' && typeof v !== 'boolean' && isFinite(v); }

  // --- formatting ---------------------------------------------------------
  var moneyFmt = {};
  function money(v, cur) {
    if (!isNum(v)) return '-';
    var code = String(cur || 'USD').toUpperCase();
    if (!moneyFmt[code]) {
      try { moneyFmt[code] = new Intl.NumberFormat('en-US', {style: 'currency', currency: code}); }
      catch (e) { moneyFmt[code] = new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'}); }
    }
    return moneyFmt[code].format(+v);
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
  function keyFor(name) { return name === 'creatives' ? S.range + '|' + S.group : S.range; }

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
    var retry = '<button class="btn sm" type="button" data-retry="' + esc(name) + '">Try again</button>';
    if (key !== null && s.dataset.key === key) {
      s.querySelector('.sec-msg').innerHTML = '<div class="errbox"><span>Couldn\'t refresh: ' + esc(msg) +
        ' Showing the last numbers that loaded.</span>' + retry + '</div>';
    } else {
      s.dataset.key = '';
      secBody(id).innerHTML = '<div class="errbox"><span>Couldn\'t load this: ' + esc(msg) + '</span>' + retry + '</div>';
    }
  }

  var URLS = {
    overview: function () { return '/hub/api/overview?range=' + S.range; },
    orders: function () { return '/hub/api/orders?range=' + S.range + '&limit=100'; },
    creatives: function () { return '/hub/api/creatives?range=' + S.range + '&group=' + S.group; },
    funnel: function () { return '/hub/api/funnel?range=' + S.range; }
  };
  var RENDER = {status: renderStatus, cards: renderCards, quality: renderQuality,
                orders: renderOrders, creatives: renderCreatives, funnel: renderFunnel};
  // A crash on the server comes back as HTTP 200 holding only an `error`. A real
  // answer always has its section's main field, even when it carries an error note.
  var MAIN_KEY = {overview: 'status', orders: 'orders', creatives: 'campaigns', funnel: 'steps'};

  function loadSection(name) {
    if (!SECTIONS[name]) return Promise.resolve();
    var my = S.seq[name] = (S.seq[name] || 0) + 1;
    var key = keyFor(name);
    SECTIONS[name].forEach(function (id) { markLoading(id, key); });
    return api(URLS[name]()).then(function (data) {
      if (data.error && !(MAIN_KEY[name] in data)) throw new Error(String(data.error));
      return data;
    }).then(function (data) {
      if (my !== S.seq[name]) return;               // a newer request for this section won
      if (name === 'overview') {
        S.ov = data;
        try { renderHeader(data); fillPixelSelect(data.quality || []); } catch (e) { console.error(e); }
      }
      SECTIONS[name].forEach(function (id) {
        try { RENDER[id](data); markDone(id, key); }
        catch (e) { console.error(e); markError(id, null, 'part of this could not be shown.', name); }
      });
    }, function (e) {
      if (my !== S.seq[name] || e.leaving) return;
      SECTIONS[name].forEach(function (id) { markError(id, key, e.message, name); });
    });
  }

  function loadAll() {
    clearTimeout(S.timer);
    S.timer = null;
    S.lastLoad = Date.now();
    var btn = $('#refreshBtn');
    btn.disabled = true;
    var done = function () { btn.disabled = false; schedule(); };
    return Promise.all(Object.keys(SECTIONS).map(loadSection)).then(done, done);
  }

  // One timer at most, and only while the tab is visible.
  function schedule() {
    clearTimeout(S.timer);
    S.timer = null;
    if (S.leaving || document.visibilityState !== 'visible') return;
    S.timer = setTimeout(function () { S.timer = null; loadAll(); },
                         Math.max(1000, REFRESH_MS - (Date.now() - S.lastLoad)));
  }

  // --- range, grouping and the URL hash -----------------------------------
  function readHash() {
    var p = new URLSearchParams(location.hash.replace(/^#/, ''));
    var r = p.get('range');
    return {range: RANGE_KEYS.indexOf(r) >= 0 ? r : 'today', group: p.get('group') === 'batch' ? 'batch' : 'adset'};
  }
  function writeHash() {
    var h = '#range=' + S.range + '&group=' + S.group;
    if (location.hash === h) return;
    try { history.replaceState(null, '', h); } catch (e) { location.hash = h; }
  }
  function rangeText() {
    var r = S.ov && S.ov.range;
    if (r && r.key === S.range && r.since) {
      return (r.label || RANGE_LABEL[S.range]) + ': ' + dayLabel(r.since) +
        (r.until && r.until !== r.since ? ' to ' + dayLabel(r.until) : '');
    }
    return RANGE_LABEL[S.range];
  }
  function paintControls() {
    document.querySelectorAll('[data-range]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.range === S.range)); });
    document.querySelectorAll('[data-group]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.group === S.group)); });
    $('#rangeText').textContent = rangeText();
  }
  function setRange(r) {
    if (RANGE_KEYS.indexOf(r) < 0 || r === S.range) return;
    S.range = r;
    writeHash();
    paintControls();
    loadAll();
  }
  function setGroup(g) {
    if ((g !== 'adset' && g !== 'batch') || g === S.group) return;
    S.group = g;
    writeHash();
    paintControls();
    loadSection('creatives');
  }

  // --- header ---------------------------------------------------------------
  function renderHeader(ov) {
    var st = ov.store || {};
    S.tz = st.timezone || S.tz;
    var dom = String(st.domain || '');
    var shown = dom.replace(/^https?:\/\//, '').replace(/\/+$/, '');
    var link = /^https?:\/\//.test(dom)
      ? '<a href="' + esc(dom) + '" target="_blank" rel="noopener noreferrer">' + esc(shown) + '</a>' : esc(shown);
    $('#store').innerHTML = esc(st.name || 'Your store') + (shown ? ' &middot; ' + link : '');
    $('#updated').textContent = 'Updated ' + fmtClock(ov.generated_at || Date.now());
    $('#rangeText').textContent = rangeText();
  }

  // --- sparkline (inline SVG) ---------------------------------------------
  // The SVG stretches to the card; the stroke stays 2px and the end dot is an
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

  // --- status -----------------------------------------------------------------
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
      : '<span class="badge b-new">Live</span> Sending your store\'s events to Meta for real.';
    var nowMs = toMs(ov.generated_at) || Date.now();
    secBody('status').innerHTML =
      '<div class="status-top"><div class="light ' + L + '" role="img" aria-label="' + HEAD[L] + '"></div><div class="status-text">' +
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

  // --- cards --------------------------------------------------------------------
  function card(o) {
    return '<div class="card' + (o.cls ? ' ' + o.cls : '') + '"><div class="c-label">' + esc(o.label) + '</div>' +
      '<div class="c-value">' + o.value + '</div>' + (o.sub ? '<div class="c-sub">' + o.sub + '</div>' : '') +
      (o.spark || '') + '</div>';
  }

  function renderCards(ov) {
    var c = ov.cards || {}, s = ov.series || {}, cur = c.currency || 'USD';
    var days = s.days || [];
    var col = function (k) { return days.map(function (_, i) { return (s[k] || [])[i]; }); };
    var nr = col('new_revenue'), rr = col('rebill_revenue'), ns = col('new_sales'), rb = col('rebills'), sp = col('spend');
    var ads = !!c.ads_connected;
    var d = function (i) { return dayLabel(days[i]); };
    var tips = function (fn) { return days.map(function (_, i) { return d(i) + ': ' + fn(i); }); };
    var newSales = c.new_sales || {}, rebills = c.rebills || {};
    // Set up but not readable just now (a Meta outage or rate limit): say so and
    // show "-", instead of asking the owner to connect what is already connected.
    var failed = !ads && !!c.ads_configured;
    var connect = '<span class="connect">Connect ad spend</span>';
    var errText = c.ads_error
      ? '<span title="' + esc(c.ads_error) + '">' + esc(trunc(c.ads_error, 120)) + '</span>' : '';
    var why = failed ? 'Couldn\'t read ad spend from Meta just now. ' + errText
      : errText || '<a href="#sec-creatives">How to connect</a>';
    var adv = function (text) { return ads || failed ? esc(text) : connect; };

    var html = [
      card({label: 'New sales', value: esc(num(newSales.count)), sub: esc(money(newSales.revenue, cur)) + ' revenue',
            spark: spark(nr, tips(function (i) { return plural(ns[i] || 0, 'sale', 'sales') + ', ' + money(nr[i] || 0, cur); }))}),
      card({label: 'Rebills', value: esc(num(rebills.count)), sub: esc(money(rebills.revenue, cur)) + ', not counted in ROAS',
            spark: spark(rr, tips(function (i) { return plural(rb[i] || 0, 'rebill', 'rebills') + ', ' + money(rr[i] || 0, cur); }))}),
      card({label: 'Average order', value: esc(money(c.aov, cur)), sub: 'New sales only',
            spark: spark(days.map(function (_, i) { return ratio(nr[i], ns[i]); }),
                         tips(function (i) { return ratio(nr[i], ns[i]) === null ? 'no sales' : money(ratio(nr[i], ns[i]), cur); }))}),
      card({label: 'Ad spend', value: adv(money(c.spend, cur)), sub: ads ? 'Meta ads, all connected accounts' : why,
            spark: ads ? spark(sp, tips(function (i) { return money(sp[i], cur); })) : ''}),
      card({label: 'True ROAS', cls: 'hero', value: adv(roas(c.true_roas)),
            sub: ads ? 'New-sale revenue divided by ad spend. Rebills left out.' : failed ? why : 'Needs ad spend. ' + why,
            spark: ads ? spark(days.map(function (_, i) { return ratio(nr[i], sp[i]); }),
                               tips(function (i) { return ratio(nr[i], sp[i]) === null ? 'no spend' : roas(ratio(nr[i], sp[i])); })) : ''}),
      card({label: 'Cost per new sale', value: adv(money(c.cost_per_sale, cur)), sub: ads ? 'Ad spend divided by new sales' : '',
            spark: ads ? spark(days.map(function (_, i) { return ratio(sp[i], ns[i]); }),
                               tips(function (i) { return ratio(sp[i], ns[i]) === null ? 'no sales' : money(ratio(sp[i], ns[i]), cur); })) : ''}),
      card({label: 'Meta-reported ROAS', cls: 'dim', value: adv(roas(c.meta_roas)),
            sub: ads ? 'What Ads Manager claims, for comparison. ' + esc(plural(c.meta_purchases || 0, 'purchase', 'purchases')) + ' reported.' : ''})
    ].join('');
    // Shopify couldn't be read: the sales numbers arrive empty and show "-", and this says why.
    var note = ov.error ? '<div class="note warn">' + esc(ov.error) + ' Sales show a dash until Shopify answers again.</div>' : '';
    var foot = isNum(c.total_revenue)
      ? 'All revenue ' + esc(RANGE_WORDS[S.range]) + ': ' + esc(money(c.total_revenue, cur)) + ' from ' +
        esc(plural(c.orders || 0, 'order', 'orders')) + ', new sales and rebills together. Charts show the last 7 days.'
      : 'Charts show the last 7 days.';
    secBody('cards').innerHTML = note + '<div class="cards">' + html + '</div>' + '<p class="sub foot">' + foot + '</p>';
  }

  // --- creatives ------------------------------------------------------------------
  function td(label, html, cls) {
    return '<td data-label="' + esc(label) + '"' + (cls ? ' class="' + cls + '"' : '') + '><div class="v">' + html + '</div></td>';
  }
  function stat(label, value, sub, cls) {
    return '<div class="stat' + (cls ? ' ' + cls : '') + '"><div class="sub">' + esc(label) + '</div><div class="v">' + esc(value) + '</div>' +
      (sub ? '<div class="sub small">' + esc(sub) + '</div>' : '') + '</div>';
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
    var orders = a.orders || [];
    var who = '<div class="ad-name">' + esc(name) + '</div>' +
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
  function groupRows(g, cur, on) {
    return '<tbody><tr class="grp">' +
      td(S.group === 'batch' ? 'Batch' : 'Ad set', '<b>' + esc(g.name || 'Unnamed') + '</b>') +
      td('Spend', esc(on ? money(g.spend, cur) : '-'), 'num') +
      td('Meta sales', metaSales(g, on, false), 'num') +
      td('Store sales', esc(num(g.store_sales)), 'num') +
      td('Assists', assistCell(g.assists, g.assist_orders), 'num') +
      td('Revenue', esc(money(g.store_revenue, cur)), 'num') +
      td('ROAS', roasCell(g.roas_store, g.roas_meta, on), 'num') + '</tr>' +
      (g.ads || []).map(function (a) { return adRow(a, cur, on); }).join('') + '</tbody>';
  }

  function renderCreatives(d) {
    var cur = d.currency || 'USD', t = d.totals || {}, on = !!d.connected;
    // Set up but not readable just now: say so, instead of the setup steps.
    var failed = !on && !!d.configured;
    var h = on ? (d.error ? '<div class="note warn">' + esc(d.error) + '</div>' : '')
      : failed ? '<div class="note warn">Couldn\'t read ad spend from Meta just now, so spend and ROAS show a dash. ' +
          esc(d.error) + '</div>'
      : setupCard(d.error);
    h += '<div class="stats">' +
      stat('Ad spend', on ? money(t.spend, cur) : failed ? '-' : 'Not connected', '') +
      stat('Meta-reported sales', on ? num(t.meta_purchases) : '-',
           on ? money(t.meta_value, cur) + ' in Ads Manager' + (hasSplit(t) ? ' ' + splitText(t) : '') : '') +
      stat('Store-confirmed from Meta', num(t.store_sales), money(t.store_revenue, cur) + ' in real orders', 'good') +
      stat('True ROAS', roas(t.true_roas), 'All new sales divided by spend', 'hero') +
      stat('Meta ROAS', roas(t.meta_roas), 'What Ads Manager reports', 'dim') + '</div>';

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
      h += '<details class="camp" data-camp="' + esc(key) + '"' + (S.closed.has(key) ? '' : ' open') + '>' +
        '<summary><span class="camp-n">' + esc(c.campaign_name || 'Unknown campaign') + '</span>' +
        '<span class="camp-s">' + esc(summary) + '</span></summary>' +
        '<div class="tbl-wrap"><table class="tbl ads">' + head +
        (c.groups || []).map(function (g) { return groupRows(g, cur, on); }).join('') + '</table></div></details>';
    });
    if (camps.length) {
      h += '<p class="sub foot">Assists are sales where the buyer clicked this ad earlier, before the ad that got the sale. ' +
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

  // --- funnel -----------------------------------------------------------------------
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
        var conv = i === 0 ? '' : v[i] === null ? 'not available right now'
          : v[i - 1] ? pct(v[i] / v[i - 1] * 100) + ' of the step before' : 'nothing to compare yet';
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
    secBody('funnel').innerHTML = note + '<div class="funnel">' + panels + '</div>' + (f.note ? '<p class="sub foot">' + esc(f.note) + '</p>' : '');
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
          '<span class="badge ' + (p.role === 'main' ? 'b-rebill' : 'b-skip') + '">' + (p.role === 'main' ? 'Main' : 'Backup') + '</span>' +
          '<span class="sub mono">' + esc(p.pixel_id) + '</span></div>' +
        '<div class="q-score"><div class="score ' + L + '">' + (sc === null ? '-' : esc(sc.toFixed(1))) + '<span class="of">/10</span></div>' +
          '<div><div class="q-word ' + L + '">' + word + '</div><div class="sub">' + esc(ev) + ' events' +
          (e.taken_at ? ', scored ' + esc(agoText(e.taken_at)) : '') + '</div></div></div>' +
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

  function typeBadge(o) {
    var cls = o.type === 'new_sale' ? 'b-new' : o.type === 'rebill' ? 'b-rebill' : 'b-skip';
    var label = o.type_label || (o.type === 'new_sale' ? 'New sale' : o.type === 'rebill' ? 'Rebill' : 'Skipped');
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
  function adCell(ad) {
    if (!ad) return '<span class="sub">No ad</span>';
    var how = {browser: 'Seen by the storefront pixel', landing_page: 'From the page the buyer landed on',
               click_id: 'Meta click ID only'}[ad.source] || '';
    var badge = '<span class="badge b-ad"' + (how ? ' title="' + esc(how) + '"' : '') + '>' + (ad.click ? 'Ad click' : 'Meta ad') + '</span>';
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
      '<div class="adpath">Sold by ' + (ad.ad_name ? '<b>' + esc(ad.ad_name) + '</b>' : 'an ad without a name') +
        (names.length ? ' \u00b7 assisted by ' + names.join(', ') : '') + '</div>' +
      (where.length ? '<div class="adpath where">' + where.map(esc).join(' <span class="gt">\u203a</span> ') + '</div>' : '');
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
      '<th>Sent to</th><th>Ad</th><th>Details sent</th><th>Status</th></tr></thead><tbody>' +
      list.map(function (o) {
        var who = '<b>' + esc(o.name || o.id) + '</b> <span class="when">' + esc(o.time_local) + '</span>' +
          (o.items ? '<div class="items">' + esc(o.items) + '</div>' : '');
        return '<tr>' + td('Order', who) + td('Total', esc(money(o.total, o.currency)), 'num') + td('Type', typeBadge(o)) +
          td('Sent to', pixelCell(o)) + td('Ad', adCell(o.ad)) + td('Details', detailCell(o.details)) +
          td('Status', statusCell(o)) + '</tr>';
      }).join('') + '</tbody></table></div>';
    secBody('orders').innerHTML = h;
  }

  function resendMsg(r) {
    if (!r || r.error) return '<span class="err-t">Couldn\'t resend: ' + esc((r && r.error) || 'no answer') + '</span>';
    // The server says why in plain words (for example "rebills are switched off").
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
    if ((el = t.closest('[data-group]'))) return setGroup(el.dataset.group);
    if ((el = t.closest('[data-retry]'))) return loadSection(el.dataset.retry);
    if ((el = t.closest('[data-run]'))) return showRun(el.dataset.run);
    if ((el = t.closest('[data-resend]'))) return resend(el);
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

  window.addEventListener('hashchange', function () {
    var h = readHash();
    var rangeChanged = h.range !== S.range, groupChanged = h.group !== S.group;
    S.range = h.range;
    S.group = h.group;
    paintControls();
    if (rangeChanged) loadAll(); else if (groupChanged) loadSection('creatives');
  });

  var start = readHash();
  S.range = start.range;
  S.group = start.group;
  writeHash();
  paintControls();
  loadAll();
})();
</script>
</body>
</html>
"""
