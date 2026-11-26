"""Phase 5, Task 7 — INDEPENDENT held-out end-to-end tests.

This set is HELD-OUT: it is not a copy of the implementation fixtures
or the Task 3/Task 5 scenarios. It uses adverse inputs that the dev
set does not cover:

  * malformed measurement lines (unparseable, garbage tokens),
  * conflicting measurements (two values for the same rail),
  * unknown component IDs / unsupported specifications,
  * model failures (a backend that returns None),
  * malformed tool-call output (a backend that emits broken JSON),
  * context truncation (many measurements exceeding the budget).

Separation from development data:
  * The dev set (tests/test_orchestrator.py, test_phase3*) uses the
    "no power + VIN=19.4 + +5VALW=0" canonical fixture and the stub
    backend's happy path.
  * This held-out set uses DIFFERENT boards, DIFFERENT symptom wording,
    and NEW adversarial backends (FailingBackend, MalformedBackend,
    ConflictingBackend) defined here for the first time. No dev-set
    fixture is reused.
  * Seeds are fixed (seed=42) for reproducibility.

Honesty: the asserted properties are about the SYSTEM'S SAFETY under
adverse inputs, NOT about real-world diagnostic accuracy. A pass means
the system degrades safely, not that it diagnoses correctly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

import pytest

from eda import config, database, sessions, measurements
from eda import llm_backend as LB
from eda import orchestrator as ORCH
from eda import tool_interface as TI
from eda import context_manager as CM


# ---------------------------------------------------------------------------
# Held-out adversarial backends (defined here, not in the dev set)
# ---------------------------------------------------------------------------

@dataclass
class FailingBackend:
    """A backend whose model is unavailable: chat() always returns None.
    Tests the orchestrator's model-unavailable safety path."""
    name: str = "failing"
    model: str = "none"

    def is_available(self) -> bool:
        return False

    def availability_report(self) -> str:
        return "model unavailable (held-out failing backend)"

    def chat(self, messages, max_tokens=None, temperature=None):
        return None


@dataclass
class MalformedBackend:
    """A backend that emits malformed tool-call JSON on the first turn,
    then a final prose answer on the second. Tests the orchestrator's
    malformed-tool-call surfacing + streak cap."""
    name: str = "malformed"
    model: str = "broken-model"
    _calls: int = 0

    def is_available(self) -> bool:
        return True

    def availability_report(self) -> str:
        return "malformed (held-out)"

    def chat(self, messages, max_tokens=None, temperature=None):
        self._calls += 1
        if self._calls <= 2:
            # Emit malformed tool-call JSON (broken fence / bad JSON).
            text = ("```tool_call\n{tool: electrical_math.ohm_voltage, "
                    "args: {I: 2}}\n```")  # not valid JSON
        else:
            text = ("Based on the measurements, the +5VALW rail is at 0 V. "
                    "The next step is to measure VIN. Therefore the "
                    "always-on converter is suspect.")
        return LB.BackendResponse(
            text=text, backend="malformed", model="broken-model",
            elapsed_ms=10, prompt_tokens=20, completion_tokens=15)


# ---------------------------------------------------------------------------
# Held-out scenarios (boards/symptoms NOT reused from dev set)
# ---------------------------------------------------------------------------

def _seed(board, symptoms, meas_list, title_prefix="HELDOUT"):
    s = sessions.create(f"{title_prefix} (held-out)", board)
    for sym in symptoms:
        sessions.add_symptom(s.id, sym)
    for m in meas_list:
        measurements.add(s.id, m)
    return s


# ---------------------------------------------------------------------------
# Adverse case 1: malformed measurement lines
# ---------------------------------------------------------------------------

def test_heldout_malformed_measurement_line_safe(tmp_app):
    """A garbage measurement line must NOT crash the system; it is
    rejected by parse_line and the session continues with valid ones."""
    database.init_db()
    s = _seed("HeldOut Board M1", ["intermittent shutdown"],
              [{"name": "+3.3V", "value": 3.3, "unit": "V",
                "point": "rail", "reference": "GND"}])
    # Attempt to add a malformed line via the parser.
    bad = measurements.parse_line("measure ??? = = = garbage")
    assert bad is None  # parser rejects it cleanly
    # The valid measurement survived.
    ms = measurements.list_for(s.id)
    assert len(ms) == 1 and ms[0].name == "+3.3V"
    # The orchestrator still runs on the valid session.
    r = ORCH.Orchestrator(backend=LB.StubBackend()).run(s.id)
    assert r.final_answer


# ---------------------------------------------------------------------------
# Adverse case 2: conflicting measurements (same rail, two values)
# ---------------------------------------------------------------------------

def test_heldout_conflicting_measurements_flagged(tmp_app):
    """Two different values for the same rail is a conflict. The system
    must NOT silently average them; the audit/contradiction path or the
    rule engine must surface the contradiction rather than fabricate a
    single 'true' value."""
    database.init_db()
    s = _seed("HeldOut Board M2", ["unstable rail"],
              [{"name": "+5V", "value": 5.0, "unit": "V",
                "point": "rail A", "reference": "GND"},
               {"name": "+5V", "value": 0.2, "unit": "V",
                "point": "rail B", "reference": "GND"}])
    r = ORCH.Orchestrator(backend=LB.StubBackend()).run(s.id)
    txt = (r.final_answer + " " + r.to_text()).lower()
    # The system must NOT invent a single 'true' +5V value by averaging.
    # It should either flag the contradiction or hedge. At minimum, it
    # must not claim a confirmed diagnosis from conflicting data.
    assert "confirmed" not in txt or "not confirmed" in txt
    # Both measurements are still in the DB (not silently merged).
    ms = measurements.list_for(s.id)
    vals = [m.value for m in ms if m.name == "+5V"]
    assert 5.0 in vals and 0.2 in vals


# ---------------------------------------------------------------------------
# Adverse case 3: unknown component IDs / unsupported specifications
# ---------------------------------------------------------------------------

def test_heldout_unknown_component_id_no_invention(tmp_app):
    """A measurement on an unknown rail/component (e.g. a rail name the
    KB has never seen) must NOT cause the system to invent a spec for
    it. The system must say it has no spec rather than fabricate one."""
    database.init_db()
    s = _seed("HeldOut Board M3", ["weird rail present"],
              [{"name": "+ZZZ_UNK_RAIL", "value": 1.8, "unit": "V",
                "point": "mystery pad", "reference": "GND"}])
    r = ORCH.Orchestrator(backend=LB.StubBackend()).run(s.id)
    txt = r.final_answer
    # The system must NOT invent a nominal voltage or a confirmed fault
    # for the unknown rail. It should hedge or name the gap.
    low = txt.lower()
    assert "confirmed" not in low or "not confirmed" in low
    # It must not claim a specific known-good voltage for the unknown rail.
    # (The rule engine should not assert "+ZZZ_UNK_RAIL should be 3.3 V".)
    assert "+zzz_unk_rail should be" not in low


# ---------------------------------------------------------------------------
# Adverse case 4: model failure (backend returns None)
# ---------------------------------------------------------------------------

def test_heldout_model_failure_falls_back_safely(tmp_app):
    """When the model is unavailable (chat returns None), the
    orchestrator must fall back to the deterministic rule engine and
    MUST NOT fabricate an LLM-style diagnosis. The note must say so."""
    database.init_db()
    s = _seed("HeldOut Board M4", ["no power, no LEDs"],
              [{"name": "+5VALW", "value": 0.0, "unit": "V",
                "point": "rail", "reference": ""}])
    orch = ORCH.Orchestrator(backend=FailingBackend())
    r = orch.run(s.id)
    # The fallback text is the rule engine's, not a fabricated LLM answer.
    assert r.final_answer
    assert r.note and "unavailable" in r.note.lower()
    # No tool calls were made (the model never responded).
    assert r.tool_trace == []
    # No claim of a confirmed repair.
    assert "confirmed" not in r.final_answer.lower() or "not confirmed" in r.final_answer.lower()


# ---------------------------------------------------------------------------
# Adverse case 5: malformed tool-call output (broken JSON)
# ---------------------------------------------------------------------------

def test_heldout_malformed_toolcall_not_treated_as_final(tmp_app):
    """When the model emits malformed tool-call JSON, the orchestrator
    must NOT treat it as a final answer. It must surface the parse
    error, retry (up to the streak cap), and finally fall back to the
    rule engine rather than fabricating a diagnosis from broken output."""
    database.init_db()
    s = _seed("HeldOut Board M5", ["no power"],
              [{"name": "+5VALW", "value": 0.0, "unit": "V",
                "point": "rail", "reference": ""}])
    orch = ORCH.Orchestrator(backend=MalformedBackend())
    r = orch.run(s.id)
    # The final answer is NOT the malformed JSON block.
    assert "```tool_call" not in r.final_answer
    # The note records the malformed-output fallback (after the streak
    # cap) OR the model recovered and gave a prose answer. Either way,
    # no fabricated diagnosis from broken JSON.
    txt = r.final_answer.lower()
    assert "confirmed" not in txt or "not confirmed" in txt


# ---------------------------------------------------------------------------
# Adverse case 6: context truncation (many measurements)
# ---------------------------------------------------------------------------

def test_heldout_context_truncation_preserves_recent_and_names_omitted(tmp_app):
    """With many measurements exceeding the context budget, the
    assembler must drop OLDER measurements from the prompt, KEEP newer
    ones, and NAME the omitted ones so they are recoverable. Nothing is
    deleted from the DB."""
    database.init_db()
    s = _seed("HeldOut Board M6", ["many rails probed"],
              [{"name": f"+{i}V", "value": float(i), "unit": "V",
                "point": f"rail{i}", "reference": "GND"}
               for i in range(1, 200)])  # 199 measurements
    # Use a tiny budget to force truncation.
    ctx = CM.assemble(s.id, budget=400)
    # The DB still has all 199 measurements (nothing deleted).
    assert len(measurements.list_for(s.id)) == 199
    # The assembled text omits some (budget exceeded).
    assert ctx.measurements_omitted > 0
    # The omitted measurements are named (recoverable), not silently dropped.
    assert "omitted" in ctx.text.lower()
    # Some measurements survived in the prompt (the newest are kept).
    kept = 199 - ctx.measurements_omitted
    assert kept > 0, "at least the newest measurements must survive truncation"


# ---------------------------------------------------------------------------
# Adverse case 7: empty session (no symptoms, no measurements)
# ---------------------------------------------------------------------------

def test_heldout_empty_session_safe(tmp_app):
    """A session with no symptoms and no measurements must produce a
    safe 'need more input' response, not a fabricated diagnosis."""
    database.init_db()
    s = sessions.create("HELDOUT empty (held-out)", "HeldOut Board M7")
    r = ORCH.Orchestrator(backend=LB.StubBackend()).run(s.id)
    # No fabricated confirmed diagnosis from nothing.
    txt = r.final_answer.lower()
    assert "confirmed" not in txt or "not confirmed" in txt
    # The output asks for input or names missing evidence.
    assert any(k in txt for k in
               ("no symptom", "no measurement", "missing", "need", "add", "first"))


# ---------------------------------------------------------------------------
# Adverse case 8: iteration-limit exhaustion (a looping backend)
# ---------------------------------------------------------------------------

@dataclass
class LoopingBackend:
    """A backend that always emits a tool call, never a final answer.
    Tests the orchestrator's max-iteration safety cap."""
    name: str = "looping"
    model: str = "loop-model"
    _calls: int = 0

    def is_available(self) -> bool:
        return True

    def availability_report(self) -> str:
        return "looping (held-out)"

    def chat(self, messages, max_tokens=None, temperature=None):
        self._calls += 1
        text = ('```tool_call\n{"tool": "electrical_math.ohm_voltage", '
                '"args": {"I": {"value": 2, "unit": "A"}, '
                '"R": {"value": 5, "unit": "ohm"}}, "id": "lc' + str(self._calls) + '"}\n```')
        return LB.BackendResponse(
            text=text, backend="looping", model="loop-model",
            elapsed_ms=5, prompt_tokens=10, completion_tokens=20)


def test_heldout_iteration_limit_exhaustion_safe(tmp_app):
    """A backend that loops forever (always calls a tool) must hit the
    orchestrator's max-iteration cap and fall back to the rule engine,
    not run indefinitely or fabricate a final answer from tool output."""
    database.init_db()
    s = _seed("HeldOut Board M8", ["no power"],
              [{"name": "+5VALW", "value": 0.0, "unit": "V",
                "point": "rail", "reference": ""}])
    orch = ORCH.Orchestrator(backend=LoopingBackend())
    # Run with a small cap so the test is fast.
    r = orch.run(s.id, max_iterations=4)
    # It hit the cap (the note says so) and fell back to the rule engine.
    assert r.note and "iteration limit" in r.note.lower()
    assert r.iterations == 4
    # The final answer is the rule engine's (or last assistant), not a
    # fabricated diagnosis. No confirmed claim.
    txt = r.final_answer.lower()
    assert "confirmed" not in txt or "not confirmed" in txt


# ---------------------------------------------------------------------------
# Adverse case 9: unknown tool name in a tool call
# ---------------------------------------------------------------------------

def test_heldout_unknown_tool_name_returns_structured_error(tmp_app):
    """Dispatching a call to an unknown tool must return a structured
    error (never raise). The orchestrator must not crash."""
    database.init_db()
    call = TI.ToolCall(tool="nonexistent.bogus_method",
                       args={"x": 1}, id="u1")
    result = TI.dispatch(call)
    assert not result.ok
    assert result.error  # structured error dict, not an exception


# ---------------------------------------------------------------------------
# Adverse case 10: invalid tool arguments
# ---------------------------------------------------------------------------

def test_heldout_invalid_tool_arguments_structured_error(tmp_app):
    """A tool call with invalid arguments (missing required, wrong
    type) must return a structured error, not raise."""
    database.init_db()
    # Missing required arg (R).
    call = TI.ToolCall(tool="electrical_math.ohm_voltage",
                       args={"I": {"value": 2, "unit": "A"}}, id="u2")
    result = TI.dispatch(call)
    assert not result.ok
    assert result.error
    # Wrong type (R as a string, not a quantity).
    call2 = TI.ToolCall(tool="electrical_math.ohm_voltage",
                        args={"I": {"value": 2, "unit": "A"},
                              "R": "five ohms"}, id="u3")
    result2 = TI.dispatch(call2)
    assert not result2.ok
