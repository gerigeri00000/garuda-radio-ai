"""
GARUDA Radio AI - database SQLite lokal.

Tabel:
  transcripts  : hasil transkripsi audio (dedupe via hash audio)
  reports      : laporan hasil AI (atau belum terklasifikasi)
  tickets      : tiket / permintaan (draft Pending -> Approved -> ... )
  alerts       : peringatan operator
  map_updates  : pembaruan peta (pending approval -> applied/rejected)
  events_log   : audit trail semua tindakan
  locations    : lokasi simulasi + status (DEMO DATA)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime
from typing import Any, Iterable

import config

_LOCK = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audio_hash TEXT UNIQUE,
    audio_path TEXT,
    source TEXT,
    text TEXT NOT NULL,
    language TEXT,
    duration REAL,
    language_probability REAL,
    avg_logprob REAL,
    transcribe_seconds REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id INTEGER,
    category TEXT NOT NULL,
    unit TEXT,
    summary TEXT,
    item TEXT,
    quantity REAL,
    unit_measure TEXT,
    location TEXT,
    location_key TEXT,
    priority TEXT,
    action TEXT,
    confidence REAL,
    needs_approval INTEGER DEFAULT 0,
    needs_verification INTEGER DEFAULT 0,
    missing_info TEXT,          -- JSON array
    llm_raw TEXT,               -- JSON mentah dari LLM (audit)
    llm_valid INTEGER DEFAULT 1,
    llm_seconds REAL,
    status TEXT DEFAULT 'NEW',  -- NEW | REVIEW | VERIFIED | ARCHIVED
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER,
    kind TEXT,
    item TEXT,
    quantity REAL,
    unit_measure TEXT,
    requester TEXT,
    priority TEXT,
    status TEXT DEFAULT 'Pending',  -- Pending|Approved|In Progress|Completed|Rejected
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER,
    category TEXT,
    priority TEXT,
    message TEXT NOT NULL,
    location TEXT,
    status TEXT DEFAULT 'Needs Review',  -- Needs Review | Acknowledged | Resolved
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS map_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER,
    location_key TEXT,
    location_name TEXT,
    new_status TEXT,
    note TEXT,
    status TEXT DEFAULT 'Pending Approval',  -- Pending Approval | Applied | Rejected
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    entity_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT,
    actor TEXT DEFAULT 'system',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS locations (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS settings_kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _LOCK, connect() as conn:
        conn.executescript(SCHEMA)
        # seed lokasi simulasi (hanya bila belum ada)
        for key, loc in config.DEMO_LOCATIONS.items():
            conn.execute(
                "INSERT OR IGNORE INTO locations(key,name,lat,lon,kind,status,note)"
                " VALUES(?,?,?,?,?,?,?)",
                (key, loc["name"], loc["lat"], loc["lon"], loc["kind"],
                 "Normal" if loc["kind"] != "infrastruktur" else "Normal", loc["note"]),
            )
        # tabel virtual FTS5 untuk pencarian riwayat (opsional)
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(text, content='transcripts', content_rowid='id')"
            )
        except sqlite3.OperationalError:
            pass


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def _rows(rs: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rs]


def log(entity: str, entity_id: int | None, action: str, detail: Any = None, actor: str = "system") -> None:
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False) if detail is not None else None
    with _LOCK, connect() as conn:
        conn.execute(
            "INSERT INTO events_log(entity,entity_id,action,detail,actor,created_at) VALUES(?,?,?,?,?,?)",
            (entity, entity_id, action, detail, actor, now()),
        )


def recent_log(limit: int = 40) -> list[dict]:
    with connect() as conn:
        return _rows(conn.execute(
            "SELECT * FROM events_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall())


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------
def audio_hash(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_transcript_by_hash(ahash: str) -> dict | None:
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM transcripts WHERE audio_hash=?", (ahash,)).fetchone())


def add_transcript(**kw) -> int:
    with _LOCK, connect() as conn:
        cur = conn.execute(
            """INSERT INTO transcripts(audio_hash,audio_path,source,text,language,duration,
               language_probability,avg_logprob,transcribe_seconds,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (kw.get("audio_hash"), kw.get("audio_path"), kw.get("source"), kw["text"],
             kw.get("language"), kw.get("duration"), kw.get("language_probability"),
             kw.get("avg_logprob"), kw.get("transcribe_seconds"), now()),
        )
        task_id = cur.lastrowid
        try:
            conn.execute("INSERT INTO transcripts_fts(rowid,text) VALUES(?,?)", (task_id, kw["text"]))
        except sqlite3.OperationalError:
            pass
        conn.commit()
        return task_id  # type: ignore[return-value]


def search_transcripts(q: str, limit: int = 20) -> list[dict]:
    with connect() as conn:
        return _rows(conn.execute(
            "SELECT * FROM transcripts WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{q}%", limit)).fetchall())


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
REPORT_FIELDS = (
    "transcript_id", "category", "unit", "summary", "item", "quantity", "unit_measure",
    "location", "location_key", "priority", "action", "confidence", "needs_approval",
    "needs_verification", "missing_info", "llm_raw", "llm_valid", "llm_seconds", "status",
)


def add_report(**kw) -> int:
    data = {k: kw.get(k) for k in REPORT_FIELDS}
    data["missing_info"] = json.dumps(data["missing_info"] or [], ensure_ascii=False)
    data["needs_approval"] = int(bool(data["needs_approval"]))
    data["needs_verification"] = int(bool(data["needs_verification"]))
    data["llm_valid"] = int(bool(data["llm_valid"])) if data["llm_valid"] is not None else 1
    data["status"] = data["status"] or "NEW"
    data["created_at"] = now()
    cols = ", ".join(data.keys())
    ph = ", ".join(["?"] * len(data))
    with _LOCK, connect() as conn:
        cur = conn.execute(f"INSERT INTO reports({cols}) VALUES({ph})", tuple(data.values()))
        rid = cur.lastrowid
        conn.commit()
        return rid  # type: ignore[return-value]


def get_report(report_id: int) -> dict | None:
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone())


def list_reports(limit: int = 100, category: str | None = None,
                 date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    sql = "SELECT * FROM reports WHERE 1=1"
    args: list[Any] = []
    if category and category != "Semua":
        sql += " AND category=?"
        args.append(category)
    if date_from:
        sql += " AND date(created_at) >= date(?)"
        args.append(date_from)
    if date_to:
        sql += " AND date(created_at) <= date(?)"
        args.append(date_to)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return _rows(conn.execute(sql, args).fetchall())


def set_report_status(report_id: int, status: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("UPDATE reports SET status=? WHERE id=?", (status, report_id))
        conn.commit()


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------
TICKET_STATUSES = ["Pending", "Approved", "In Progress", "Completed", "Rejected"]


def add_ticket(**kw) -> int:
    ts = now()
    with _LOCK, connect() as conn:
        cur = conn.execute(
            """INSERT INTO tickets(report_id,kind,item,quantity,unit_measure,requester,
               priority,status,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (kw.get("report_id"), kw.get("kind"), kw.get("item"), kw.get("quantity"),
             kw.get("unit_measure"), kw.get("requester"), kw.get("priority"),
             kw.get("status") or "Pending", kw.get("notes"), ts, ts),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]


def list_tickets(limit: int = 200) -> list[dict]:
    with connect() as conn:
        return _rows(conn.execute("SELECT * FROM tickets ORDER BY id DESC LIMIT ?", (limit,)).fetchall())


def get_ticket(ticket_id: int) -> dict | None:
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone())


def update_ticket_status(ticket_id: int, status: str, actor: str = "operator") -> None:
    with _LOCK, connect() as conn:
        conn.execute("UPDATE tickets SET status=?, updated_at=? WHERE id=?", (status, now(), ticket_id))
        conn.commit()
    log("ticket", ticket_id, "status_change", {"to": status}, actor)


def open_tickets() -> list[dict]:
    with connect() as conn:
        return _rows(conn.execute(
            "SELECT * FROM tickets WHERE status NOT IN ('Completed','Rejected') ORDER BY id").fetchall())


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
ALERT_STATUSES = ["Needs Review", "Acknowledged", "Resolved"]


def add_alert(**kw) -> int:
    ts = now()
    with _LOCK, connect() as conn:
        cur = conn.execute(
            "INSERT INTO alerts(report_id,category,priority,message,location,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (kw.get("report_id"), kw.get("category"), kw.get("priority"), kw["message"],
             kw.get("location"), kw.get("status") or "Needs Review", ts, ts),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]


def list_alerts(limit: int = 100) -> list[dict]:
    with connect() as conn:
        return _rows(conn.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall())


def update_alert_status(alert_id: int, status: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("UPDATE alerts SET status=?, updated_at=? WHERE id=?", (status, now(), alert_id))
        conn.commit()


# ---------------------------------------------------------------------------
# Map updates
# ---------------------------------------------------------------------------
def add_map_update(**kw) -> int:
    ts = now()
    with _LOCK, connect() as conn:
        cur = conn.execute(
            "INSERT INTO map_updates(report_id,location_key,location_name,new_status,note,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (kw.get("report_id"), kw.get("location_key"), kw.get("location_name"),
             kw.get("new_status"), kw.get("note"), kw.get("status") or "Pending Approval", ts, ts),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]


def list_map_updates(limit: int = 100) -> list[dict]:
    with connect() as conn:
        return _rows(conn.execute("SELECT * FROM map_updates ORDER BY id DESC LIMIT ?", (limit,)).fetchall())


def get_map_update(update_id: int) -> dict | None:
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM map_updates WHERE id=?", (update_id,)).fetchone())


def resolve_map_update(update_id: int, approve: bool, actor: str = "operator") -> None:
    upd = get_map_update(update_id)
    if not upd or upd["status"] != "Pending Approval":
        return
    status = "Applied" if approve else "Rejected"
    with _LOCK, connect() as conn:
        conn.execute("UPDATE map_updates SET status=?, updated_at=? WHERE id=?", (status, now(), update_id))
        if approve:
            conn.execute("UPDATE locations SET status=? WHERE key=?", (upd["new_status"], upd["location_key"]))
        conn.commit()
    log("map_update", update_id, "approved" if approve else "rejected",
        {"location": upd["location_name"], "status": upd["new_status"]}, actor)


def list_locations() -> list[dict]:
    with connect() as conn:
        return _rows(conn.execute("SELECT * FROM locations ORDER BY kind, name").fetchall())


# ---------------------------------------------------------------------------
# Dashboard stats
# ---------------------------------------------------------------------------
def stats() -> dict:
    with connect() as conn:
        q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
        return {
            "reports": q("SELECT COUNT(*) FROM reports"),
            "reports_today": q("SELECT COUNT(*) FROM reports WHERE date(created_at)=date('now','localtime')"),
            "tickets_open": q("SELECT COUNT(*) FROM tickets WHERE status NOT IN ('Completed','Rejected')"),
            "tickets_pending": q("SELECT COUNT(*) FROM tickets WHERE status='Pending'"),
            "alerts_open": q("SELECT COUNT(*) FROM alerts WHERE status!='Resolved'"),
            "alerts_new": q("SELECT COUNT(*) FROM alerts WHERE status='Needs Review'"),
            "map_pending": q("SELECT COUNT(*) FROM map_updates WHERE status='Pending Approval'"),
            "needs_verification": q("SELECT COUNT(*) FROM reports WHERE needs_verification=1"),
        }


def category_counts(date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    sql = "SELECT category, COUNT(*) AS n FROM reports WHERE 1=1"
    args: list[Any] = []
    if date_from:
        sql += " AND date(created_at) >= date(?)"
        args.append(date_from)
    if date_to:
        sql += " AND date(created_at) <= date(?)"
        args.append(date_to)
    sql += " GROUP BY category ORDER BY n DESC"
    with connect() as conn:
        return _rows(conn.execute(sql, args).fetchall())


def latest_status() -> dict:
    """Status ringkas untuk dashboard (laporan, tiket, alert terbaru)."""
    with connect() as conn:
        rep = _row(conn.execute("SELECT * FROM reports ORDER BY id DESC LIMIT 1").fetchone())
        tic = _row(conn.execute("SELECT * FROM tickets ORDER BY id DESC LIMIT 1").fetchone())
        alr = _row(conn.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 1").fetchone())
    return {"report": rep, "ticket": tic, "alert": alr}
