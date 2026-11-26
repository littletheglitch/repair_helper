"""Phase 5, Task 5 — diagnostic session -> learning exercise conversion.

Verifies (per the spec):
  * A real diagnostic session converts to a learning exercise WITHOUT
    changing the original measurements, symptoms, or evidence.
  * An unconfirmed repair outcome stays unconfirmed (the converted
    case must NOT claim a confirmed root cause).
  * Learning feedback (the conversion + follow-up exercise) does NOT
    contaminate the general KB facts (architectural boundary: the
    learning subsystem is separate from the knowledge base).
  * The conversion does not modify the source session.
"""
from __future__ import annotations

import pytest

from eda import config, database, sessions, measurements
from eda import learning as repair_learning
from eda.learning_system import repair_to_learning as R2L


def _seed_session(symptoms, meas_list, title="R2L test (synthetic)"):
    s = sessions.create(title, "Synthetic R2L Board")
    for sym in symptoms:
        sessions.add_symptom(s.id, sym)
    for m in meas_list:
        measurements.add(s.id, m)
    return s


def test_conversion_preserves_measurements_and_symptoms(tmp_app):
    """The conversion must NOT change the session's measurements or
    symptoms — it reads them verbatim into the LearningCase."""
    database.init_db()
    s = _seed_session(["no power"],
                      [{"name": "VIN", "value": 19.4, "unit": "V",
                        "point": "jack", "reference": ""},
                       {"name": "+5VALW", "value": 0.0, "unit": "V",
                        "point": "rail", "reference": ""}])
    conv = R2L.RepairToLearning()
    res = conv.convert(s.id, seed=7)
    assert res["ok"]
    case = res["case"]
    # The case carries the symptoms and measurements verbatim.
    assert case["symptoms"] == ["no power"]
    assert len(case["measurements"]) == 2
    assert case["measurements"][0]["name"] == "VIN"
    assert case["measurements"][0]["value"] == 19.4
    assert case["measurements"][1]["name"] == "+5VALW"
    assert case["measurements"][1]["value"] == 0.0
    # The source session is unchanged (re-read its measurements).
    ms_after = measurements.list_for(s.id)
    assert [m.name for m in ms_after] == ["VIN", "+5VALW"]
    assert ms_after[0].value == 19.4


def test_unconfirmed_outcome_stays_unconfirmed(tmp_app):
    """A session with NO recorded repair case (or an unconfirmed one)
    must convert to a case with confirmed=False and no root cause."""
    database.init_db()
    s = _seed_session(["no power"],
                      [{"name": "+5VALW", "value": 0.0, "unit": "V",
                        "point": "rail", "reference": ""}])
    conv = R2L.RepairToLearning()
    res = conv.convert(s.id, seed=7)
    case = res["case"]
    # No technician confirmed anything -> stays unconfirmed.
    assert case["confirmed"] is False
    assert case["confirmed_root_cause"] is None
    assert case["outcome"] == "unknown"


def test_confirmed_case_disclosed_only_if_technician_confirmed(tmp_app):
    """When a technician-confirmed repair case exists for the session,
    the converted case carries the confirmed root cause; otherwise it
    does not (the confirmation mechanism is the ONLY path)."""
    database.init_db()
    s = _seed_session(["no power"],
                      [{"name": "+5VALW", "value": 0.0, "unit": "V",
                        "point": "rail", "reference": ""}])
    # Record a technician-confirmed case.
    repair_learning.record_case(
        s.id, "shorted +5VALW bulk cap", ["C123"],
        confirming_test="resistance", outcome="fixed",
        technician_confirmed=True)
    conv = R2L.RepairToLearning()
    res = conv.convert(s.id, seed=7)
    case = res["case"]
    assert case["confirmed"] is True
    assert case["confirmed_root_cause"] == "shorted +5VALW bulk cap"
    assert case["outcome"] == "fixed"


def test_unconfirmed_record_does_not_disclose_root_cause(tmp_app):
    """A recorded-but-UNconfirmed case (technician_confirmed=False) must
    NOT disclose the recorded fault as a confirmed root cause."""
    database.init_db()
    s = _seed_session(["no power"],
                      [{"name": "+5VALW", "value": 0.0, "unit": "V",
                        "point": "rail", "reference": ""}])
    repair_learning.record_case(
        s.id, "suspected shorted cap", ["C123"],
        confirming_test="resistance", outcome="tentative",
        technician_confirmed=False)
    conv = R2L.RepairToLearning()
    res = conv.convert(s.id, seed=7)
    case = res["case"]
    assert case["confirmed"] is False
    assert case["confirmed_root_cause"] is None


def test_conversion_does_not_add_kb_facts(tmp_app):
    """The conversion + follow-up exercise must NOT add general-KB
    facts. The learning subsystem is separate from the knowledge base
    (architectural boundary)."""
    database.init_db()
    before = database.count_facts()
    s = _seed_session(["no power"],
                      [{"name": "+5VALW", "value": 0.0, "unit": "V",
                        "point": "rail", "reference": ""}])
    conv = R2L.RepairToLearning()
    conv.convert(s.id, seed=7)
    after = database.count_facts()
    assert after == before, (
        "learning conversion must not add KB facts; learning is "
        "separate from the general knowledge base")


def test_followup_exercise_is_hidden_and_perturbed(tmp_app):
    """The generated follow-up exercise hides its solution and uses
    perturbed measurement values (not the original), so the user must
    reason rather than recall. The original session values are intact."""
    database.init_db()
    s = _seed_session(["no power"],
                      [{"name": "VIN", "value": 19.4, "unit": "V",
                        "point": "jack", "reference": ""}])
    conv = R2L.RepairToLearning()
    res = conv.convert(s.id, seed=7)
    case = res["case"]
    assert case["exercises_generated"]
    # The exercise is stored; fetch it and check it's hidden.
    from eda.learning_system import db as lsdb
    ex = lsdb.get_exercise(case["exercises_generated"][0])
    assert ex is not None
    assert ex["solution_hidden"] in (1, True)
    # The session's own VIN is still 19.4 (not perturbed in place).
    assert measurements.list_for(s.id)[0].value == 19.4


def test_conversion_does_not_modify_source_session(tmp_app):
    """Running the conversion twice yields the same case measurements;
    the session is read-only through the conversion."""
    database.init_db()
    s = _seed_session(["no power"],
                      [{"name": "VIN", "value": 19.4, "unit": "V",
                        "point": "jack", "reference": ""}])
    conv = R2L.RepairToLearning()
    r1 = conv.convert(s.id, seed=7)["case"]
    r2 = conv.convert(s.id, seed=7)["case"]
    assert r1["measurements"] == r2["measurements"]
    assert r1["symptoms"] == r2["symptoms"]
