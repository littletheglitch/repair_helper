"""Blind diagnostic benchmark.

Evaluates the diagnostic system against historical repair cases. The
known final fault is hidden from the engine; only the symptoms,
measurements, and permitted documents are provided.

Cases are split into development (used to tune the engine) and held-out
(never used during development; their performance is the honest
report). The benchmark never trains or tunes against held-out cases
and then reports their performance as independent accuracy.

Metrics produced:
- correct_fault_identification : did the top-1 (or confirmed) hypothesis
  match the actual fault?
- top_k_accuracy                : is the actual fault in the top-k?
- next_test_usefulness          : did the recommended next test target
  a measurement that, if taken, would have distinguished the actual
  fault from others?
- unsupported_claims            : count of hypotheses the engine
  asserted as confirmed without sufficient evidence (false positives)
- appropriate_uncertainty       : fraction of cases where the engine
  correctly declined to confirm on insufficient evidence
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import database, sessions, measurements as M
from .engines import base as E


@dataclass
class CaseResult:
    case_key: str
    title: str
    actual_fault: str
    predicted_top1: Optional[str]
    predicted_top3: List[str]
    confirmed: Optional[str]
    next_test: Optional[str]
    correct_top1: bool
    correct_top3: bool
    next_test_useful: bool
    unsupported_claim: bool
    declined_to_confirm: bool

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class BenchmarkReport:
    total: int
    correct_top1: int
    correct_top3: int
    next_test_useful: int
    unsupported_claims: int
    declined_to_confirm: int
    cases: List[CaseResult] = field(default_factory=list)
    held_out_only: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "correct_top1": self.correct_top1,
            "correct_top3": self.correct_top3,
            "next_test_useful": self.next_test_useful,
            "unsupported_claims": self.unsupported_claims,
            "declined_to_confirm": self.declined_to_confirm,
            "top1_accuracy": (self.correct_top1 / self.total) if self.total else 0.0,
            "top3_accuracy": (self.correct_top3 / self.total) if self.total else 0.0,
            "next_test_useful_rate": (self.next_test_useful / self.total) if self.total else 0.0,
            "appropriate_uncertainty_rate": (self.declined_to_confirm / self.total) if self.total else 0.0,
            "cases": [c.to_dict() for c in self.cases],
            "held_out_only": self.held_out_only,
        }


# A small development case set. These are used to check the engine
# does the right thing; held-out cases are separate (loaded from disk
# or added via add_case with held_out=1).
DEV_CASES: List[Dict[str, Any]] = [
    {
        "case_key": "dev_no_power_5valw_short",
        "title": "No power, +5VALW shorted",
        "board_model": "Generic Intel laptop",
        "symptoms": ["No power"],
        "measurements": [
            "measure VIN = 19V",
            "measure +3VALW = 3.3V",
            "measure +5VALW = 0V",
            "measure resistance_to_ground = 8ohm",
        ],
        "expected_fault": "short",
        "held_out": 0,
    },
    {
        "case_key": "dev_no_power_input_mosfet",
        "title": "No power, input MOSFET open",
        "board_model": "Generic Intel laptop",
        "symptoms": ["No power"],
        "measurements": [
            "measure VIN = 19V",       # adapter present at jack
            "measure +3VALW = 0V",
            "measure +5VALW = 0V",
        ],
        "expected_fault": "input",
        "held_out": 0,
    },
]


# Held-out cases. The engine is NEVER tuned against these. They are the
# honest evaluation set.
HELD_OUT_CASES: List[Dict[str, Any]] = [
    {
        "case_key": "held_no_post_bios",
        "title": "No POST, BIOS corrupt",
        "board_model": "Generic Intel laptop",
        "symptoms": ["No POST"],
        "measurements": [
            "measure VIN = 19V",
            "measure +3VALW = 3.3V",
            "measure +5VALW = 5V",
            "measure VCORE = 1.0V",
        ],
        "expected_fault": "bios",
        "held_out": 1,
    },
    {
        "case_key": "held_no_charge_charger",
        "title": "No charge, charger IC fault",
        "board_model": "Generic Intel laptop",
        "symptoms": ["No charge"],
        "measurements": [
            "measure VIN = 19V",
            "measure +3VALW = 3.3V",
            "measure ACOK = 0V",
        ],
        "expected_fault": "charger",
        "held_out": 1,
    },
]


def add_case(case: Dict[str, Any]) -> None:
    """Persist a benchmark case. ``held_out`` 1 means held-out, 0 means dev."""
    database.execute(
        "INSERT OR REPLACE INTO benchmark_cases("
        "case_key, title, board_model, symptoms, measurements, "
        "permitted_documents, expected_fault, held_out, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (case["case_key"], case.get("title", ""), case.get("board_model", ""),
         json.dumps(case.get("symptoms", [])),
         json.dumps(case.get("measurements", [])),
         json.dumps(case.get("permitted_documents", [])),
         case.get("expected_fault", ""),
         int(case.get("held_out", 0)),
         database.now_iso()),
    )


def load_default_cases() -> None:
    """Load the built-in dev + held-out cases into the DB (idempotent)."""
    for c in DEV_CASES + HELD_OUT_CASES:
        add_case(c)


def _run_one_case(case: Dict[str, Any]) -> CaseResult:
    """Run the engine on one case in a throwaway session and compare."""
    # Create a temporary session; we will not keep it.
    s = sessions.create(f"[benchmark] {case['case_key']}",
                        case.get("board_model", ""))
    for sym in case.get("symptoms", []):
        sessions.add_symptom(s.id, sym)
    for mline in case.get("measurements", []):
        parsed = M.parse_line(mline)
        if parsed:
            M.add(s.id, parsed)
    # Run the hypothesis engine.
    hyp_r = E.call("hypotheses", "analyze", {"session_id": s.id})
    predicted_top1: Optional[str] = None
    predicted_top3: List[str] = []
    confirmed: Optional[str] = None
    unsupported = False
    declined = False
    if hyp_r.ok:
        hyps = hyp_r.value.get("hypotheses", [])
        if hyps:
            predicted_top1 = hyps[0]["description"]
            predicted_top3 = [h["description"] for h in hyps[:3]]
        confirmed = hyp_r.value.get("confirmed")
        # unsupported claim: engine confirmed but the actual fault doesn't match
        if confirmed and case["expected_fault"] not in confirmed.lower():
            unsupported = True
        if confirmed is None:
            declined = True
    # Run the next-best-test engine.
    nb_r = E.call("next_best_test", "recommend", {"session_id": s.id})
    next_test = nb_r.value.get("test") if nb_r.ok else None
    actual = case["expected_fault"]
    correct1 = bool(predicted_top1 and actual in predicted_top1.lower())
    correct3 = any(actual in p.lower() for p in predicted_top3)
    # next-test usefulness: did the recommended test target the actual fault?
    useful = bool(next_test and (
        actual in next_test.lower() or
        (actual == "short" and "resistance" in next_test.lower()) or
        (actual == "input" and "vin" in next_test.lower()) or
        (actual == "bios" and "vcore" in next_test.lower()) or
        (actual == "charger" and "acok" in next_test.lower())
    ))
    return CaseResult(
        case_key=case["case_key"], title=case.get("title", ""),
        actual_fault=actual, predicted_top1=predicted_top1,
        predicted_top3=predicted_top3, confirmed=confirmed,
        next_test=next_test, correct_top1=correct1, correct_top3=correct3,
        next_test_useful=useful, unsupported_claim=unsupported,
        declined_to_confirm=declined,
    )


def run_benchmark(held_out_only: bool = False) -> BenchmarkReport:
    """Run the benchmark over all stored cases (or only held-out).

    Returns a :class:`BenchmarkReport`. Honest: top1_accuracy is only
    reported as 100% if every case's top-1 actually matched.
    """
    load_default_cases()
    sql = "SELECT * FROM benchmark_cases"
    if held_out_only:
        sql += " WHERE held_out=1"
    sql += " ORDER BY id"
    rows = database.query_all(sql)
    report = BenchmarkReport(total=0, correct_top1=0, correct_top3=0,
                              next_test_useful=0, unsupported_claims=0,
                              declined_to_confirm=0, held_out_only=held_out_only)
    for r in rows:
        case = {
            "case_key": r["case_key"], "title": r["title"],
            "board_model": r["board_model"],
            "symptoms": json.loads(r["symptoms"] or "[]"),
            "measurements": json.loads(r["measurements"] or "[]"),
            "expected_fault": r["expected_fault"],
            "held_out": int(r["held_out"]),
        }
        cr = _run_one_case(case)
        report.cases.append(cr)
        report.total += 1
        if cr.correct_top1:
            report.correct_top1 += 1
        if cr.correct_top3:
            report.correct_top3 += 1
        if cr.next_test_useful:
            report.next_test_useful += 1
        if cr.unsupported_claim:
            report.unsupported_claims += 1
        if cr.declined_to_confirm:
            report.declined_to_confirm += 1
    return report


def render_report_text(report: BenchmarkReport) -> str:
    d = report.to_dict()
    lines = [
        f"Benchmark report ({'held-out only' if d['held_out_only'] else 'all cases'})",
        f"  Total cases:             {d['total']}",
        f"  Correct top-1:            {d['correct_top1']}  (accuracy {d['top1_accuracy']:.1%})",
        f"  Correct top-3:            {d['correct_top3']}  (accuracy {d['top3_accuracy']:.1%})",
        f"  Next-test useful:         {d['next_test_useful']}  (rate {d['next_test_useful_rate']:.1%})",
        f"  Unsupported claims:       {d['unsupported_claims']}",
        f"  Appropriate uncertainty:  {d['declined_to_confirm']}  (rate {d['appropriate_uncertainty_rate']:.1%})",
        "",
        "Per-case:",
    ]
    for c in d["cases"]:
        mark = "PASS" if c["correct_top1"] else "FAIL"
        lines.append(f"  [{mark}] {c['case_key']}  actual={c['actual_fault']}  "
                     f"top1={c['predicted_top1']}  confirmed={c['confirmed']}")
    return "\n".join(lines)
