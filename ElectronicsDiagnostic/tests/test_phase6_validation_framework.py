"""Phase 6, Task 3 — validation framework lifecycle tests.

Verifies the end-to-end diagnostic validation framework:
  * Case types A (synthetic), B (real_unresolved), C (technician_confirmed).
  * Blind evaluation: the confirmed root cause is NOT exposed before
    the prediction is frozen.
  * The prediction, once frozen, is immutable.
  * Reveal requires a frozen prediction (blind-evaluation integrity).
  * Synthetic cases cannot be confirmed as real repairs.
  * Promotion to the verified repair KB is the only path and is gated
    by the existing learning.record_case mechanism.
  * JSON + Markdown export carry the full evidence + prediction.
"""
from __future__ import annotations

import json

import pytest

from eda import config, database, sessions, measurements
from eda import validation_framework as VF


def _seed_session(symptoms, meas_list, title="VF test", board="VF Board"):
    s = sessions.create(title, board)
    for sym in symptoms:
        sessions.add_symptom(s.id, sym)
    for m in meas_list:
        measurements.add(s.id, m)
    return s


# ---------------------------------------------------------------------------
# Case types
# ---------------------------------------------------------------------------

def test_three_case_types_accepted(tmp_app):
    """create_case accepts the three documented types and rejects others."""
    database.init_db()
    s = _seed_session(["no power"], [])
    for ct in ("synthetic", "real_unresolved", "technician_confirmed"):
        r = VF.create_case(f"k_{ct}", ct, board_model="B", session_id=s.id)
        assert r["ok"], f"{ct} should be accepted: {r}"
    r = VF.create_case("k_bad", "bogus", board_model="B")
    assert not r["ok"]
    assert "valid_types" in r


def test_case_key_unique(tmp_app):
    """Duplicate case_key is rejected."""
    database.init_db()
    s = _seed_session(["x"], [])
    VF.create_case("dup_key", "real_unresolved", session_id=s.id)
    r = VF.create_case("dup_key", "real_unresolved", session_id=s.id)
    assert not r["ok"]
    assert "already exists" in r["error"]


# ---------------------------------------------------------------------------
# Blind evaluation
# ---------------------------------------------------------------------------

def test_blind_view_hides_confirmed_root_cause(tmp_app):
    """get_case_for_prediction must NOT include confirmed_root_cause."""
    database.init_db()
    s = _seed_session(["no power"],
                      [{"name": "+5VALW", "value": 0.0, "unit": "V",
                        "point": "rail", "reference": ""}])
    r = VF.create_case("blind_1", "real_unresolved",
                       session_id=s.id, confirmed_root_cause="SECRET ANSWER")
    bv = VF.get_case_for_prediction(r["case_id"])
    assert bv["ok"]
    view = bv["view"]
    assert "confirmed_root_cause" not in view
    assert view["prediction_frozen"] is False


def test_blind_view_carries_evidence(tmp_app):
    """The blind view carries symptoms + measurements with units,
    reference, board state, and timestamps."""
    database.init_db()
    s = _seed_session(["no power"],
                      [{"name": "VIN", "value": 19.4, "unit": "V",
                        "point": "jack", "reference": "GND"}])
    r = VF.create_case("blind_2", "real_unresolved", session_id=s.id)
    bv = VF.get_case_for_prediction(r["case_id"])
    ms = bv["view"]["measurements"]
    assert len(ms) == 1
    assert ms[0]["name"] == "VIN"
    assert ms[0]["value"] == 19.4
    assert ms[0]["unit"] == "V"
    assert ms[0]["reference"] == "GND"
    assert ms[0]["taken_at"]  # timestamp present


def test_prediction_freezes_and_is_immutable(tmp_app):
    """record_prediction freezes; a second record_prediction is rejected."""
    database.init_db()
    s = _seed_session(["no power"],
                      [{"name": "+5VALW", "value": 0.0, "unit": "V",
                        "point": "rail", "reference": ""}])
    r = VF.create_case("frz_1", "real_unresolved", session_id=s.id)
    cid = r["case_id"]
    rp = VF.record_prediction(cid, [
        {"hypothesis": "always-on converter", "score": 0.7,
         "supporting": ["+5VALW=0"], "missing": ["+3VALW"]}],
        [{"test": "Measure VIN", "rationale": "decisive",
          "distinguishes": ["adapter_open"]}])
    assert rp["ok"] and rp["frozen"]
    # Second attempt rejected.
    rp2 = VF.record_prediction(cid, [{"hypothesis": "other", "score": 0.1}],
                              [])
    assert not rp2["ok"]
    assert "frozen" in rp2["error"].lower()
    # The case is now frozen in the view.
    bv = VF.get_case_for_prediction(cid)
    assert bv["view"]["prediction_frozen"] is True


def test_reveal_requires_frozen_prediction(tmp_app):
    """reveal_and_confirm before freezing is rejected (blind integrity)."""
    database.init_db()
    s = _seed_session(["x"], [])
    r = VF.create_case("rev_1", "real_unresolved", session_id=s.id)
    rv = VF.reveal_and_confirm(r["case_id"], "fault", "fixed", "Tech")
    assert not rv["ok"]
    assert "not frozen" in rv["error"].lower()
    # After freezing, reveal works.
    VF.freeze_prediction(r["case_id"])
    rv2 = VF.reveal_and_confirm(r["case_id"], "fault", "fixed", "Tech")
    assert rv2["ok"]


def test_synthetic_cannot_be_confirmed(tmp_app):
    """A synthetic case cannot be confirmed as a real repair."""
    database.init_db()
    s = _seed_session(["x"], [])
    r = VF.create_case("syn_1", "synthetic", session_id=s.id)
    VF.freeze_prediction(r["case_id"])
    rv = VF.reveal_and_confirm(r["case_id"], "fake fault", "fixed")
    assert not rv["ok"]
    assert "synthetic" in rv["error"].lower()


def test_create_synthetic_confirmed_rejected(tmp_app):
    """Creating a synthetic case with a 'fixed' outcome + technician is
    rejected at creation (it would be a fake confirmed repair)."""
    database.init_db()
    s = _seed_session(["x"], [])
    r = VF.create_case("syn_bad", "synthetic", session_id=s.id,
                       confirmed_root_cause="x", outcome="fixed",
                       technician="Fake")
    assert not r["ok"]


# ---------------------------------------------------------------------------
# Results + comparison
# ---------------------------------------------------------------------------

def test_record_result_and_export(tmp_app):
    """record_result stores an observed test result; export carries it."""
    database.init_db()
    s = _seed_session(["no power"],
                      [{"name": "+5VALW", "value": 0.0, "unit": "V",
                        "point": "rail", "reference": ""}])
    r = VF.create_case("res_1", "real_unresolved", session_id=s.id)
    cid = r["case_id"]
    VF.record_prediction(cid,
                         [{"hypothesis": "always-on converter", "score": 0.7}],
                         [])
    VF.freeze_prediction(cid)
    rr = VF.record_result(cid, "VIN", "19.4 V present", "adapter ok")
    assert rr["ok"]
    exp = VF.export_case(cid, "json")
    assert exp["ok"]
    assert any(x["test_name"] == "VIN" for x in exp["content"]["results"])


def test_comparison_transparent_overlap(tmp_app):
    """compare_with_confirmed returns a transparent word-overlap, not a
    single auto-score, and flags technician review."""
    database.init_db()
    s = _seed_session(["no power"],
                      [{"name": "+5VALW", "value": 0.0, "unit": "V",
                        "point": "rail", "reference": ""}])
    r = VF.create_case("cmp_1", "real_unresolved", session_id=s.id)
    cid = r["case_id"]
    VF.record_prediction(cid,
                         [{"hypothesis": "always-on converter failure",
                           "score": 0.7}], [])
    VF.reveal_and_confirm(cid, "always-on converter failure", "fixed", "T")
    comp = VF.compare_with_confirmed(cid)
    assert comp["ok"]
    assert comp["comparison"]["overlap_ratio"] > 0
    assert comp["comparison"]["technician_should_review"] is True


def test_comparison_none_when_blind(tmp_app):
    """Before the cause is revealed, comparison is None (still blind)."""
    database.init_db()
    s = _seed_session(["x"], [])
    r = VF.create_case("cmp_2", "real_unresolved", session_id=s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h", "score": 0.5}], [])
    comp = VF.compare_with_confirmed(r["case_id"])
    assert comp["comparison"] is None


# ---------------------------------------------------------------------------
# Promotion to verified repair KB (the only path)
# ---------------------------------------------------------------------------

def test_promote_requires_technician_confirmed(tmp_app):
    """promote_to_repair_kb rejects non-confirmed cases."""
    database.init_db()
    s = _seed_session(["no power"],
                      [{"name": "+5VALW", "value": 0.0, "unit": "V",
                        "point": "rail", "reference": ""}])
    r = VF.create_case("prom_1", "real_unresolved", session_id=s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h", "score": 0.5}], [])
    VF.reveal_and_confirm(r["case_id"], "fault", "fixed", "T")
    # case_type is now technician_confirmed (reveal set it), but let's
    # test the explicit path: a still-real_unresolved case.
    r2 = VF.create_case("prom_2", "real_unresolved", session_id=s.id)
    VF.freeze_prediction(r2["case_id"])
    # Do NOT reveal/confirm r2 -> stays real_unresolved.
    p = VF.promote_to_repair_kb(r2["case_id"])
    assert not p["ok"]


def test_promote_uses_existing_record_case(tmp_app):
    """Promotion of a confirmed case calls learning.record_case with
    technician_confirmed=True and adds it to repair_cases."""
    database.init_db()
    from eda import learning
    s = _seed_session(["no power"],
                      [{"name": "+5VALW", "value": 0.0, "unit": "V",
                        "point": "rail", "reference": ""}])
    r = VF.create_case("prom_3", "real_unresolved", session_id=s.id)
    cid = r["case_id"]
    VF.record_prediction(cid,
                         [{"hypothesis": "always-on converter", "score": 0.7}],
                         [])
    VF.reveal_and_confirm(cid, "always-on converter failure", "fixed", "Ali")
    p = VF.promote_to_repair_kb(cid)
    assert p["ok"]
    # The repair_cases table now has the case.
    cases = learning.list_cases(technician_confirmed_only=True)
    assert any(c.get("case_key") == "prom_3" for c in cases)


def test_promote_rejects_non_fixed_outcome(tmp_app):
    """Promotion requires outcome='fixed'."""
    database.init_db()
    s = _seed_session(["x"], [])
    r = VF.create_case("prom_4", "real_unresolved", session_id=s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h", "score": 0.5}], [])
    VF.reveal_and_confirm(r["case_id"], "fault", "no-fix", "T")
    p = VF.promote_to_repair_kb(r["case_id"])
    assert not p["ok"]
    assert "fixed" in p["error"].lower()


# ---------------------------------------------------------------------------
# Export formats
# ---------------------------------------------------------------------------

def test_export_markdown(tmp_app):
    database.init_db()
    s = _seed_session(["no power"],
                      [{"name": "VIN", "value": 19.4, "unit": "V",
                        "point": "jack", "reference": "GND"}])
    r = VF.create_case("exp_1", "real_unresolved", session_id=s.id)
    cid = r["case_id"]
    VF.record_prediction(cid,
                         [{"hypothesis": "always-on converter", "score": 0.7,
                           "supporting": ["+5VALW=0"], "missing": ["+3VALW"]}],
                         [{"test": "Measure VIN", "rationale": "decisive",
                           "distinguishes": ["adapter_open"]}])
    VF.reveal_and_confirm(cid, "always-on converter failure", "fixed", "Ali")
    exp = VF.export_case(cid, "markdown")
    assert exp["ok"]
    md = exp["content"]
    assert "Validation Case" in md
    assert "always-on converter" in md
    assert "Measure VIN" in md
    assert "technician must review" in md.lower()


def test_export_json_round_trip(tmp_app):
    database.init_db()
    s = _seed_session(["x"],
                      [{"name": "V", "value": 1.0, "unit": "V",
                        "point": "p", "reference": ""}])
    r = VF.create_case("exp_2", "synthetic", session_id=s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h", "score": 0.5}], [])
    exp = VF.export_case(r["case_id"], "json")
    assert exp["ok"]
    # JSON-serializable.
    blob = json.dumps(exp["content"], ensure_ascii=False)
    assert "hypothesis" in blob


def test_export_unknown_format_rejected(tmp_app):
    database.init_db()
    s = _seed_session(["x"], [])
    r = VF.create_case("exp_3", "synthetic", session_id=s.id)
    exp = VF.export_case(r["case_id"], "yaml")
    assert not exp["ok"]


# ---------------------------------------------------------------------------
# Listing + types distinction
# ---------------------------------------------------------------------------

def test_list_cases_by_type(tmp_app):
    """list_cases filters by case_type; the three types stay distinct."""
    database.init_db()
    s = _seed_session(["x"], [])
    VF.create_case("a1", "synthetic", session_id=s.id)
    VF.create_case("b1", "real_unresolved", session_id=s.id)
    r = VF.create_case("c1", "real_unresolved", session_id=s.id)
    VF.freeze_prediction(r["case_id"])
    VF.reveal_and_confirm(r["case_id"], "f", "fixed", "T")
    syn = VF.list_cases("synthetic")
    unres = VF.list_cases("real_unresolved")
    conf = VF.list_cases("technician_confirmed")
    assert len(syn) == 1 and syn[0].case_type == "synthetic"
    assert len(unres) == 1 and unres[0].case_type == "real_unresolved"
    assert len(conf) == 1 and conf[0].case_type == "technician_confirmed"
