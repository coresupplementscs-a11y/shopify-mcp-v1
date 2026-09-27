"""
SQLite persistence. One file on the Railway volume. Three tables:

  sessions  – what the pixel told us about a browser (fbp/fbc/ip/ua/contact),
              keyed by Shopify's client id and, once known, the checkout token.
  events    – every event we sent (or tried to send) to Meta, with the trace id.
  orders    – Shopify orders that must produce a Purchase, and where they stand.
"""
import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional

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
    last_seen      REAL NOT NULL
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
    status      TEXT NOT NULL,            -- sent | failed
    fbtrace_id  TEXT,
    error       TEXT,
    match_keys  TEXT,                     -- comma list of user_data keys we had
    order_id    TEXT,
    payload     TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_order ON events(order_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_dedup ON events(event_name, event_id, status);

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
    sent_at        REAL
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

CREATE TABLE IF NOT EXISTS meta_kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Columns added after the first release; applied to existing volumes on boot.
MIGRATIONS = {
    ("orders", "kind"): "ALTER TABLE orders ADD COLUMN kind TEXT",
    ("orders", "forced"): "ALTER TABLE orders ADD COLUMN forced INTEGER NOT NULL DEFAULT 0",
}


def init() -> None:
    global _conn
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.executescript(SCHEMA)
    for (table, column), ddl in MIGRATIONS.items():
        cols = {r[1] for r in _conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            _conn.execute(ddl)


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
                  "phone", "first_name", "last_name", "landing_url")


def upsert_session(client_id: str, **fields: Any) -> dict:
    """Merge new facts about a browser into its session. Never overwrite a
    known value with an empty one; the pixel always sends the current cookies,
    so a newer ad click replaces the stored fbc."""
    now = time.time()
    with _lock:
        row = _c().execute("SELECT * FROM sessions WHERE client_id=?", (client_id,)).fetchone()
        cur = dict(row) if row else {"client_id": client_id, "first_seen": now}
        for k in SESSION_FIELDS:
            v = fields.get(k)
            if v:
                cur[k] = v
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

def record_event(event_name: str, event_id: str, source: str, status: str,
                 payload: dict, fbtrace_id: str = "", error: str = "",
                 order_id: str = "") -> None:
    match_keys = ",".join(sorted(k for k, v in (payload.get("user_data") or {}).items() if v))
    with _lock:
        _c().execute(
            "INSERT OR REPLACE INTO events (event_name, event_id, source, status, fbtrace_id, error, "
            "match_keys, order_id, payload, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (event_name, event_id, source, status, fbtrace_id, error, match_keys,
             order_id or None, json.dumps(payload, default=str), time.time()),
        )


def event_already_sent(event_name: str, event_id: str) -> bool:
    with _lock:
        row = _c().execute(
            "SELECT 1 FROM events WHERE event_name=? AND event_id=? AND status='sent' LIMIT 1",
            (event_name, event_id),
        ).fetchone()
        return row is not None


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


def event_stats(since: float) -> dict:
    """Counts by event and status. A 'failed' row whose event was later sent is
    a recovered retry, not a loss, so it is left out."""
    with _lock:
        rows = _rows(_c().execute(
            "SELECT e.event_name, e.status, COUNT(*) AS n FROM events e WHERE e.created_at>=? "
            "AND NOT (e.status='failed' AND EXISTS (SELECT 1 FROM events s WHERE "
            "s.event_name=e.event_name AND s.event_id=e.event_id AND s.status='sent')) "
            "GROUP BY e.event_name, e.status", (since,)))
        mk = _rows(_c().execute(
            "SELECT match_keys, COUNT(*) AS n FROM events WHERE created_at>=? AND status='sent' "
            "AND event_name='Purchase' GROUP BY match_keys", (since,)))
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


def reset_order(order_id: str, order: Optional[dict] = None, forced: bool = False) -> None:
    """Queue an order again. With `forced`, every retry ignores the start/skip
    rules, so a manual resend that fails transiently keeps its intent."""
    with _lock:
        if order is not None:
            _c().execute("UPDATE orders SET order_json=? WHERE order_id=?",
                         (json.dumps(order, default=str), str(order_id)))
        _c().execute(
            "UPDATE orders SET status='pending', attempts=0, last_error=NULL, "
            "forced=CASE WHEN ? THEN 1 ELSE forced END WHERE order_id=?",
            (1 if forced else 0, str(order_id)))


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

def kv_get(key: str) -> Optional[str]:
    with _lock:
        row = _c().execute("SELECT value FROM meta_kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def kv_set(key: str, value: str) -> None:
    with _lock:
        _c().execute("INSERT OR REPLACE INTO meta_kv (key, value) VALUES (?,?)", (key, value))
