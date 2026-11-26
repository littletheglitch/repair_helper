"""Bridge: run the existing power-rail engine using framework Measurements.

The existing `DiagnosticEngines.core.engine.DiagnosticEngine` consumes its
own simple `Measurement` dataclass (name/value/measured). The framework
introduces a richer `Measurement` (with state, condition, kind).

This bridge converts a list of framework Measurements into the engine's
format and wraps the engine's `DiagnosisResult` into a framework
`EngineConclusion` so the host can merge it into a DiagnosticSession
uniformly across all engines.

Conversion rules (NO silent interpretation):
  * framework state NOT_MEASURED  -> engine measured=False, value=None
  * framework state MEASURED_ZERO -> engine measured=True, value=0.0
  * framework state IN_RANGE      -> engine measured=True, value=<numeric>
  * framework state BELOW_RANGE / ABOVE_RANGE -> engine measured=True, value=None
                                   (the engine treats this as "measured but no usable value")
  * framework state INVALID / NOT_APPLICABLE -> engine measured=False, value=None
  * framework state UNKNOWN       -> engine measured=False, value=None

The bridge also asserts boolean facts derived from the measurement name.
For example, a framework Measurement named 'rail_short' with value=True
becomes an engine Measurement with name='rail_short', value=True, measured=True.
"""
from __future__ import annotations

from typing import Optional, Any

from .framework import (
    Measurement as FrameworkMeasurement,
    MeasurementState,
)
from .framework.session import EngineConclusion
from .framework.evidence import Evidence, EvidenceType, EvidenceStrength
from .framework.hypothesis import Hypothesis

# Import the existing engine lazily to avoid a circular import.
from . import engine as _legacy_engine  # noqa: E402


def _convert(fwm: FrameworkMeasurement) -> _legacy_engine.Measurement:
    """Convert one framework Measurement to the legacy engine format."""
    state = fwm.state
    if state == MeasurementState.NOT_MEASURED:
        return _legacy_engine.Measurement(name=fwm.name, value=None, measured=False)
    if state == MeasurementState.MEASURED_ZERO:
        return _legacy_engine.Measurement(name=fwm.name, value=0.0, measured=True)
    if state == MeasurementState.IN_RANGE:
        val: Any = fwm.value
        if isinstance(val, bool):
            return _legacy_engine.Measurement(name=fwm.name, value=val, measured=True)
        if val is None:
            # IN_RANGE with value=None is an inconsistent measurement
            # (state claims a reading was taken, but no value supplied).
            # Do NOT silently convert to 0.0 — that would turn missing
            # data into a fake zero reading, violating the framework's
            # core principle. Treat as measured-but-value-unknown so the
            # legacy engine surfaces requires_info=True.
            return _legacy_engine.Measurement(name=fwm.name, value=None, measured=True)
        return _legacy_engine.Measurement(name=fwm.name, value=val, measured=True)
    if state in (MeasurementState.BELOW_RANGE, MeasurementState.ABOVE_RANGE):
        # Measured but no usable numeric value. The legacy engine treats
        # None+measured=True as "measured, value unknown" which propagates
        # requires_info correctly.
        return _legacy_engine.Measurement(name=fwm.name, value=None, measured=True)
    if state in (MeasurementState.INVALID, MeasurementState.NOT_APPLICABLE):
        return _legacy_engine.Measurement(name=fwm.name, value=None, measured=False)
    # UNKNOWN
    return _legacy_engine.Measurement(name=fwm.name, value=None, measured=False)


def diagnose_with_framework_measurements(
    eng: _legacy_engine.DiagnosticEngine,
    rail_family: str,
    measurements: list[FrameworkMeasurement],
    controller_part: Optional[str] = None,
    topology: Optional[str] = None,
) -> EngineConclusion:
    """Run the legacy engine on framework measurements and wrap the
    result as a framework EngineConclusion."""
    legacy_ms = [_convert(m) for m in measurements]
    res = eng.diagnose(
        rail_family=rail_family,
        measurements=legacy_ms,
        controller_part=controller_part,
        topology=topology,
    )

    # Build next_steps as serialized dicts (host renders them).
    next_steps: list[dict] = []
    for s in res.next_steps:
        next_steps.append({
            "step": s.step,
            "action": s.action,
            "measure": s.measure,
            "interpretations": s.interpretations,
            "requires_info": s.requires_info,
            "requires_info_note": s.requires_info_note,
        })

    # Build a hypothesis from the fired rule (if any). The hypothesis
    # statement is the fired rule's first interpretation's 'then' if
    # present, otherwise the action.
    new_hypotheses: list[Hypothesis] = []
    new_evidence: list[Evidence] = []
    if res.fired_rule_id:
        # The fired rule represents the *next* test, not yet a confirmed
        # fault. We propose a hypothesis at POSSIBLE status that captures
        # the current suspicion; evidence gets attached when the test is
        # actually run.
        statement_parts = [f"Rail {rail_family} diagnostic in progress"]
        if res.next_steps:
            statement_parts.append(f"next step: {res.next_steps[0].action}")
        hyp = Hypothesis(
            statement=". ".join(statement_parts)[:300],
            engine="power_rail",
            rule_source=res.fired_rule_id,
        )
        # Background evidence: the measured facts that drove this rule.
        # Cite BOTH True and False facts (a False fact like
        # 'rail_short=False' is meaningful evidence that a short was
        # ruled out). Skip None (unmeasured) facts and the derived
        # '_measured' / '_present' suffixes to avoid redundancy.
        for fact_name, fact_val in res.facts_evaluated.items():
            if fact_val is None:
                continue  # unmeasured — not evidence
            if fact_name.endswith("_measured") or fact_name.endswith("_present"):
                continue  # derived; the underlying fact is cited separately
            val_str = "True" if fact_val is True else "False"
            new_evidence.append(Evidence(
                type=EvidenceType.BACKGROUND,
                strength=EvidenceStrength.WEAK,
                description=f"Fact '{fact_name}' is {val_str} per supplied measurements",
                rule_source=res.fired_rule_id,
                source_kind="measurement",
                hypothesis_id=hyp.hypothesis_id,
            ))
        new_hypotheses.append(hyp)

    return EngineConclusion(
        engine="power_rail",
        rule_id=res.fired_rule_id,
        next_steps=next_steps,
        new_hypotheses=new_hypotheses,
        new_evidence=new_evidence,
        requires_info=res.requires_info,
        requires_info_note=res.requires_info_note,
        notes=res.notes,
    )
