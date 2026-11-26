"""Evidence model for the Core Diagnostic Framework.

Every diagnostic conclusion an engine produces MUST cite the evidence
it relied on and the rule source. Evidence is typed so the host can
render supporting/contradicting/missing evidence separately.

`EvidenceStrength` is qualitative, not a fake percentage. The framework
refuses to convert it to a numeric confidence; engines that want a
ranking use the hypothesis module's rank() which orders by evidence
weight without inventing precision.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class EvidenceType(str, Enum):
    SUPPORTING = "supporting"
    CONTRADICTING = "contradicting"
    MISSING = "missing"            # a measurement that would decide but was not taken
    AMBIGUOUS = "ambiguous"
    BACKGROUND = "background"      # reference info, not direct evidence


class EvidenceStrength(str, Enum):
    """Qualitative strength. Order matters for ranking (WEAK < MODERATE
    < STRONG < DEFINITIVE)."""
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    DEFINITIVE = "definitive"

    def __lt__(self, other) -> bool:
        order = {
            EvidenceStrength.WEAK: 0,
            EvidenceStrength.MODERATE: 1,
            EvidenceStrength.STRONG: 2,
            EvidenceStrength.DEFINITIVE: 3,
        }
        if not isinstance(other, EvidenceStrength):
            return NotImplemented
        return order[self] < order[other]


_STRENGTH_ORDER = {
    EvidenceStrength.WEAK: 0,
    EvidenceStrength.MODERATE: 1,
    EvidenceStrength.STRONG: 2,
    EvidenceStrength.DEFINITIVE: 3,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Evidence:
    """One piece of evidence linked to a hypothesis.

    `source` is a free-form string identifying where the evidence came
    from: a measurement_id, a rule_id, a datasheet URL, a knowledge
    doc id. The framework does not resolve it; the host can.

    `rule_source` is the rule (or engine + step) that produced this
    evidence. Required so the host can trace any conclusion back to
    the rule that generated it.
    """
    evidence_id: str = field(default_factory=lambda: f"E_{uuid.uuid4().hex[:10]}")
    hypothesis_id: str = ""        # which hypothesis this evidence relates to
    type: EvidenceType = EvidenceType.SUPPORTING
    strength: EvidenceStrength = EvidenceStrength.MODERATE
    description: str = ""
    measurement_id: Optional[str] = None
    rule_source: str = ""           # e.g. "RULE_BUCK_004_CHECK_ENABLE"
    source: str = ""                # measurement_id / URL / doc id
    source_kind: str = ""           # 'measurement' | 'rule' | 'datasheet' | 'knowledge'
    notes: str = ""
    created_at_utc: str = field(default_factory=_utc_now_iso)

    def __post_init__(self):
        if isinstance(self.type, str):
            self.type = EvidenceType(self.type)
        if isinstance(self.strength, str):
            self.strength = EvidenceStrength(self.strength)
        if not self.description:
            raise ValueError("Evidence requires a non-empty description")
        if not self.rule_source:
            # rule_source is mandatory per the spec ("Source of the rule").
            raise ValueError(
                "Evidence requires rule_source (the rule/engine that produced it)"
            )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        d["strength"] = self.strength.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Evidence":
        d = dict(d)
        if isinstance(d.get("type"), str):
            d["type"] = EvidenceType(d["type"])
        if isinstance(d.get("strength"), str):
            d["strength"] = EvidenceStrength(d["strength"])
        return cls(**d)


def net_strength(evidence_list: list[Evidence]) -> Optional[EvidenceStrength]:
    """Combine a list of evidence for one hypothesis into a net strength.

    Rules:
      * Any DEFINITIVE SUPPORTING with no DEFINITIVE CONTRADICTING -> DEFINITIVE
      * Any DEFINITIVE CONTRADICTING with no DEFINITIVE SUPPORTING -> reject
        (returns None; the hypothesis module handles rejection)
      * Otherwise: highest SUPPORTING strength minus highest CONTRADICTING
        strength, clamped at WEAK.
      * An empty list returns None (no evidence).
    """
    if not evidence_list:
        return None
    sup = [e for e in evidence_list if e.type == EvidenceType.SUPPORTING]
    con = [e for e in evidence_list if e.type == EvidenceType.CONTRADICTING]
    sup_max = max((e.strength for e in sup), default=None)
    con_max = max((e.strength for e in con), default=None)

    if sup_max == EvidenceStrength.DEFINITIVE and con_max != EvidenceStrength.DEFINITIVE:
        return EvidenceStrength.DEFINITIVE
    if con_max == EvidenceStrength.DEFINITIVE and sup_max != EvidenceStrength.DEFINITIVE:
        return None  # rejected
    if sup_max is None:
        return None
    if con_max is None:
        return sup_max
    # Both present, neither definitive: net = sup - con (ordinal)
    delta = _STRENGTH_ORDER[sup_max] - _STRENGTH_ORDER[con_max]
    if delta <= 0:
        return EvidenceStrength.WEAK
    for s, o in _STRENGTH_ORDER.items():
        if o == delta:
            return s
    return EvidenceStrength.WEAK
