"""Tests for the Knowledge Collector CLI (offline commands)."""
import pytest

from kc import cli, database, config, schemas, classifier, extraction


def _seed_one_record():
    rec = schemas.Record(
        source_name="eevblog", url="https://x/t1", thread_id="t1",
        post_id="p1", title="Lenovo G50-80 no power", author="tech",
        published_at="2024-05-01", collected_at=config.now_iso(),
        text="I replaced C45, measured 480 ohm. Board is fixed and works now.")
    rec.structured = extraction.extract(rec.text, rec.title)
    rec.classification = classifier.classify(rec.text, rec.structured)
    database.store_record(rec)


def test_help(tmp_app):
    out = cli.dispatch("help")
    assert "Knowledge Collector" in out
    assert "sources" in out
    assert "collect" in out
    assert "export" in out


def test_sources_empty(tmp_app):
    out = cli.dispatch("sources")
    assert "No sources" in out


def test_source_add_and_list(tmp_app):
    out = cli.dispatch("source add eevblog forum_html https://www.eevblog.com/forum/")
    assert "Added source 'eevblog'" in out
    out2 = cli.dispatch("sources")
    assert "eevblog" in out2
    assert "disabled" in out2  # disabled by default


def test_source_enable_disable(tmp_app):
    cli.dispatch("source add eevblog forum_html https://x/")
    out = cli.dispatch("source enable eevblog")
    assert "Enabled" in out
    assert database.get_source("eevblog")["enabled"] == 1
    cli.dispatch("source disable eevblog")
    assert database.get_source("eevblog")["enabled"] == 0


def test_status_shows_counts(tmp_app):
    _seed_one_record()
    out = cli.dispatch("status")
    assert "Records total:" in out
    assert "1" in out
    assert "new:" in out


def test_stats(tmp_app):
    _seed_one_record()
    out = cli.dispatch("stats")
    assert "Storage used:" in out
    assert "Records total:" in out


def test_search_finds_record(tmp_app):
    _seed_one_record()
    out = cli.dispatch("search C45")
    assert "C45" in out
    assert "eevblog" in out


def test_search_no_hits(tmp_app):
    out = cli.dispatch("search nonexistent_term_xyz")
    assert "No records match" in out


def test_export_jsonl(tmp_app):
    _seed_one_record()
    out = cli.dispatch("export jsonl")
    assert "Exported" in out


def test_export_knowledge(tmp_app):
    _seed_one_record()
    out = cli.dispatch("export knowledge")
    assert "Knowledge JSON" in out


def test_export_unknown_format(tmp_app):
    out = cli.dispatch("export xml")
    assert "failed" in out or "unknown" in out


def test_verify(tmp_app):
    out = cli.dispatch("verify")
    assert "Integrity:" in out
    assert "ok" in out


def test_documents_empty(tmp_app):
    out = cli.dispatch("documents")
    assert "No documents" in out


def test_collect_no_enabled_sources(tmp_app):
    out = cli.dispatch("collect")
    assert "No enabled sources" in out


def test_unknown_command(tmp_app):
    out = cli.dispatch("frobnicate")
    assert "Unknown command" in out


def test_pause_resume(tmp_app):
    cli.dispatch("source add eevblog forum_html https://x/")
    cli.dispatch("source enable eevblog")
    out = cli.dispatch("pause --source eevblog")
    assert "Paused" in out
    state = database.get_crawl_state("eevblog")
    assert state["status"] == "paused"


def test_cli_help_lists_all_required_commands(tmp_app):
    """The spec requires: sources, source add, source disable, collect,
    resume, pause, status, search, documents, export, verify, stats, help, exit."""
    out = cli.dispatch("help")
    for cmd in ("sources", "source", "collect", "resume", "pause", "status",
                "search", "documents", "export", "verify", "stats", "help"):
        assert cmd in out, f"help text missing '{cmd}'"
