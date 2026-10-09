"""
The Database tab: who buys, from where, on what, and what the quiz learns.

Records: every sale of the last months (the backend's orders, with the ad
the tracker credited), every abandoned checkout, every quiz taker. The
analyzer turns them into charts: who the buyer is (the man himself or a
partner buying for him), countries and cities, device and app, hour and
weekday, bundle sizes, abandoned checkouts by step, Meta's own age and
gender split, plus an estimate of Muslim vs other buyers from first and
last names (totals only; no single order is ever labelled on the page).

The quiz (why-still-one-line.netlify.app) posts each step to /quiz:
which question was shown, what was answered, how long it took, where the
taker went at the end. Nothing personal: no name, email or address is
stored; the taker's country comes from their browser's time zone.
"""
import csv
import datetime as dt
import io
import json
import logging
import re
import time
from typing import Any, Optional

import attribution
import config
import db

log = logging.getLogger("database")

# --- who the buyer is, from a first name --------------------------------------------------

FEMALE = set("""
aaliyah abby abigail ada adele adriana aisha alexa alexandra alexis alice alicia alison allison alyssa amanda
amber amelia amina amira amy ana anastasia andrea angela angelina anita ann anna anne annie april ariana ashley
aubrey audrey autumn ava ayesha barbara beatrice becky bella beth bethany beverly bianca bonnie brenda brianna
bridget brittany brooke caitlin camila candice carla carmen carol carolina caroline carrie cassandra catherine
cecilia chantal charlene charlotte chelsea cheryl chloe christina christine cindy claire clara claudia colleen
courtney crystal cynthia daisy dana danielle daniela dawn deborah debbie denise diana diane dina donna dora doris
dorothy eleanor elena elise eliza elizabeth ella ellen ellie elsa emily emma erica erin esther eva evelyn faith
farah fatima fiona florence frances gabriela gail gemma georgia gina gloria grace hailey haley hana hannah
harriet heather heidi helen helena holly huda ingrid irene iris isabel isabella isabelle ivy jackie jacqueline
jade jamie jane janet janice jasmine jean jenna jennifer jenny jessica jill jo joan joanna joanne jodie josephine
joy joyce judith judy julia julie juliet june kaitlyn karen kate katherine kathleen kathryn kathy katie katrina
kayla kelly kelsey kerry khadija kim kimberly kristen kristin kristina krystal laila lara laura lauren layla leah
leila lena leslie liliana lily linda lindsay lisa lola lorraine louise lucy lydia lynn mackenzie madeline madison
maggie mandy margaret maria mariam marie marilyn marina marion marwa mary maryam maya megan melanie melissa mia
michelle miriam molly monica morgan nadia nancy naomi natalie natasha nicole nina noor nora norah olivia paige
pamela patricia paula pauline penny phoebe priya rachel rania rebecca regina renee rhonda rita roberta robin rosa
rose rosemary ruby ruth sabrina sadia sally salma samantha samira sandra sara sarah sasha selena shannon sharon
sheila shelley sherry shirley sofia sonia sophia sophie stacey stacy stephanie sue susan suzanne sylvia tamara
tanya tara teresa tessa theresa tiffany tina tracey tracy valerie vanessa vera veronica vicki victoria violet
virginia vivian wendy whitney yasmin yolanda yvonne zahra zainab zara zoe zoey
""".split())
MALE = set("""
aaron abdul abdullah abraham adam adrian ahmad ahmed aidan alan albert alex alexander alfred ali allan allen
alvin amir anthony andre andrew andy angus anwar archie arnold arthur asad ashton austin ayman barry ben benjamin
bernard bilal bill billy blake bob bobby brad bradley brandon brendan brent brett brian bruce bryan caleb callum
calvin cameron carl carlos cedric chad charles charlie chris christian christopher clarence clifford clint cody
colin connor corey craig curtis dale damian damien dan daniel danny darren darryl dave david dean dennis derek
derrick devin dominic don donald douglas duncan dustin dylan earl eddie edgar edward edwin eli elijah elliot
emmanuel eric ernest ethan eugene evan faisal felix fernando francis frank franklin fred frederick gabriel gareth
gary gavin george gerald gilbert glen glenn gordon graham grant greg gregory guy hamza harold harry harvey hassan
henry herbert howard hugh hussain hussein ian ibrahim imran isaac ismail ivan jack jackson jacob jake james jamie
jared jason jasper javier jay jeff jeffrey jeremy jerome jerry jesse jim jimmy joe joel john johnny jon jonathan
jordan jorge jose joseph josh joshua juan julian justin karim keith kelvin ken kenneth kevin khalid kieran kurt
kyle lance larry lawrence lee leo leon leonard leroy lewis liam lloyd logan louis lucas luis luke malcolm malik
manuel marc marcus mario mark martin marvin mason matt matthew maurice max maxwell michael mick mike miles mitchell
mohamed mohammad mohammed morris moses mustafa nathan nathaniel neil nicholas nick nigel noah norman oliver omar
oscar osman owen patrick paul pedro percy peter phil philip rafael ralph ramon randy raymond reginald ricardo
richard rick ricky rob robert robin rodney roger roland ron ronald ross roy russell ryan said saeed salman sam
samir samuel scott sean sebastian shane shaun sidney simon spencer stanley stephen steve steven stuart tariq
terry theodore thomas tim timothy toby todd tom tommy tony travis trevor troy tyler usman victor vincent wade
walter warren wayne wesley will william willie yusuf zack zain zachary
""".split())

# An estimate only: common Muslim given names and family names (Arabic, Turkish, Persian, South Asian).
MUSLIM_GIVEN = set("""
aamir abbas abdel abdul abdullah abdulrahman abdur abid adeel adnan ahmad ahmed aisha akbar akram ali alia amal
amina amir amjad ammar anas anwar arif asad asif asma ayaan ayesha ayman aziz azra bashir bilal bushra danish
dawood ebrahim ehsan elyas emre eman esra fahad faisal faiza farah farhan farid fariha farooq fatima fatma fawaz
fayez feroz ghulam habib hadi hafsa haider hajar hakim halima hamid hamza hana hanan hani haris haroon hasan hassan
hiba hossam huda humza husain hussain hussein ibrahim idris iftikhar ilyas imad iman imran inaya irfan isa ishaq
ismail jamal jamil javed jawad junaid kamal kamran karim kashif khadija khalid khalil laila layla leila lubna
mahmood mahmoud majid malak mariam marwa marwan maryam mehmet mehreen mohamed mohammad mohammed mohsin moiz
mubarak muhammad muhammed mujtaba mumtaz murad musa mustafa nabeel nabil nadeem nadia naeem naila najib nasir
nasser naveed nawaz nazia nazir noman noor nour omar omer osama osman qasim rabia rafiq rahim rahman rania
rashid rayan raza reem rehan rehman riaz ridwan rizwan ruqayyah saad saba sabah sabir sadaf sadia saeed safa saif
sajid salah saleem salem salma salman sameer samir samira sana saqib sara sarah saud shabana shabbir shafiq
shahid shahzad shakil shamim sharif sheikh shiraz shoaib siddiq sohail soraya sultan sumaya syed taha tahir
talha tamer tanveer tariq tasneem tayyab umar umer usama usman wajid waleed walid waqar waseem yahya yasin yasir
yasmin younis yousef yousuf yunus yusuf zahid zahra zain zainab zaid zakaria zaki zara zayd zeeshan zia zubair
""".split())
MUSLIM_FAMILY = set("""
abbas abbasi abdallah abdullah afzal ahmad ahmadi ahmed akhtar akram alam ali ansari ashraf aslam aziz baig
bashir begum bhatti butt chaudhry chaudhary choudhury dar el-sayed elsayed farooq farooqi ghani habib haddad
hamdan hamid haq hasan hashmi hassan hossain hussain hussein ibrahim iqbal islam ismail jaffer jamal javed kamal
karim khan khatun malik mansour mehmood memon mirza mohamed mohammad mohammed mughal mustafa nasser nawaz omar
osman pasha qadri qureshi rahman rana rashid raza rehman riaz saeed saleh salim shah shaikh sheikh siddiqui syed
tariq uddin usman yousef yousuf yusuf zaman zia
""".split())

# A time zone says roughly which country a quiz taker is in.
TZ_COUNTRY = {"america/new_york": "US", "america/chicago": "US", "america/denver": "US", "america/los_angeles": "US",
              "america/phoenix": "US", "america/anchorage": "US", "pacific/honolulu": "US", "america/detroit": "US",
              "america/toronto": "CA", "america/vancouver": "CA", "america/edmonton": "CA", "america/winnipeg": "CA",
              "america/halifax": "CA", "america/regina": "CA", "america/st_johns": "CA", "europe/london": "GB",
              "europe/dublin": "IE", "australia/sydney": "AU", "australia/melbourne": "AU", "australia/brisbane": "AU",
              "australia/perth": "AU", "australia/adelaide": "AU", "australia/hobart": "AU", "australia/darwin": "AU",
              "pacific/auckland": "NZ", "europe/berlin": "DE", "europe/paris": "FR", "europe/amsterdam": "NL",
              "europe/stockholm": "SE", "europe/oslo": "NO", "europe/copenhagen": "DK", "europe/madrid": "ES",
              "europe/rome": "IT", "europe/lisbon": "PT", "europe/zurich": "CH", "europe/vienna": "AT",
              "europe/brussels": "BE", "asia/dubai": "AE", "asia/riyadh": "SA", "asia/singapore": "SG",
              "africa/johannesburg": "ZA", "asia/karachi": "PK", "asia/kolkata": "IN", "europe/istanbul": "TR"}

QUIZ_KINDS = ("start", "answer", "reveal", "exit", "finish")
QUIZ_KEEP_DAYS = 180
ABANDONED_FIRST_DAYS = 60
RECORDS_MAX = 400
_PII = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|\+?\(?\d[\d\s().-]{5,}\d")
_WORD = re.compile(r"[a-z]+")


def _s(v: Any, n: int) -> str:
    return str(v or "").strip()[:n]


def _words(name: Any) -> list[str]:
    return _WORD.findall(str(name or "").lower().replace("-", " "))


def who_from_name(first_name: Any) -> str:
    """'him' (a man's name), 'her' (a woman's, so a partner buying for him) or ''."""
    w = _words(first_name)
    if not w:
        return ""
    if w[0] in FEMALE:
        return "her"
    if w[0] in MALE:
        return "him"
    return ""


def faith_from_names(first_name: Any, last_name: Any) -> str:
    """An estimate from the names alone: 'muslim', 'other', or '' when the names say nothing."""
    first, last = _words(first_name), _words(last_name)
    if any(w in MUSLIM_GIVEN for w in first) or any(w in MUSLIM_FAMILY for w in last):
        return "muslim"
    if (first and (first[0] in FEMALE or first[0] in MALE)) or last:
        return "other"
    return ""


def device_of(user_agent: Any) -> str:
    fam = attribution.os_family(user_agent)
    if fam == "ios":
        return "iphone"
    if fam == "android":
        return "android"
    return "desktop" if user_agent else ""


def app_of(user_agent: Any) -> str:
    ua = str(user_agent or "")
    if "Instagram" in ua:
        return "instagram"
    if attribution.in_app_browser(ua):
        return "facebook"
    return "browser" if ua else ""


def enrich_order(o: dict) -> dict:
    """The database's columns for one Shopify order: nothing personal, only
    what the names, the browser and the cart say about the buyer."""
    import tracking                                   # late: tracking imports attribution, not this
    cust = o.get("customer") or {}
    addr = o.get("shipping_address") or o.get("billing_address") or {}
    first = cust.get("first_name") or addr.get("first_name") or ""
    last = cust.get("last_name") or addr.get("last_name") or ""
    ua = (o.get("client_details") or {}).get("user_agent") or ""
    lines = [li for li in o.get("line_items") or [] if isinstance(li, dict)]
    qty = sum(int(li.get("quantity") or 0) for li in lines)
    main = max(lines, key=lambda li: float(li.get("price") or 0) * int(li.get("quantity") or 0), default={})
    when = tracking._parse_time(o.get("created_at")) or time.time()
    local = dt.datetime.fromtimestamp(when, config.store_tz())
    return {"device": device_of(ua), "app": app_of(ua), "qty": qty, "product": _s(main.get("title"), 80),
            "kind": "mrr" if tracking.is_renewal(o) else "new", "who": who_from_name(first),
            "faith": faith_from_names(first, last), "hour": local.hour, "weekday": local.weekday()}


# --- abandoned checkouts ----------------------------------------------------------------

def checkout_step(c: dict) -> str:
    """How far an abandoned checkout got: 'cart' (no address), 'shipping' (an address, no shipping
    choice), 'payment' (shipping chosen, left at payment)."""
    if c.get("shipping_lines"):
        return "payment"
    if c.get("shipping_address") or c.get("email"):
        return "shipping"
    return "cart"


def store_abandoned(c: dict, now: float) -> None:
    import tracking
    token = _s(c.get("token") or c.get("id"), 80)
    created = tracking._parse_time(c.get("created_at"))
    if not token or created is None:
        return
    addr = c.get("shipping_address") or c.get("billing_address") or {}
    lines = [li for li in c.get("line_items") or [] if isinstance(li, dict)]
    main = max(lines, key=lambda li: float(li.get("price") or 0) * int(li.get("quantity") or 0), default={})
    params = attribution.ad_params_from_url("https://x/" + str(c.get("landing_site") or "").lstrip("/"))
    lp, stripped = attribution.landing_page(params) if params else ("", False)
    ua = (c.get("client_details") or {}).get("user_agent") or ""
    db.run("INSERT INTO abandoned (token, created_at, updated_at, country, city, total, currency, step, qty, product, "
           "recovered, landing, ad_id, device, app) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET "
           "updated_at=excluded.updated_at, country=excluded.country, city=excluded.city, total=excluded.total, "
           "step=excluded.step, qty=excluded.qty, product=excluded.product, recovered=excluded.recovered",
           (token, created, now, _s(addr.get("country_code"), 2).upper(), _s(addr.get("city"), 60),
            round(float(c.get("total_price") or 0), 2), _s(c.get("currency"), 3).upper(), checkout_step(c),
            sum(int(li.get("quantity") or 0) for li in lines), _s(main.get("title"), 80),
            1 if c.get("completed_at") else 0, attribution.landing_kind(lp, stripped) or "direct",
            _s(params.get("ad_id"), 40), device_of(ua), app_of(ua)))


# --- the quiz ------------------------------------------------------------------------------

def record_quiz(payload: dict, user_agent: str, now: float) -> None:
    """One step of a quiz taker, from the quiz page. Raises ValueError on junk."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    session = _s(payload.get("session"), 64)
    kind = _s(payload.get("kind"), 10).lower()
    if not re.fullmatch(r"[\w-]{8,64}", session):
        raise ValueError("session must be 8 to 64 letters, digits, - or _")
    if kind not in QUIZ_KINDS:
        raise ValueError("kind must be one of " + ", ".join(QUIZ_KINDS))
    try:
        step = max(0, min(99, int(payload.get("step") or 0)))
        ms = max(0, min(3_600_000, int(payload.get("ms") or 0)))
    except (TypeError, ValueError):
        raise ValueError("step and ms must be whole numbers")
    tz = str(payload.get("tz") or "").lower()
    country = _s(payload.get("country"), 2).upper() if re.fullmatch(r"[A-Za-z]{2}", str(payload.get("country") or "")) else ""
    country = country or TZ_COUNTRY.get(tz, "")
    db.run("INSERT INTO quiz_events (session, at, kind, step, question, answer, dest, country, device, app, ms) "
           "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
           (session, now, kind, step, _PII.sub("", _s(payload.get("question"), 160)), _PII.sub("", _s(payload.get("answer"), 160)),
            _s(payload.get("dest"), 20).lower(), country, device_of(user_agent), app_of(user_agent), ms))


def _quiz_sessions(start: float) -> dict[str, dict]:
    """Every quiz taker since `start`: their steps in order."""
    out: dict[str, dict] = {}
    for e in db.query("SELECT * FROM quiz_events WHERE at>=? ORDER BY at, id", (start,)):
        s = out.setdefault(e["session"], {"session": e["session"], "started": e["at"], "last": e["at"], "country": "",
                                           "device": "", "app": "", "answers": {}, "steps": set(), "reveal": False,
                                           "finished": False, "dest": "", "ms": {}})
        s["last"] = e["at"]
        s["country"] = s["country"] or e["country"]
        s["device"] = s["device"] or e["device"]
        s["app"] = s["app"] or e["app"]
        if e["step"]:
            s["steps"].add(e["step"])
        if e["kind"] == "answer" and e["question"]:
            s["answers"][e["question"]] = e["answer"]
            if e["ms"]:
                s["ms"].setdefault(e["question"], []).append(e["ms"])
        if e["kind"] == "reveal":
            s["reveal"] = True
        if e["kind"] in ("finish", "exit"):
            s["finished"] = s["finished"] or e["kind"] == "finish" or bool(e["dest"])
            s["dest"] = e["dest"] or s["dest"]
    return out


def quiz_block(days: int, now: float, sales_by_session: dict[str, dict]) -> dict:
    """The quiz as a funnel: who reached each question, where they quit, how
    long each took, which answers buy, and how many revealed the card."""
    sessions = _quiz_sessions(now - days * 86400)
    if not sessions:
        return {"takers": 0, "questions": [], "note": "No quiz taker recorded yet."}
    takers = len(sessions)
    questions: dict[str, dict] = {}
    order: list[str] = []
    for s in sessions.values():
        for q in s["answers"]:
            if q not in questions:
                questions[q] = {"question": q, "reached": 0, "answers": {}, "ms": []}
                order.append(q)
    # The question order is the order takers met them (the first taker's order, then any new ones).
    for q in order:
        row = questions[q]
        for s in sessions.values():
            a = s["answers"].get(q)
            if a is None:
                continue
            row["reached"] += 1
            e = row["answers"].setdefault(a, {"answer": a, "n": 0, "bought": 0, "revenue": 0.0})
            e["n"] += 1
            sale = sales_by_session.get(s["session"])
            if sale:
                e["bought"] += 1
                e["revenue"] += sale["total"] or 0
            row["ms"].extend(s["ms"].get(q, []))
    out_q = []
    prev = takers
    worst, worst_drop = "", 0
    for i, q in enumerate(order):
        row = questions[q]
        quit_ = max(0, prev - row["reached"])
        if quit_ > worst_drop:
            worst, worst_drop = q, quit_
        ms = sorted(row["ms"])
        out_q.append({"n": i + 1, "question": q, "reached": row["reached"], "quit_before": quit_,
                      "median_s": round(ms[len(ms) // 2] / 1000, 1) if ms else None,
                      "answers": sorted(({**a, "revenue": round(a["revenue"], 2),
                                          "rate": round(a["bought"] / a["n"], 4) if a["n"] else None}
                                         for a in row["answers"].values()), key=lambda a: -a["n"])})
        prev = row["reached"]
    finished = sum(1 for s in sessions.values() if s["finished"] or len(s["answers"]) >= len(order))
    revealed = sum(1 for s in sessions.values() if s["reveal"])
    dests: dict[str, int] = {}
    for s in sessions.values():
        if s["dest"]:
            dests[s["dest"]] = dests.get(s["dest"], 0) + 1
    bought = sum(1 for s in sessions.values() if s["session"] in sales_by_session)
    by_country: dict[str, int] = {}
    for s in sessions.values():
        k = s["country"] or "??"
        by_country[k] = by_country.get(k, 0) + 1
    return {"takers": takers, "finished": finished, "finish_rate": round(finished / takers, 4),
            "revealed": revealed, "reveal_rate": round(revealed / takers, 4), "bought": bought,
            "buy_rate": round(bought / takers, 4), "worst_question": worst, "worst_quit": worst_drop,
            "destinations": sorted(({"dest": k, "n": v} for k, v in dests.items()), key=lambda d: -d["n"]),
            "countries": sorted(({"key": k, "n": v} for k, v in by_country.items()), key=lambda c: -c["n"])[:10],
            "devices": _count(s["device"] or "unknown" for s in sessions.values()),
            "apps": _count(s["app"] or "unknown" for s in sessions.values()),
            "questions": out_q,
            "note": "Each question: how many takers reached it, how many quit before it, the median seconds on it, "
                    "and which answers went on to buy."}


# --- the analyzer ---------------------------------------------------------------------------

def _count(values) -> list[dict]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return sorted(({"key": k, "n": n} for k, n in out.items()), key=lambda x: -x["n"])


def _share(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 4) if whole else None


def _split(orders: list[dict], key: str, labels: dict[str, str]) -> list[dict]:
    rev: dict[str, float] = {}
    n: dict[str, int] = {}
    for o in orders:
        k = str(o.get(key) or "unknown")
        n[k] = n.get(k, 0) + 1
        rev[k] = rev.get(k, 0.0) + (o["total"] or 0)
    total = len(orders)
    return sorted(({"key": k, "label": labels.get(k, k), "n": n[k], "revenue": round(rev[k], 2), "share": _share(n[k], total)}
                   for k in n), key=lambda r: -r["n"])


def _credit_rows(orders: list[dict]) -> dict[str, dict]:
    """The ad the tracker credited each order with (campaign > ad set > ad, landing page)."""
    out = {}
    for oid, r in db.orders_by_id([o["order_id"] for o in orders]).items():
        a = r.get("attribution") if isinstance(r.get("attribution"), dict) else {}
        lp, stripped = a.get("lp"), a.get("ids_stripped")
        out[oid] = {"campaign": a.get("campaign_name") or "", "adset": a.get("adset_name") or "", "ad": a.get("ad_name") or "",
                    "ad_id": a.get("ad_id") or "", "meta": bool(a.get("meta")),
                    "landing": (attribution.landing_kind(lp, stripped) or "direct") if a.get("meta") else "",
                    "quiz": attribution.landing_kind(lp, stripped) == attribution.QUIZ or attribution.quiz_assist(a),
                    "session": _s(a.get("qs"), 64)}
    return out


def _period(start: float, end: float) -> list[dict]:
    return db.query("SELECT * FROM backend_orders WHERE created_at>=? AND created_at<? AND test=0 AND cancelled_at IS NULL "
                    "ORDER BY created_at DESC", (start, end))


def _trends(cur: list[dict], prev: list[dict], days: int, abandoned: dict, quiz: dict, cur_credits: dict,
            currency: str = "USD") -> list[str]:
    """Short plain lines on what moved against the previous period of the same length."""
    lines = []
    new = [o for o in cur if o.get("kind") != "mrr"]
    new_prev = [o for o in prev if o.get("kind") != "mrr"]
    rev, rev_prev = sum(o["total"] or 0 for o in new), sum(o["total"] or 0 for o in new_prev)
    if new_prev:
        change = (rev - rev_prev) / rev_prev if rev_prev else 0
        lines.append(f"New sales {'up' if change >= 0 else 'down'} {abs(change) * 100:.0f}% on the previous {days} days "
                     f"({len(new)} vs {len(new_prev)} orders).")
    elif new:
        lines.append(f"{len(new)} new sales in the last {days} days; nothing to compare with before.")
    if new:
        top = _split(new, "country", {})[0]
        prev_share = _share(sum(1 for o in new_prev if o.get("country") == top["key"]), len(new_prev))
        lines.append(f"{top['key']} is {top['share'] * 100:.0f}% of new sales" +
                     (f", {'up' if (prev_share or 0) <= top['share'] else 'down'} from {prev_share * 100:.0f}%." if prev_share is not None else "."))
        her = sum(1 for o in new if o.get("who") == "her")
        known = sum(1 for o in new if o.get("who"))
        if known:
            her_prev = _share(sum(1 for o in new_prev if o.get("who") == "her"), sum(1 for o in new_prev if o.get("who")))
            lines.append(f"Partners buying for him: {her / known * 100:.0f}% of buyers with a readable name" +
                         (f" ({her_prev * 100:.0f}% before)." if her_prev is not None else "."))
        ip = sum(1 for o in new if o.get("device") == "iphone")
        fb = sum(1 for o in new if o.get("app") == "facebook")
        ig = sum(1 for o in new if o.get("app") == "instagram")
        dev_known = sum(1 for o in new if o.get("device"))
        if dev_known:
            lines.append(f"iPhone buyers {ip / dev_known * 100:.0f}%; bought inside the Facebook app {fb / dev_known * 100:.0f}%, "
                         f"Instagram {ig / dev_known * 100:.0f}%.")
        big = sum(1 for o in new if (o.get("qty") or 0) >= 3)
        big_prev = _share(sum(1 for o in new_prev if (o.get("qty") or 0) >= 3), len(new_prev))
        lines.append(f"{big / len(new) * 100:.0f}% took 3 bottles or more" +
                     (f" (was {big_prev * 100:.0f}%)." if big_prev is not None else "."))
        hours = _count(str(o.get("hour")) for o in new if o.get("hour") is not None)
        if hours:
            h = int(hours[0]["key"])
            lines.append(f"Most sales land around {h % 12 or 12} {'AM' if h < 12 else 'PM'} store time ({hours[0]['n']} of them).")
        quiz_sales = sum(1 for o in new if cur_credits.get(o["order_id"], {}).get("quiz"))
        if quiz_sales:
            lines.append(f"{quiz_sales} of the {len(new)} new sales started in the quiz.")
    if abandoned.get("n"):
        pay = next((s["n"] for s in abandoned.get("steps", []) if s["key"] == "payment"), 0)
        sym = "$" if currency in ("USD", "CAD", "AUD", "NZD") else currency + " "
        lines.append(f"{abandoned['n']} checkouts abandoned, worth {sym}{abandoned['value']:,.0f}; "
                     f"{pay / abandoned['n'] * 100:.0f}% got as far as payment; {abandoned.get('recovered', 0)} came back and bought.")
    if quiz.get("takers"):
        lines.append(f"Quiz: {quiz['takers']} started, {quiz['finish_rate'] * 100:.0f}% finished" +
                     (f", most quit before \"{quiz['worst_question'][:60]}\"." if quiz.get("worst_question") else "."))
    return lines[:8]


def overview(days: int, country: str = "", landing: str = "", kind: str = "", now: Optional[float] = None) -> dict:
    """The whole tab for the orders of the last `days` days, narrowed by
    country, landing page (direct, listicle, quiz) and kind (new, mrr)."""
    now = now or time.time()
    start = now - days * 86400
    orders = _period(start, now + 1)
    credits = _credit_rows(orders)
    country, landing, kind = _s(country, 2).upper(), _s(landing, 10).lower(), _s(kind, 5).lower()
    chosen = [o for o in orders if (not country or o.get("country") == country)
              and (not landing or credits.get(o["order_id"], {}).get("landing") == landing)
              and (not kind or o.get("kind") == kind)]
    new = [o for o in chosen if o.get("kind") != "mrr"]
    prev = [o for o in _period(start - days * 86400, start)
            if (not country or o.get("country") == country) and (not kind or o.get("kind") == kind)]
    aband = db.query("SELECT * FROM abandoned WHERE created_at>=? ORDER BY created_at DESC", (start,))
    if country:
        aband = [a for a in aband if a.get("country") == country]
    sales_by_session = {credits[o["order_id"]]["session"]: o for o in orders if credits.get(o["order_id"], {}).get("session")}
    quiz = quiz_block(days, now, sales_by_session)
    heat = [[0] * 24 for _ in range(7)]
    for o in new:
        if o.get("hour") is not None and o.get("weekday") is not None:
            heat[int(o["weekday"])][int(o["hour"])] += 1
    countries = []
    for row in _split(new, "country", {}):
        mine = [o for o in new if (o.get("country") or "unknown") == row["key"]]
        countries.append({**row, "aov": round(row["revenue"] / row["n"], 2) if row["n"] else None,
                          "mrr": sum(1 for o in chosen if o.get("kind") == "mrr" and (o.get("country") or "unknown") == row["key"]),
                          "bundles": {str(q): sum(1 for o in mine if (o.get("qty") or 0) == q) for q in (1, 3, 5)},
                          "her": _share(sum(1 for o in mine if o.get("who") == "her"), sum(1 for o in mine if o.get("who")))})
    cities = _count((o.get("country") or "??") + " · " + o["city"] for o in new if o.get("city"))[:12]
    aband_block = {"n": len(aband), "value": round(sum(a["total"] or 0 for a in aband), 2),
                   "recovered": sum(1 for a in aband if a.get("recovered")),
                   "steps": _count(a.get("step") or "cart" for a in aband),
                   "countries": _count(a.get("country") or "??" for a in aband)[:8],
                   "landing": _count(a.get("landing") or "direct" for a in aband),
                   "rate": _share(len(aband), len(aband) + len(new))}
    who = _split(new, "who", {"him": "Him", "her": "A partner, for him", "unknown": "Can't tell from the name"})
    faith = _split(new, "faith", {"muslim": "Muslim (estimated from names)", "other": "Other", "unknown": "Can't tell"})
    records = []
    for o in chosen[:RECORDS_MAX]:
        c = credits.get(o["order_id"], {})
        records.append({"order_id": o["order_id"], "order_name": o["order_name"], "at": o["created_at"], "total": o["total"],
                        "currency": o["currency"], "country": o["country"], "city": o["city"], "device": o.get("device") or "",
                        "app": o.get("app") or "", "qty": o.get("qty"), "product": o.get("product") or "", "kind": o.get("kind") or "new",
                        "campaign": c.get("campaign", ""), "adset": c.get("adset", ""), "ad": c.get("ad", ""),
                        "landing": c.get("landing", ""), "quiz": bool(c.get("quiz"))})
    return {"days": days, "filters": {"country": country, "landing": landing, "kind": kind},
            "currency": next((o["currency"] for o in chosen if o.get("currency")), "USD"),
            "tiles": {"sales": len(new), "revenue": round(sum(o["total"] or 0 for o in new), 2), "mrr": len(chosen) - len(new),
                      "countries": len(countries), "quiz_takers": quiz.get("takers", 0), "abandoned": len(aband)},
            "trends": _trends(chosen, prev, days, aband_block, quiz, credits,
                              next((o["currency"] for o in chosen if o.get("currency")), "USD")),
            "who": who, "faith": faith, "countries": countries, "cities": cities,
            "devices": _split(new, "device", {"iphone": "iPhone", "android": "Android", "desktop": "Desktop", "unknown": "Unknown"}),
            "apps": _split(new, "app", {"facebook": "Facebook app", "instagram": "Instagram app", "browser": "Browser", "unknown": "Unknown"}),
            "bundles": _split(new, "qty", {"1": "1 bottle", "3": "3 bottles", "5": "5 bottles"}),
            "landing": _split([{**o, "landing": credits.get(o["order_id"], {}).get("landing") or "not from an ad"} for o in new], "landing",
                              {"direct": "Product page", "listicle": "Listicle", "quiz": "Quiz"}),
            "heatmap": heat, "abandoned": aband_block, "quiz": quiz, "records": records,
            "note": "Who buys and Muslim vs other are estimates from first and last names, shown as totals only."}


def export_csv(what: str, days: int, now: Optional[float] = None) -> tuple[str, str]:
    """(filename, CSV text) of the sales, the abandoned checkouts or the quiz takers of the last `days` days."""
    now = now or time.time()
    start = now - days * 86400
    buf = io.StringIO()
    w = csv.writer(buf)
    tz = config.store_tz()

    def when(ts):
        return dt.datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M") if ts else ""
    if what == "abandoned":
        w.writerow(["created", "country", "city", "total", "currency", "left_at", "items", "product", "landing", "device", "app", "recovered"])
        for a in db.query("SELECT * FROM abandoned WHERE created_at>=? ORDER BY created_at DESC", (start,)):
            w.writerow([when(a["created_at"]), a["country"], a["city"], a["total"], a["currency"], a["step"], a["qty"], a["product"],
                        a["landing"], a["device"], a["app"], "yes" if a["recovered"] else "no"])
        return f"abandoned-checkouts-{days}d.csv", buf.getvalue()
    if what == "quiz":
        w.writerow(["session", "started", "country", "device", "app", "finished", "revealed_card", "went_to", "answers"])
        for s in _quiz_sessions(start).values():
            w.writerow([s["session"], when(s["started"]), s["country"], s["device"], s["app"], "yes" if s["finished"] else "no",
                        "yes" if s["reveal"] else "no", s["dest"], json.dumps(s["answers"], ensure_ascii=False)])
        return f"quiz-takers-{days}d.csv", buf.getvalue()
    orders = _period(start, now + 1)
    credits = _credit_rows(orders)
    w.writerow(["order", "created", "kind", "total", "currency", "country", "city", "items", "product", "device", "app",
                "campaign", "ad_set", "ad", "landing", "started_in_quiz"])
    for o in orders:
        c = credits.get(o["order_id"], {})
        w.writerow([o["order_name"], when(o["created_at"]), o.get("kind") or "new", o["total"], o["currency"], o["country"], o["city"],
                    o.get("qty"), o.get("product"), o.get("device"), o.get("app"), c.get("campaign", ""), c.get("adset", ""),
                    c.get("ad", ""), c.get("landing", ""), "yes" if c.get("quiz") else "no"])
    return f"sales-{days}d.csv", buf.getvalue()
