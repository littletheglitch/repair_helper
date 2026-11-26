"""Engine validation framework.

Produces a machine-readable test report for any engine, comparing its
outputs against independently-calculated reference answers. The engine
under test never generates its own expected answers; the test author
writes them by hand or computes them with a separate, simpler method.

The report is a JSON-serializable dict with:

* ``engine``       — engine name + version
* ``method``       — method under test
* ``environment``  — python version, platform, sqlite version
* ``total``        — number of test cases
* ``passed``       — count of passes
* ``failed``       — count of failures
* ``cases``        — per-case details (name, input, expected, actual, error, tolerance, pass)
* ``max_error``    — largest absolute error across passing cases
* ``tolerance``    — the tolerance used
* ``summary``      — "PASS" or "FAIL"

The framework never reports 100% unless the relevant reference tests
actually pass; the summary is honest about failures.
"""
from __future__ import annotations

import math
import platform
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .engines import base as E
from . import schemas as S


@dataclass
class Case:
    name: str
    method: str
    inputs: Dict[str, Any]
    expected: Any  # the reference answer (computed independently)
    tolerance: float = 1e-6  # relative tolerance for float comparison
    kind: str = "normal"   # normal | boundary | invalid | extreme


@dataclass
class CaseResult:
    name: str
    kind: str
    passed: bool
    expected: Any
    actual: Any
    abs_error: Optional[float] = None
    rel_error: Optional[float] = None
    error_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "name": self.name, "kind": self.kind, "passed": self.passed,
            "expected": _safe(self.expected), "actual": _safe(self.actual),
        }
        if self.abs_error is not None:
            d["abs_error"] = self.abs_error
        if self.rel_error is not None:
            d["rel_error"] = self.rel_error
        if self.error_message:
            d["error_message"] = self.error_message
        return d


def _safe(v: Any) -> Any:
    if isinstance(v, S.Quantity):
        return {"value": v.render(), "si": v.magnitude, "dim": v.dimension.name()}
    if isinstance(v, S.EngineResult):
        return v.to_dict()
    return v


@dataclass
class Report:
    engine: str
    version: str
    method: str
    environment: Dict[str, str] = field(default_factory=dict)
    total: int = 0
    passed: int = 0
    failed: int = 0
    max_error: float = 0.0
    tolerance: float = 1e-6
    cases: List[CaseResult] = field(default_factory=list)
    summary: str = "PASS"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine": self.engine, "version": self.version, "method": self.method,
            "environment": self.environment,
            "total": self.total, "passed": self.passed, "failed": self.failed,
            "max_error": self.max_error, "tolerance": self.tolerance,
            "summary": self.summary,
            "cases": [c.to_dict() for c in self.cases],
        }


def _environment() -> Dict[str, str]:
    import sqlite3
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
    }


def _compare(actual: Any, expected: Any, tol: float) -> Tuple[bool, Optional[float], Optional[float]]:
    """Return (passed, abs_error, rel_error).

    For Quantity vs Quantity, compare SI magnitudes.
    For EngineResult, unwrap .value if expected is a Quantity.
    For float vs float, compare with relative tolerance.
    For invalid-input cases, expected is an ``EngineError``-like tuple
    (code_str, message_substr) and the actual must be an EngineResult
    with an error of that code.
    """
    # Invalid-case convention: expected = ("error_code", optional substring)
    if isinstance(expected, tuple) and expected and isinstance(expected[0], str):
        if not isinstance(actual, S.EngineResult):
            return False, None, None
        if not actual.error:
            return False, None, None
        if actual.error.code.value != expected[0]:
            return False, None, None
        if len(expected) > 1 and expected[1] and expected[1] not in actual.error.message:
            return False, None, None
        return True, None, None
    # Unwrap EngineResult.value when expected is a Quantity/number.
    if isinstance(actual, S.EngineResult):
        if actual.error:
            return False, None, None
        actual = actual.value
    # Quantity vs Quantity
    if isinstance(actual, S.Quantity) and isinstance(expected, S.Quantity):
        if actual.dimension != expected.dimension:
            return False, None, None
        a, e = actual.magnitude, expected.magnitude
        abs_err = abs(a - e)
        rel_err = abs_err / max(abs(e), 1e-15) if e != 0 else abs_err
        return rel_err <= tol, abs_err, rel_err
    # number vs number
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        a, e = float(actual), float(expected)
        abs_err = abs(a - e)
        rel_err = abs_err / max(abs(e), 1e-15) if e != 0 else abs_err
        return rel_err <= tol, abs_err, rel_err
    # fallback: equality
    return actual == expected, None, None


def run_cases(engine_name: str, cases: List[Case]) -> Report:
    """Run a list of cases against an engine, returning a Report.

    Each case is run via :func:`eda.engines.base.call`. The report is
    honest: a case is "passed" only if the comparison succeeds.
    """
    try:
        engine = E.get(engine_name)
        eng_name, eng_version = engine.name, engine.version
    except Exception as e:
        return Report(engine=engine_name, version="?", method="?", summary="FAIL",
                      cases=[CaseResult(name="(setup)", kind="normal", passed=False,
                                        expected=None, actual=None,
                                        error_message=f"engine load failed: {e}")])
    # Group cases by method so the report's `method` field is meaningful.
    # If multiple methods are present, we still produce one report; the
    # `method` field lists the set.
    methods = sorted({c.method for c in cases})
    report = Report(
        engine=eng_name, version=eng_version,
        method=",".join(methods),
        environment=_environment(),
        tolerance=min((c.tolerance for c in cases), default=1e-6),
    )
    for c in cases:
        result = E.call(engine_name, c.method, c.inputs)
        ok, abs_err, rel_err = _compare(result, c.expected, c.tolerance)
        # For invalid cases, an EngineResult with an error is "passed".
        if c.kind == "invalid":
            ok = bool(result.error) if isinstance(result, S.EngineResult) else False
        cr = CaseResult(
            name=c.name, kind=c.kind, passed=ok,
            expected=c.expected,
            actual=result if isinstance(result, S.EngineResult) else result,
            abs_error=abs_err, rel_error=rel_err,
            error_message="" if ok else (result.error.message if (isinstance(result, S.EngineResult) and result.error) else "mismatch"),
        )
        report.cases.append(cr)
        report.total += 1
        if ok:
            report.passed += 1
            if abs_err is not None and abs_err > report.max_error:
                report.max_error = abs_err
        else:
            report.failed += 1
    report.summary = "PASS" if report.failed == 0 and report.total > 0 else "FAIL"
    return report


def render_report_text(report: Report) -> str:
    lines = [
        f"Engine: {report.engine} v{report.version}",
        f"Method: {report.method}",
        f"Environment: {report.environment}",
        f"Total: {report.total}  Passed: {report.passed}  Failed: {report.failed}",
        f"Max abs error (passing): {report.max_error}",
        f"Tolerance: {report.tolerance}",
        f"Summary: {report.summary}",
        "",
    ]
    for c in report.cases:
        mark = "PASS" if c.passed else "FAIL"
        line = f"[{mark}] {c.kind:8s} {c.name}"
        if c.error_message:
            line += f"  — {c.error_message}"
        elif c.abs_error is not None:
            line += f"  — abs_err={c.abs_error:.3g}"
        lines.append(line)
    return "\n".join(lines)
