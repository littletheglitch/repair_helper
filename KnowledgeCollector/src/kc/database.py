"""SQLite database + migrations + FTS5 for the Knowledge Collector.

The Collector's database is separate from the Electronics Diagnostic
Assistant's database. Schema is versioned via ``PRAGMA user_version``;
migrations are idempotent.

Tables:
- sources            configured sources
- records            collected records (forum posts / answers / articles)
- records_fts        FTS5 over records.text (auto-maintained)
- documents          collected documents (PDF/HTML/text)
- dedup_index        content_hash -> record_id mapping
- crawl_state        per-source resume state
- source_manifest    license / terms / attribution per source
- export_runs        log of export operations
- rejections         why items were dropped
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import config

# Thread-local connections.
_connections: Dict[int, sqlite3.Connection] = {}
_lock = threading.Lock()


SCHEMA_VERSION = 1


SCHEMA_SQL: List[str] = [
    """CREATE TABLE IF NOT EXISTS sources (
        name TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        base_url TEXT NOT NULL,
        enabled INTEGER DEFAULT 0,
        license TEXT,
        terms_url TEXT,
        attribution TEXT,
        access_method TEXT,
        max_depth INTEGER DEFAULT 5,
        rate_per_min INTEGER DEFAULT 6,
        max_records INTEGER DEFAULT 5000,
        max_bytes INTEGER DEFAULT 524288000,
        added_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_name TEXT NOT NULL,
        url TEXT NOT NULL,
        thread_id TEXT,
        post_id TEXT,
        title TEXT,
        author TEXT,
        published_at TEXT,
        collected_at TEXT NOT NULL,
        text TEXT NOT NULL,
        structured TEXT,                  -- JSON
        classification TEXT,             -- JSON
        content_hash TEXT NOT NULL,
        normalized_hash TEXT NOT NULL,
        also_seen_at TEXT,               -- JSON list of cross-source dup URLs
        variants TEXT,                   -- JSON list of near-dup text variants
        status TEXT DEFAULT 'new',
        rejection_reason TEXT DEFAULT ''
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_records_url ON records(source_name, url)""",
    """CREATE INDEX IF NOT EXISTS idx_records_hash ON records(content_hash)""",
    """CREATE INDEX IF NOT EXISTS idx_records_nhash ON records(normalized_hash)""",
    """CREATE INDEX IF NOT EXISTS idx_records_source ON records(source_name)""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
        text, title, source_name UNINDEXED,
        content='records', content_rowid='id'
    )""",
    # FTS insert trigger
    """CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
        INSERT INTO records_fts(rowid, text, title, source_name)
        VALUES (new.id, new.text, COALESCE(new.title,''), new.source_name);
    END;""",
    # FTS delete trigger
    """CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
        INSERT INTO records_fts(records_fts, rowid, text, title, source_name)
        VALUES ('delete', old.id, old.text, COALESCE(old.title,''), old.source_name);
    END;""",
    """CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_name TEXT NOT NULL,
        url TEXT NOT NULL,
        filename TEXT NOT NULL,
        file_type TEXT,
        sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        status TEXT NOT NULL,
        meta TEXT,
        collected_at TEXT NOT NULL,
        original_path TEXT,
        extracted_text_path TEXT
    )""",
    """CREATE INDEX IF NOT EXISTS idx_docs_source ON documents(source_name)""",
    """CREATE INDEX IF NOT EXISTS idx_docs_sha ON documents(sha256)""",
    """CREATE TABLE IF NOT EXISTS dedup_index (
        content_hash TEXT NOT NULL,
        normalized_hash TEXT NOT NULL,
        record_id INTEGER NOT NULL,
        source_name TEXT NOT NULL,
        PRIMARY KEY (content_hash, record_id)
    )""",
    """CREATE TABLE IF NOT EXISTS crawl_state (
        source_name TEXT PRIMARY KEY,
        last_url TEXT,
        last_page INTEGER DEFAULT 0,
        last_run_at TEXT,
        status TEXT DEFAULT 'idle',
        error TEXT,
        items_collected INTEGER DEFAULT 0,
        items_rejected INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS source_manifest (
        source_name TEXT PRIMARY KEY,
        license TEXT,
        terms_url TEXT,
        access_method TEXT,
        collected_at TEXT NOT NULL,
        permitted_storage TEXT,
        attribution TEXT,
        notes TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS export_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_at TEXT NOT NULL,
        format TEXT NOT NULL,
        file_count INTEGER,
        record_count INTEGER,
        schema_version INTEGER,
        output_dir TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS rejections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_name TEXT NOT NULL,
        url TEXT NOT NULL,
        reason TEXT NOT NULL,
        detail TEXT,
        collected_at TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_rej_source ON rejections(source_name)""",
]


# Migration v1 adds nothing beyond the base schema (the schema is new in v1),
# but we keep the slot so future migrations are versioned.
MIGRATIONS: List[Tuple[int, List[str], List[str], str]] = [
    (
        1,
        [],   # base schema already creates everything
        [],
        "Initial schema (sources, records, records_fts, documents, "
        "dedup_index, crawl_state, source_manifest, export_runs, rejections).",
    ),
]


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (or reuse) a thread-local connection."""
    tid = threading.get_ident()
    with _lock:
        conn = _connections.get(tid)
        if conn is not None:
            return conn
        p = str(path or config.db_path())
        conn = sqlite3.connect(p, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        _connections[tid] = conn
        return conn


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    return connect().execute(sql, tuple(params))


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    r = connect().execute(sql, tuple(params)).fetchone()
    return dict(r) if r else None


def query_all(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in connect().execute(sql, tuple(params)).fetchall()]


def current_version() -> int:
    return int(connect().execute("PRAGMA user_version").fetchone()[0])


def set_version(v: int) -> None:
    connect().execute(f"PRAGMA user_version = {int(v)}")


def fts5_available() -> bool:
    try:
        connect().execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS _kc_fts5_probe USING fts5(x)"
        )
        connect().execute("DROP TABLE _kc_fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def init_db(path: Optional[Path] = None) -> None:
    """Create the database, all tables, FTS5 tables, triggers, and run
    pending migrations."""
    config.ensure_layout()
    conn = connect(path)
    if not fts5_available():
        raise RuntimeError("SQLite FTS5 is not available in this build.")
    for stmt in SCHEMA_SQL:
        try:
            # Triggers (CREATE TRIGGER ... BEGIN ... END) must run via
            # executescript because execute() mishandles the trailing END
            # when isolation_level=None.
            if "CREATE TRIGGER" in stmt.upper():
                conn.executescript(stmt)
            else:
                conn.execute(stmt)
        except sqlite3.Error as e:
            if "already exists" not in str(e):
                raise
    # Run migrations (idempotent).
    if current_version() < SCHEMA_VERSION:
        for ver, ups, _downs, _desc in MIGRATIONS:
            if ver <= current_version():
                continue
            for stmt in ups:
                try:
                    conn.execute(stmt)
                except sqlite3.Error as e:
                    if "already exists" not in str(e) and "duplicate column" not in str(e):
                        raise
            set_version(ver)


def now_iso() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---- High-level helpers ----------------------------------------------

def upsert_source(name: str, kind: str, base_url: str, *,
                  enabled: bool = False, license: str = "", terms_url: str = "",
                  attribution: str = "", access_method: str = "",
                  max_depth: int = 5, rate_per_min: int = 6,
                  max_records: int = 5000, max_bytes: int = 524288000) -> None:
    execute(
        "INSERT OR REPLACE INTO sources(name, kind, base_url, enabled, "
        "license, terms_url, attribution, access_method, max_depth, "
        "rate_per_min, max_records, max_bytes, added_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, kind, base_url, int(enabled), license, terms_url, attribution,
         access_method, max_depth, rate_per_min, max_records, max_bytes,
         now_iso()),
    )


def get_source(name: str) -> Optional[Dict[str, Any]]:
    return query_one("SELECT * FROM sources WHERE name=?", (name,))


def list_sources(enabled_only: bool = False) -> List[Dict[str, Any]]:
    if enabled_only:
        return query_all("SELECT * FROM sources WHERE enabled=1 ORDER BY name")
    return query_all("SELECT * FROM sources ORDER BY name")


def enable_source(name: str, enabled: bool) -> None:
    execute("UPDATE sources SET enabled=? WHERE name=?", (int(enabled), name))


def store_record(rec) -> int:
    """Insert a record; returns the row id. The records_fts trigger fires
    automatically. If (source_name, url) already exists, the row is
    replaced (re-crawl updates in place)."""
    from .schemas import Record, Classification
    if not isinstance(rec, Record):
        raise TypeError("store_record expects a Record")
    cls_json = json.dumps(rec.classification.to_dict(), ensure_ascii=False) if rec.classification else None
    struct_json = json.dumps(rec.structured.to_dict(), ensure_ascii=False)
    cur = execute(
        "INSERT OR REPLACE INTO records(source_name, url, thread_id, post_id, "
        "title, author, published_at, collected_at, text, structured, "
        "classification, content_hash, normalized_hash, also_seen_at, "
        "variants, status, rejection_reason) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rec.source_name, rec.url, rec.thread_id, rec.post_id, rec.title,
         rec.author, rec.published_at, rec.collected_at, rec.text,
         struct_json, cls_json, rec.content_hash, rec.normalized_hash,
         json.dumps(rec.also_seen_at, ensure_ascii=False),
         json.dumps(rec.variants, ensure_ascii=False),
         rec.status, rec.rejection_reason),
    )
    rid = int(cur.lastrowid)
    # Update dedup_index.
    execute("INSERT OR IGNORE INTO dedup_index(content_hash, normalized_hash, "
            "record_id, source_name) VALUES(?,?,?,?)",
            (rec.content_hash, rec.normalized_hash, rid, rec.source_name))
    return rid


def find_duplicate(content_hash: str, normalized_hash: str,
                   source_name: str) -> Optional[Dict[str, Any]]:
    """Find an existing record with the same content_hash (cross-source)."""
    rows = query_all(
        "SELECT r.* FROM records r JOIN dedup_index d ON d.record_id = r.id "
        "WHERE d.content_hash=? AND r.source_name != ? LIMIT 1",
        (content_hash, source_name),
    )
    return rows[0] if rows else None


def find_near_duplicate(text: str, source_name: str, threshold: float = 0.9) -> Optional[Dict[str, Any]]:
    """Cheap near-duplicate check via normalized-hash prefix + Jaccard
    on shingles. Scans only records from the same source first, then
    cross-source. Returns the first near-dup or None."""
    from .dedup import jaccard_3shingle
    norm = _normalize_for_dedup(text)
    if not norm:
        return None
    # Same-source candidates: same normalized_hash.
    cands = query_all(
        "SELECT * FROM records WHERE source_name=? AND status != 'rejected' "
        "ORDER BY id", (source_name,)
    )
    for r in cands:
        if _jaccard(norm, _normalize_for_dedup(r["text"] or "")) >= threshold:
            return r
    # Cross-source candidates: scan a bounded sample.
    cands2 = query_all(
        "SELECT * FROM records WHERE source_name != ? AND status != 'rejected' "
        "ORDER BY id LIMIT 2000", (source_name,)
    )
    for r in cands2:
        if _jaccard(norm, _normalize_for_dedup(r["text"] or "")) >= threshold:
            return r
    return None


def _normalize_for_dedup(text: str) -> str:
    from .schemas import normalize_text
    return normalize_text(text)


def _jaccard(a: str, b: str) -> float:
    from .dedup import jaccard_3shingle
    return jaccard_3shingle(a, b)


def add_cross_source_ref(record_id: int, url: str) -> None:
    """Append a cross-source duplicate URL to a record's also_seen_at."""
    row = query_one("SELECT also_seen_at FROM records WHERE id=?", (record_id,))
    if not row:
        return
    try:
        lst = json.loads(row["also_seen_at"] or "[]")
    except json.JSONDecodeError:
        lst = []
    if url not in lst:
        lst.append(url)
        execute("UPDATE records SET also_seen_at=? WHERE id=?",
                (json.dumps(lst, ensure_ascii=False), record_id))


def add_variant(record_id: int, text: str) -> None:
    """Append a near-duplicate text variant."""
    row = query_one("SELECT variants FROM records WHERE id=?", (record_id,))
    if not row:
        return
    try:
        lst = json.loads(row["variants"] or "[]")
    except json.JSONDecodeError:
        lst = []
    if text not in lst:
        lst.append(text)
        execute("UPDATE records SET variants=? WHERE id=?",
                (json.dumps(lst, ensure_ascii=False), record_id))


def store_document(doc) -> int:
    from .schemas import Document
    if not isinstance(doc, Document):
        raise TypeError("store_document expects a Document")
    cur = execute(
        "INSERT INTO documents(source_name, url, filename, file_type, sha256, "
        "size_bytes, status, meta, collected_at, original_path, "
        "extracted_text_path) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (doc.source_name, doc.url, doc.filename, doc.file_type, doc.sha256,
         doc.size_bytes, doc.status, json.dumps(doc.meta, ensure_ascii=False),
         doc.collected_at, doc.original_path, doc.extracted_text_path),
    )
    return int(cur.lastrowid)


def store_rejection(rej) -> None:
    from .schemas import Rejection
    if not isinstance(rej, Rejection):
        raise TypeError("store_rejection expects a Rejection")
    execute(
        "INSERT INTO rejections(source_name, url, reason, detail, collected_at) "
        "VALUES(?,?,?,?,?)",
        (rej.source_name, rej.url, rej.reason.value, rej.detail, rej.collected_at),
    )


def get_crawl_state(source_name: str) -> Dict[str, Any]:
    row = query_one("SELECT * FROM crawl_state WHERE source_name=?", (source_name,))
    if not row:
        return {"source_name": source_name, "last_url": "", "last_page": 0,
                "last_run_at": "", "status": "idle", "error": "",
                "items_collected": 0, "items_rejected": 0}
    return row


def update_crawl_state(source_name: str, *, last_url: str = None,
                       last_page: int = None, status: str = None,
                       error: str = None, items_collected: int = None,
                       items_rejected: int = None) -> None:
    cur = query_one("SELECT * FROM crawl_state WHERE source_name=?", (source_name,))
    if not cur:
        execute(
            "INSERT INTO crawl_state(source_name, last_url, last_page, "
            "last_run_at, status, error, items_collected, items_rejected) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (source_name, last_url or "", last_page or 0, now_iso(),
             status or "idle", error or "", items_collected or 0,
             items_rejected or 0),
        )
        return
    sets = []
    vals = []
    if last_url is not None:
        sets.append("last_url=?"); vals.append(last_url)
    if last_page is not None:
        sets.append("last_page=?"); vals.append(last_page)
    if status is not None:
        sets.append("status=?"); vals.append(status)
    if error is not None:
        sets.append("error=?"); vals.append(error)
    if items_collected is not None:
        sets.append("items_collected=?"); vals.append(items_collected)
    if items_rejected is not None:
        sets.append("items_rejected=?"); vals.append(items_rejected)
    sets.append("last_run_at=?"); vals.append(now_iso())
    vals.append(source_name)
    execute(f"UPDATE crawl_state SET {', '.join(sets)} WHERE source_name=?", vals)


def upsert_manifest(source_name: str, *, license: str, terms_url: str,
                    access_method: str, permitted_storage: str,
                    attribution: str, notes: str = "") -> None:
    execute(
        "INSERT OR REPLACE INTO source_manifest(source_name, license, "
        "terms_url, access_method, collected_at, permitted_storage, "
        "attribution, notes) VALUES(?,?,?,?,?,?,?,?)",
        (source_name, license, terms_url, access_method, now_iso(),
         permitted_storage, attribution, notes),
    )


def get_manifest(source_name: str) -> Optional[Dict[str, Any]]:
    return query_one("SELECT * FROM source_manifest WHERE source_name=?", (source_name,))


def list_manifests() -> List[Dict[str, Any]]:
    return query_all("SELECT * FROM source_manifest ORDER BY source_name")


def log_export(format: str, file_count: int, record_count: int,
               output_dir: str) -> None:
    execute(
        "INSERT INTO export_runs(run_at, format, file_count, record_count, "
        "schema_version, output_dir) VALUES(?,?,?,?,?,?)",
        (now_iso(), format, file_count, record_count, SCHEMA_VERSION, output_dir),
    )


def search_records(query: str, limit: int = 20,
                   source_name: str = "") -> List[Dict[str, Any]]:
    """FTS5 search over records.text. Returns the full record rows."""
    sql = ("SELECT r.id, r.source_name, r.url, r.title, r.author, "
           "r.published_at, r.collected_at, r.text, r.classification, "
           "r.status, r.content_hash FROM records_fts f "
           "JOIN records r ON r.id = f.rowid WHERE f.text MATCH ?")
    params: List[Any] = [query]
    if source_name:
        sql += " AND r.source_name = ?"
        params.append(source_name)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        return query_all(sql, params)
    except sqlite3.OperationalError:
        # FTS syntax error -> fall back to LIKE.
        like = f"%{query}%"
        sql2 = ("SELECT id, source_name, url, title, author, published_at, "
                "collected_at, text, classification, status, content_hash "
                "FROM records WHERE text LIKE ?")
        params2: List[Any] = [like]
        if source_name:
            sql2 += " AND source_name = ?"
            params2.append(source_name)
        sql2 += " LIMIT ?"
        params2.append(limit)
        return query_all(sql2, params2)


def record_counts() -> Dict[str, int]:
    """Aggregated counts for the status display."""
    out: Dict[str, int] = {}
    out["records_total"] = int(query_one("SELECT COUNT(*) AS n FROM records")["n"])
    out["records_new"] = int(query_one(
        "SELECT COUNT(*) AS n FROM records WHERE status='new'")["n"])
    out["records_duplicate"] = int(query_one(
        "SELECT COUNT(*) AS n FROM records WHERE status='duplicate'")["n"])
    out["records_near_duplicate"] = int(query_one(
        "SELECT COUNT(*) AS n FROM records WHERE status='near_duplicate'")["n"])
    out["records_rejected"] = int(query_one(
        "SELECT COUNT(*) AS n FROM records WHERE status='rejected'")["n"])
    out["documents_total"] = int(query_one("SELECT COUNT(*) AS n FROM documents")["n"])
    out["documents_extracted"] = int(query_one(
        "SELECT COUNT(*) AS n FROM documents WHERE status='extracted'")["n"])
    out["documents_stored_only"] = int(query_one(
        "SELECT COUNT(*) AS n FROM documents WHERE status='stored_only'")["n"])
    out["documents_failed"] = int(query_one(
        "SELECT COUNT(*) AS n FROM documents WHERE status='failed'")["n"])
    out["documents_rejected"] = int(query_one(
        "SELECT COUNT(*) AS n FROM documents WHERE status='rejected'")["n"])
    out["rejections_total"] = int(query_one("SELECT COUNT(*) AS n FROM rejections")["n"])
    return out


def integrity_check() -> Dict[str, Any]:
    ic = [r[0] for r in connect().execute("PRAGMA integrity_check").fetchall()]
    return {"integrity_check": ic, "ok": ic == ["ok"],
            "schema_version": current_version()}
