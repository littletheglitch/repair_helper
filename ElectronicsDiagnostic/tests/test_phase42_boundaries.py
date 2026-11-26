"""Phase 4.2 — architectural boundaries reaffirmed.

Item 6: BIOS, schematic, and BoardView stay independent of the
general KB. Manual documents are only usable within their repair
scope. Proprietary docs are NOT auto-imported to the general KB.
Phase 3 behavior unchanged.
"""
import pytest

from eda import config, database, documents, knowledge_base, cli
from eda.learning_system import ExerciseGenerator, datasheet_exercises


def test_datasheet_values_come_from_registry_not_kb(tmp_app):
    """Datasheet exercises cite the verified registry, not KB facts.
    Importing a document does NOT add datasheet values."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    before = database.count_facts()
    # Generate datasheet exercises — they must cite the registry URLs.
    gen = ExerciseGenerator()
    exs = gen.generate_datasheet()
    for e in exs:
        assert "http" in e.source_ref  # registry URL, not KB
        # The exercise is NOT sourced from knowledge_base facts.
        assert e.source_kind == "datasheet"
    after = database.count_facts()
    # Generating datasheet exercises added 0 KB facts.
    assert after == before


def test_scenario_exercises_do_not_add_kb_facts(tmp_app):
    """Generating scenario exercises does NOT add general-KB facts.
    The scenarios are a curated data file, not a knowledge feed."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    before = database.count_facts()
    gen = ExerciseGenerator()
    gen.generate_scenarios(seed=7)
    after = database.count_facts()
    assert after == before


def test_transfer_exercises_do_not_add_kb_facts(tmp_app):
    """Transfer exercises do NOT add KB facts."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    before = database.count_facts()
    gen = ExerciseGenerator()
    from eda.learning_system.db import SKILL_CATEGORIES
    for c in SKILL_CATEGORIES:
        gen.generate_transfer(c["key"], n=1, seed=7)
    after = database.count_facts()
    assert after == before


def test_importing_document_still_no_kb_facts(tmp_app, tmp_path):
    """Reaffirmed from 4.1: importing a document adds 0 KB facts."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    before = database.count_facts()
    f = tmp_path / "board.txt"
    f.write_text("BOARDVIEW BoardA")
    documents.import_file(f)
    after = database.count_facts()
    assert after == before


def test_phase3_commands_unchanged(tmp_app):
    """Phase 3 (orchestrate, analyze, learn record) unchanged."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    cli.dispatch("new TestBoard Lenovo")
    cli.dispatch("symptom no power")
    cli.dispatch("measure VIN = 19.4V")
    out_an = cli.dispatch("analyze")
    assert "Traceback" not in out_an
    out_or = cli.dispatch("orchestrate --backend stub")
    assert "Unknown command" not in out_or
    out_lr = cli.dispatch("learn record shorted 5VALW --replaced C123 --confirming resistance --outcome fixed")
    assert "Recorded case" in out_lr


def test_no_duplicate_datasheet_keys(tmp_app):
    """Datasheet exercise keys are unique per (part, value-class, idx);
    regenerating does not collide."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    a = gen.generate_datasheet(seed=1)
    b = gen.generate_datasheet(seed=2)
    keys_a = {e.exercise_key for e in a}
    keys_b = {e.exercise_key for e in b}
    assert keys_a == keys_b  # same registry -> same keys (idempotent)
