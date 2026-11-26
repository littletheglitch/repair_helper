"""Tests for the export module (all 5 formats + manifest)."""
import json
import sqlite3
from pathlib import Path

from kc import export as X, database, config, schemas, classifier, extraction


def _seed_records():
    rec1 = schemas.Record(
        source_name="eevblog", url="https://x/t1", thread_id="t1",
        post_id="p1", title="Lenovo G50-80 no power",
        author="tech", published_at="2024-05-01",
        collected_at=config.now_iso(),
        text="I replaced C45 and measured 480 ohm. The board is fixed and works now.")
    rec1.structured = extraction.extract(rec1.text, rec1.title)
    rec1.classification = classifier.classify(rec1.text, rec1.structured)
    database.store_record(rec1)
    rec2 = schemas.Record(
        source_name="stackexchange_electronics", url="https://x/t2",
        thread_id="t2", post_id="p2", title="Buck converter MOSFET",
        author="eng", published_at="2024-06-15",
        collected_at=config.now_iso(),
        text="A buck converter steps voltage down because of the inductor. "
             "The MOSFET switches the input.")
    rec2.structured = extraction.extract(rec2.text, rec2.title)
    rec2.classification = classifier.classify(rec2.text, rec2.structured)
    database.store_record(rec2)


def test_export_jsonl(tmp_app):
    _seed_records()
    r = X.export("jsonl")
    assert r["ok"]
    p = Path(r["file"])
    assert p.exists()
    lines = p.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    obj = json.loads(lines[0])
    assert "source_name" in obj and "text" in obj


def test_export_markdown(tmp_app):
    _seed_records()
    r = X.export("markdown")
    assert r["ok"]
    p = Path(r["file"])
    txt = p.read_text(encoding="utf-8")
    assert "Lenovo G50-80" in txt
    assert "Source:" in txt
    assert "URL:" in txt


def test_export_sqlite_snapshot(tmp_app):
    _seed_records()
    r = X.export("sqlite")
    assert r["ok"]
    p = Path(r["file"])
    assert p.exists()
    conn = sqlite3.connect(str(p))
    names = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "records" in names
    conn.close()


def test_export_text_per_record(tmp_app):
    _seed_records()
    r = X.export("text")
    assert r["ok"]
    out = config.export_dir() / "text"
    files = list(out.glob("*.txt"))
    assert len(files) == 2
    txt = files[0].read_text(encoding="utf-8")
    assert "Source:" in txt  # source-reference header
    assert "URL:" in txt


def test_export_knowledge_matches_assistant_schema(tmp_app):
    _seed_records()
    r = X.export("knowledge")
    assert r["ok"]
    out = config.export_dir() / "knowledge"
    # Exclude manifest.json from the source-file count.
    files = sorted(f for f in out.glob("*.json") if f.name != "manifest.json")
    assert len(files) == 2  # one per source
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        # Top-level structure must match the assistant's _ingest expectations.
        assert "source" in data
        assert "sections" in data
        assert isinstance(data["sections"], list)
        for sec in data["sections"]:
            assert "category" in sec
            assert "topic" in sec
            assert "facts" in sec
            for fact in sec["facts"]:
                assert "text" in fact
                # Each fact carries tier + provenance (the contract).
                assert fact.get("tier") or fact.get("provenance")
                assert fact.get("source_url")


def test_export_knowledge_tier_from_classification(tmp_app):
    """A class-A record (technician-confirmed) must export with
    tier='technician-confirmed'."""
    rec = schemas.Record(
        source_name="eevblog", url="https://x/t1", thread_id="t1",
        post_id="p1", title="Confirmed repair", author="", published_at="",
        collected_at=config.now_iso(),
        text="I replaced C45, measured 480 ohm after the fix. The board "
             "is fixed and working now.")
    rec.structured = extraction.extract(rec.text, rec.title)
    rec.classification = classifier.classify(rec.text, rec.structured)
    database.store_record(rec)
    X.export("knowledge")
    out = config.export_dir() / "knowledge"
    files = [f for f in out.glob("*.json") if f.name != "manifest.json"]
    data = json.loads(files[0].read_text(encoding="utf-8"))
    fact = data["sections"][0]["facts"][0]
    assert fact["tier"] == "technician-confirmed"


def test_export_writes_manifest(tmp_app):
    _seed_records()
    X.export("jsonl")
    m = config.export_dir() / "jsonl" / "manifest.json"
    assert m.exists()
    manifest = json.loads(m.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == X.SCHEMA_VERSION
    assert "files" in manifest


def test_export_unknown_format_errors(tmp_app):
    r = X.export("xml")
    assert not r["ok"]
    assert "unknown format" in r["error"]


def test_export_filtered_by_source(tmp_app):
    _seed_records()
    r = X.export("knowledge", source_name="eevblog")
    assert r["ok"]
    files = [f for f in (config.export_dir() / "knowledge").glob("*.json")
             if f.name != "manifest.json"]
    # Only the eevblog source file.
    assert all("eevblog" in f.name for f in files)
