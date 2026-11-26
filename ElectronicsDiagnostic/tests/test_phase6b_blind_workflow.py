"""Phase 6b — verify the documented blind-validation workflow executes
against the real API. This mirrors scripts/run_blind_validation_example.py
and DEPLOYMENT_PROCEDURE.md section 4b, asserting each documented step.

Covers Task 4 requirements:
  1. Create a real unresolved case linked to an existing session.
  2. Freeze the assistant prediction before the technician reveals.
  3. Record hypotheses, confidence, evidence, and recommended tests.
  4. Reveal and confirm only after prediction freeze.
  5. Prevent any prediction overwrite after reveal.
  6. Promote only technician-confirmed repaired cases through the
     existing authorized mechanism (learning.record_case).
  7. Ensure unresolved/synthetic cases cannot be promoted.
  8. Prevent held-out evidence from leaking into dev fixtures before
     prediction freeze.
"""
from __future__ import annotations

import pytest

from eda import config, database, sessions, measurements
from eda import validation_framework as VF
from eda import held_out_evaluation as HE
from eda import learning


def _seed_session():
    s = sessions.create("Lenovo G50-80 workshop 001", "Lenovo G50-80")
    sessions.add_symptom(s.id, "no power, no LEDs when adapter inserted")
    measurements.add(s.id, {"name": "VIN", "value": 19.4, "unit": "V",
                             "point": "DC jack", "reference": "GND"})
    measurements.add(s.id, {"name": "+5VALW", "value": 0.0, "unit": "V",
                             "point": "always-on rail", "reference": "GND"})
    return s


def _hypotheses():
    return [
        {"hypothesis": "+5VALW regulator not enabled (enable signal absent)",
         "score": 0.45,
         "supporting": ["+5VALW=0V", "VIN=19.4V (adapter ok)"],
         "contradicting": [],
         "missing": ["+5VALW enable signal voltage",
                     "resistance_to_ground on +5VALW"]},
        {"hypothesis": "+5VALW short to ground (low-impedance fault)",
         "score": 0.35,
         "supporting": ["+5VALW=0V"],
         "contradicting": ["VIN present (no over-current shutdown observed)"],
         "missing": ["resistance_to_ground on +5VALW", "current draw on VIN"]},
        {"hypothesis": "open input path between jack and +5VALW stage",
         "score": 0.20,
         "supporting": [],
         "contradicting": ["VIN=19.4V at jack (input path to jack ok)"],
         "missing": ["voltage at +5VALW stage input"]},
    ]


def _next_tests():
    return [
        {"test": "resistance_to_ground on +5VALW (board unpowered)",
         "rationale": "Low R supports a short; high R rules it out.",
         "distinguishes": ["+5VALW short to ground",
                           "+5VALW regulator not enabled"]},
    ]


def test_blind_workflow_creates_unresolved_case_linked_to_session(tmp_app):
    """Step 1: create a real_unresolved case linked to an existing
    session whose evidence (symptoms + measurements) is carried."""
    database.init_db()
    s = _seed_session()
    r = VF.create_case("doc_1", "real_unresolved", session_id=s.id,
                       board_model="Lenovo G50-80", technician="Ali")
    assert r["ok"]
    bv = VF.get_case_for_prediction(r["case_id"])
    assert bv["view"]["symptoms"] == ["no power, no LEDs when adapter inserted"]
    assert len(bv["view"]["measurements"]) == 2


def test_blind_workflow_blind_view_hides_root_cause(tmp_app):
    """Step 2: the blind view does NOT expose the confirmed root cause."""
    database.init_db()
    s = _seed_session()
    r = VF.create_case("doc_2", "real_unresolved", session_id=s.id,
                       confirmed_root_cause="SECRET")
    bv = VF.get_case_for_prediction(r["case_id"])
    assert "confirmed_root_cause" not in bv["view"]


def test_blind_workflow_record_then_freeze_prediction(tmp_app):
    """Step 3: record hypotheses + next tests; the prediction is frozen."""
    database.init_db()
    s = _seed_session()
    r = VF.create_case("doc_3", "real_unresolved", session_id=s.id)
    rec = VF.record_prediction(r["case_id"], _hypotheses(), _next_tests())
    assert rec["ok"] and rec["frozen"]
    case = VF.get_case(r["case_id"])
    assert case.prediction_frozen is True
    # The recorded hypotheses carry confidence + evidence + missing info.
    preds = VF.get_predictions(r["case_id"])
    assert len(preds) == 3
    assert preds[0]["score"] == 0.45
    assert preds[0]["supporting"]
    assert preds[0]["missing"]


def test_blind_workflow_reveal_requires_frozen_prediction(tmp_app):
    """Step 4: reveal_and_confirm is rejected BEFORE the prediction is
    frozen (blind-evaluation integrity)."""
    database.init_db()
    s = _seed_session()
    r = VF.create_case("doc_4", "real_unresolved", session_id=s.id)
    rev = VF.reveal_and_confirm(r["case_id"], "x", "fixed", "Ali")
    assert not rev["ok"]
    assert "not frozen" in rev["error"].lower()
    # After freezing, reveal works.
    VF.freeze_prediction(r["case_id"])
    rev2 = VF.reveal_and_confirm(r["case_id"], "fault", "fixed", "Ali")
    assert rev2["ok"]


def test_blind_workflow_prediction_immutable_after_reveal(tmp_app):
    """Step 5: a second record_prediction after reveal is rejected."""
    database.init_db()
    s = _seed_session()
    r = VF.create_case("doc_5", "real_unresolved", session_id=s.id)
    VF.record_prediction(r["case_id"], _hypotheses(), _next_tests())
    VF.reveal_and_confirm(r["case_id"], "fault", "fixed", "Ali")
    over = VF.record_prediction(r["case_id"],
                                [{"hypothesis": "changed", "score": 0.9}], [])
    assert not over["ok"]
    assert "frozen" in over["error"].lower()


def test_blind_workflow_promote_only_confirmed_fixed(tmp_app):
    """Step 6: promote_to_repair_kb admits only a technician-confirmed,
    fixed case, via learning.record_case."""
    database.init_db()
    s = _seed_session()
    r = VF.create_case("doc_6", "real_unresolved", session_id=s.id)
    VF.record_prediction(r["case_id"], _hypotheses(), _next_tests())
    VF.reveal_and_confirm(r["case_id"],
        "shorted +5VALW bulk decoupling capacitor", "fixed", "Ali")
    p = VF.promote_to_repair_kb(r["case_id"])
    assert p["ok"]
    cases = learning.list_cases(technician_confirmed_only=True)
    assert any(c.get("case_key") == "doc_6" for c in cases)


def test_blind_workflow_synthetic_cannot_promote(tmp_app):
    """Step 7a: a synthetic case cannot be promoted to the verified KB."""
    database.init_db()
    s = _seed_session()
    r = VF.create_case("doc_7", "synthetic", session_id=s.id)
    VF.freeze_prediction(r["case_id"])
    p = VF.promote_to_repair_kb(r["case_id"])
    assert not p["ok"]


def test_blind_workflow_unresolved_nonfixed_cannot_promote(tmp_app):
    """Step 7b: a real_unresolved (non-fixed) case cannot be promoted."""
    database.init_db()
    s = _seed_session()
    r = VF.create_case("doc_8", "real_unresolved", session_id=s.id)
    VF.freeze_prediction(r["case_id"])
    VF.reveal_and_confirm(r["case_id"], "unknown", "no-fix", "Ali")
    p = VF.promote_to_repair_kb(r["case_id"])
    assert not p["ok"]


def test_held_out_leakage_detection_prevents_evidence_leak(tmp_app):
    """Step 8: held-out evidence that also appears as a dev fixture is
    flagged as leakage, preventing it from leaking into development
    context. The blind view is unaffected (root cause still hidden)."""
    database.init_db()
    # Two sessions with IDENTICAL evidence.
    a = sessions.create("dev", "B")
    b = sessions.create("held", "B")
    for s in (a, b):
        sessions.add_symptom(s.id, "no power")
        measurements.add(s.id, {"name": "+5VALW", "value": 0.0, "unit": "V",
                                 "point": "rail", "reference": ""})
    dev = VF.create_case("leak_dev", "synthetic", session_id=a.id)
    held = VF.create_case("leak_held", "real_unresolved", session_id=b.id)
    HE.register_dev_fixture(dev["case_id"], source="dev fixture")
    HE.register_held_out(held["case_id"], dataset="held_out",
                         source="held-out case")
    leak = HE.detect_leakage()
    assert leak["leakage"] is True
    assert len(leak["collisions"]) == 1
    # The blind view of the held-out case still hides the root cause.
    bv = VF.get_case_for_prediction(held["case_id"])
    assert "confirmed_root_cause" not in bv["view"]


def test_held_out_predictor_receives_blind_view_only(tmp_app):
    """The predictor in run_evaluation receives a view WITHOUT the
    confirmed root cause, even for a held-out case that has one."""
    database.init_db()
    s = _seed_session()
    r = VF.create_case("ho_pred", "real_unresolved", session_id=s.id,
                       confirmed_root_cause="SECRET ANSWER")
    HE.register_held_out(r["case_id"], dataset="held_out")
    seen = []

    def predictor(view):
        seen.append(set(view.keys()))
        return {"hypotheses": _hypotheses(), "next_tests": _next_tests(),
                "answer_text": "suspect converter"}

    res = HE.run_evaluation([r["case_id"]], predictor)
    assert res["ok"]
    assert all("confirmed_root_cause" not in keys for keys in seen)


def test_resistance_to_ground_required_before_short_asserted(tmp_app):
    """The documented example must NOT assert a short from VIN+0V alone.
    The hypotheses name resistance_to_ground as missing, and the
    next-test distinguishes the short vs not-enabled hypotheses."""
    database.init_db()
    s = _seed_session()
    r = VF.create_case("safety_1", "real_unresolved", session_id=s.id)
    VF.record_prediction(r["case_id"], _hypotheses(), _next_tests())
    preds = VF.get_predictions(r["case_id"])
    short_hyp = next(p for p in preds
                     if "short" in p["hypothesis"].lower())
    # The short hypothesis explicitly names resistance_to_ground as missing.
    assert any("resistance_to_ground" in m.lower()
               for m in short_hyp["missing"])
    # The recommended next test distinguishes >= 2 hypotheses.
    nts = VF.get_next_tests(r["case_id"])
    assert len(nts[0]["distinguishes"]) >= 2
