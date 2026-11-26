"""Tests for the database + migrations + FTS5."""
from kc import database, config, schemas


def test_init_creates_tables(tmp_app):
    conn = database.connect()
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("sources", "records", "records_fts", "documents",
              "dedup_index", "crawl_state", "source_manifest",
              "export_runs", "rejections"):
        assert t in names, f"missing table {t}"


def test_schema_version_is_one(tmp_app):
    assert database.current_version() == 1


def test_init_idempotent(tmp_app):
    database.init_db()
    database.init_db()  # must not raise
    assert database.current_version() == 1


def test_upsert_and_get_source(tmp_app):
    database.upsert_source("eevblog", "forum_html", "https://www.eevblog.com/forum/",
                           enabled=True, license="forum ToS",
                           access_method="HTML (robots-respecting)")
    s = database.get_source("eevblog")
    assert s["name"] == "eevblog"
    assert s["enabled"] == 1
    assert s["license"] == "forum ToS"


def test_enable_disable_source(tmp_app):
    database.upsert_source("eevblog", "forum_html", "https://x/")
    database.enable_source("eevblog", False)
    assert database.get_source("eevblog")["enabled"] == 0
    database.enable_source("eevblog", True)
    assert database.get_source("eevblog")["enabled"] == 1


def test_list_sources(tmp_app):
    database.upsert_source("a", "forum_html", "https://a/")
    database.upsert_source("b", "forum_html", "https://b/", enabled=True)
    all_sources = database.list_sources()
    assert len(all_sources) == 2
    enabled = database.list_sources(enabled_only=True)
    assert len(enabled) == 1
    assert enabled[0]["name"] == "b"


def test_store_record_and_search(tmp_app):
    rec = schemas.Record(
        source_name="eevblog", url="https://x/t1",
        thread_id="t1", post_id="p1", title="No power G50",
        author="tech1", published_at="2024-05-01",
        collected_at=config.now_iso(),
        text="The +5VALW rail was shorted; replaced C45 and the board "
             "powered on. Measured 480 ohm to ground after the fix.",
    )
    rid = database.store_record(rec)
    assert rid > 0
    # FTS trigger fired: search finds it.
    hits = database.search_records("5VALW shorted C45")
    assert hits
    assert any("+5VALW" in h["text"] for h in hits)


def test_store_record_replaces_on_same_url(tmp_app):
    rec1 = schemas.Record(
        source_name="eevblog", url="https://x/t1", thread_id="t1",
        post_id="p1", title="T", author="", published_at="",
        collected_at=config.now_iso(), text="original text")
    rid1 = database.store_record(rec1)
    rec2 = schemas.Record(
        source_name="eevblog", url="https://x/t1", thread_id="t1",
        post_id="p1", title="T", author="", published_at="",
        collected_at=config.now_iso(), text="updated text")
    rid2 = database.store_record(rec2)
    # Same (source_name, url) -> replaced, not duplicated.
    rows = database.query_all(
        "SELECT * FROM records WHERE source_name='eevblog' AND url='https://x/t1'")
    assert len(rows) == 1
    assert rows[0]["text"] == "updated text"


def test_find_duplicate_cross_source(tmp_app):
    rec1 = schemas.Record(
        source_name="eevblog", url="https://a/t1", thread_id="t1",
        post_id="p1", title="", author="", published_at="",
        collected_at=config.now_iso(), text="identical text here")
    database.store_record(rec1)
    # Same text, different source -> duplicate.
    dup = database.find_duplicate(rec1.content_hash, rec1.normalized_hash,
                                  "stackexchange_electronics")
    assert dup is not None
    assert dup["source_name"] == "eevblog"


def test_add_cross_source_ref(tmp_app):
    rec = schemas.Record(
        source_name="eevblog", url="https://a/t1", thread_id="t1",
        post_id="p1", title="", author="", published_at="",
        collected_at=config.now_iso(), text="dup-text")
    rid = database.store_record(rec)
    database.add_cross_source_ref(rid, "https://b/t1")
    row = database.query_one("SELECT also_seen_at FROM records WHERE id=?", (rid,))
    import json
    assert "https://b/t1" in json.loads(row["also_seen_at"])


def test_crawl_state_upsert(tmp_app):
    database.update_crawl_state("eevblog", status="running", last_url="https://x/p1")
    st = database.get_crawl_state("eevblog")
    assert st["status"] == "running"
    assert st["last_url"] == "https://x/p1"
    database.update_crawl_state("eevblog", status="paused")
    st = database.get_crawl_state("eevblog")
    assert st["status"] == "paused"
    # last_url should be preserved (not set to "").
    assert st["last_url"] == "https://x/p1"


def test_upsert_manifest(tmp_app):
    database.upsert_manifest("eevblog", license="forum ToS",
                             terms_url="https://www.eevblog.com/forum/tos",
                             access_method="HTML (robots-respecting)",
                             permitted_storage="metadata + short extracts",
                             attribution="EEVblog forum, link to original thread")
    m = database.get_manifest("eevblog")
    assert m["license"] == "forum ToS"
    assert m["attribution"] == "EEVblog forum, link to original thread"


def test_store_rejection(tmp_app):
    from kc.schemas import Rejection, RejectionReason
    database.store_rejection(Rejection(
        source_name="eevblog", url="https://x/spam",
        reason=RejectionReason.SPAM, detail="commercial spam",
        collected_at=config.now_iso()))
    rows = database.query_all("SELECT * FROM rejections")
    assert len(rows) == 1
    assert rows[0]["reason"] == "spam"


def test_record_counts(tmp_app):
    rec = schemas.Record(
        source_name="eevblog", url="https://x/t1", thread_id="t1",
        post_id="p1", title="", author="", published_at="",
        collected_at=config.now_iso(), text="some text")
    database.store_record(rec)
    counts = database.record_counts()
    assert counts["records_total"] == 1
    assert counts["records_new"] == 1


def test_integrity_check_ok(tmp_app):
    rep = database.integrity_check()
    assert rep["ok"] is True
    assert rep["schema_version"] == 1


def test_search_records_filters_by_source(tmp_app):
    r1 = schemas.Record(
        source_name="eevblog", url="https://x/t1", thread_id="t1",
        post_id="p1", title="", author="", published_at="",
        collected_at=config.now_iso(), text="MOSFET gate driver failure")
    r2 = schemas.Record(
        source_name="stackexchange", url="https://x/t2", thread_id="t2",
        post_id="p2", title="", author="", published_at="",
        collected_at=config.now_iso(), text="MOSFET gate driver failure")
    database.store_record(r1)
    database.store_record(r2)
    only_eevblog = database.search_records("MOSFET", source_name="eevblog")
    assert all(h["source_name"] == "eevblog" for h in only_eevblog)
