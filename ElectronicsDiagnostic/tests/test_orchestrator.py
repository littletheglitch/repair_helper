"""Tests for the diagnostic orchestrator (Phase 3).

Verifies the LLM-driven loop: with the stub backend it must call a
tool, receive a structured result with provenance, and produce an
audited final answer. The audit must catch unsourced technical claims.
The rule engine must always run as a cross-check/fallback.
"""
import pytest

from eda import config, database, sessions, measurements
from eda import llm_backend as LB
from eda import orchestrator as ORCH
from eda import tool_interface as TI


def _seed(session_title="No power test", board="Lenovo G50-80"):
    s = sessions.create(session_title, board)
    sessions.add_symptom(s.id, "no power, no LEDs")
    measurements.add(s.id, {"name": "VIN", "value": 19.4, "unit": "V",
                            "point": "board input"})
    measurements.add(s.id, {"name": "+5VALW", "value": 0.0, "unit": "V",
                            "point": "rail"})
    return s


def test_orchestrator_runs_with_stub_and_returns_result(tmp_app):
    database.init_db()
    s = _seed()
    orch = ORCH.Orchestrator(backend=LB.StubBackend())
    r = orch.run(s.id)
    assert r.session_id == s.id
    assert r.final_answer
    assert r.backend == "stub"
    assert r.elapsed_ms >= 0
    assert r.iterations >= 1


def test_orchestrator_calls_at_least_one_tool(tmp_app):
    database.init_db()
    s = _seed()
    orch = ORCH.Orchestrator(backend=LB.StubBackend())
    r = orch.run(s.id)
    # The stub emits a tool call for a no-power case.
    assert len(r.tool_trace) >= 1
    assert r.tool_trace[0].call["tool"].startswith(
        ("next_best_test", "electrical_math", "measurement_validation"))


def test_orchestrator_tool_results_carry_provenance(tmp_app):
    database.init_db()
    s = _seed()
    orch = ORCH.Orchestrator(backend=LB.StubBackend())
    r = orch.run(s.id)
    for step in r.tool_trace:
        if step.result.get("ok"):
            assert step.result["provenance"]
        else:
            assert step.result["error"]  # structured error, never raises


def test_orchestrator_also_runs_rule_engine(tmp_app):
    """The deterministic rule engine is always run as a cross-check."""
    database.init_db()
    s = _seed()
    orch = ORCH.Orchestrator(backend=LB.StubBackend())
    r = orch.run(s.id)
    assert r.rule_engine_analysis  # non-empty
    assert "Hypothesis" in r.rule_engine_analysis or "hypothes" in r.rule_engine_analysis.lower()


def test_orchestrator_audits_unsourced_claims(tmp_app):
    """The audit must flag a technical claim with no tool/KB source."""
    database.init_db()
    # We cannot easily force the stub to emit an unsourced claim, but we
    # can call the audit helper directly on a fabricated answer.
    orch = ORCH.Orchestrator(backend=LB.StubBackend())
    fake_answer = ("The +5VALW rail is shorted to ground at 0V because the "
                   "TPS51716 IO chip is bad. The resistance is 8Ω. Replace Q34.")
    audit = orch._audit(fake_answer, [], type("Ctx", (), {
        "kb_facts": []})(), "rule text")
    # These are numeric/part claims with no source; the audit should flag them.
    assert len(audit.unsourced_claims) >= 1


def test_orchestrator_detects_stated_gaps(tmp_app):
    """When the answer states a gap, the audit records it (good behaviour)."""
    orch = ORCH.Orchestrator(backend=LB.StubBackend())
    fake_answer = ("I cannot conclude yet. I need to measure VIN before I "
                   "can distinguish the input-MOSFET and standby-converter "
                   "hypotheses. The measurement is missing.")
    audit = orch._audit(fake_answer, [], type("Ctx", (), {
        "kb_facts": []})(), "rule text")
    assert len(audit.gaps_stated) >= 1


def test_orchestrator_falls_back_when_backend_unavailable(tmp_app):
    database.init_db()
    s = _seed()
    orch = ORCH.Orchestrator(backend=LB.DisabledBackend())
    r = orch.run(s.id)
    # Backend returns None; the orchestrator uses the rule engine text.
    assert r.final_answer
    assert "rule engine" in r.note.lower() or r.rule_engine_analysis in r.final_answer


def test_orchestrator_bounded_iterations(tmp_app):
    """The loop must not run forever."""
    database.init_db()
    s = _seed()
    orch = ORCH.Orchestrator(backend=LB.StubBackend())
    r = orch.run(s.id, max_iterations=2)
    assert r.iterations <= 2


def test_orchestration_to_text_includes_tool_trace_and_audit(tmp_app):
    database.init_db()
    s = _seed()
    orch = ORCH.Orchestrator(backend=LB.StubBackend())
    r = orch.run(s.id)
    text = r.to_text()
    assert "Tool trace" in text
    assert "backend=stub" in text


def test_orchestration_to_dict_is_json_serializable(tmp_app):
    import json
    database.init_db()
    s = _seed()
    orch = ORCH.Orchestrator(backend=LB.StubBackend())
    r = orch.run(s.id)
    d = r.to_dict()
    # Must round-trip through JSON without raising.
    json.dumps(d, ensure_ascii=False)
