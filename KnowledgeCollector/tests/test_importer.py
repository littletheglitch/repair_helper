"""Tests for the importer: offline import into the diagnostic assistant."""
import json
import sqlite3
from pathlib import Path

from kc import export as X, importer as I, database, config, schemas
from kc import classifier, extraction


def _seed_and_export(tmp_app):
    rec = schemas.Record(
        source_name="eevblog", url="https://x/t1", thread_id="t1",
        post_id="p1", title="Lenovo G50-80 no power",
        author="tech", published_at="2024-05-01",
        collected_at=config.now_iso(),
        text="I replaced C45, measured 480 ohm. The board is fixed and works now.")
    rec.structured = extraction.extract(rec.text, rec.title)
    rec.classification = classifier.classify(rec.text, rec.structured)
    database.store_record(rec)
    X.export("knowledge")
    return config.export_dir() / "knowledge"


def _knowledge_files(d):
    """Return only the source-knowledge JSON files, not manifest.json."""
    return sorted(f for f in d.glob("*.json") if f.name != "manifest.json")


def test_validate_export_file_ok(tmp_app, tmp_path):
    d = _seed_and_export(tmp_app)
    f = _knowledge_files(d)[0]
    v = I.validate_export_file(f)
    assert v["ok"]
    assert v["fact_count"] >= 1


def test_validate_export_file_bad(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"sections": [{"facts": [{}]}]}))
    v = I.validate_export_file(bad)
    assert not v["ok"]
    assert any("text" in m for m in v["missing"])


def test_import_dry_run(tmp_app, tmp_path, monkeypatch):
    d = _seed_and_export(tmp_app)
    # Point the assistant root at a temp dir so we don't write into the real install.
    fake_root = tmp_path / "assistant"
    monkeypatch.setenv("KC_ASSISTANT_ROOT", str(fake_root))
    import importlib
    importlib.reload(I)
    r = I.import_export(d, dry_run=True)
    assert r["ok"]
    assert r["dry_run"]
    assert r["facts"] >= 1


def test_import_copies_into_assistant_knowledge_dir(tmp_app, tmp_path, monkeypatch):
    d = _seed_and_export(tmp_app)
    fake_root = tmp_path / "assistant"
    (fake_root / "Knowledge").mkdir(parents=True)
    monkeypatch.setenv("KC_ASSISTANT_ROOT", str(fake_root))
    import importlib
    importlib.reload(I)
    r = I.import_export(d, dry_run=False)
    assert r["ok"]
    files = list((fake_root / "Knowledge").glob("*.json"))
    assert len(files) == 1
    # The copied file matches the schema.
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert "sections" in data
    assert data["sections"][0]["facts"][0]["tier"]


def test_import_force_clears_knowledge_sources(tmp_app, tmp_path, monkeypatch):
    d = _seed_and_export(tmp_app)
    fake_root = tmp_path / "assistant"
    (fake_root / "Knowledge").mkdir(parents=True)
    (fake_root / "Config").mkdir(parents=True)
    # Create a fake assistant DB with a knowledge_sources table + a row.
    db = fake_root / "Config" / "eda.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE knowledge_sources (file_stem TEXT PRIMARY KEY, "
                 "fact_count INTEGER, loaded_at TEXT)")
    stem = _knowledge_files(d)[0].stem
    conn.execute("INSERT INTO knowledge_sources VALUES (?, 1, '2024-01-01')", (stem,))
    conn.commit(); conn.close()
    monkeypatch.setenv("KC_ASSISTANT_ROOT", str(fake_root))
    import importlib
    importlib.reload(I)
    r = I.import_export(d, force=True)
    assert r["ok"]
    assert r["cleared"] == 1


def test_render_import_report(tmp_app, tmp_path, monkeypatch):
    d = _seed_and_export(tmp_app)
    fake_root = tmp_path / "assistant"
    (fake_root / "Knowledge").mkdir(parents=True)
    monkeypatch.setenv("KC_ASSISTANT_ROOT", str(fake_root))
    import importlib
    importlib.reload(I)
    r = I.import_export(d)
    txt = I.render_import_report(r)
    assert "Imported" in txt
    assert "Knowledge" in txt


def test_import_missing_dir_errors(tmp_path):
    r = I.import_export(tmp_path / "nonexistent")
    assert not r["ok"]
    assert "not found" in r["error"]


def test_import_empty_dir_errors(tmp_path):
    (tmp_path / "empty").mkdir()
    r = I.import_export(tmp_path / "empty")
    assert not r["ok"]
    assert "no kc_*.json" in r["error"]


def test_import_preserves_source_reference(tmp_app, tmp_path, monkeypatch):
    """The source URL and title must survive the export/import round-trip."""
    d = _seed_and_export(tmp_app)
    fake_root = tmp_path / "assistant"
    (fake_root / "Knowledge").mkdir(parents=True)
    monkeypatch.setenv("KC_ASSISTANT_ROOT", str(fake_root))
    import importlib
    importlib.reload(I)
    I.import_export(d)
    f = list((fake_root / "Knowledge").glob("*.json"))[0]
    data = json.loads(f.read_text(encoding="utf-8"))
    fact = data["sections"][0]["facts"][0]
    assert fact["source_url"] == "https://x/t1"
    assert "Lenovo G50-80" in fact["source_title"]
