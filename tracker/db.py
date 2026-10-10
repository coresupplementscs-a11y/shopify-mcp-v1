"""
SQLite persistence. One file on the Railway volume. The main tables:

  sessions  – what the pixel told us about a browser (fbp/fbc/ip/ua/contact and
              the Meta ads it arrived from), keyed by Shopify's client id and,
              once known, the checkout token.
  events    – every event we sent (or tried to send) to Meta, per dataset, with the trace id.
  orders    – Shopify orders that must produce a Purchase, where they stand,
              and the ad click credited with each sale (the one stored decision).
  proposals – fixes the watchdog suggests; nothing happens until the owner approves.
"""
import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional

import attribution
import config
from config import DB_PATH

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    client_id      TEXT PRIMARY KEY,
    checkout_token TEXT,
    fbp            TEXT,
    fbc            TEXT,
    ip             TEXT,
    user_agent     TEXT,
    email          TEXT,
    phone          TEXT,
    first_name     TEXT,
    last_name      TEXT,
    landing_url    TEXT,
    first_seen     REAL NOT NULL,
    last_seen      REAL NOT NULL,
    ad_params      TEXT,                  -- JSON: the last Meta ad link this browser arrived from
    ad_seen_at     REAL,
    ad_history     TEXT                   -- JSON: its last 20 Meta ad arrivals, for assists
);
CREATE INDEX IF NOT EXISTS idx_sessions_checkout ON sessions(checkout_token);
CREATE INDEX IF NOT EXISTS idx_sessions_fbp ON sessions(fbp);
CREATE INDEX IF NOT EXISTS idx_sessions_email ON sessions(email);
CREATE INDEX IF NOT EXISTS idx_sessions_seen ON sessions(last_seen);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_name  TEXT NOT NULL,
    event_id    TEXT NOT NULL,
    source      TEXT NOT NULL,            -- pixel | webhook | reconcile | manual | test
    status      TEXT NOT NULL,            -- sent | failed | repeat (kept, not sent: tracking.REPEAT_EVENTS)
                                          -- | held (waiting for the shopper's email: tracking.HOLD_EVENTS)
                                          -- | released (a held one that went out; its sends are source 'released')
    fbtrace_id  TEXT,
    error       TEXT,
    match_keys  TEXT,                     -- comma list of user_data keys we had
    order_id    TEXT,
    payload     TEXT NOT NULL,
    created_at  REAL NOT NULL,
    pixel_id    TEXT,                     -- the dataset it went to
    client_id   TEXT                      -- the browser, for storefront events (funnel)
);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_order ON events(order_id);

CREATE TABLE IF NOT EXISTS orders (
    order_id       TEXT PRIMARY KEY,
    order_name     TEXT,
    checkout_token TEXT,
    status         TEXT NOT NULL,         -- pending | sent | failed | skipped
    kind           TEXT,                  -- purchase | renewal | test | too_old | before_start | cancelled | manual
    attempts       INTEGER NOT NULL DEFAULT 0,
    forced         INTEGER NOT NULL DEFAULT 0,   -- operator asked for a resend: ignore start/skip rules
    last_error     TEXT,
    fbtrace_id     TEXT,
    order_json     TEXT NOT NULL,
    received_at    REAL NOT NULL,
    sent_at        REAL,
    attribution    TEXT,                  -- JSON: the click credited with the sale (attribution.resolve)
    wait_until     REAL,                  -- an unmatched new sale waits for its visit until then
    match_tries    INTEGER NOT NULL DEFAULT 0,
    resend_mark    INTEGER                -- a manual resend: the last events.id when it was asked for
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

CREATE TABLE IF NOT EXISTS meta_kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS watchdog_runs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at   REAL NOT NULL,
    status   TEXT NOT NULL,               -- ok | warn | fail
    results  TEXT NOT NULL                -- JSON list of checks
);
CREATE INDEX IF NOT EXISTS idx_watchdog_run_at ON watchdog_runs(run_at);

CREATE TABLE IF NOT EXISTS emq_snapshots (
    pixel_id    TEXT NOT NULL,
    event_name  TEXT NOT NULL,
    score       REAL,
    taken_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emq_taken ON emq_snapshots(pixel_id, taken_at);

CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,     -- one proposal per problem, ever
    kind        TEXT NOT NULL,            -- resend | renewal_tag | stripped_ids
    title       TEXT NOT NULL,
    detail      TEXT NOT NULL,
    action      TEXT NOT NULL,            -- JSON: what approving does
    status      TEXT NOT NULL,            -- pending | approved | dismissed | done | failed
    created_at  REAL NOT NULL,
    decided_at  REAL,
    result      TEXT
);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status, created_at);

CREATE TABLE IF NOT EXISTS agent_chats (
    id          TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    messages    TEXT NOT NULL             -- JSON: the whole conversation, appended to, never edited
);

-- The Backend tab (backend.py): the store's recent orders after the sale, their parcels, refunds and
-- disputes. Shipping country and city only; never a name, email or address.
CREATE TABLE IF NOT EXISTS backend_orders (
    order_id         TEXT PRIMARY KEY,
    order_name       TEXT,
    created_at       REAL NOT NULL,
    country          TEXT,                 -- the shipping address's country code
    city             TEXT,
    total            REAL,
    currency         TEXT,
    financial_status TEXT,
    cancelled_at     REAL,
    fulfilled_at     REAL,                 -- the first fulfilment
    test             INTEGER NOT NULL DEFAULT 0,
    updated_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backend_orders_created ON backend_orders(created_at);
CREATE TABLE IF NOT EXISTS shipments (
    tracking_number TEXT PRIMARY KEY,
    order_id        TEXT NOT NULL,
    carrier         TEXT,                  -- Shopify's name for it
    carrier_code    INTEGER,               -- 17TRACK's code, once known
    fulfilled_at    REAL,
    registered      INTEGER NOT NULL DEFAULT 0,   -- 0 not yet, 1 with 17TRACK, -1 refused
    reg_error       TEXT,
    status          TEXT,                  -- 17TRACK's main status (Delivered, InTransit...)
    sub_status      TEXT,
    last_event      TEXT,
    last_event_at   REAL,
    delivered_at    REAL,
    transit_days    REAL,
    checked_at      REAL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shipments_order ON shipments(order_id);
CREATE TABLE IF NOT EXISTS refunds (
    refund_id   TEXT PRIMARY KEY,
    order_id    TEXT NOT NULL,
    created_at  REAL NOT NULL,
    amount      REAL,
    currency    TEXT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_refunds_order ON refunds(order_id);
-- The Database tab (database.py): abandoned checkouts and the quiz's takers. Nothing personal.
CREATE TABLE IF NOT EXISTS abandoned (
    token       TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    country     TEXT,
    city        TEXT,
    total       REAL,
    currency    TEXT,
    step        TEXT,                  -- cart, shipping or payment: where they left
    qty         INTEGER,
    product     TEXT,
    recovered   INTEGER NOT NULL DEFAULT 0,
    landing     TEXT,                  -- direct, listicle or quiz
    ad_id       TEXT,
    device      TEXT,
    app         TEXT
);
CREATE INDEX IF NOT EXISTS idx_abandoned_created ON abandoned(created_at);
CREATE TABLE IF NOT EXISTS quiz_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    session   TEXT NOT NULL,
    at        REAL NOT NULL,
    kind      TEXT NOT NULL,           -- start, answer, reveal, exit, finish
    step      INTEGER,
    question  TEXT,
    answer    TEXT,
    dest      TEXT,                    -- where the taker went at the end: listicle, product, ''
    country   TEXT,
    device    TEXT,
    app       TEXT,
    ms        INTEGER,                 -- time spent on the question
    bot       INTEGER NOT NULL DEFAULT 0  -- a crawler (Meta's ad review, Google...): left out of the quiz numbers
);
CREATE INDEX IF NOT EXISTS idx_quiz_session ON quiz_events(session, at);
CREATE INDEX IF NOT EXISTS idx_quiz_at ON quiz_events(at);
CREATE TABLE IF NOT EXISTS disputes (
    dispute_id       TEXT PRIMARY KEY,
    order_id         TEXT NOT NULL,
    type             TEXT,
    status           TEXT,
    reason           TEXT,
    network_reason   TEXT,
    amount           REAL,
    currency         TEXT,
    initiated_at     REAL,
    evidence_due_by  REAL,
    evidence_sent_on REAL,
    finalized_on     REAL,
    updated_at       REAL NOT NULL
);
"""

# Columns added after the first release; applied to existing volumes on boot.
MIGRATIONS = {
    ("orders", "kind"): "ALTER TABLE orders ADD COLUMN kind TEXT",
    ("orders", "forced"): "ALTER TABLE orders ADD COLUMN forced INTEGER NOT NULL DEFAULT 0",
    ("events", "pixel_id"): "ALTER TABLE events ADD COLUMN pixel_id TEXT",
    ("events", "client_id"): "ALTER TABLE events ADD COLUMN client_id TEXT",
    ("sessions", "ad_params"): "ALTER TABLE sessions ADD COLUMN ad_params TEXT",
    ("sessions", "ad_seen_at"): "ALTER TABLE sessions ADD COLUMN ad_seen_at REAL",
    ("orders", "attribution"): "ALTER TABLE orders ADD COLUMN attribution TEXT",
    ("sessions", "ad_history"): "ALTER TABLE sessions ADD COLUMN ad_history TEXT",
    ("orders", "wait_until"): "ALTER TABLE orders ADD COLUMN wait_until REAL",
    ("orders", "match_tries"): "ALTER TABLE orders ADD COLUMN match_tries INTEGER NOT NULL DEFAULT 0",
    ("orders", "resend_mark"): "ALTER TABLE orders ADD COLUMN resend_mark INTEGER",
    # The Database tab's columns on the backend's orders (Oct 9 2026).
    ("backend_orders", "device"): "ALTER TABLE backend_orders ADD COLUMN device TEXT",
    ("backend_orders", "app"): "ALTER TABLE backend_orders ADD COLUMN app TEXT",
    ("backend_orders", "qty"): "ALTER TABLE backend_orders ADD COLUMN qty INTEGER",
    ("backend_orders", "product"): "ALTER TABLE backend_orders ADD COLUMN product TEXT",
    ("backend_orders", "kind"): "ALTER TABLE backend_orders ADD COLUMN kind TEXT",
    ("backend_orders", "who"): "ALTER TABLE backend_orders ADD COLUMN who TEXT",
    ("backend_orders", "faith"): "ALTER TABLE backend_orders ADD COLUMN faith TEXT",
    ("backend_orders", "hour"): "ALTER TABLE backend_orders ADD COLUMN hour INTEGER",
    ("backend_orders", "weekday"): "ALTER TABLE backend_orders ADD COLUMN weekday INTEGER",
    ("quiz_events", "bot"): "ALTER TABLE quiz_events ADD COLUMN bot INTEGER NOT NULL DEFAULT 0",
}


def init() -> None:
    global _conn
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.executescript(SCHEMA)
    attribution.set_organic_lookup(lambda key: kv_get("organic_click:" + key) is not None)
    for (table, column), ddl in MIGRATIONS.items():
        cols = {r[1] for r in _conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            _conn.execute(ddl)
    # Rows from before backup pixels existed all went to the main dataset.
    # The dedup key now includes the dataset, so the same event can be
    # recorded once per pixel.
    _conn.execute("UPDATE events SET pixel_id=? WHERE pixel_id IS NULL OR pixel_id=''",
                  (config.META_PIXEL_ID,))
    _conn.execute("DROP INDEX IF EXISTS idx_events_dedup")
    _conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_events_pixel_dedup "
                  "ON events(pixel_id, event_name, event_id, status)")
    # The hub and the watchdog read one pixel's recent events every minute.
    # Without this they walk every stored row of that pixel, on the event loop
    # that also serves /collect. It covers the funnel and the event counts.
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_events_pixel_time "
                  "ON events(pixel_id, created_at, status, event_name, source, client_id)")


def _c() -> sqlite3.Connection:
    if _conn is None:
        init()
    return _conn  # type: ignore[return-value]


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _with_payload(rows: list[dict]) -> list[dict]:
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return rows


# --- sessions ---------------------------------------------------------------

SESSION_FIELDS = ("checkout_token", "fbp", "fbc", "ip", "user_agent", "email",
                  "phone", "first_name", "last_name", "landing_url", "ad_params", "ad_seen_at",
                  "ad_history")


def upsert_session(client_id: str, *, ad_visit: Optional[dict] = None, arrival: Optional[dict] = None,
                   **fields: Any) -> dict:
    """Merge new facts about a browser into its session. Never overwrite a
    known value with an empty one. `ad_visit` (a Meta ad arrival) is added to
    the browser's click history.

    `arrival` is a storefront page reached from an ad ({"params", "fbclid",
    "at"}): it becomes the session's current click, the newest always winning.
    Its fbc is stamped with the moment its fbclid first arrived here, so a
    reload of the same link is the same click, not a new one, and an older ad
    link opened again (a restored tab, the back button) never takes over from
    a newer click. An fbc cookie (`fbc`) only replaces the stored one when it
    is a newer click."""
    now = time.time()
    with _lock:
        row = _c().execute("SELECT * FROM sessions WHERE client_id=?", (client_id,)).fetchone()
        cur = dict(row) if row else {"client_id": client_id, "first_seen": now}
        for k in SESSION_FIELDS:
            v = fields.get(k)
            if v and k != "fbc":
                cur[k] = v
        history = cur.get("ad_history")
        incoming = str(fields.get("fbc") or "")
        seen = attribution.first_arrival(history, attribution.fbc_fbclid(incoming))
        if seen is not None:
            # The pixel re-stamps its cookie when an old ad link is opened again:
            # a click seen before keeps the moment it first arrived.
            incoming = attribution.make_fbc(attribution.fbc_fbclid(incoming), seen)
        cur["fbc"] = attribution.newer_fbc(cur.get("fbc"), incoming) or None
        if arrival:
            # Every arrival to the millisecond, like the fbc one may carry, so
            # two arrivals always compare in the order they came.
            params, fbclid, at = arrival["params"], arrival.get("fbclid") or "", int(float(arrival["at"]) * 1000) / 1000
            same_link = cur.get("ad_params") == json.dumps(params)
            first = None                                         # when this click first arrived, if it did before
            if fbclid and attribution.fbc_fbclid(cur.get("fbc")) == fbclid:
                first = attribution.click_time(cur["fbc"])
            elif fbclid:
                first = attribution.first_arrival(history, fbclid)
            if first is not None and float(cur.get("ad_seen_at") or 0) > first + 0.002:
                arrival = None                                   # an older click came back: the newer one stays
            elif fbclid:
                if first is not None:
                    at = first                                   # the same click again: a reload
                if first is None or attribution.fbc_fbclid(cur.get("fbc")) != fbclid:
                    cur["fbc"] = attribution.make_fbc(fbclid, at)
            elif same_link and cur.get("ad_seen_at") and at - float(cur["ad_seen_at"]) < attribution.REPEAT_SECONDS:
                at = float(cur["ad_seen_at"])                    # the next page of the same visit
        if arrival:
            cur["ad_params"], cur["ad_seen_at"] = json.dumps(params), at
            ad_visit = attribution.ad_visit(params, at, fbclid) or ad_visit
        if ad_visit:
            # Read and written under the lock, so two events from one browser can't drop a visit.
            cur["ad_history"] = json.dumps(attribution.add_ad_visit(cur.get("ad_history"), ad_visit))
        cur["last_seen"] = now
        cols = ["client_id", "first_seen", "last_seen", *SESSION_FIELDS]
        _c().execute(
            f"INSERT OR REPLACE INTO sessions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [cur.get(c) for c in cols],
        )
        return cur


def get_session(client_id: str) -> Optional[dict]:
    with _lock:
        row = _c().execute("SELECT * FROM sessions WHERE client_id=?", (client_id,)).fetchone()
        return dict(row) if row else None


def find_session_by_checkout(checkout_token: str) -> Optional[dict]:
    if not checkout_token:
        return None
    with _lock:
        row = _c().execute(
            "SELECT * FROM sessions WHERE checkout_token=? ORDER BY last_seen DESC LIMIT 1",
            (checkout_token,),
        ).fetchone()
        return dict(row) if row else None


def client_ids_by_checkout(checkout_tokens: list[str]) -> dict[str, str]:
    """The browser (client id) behind each checkout token, like
    find_session_by_checkout: the most recently seen session when several share one."""
    tokens = list(dict.fromkeys(str(t) for t in checkout_tokens if t))
    out: dict[str, str] = {}
    with _lock:
        for i in range(0, len(tokens), 500):
            chunk = tokens[i:i + 500]
            for r in _c().execute(
                    "SELECT checkout_token, client_id FROM sessions "
                    f"WHERE checkout_token IN ({','.join('?' * len(chunk))}) ORDER BY last_seen", chunk):
                out[r["checkout_token"]] = r["client_id"]         # newer rows come later and win
    return out


def in_app_sessions_near(ip_block: str, since: float, until: float) -> list[dict]:
    """Sessions of Meta's in-app browsers from an IP block that came from a Meta
    ad and were seen between `since` and `until`, newest first."""
    if not ip_block:
        return []
    with _lock:
        rows = _rows(_c().execute(
            "SELECT * FROM sessions WHERE ip LIKE ? AND ad_seen_at IS NOT NULL AND last_seen>=? AND first_seen<=? "
            "ORDER BY last_seen DESC LIMIT 50", (ip_block.replace("%", "") + "%", since, until)))
    return [r for r in rows if attribution.in_app_browser(r.get("user_agent"))]


def checkout_cart(client_id: str, *, before: Optional[float] = None, newest: bool = True) -> Optional[dict]:
    """What a browser had at checkout: its newest (or first) InitiateCheckout
    the pixel reported, not after `before`: {"at", "value", "currency",
    "contents": ((product id, quantity), ...) sorted}. None without one."""
    with _lock:
        row = _c().execute(
            "SELECT payload, created_at FROM events WHERE client_id=? AND event_name='InitiateCheckout' "
            "AND source='pixel' AND pixel_id=? AND created_at<=? "
            f"ORDER BY created_at {'DESC' if newest else 'ASC'} LIMIT 1",
            (client_id, config.META_PIXEL_ID, before if before is not None else 1e12)).fetchone()
    if not row:
        return None
    try:
        custom = (json.loads(row["payload"]) or {}).get("custom_data") or {}
    except (TypeError, ValueError, AttributeError):
        return None
    contents = []
    for c in custom.get("contents") or []:
        if isinstance(c, dict) and c.get("id") is not None:
            try:
                contents.append((str(c["id"]), int(float(c.get("quantity") or 1))))
            except (TypeError, ValueError):
                continue
    try:
        value = round(float(custom.get("value") or 0), 2)
    except (TypeError, ValueError):
        value = 0.0
    return {"at": float(row["created_at"]), "value": value, "currency": str(custom.get("currency") or ""),
            "contents": tuple(sorted(contents))}


def first_page_url(client_id: str) -> str:
    """The first storefront page the pixel reported for a browser ('' when none)."""
    with _lock:
        row = _c().execute(
            "SELECT json_extract(payload, '$.event_source_url') AS url FROM events "
            "WHERE client_id=? AND event_name='PageView' AND source='pixel' AND pixel_id=? "
            "ORDER BY created_at LIMIT 1", (client_id, config.META_PIXEL_ID)).fetchone()
    return str(row["url"] or "") if row else ""


def find_session_by_fbp(fbp: str) -> Optional[dict]:
    if not fbp:
        return None
    with _lock:
        row = _c().execute(
            "SELECT * FROM sessions WHERE fbp=? ORDER BY last_seen DESC LIMIT 1", (fbp,),
        ).fetchone()
        return dict(row) if row else None


def find_sessions_by_email(email: str, since: float) -> list[dict]:
    """All recent sessions claiming this email, newest first. The caller must
    corroborate one with data the pixel cannot forge before trusting it."""
    if not email:
        return []
    with _lock:
        return _rows(_c().execute(
            "SELECT * FROM sessions WHERE lower(email)=lower(?) AND last_seen>=? "
            "ORDER BY last_seen DESC LIMIT 20", (email, since)))


def last_pixel_seen() -> Optional[float]:
    with _lock:
        row = _c().execute("SELECT MAX(last_seen) AS t FROM sessions").fetchone()
        return row["t"] if row and row["t"] else None


# --- events -----------------------------------------------------------------

# An order's events are its Purchase (or MRR event) and, for an express
# checkout, the server InitiateCheckout sent just before it
# (tracking.express_checkout_event). What an order was reported as, and which
# datasets have it, go by the first kind only.
REPORTED = "event_name<>'InitiateCheckout'"


def record_event(event_name: str, event_id: str, source: str, status: str,
                 payload: dict, fbtrace_id: str = "", error: str = "",
                 order_id: str = "", pixel_id: str = "", client_id: str = "") -> None:
    match_keys = ",".join(sorted(k for k, v in (payload.get("user_data") or {}).items() if v))
    with _lock:
        _c().execute(
            "INSERT OR REPLACE INTO events (event_name, event_id, source, status, fbtrace_id, error, "
            "match_keys, order_id, payload, created_at, pixel_id, client_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_name, event_id, source, status, fbtrace_id, error, match_keys,
             order_id or None, json.dumps(payload, default=str), time.time(),
             pixel_id or config.META_PIXEL_ID, client_id or None),
        )


def event_already_sent(event_name: str, event_id: str, pixel_id: str = "", after: int = 0) -> bool:
    """Whether the dataset accepted this event. With `after` (an order's
    resend_mark), only an acceptance recorded after that mark counts."""
    with _lock:
        row = _c().execute(
            "SELECT 1 FROM events WHERE pixel_id=? AND event_name=? AND event_id=? AND status='sent' AND id>? LIMIT 1",
            (pixel_id or config.META_PIXEL_ID, event_name, event_id, int(after or 0)),
        ).fetchone()
        return row is not None


def pixel_checkout_seen(client_ids: list[str], checkout_token: str, since: float) -> bool:
    """Whether the storefront pixel reported a checkout_started (sent to Meta
    or not) since `since`, from one of these browsers or from a browser with
    this checkout token. Read from the main dataset's rows, which every
    storefront event has (like the funnel), so idx_events_pixel_time covers it."""
    ids = [str(c) for c in client_ids if c][:50]
    if not ids and not checkout_token:
        return False
    who = []
    if ids:
        who.append(f"client_id IN ({','.join('?' * len(ids))})")
    if checkout_token:
        who.append("client_id IN (SELECT client_id FROM sessions WHERE checkout_token=?)")
    args = [*ids, *([checkout_token] if checkout_token else [])]
    with _lock:
        row = _c().execute(
            "SELECT 1 FROM events WHERE pixel_id=? AND created_at>=? AND event_name='InitiateCheckout' "
            f"AND source='pixel' AND ({' OR '.join(who)}) LIMIT 1",
            (config.META_PIXEL_ID, since, *args)).fetchone()
    return row is not None


def recent_pixel_sends(event_name: str, client_id: str, since: float) -> list[dict]:
    """The storefront events of this name this browser sent to the main
    dataset since `since` (or holds to send: tracking.HOLD_EVENTS), newest
    first, payload decoded."""
    with _lock:
        rows = _rows(_c().execute(
            "SELECT payload, created_at FROM events WHERE pixel_id=? AND created_at>=? AND event_name=? "
            "AND source='pixel' AND status IN ('sent','held','released') AND client_id=? "
            "ORDER BY created_at DESC LIMIT 20",
            (config.META_PIXEL_ID, since, event_name, str(client_id))))
    for r in rows:
        try:
            r["payload"] = json.loads(r["payload"]) if r["payload"] else {}
        except ValueError:
            r["payload"] = {}
    return rows


def held_events(since: float, *, client_id: str = "", before: Optional[float] = None) -> list[dict]:
    """Storefront events held for the shopper's email (status 'held'), oldest
    first, payload decoded: one browser's, or every one older than `before`."""
    q = ("SELECT id, event_name, event_id, payload, created_at, client_id FROM events WHERE pixel_id=? "
         "AND created_at>=? AND status='held' AND source='pixel'")
    args: list = [config.META_PIXEL_ID, since]
    if client_id:
        q += " AND client_id=?"
        args.append(client_id)
    if before is not None:
        q += " AND created_at<?"
        args.append(before)
    with _lock:
        rows = _rows(_c().execute(q + " ORDER BY created_at LIMIT 200", args))
    for r in rows:
        try:
            r["payload"] = json.loads(r["payload"]) if r["payload"] else {}
        except ValueError:
            r["payload"] = {}
    return rows


def claim_held(row_id: int) -> bool:
    """Mark a held event released; False when something else already did, so it goes out once."""
    with _lock:
        return _c().execute("UPDATE events SET status='released' WHERE id=? AND status='held'",
                            (row_id,)).rowcount == 1


def sent_event_name(order_id: str) -> str:
    """The event an order already reached a dataset as (Purchase or the
    renewal event), or "" when no dataset has it yet."""
    with _lock:
        row = _c().execute("SELECT event_name FROM events WHERE order_id=? AND status='sent' "
                           f"AND {REPORTED} ORDER BY id LIMIT 1", (str(order_id),)).fetchone()
    return row["event_name"] if row else ""


def recent_events(limit: int = 50, status: Optional[str] = None,
                  event_name: Optional[str] = None) -> list[dict]:
    q, args = "SELECT * FROM events", []
    conds = []
    if status:
        conds.append("status=?"); args.append(status)
    if event_name:
        conds.append("event_name=?"); args.append(event_name)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _lock:
        return _with_payload(_rows(_c().execute(q, args)))


def events_for_order(order_id: str, limit: int = 50) -> list[dict]:
    with _lock:
        return _with_payload(_rows(_c().execute(
            "SELECT * FROM events WHERE order_id=? ORDER BY id DESC LIMIT ?",
            (str(order_id), limit))))


_NOT_RECOVERED = ("NOT (e.status='failed' AND EXISTS (SELECT 1 FROM events s WHERE "
                  "s.pixel_id=e.pixel_id AND s.event_name=e.event_name AND s.event_id=e.event_id "
                  "AND s.status='sent'))")


def event_stats(since: float, pixel_id: str = "") -> dict:
    """Counts by event and status for one dataset (the main one by default).
    A 'failed' row whose event was later sent is a recovered retry, not a
    loss, so it is left out."""
    pid = pixel_id or config.META_PIXEL_ID
    with _lock:
        rows = _rows(_c().execute(
            "SELECT e.event_name, e.status, COUNT(*) AS n FROM events e WHERE e.created_at>=? "
            f"AND e.pixel_id=? AND {_NOT_RECOVERED} "
            "GROUP BY e.event_name, e.status", (since, pid)))
        mk = _rows(_c().execute(
            "SELECT match_keys, COUNT(*) AS n FROM events WHERE created_at>=? AND pixel_id=? "
            "AND status='sent' AND event_name='Purchase' GROUP BY match_keys", (since, pid)))
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        out.setdefault(r["event_name"], {})[r["status"]] = r["n"]
    return {"by_event": out, "purchase_match_keys": mk}


# --- orders -----------------------------------------------------------------

def upsert_order(order: dict, checkout_token: str = "") -> bool:
    """Store a Shopify order for processing. Returns False if we already have it."""
    oid = str(order["id"])
    with _lock:
        if _c().execute("SELECT 1 FROM orders WHERE order_id=?", (oid,)).fetchone():
            return False
        _c().execute(
            "INSERT INTO orders (order_id, order_name, checkout_token, status, order_json, received_at) "
            "VALUES (?,?,?,?,?,?)",
            (oid, order.get("name"), checkout_token or order.get("checkout_token"),
             "pending", json.dumps(order, default=str), time.time()),
        )
        return True


def refresh_order_tags(order: dict) -> bool:
    """Copy Shopify's current tags onto a stored order that no dataset has
    received yet. Apps tag orders seconds after they are created (Kaching
    marks its rebills this way), after the webhook already delivered the
    order. Only the tags change. An order already sent anywhere (even to one
    pixel while another failed) keeps what it was reported as, so no dataset
    ever gets it both as a Purchase and as a renewal."""
    if "tags" not in order:
        return False
    oid = str(order["id"])
    with _lock:
        row = _c().execute(
            "SELECT order_json FROM orders WHERE order_id=? AND status IN ('pending','failed') "
            "AND NOT EXISTS (SELECT 1 FROM events WHERE events.order_id=orders.order_id AND events.status='sent' "
            f"AND events.{REPORTED})",
            (oid,)).fetchone()
        if not row:
            return False
        stored = json.loads(row["order_json"])
        if stored.get("tags") == order["tags"]:
            return False
        stored["tags"] = order["tags"]
        _c().execute("UPDATE orders SET order_json=? WHERE order_id=?", (json.dumps(stored, default=str), oid))
        return True


def get_order(order_id: str) -> Optional[dict]:
    with _lock:
        row = _c().execute("SELECT * FROM orders WHERE order_id=?", (str(order_id),)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["order_json"] = json.loads(d["order_json"])
    return d


def pending_orders() -> list[dict]:
    """Everything not yet delivered. There is no attempt cap: the per-order
    backoff bounds load, and classify_order retires orders once they are too
    old for Meta, so a fixed token drains the backlog on its own."""
    with _lock:
        rows = _rows(_c().execute(
            "SELECT * FROM orders WHERE status IN ('pending','failed') ORDER BY received_at ASC"))
    for r in rows:
        r["order_json"] = json.loads(r["order_json"])
    return rows


def mark_order(order_id: str, status: str, error: str = "", fbtrace_id: str = "",
               kind: str = "", count_attempt: bool = True) -> None:
    with _lock:
        _c().execute(
            "UPDATE orders SET status=?, attempts=attempts+?, last_error=?, "
            "fbtrace_id=COALESCE(NULLIF(?, ''), fbtrace_id), "
            "kind=COALESCE(NULLIF(?, ''), kind), "
            "sent_at=CASE WHEN ?='sent' THEN ? ELSE sent_at END WHERE order_id=?",
            (status, 1 if count_attempt else 0, error or None, fbtrace_id, kind,
             status, time.time(), str(order_id)),
        )


def reset_order(order_id: str, order: Optional[dict] = None, forced: bool = False,
                only_missing: bool = False) -> None:
    """Queue an order again. With `forced` (an operator's resend), every retry
    ignores the start/skip rules, so a manual resend that fails transiently
    keeps its intent, and the resend is marked: a dataset gets the event again
    until it accepts it after this moment, once, even if it had it before
    (Meta dedupes on event_id). With `only_missing`, the mark stays as it was,
    so only the datasets still missing the event get it."""
    with _lock:
        if order is not None:
            _c().execute("UPDATE orders SET order_json=? WHERE order_id=?",
                         (json.dumps(order, default=str), str(order_id)))
        mark = forced and not only_missing
        _c().execute(
            "UPDATE orders SET status='pending', attempts=0, last_error=NULL, wait_until=NULL, match_tries=0, "
            "forced=CASE WHEN ? THEN 1 ELSE forced END, "
            "resend_mark=CASE WHEN ? THEN (SELECT COALESCE(MAX(id), 0) FROM events) ELSE resend_mark END "
            "WHERE order_id=?",
            (1 if forced else 0, 1 if mark else 0, str(order_id)))


def set_order_wait(order_id: str, until: float) -> None:
    """An unmatched new sale waits for its visit to show up until `until`.
    Kept here, not in memory, so a restart doesn't cut the wait short or
    lose track of it: the send loop picks the order up again after that."""
    with _lock:
        _c().execute("UPDATE orders SET wait_until=?, match_tries=match_tries+1 WHERE order_id=?",
                     (until, str(order_id)))


def order_summary(since: float) -> dict:
    with _lock:
        rows = _rows(_c().execute(
            "SELECT status, COALESCE(kind, 'unknown') AS kind, COUNT(*) AS n FROM orders "
            "WHERE received_at>=? GROUP BY status, kind", (since,)))
        failed = _rows(_c().execute(
            "SELECT order_id, order_name, attempts, last_error FROM orders "
            "WHERE received_at>=? AND status='failed' ORDER BY received_at DESC LIMIT 20", (since,)))
        stuck = _rows(_c().execute(
            "SELECT order_id, order_name, attempts, last_error FROM orders "
            "WHERE status='pending' AND received_at<? ORDER BY received_at LIMIT 20",
            (time.time() - 1800,)))
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    skipped: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + r["n"]
        if r["status"] == "sent":
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + r["n"]
        elif r["status"] == "skipped":
            skipped[r["kind"]] = skipped.get(r["kind"], 0) + r["n"]
    return {"by_status": by_status, "sent_by_kind": by_kind, "skipped_by_reason": skipped,
            "failed": failed, "pending_over_30_min": stuck}


# --- hub queries ------------------------------------------------------------

def set_order_attribution(order_id: str, attribution: dict) -> None:
    with _lock:
        _c().execute("UPDATE orders SET attribution=? WHERE order_id=?",
                     (json.dumps(attribution, default=str), str(order_id)))


def refresh_order_identity(order_id: str, fbc: str, identity: dict) -> bool:
    """Write a sent sale's ad (attribution.sent_click_identity) into its stored
    record, only while that record still names no ad and still holds the
    click `fbc` Meta was sent. Nothing else in the record changes."""
    with _lock:
        row = _c().execute("SELECT attribution FROM orders WHERE order_id=?", (str(order_id),)).fetchone()
        try:
            rec = json.loads(row["attribution"]) if row and row["attribution"] else None
        except ValueError:
            rec = None
        if not attribution.needs_identity(rec) or rec.get("fbc") != fbc:
            return False
        rec.update({k: identity[k] for k in (*attribution.IDENTITY_KEYS, "identity_refreshed") if k in identity})
        _c().execute("UPDATE orders SET attribution=? WHERE order_id=?", (json.dumps(rec, default=str), str(order_id)))
        return True


def realign_order_attribution(order_id: str, fbc: str, record: dict) -> bool:
    """Replace a sent sale's stored credit with `record` (tracking.realign_sent),
    only while it still holds the click `fbc` Meta was sent. True when written."""
    with _lock:
        row = _c().execute("SELECT attribution FROM orders WHERE order_id=?", (str(order_id),)).fetchone()
        try:
            rec = json.loads(row["attribution"]) if row and row["attribution"] else None
        except ValueError:
            rec = None
        if not isinstance(rec, dict) or not fbc or rec.get("fbc") != fbc or record.get("fbc") != fbc:
            return False
        _c().execute("UPDATE orders SET attribution=? WHERE order_id=?",
                     (json.dumps(record, default=str), str(order_id)))
        return True


def realign_unclicked_attribution(order_id: str, record: dict) -> bool:
    """Replace the stored credit of a sale Meta got without a click (fbc '')
    that names no ad, only while it still is that. True when written."""
    with _lock:
        row = _c().execute("SELECT attribution FROM orders WHERE order_id=?", (str(order_id),)).fetchone()
        try:
            rec = json.loads(row["attribution"]) if row and row["attribution"] else None
        except ValueError:
            rec = None
        if (not isinstance(rec, dict) or "fbc" not in rec or rec.get("fbc") or rec.get("ad_id") or rec.get("ad_name")
                or record.get("fbc")):
            return False
        _c().execute("UPDATE orders SET attribution=? WHERE order_id=?",
                     (json.dumps(record, default=str), str(order_id)))
        return True


def stand_in_page_views(since: float) -> list[dict]:
    """The main dataset's storefront page views since `since` whose link held a
    stand-in for Meta's click id, oldest first: (client_id, url, created_at)."""
    with _lock:
        rows = _rows(_c().execute(
            "SELECT client_id, json_extract(payload, '$.event_source_url') AS url, created_at FROM events "
            "WHERE pixel_id=? AND created_at>=? AND event_name='PageView' AND source='pixel' "
            "AND client_id IS NOT NULL AND json_extract(payload, '$.event_source_url') LIKE '%fbclid=%' "
            "ORDER BY created_at", (config.META_PIXEL_ID, since)))
    return [r for r in rows if attribution.stand_in_fbclid(r["url"])]


def sent_purchase_fbc(order_id: str) -> str:
    """The fbc a dataset accepted with this order's Purchase, or ""."""
    with _lock:
        row = _c().execute("SELECT payload FROM events WHERE order_id=? AND status='sent' AND event_name='Purchase' "
                           "ORDER BY id LIMIT 1", (str(order_id),)).fetchone()
    try:
        p = json.loads(row["payload"]) if row and row["payload"] else {}
    except ValueError:
        return ""
    return str((p.get("user_data") or {}).get("fbc") or "") if isinstance(p, dict) else ""


def keep_sent_fbc(order_id: str, fbc: str) -> bool:
    """Write the click Meta was sent into a stored record that lacks it (one
    decided before records kept it). Nothing else in the record changes."""
    with _lock:
        row = _c().execute("SELECT attribution FROM orders WHERE order_id=?", (str(order_id),)).fetchone()
        try:
            rec = json.loads(row["attribution"]) if row and row["attribution"] else None
        except ValueError:
            rec = None
        if not isinstance(rec, dict) or rec.get("fbc") or not fbc:
            return False
        rec["fbc"] = fbc
        _c().execute("UPDATE orders SET attribution=? WHERE order_id=?", (json.dumps(rec, default=str), str(order_id)))
        return True


def orders_since(since: float, statuses: tuple = ("sent", "skipped")) -> list[dict]:
    """Stored orders received since `since` in these statuses, order JSON and
    attribution decoded, with `reported` like orders_by_id. For the attribution
    backfill and the watchdog."""
    with _lock:
        rows = _rows(_c().execute(
            "SELECT *, (SELECT e.event_name FROM events e WHERE e.order_id=orders.order_id AND e.status='sent' "
            f"AND e.{REPORTED} ORDER BY e.id LIMIT 1) AS reported FROM orders WHERE received_at>=? AND status IN "
            f"({','.join('?' * len(statuses))}) ORDER BY received_at", (since, *statuses)))
    for r in rows:
        r["order_json"] = json.loads(r["order_json"])
        r["attribution"] = json.loads(r["attribution"]) if r["attribution"] else None
    return rows


def orders_by_id(order_ids: list[str]) -> dict[str, dict]:
    """Stored orders keyed by id (without the order JSON), attribution decoded.
    `reported` is the event a dataset already accepted for it (sent_event_name)."""
    if not order_ids:
        return {}
    out: dict[str, dict] = {}
    with _lock:
        for i in range(0, len(order_ids), 500):
            chunk = [str(o) for o in order_ids[i:i + 500]]
            for r in _rows(_c().execute(
                    "SELECT order_id, order_name, status, kind, attempts, last_error, fbtrace_id, "
                    "received_at, sent_at, attribution, (SELECT e.event_name FROM events e WHERE "
                    f"e.order_id=orders.order_id AND e.status='sent' AND e.{REPORTED} ORDER BY e.id LIMIT 1) "
                    "AS reported "
                    f"FROM orders WHERE order_id IN ({','.join('?' * len(chunk))})",
                    chunk)):
                r["attribution"] = json.loads(r["attribution"]) if r["attribution"] else None
                out[r["order_id"]] = r
    return out


def sent_order_events(order_ids: list[str]) -> dict[str, dict]:
    """For each order: the datasets it reached and the Core Club match keys."""
    if not order_ids:
        return {}
    out: dict[str, dict] = {}
    with _lock:
        for i in range(0, len(order_ids), 500):
            chunk = [str(o) for o in order_ids[i:i + 500]]
            for r in _rows(_c().execute(
                    "SELECT order_id, pixel_id, event_name, match_keys, created_at FROM events "
                    f"WHERE status='sent' AND {REPORTED} AND order_id IN ({','.join('?' * len(chunk))})", chunk)):
                o = out.setdefault(r["order_id"], {"pixels": {}, "match_keys": "", "event_name": ""})
                o["pixels"][r["pixel_id"]] = r["created_at"]
                o["event_name"] = r["event_name"]
                if r["pixel_id"] == config.META_PIXEL_ID:
                    o["match_keys"] = r["match_keys"] or ""
    return out


def last_sent_at(pixel_id: str) -> Optional[float]:
    with _lock:
        row = _c().execute("SELECT MAX(created_at) AS t FROM events WHERE pixel_id=? AND status='sent'",
                           (pixel_id,)).fetchone()
    return row["t"] if row and row["t"] else None


def storefront_funnel(since: float, until: Optional[float] = None,
                      events: Optional[list[str]] = None) -> list[dict]:
    """Distinct browsers per storefront event in [since, until), with their
    session's ad data and when they first and last did that step in the range
    (`first_at`, `at`). `events` limits it to those event names."""
    names = list(events or [])
    only = f"AND e.event_name IN ({','.join('?' * len(names))}) " if names else ""
    with _lock:
        return _rows(_c().execute(
            "SELECT e.event_name, e.client_id, MIN(e.created_at) AS first_at, MAX(e.created_at) AS at, "
            "s.fbc, s.ad_params, s.ad_seen_at "
            "FROM events e LEFT JOIN sessions s ON s.client_id = e.client_id "
            "WHERE e.pixel_id=? AND e.created_at>=? AND e.created_at<? AND e.client_id IS NOT NULL "
            f"AND e.source='pixel' {only}GROUP BY e.event_name, e.client_id",
            (config.META_PIXEL_ID, since, until if until is not None else float("inf"), *names)))


def viewed_products(since: float, until: Optional[float] = None) -> dict[str, str]:
    """The first product (its title, as the pixel reported it) each browser
    viewed in [since, until), by client id."""
    with _lock:
        rows = _rows(_c().execute(
            "SELECT client_id, json_extract(payload, '$.custom_data.content_name') AS title, MIN(created_at) AS at "
            "FROM events WHERE pixel_id=? AND created_at>=? AND created_at<? AND event_name='ViewContent' "
            "AND source='pixel' AND client_id IS NOT NULL GROUP BY client_id",
            (config.META_PIXEL_ID, since, until if until is not None else float("inf"))))
    return {r["client_id"]: str(r["title"]).strip()[:200] for r in rows if r["title"]}


def first_storefront_event_at() -> Optional[float]:
    """When the main pixel's oldest storefront event still on record came in
    (the funnel counts browsers from then on), or None before any."""
    # Walks idx_events_pixel_time in time order and stops at the first match
    # (a MIN() with these extra conditions would read every row of the pixel).
    with _lock:
        row = _c().execute("SELECT created_at AS t FROM events WHERE pixel_id=? AND source='pixel' "
                           "AND client_id IS NOT NULL ORDER BY created_at LIMIT 1", (config.META_PIXEL_ID,)).fetchone()
    return row["t"] if row and row["t"] else None


def sessions_ad_data(client_ids: list[str]) -> dict[str, dict]:
    """The ad data (fbc, ad_params, ad_seen_at) of these browsers, by client id."""
    ids = list(dict.fromkeys(str(c) for c in client_ids if c))
    out: dict[str, dict] = {}
    with _lock:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in _c().execute("SELECT client_id, fbc, ad_params, ad_seen_at FROM sessions "
                                  f"WHERE client_id IN ({','.join('?' * len(chunk))})", chunk):
                out[r["client_id"]] = dict(r)
    return out


def purchase_match_keys(since: float) -> list[str]:
    with _lock:
        return [r["match_keys"] or "" for r in _c().execute(
            "SELECT match_keys FROM events WHERE created_at>=? AND pixel_id=? AND status='sent' "
            "AND event_name='Purchase'", (since, config.META_PIXEL_ID))]


def renewal_orders_sent_as_purchase(since: float) -> list[str]:
    # From the (few) renewal orders, each looked up by order id: starting from
    # events would walk every recent event. EXISTS also lists an order once,
    # though with a backup pixel it has one sent event per dataset.
    with _lock:
        return [r["order_name"] or r["order_id"] for r in _c().execute(
            "SELECT o.order_id, o.order_name FROM orders o WHERE o.kind='renewal' AND EXISTS ("
            "SELECT 1 FROM events e WHERE e.order_id=o.order_id AND e.event_name='Purchase' "
            "AND e.status='sent' AND e.created_at>=?)",
            (since,))]


def add_watchdog_run(status: str, results: list[dict]) -> None:
    now = time.time()
    with _lock:
        _c().execute("INSERT INTO watchdog_runs (run_at, status, results) VALUES (?,?,?)",
                     (now, status, json.dumps(results, default=str)))
        _c().execute("DELETE FROM watchdog_runs WHERE run_at < ?", (now - 7 * 86400,))


def watchdog_runs(since: float) -> list[dict]:
    with _lock:
        rows = _rows(_c().execute(
            "SELECT run_at, status, results FROM watchdog_runs WHERE run_at>=? ORDER BY run_at", (since,)))
    for r in rows:
        r["results"] = json.loads(r["results"])
    return rows


def add_emq_snapshot(pixel_id: str, scores: dict[str, Optional[float]]) -> None:
    now = time.time()
    with _lock:
        for event_name, score in scores.items():
            _c().execute("INSERT INTO emq_snapshots (pixel_id, event_name, score, taken_at) VALUES (?,?,?,?)",
                         (pixel_id, event_name, score, now))
        _c().execute("DELETE FROM emq_snapshots WHERE taken_at < ?", (now - 90 * 86400,))


def emq_history(pixel_id: str, event_name: str, since: float) -> list[dict]:
    with _lock:
        return _rows(_c().execute(
            "SELECT score, taken_at FROM emq_snapshots WHERE pixel_id=? AND event_name=? AND taken_at>=? "
            "ORDER BY taken_at", (pixel_id, event_name, since)))


def ad_arrivals(since: float) -> list[dict]:
    """Every Meta ad arrival the pixel recorded since `since` (the current
    click and the click history of each browser seen since then), each once."""
    with _lock:
        rows = _rows(_c().execute(
            "SELECT client_id, ad_params, ad_seen_at, ad_history FROM sessions WHERE last_seen>=?", (since,)))
    out = []
    for r in rows:
        seen: set = set()
        for v in attribution.ad_history(r["ad_history"]):
            if v["at"] >= since:
                seen.add(round(v["at"], 3))
                out.append(v)
        try:
            params = json.loads(r["ad_params"]) if r["ad_params"] else {}
        except ValueError:
            params = {}
        at = r["ad_seen_at"]
        if isinstance(params, dict) and params and at and at >= since and round(at, 3) not in seen:
            lp, stripped = attribution.landing_page(params)
            out.append({"at": at, "lp": lp, "ids_stripped": stripped, "ref": str(params.get("ref") or ""),
                        "click": bool(params.get("fbclid"))})       # a real ad click, not a hand-shared link
    return out


# --- proposals ----------------------------------------------------------------

PROPOSAL_FIELDS = "id, key, kind, title, detail, action, status, created_at, decided_at, result"


def _proposal(row) -> dict:
    d = dict(row)
    d["action"] = json.loads(d["action"]) if d.get("action") else {}
    return d


def add_proposal(key: str, kind: str, title: str, detail: str, action: dict) -> bool:
    """Suggest a fix once: a key already proposed (whatever became of it) is left alone."""
    with _lock:
        cur = _c().execute(
            "INSERT OR IGNORE INTO proposals (key, kind, title, detail, action, status, created_at) "
            "VALUES (?,?,?,?,?,'pending',?)", (key, kind, title, detail, json.dumps(action), time.time()))
        return cur.rowcount == 1


def proposals(since: float = 0.0) -> list[dict]:
    """Pending proposals, then the ones decided since `since`, newest first."""
    with _lock:
        rows = _c().execute(
            f"SELECT {PROPOSAL_FIELDS} FROM proposals WHERE status='pending' OR decided_at>=? "
            "ORDER BY status!='pending', COALESCE(decided_at, created_at) DESC LIMIT 200", (since,)).fetchall()
    return [_proposal(r) for r in rows]


def get_proposal(proposal_id: Any) -> Optional[dict]:
    with _lock:
        row = _c().execute(f"SELECT {PROPOSAL_FIELDS} FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    return _proposal(row) if row else None


def decide_proposal(proposal_id: Any, status: str, result: str = "", only_from: str = "pending") -> bool:
    """Move a proposal on from `only_from`. False when it wasn't there any more
    (decided already, or in a double click's second request)."""
    with _lock:
        cur = _c().execute("UPDATE proposals SET status=?, decided_at=?, result=? WHERE id=? AND status=?",
                           (status, time.time(), result[:500] or None, proposal_id, only_from))
        return cur.rowcount == 1


def pending_proposals(kind: str) -> list[dict]:
    with _lock:
        rows = _c().execute(f"SELECT {PROPOSAL_FIELDS} FROM proposals WHERE status='pending' AND kind=?",
                            (kind,)).fetchall()
    return [_proposal(r) for r in rows]


def extra_renewal_tags() -> set[str]:
    """Rebill tags the owner approved in the hub, on top of RENEWAL_TAGS."""
    try:
        tags = json.loads(kv_get("extra_renewal_tags") or "[]")
    except ValueError:
        return set()
    return {str(t).strip().lower() for t in tags if str(t).strip()} if isinstance(tags, list) else set()


def add_extra_renewal_tag(tag: str) -> None:
    with _lock:
        kv_set("extra_renewal_tags", json.dumps(sorted(extra_renewal_tags() | {tag.strip().lower()})))


# --- backup -------------------------------------------------------------------

def backup_to(path: str) -> None:
    """A consistent copy of the whole database in `path`, taken with SQLite's
    backup API from a connection of its own, in one step: other writers
    (WAL mode) keep going and the copy is one moment's snapshot."""
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


# --- retention --------------------------------------------------------------

def prune(now: float, session_days: int, pixel_event_days: int, order_days: int) -> dict:
    """Drop rows nothing can use any more, so a public endpoint can't fill the volume."""
    day = 86400
    with _lock:
        c = _c()
        n_sess = c.execute("DELETE FROM sessions WHERE last_seen < ?", (now - session_days * day,)).rowcount
        n_pix = c.execute("DELETE FROM events WHERE order_id IS NULL AND created_at < ?",
                          (now - pixel_event_days * day,)).rowcount
        n_ord_ev = c.execute("DELETE FROM events WHERE order_id IS NOT NULL AND created_at < ?",
                             (now - order_days * day,)).rowcount
        n_ord = c.execute("DELETE FROM orders WHERE received_at < ?", (now - order_days * day,)).rowcount
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {"sessions": n_sess, "pixel_events": n_pix, "order_events": n_ord_ev, "orders": n_ord}


# --- kv ---------------------------------------------------------------------

# --- agent chats -------------------------------------------------------------

def agent_chat(chat_id: str) -> Optional[list]:
    with _lock:
        row = _c().execute("SELECT messages FROM agent_chats WHERE id=?", (chat_id,)).fetchone()
    try:
        return json.loads(row["messages"]) if row else None
    except ValueError:
        return None


def save_agent_chat(chat_id: str, messages: list) -> None:
    now = time.time()
    with _lock:
        _c().execute("INSERT INTO agent_chats (id, created_at, updated_at, messages) VALUES (?,?,?,?) "
                     "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, messages=excluded.messages",
                     (chat_id, now, now, json.dumps(messages, default=str)))
        _c().execute("DELETE FROM agent_chats WHERE updated_at<?", (now - 30 * 86400,))


def add_agent_spend(day: str, usd: float) -> float:
    """Add to the agent's spend for a store day; returns the day's total."""
    key = f"agent_spend:{day}"
    with _lock:
        row = _c().execute("SELECT value FROM meta_kv WHERE key=?", (key,)).fetchone()
        total = round((float(row["value"]) if row else 0.0) + usd, 6)
        _c().execute("INSERT OR REPLACE INTO meta_kv (key, value) VALUES (?,?)", (key, str(total)))
    return total


STAND_IN_PREFIX = "stand_in_click:"


def note_stand_in_click(params: dict, value: str) -> None:
    """Count a visit from an ad whose link held a stand-in for Meta's click id,
    per campaign, for the watchdog (names and counts only)."""
    camp = str(params.get("campaign_id") or params.get("utm_campaign") or "unknown")[:80]
    key = STAND_IN_PREFIX + camp
    now = time.time()
    with _lock:
        row = _c().execute("SELECT value FROM meta_kv WHERE key=?", (key,)).fetchone()
        try:
            rec = json.loads(row["value"]) if row else {}
        except ValueError:
            rec = {}
        if not isinstance(rec, dict) or now - float(rec.get("last") or 0) > 86400:
            rec = {"n": 0, "first": now}                 # a day without one starts the count again
        ads = [a for a in (rec.get("ads") or []) if isinstance(a, str)]
        ad = str(params.get("utm_content") or params.get("ad_id") or "")[:80]
        if ad and ad not in ads and len(ads) < 5:
            ads.append(ad)
        rec.update(n=int(rec.get("n") or 0) + 1, last=now, value=str(value)[:40], ads=ads,
                   campaign=str(params.get("utm_campaign") or "")[:80], campaign_id=str(params.get("campaign_id") or "")[:40])
        _c().execute("INSERT OR REPLACE INTO meta_kv (key, value) VALUES (?,?)", (key, json.dumps(rec)))


def stand_in_clicks(since: float) -> list[dict]:
    """The campaigns noted by note_stand_in_click since `since`, most visits first."""
    with _lock:
        rows = _c().execute("SELECT value FROM meta_kv WHERE key LIKE ?", (STAND_IN_PREFIX + "%",)).fetchall()
    out = []
    for r in rows:
        try:
            rec = json.loads(r["value"])
        except ValueError:
            continue
        if isinstance(rec, dict) and float(rec.get("last") or 0) >= since:
            out.append(rec)
    return sorted(out, key=lambda x: -int(x.get("n") or 0))


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Rows of one read, as dicts (the Backend tab's tables)."""
    with _lock:
        return _rows(_c().execute(sql, params))


def run(sql: str, params: tuple = ()) -> None:
    """One write (the Backend tab's tables)."""
    with _lock:
        _c().execute(sql, params)


def kv_get(key: str) -> Optional[str]:
    with _lock:
        row = _c().execute("SELECT value FROM meta_kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def kv_set(key: str, value: str) -> None:
    with _lock:
        _c().execute("INSERT OR REPLACE INTO meta_kv (key, value) VALUES (?,?)", (key, value))
