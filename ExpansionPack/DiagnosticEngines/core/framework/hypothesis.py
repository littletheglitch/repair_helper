"""Hypothesis model for the Core Diagnostic Framework.

A Hypothesis is a candidate explanation for the observed symptoms. The
framework supports multiple hypotheses per case, evidence-based ranking,
explicit contradictions, uncertainty, and rejection of unsupported
hypotheses.

A hypothesis is never reported as CONFIRMED without DEFINITIVE supporting
evidence and no DEFINITIVE contradicting evidence. A hypothesis is never
REJECTED without contradicting evidence. The framework prefers
PROBABLE / POSSIBLE / UNCERTAIN over false certainty.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .evidence import Evidence, EvidenceType, EvidenceStrength, net_strength


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    POSSIBLE = "possible"          # some supporting evidence, not contradicted
    PROBABLE = "probable"          # strong supporting, no contradicting
    CONFIRMED = "confirmed"        # definitive supporting, no contradicting
    CONTRADICTED = "contradicted"  # strong+ contradicting, weak supporting
    REJECTED = "rejected"          # definitive contradicting, no/weak supporting
    UNCERTAIN = "uncertain"        # evidence is ambiguous or missing


_HYPOTHESIS_RANK = {
    # Higher number = higher confidence. Used for sorting.
    HypothesisStatus.REJECTED: -2,
    HypothesisStatus.CONTRADICTED: -1,
    HypothesisStatus.UNCERTAIN: 0,
    HypothesisStatus.PROPOSED: 1,
    HypothesisStatus.POSSIBLE: 2,
    HypothesisStatus.PROBABLE: 3,
    HypothesisStatus.CONFIRMED: 4,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Hypothesis:
    """A candidate explanation for the case's symptoms."""
    hypothesis_id: str = field(default_factory=lambda: f"H_{uuid.uuid4().hex[:10]}")
    statement: str = ""                # human-readable explanation
    engine: str = ""                   # which engine proposed it (e.g. "motherboard.laptop")
    rule_source: str = ""              # rule_id that proposed it
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    evidence: list[Evidence] = field(default_factory=list)
    proposed_at_utc: str = field(default_factory=_utc_now_iso)
    updated_at_utc: str = field(default_factory=_utc_now_iso)
    notes: str = ""

    def __post_init__(self):
        if not self.statement:
            raise ValueError("Hypothesis requires a non-empty statement")
        if isinstance(self.status, str):
            self.status = HypothesisStatus(self.status)
        if not self.rule_source:
            raise ValueError(
                "Hypothesis requires rule_source (the rule/engine that proposed it)"
            )

    # --- evidence management -------------------------------------------

    def add_evidence(self, ev: Evidence) -> None:
        if ev.hypothesis_id and ev.hypothesis_id != self.hypothesis_id:
            raise ValueError("Evidence belongs to a different hypothesis")
        ev.hypothesis_id = self.hypothesis_id
        self.evidence.append(ev)
        self.updated_at_utc = _utc_now_iso()
        self._recompute_status()

    def _recompute_status(self) -> None:
        """Re-derive status from evidence. Never inflates confidence.

        The rules:
          * DEFINITIVE supporting + no DEFINITIVE contradicting -> CONFIRMED
          * DEFINITIVE contradicting + no DEFINITIVE supporting   -> REJECTED
          * STRONG supporting + no contradicting                  -> PROBABLE
          * any supporting + no contradicting                     -> POSSIBLE
          * any supporting + any contradicting (neither definitive) -> UNCERTAIN
          * no evidence at all                                    -> PROPOSED
        """
        if not self.evidence:
            self.status = HypothesisStatus.PROPOSED
            return

        sup = [e for e in self.evidence if e.type == EvidenceType.SUPPORTING]
        con = [e for e in self.evidence if e.type == EvidenceType.CONTRADICTING]
        sup_def = any(e.strength == EvidenceStrength.DEFINITIVE for e in sup)
        con_def = any(e.strength == EvidenceStrength.DEFINITIVE for e in con)

        if sup_def and not con_def:
            self.status = HypothesisStatus.CONFIRMED
        elif con_def and not sup_def:
            self.status = HypothesisStatus.REJECTED
        elif con_def and sup_def:
            self.status = HypothesisStatus.UNCERTAIN  # both definitive -> can't decide
        elif sup and not con:
            best = max(e.strength for e in sup)
            if best == EvidenceStrength.STRONG:
                self.status = HypothesisStatus.PROBABLE
            else:
                self.status = HypothesisStatus.POSSIBLE
        elif sup and con:
            self.status = HypothesisStatus.UNCERTAIN
        elif con and not sup:
            # Only contradicting, none definitive -> contradicted (not rejected)
            self.status = HypothesisStatus.CONTRADICTED
        else:
            # only MISSING / AMBIGUOUS / BACKGROUND evidence
            self.status = HypothesisStatus.UNCERTAIN

    @property
    def net_strength(self) -> Optional[EvidenceStrength]:
        return net_strength(self.evidence)

    @property
    def rank(self) -> int:
        """Sort key. Higher is more credible."""
        return _HYPOTHESIS_RANK[self.status]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["evidence"] = [e.to_dict() for e in self.evidence]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Hypothesis":
        d = dict(d)
        if isinstance(d.get("status"), str):
            d["status"] = HypothesisStatus(d["status"])
        ev_list = d.pop("evidence", [])
        h = cls(**d)
        for ev in ev_list:
            h.add_evidence(Evidence.from_dict(ev))
        return h
