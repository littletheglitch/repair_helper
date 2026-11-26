"""End-to-end test: Collector export -> real assistant ingestion.

This is the spec's §15.9 requirement: "Test offline import into the
diagnostic assistant." It seeds the Collector with a fixture-sourced
record, exports Knowledge JSON, imports it into a sandboxed copy of
the REAL Electronics Diagnostic Assistant, and confirms the assistant's
knowledge_base.load_builtin() picks it up — with NO code change to the
assistant.

The assistant is sandboxed by copying it to a temp dir (so the real
install isn't touched) and pointing KC_ASSISTANT_ROOT at it.
"""
import importlib
import json
import os
import shutil
import sys
import sqlite3
from pathlib import Path

import pytest

ASSISTANT_SRC = Path("/home/z/my-project/download/ElectronicsDiagnostic")


@pytest.fixture
def sandboxed_assistant(tmp_path, monkeypatch):
    """Copy the real assistant into a temp dir so we can import into it
    without touching the live install."""
    if not ASSISTANT_SRC.exists():
        pytest.skip("ElectronicsDiagnostic not present for end-to-end test")
    sandbox = tmp_path / "assistant"
    # Copy the source tree (without data/ and __pycache__).
    shutil.copytree(ASSISTANT_SRC, sandbox,
                    ignore=shutil.ignore_patterns("__pycache__", "Config",
                                                   "Sessions", "Documents",
                                                   "Models", "Reports", "Temp",
                                                   "Logs", "build", ".pytest_cache",
                                                   ".git"))
    # Make the assistant importable.
    monkeypatch.syspath_prepend(str(sandbox / "src"))
    monkeypatch.setenv("EDA_APP_DIR", str(sandbox))
    monkeypatch.setenv("KC_ASSISTANT_ROOT", str(sandbox))
    yield sandbox


def test_end_to_end_collector_to_assistant(tmp_app, sandboxed_assistant):
    """The full pipeline: Collector seeds a record -> exports Knowledge
    JSON -> importer copies it into the assistant's Knowledge/ dir ->
    the assistant's load_builtin() ingests it with no code change."""
    from kc import (database, config, schemas, classifier, extraction,
                    export as X, importer as I)
    # 1. Seed a record in the Collector.
    rec = schemas.Record(
        source_name="eevblog", url="https://www.eevblog.com/forum/repair/topic,12345.0.html",
        thread_id="12345", post_id="12345_0",
        title="Lenovo G50-80 no power, +5VALW shorted",
        author="repair_tech", published_at="2024-05-01",
        collected_at=config.now_iso(),
        text="My Lenovo G50-80 has no power. Measured +5VALW = 0V. VIN is 19V. "
             "Resistance to ground on +5VALW was 8 ohm. Replaced C45 (22uF 6.3V) "
             "and the board powered on. Measured 480 ohm after the fix.")
    rec.structured = extraction.extract(rec.text, rec.title)
    rec.classification = classifier.classify(rec.text, rec.structured)
    database.store_record(rec)
    # 2. Export to Knowledge JSON.
    X.export("knowledge")
    export_dir = config.export_dir() / "knowledge"
    kfiles = [f for f in export_dir.glob("*.json") if f.name != "manifest.json"]
    assert kfiles, "Knowledge export produced no files"
    # 3. Validate the export matches the assistant's schema.
    v = I.validate_export_file(kfiles[0])
    assert v["ok"], f"validation failed: {v.get('missing')}"
    # 4. Import into the sandboxed assistant.
    import importlib
    importlib.reload(I)
    r = I.import_export(export_dir, force=True)
    assert r["ok"], f"import failed: {r}"
    # 5. The assistant's Knowledge dir now has the file.
    knowledge_files = list((sandboxed_assistant / "Knowledge").glob("kc_*.json"))
    assert knowledge_files
    # 6. The REAL assistant ingests it via load_builtin() with NO code change.
    # Re-import the assistant's knowledge_base against the sandbox.
    # We use a fresh assistant DB at the sandbox location.
    from eda import config as eda_config, database as eda_db, knowledge_base as eda_kb
    # Point the assistant at the sandbox.
    eda_config.ensure_layout()
    eda_db.init_db()
    inserted = eda_kb.load_builtin(force=True)
    assert inserted > 0, "assistant ingested no facts"
    # 7. The assistant can now find the Collector-sourced fact via search.
    hits = eda_kb.search_facts("5VALW shorted C45", limit=10)
    assert hits, "assistant search did not find the Collector-sourced fact"
    # 8. The fact carries the technician-confirmed tier (class A).
    fact = next((h for h in hits if "C45" in h.get("fact", "")), None)
    assert fact is not None, "the Collector fact was not ingested"
    assert fact.get("tier") == "technician-confirmed"
    # 9. The source URL survived the round-trip.
    assert "eevblog.com" in fact.get("source_url", "") or "eevblog" in fact.get("source_title", "")


def test_export_schema_matches_assistant_ingest_dict(tmp_app, sandboxed_assistant):
    """The exported JSON's structure must match exactly what the
    assistant's knowledge_base._ingest() expects."""
    from kc import database, config, schemas, classifier, extraction, export as X
    rec = schemas.Record(
        source_name="stackexchange_electronics",
        url="https://electronics.stackexchange.com/q/1",
        thread_id="1", post_id="1", title="Buck converter MOSFET",
        author="eng", published_at="2024-06-15",
        collected_at=config.now_iso(),
        text="A buck converter steps voltage down because of the inductor.")
    rec.structured = extraction.extract(rec.text, rec.title)
    rec.classification = classifier.classify(rec.text, rec.structured)
    database.store_record(rec)
    X.export("knowledge")
    kfile = next(f for f in (config.export_dir() / "knowledge").glob("kc_*.json"))
    data = json.loads(kfile.read_text(encoding="utf-8"))
    # Exact shape the assistant's _ingest reads.
    assert "source" in data
    assert "provenance" in data
    assert "sections" in data
    sec = data["sections"][0]
    assert "category" in sec
    assert "topic" in sec
    assert "facts" in sec
    fact = sec["facts"][0]
    assert "text" in fact
    # The assistant's import_fact accepts these keys.
    for k in ("text", "confidence", "provenance", "tier", "source_url",
              "source_title", "source_date", "tags"):
        assert k in fact
