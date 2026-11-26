"""Repair-to-Learning converter.

Converts a COMPLETED repair session into one or more learning cases.
The conversion is one-way: the repair session is read-only here; we
extract its context and synthesize learning material from it, but we
never modify the session or its measurements.

Rules from the spec:

* Extract: device/board context, symptoms, confirmed measurements,
  initial hypotheses, tests performed, confirmed root cause (if
  known), repair outcome, relevant engineering principles, knowledge
  gaps exposed.
* Do NOT mark an uncertain repair outcome as confirmed. If the case
  was recorded with technician_confirmed=0, or the outcome is not
  "fixed", the learning case is generated with the uncertainty made
  explicit and the reference solution is marked "unconfirmed".
* Never automatically disclose the original repair's solution in a
  learning exercise if the user is expected to solve it independently.
  The exercise prompt gives symptoms + perturbed measurements and
  asks for hypotheses + a decisive test; the original fault is stored
  as the hidden reference and is revealed only on an explicit
  'learning explain' request.
* Create follow-up exercises that are SIMILAR BUT NOT IDENTICAL:
  component labels and numeric values are perturbed, so the user must
  reason rather than recall.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .. import sessions, measurements as M, reasoning, learning as repair_learning
from . import db
from .exercise_generator import ExerciseGenerator


@dataclass
class LearningCase:
    session_id: int
    board_model: str
    symptoms: List[str]
    measurements: List[Dict[str, Any]]
    hypotheses: List[Dict[str, Any]]
    tests_performed: List[str]
    confirmed_root_cause: Optional[str]
    outcome: str
    confirmed: bool               # technician_confirmed flag
    relevant_principles: List[str]
    knowledge_gaps: List[str]
    exercises_generated: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id, "board_model": self.board_model,
            "symptoms": self.symptoms, "measurements": self.measurements,
            "hypotheses": self.hypotheses, "tests_performed": self.tests_performed,
            "confirmed_root_cause": self.confirmed_root_cause,
            "outcome": self.outcome, "confirmed": self.confirmed,
            "relevant_principles": self.relevant_principles,
            "knowledge_gaps": self.knowledge_gaps,
            "exercises_generated": self.exercises_generated,
        }


class RepairToLearning:
    """Reads a completed session and synthesizes learning material."""

    def __init__(self, profile_id: Optional[int] = None,
                 profile_name: str = "default"):
        if profile_id is None:
            prof = db.get_or_create_profile(profile_name)
            profile_id = int(prof["id"])
        self.profile_id = int(profile_id)
        self.generator = ExerciseGenerator(profile_id)

    # -- Public ---------------------------------------------------------
    def convert(self, session_id: int, seed: Optional[int] = None) -> Dict[str, Any]:
        """Extract the case context and generate follow-up exercises.

        Returns a dict with the case + the generated exercise ids. Does
        NOT modify the source session. Does NOT mark an uncertain
        outcome as confirmed. Does NOT auto-disclose the solution.
        """
        s = sessions.get(session_id)
        if not s:
            return {"ok": False, "error": f"no session {session_id}"}
        syms = sessions.symptoms(session_id)
        ms = M.list_for(session_id)
        # Run the rule engine to get the hypotheses + next tests
        # (read-only; this persists an analysis row, which is expected
        # and harmless — it's the same thing 'analyze' does).
        analysis = reasoning.analyze(session_id, syms, ms)
        hypotheses = [
            {"fault": h.fault, "score": h.score,
             "supporting": h.supporting, "contradicting": h.contradicting,
             "missing": h.missing, "rationale": h.rationale}
            for h in analysis.hypotheses
        ]
        tests_performed = [m.name for m in ms]
        # Look up the recorded repair case (if any) for this session.
        cases = repair_learning.list_cases(technician_confirmed_only=False)
        recorded = next((c for c in cases
                         if c.get("session_id") == session_id), None)
        confirmed_root_cause: Optional[str] = None
        outcome = "unknown"
        confirmed = False
        if recorded:
            confirmed_root_cause = recorded.get("actual_fault") if \
                recorded.get("technician_confirmed") else None
            outcome = recorded.get("outcome", "unknown")
            confirmed = bool(recorded.get("technician_confirmed"))
        # If the outcome is not "fixed" or the case was unconfirmed, we
        # explicitly do NOT treat the fault as confirmed.
        if outcome != "fixed" or not confirmed:
            confirmed_root_cause = None
        # Identify relevant engineering principles from the symptoms +
        # measurements (heuristic mapping).
        principles = self._infer_principles(syms, ms)
        # Identify knowledge gaps exposed by the case (hypotheses with
        # missing evidence are the gaps).
        gaps = self._infer_gaps(analysis)
        case = LearningCase(
            session_id=session_id, board_model=s.board_model or "",
            symptoms=syms,
            measurements=[{"name": m.name, "value": m.value, "unit": m.unit,
                           "point": m.point} for m in ms],
            hypotheses=hypotheses, tests_performed=tests_performed,
            confirmed_root_cause=confirmed_root_cause, outcome=outcome,
            confirmed=confirmed, relevant_principles=principles,
            knowledge_gaps=gaps,
        )
        # Generate follow-up exercises (similar-but-not-identical).
        ex_ids = self._generate_follow_ups(case, seed)
        case.exercises_generated = ex_ids
        # Persist a learning-session row.
        self._record_learning_session(case)
        return {"ok": True, "case": case.to_dict()}

    # -- internals ------------------------------------------------------
    def _infer_principles(self, syms, ms) -> List[str]:
        text = " ".join(syms).lower() + " " + " ".join(m.name.lower() for m in ms)
        principles = []
        if any(k in text for k in ("valw", "rail", "3.3", "5v", "standby")):
            principles.append("power-sequence / always-on rail behavior")
        if any(k in text for k in ("short", "resistance", "ohm", "ground")):
            principles.append("resistance-to-ground / short detection")
        if any(k in text for k in ("mosfet", "gate", "vin", "input")):
            principles.append("input MOSFET gate drive / power path")
        if any(k in text for k in ("charge", "battery", "isolated")):
            principles.append("battery charging / isolation")
        if any(k in text for k in ("sequence", "enable", "pg")):
            principles.append("power sequencing / enable ordering")
        if not principles:
            principles.append("general fault isolation + decisive testing")
        return principles

    def _infer_gaps(self, analysis) -> List[str]:
        gaps = []
        for h in analysis.hypotheses:
            for m in h.missing:
                gaps.append(f"to test '{h.fault[:50]}': {m}")
        if analysis.missing_evidence:
            gaps.extend(list(analysis.missing_evidence)[:5])
        return gaps

    def _generate_follow_ups(self, case: LearningCase,
                             seed: Optional[int]) -> List[int]:
        ex_ids: List[int] = []
        # 1) A fault-isolation exercise from the case (similar-but-not-
        #    identical): perturbed measurements, hidden solution.
        rng_seed = seed if seed is not None else case.session_id
        rng = _rng_for(f"r2l_{case.session_id}_{rng_seed}")
        meas_lines = []
        for m in case.measurements[:4]:
            v = m.get("value")
            if isinstance(v, (int, float)) and v:
                v = round(v * rng.uniform(0.93, 1.07), 2)
            meas_lines.append(f"- {m.get('name','?')} = {v}{m.get('unit','')}")
        # Pick the target skill: if the confirmed root cause is known and
        # the case is confirmed, use fault_isolation; else use a
        # measurement-interpretation exercise on the gaps.
        target_skill = "fault_isolation" if case.confirmed else \
                       "measurement_interp"
        prompt = ("A board presents with these symptoms:\n  - "
                  + "\n  - ".join(case.symptoms[:3])
                  + "\nMeasurements (a similar board; values may differ "
                    "slightly from any case you have seen):\n  "
                  + "\n  ".join(meas_lines)
                  + "\n\nFormulate TWO ranked hypotheses and the ONE "
                    "decisive measurement you would take next. Justify "
                    "your ranking from the evidence; do not guess the "
                    "fault.")
        if not case.confirmed:
            prompt += ("\n\nNote: this case's original outcome was not "
                       "confirmed ('" + case.outcome + "'); treat the "
                       "root cause as unknown and focus on the reasoning.")
        reference = (case.confirmed_root_cause or
                     "(unconfirmed — reasoning is graded, not the fault)")
        eid = db.upsert_exercise(
            exercise_key=f"r2l_case_{case.session_id}_{rng_seed}",
            skill_key=target_skill, source_kind="confirmed_case"
            if case.confirmed else "synthetic",
            source_ref=f"session#{case.session_id}",
            prompt=prompt, reference_solution=reference,
            unit="", formulas="", assumptions=(
                "reference is the original confirmed fault, kept hidden; "
                "graded on hypothesis ranking + decisive-test reasoning"
                if case.confirmed else
                "unconfirmed case; graded on reasoning only"),
            difficulty=3, solution_hidden=True)
        ex_ids.append(eid)
        # 2) A principle exercise on the first relevant principle.
        if case.relevant_principles:
            p = case.relevant_principles[0]
            skill_map = {
                "power-sequence": "power_sequencing",
                "resistance": "measurement_interp",
                "mosfet": "mosfet",
                "battery": "battery_charging",
                "sequencing": "power_sequencing",
                "fault isolation": "fault_isolation",
            }
            sk = "measurement_interp"
            for k, v in skill_map.items():
                if k in p:
                    sk = v
                    break
            eid2 = db.upsert_exercise(
                exercise_key=f"r2l_principle_{case.session_id}_{rng_seed}",
                skill_key=sk, source_kind="synthetic",
                source_ref=f"session#{case.session_id}",
                prompt=(f"This case touched: {p}. Explain the underlying "
                        f"principle in your own words and describe ONE "
                        f"diagnostic situation where misapplying it would "
                        f"send you down the wrong path."),
                reference_solution=p,
                unit="", formulas="",
                assumptions="open-ended; graded on reasoning",
                difficulty=2, solution_hidden=True)
            ex_ids.append(eid2)
        return ex_ids

    def _record_learning_session(self, case: LearningCase) -> None:
        now = db.database.now_iso()
        db.database.execute(
            "INSERT INTO learning_sessions(profile_id, mode, focus_skill, "
            "started_at) VALUES (?,?,?,?)",
            (self.profile_id, "learning", "fault_isolation", now))


def _rng_for(seed_key: str) -> random.Random:
    h = hashlib.sha256(seed_key.encode("utf-8")).digest()
    return random.Random(int.from_bytes(h[:8], "big"))
