"""Phase 5, Task 4 — next-test selection is evidence-based, not canned.

Verifies the next-best-test recommendation:
  * is computed from the CURRENT active hypotheses (not a fixed sequence),
  * changes when the active hypotheses change (new evidence -> new rec),
  * carries a rationale that names the specific hypotheses it distinguishes,
  * states its expected observation and how different outcomes affect
    the hypotheses (the `outcomes` list),
  * does NOT claim a calibrated probability (the gain is a transparent
    heuristic count, documented as such),
  * skips tests already taken.
"""
from __future__ import annotations

import pytest

from eda import config, database, sessions, measurements
from eda import llm_backend as LB
from eda import orchestrator as ORCH
from eda.engines import next_best_test as NBT


def _seed_no_power():
    s = sessions.create("NBT no power (synthetic)", "Synth NBT-A")
    sessions.add_symptom(s.id, "no power")
    measurements.add(s.id, {"name": "+5VALW", "value": 0.0, "unit": "V",
                            "point": "rail", "reference": ""})
    return s


def test_next_test_has_transparent_information_gain(tmp_app):
    """The recommendation reports an `information_gain` field that is a
    transparent count-derived heuristic, not a fabricated probability."""
    database.init_db()
    s = _seed_no_power()
    orch = ORCH.Orchestrator(backend=LB.StubBackend())
    r = orch.run(s.id)
    nbt = [st for st in r.tool_trace
           if st.call.get("tool", "").startswith("next_best_test")]
    assert nbt
    res = nbt[0].result
    assert res.get("ok")
    val = res.get("value", {})
    # The gain field exists and is a non-negative number.
    ig = val.get("information_gain")
    assert isinstance(ig, (int, float)) and ig >= 0
    # The rationale names the distinguished hypotheses (not a canned string).
    rat = val.get("rationale", "")
    assert "decisive for" in rat.lower() or "distinguish" in rat.lower()


def test_next_test_changes_with_evidence(tmp_app):
    """Adding decisive evidence changes the recommendation — the engine
    does NOT return a fixed canned sequence."""
    database.init_db()
    s = _seed_no_power()
    # First recommendation with only +5VALW=0.
    rec1 = NBT.NextBestTestEngine().run("recommend", {"session_id": s.id})
    # Add VIN measurement (decisive for the adapter-open hypothesis).
    measurements.add(s.id, {"name": "VIN", "value": 19.4, "unit": "V",
                            "point": "jack", "reference": ""})
    rec2 = NBT.NextBestTestEngine().run("recommend", {"session_id": s.id})
    # The two recommendations differ (either the test name or the
    # distinguished-hypotheses set changes with new evidence).
    def _sig(res):
        if not res.ok:
            return ("err", str(res.error))
        v = res.value
        return (v.get("test"), tuple(sorted(v.get("distinguishes", []))))
    assert _sig(rec1) != _sig(rec2), (
        "next-test recommendation must change when new evidence arrives; "
        "a fixed canned sequence would be a defect")


def test_next_test_outcomes_describe_branch_effects(tmp_app):
    """Each recommended test lists `outcomes` describing how different
    observed results affect the hypotheses — not a single expected value."""
    database.init_db()
    s = _seed_no_power()
    rec = NBT.NextBestTestEngine().run("recommend", {"session_id": s.id})
    assert rec.ok
    outcomes = rec.value.get("outcomes", [])
    assert len(outcomes) >= 2, (
        "a useful test must describe at least 2 outcome branches")
    # Each outcome names a condition (what you would observe) and an
    # implication (which hypothesis it supports/refutes).
    for o in outcomes:
        assert o.get("condition") or o.get("if_observed")
        assert o.get("implication") or o.get("supports") or o.get("refutes")


def test_next_test_skips_already_taken(tmp_app):
    """A test whose target measurement is already taken is not
    recommended again — the engine dedupes by normalized measurement
    name, so adding the VIN measurement removes the VIN recommendation."""
    database.init_db()
    s = _seed_no_power()
    rec1 = NBT.NextBestTestEngine().run("recommend", {"session_id": s.id})
    assert rec1.ok
    test1 = rec1.value.get("test")
    # The first recommendation for a no-power +5VALW=0 case is to
    # measure VIN. Add that measurement (mark it taken) and re-run.
    measurements.add(s.id, {"name": "VIN", "value": 19.4, "unit": "V",
                            "point": "jack", "reference": ""})
    rec2 = NBT.NextBestTestEngine().run("recommend", {"session_id": s.id})
    if rec2.ok:
        test2 = rec2.value.get("test")
        # The second recommendation is a DIFFERENT test (VIN was taken).
        assert test2 != test1, (
            "engine must not recommend a measurement already taken")


def test_next_test_no_fabricated_probability(tmp_app):
    """The recommendation must not present a calibrated probability as
    if it were measured. The `information_gain` is a transparent
    heuristic count, and the rationale says so (or omits probability)."""
    database.init_db()
    s = _seed_no_power()
    rec = NBT.NextBestTestEngine().run("recommend", {"session_id": s.id})
    assert rec.ok
    blob = repr(rec.value).lower()
    # No claim of a measured/calibrated probability.
    assert "p =" not in blob and "probability" not in blob or "not a calibrated probability" in blob
