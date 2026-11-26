"""SQLite persistence layer with FTS5 full-text search.

Schema is intentionally small and explicit so the schema is easy to verify
in tests. All multi-statement setup runs inside one transaction.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import config

# Thread-local connections so the DB can be shared across the CLI thread and
# the test runner without "objects created in one thread" errors.
_connections: "Dict[int, sqlite3.Connection]" = {}
_lock = threading.Lock()

# Phase 2 provenance tiers, in descending authority:
#   manufacturer-spec    : from the part's official datasheet / app note
#   verified-reference   : textbook / verified technical reference
#   technician-confirmed : technician-confirmed repair case (learning)
#   community            : technician-reported forum/community observation
#   unverified           : unverified hypothesis or community suggestion
#   general-principle    : general electrical-engineering law (Ohm, KVL...)
# Map legacy `provenance` strings (from Phase 1 KB JSON) to these tiers.
_PROVENANCE_TO_TIER = {
    "general-electrical-law": "general-principle",
    "manufacturer-app-note": "manufacturer-spec",
    "repair-forum-summary": "community",
    "verified-reference": "verified-reference",
}


SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        board_model TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        notes TEXT DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS symptoms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS measurements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        value REAL,
        unit TEXT,
        raw_text TEXT,
        point TEXT,
        reference TEXT,
        board_state TEXT,
        adapter_state TEXT,
        battery_state TEXT,
        note TEXT,
        taken_at TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        original_path TEXT,
        stored_path TEXT NOT NULL,
        file_name TEXT NOT NULL,
        file_type TEXT,
        sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        status TEXT NOT NULL,
        meta TEXT DEFAULT '{}',
        imported_at TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER NOT NULL,
        seq INTEGER NOT NULL,
        page INTEGER,
        text TEXT NOT NULL,
        FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hypotheses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        rank INTEGER NOT NULL,
        fault TEXT NOT NULL,
        rationale TEXT,
        supporting TEXT,
        contradicting TEXT,
        confidence REAL DEFAULT 0.0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS missing_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        item TEXT NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS next_tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        test TEXT NOT NULL,
        where_to_measure TEXT,
        reference_point TEXT,
        expected TEXT,
        rationale TEXT,
        priority INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS timeline (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        event_at TEXT NOT NULL,
        kind TEXT NOT NULL,
        text TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        topic TEXT NOT NULL,
        fact TEXT NOT NULL,
        provenance TEXT,
        confidence TEXT,
        source_url TEXT,
        source_title TEXT,
        source_date TEXT,
        retrieved_at TEXT,
        tags TEXT
    )
    """,
    # FTS5 over document chunks. "content" pointing to the source column lets
    # SQLite keep chunks and index in sync.
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
        text, document_id UNINDEXED, page UNINDEXED,
        content='document_chunks', content_rowid='id'
    )
    """,
    # Triggers keep FTS in sync with the chunks table.
    """
    CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON document_chunks BEGIN
        INSERT INTO chunks_fts(rowid, text, document_id, page)
        VALUES (new.id, new.text, new.document_id, new.page);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON document_chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, text, document_id, page)
        VALUES('delete', old.id, old.text, old.document_id, old.page);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON document_chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, text, document_id, page)
        VALUES('delete', old.id, old.text, old.document_id, old.page);
        INSERT INTO chunks_fts(rowid, text, document_id, page)
        VALUES (new.id, new.text, new.document_id, new.page);
    END
    """,
    # FTS5 over knowledge facts for keyword lookup without the LLM.
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
        fact, category UNINDEXED, topic UNINDEXED,
        content='knowledge_facts', content_rowid='id'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge_facts BEGIN
        INSERT INTO knowledge_fts(rowid, fact, category, topic)
        VALUES (new.id, new.fact, new.category, new.topic);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge_facts BEGIN
        INSERT INTO knowledge_fts(knowledge_fts, rowid, fact, category, topic)
        VALUES('delete', old.id, old.fact, new.category, new.topic);
    END
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_meas_session ON measurements(session_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_docs_session ON documents(session_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_hyp_session ON hypotheses(session_id);
    """,
]

# Note: SQLite trigger referencing new.* in AFTER DELETE is illegal; fix below.
SCHEMA_SQL = [
    s.replace(
        "INSERT INTO knowledge_fts(knowledge_fts, rowid, fact, category, topic)\n        VALUES('delete', old.id, old.fact, new.category, new.topic);",
        "INSERT INTO knowledge_fts(knowledge_fts, rowid, fact, category, topic)\n        VALUES('delete', old.id, old.fact, old.category, old.topic);",
    )
    if "knowledge_ad" in s else s
    for s in SCHEMA_SQL
]


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """Return a thread-local connection with foreign keys on."""
    import os
    tid = os.getpid() ^ threading.get_ident()
    if tid in _connections:
        return _connections[tid]
    p = str(path or config.db_path())
    conn = sqlite3.connect(p, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    _connections[tid] = conn
    return conn


def init_db(path: Optional[Path] = None) -> None:
    """Create every table, index, trigger and FTS table if missing."""
    conn = connect(path)
    cur = conn.cursor()
    if not fts5_available():
        raise RuntimeError(
            "SQLite FTS5 is not available in this build. "
            "Install pysqlite3 or use a Python build with FTS5 enabled."
        )
    for stmt in SCHEMA_SQL:
        try:
            cur.execute(stmt)
        except sqlite3.Error as e:
            # Triggers may already exist after a partial run; ignore.
            if "already exists" not in str(e):
                raise
    conn.commit()
    # Apply Phase 2+ migrations on top of the base schema. This is
    # idempotent (statements that fail with duplicate-column/already-
    # exists are swallowed). Imported lazily to avoid a circular import.
    try:
        from . import migrations as _m
        _m.migrate()
    except Exception:
        # Migrations must never block startup. If they fail, the app
        # still runs on the base schema; the user can run `db recover`.
        pass


def now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def touch_session(session_id: int) -> None:
    """Update a session's updated_at timestamp."""
    execute(
        "UPDATE sessions SET updated_at=? WHERE id=?",
        (now_iso(), session_id),
    )


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    conn = connect()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    row = connect().execute(sql, tuple(params)).fetchone()
    return dict(row) if row else None


def query_all(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in connect().execute(sql, tuple(params)).fetchall()]


# --- High-level helpers ---------------------------------------------------

def add_timeline(session_id: int, kind: str, text: str) -> None:
    execute(
        "INSERT INTO timeline(session_id, event_at, kind, text) VALUES(?,?,?,?)",
        (session_id, now_iso(), kind, text),
    )


def import_fact(category: str, topic: str, fact: str, provenance: str,
                 confidence: str = "verified", source_url: str = "",
                 source_title: str = "", source_date: str = "",
                 retrieved_at: str = "", tags: str = "",
                 tier: str = "") -> int:
    # tier column is added by migration v1; default to provenance-derived
    # tier if not specified, falling back to "verified-reference".
    if not tier:
        tier = _PROVENANCE_TO_TIER.get(provenance, "verified-reference")
    # Use the tier column only if it exists (migration has run).
    conn = connect()
    has_tier = any(c["name"] == "tier" for c in conn.execute(
        "PRAGMA table_info(knowledge_facts)").fetchall())
    if has_tier:
        cur = execute(
            """INSERT INTO knowledge_facts(category, topic, fact, provenance,
               confidence, source_url, source_title, source_date, retrieved_at,
               tags, tier)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (category, topic, fact, provenance, confidence, source_url,
             source_title, source_date, retrieved_at, tags, tier),
        )
    else:
        cur = execute(
            """INSERT INTO knowledge_facts(category, topic, fact, provenance,
               confidence, source_url, source_title, source_date, retrieved_at, tags)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (category, topic, fact, provenance, confidence, source_url,
             source_title, source_date, retrieved_at, tags),
        )
    return int(cur.lastrowid)


def count_facts() -> int:
    r = query_one("SELECT COUNT(*) AS n FROM knowledge_facts")
    return int(r["n"]) if r else 0


def fts5_available() -> bool:
    try:
        connect().execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)"
        )
        connect().execute("DROP TABLE IF EXISTS _fts5_probe")
        return True
    except sqlite3.Error:
        return False
