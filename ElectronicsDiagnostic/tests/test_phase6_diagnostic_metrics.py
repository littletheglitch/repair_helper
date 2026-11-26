"""Phase 6, Task 4 — diagnostic quality metrics tests.

Verifies the metrics are transparent (no single misleading accuracy %),
report explicit denominators, and never claim statistical significance.
"""
from __future__ import annotations

import pytest

from eda import config, database, sessions, measurements
from eda import validation_framework as VF
from eda import diagnostic_metrics as DM


def _seed(symptoms, meas, title="DM test", board="DM Board"):
    s = sessions.create(title, board)
    for sym in symptoms:
        sessions.add_symptom(s.id, sym)
    for m in meas:
        measurements.add(s.id, m)
    return s


def _make_case(s_id, case_type="real_unresolved", key="dm1"):
    return VF.create_case(key, case_type, session_id=s_id,
                          board_model="DM Board")


# ---- per-case metrics -----------------------------------------------------

def test_hypothesis_relevance_with_evidence(tmp_app):
    database.init_db()
    s = _seed(["no power"],
              [{"name": "+5VALW", "value": 0.0, "unit": "V",
                "point": "rail", "reference": ""}])
    r = _make_case(s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "always-on converter", "score": 0.7,
                           "supporting": ["+5VALW=0V"], "missing": ["+3VALW"]}],
                         [])
    rep = DM.evaluate_case(r["case_id"])
    assert rep["ok"]
    assert rep["report"]["metrics"]["hypothesis_relevance"]["value"] is True


def test_hypothesis_relevance_without_evidence(tmp_app):
    database.init_db()
    s = _seed(["no power"], [])
    r = _make_case(s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "always-on converter", "score": 0.7,
                           "supporting": [], "missing": []}], [])
    rep = DM.evaluate_case(r["case_id"])
    assert rep["report"]["metrics"]["hypothesis_relevance"]["value"] is False


def test_unsupported_claims_from_audit(tmp_app):
    database.init_db()
    s = _seed(["x"], [])
    r = _make_case(s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h", "score": 0.5}], [])
    audit = {"unsourced_claims": [{"text": "the rail is 3.3V"}]}
    rep = DM.evaluate_case(r["case_id"], audit=audit)
    assert rep["report"]["metrics"]["unsupported_claims"]["value"] == 1


def test_unsupported_claims_none_without_audit(tmp_app):
    database.init_db()
    s = _seed(["x"], [])
    r = _make_case(s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h", "score": 0.5}], [])
    rep = DM.evaluate_case(r["case_id"])
    assert rep["report"]["metrics"]["unsupported_claims"]["value"] is None


def test_missing_info_identified(tmp_app):
    database.init_db()
    s = _seed(["no power"],
              [{"name": "+5VALW", "value": 0.0, "unit": "V",
                "point": "rail", "reference": ""}])
    r = _make_case(s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "always-on converter", "score": 0.7,
                           "missing": ["+3VALW"]}], [])
    rep = DM.evaluate_case(r["case_id"])
    assert rep["report"]["metrics"]["missing_info_identified"]["value"] is True


def test_missing_info_not_identified(tmp_app):
    database.init_db()
    s = _seed(["x"], [])
    r = _make_case(s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h", "score": 0.5, "missing": []}], [])
    rep = DM.evaluate_case(r["case_id"])
    assert rep["report"]["metrics"]["missing_info_identified"]["value"] is False


def test_next_test_quality_decisive(tmp_app):
    database.init_db()
    s = _seed(["x"], [])
    r = _make_case(s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h", "score": 0.5}],
                         [{"test": "Measure VIN", "rationale": "decisive",
                           "distinguishes": ["adapter_open", "converter_fault"]}])
    rep = DM.evaluate_case(r["case_id"])
    nt = rep["report"]["metrics"]["next_test_quality"]
    assert nt["value"] is True
    assert nt["distinguishes_competing"] is True


def test_next_test_not_distinguishes_competing(tmp_app):
    database.init_db()
    s = _seed(["x"], [])
    r = _make_case(s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h", "score": 0.5}],
                         [{"test": "Measure VIN", "rationale": "decisive",
                           "distinguishes": ["only_one"]}])
    rep = DM.evaluate_case(r["case_id"])
    nt = rep["report"]["metrics"]["next_test_quality"]
    assert nt["value"] is True  # decisive for >= 1
    assert nt["distinguishes_competing"] is False  # only 1


def test_hypothesis_updated_between_rounds(tmp_app):
    database.init_db()
    s = _seed(["x"], [])
    r = _make_case(s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h1", "score": 0.7}], [])
    rep = DM.evaluate_case(
        r["case_id"],
        prediction_round_2=[{"hypothesis": "h2", "score": 0.9}])
    assert rep["report"]["metrics"]["hypothesis_updated"]["value"] is True


def test_hypothesis_updated_none_with_one_round(tmp_app):
    database.init_db()
    s = _seed(["x"], [])
    r = _make_case(s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h1", "score": 0.7}], [])
    rep = DM.evaluate_case(r["case_id"])
    assert rep["report"]["metrics"]["hypothesis_updated"]["value"] is None


def test_premature_confirmation_detected(tmp_app):
    """For a real_unresolved case, claiming 'repair confirmed' is premature."""
    database.init_db()
    s = _seed(["x"], [])
    r = _make_case(s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h", "score": 0.5}], [])
    rep = DM.evaluate_case(r["case_id"],
                           answer_text="The repair is confirmed. "
                           "Fault confirmed.")
    assert rep["report"]["metrics"]["premature_confirmation"]["value"] is True


def test_premature_confirmation_not_for_confirmed_case(tmp_app):
    """For a technician_confirmed case, 'confirmed' phrasing is not premature."""
    database.init_db()
    s = _seed(["x"], [])
    r = _make_case(s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h", "score": 0.5}], [])
    VF.reveal_and_confirm(r["case_id"], "fault", "fixed", "T")
    rep = DM.evaluate_case(r["case_id"],
                           answer_text="The repair is confirmed.")
    assert rep["report"]["metrics"]["premature_confirmation"]["value"] is False


def test_agreement_only_for_confirmed(tmp_app):
    database.init_db()
    s = _seed(["x"], [])
    r = _make_case(s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "always-on converter", "score": 0.7}],
                         [])
    # Not confirmed yet -> agreement is None.
    rep = DM.evaluate_case(r["case_id"])
    assert rep["report"]["metrics"]["agreement_with_confirmed"]["value"] is None
    # Confirm -> agreement computed.
    VF.reveal_and_confirm(r["case_id"], "always-on converter failure", "fixed", "T")
    rep2 = DM.evaluate_case(r["case_id"])
    assert rep2["report"]["metrics"]["agreement_with_confirmed"]["value"] is True


# ---- aggregation ----------------------------------------------------------

def test_aggregate_reports_explicit_denominators(tmp_app):
    database.init_db()
    s = _seed(["no power"],
              [{"name": "+5VALW", "value": 0.0, "unit": "V",
                "point": "rail", "reference": ""}])
    ids = []
    for i in range(3):
        r = VF.create_case(f"agg_{i}", "real_unresolved", session_id=s.id)
        VF.record_prediction(r["case_id"],
                             [{"hypothesis": "always-on converter", "score": 0.7,
                               "supporting": ["+5VALW=0"], "missing": ["+3VALW"]}],
                             [{"test": "VIN", "rationale": "x",
                               "distinguishes": ["a", "b"]}])
        ids.append(r["case_id"])
    agg = DM.aggregate(ids)
    # Totals report denominators explicitly.
    assert agg["totals"]["cases_attempted"] == 3
    assert agg["totals"]["cases_with_sufficient_evidence"] == 3
    # Metrics are "x/y" strings, not a single percentage.
    assert agg["metrics"]["hypothesis_relevant"] == "3/3"
    assert agg["metrics"]["next_test_distinguishes_competing"] == "3/3"
    # No statistical-significance claim.
    assert "no statistical significance" in agg["note"].lower()


def test_aggregate_separates_case_types(tmp_app):
    """The aggregate separates synthetic / unresolved / confirmed counts."""
    database.init_db()
    s = _seed(["x"], [])
    # 1 synthetic, 2 real_unresolved, 1 confirmed
    r1 = VF.create_case("a", "synthetic", session_id=s.id)
    VF.record_prediction(r1["case_id"], [{"hypothesis": "h", "score": 0.5}], [])
    r2 = VF.create_case("b", "real_unresolved", session_id=s.id)
    VF.record_prediction(r2["case_id"], [{"hypothesis": "h", "score": 0.5}], [])
    r3 = VF.create_case("c", "real_unresolved", session_id=s.id)
    VF.record_prediction(r3["case_id"], [{"hypothesis": "h", "score": 0.5}], [])
    r4 = VF.create_case("d", "real_unresolved", session_id=s.id)
    VF.record_prediction(r4["case_id"], [{"hypothesis": "h", "score": 0.5}], [])
    VF.reveal_and_confirm(r4["case_id"], "fault", "fixed", "T")
    agg = DM.aggregate([r1["case_id"], r2["case_id"], r3["case_id"], r4["case_id"]])
    assert agg["totals"]["by_type"]["synthetic"] == 1
    assert agg["totals"]["by_type"]["real_unresolved"] == 2
    assert agg["totals"]["by_type"]["technician_confirmed"] == 1
    assert agg["totals"]["technician_confirmed_cases"] == 1
    assert agg["totals"]["cases_unresolved"] == 2


def test_aggregate_no_single_accuracy_percentage(tmp_app):
    """The aggregate must NOT expose a single 'accuracy' field that could
    be misread as a quality summary. Metrics are per-attribute counts."""
    database.init_db()
    s = _seed(["x"], [])
    r = VF.create_case("acc", "synthetic", session_id=s.id)
    VF.record_prediction(r["case_id"], [{"hypothesis": "h", "score": 0.5}], [])
    agg = DM.aggregate([r["case_id"]])
    assert "accuracy" not in agg
    assert "accuracy" not in agg["metrics"]
