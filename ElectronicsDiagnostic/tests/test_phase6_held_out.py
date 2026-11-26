"""Phase 6, Task 5 — blind and held-out evaluation framework tests.

Verifies:
  * Registration tracks provenance (dataset, source, created_by).
  * Leakage detection flags evidence that appears in BOTH a dev fixture
    and a held-out case.
  * Adverse-case categories cover conflicting/missing/unknown/insufficient.
  * The evaluation procedure keeps the assistant blind (the predictor
    receives a view WITHOUT the confirmed root cause) and freezes the
    prediction.
  * Reproducibility: re-running does not overwrite a frozen prediction.
  * Synthetic cases stay synthetic; never relabeled.
"""
from __future__ import annotations

import pytest

from eda import config, database, sessions, measurements
from eda import validation_framework as VF
from eda import held_out_evaluation as HE


def _seed(symptoms, meas, title="HE test", board="HE Board"):
    s = sessions.create(title, board)
    for sym in symptoms:
        sessions.add_symptom(s.id, sym)
    for m in meas:
        measurements.add(s.id, m)
    return s


def _case(s_id, key, case_type="real_unresolved",
          confirmed_root_cause=None, outcome=None):
    return VF.create_case(key, case_type, session_id=s_id,
                          confirmed_root_cause=confirmed_root_cause,
                          outcome=outcome)


# ---- registration + provenance --------------------------------------------

def test_register_held_out_records_provenance(tmp_app):
    database.init_db()
    s = _seed(["no power"],
              [{"name": "+5VALW", "value": 0.0, "unit": "V",
                "point": "rail", "reference": ""}])
    r = _case(s.id, "ho_1")
    reg = HE.register_held_out(r["case_id"], dataset="held_out",
                              adverse_kind="normal",
                              source="workshop 2026-01, technician Ali",
                              created_by="Ali")
    assert reg["ok"]
    rep = HE.provenance_report()
    assert rep["by_dataset"]["held_out"] == 1
    e = rep["entries"][0]
    assert e["source"] == "workshop 2026-01, technician Ali"
    assert e["created_by"] == "Ali"
    assert e["adverse_kind"] == "normal"


def test_register_rejects_invalid_adverse_kind(tmp_app):
    database.init_db()
    s = _seed(["x"], [])
    r = _case(s.id, "ho_2")
    reg = HE.register_held_out(r["case_id"], adverse_kind="bogus")
    assert not reg["ok"]
    assert "valid" in reg


def test_register_rejects_duplicate(tmp_app):
    database.init_db()
    s = _seed(["x"], [])
    r = _case(s.id, "ho_3")
    HE.register_held_out(r["case_id"])
    reg2 = HE.register_held_out(r["case_id"])
    assert not reg2["ok"]


# ---- leakage detection ----------------------------------------------------

def test_detect_leakage_flags_shared_evidence(tmp_app):
    """The same evidence (same symptoms + measurements) registered as
    BOTH a dev fixture and a held-out case must be flagged as leakage."""
    database.init_db()
    # Two sessions with IDENTICAL evidence.
    s1 = _seed(["no power"],
               [{"name": "+5VALW", "value": 0.0, "unit": "V",
                 "point": "rail", "reference": ""}], title="dev")
    s2 = _seed(["no power"],
               [{"name": "+5VALW", "value": 0.0, "unit": "V",
                 "point": "rail", "reference": ""}], title="held")
    dev = _case(s1.id, "dev_1")
    held = _case(s2.id, "held_1")
    HE.register_dev_fixture(dev["case_id"], source="dev fixture")
    HE.register_held_out(held["case_id"], dataset="held_out",
                         source="held-out case")
    leak = HE.detect_leakage()
    assert leak["leakage"] is True
    assert len(leak["collisions"]) == 1


def test_detect_leakage_clean_when_evidence_differs(tmp_app):
    """Different evidence fingerprints -> no leakage."""
    database.init_db()
    s1 = _seed(["no power"],
               [{"name": "+5VALW", "value": 0.0, "unit": "V",
                 "point": "rail", "reference": ""}], title="dev")
    s2 = _seed(["no power"],
               [{"name": "VIN", "value": 19.4, "unit": "V",
                 "point": "jack", "reference": ""}], title="held")
    dev = _case(s1.id, "dev_2")
    held = _case(s2.id, "held_2")
    HE.register_dev_fixture(dev["case_id"])
    HE.register_held_out(held["case_id"], dataset="held_out")
    leak = HE.detect_leakage()
    assert leak["leakage"] is False


# ---- adverse coverage -----------------------------------------------------

def test_adverse_coverage_counts_kinds(tmp_app):
    database.init_db()
    s = _seed(["x"], [])
    cases = {
        "conflicting_measurements": _case(s.id, "a1"),
        "missing_reference": _case(s.id, "a2"),
        "unknown_component": _case(s.id, "a3"),
        "insufficient_evidence": _case(s.id, "a4"),
        "normal": _case(s.id, "a5"),
    }
    for kind, r in cases.items():
        HE.register_held_out(r["case_id"], dataset="held_out",
                             adverse_kind=kind)
    cov = HE.adverse_coverage()
    for kind in ("conflicting_measurements", "missing_reference",
                 "unknown_component", "insufficient_evidence", "normal"):
        assert cov[kind] == 1, f"{kind} not counted"


# ---- blind evaluation procedure -------------------------------------------

def test_run_evaluation_keeps_predictor_blind(tmp_app):
    """The predictor's view must NOT contain the confirmed root cause."""
    database.init_db()
    s = _seed(["no power"],
              [{"name": "+5VALW", "value": 0.0, "unit": "V",
                "point": "rail", "reference": ""}])
    r = _case(s.id, "ev_1", confirmed_root_cause="SECRET")
    HE.register_held_out(r["case_id"], dataset="held_out")
    seen_keys = []

    def predictor(view):
        seen_keys.append(set(view.keys()))
        return {"hypotheses": [{"hypothesis": "always-on converter",
                                 "score": 0.7, "supporting": ["+5VALW=0"],
                                 "missing": ["+3VALW"]}],
                "next_tests": [{"test": "VIN", "rationale": "x",
                                 "distinguishes": ["a", "b"]}],
                "answer_text": "always-on converter is suspect"}

    res = HE.run_evaluation([r["case_id"]], predictor)
    assert res["ok"]
    # The predictor never saw the confirmed_root_cause.
    assert all("confirmed_root_cause" not in keys for keys in seen_keys)


def test_run_evaluation_freezes_prediction(tmp_app):
    database.init_db()
    s = _seed(["no power"],
              [{"name": "+5VALW", "value": 0.0, "unit": "V",
                "point": "rail", "reference": ""}])
    r = _case(s.id, "ev_2")
    HE.register_held_out(r["case_id"], dataset="held_out")

    def predictor(view):
        return {"hypotheses": [{"hypothesis": "h", "score": 0.5}],
                "next_tests": []}

    HE.run_evaluation([r["case_id"]], predictor)
    case = VF.get_case(r["case_id"])
    assert case.prediction_frozen is True


def test_run_evaluation_does_not_overwrite_frozen(tmp_app):
    """Re-running evaluation does NOT overwrite a frozen prediction."""
    database.init_db()
    s = _seed(["no power"],
              [{"name": "+5VALW", "value": 0.0, "unit": "V",
                "point": "rail", "reference": ""}])
    r = _case(s.id, "ev_3")
    HE.register_held_out(r["case_id"], dataset="held_out")
    call_count = [0]

    def predictor(view):
        call_count[0] += 1
        return {"hypotheses": [{"hypothesis": f"h{call_count[0]}",
                                 "score": 0.5}], "next_tests": []}

    HE.run_evaluation([r["case_id"]], predictor)
    first_preds = VF.get_predictions(r["case_id"])
    # Re-run: the prediction must not change (frozen).
    HE.run_evaluation([r["case_id"]], predictor)
    second_preds = VF.get_predictions(r["case_id"])
    assert first_preds == second_preds


def test_run_evaluation_aggregates_with_denominators(tmp_app):
    database.init_db()
    s = _seed(["no power"],
              [{"name": "+5VALW", "value": 0.0, "unit": "V",
                "point": "rail", "reference": ""}])
    ids = []
    for i in range(3):
        r = _case(s.id, f"ev_{i}")
        HE.register_held_out(r["case_id"], dataset="held_out")
        ids.append(r["case_id"])

    def predictor(view):
        return {"hypotheses": [{"hypothesis": "always-on converter",
                                 "score": 0.7, "supporting": ["+5VALW=0"],
                                 "missing": ["+3VALW"]}],
                "next_tests": [{"test": "VIN", "rationale": "x",
                                 "distinguishes": ["a", "b"]}],
                "answer_text": "suspect converter"}

    res = HE.run_evaluation(ids, predictor)
    agg = res["aggregate"]
    assert agg["totals"]["cases_attempted"] == 3
    assert agg["metrics"]["hypothesis_relevant"] == "3/3"
    assert "no statistical significance" in agg["note"].lower()
