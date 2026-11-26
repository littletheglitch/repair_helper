"""Next-test selection.

The NextTestSelector picks the most informative next measurement based
on: information gain (does it distinguish competing hypotheses?),
safety (the SafetyPolicy can BLOCK), cost, available instruments,
technician skill, reversibility.

Every recommendation carries an explanation: the host application must
be able to tell the technician WHY a test is recommended, not just
WHAT to do.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

from .safety import SafetyPolicy, SafetyViolation, SafetyLevel
from .hypothesis import Hypothesis, HypothesisStatus


class TestCost(str, Enum):
    FREE = "free"               # visual inspection, smell, touch (cold)
    LOW = "low"                 # DMM resistance / diode check, unpowered
    MEDIUM = "medium"           # DMM voltage, powered, low-risk
    HIGH = "high"               # oscilloscope on live rail, current injection
    VERY_HIGH = "very_high"     # invasive: desolder, lift pin, rework


class SkillLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


@dataclass
class NextTest:
    """A recommended next measurement."""
    test_id: str = field(default_factory=lambda: f"T_{uuid.uuid4().hex[:10]}")
    what: str = ""                # what to measure / inspect
    where: str = ""               # location on the board
    instrument: str = ""          # required instrument
    expected: str = ""            # expected result
    safety: str = ""              # safety precaution (free text)
    cost: TestCost = TestCost.LOW
    required_skill: SkillLevel = SkillLevel.INTERMEDIATE
    reversible: bool = True
    instruments_needed: list[str] = field(default_factory=list)
    distinguishes: list[str] = field(default_factory=list)  # hypothesis_ids it separates
    rationale: str = ""           # WHY this test (information gain reasoning)
    rule_source: str = ""         # rule that proposed it
    safety_violations: list[SafetyViolation] = field(default_factory=list)
    blocked: bool = False

    def __post_init__(self):
        if isinstance(self.cost, str):
            self.cost = TestCost(self.cost)
        if isinstance(self.required_skill, str):
            self.required_skill = SkillLevel(self.required_skill)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["cost"] = self.cost.value
        d["required_skill"] = self.required_skill.value
        d["safety_violations"] = [sv.to_dict() for sv in self.safety_violations]
        return d


_COST_RANK = {
    TestCost.FREE: 0,
    TestCost.LOW: 1,
    TestCost.MEDIUM: 2,
    TestCost.HIGH: 3,
    TestCost.VERY_HIGH: 4,
}

_SKILL_RANK = {
    SkillLevel.BEGINNER: 0,
    SkillLevel.INTERMEDIATE: 1,
    SkillLevel.ADVANCED: 2,
    SkillLevel.EXPERT: 3,
}


class NextTestSelector:
    """Selects the best next test from a list of candidates.

    The selector is conservative: any candidate that trips a BLOCK-level
    safety rule is filtered out (and recorded as a blocked candidate).
    Among the survivors, the selector ranks by:

      1. information gain (number of competing hypotheses the test
         distinguishes — more is better)
      2. cost (lower is better)
      3. required skill (lower is better, given the technician's level)
      4. reversibility (reversible is better)

    The selector never auto-performs a test; it returns a ranked list
    and the host decides.
    """

    def __init__(self, safety_policy: Optional[SafetyPolicy] = None):
        self.safety = safety_policy or SafetyPolicy()

    def select(self,
               candidates: list[NextTest],
               technician_skill: SkillLevel = SkillLevel.INTERMEDIATE,
               available_instruments: Optional[list[str]] = None) -> list[NextTest]:
        """Return candidates ranked best-first. Blocked and
        unavailable-instrument candidates are filtered but kept on the
        returned objects (blocked=True) so the host can show why they
        were skipped."""
        available = available_instruments or []
        ranked: list[NextTest] = []
        for cand in candidates:
            # Safety check
            violations = self.safety.evaluate({
                "what": cand.what, "where": cand.where,
                "instrument": cand.instrument, "safety": cand.safety,
            })
            cand.safety_violations = violations
            cand.blocked = any(v.level == SafetyLevel.BLOCK for v in violations)
            # Instrument availability check (only if a list was supplied)
            if available and cand.instruments_needed:
                if not all(i in available for i in cand.instruments_needed):
                    cand.blocked = True
                    cand.safety_violations.append(SafetyViolation(
                        level=SafetyLevel.BLOCK,
                        rule="INSTRUMENT_UNAVAILABLE",
                        message=f"Required instrument(s) not available: "
                                f"{cand.instruments_needed}",
                        context=cand.what[:200],
                    ))
            # Skill check (only downgrade, never block)
            if _SKILL_RANK[cand.required_skill] > _SKILL_RANK[technician_skill]:
                # Don't block; just demote via a higher effective cost.
                pass
            ranked.append(cand)

        # Sort: non-blocked first, then by (info_gain desc, cost asc,
        # skill asc, reversible desc).
        ranked.sort(key=lambda t: (
            t.blocked,                                   # False (0) first
            -len(t.distinguishes),                       # more distinguishing first
            _SKILL_RANK[t.required_skill]
                if _SKILL_RANK[t.required_skill] <= _SKILL_RANK[technician_skill]
                else 99,                                  # demote over-skill tests
            _COST_RANK[t.cost],
            0 if t.reversible else 1,
        ))
        return ranked

    def explain(self, test: NextTest) -> str:
        """Return a human-readable rationale for a recommended test."""
        parts = []
        if test.rationale:
            parts.append(test.rationale)
        if test.distinguishes:
            parts.append(f"Distinguishes hypothesis(ies): {', '.join(test.distinguishes)}")
        parts.append(f"Cost: {test.cost.value}; required skill: {test.required_skill.value}; "
                     f"reversible: {test.reversible}")
        if test.safety_violations:
            for sv in test.safety_violations:
                parts.append(f"[{sv.level.value}] {sv.rule}: {sv.message}")
        return " | ".join(parts)
