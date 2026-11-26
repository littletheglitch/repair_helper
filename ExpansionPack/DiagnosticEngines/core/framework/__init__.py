"""Core Diagnostic Framework — shared data model.

This package is the single source of truth for the diagnostic data
structures used across all specialized engines (motherboard, power
supply, circuit analysis, etc.). Every engine consumes and produces
instances of these types so the host application never has to deal with
engine-specific measurement or evidence formats.

Design rules enforced here and by the validators in Tests/:

  * A measurement is never silently treated as "normal" when it was
    not taken. `MeasurementState.NOT_MEASURED` is distinct from
    `MEASURED_ZERO`.
  * A hypothesis is never reported as confirmed without supporting
    evidence and never reported as rejected without contradicting
    evidence.
  * Every diagnostic conclusion carries its evidence trail and the rule
    source it came from.
  * The framework is pure standard library. No network, no LLM, no
    third-party packages.
"""
from .case import Case, CaseStatus
from .measurement import (
    Measurement,
    MeasurementKind,
    MeasurementState,
    MeasurementCondition,
)
from .evidence import Evidence, EvidenceType, EvidenceStrength
from .hypothesis import Hypothesis, HypothesisStatus
from .session import DiagnosticSession
from .next_test import NextTest, NextTestSelector, TestCost, SkillLevel
from .report import ReportBuilder
from .safety import SafetyPolicy, SafetyViolation, SafetyLevel

__all__ = [
    "Case", "CaseStatus",
    "Measurement", "MeasurementKind", "MeasurementState",
    "MeasurementCondition",
    "Evidence", "EvidenceType", "EvidenceStrength",
    "Hypothesis", "HypothesisStatus",
    "DiagnosticSession",
    "NextTest", "NextTestSelector", "TestCost", "SkillLevel",
    "ReportBuilder",
    "SafetyPolicy", "SafetyViolation", "SafetyLevel",
]
