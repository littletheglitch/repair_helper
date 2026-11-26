"""Skill Assessment Engine — evidence-based mastery levels.

Mastery is computed from the EVIDENCE rows in ``mastery_evidence``,
never from a single score or from "viewed an explanation". This is the
core anti-memorization guarantee: a lucky guess on one exercise cannot
become "mastery", and reading an explanation does NOT raise the level.

Evidence kinds (in increasing weight):
  viewed                      — the user read an explanation. Weight 0.
  attempted                   — the user tried an exercise but did not
                                solve it. Weight 0 (it is a signal of
                                engagement, not of ability).
  solved_with_hints           — the user solved it but used hints.
                                Small weight.
  solved_independently        — the user solved it with no hints and
                                correct reasoning. Meaningful weight.
  demonstrated_in_new_context — the user solved a DIFFERENT exercise
                                on the same skill, independently. This
                                is the strongest single piece of
                                evidence and is required for "expert".

Mastery levels (LEVELS in db.py): novice → apprentice → practitioner →
expert. The thresholds are evidence counts, not percentages:

  novice        : default (no evidence yet).
  apprentice    : >= 2 independent solves OR 1 independent + 2 with-hints.
  practitioner  : >= 3 independent solves across >= 2 distinct exercises.
  expert        : >= 3 independent solves across >= 2 distinct exercises
                  AND >= 1 demonstration in a new context.

These thresholds are deliberately conservative and are NOT inferred
from reading. "Viewing" an explanation records evidence but contributes
zero weight; the adaptive engine uses it only to know what the user
has SEEN, not what they can DO.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import db


@dataclass
class MasteryLevel:
    skill_key: str
    level: str               # one of db.LEVELS
    independent_solves: int
    distinct_exercises_solved: int
    solves_with_hints: int
    new_context_demos: int
    views: int
    evidence_rows: int
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


# Evidence weights (only used to summarize, never to inflate mastery).
WEIGHT = {
    "viewed": 0,
    "attempted": 0,
    "solved_with_hints": 1,
    "solved_independently": 3,
    "demonstrated_in_new_context": 5,
}

# Level thresholds (see module docstring).
# Expert requires practitioner-level evidence PLUS >=2 new-context
# demonstrations, so a single lucky run cannot reach expert.
_THRESHOLDS = {
    "apprentice":   {"independent": 2, "alt_or_hints": True},
    "practitioner": {"independent": 3, "distinct": 2},
    "expert":       {"independent": 5, "distinct": 4, "new_context": 2},
}


class SkillAssessment:
    """Compute and record evidence-based mastery levels."""

    def __init__(self, profile_id: Optional[int] = None,
                 profile_name: str = "default"):
        if profile_id is None:
            prof = db.get_or_create_profile(profile_name)
            profile_id = int(prof["id"])
        self.profile_id = int(profile_id)

    # -- Recording evidence ---------------------------------------------
    def note_viewed(self, skill_key: str, exercise_id: Optional[int] = None) -> int:
        """Record that the user viewed an explanation. Zero weight."""
        return db.record_evidence(self.profile_id, skill_key, "viewed",
                                  exercise_id=exercise_id)

    def note_attempted(self, skill_key: str, exercise_id: Optional[int] = None) -> int:
        """Record an attempt (not solved). Zero weight."""
        return db.record_evidence(self.profile_id, skill_key, "attempted",
                                  exercise_id=exercise_id)

    def note_solved_with_hints(self, skill_key: str,
                               exercise_id: Optional[int] = None) -> int:
        return db.record_evidence(self.profile_id, skill_key,
                                  "solved_with_hints", exercise_id=exercise_id)

    def note_solved_independently(self, skill_key: str,
                                  exercise_id: Optional[int] = None) -> int:
        return db.record_evidence(self.profile_id, skill_key,
                                  "solved_independently",
                                  exercise_id=exercise_id)

    def note_demonstrated_in_new_context(self, skill_key: str,
                                         exercise_id: Optional[int] = None,
                                         context_tag: str = "") -> int:
        return db.record_evidence(self.profile_id, skill_key,
                                  "demonstrated_in_new_context",
                                  exercise_id=exercise_id,
                                  context_tag=context_tag)

    # -- Computing mastery ----------------------------------------------
    def mastery(self, skill_key: str) -> MasteryLevel:
        """Compute the current mastery level for one skill from evidence."""
        evidence = db.list_evidence(self.profile_id, skill_key)
        return self._mastery_from_evidence(skill_key, evidence)

    def all_mastery(self) -> List[MasteryLevel]:
        """Compute mastery for every seeded skill category."""
        out: List[MasteryLevel] = []
        for cat in db.list_skill_categories():
            out.append(self.mastery(cat["key"]))
        return out

    def _mastery_from_evidence(self, skill_key: str,
                               evidence: List[Dict[str, Any]]) -> MasteryLevel:
        indep = 0
        hints = 0
        new_ctx = 0
        views = 0
        distinct_solved: set = set()
        for e in evidence:
            kind = e.get("kind", "")
            if kind == "viewed":
                views += 1
            elif kind == "attempted":
                pass
            elif kind == "solved_with_hints":
                hints += 1
                if e.get("exercise_id"):
                    distinct_solved.add(e["exercise_id"])
            elif kind == "solved_independently":
                indep += 1
                if e.get("exercise_id"):
                    distinct_solved.add(e["exercise_id"])
            elif kind == "demonstrated_in_new_context":
                new_ctx += 1
                if e.get("exercise_id"):
                    distinct_solved.add(e["exercise_id"])
        # Decide level top-down: if expert criteria met, expert; else if
        # practitioner met, practitioner; etc.
        level = "novice"
        rationale = "no evidence of independent ability yet"
        t_exp = _THRESHOLDS["expert"]
        t_prac = _THRESHOLDS["practitioner"]
        t_app = _THRESHOLDS["apprentice"]
        if (indep >= t_exp["independent"]
                and len(distinct_solved) >= t_exp["distinct"]
                and new_ctx >= t_exp["new_context"]):
            level = "expert"
            rationale = (f"{indep} independent solves across "
                         f"{len(distinct_solved)} distinct exercises + "
                         f"{new_ctx} demonstration(s) in a new context")
        elif (indep >= t_prac["independent"]
                and len(distinct_solved) >= t_prac["distinct"]):
            level = "practitioner"
            rationale = (f"{indep} independent solves across "
                         f"{len(distinct_solved)} distinct exercises")
        elif (indep >= t_app["independent"]
                or (indep >= 1 and hints >= 2)):
            level = "apprentice"
            rationale = (f"{indep} independent solve(s) + {hints} with-hints; "
                         "not yet enough distinct exercises for practitioner")
        elif views > 0:
            rationale = (f"only {views} explanation(s) viewed — viewing does "
                         "NOT raise mastery; solve exercises independently")
        return MasteryLevel(
            skill_key=skill_key, level=level,
            independent_solves=indep,
            distinct_exercises_solved=len(distinct_solved),
            solves_with_hints=hints, new_context_demos=new_ctx,
            views=views, evidence_rows=len(evidence),
            rationale=rationale,
        )

    # -- Persisting a snapshot ------------------------------------------
    def snapshot(self, skill_key: str, note: str = "") -> int:
        """Record the current computed level as an assessment row
        (append-only). Used for history/progress views; mastery is
        re-computed from evidence each time, so this snapshot never
        *becomes* the mastery."""
        m = self.mastery(skill_key)
        return db.record_assessment(self.profile_id, skill_key, m.level,
                                    evidence_type="snapshot", note=note)

    def history(self, skill_key: Optional[str] = None) -> List[Dict[str, Any]]:
        return db.list_assessments(self.profile_id, skill_key)
