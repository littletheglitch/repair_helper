"""DiagnosticSession — live working state for one Case.

Holds the running list of measurements, hypotheses, evidence, engine
conclusions, and safety violations. Engines append to the session; the
host reads from it. The session is the single merge point across
multiple specialized engines.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from .case import Case, CaseStatus
from .measurement import Measurement
from .evidence import Evidence, EvidenceStrength
from .hypothesis import Hypothesis, HypothesisStatus
from .safety import SafetyViolation


_STRENGTH_ORDER = {
    None: -1,
    EvidenceStrength.WEAK: 0,
    EvidenceStrength.MODERATE: 1,
    EvidenceStrength.STRONG: 2,
    EvidenceStrength.DEFINITIVE: 3,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class EngineConclusion:
    """A structured conclusion returned by a specialized engine.

    Engines produce one of these per invocation; the host merges it
    into the session. Contains the engine name, the rule that fired,
    the next steps, hypotheses to add, evidence to attach, and any
    safety violations to flag.
    """
    engine: str
    rule_id: Optional[str]
    next_steps: list[dict]                       # serialized NextTest
    new_hypotheses: list[Hypothesis] = field(default_factory=list)
    new_evidence: list[Evidence] = field(default_factory=list)
    safety_violations: list[SafetyViolation] = field(default_factory=list)
    requires_info: bool = False
    requires_info_note: str = ""
    notes: list[str] = field(default_factory=list)
    concluded_at_utc: str = field(default_factory=_utc_now_iso)
    conclusion_id: str = field(default_factory=lambda: f"C_{uuid.uuid4().hex[:10]}")


@dataclass
class DiagnosticSession:
    """Live state for one case."""
    case: Case
    measurements: list[Measurement] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    conclusions: list[EngineConclusion] = field(default_factory=list)
    safety_violations: list[SafetyViolation] = field(default_factory=list)
    created_at_utc: str = field(default_factory=_utc_now_iso)
    updated_at_utc: str = field(default_factory=_utc_now_iso)

    # --- measurement management ---------------------------------------

    def add_measurement(self, m: Measurement) -> None:
        self.measurements.append(m)
        self.case.touch()
        self.touch()

    def measurement_by_name(self, name: str) -> Optional[Measurement]:
        """Return the most recent measurement with the given name."""
        for m in reversed(self.measurements):
            if m.name == name:
                return m
        return None

    def measurements_for_rail(self, rail_family: str) -> list[Measurement]:
        """All measurements whose name starts with the rail family prefix.
        Heuristic: rail measurements are typically named
        '<rail_family>.<signal>' or '<rail_family>_<signal>'."""
        out = []
        for m in self.measurements:
            if m.name.startswith(rail_family + ".") or m.name.startswith(rail_family + "_"):
                out.append(m)
        return out

    # --- hypothesis management ----------------------------------------

    def add_hypothesis(self, h: Hypothesis) -> None:
        self.hypotheses.append(h)
        self.touch()

    def attach_evidence(self, hypothesis_id: str, ev: Evidence) -> None:
        for h in self.hypotheses:
            if h.hypothesis_id == hypothesis_id:
                h.add_evidence(ev)
                self.touch()
                return
        raise KeyError(f"Unknown hypothesis_id {hypothesis_id!r}")

    def ranked_hypotheses(self) -> list[Hypothesis]:
        """Hypotheses sorted by rank (descending). Ties broken by
        net_strength (ordinal) then by recency (proposed_at_utc, oldest
        first so the earliest strong hypothesis wins)."""
        return sorted(
            self.hypotheses,
            key=lambda h: (
                -h.rank,
                -_STRENGTH_ORDER[h.net_strength],
                h.proposed_at_utc,
            ),
        )

    def confirmed_hypothesis(self) -> Optional[Hypothesis]:
        for h in self.hypotheses:
            if h.status == HypothesisStatus.CONFIRMED:
                return h
        return None

    # --- conclusion management ----------------------------------------

    def merge_conclusion(self, c: EngineConclusion) -> None:
        """Merge an engine conclusion into the session.

        Adds the new hypotheses and evidence, records the conclusion,
        and surfaces any safety violations. Does NOT mutate existing
        hypotheses; engines that want to update an existing hypothesis
        do so by attaching evidence via attach_evidence().
        """
        self.conclusions.append(c)
        for h in c.new_hypotheses:
            self.hypotheses.append(h)
        for ev in c.new_evidence:
            # Attach to the named hypothesis if any, else record orphan.
            if ev.hypothesis_id:
                try:
                    self.attach_evidence(ev.hypothesis_id, ev)
                except KeyError:
                    # Orphan evidence: keep it on the conclusion only.
                    pass
        for sv in c.safety_violations:
            self.safety_violations.append(sv)
        self.touch()

    # --- status -------------------------------------------------------

    def touch(self) -> None:
        self.updated_at_utc = _utc_now_iso()

    def to_dict(self) -> dict:
        return {
            "case": self.case.to_dict(),
            "measurements": [m.to_dict() for m in self.measurements],
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "conclusions": [
                {**asdict(c),
                 "new_hypotheses": [h.to_dict() for h in c.new_hypotheses],
                 "new_evidence": [e.to_dict() for e in c.new_evidence],
                 "safety_violations": [sv.to_dict() for sv in c.safety_violations]}
                for c in self.conclusions
            ],
            "safety_violations": [sv.to_dict() for sv in self.safety_violations],
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
        }
