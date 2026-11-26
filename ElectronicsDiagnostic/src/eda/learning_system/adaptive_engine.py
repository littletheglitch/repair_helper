"""Adaptive Learning Engine.

Picks the next useful thing for the user to do, based on their
evidence-based mastery (from skill_assessment) and the canonical
difficulty order. Rules:

  * Never infer mastery from reading — the adaptive engine only
    RECOMMENDS exercises; mastery changes only when the user solves
    them independently (handled by skill_assessment + reasoning_evaluator).
  * Prefer the lowest-mastery skill that the user has not yet worked
    on (a gap), then the skill with the most recent failure.
  * Difficulty scales with demonstrated mastery on that skill:
    novice/apprentice → difficulty 1-2; practitioner → 2-3; expert → 3-4.
  * If the user has an unsolved exercise on a skill, recommend
    re-attempting it (with a hint) before generating a new one.
  * Never recommend an exercise whose solution the user has already
    solved independently (to avoid memorization).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import db, skill_assessment as SA
from .exercise_generator import ExerciseGenerator


@dataclass
class Recommendation:
    skill_key: str
    reason: str
    difficulty: int
    exercise_id: Optional[int] = None
    exercise_prompt: str = ""
    exercise_source_kind: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


_LEVEL_DIFFICULTY = {
    "novice": (1, 2),
    "apprentice": (2, 3),
    "practitioner": (2, 3),
    "expert": (3, 4),
}


class AdaptiveEngine:
    """Picks the next skill to work on and the next exercise within it."""

    def __init__(self, profile_id: Optional[int] = None,
                 profile_name: str = "default"):
        if profile_id is None:
            prof = db.get_or_create_profile(profile_name)
            profile_id = int(prof["id"])
        self.profile_id = int(profile_id)
        self.assessment = SA.SkillAssessment(profile_id)
        self.generator = ExerciseGenerator(profile_id)

    # -- Public ---------------------------------------------------------
    def recommend_next(self, focus_skill: Optional[str] = None,
                       seed: Optional[int] = None) -> Recommendation:
        """Recommend the next exercise. If ``focus_skill`` is given,
        work on that skill regardless of the gap analysis."""
        db.ensure_schema()
        if focus_skill:
            return self._recommend_in_skill(focus_skill, seed)
        # 1) Find the gap: lowest-mastery skill, in difficulty order,
        #    that the user has not yet worked on OR has worked on but
        #    not reached apprentice.
        skills = db.list_skill_categories()
        mastery_by = {m.skill_key: m for m in self.assessment.all_mastery()}
        # Sort by (level rank, difficulty_order) so we surface the
        # earliest gap first.
        level_rank = {"novice": 0, "apprentice": 1,
                      "practitioner": 2, "expert": 3}
        # Prefer skills the user has attempted but not yet passed, then
        # totally-unattempted skills, then skills due for review.
        attempted = []
        unattempted = []
        for sk in skills:
            m = mastery_by.get(sk["key"])
            if m and m.evidence_rows > 0 and m.level in ("novice", "apprentice"):
                attempted.append((sk, m))
            elif not m or m.evidence_rows == 0:
                unattempted.append((sk, m))
        if attempted:
            attempted.sort(key=lambda x: (level_rank.get(x[1].level, 0),
                                          x[0]["difficulty_order"]))
            return self._recommend_in_skill(attempted[0][0]["key"], seed)
        if unattempted:
            unattempted.sort(key=lambda x: x[0]["difficulty_order"])
            return self._recommend_in_skill(unattempted[0][0]["key"], seed)
        # Nothing unattempted and nothing struggling: recommend a review
        # on the lowest-mastery skill overall.
        all_skills = [(sk, mastery_by.get(sk["key"]))
                      for sk in skills]
        all_skills.sort(key=lambda x: (level_rank.get(
            x[1].level if x[1] else "novice", 0), x[0]["difficulty_order"]))
        return self._recommend_in_skill(all_skills[0][0]["key"], seed)

    def gaps(self) -> List[Dict[str, Any]]:
        """Return skills the user has not yet reached apprentice on."""
        out = []
        for m in self.assessment.all_mastery():
            if m.level in ("novice", "apprentice"):
                out.append({
                    "skill_key": m.skill_key,
                    "level": m.level,
                    "independent_solves": m.independent_solves,
                    "distinct_exercises": m.distinct_exercises_solved,
                    "rationale": m.rationale,
                })
        return out

    # -- internals ------------------------------------------------------
    def _recommend_in_skill(self, skill_key: str,
                            seed: Optional[int]) -> Recommendation:
        m = self.assessment.mastery(skill_key)
        # First: is there an unsolved exercise on this skill? If so,
        # recommend re-attempting it (with a hint) rather than a new one.
        responses = db.list_responses(self.profile_id)
        solved_ids = {r["exercise_id"] for r in responses
                      if r.get("solved_independently")}
        exs = db.list_exercises(skill_key, limit=50)
        unsolved = [e for e in exs if e["id"] not in solved_ids]
        if unsolved:
            ex = unsolved[0]
            return Recommendation(
                skill_key=skill_key,
                reason=(f"You have an unsolved exercise on {skill_key}; "
                        f"re-attempt it (use 'learning hint' if stuck). "
                        f"Current mastery: {m.level} "
                        f"({m.independent_solves} independent solve(s))."),
                difficulty=int(ex.get("difficulty", 2)),
                exercise_id=int(ex["id"]),
                exercise_prompt=ex.get("prompt", ""),
                exercise_source_kind=ex.get("source_kind", ""),
            )
        # No unsolved exercises: generate a new one at the right difficulty.
        lo, hi = _LEVEL_DIFFICULTY.get(m.level, (2, 3))
        # Generate a few and pick one in the difficulty band.
        gen = self.generator.generate(skill_key, n=3, seed=seed)
        in_band = [g for g in gen if lo <= g.difficulty <= hi]
        chosen = in_band[0] if in_band else (gen[0] if gen else None)
        if not chosen:
            return Recommendation(
                skill_key=skill_key,
                reason=(f"No exercises could be generated for {skill_key} "
                        f"from the available sources. Current mastery: "
                        f"{m.level}."),
                difficulty=lo)
        return Recommendation(
            skill_key=skill_key,
            reason=(f"Generated a new {chosen.source_kind} exercise for "
                    f"{skill_key} at difficulty {chosen.difficulty} "
                    f"(your mastery: {m.level})."),
            difficulty=chosen.difficulty,
            exercise_id=chosen.id,
            exercise_prompt=chosen.prompt,
            exercise_source_kind=chosen.source_kind,
        )
