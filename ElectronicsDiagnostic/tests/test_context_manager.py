"""Tests for the context manager (Phase 3).

Verifies: assembly produces blocks, token budget respected, KB facts
included, no-data-deleted (omitted measurements are noted), structured
summary works.
"""
import pytest

from eda import config, database, sessions, measurements
from eda import context_manager as CM


def _seed_session():
    s = sessions.create("Test board", "Lenovo G50-80")
    sessions.add_symptom(s.id, "no power")
    measurements.add(s.id, {"name": "VIN", "value": 19.4, "unit": "V",
                            "point": "board input"})
    measurements.add(s.id, {"name": "+5VALW", "value": 0.0, "unit": "V",
                            "point": "rail"})
    return s


def test_assemble_produces_all_blocks(tmp_app):
    database.init_db()
    s = _seed_session()
    ctx = CM.assemble(s.id)
    labels = [b.label for b in ctx.blocks]
    assert "Session" in labels
    assert "Symptoms" in labels
    assert "Measurements" in labels
    assert "Knowledge Base facts" in labels
    assert "Attached documents" in labels


def test_assemble_contains_session_id_and_board(tmp_app):
    database.init_db()
    s = _seed_session()
    ctx = CM.assemble(s.id)
    assert str(s.id) in ctx.text
    assert "Lenovo G50-80" in ctx.text


def test_assemble_contains_measurements(tmp_app):
    database.init_db()
    s = _seed_session()
    ctx = CM.assemble(s.id)
    assert "VIN" in ctx.text
    assert "19.4" in ctx.text
    assert "+5VALW" in ctx.text


def test_assemble_respects_token_budget(tmp_app):
    database.init_db()
    s = _seed_session()
    ctx = CM.assemble(s.id, budget=200)
    assert ctx.total_tokens <= 200 + 50  # small slack for the trim guard
    assert ctx.budget_tokens == 200


def test_assemble_notes_omitted_measurements(tmp_app):
    """No data is silently dropped: omitted measurements are named."""
    database.init_db()
    s = _seed_session()
    # Add many measurements to force trimming.
    for i in range(40):
        measurements.add(s.id, {"name": f"rail_{i}", "value": float(i),
                               "unit": "V", "point": f"pt{i}"})
    ctx = CM.assemble(s.id, budget=150)
    assert ctx.measurements_omitted > 0
    # The omission is noted in the measurements block text.
    meas_block = next(b for b in ctx.blocks if b.label == "Measurements")
    assert "omitted" in meas_block.text.lower()
    assert "database" in meas_block.text.lower()  # tells the user where they live


def test_assemble_with_empty_session(tmp_app):
    database.init_db()
    s = sessions.create("Empty", "")
    ctx = CM.assemble(s.id)
    assert "(no symptoms recorded)" in ctx.text
    assert "(no measurements)" in ctx.text


def test_structured_summary_lists_measurements(tmp_app):
    database.init_db()
    s = _seed_session()
    summ = CM.structured_summary(s.id)
    assert "Test board" in summ
    assert "VIN" in summ
    assert "+5VALW" in summ


def test_token_budget_returns_positive(tmp_app):
    database.init_db()
    budget = CM.token_budget()
    assert budget >= 512
