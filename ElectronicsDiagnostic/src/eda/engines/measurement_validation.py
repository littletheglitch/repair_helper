"""Measurement validation engine.

Compares recorded measurements against verified reference ranges,
detects contradictions across operating conditions, and surfaces
missing context. Returns a structured :class:`EngineResult` whose
``value`` is a validation verdict (``normal`` / ``low`` / ``high`` /
``out_of range`` / ``contradicted`` / ``insufficient context``) plus a
list of findings.

Important rules enforced here:
- Measured values are never compared against theoretical values
  silently; the verdict always names whether the comparison is to a
  verified reference range, a datasheet value, or another measurement.
- Measurements from different operating conditions (on adapter vs on
  battery, powered vs unpowered) are never compared as if directly
  equivalent; a contradiction finding is only raised when the conditions
  match.
- Missing context is a finding, not an error; the engine still returns
  a result. The error envelope is reserved for malformed inputs.

The engine reads reference ranges from the local SQLite DB
(``reference_ranges`` table, added by the migration layer) but also
accepts an explicit ``range`` input so it is testable without a DB.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .base import Engine, MethodSpec
from ..schemas import (
    DimensionalError, EngineError, EngineResult, ErrorCode,
    Quantity, _Dim,
)
from .. import measurements as M


VERDICT_NORMAL = "normal"
VERDICT_LOW = "low"
VERDICT_HIGH = "high"
VERDICT_OUT_OF_RANGE = "out_of_range"
VERDICT_CONTRADICTED = "contradicted"
VERDICT_INSUFFICIENT_CONTEXT = "insufficient_context"


def _as_quantity(v: Any) -> Quantity:
    if isinstance(v, Quantity):
        return v
    if isinstance(v, tuple) and len(v) == 2:
        return Quantity.parse(v[0], v[1])
    if isinstance(v, str):
        return Quantity.parse(v)
    if isinstance(v, dict):
        # Accept {"value": Quantity | number, "unit": str} or {"value": "19V"}.
        val = v.get("value")
        if isinstance(val, Quantity):
            return val
        if isinstance(val, (int, float)):
            return Quantity.parse(val, v.get("unit", "V"))
        if isinstance(val, str):
            return Quantity.parse(val)
    raise ValueError(f"cannot coerce {v!r} to a Quantity")


@dataclass
class Finding:
    """One observation the validation engine made about a measurement."""
    code: str            # normal | low | high | out_of_range | contradicted | insufficient_context
    message: str
    reference: str = ""  # what was the comparison basis

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "reference": self.reference}


@dataclass
class ValidationVerdict:
    """The structured value returned by the validation engine."""
    verdict: str
    findings: List[Finding] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"verdict": self.verdict,
                "findings": [f.to_dict() for f in self.findings]}


class MeasurementValidationEngine(Engine):
    name = "measurement_validation"
    version = "1.0.0"
    summary = ("Validates measurements against verified reference ranges, "
               "detects contradictions across matching operating conditions, "
               "and surfaces missing context. Never compares measurements from "
               "different operating conditions as equivalent.")
    provenance = ("Original implementation. Reference ranges are sourced from "
                  "the local `reference_ranges` table (technician-verified) or "
                  "passed explicitly via the `range` input.")

    methods = {
        "validate": MethodSpec(
            "validate", "Validate one measurement against a reference range",
            {"name": ("string", True), "value": ("any", True),
             "unit": ("string", False), "range": ("dict", False),
             "operating_condition": ("string", False)},
            "ValidationVerdict"),
        "compare": MethodSpec(
            "compare", "Compare two measurements of the same name across matching operating conditions",
            {"a": ("dict", True), "b": ("dict", True), "tolerance": ("number", False)},
            "ValidationVerdict"),
        "session_review": MethodSpec(
            "session_review", "Review all measurements in a session for missing context and contradictions",
            {"session_id": ("number", True)}, "ValidationVerdict"),
    }

    # ---- validate ---------------------------------------------------
    def _validate(self, inputs: Dict[str, Any]) -> EngineResult:
        name = inputs.get("name", "")
        if not name:
            return self._err("validate", ErrorCode.MISSING_INPUT, "missing name", "name")
        if "value" not in inputs:
            return self._err("validate", ErrorCode.MISSING_INPUT, "missing value", "value")
        # Coerce value to a Quantity.
        raw_value = inputs["value"]
        unit_hint = inputs.get("unit", "")
        try:
            if isinstance(raw_value, Quantity):
                q = raw_value
            elif isinstance(raw_value, (int, float)):
                if not unit_hint:
                    return self._err("validate", ErrorCode.MISSING_INPUT,
                                     "unit required when value is a bare number",
                                     "unit")
                q = Quantity.parse(raw_value, unit_hint)
            elif isinstance(raw_value, dict):
                q = _as_quantity(raw_value)
            elif isinstance(raw_value, str):
                q = Quantity.parse(raw_value)
            else:
                q = _as_quantity(raw_value)
        except (ValueError, DimensionalError) as e:
            return self._err("validate", ErrorCode.INVALID_INPUT, str(e), "value")
        rng = inputs.get("range")
        verdict = ValidationVerdict(verdict=VERDICT_INSUFFICIENT_CONTEXT)
        if rng is None:
            verdict.findings.append(Finding(
                code=VERDICT_INSUFFICIENT_CONTEXT,
                message=(f"No reference range supplied for '{name}'. The value "
                         f"{q.render()} cannot be judged normal/abnormal without a "
                         "verified range."),
            ))
        else:
            try:
                lo = float(rng["lo"]); hi = float(rng["hi"])
            except (KeyError, TypeError, ValueError) as e:
                return self._err("validate", ErrorCode.INVALID_INPUT,
                                 f"invalid range: {e}", "range")
            v = q.magnitude
            ref_desc = rng.get("source", "explicit range")
            if v < lo:
                verdict.verdict = VERDICT_LOW
                verdict.findings.append(Finding(
                    code=VERDICT_LOW,
                    message=f"{name} = {q.render()} is below the verified range [{lo}, {hi}].",
                    reference=ref_desc,
                ))
            elif v > hi:
                verdict.verdict = VERDICT_HIGH
                verdict.findings.append(Finding(
                    code=VERDICT_HIGH,
                    message=f"{name} = {q.render()} is above the verified range [{lo}, {hi}].",
                    reference=ref_desc,
                ))
            else:
                verdict.verdict = VERDICT_NORMAL
                verdict.findings.append(Finding(
                    code=VERDICT_NORMAL,
                    message=f"{name} = {q.render()} is within the verified range [{lo}, {hi}].",
                    reference=ref_desc,
                ))
        # Operating condition context.
        oc = inputs.get("operating_condition", "")
        if not oc:
            verdict.findings.append(Finding(
                code=VERDICT_INSUFFICIENT_CONTEXT,
                message=(f"Operating condition (adapter/battery, powered/unpowered) "
                         f"for {name} is not recorded; interpretation may change."),
            ))
        return self._ok("validate", value=verdict, value_si=None, unit="",
                        inputs={"name": name, "value": q.render(),
                                "range": rng, "operating_condition": oc},
                        formulas=[],
                        assumptions=["reference range applies at the stated operating condition"],
                        limitations=["without a verified range, only context gaps are reported"])

    # ---- compare ----------------------------------------------------
    def _compare(self, inputs: Dict[str, Any]) -> EngineResult:
        a = inputs.get("a"); b = inputs.get("b")
        if not isinstance(a, dict) or not isinstance(b, dict):
            return self._err("compare", ErrorCode.INVALID_INPUT,
                             "a and b must be measurement dicts", "a")
        try:
            qa = _as_quantity(a)
            qb = _as_quantity(b)
        except (ValueError, DimensionalError) as e:
            return self._err("compare", ErrorCode.INVALID_INPUT, str(e))
        # Operating conditions must match for a contradiction to be meaningful.
        oc_a = a.get("operating_condition", "")
        oc_b = b.get("operating_condition", "")
        tol = float(inputs.get("tolerance", 0.05))  # 5% default
        verdict = ValidationVerdict(verdict=VERDICT_NORMAL)
        if oc_a and oc_b and oc_a != oc_b:
            verdict.verdict = VERDICT_INSUFFICIENT_CONTEXT
            verdict.findings.append(Finding(
                code=VERDICT_INSUFFICIENT_CONTEXT,
                message=(f"Measurements taken under different conditions "
                         f"({oc_a} vs {oc_b}); not directly comparable."),
                reference="operating-condition rule",
            ))
            return self._ok("compare", value=verdict, inputs={"a": a, "b": b, "tolerance": tol},
                            assumptions=["different operating conditions are not equivalent"])
        # Same (or missing) conditions: compare.
        if qa.dimension != qb.dimension:
            return self._err("compare", ErrorCode.DIMENSIONAL,
                             f"dimensions differ: {qa.dimension.name()} vs {qb.dimension.name()}",
                             "b")
        rel = abs(qa.magnitude - qb.magnitude) / max(abs(qa.magnitude), abs(qb.magnitude), 1e-15)
        if rel > tol:
            verdict.verdict = VERDICT_CONTRADICTED
            verdict.findings.append(Finding(
                code=VERDICT_CONTRADICTED,
                message=(f"Two measurements of the same name differ by "
                         f"{rel*100:.1f}% (> {tol*100:.1f}% tolerance): "
                         f"{qa.render()} vs {qb.render()}."),
                reference="tolerance comparison",
            ))
        else:
            verdict.findings.append(Finding(
                code=VERDICT_NORMAL,
                message=f"Measurements agree within {tol*100:.1f}% tolerance.",
                reference="tolerance comparison",
            ))
        return self._ok("compare", value=verdict, inputs={"a": a, "b": b, "tolerance": tol},
                        assumptions=[f"tolerance = {tol}"])

    # ---- session_review ---------------------------------------------
    def _session_review(self, inputs: Dict[str, Any]) -> EngineResult:
        sid = inputs.get("session_id")
        if sid is None:
            return self._err("session_review", ErrorCode.MISSING_INPUT,
                             "missing session_id", "session_id")
        ms = M.list_for(int(sid))
        if not ms:
            return self._err("session_review", ErrorCode.NOT_APPLICABLE,
                             "no measurements in session")
        verdict = ValidationVerdict(verdict=VERDICT_INSUFFICIENT_CONTEXT)
        # Missing context via the existing manager.
        for gap in M.identify_missing_context(int(sid)):
            verdict.findings.append(Finding(
                code=VERDICT_INSUFFICIENT_CONTEXT, message=gap,
                reference="measurement manager",
            ))
        # Detect duplicate-name measurements with inconsistent values
        # taken under the same conditions (potential contradictions).
        from collections import defaultdict
        by_name = defaultdict(list)
        for m in ms:
            by_name[m.name].append(m)
        for name, group in by_name.items():
            if len(group) < 2:
                continue
            # Only flag if values differ and adapter_state matches.
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    if a.value is None or b.value is None:
                        continue
                    if (a.adapter_state or "") != (b.adapter_state or ""):
                        continue  # different conditions: not a contradiction
                    if abs(a.value - b.value) > max(1e-6, 0.05 * max(abs(a.value), abs(b.value), 1e-9)):
                        verdict.findings.append(Finding(
                            code=VERDICT_CONTRADICTED,
                            message=(f"Two measurements of '{name}' under the same "
                                     f"adapter state differ by >5%: "
                                     f"{a.value} {a.unit} vs {b.value} {b.unit}."),
                            reference="same-condition comparison",
                        ))
        if not any(f.code == VERDICT_CONTRADICTED for f in verdict.findings):
            verdict.verdict = VERDICT_NORMAL if not any(
                f.code == VERDICT_INSUFFICIENT_CONTEXT for f in verdict.findings
            ) else VERDICT_INSUFFICIENT_CONTEXT
        return self._ok("session_review", value=verdict, inputs={"session_id": sid},
                        assumptions=["5% tolerance for same-condition contradictions"])

    # ---- dispatch ---------------------------------------------------
    _DISPATCH = None

    def run(self, method: str, inputs: Dict[str, Any]) -> EngineResult:
        fn = MeasurementValidationEngine._DISPATCH.get(method)
        if fn is None:
            return self._err(method, ErrorCode.NOT_APPLICABLE, f"unknown method '{method}'")
        return fn(self, inputs)


MeasurementValidationEngine._DISPATCH = {
    "validate": MeasurementValidationEngine._validate,
    "compare": MeasurementValidationEngine._compare,
    "session_review": MeasurementValidationEngine._session_review,
}
