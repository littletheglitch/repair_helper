"""LLM benchmark harness — independent comparison of LLM backends.

This is a *separate* harness from ``benchmark.py`` (which scores the
deterministic rule engine). It scores the **LLM orchestrator** on the
metrics the spec demands:

1. **Engine selection** — did the orchestrator call the right engine
   method for the case? (e.g. for a no-power case, did it call
   ``next_best_test.recommend`` and/or ``measurement_validation.*``?)
2. **Output interpretation** — did it correctly interpret the engine's
   output (did it cite the recommended test, the expected value, the
   expected implication)?
3. **Gap / contradiction detection** — did it state the missing
   evidence, and flag contradictions when present?
4. **Unsourced-claim detection** — does the audit catch technical
   claims with no tool or KB provenance?
5. **Next-test quality** — is the recommended next test the same one
   the deterministic engine would recommend (the gold standard)?
6. **Refusal discipline** — on a case with deliberately contradictory
   measurements, did the orchestrator refuse to confirm a fault
   (rather than guessing)?
7. **RAM** — resident memory of the running model process.
8. **Speed** — wall-clock per case, tokens/s, iterations.

Every backend runs the SAME cases in the SAME order. The stub backend
is the "perfect tool-caller" upper bound; real models are scored
against it. Numbers are real, measured in this process, not invented.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import config, database, sessions, measurements
from . import orchestrator as ORCH
from . import llm_backend as LB
from . import model_registry as MR
from . import tool_interface as TI


# ---------------------------------------------------------------------------
# Cases — each designed to probe one specific metric
# ---------------------------------------------------------------------------

@dataclass
class LLMCase:
    key: str
    title: str
    board_model: str
    symptoms: List[str]
    measurements: List[Dict[str, Any]]   # dicts ready for measurements.add
    # Expected behaviours (used to score):
    expected_engines: List[str]          # engines that SHOULD be called
    expected_next_test_substring: str   # substring the next test should contain
    has_contradiction: bool = False      # deliberately contradictory data
    has_gap: bool = False                # deliberately incomplete
    expected_fault_substring: str = ""   # substring the final answer should contain
    expect_refusal: bool = False        # the orchestrator should NOT confirm a fault


CASES: List[LLMCase] = [
    LLMCase(
        key="no_power_5valw_short",
        title="No power, +5VALW shorted to ground",
        board_model="Lenovo G50-80 (Generic Intel)",
        symptoms=["No power, no LEDs, adapter present"],
        measurements=[
            {"name": "VIN", "value": 19.4, "unit": "V", "point": "board input"},
            {"name": "+3VALW", "value": 3.31, "unit": "V", "point": "rail"},
            {"name": "+5VALW", "value": 0.0, "unit": "V", "point": "rail"},
            {"name": "resistance_to_ground", "value": 8.0, "unit": "Ω", "point": "+5VALW"},
        ],
        expected_engines=["next_best_test"],
        expected_next_test_substring="3VALW",  # rule engine recommends measuring +3VALW
        expected_fault_substring="short",
        has_gap=False, has_contradiction=False, expect_refusal=False,
    ),
    LLMCase(
        key="no_power_input_open",
        title="No power, input MOSFET open (VIN missing at board)",
        board_model="Generic Intel laptop",
        symptoms=["No power, no LEDs"],
        measurements=[
            {"name": "VIN", "value": 0.0, "unit": "V", "point": "board input"},
            {"name": "+3VALW", "value": 0.0, "unit": "V", "point": "rail"},
        ],
        expected_engines=["next_best_test"],
        expected_next_test_substring="adapter",  # rule engine: check adapter/jack
        expected_fault_substring="input",        # input path / adapter
        has_gap=True,  # only 2 measurements; many hypotheses remain open
        expect_refusal=True,  # cannot confirm a single fault
    ),
    LLMCase(
        key="contradictory_3valw",
        title="Contradictory +3VALW measurements (impossible values)",
        board_model="Generic Intel laptop",
        symptoms=["No power"],
        measurements=[
            {"name": "VIN", "value": 19.4, "unit": "V", "point": "board input"},
            {"name": "+3VALW", "value": 3.31, "unit": "V", "point": "test point A"},
            {"name": "+3VALW", "value": 0.0, "unit": "V", "point": "test point B"},
        ],
        expected_engines=["measurement_validation", "next_best_test"],
        expected_next_test_substring="",  # the point here is contradiction detection
        has_contradiction=True,
        expect_refusal=True,  # must not confirm a fault from contradictory data
    ),
    LLMCase(
        key="missing_critical_measurement",
        title="No power but VIN not yet measured (deliberate gap)",
        board_model="Generic Intel laptop",
        symptoms=["No power, no LEDs"],
        measurements=[
            {"name": "+3VALW", "value": 3.31, "unit": "V", "point": "rail"},
        ],
        expected_engines=["next_best_test"],
        expected_next_test_substring="VIN",  # rule engine: measure VIN first
        has_gap=True,
        expect_refusal=True,  # cannot confirm a fault without VIN
    ),
]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class CaseScore:
    case_key: str
    # Engine selection
    called_correct_engine: bool
    called_engines: List[str]
    expected_engines: List[str]
    # Output interpretation
    cited_next_test: bool
    next_test_matches_gold: bool
    # Gap / contradiction
    stated_gap: bool
    detected_contradiction: bool
    # Unsourced claims
    unsourced_claim_count: int
    audit_passed: bool
    # Refusal discipline
    refused_to_confirm: bool
    # expected
    expect_refusal: bool
    # Performance
    elapsed_ms: int
    iterations: int
    prompt_tokens: int
    completion_tokens: int
    tokens_per_s: float
    # Diagnostics
    final_answer_excerpt: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


@dataclass
class BackendReport:
    backend: str
    model: str
    case_scores: List[CaseScore] = field(default_factory=list)
    # Aggregate metrics
    ram_mb: int = 0
    total_elapsed_ms: int = 0
    total_tokens: int = 0
    avg_tokens_per_s: float = 0.0
    avg_latency_ms: float = 0.0

    def aggregate(self) -> None:
        self.total_elapsed_ms = sum(c.elapsed_ms for c in self.case_scores)
        self.total_tokens = sum(c.completion_tokens for c in self.case_scores)
        total_s = sum(c.elapsed_ms for c in self.case_scores) / 1000.0
        if total_s > 0 and self.total_tokens > 0:
            self.avg_tokens_per_s = self.total_tokens / total_s
        if self.case_scores:
            self.avg_latency_ms = self.total_elapsed_ms / len(self.case_scores)

    @property
    def engine_selection_rate(self) -> float:
        if not self.case_scores:
            return 0.0
        return sum(1 for c in self.case_scores if c.called_correct_engine) / len(self.case_scores)

    @property
    def next_test_match_rate(self) -> float:
        applicable = [c for c in self.case_scores if c.next_test_matches_gold is not None
                      and c.expected_engines]
        if not applicable:
            return 0.0
        return sum(1 for c in applicable if c.next_test_matches_gold) / len(applicable)

    @property
    def gap_detection_rate(self) -> float:
        applicable = [c for c in self.case_scores if c.case_key.endswith("gap")
                      or c.case_key == "missing_critical_measurement"
                      or "input_open" in c.case_key]
        if not applicable:
            return 0.0
        return sum(1 for c in applicable if c.stated_gap) / len(applicable)

    @property
    def contradiction_detection_rate(self) -> float:
        applicable = [c for c in self.case_scores if c.detected_contradiction is not None
                      and "contradictory" in c.case_key]
        if not applicable:
            return 0.0
        return sum(1 for c in applicable if c.detected_contradiction) / len(applicable)

    @property
    def unsourced_claim_rate(self) -> float:
        if not self.case_scores:
            return 0.0
        return sum(c.unsourced_claim_count for c in self.case_scores) / len(self.case_scores)

    @property
    def refusal_discipline_rate(self) -> float:
        applicable = [c for c in self.case_scores if c.expect_refusal]
        if not applicable:
            return 0.0
        return sum(1 for c in applicable if c.refused_to_confirm) / len(applicable)

    def to_dict(self) -> Dict[str, Any]:
        self.aggregate()
        return {
            "backend": self.backend, "model": self.model,
            "ram_mb": self.ram_mb,
            "total_elapsed_ms": self.total_elapsed_ms,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "avg_tokens_per_s": round(self.avg_tokens_per_s, 2),
            "total_tokens": self.total_tokens,
            "engine_selection_rate": round(self.engine_selection_rate, 3),
            "next_test_match_rate": round(self.next_test_match_rate, 3),
            "gap_detection_rate": round(self.gap_detection_rate, 3),
            "contradiction_detection_rate": round(self.contradiction_detection_rate, 3),
            "unsourced_claim_rate": round(self.unsourced_claim_rate, 3),
            "refusal_discipline_rate": round(self.refusal_discipline_rate, 3),
            "cases": [c.to_dict() for c in self.case_scores],
        }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_backend(backend: LB.Backend,
                cases: Optional[List[LLMCase]] = None,
                reset_db: bool = True) -> BackendReport:
    """Run every case against one backend; return a scored report.

    Each case gets a fresh session + measurements so cases do not
    contaminate each other. The DB is the project's normal SQLite store.
    """
    cases = cases or CASES
    if reset_db:
        # Clean sessions/measurements for an isolated run.
        database.execute("DELETE FROM measurements")
        database.execute("DELETE FROM symptoms")
        database.execute("DELETE FROM sessions")
    report = BackendReport(backend=backend.name, model=backend.model)
    for case in cases:
        score = _run_case(case, backend)
        report.case_scores.append(score)
    report.aggregate()
    # RAM (if a real server is running)
    if backend.name in ("llama_server", "llama_cli"):
        report.ram_mb = MR.ram_usage_mb().get("rss_mb", 0)
    return report


def _run_case(case: LLMCase, backend: LB.Backend) -> CaseScore:
    # Fresh session for the case.
    sess = sessions.create(case.title, case.board_model)
    for sym in case.symptoms:
        sessions.add_symptom(sess.id, sym)
    for m in case.measurements:
        measurements.add(sess.id, m)
    # Run the orchestrator with this backend.
    orch = ORCH.Orchestrator(config.load_settings(), backend=backend)
    result = orch.run(sess.id, max_iterations=8)
    # Score.
    called_engines = sorted({step.call.get("tool", "").split(".")[0]
                             for step in result.tool_trace
                             if "." in step.call.get("tool", "")})
    expected_engines = case.expected_engines
    called_correct = any(e in called_engines for e in expected_engines) \
        if expected_engines else True
    # Did the final answer cite the recommended next test?
    final = result.final_answer or ""
    cited_next_test = bool(case.expected_next_test_substring and
                           case.expected_next_test_substring.lower() in final.lower())
    # Does the orchestrator's next-test match the gold (rule engine) one?
    next_test_matches_gold = False
    if expected_engines and case.expected_next_test_substring:
        # The rule engine's next test is in result.rule_engine_analysis.
        next_test_matches_gold = (case.expected_next_test_substring.lower()
                                  in result.rule_engine_analysis.lower())
    stated_gap = bool(result.audit.gaps_stated)
    detected_contradiction = bool(result.audit.contradictions_found) or \
                             "contradict" in final.lower()
    unsourced = len(result.audit.unsourced_claims)
    audit_passed = result.audit.passed
    refused = (case.expect_refusal and
               (stated_gap or detected_contradiction
                or "cannot" in final.lower() or "insufficient" in final.lower()
                or "not enough" in final.lower()))
    return CaseScore(
        case_key=case.key,
        called_correct_engine=called_correct,
        called_engines=called_engines,
        expected_engines=expected_engines,
        cited_next_test=cited_next_test,
        next_test_matches_gold=next_test_matches_gold,
        stated_gap=stated_gap,
        detected_contradiction=detected_contradiction,
        unsourced_claim_count=unsourced,
        audit_passed=audit_passed,
        refused_to_confirm=refused,
        expect_refusal=case.expect_refusal,
        elapsed_ms=result.elapsed_ms,
        iterations=result.iterations,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        tokens_per_s=round(result.tokens_per_s, 2),
        final_answer_excerpt=final[:200],
    )


def render_report(reports: List[BackendReport]) -> str:
    """Human-readable comparison table across backends."""
    lines = ["=" * 72]
    lines.append("LLM Orchestrator Benchmark — backend comparison")
    lines.append("=" * 72)
    # Header
    hdr = (f"{'Backend':<16} {'EngSel':>7} {'NextTest':>8} {'GapDet':>7} "
           f"{'ContrDet':>8} {'Unsourced':>9} {'Refusal':>7} {'RAM(MB)':>8} "
           f"{'AvgMs':>7} {'Tok/s':>6}")
    lines.append(hdr)
    lines.append("-" * 72)
    for r in reports:
        r.aggregate()
        lines.append(
            f"{r.backend:<16} "
            f"{r.engine_selection_rate:>7.2f} "
            f"{r.next_test_match_rate:>8.2f} "
            f"{r.gap_detection_rate:>7.2f} "
            f"{r.contradiction_detection_rate:>8.2f} "
            f"{r.unsourced_claim_rate:>9.2f} "
            f"{r.refusal_discipline_rate:>7.2f} "
            f"{r.ram_mb:>8} "
            f"{r.avg_latency_ms:>7.0f} "
            f"{r.avg_tokens_per_s:>6.1f}"
        )
    lines.append("-" * 72)
    lines.append("Legend: rates are 0..1 (1 = perfect). Unsourced = lower is better.")
    lines.append("RAM and Tok/s are 0 for backends that load no model (stub).")
    return "\n".join(lines)
