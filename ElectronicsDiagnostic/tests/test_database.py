"""Database and FTS5 initialization tests."""
import sqlite3

from eda import database, config


def test_init_creates_tables(tmp_app):
    conn = database.connect()
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    expected = {
        "sessions", "symptoms", "measurements", "documents",
        "document_chunks", "hypotheses", "missing_evidence",
        "next_tests", "timeline", "knowledge_facts",
    }
    assert expected.issubset(names), f"missing tables: {expected - names}"


def test_fts5_available(tmp_app):
    assert database.fts5_available() is True


def test_fts5_chunks_virtual_table_exists(tmp_app):
    conn = database.connect()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
    ).fetchall()
    assert rows, "chunks_fts not created"


def test_foreign_keys_on(tmp_app):
    conn = database.connect()
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_db_path_under_config(tmp_app):
    assert config.db_path().parent.name == "Config"
