"""Learning Session — mode switch + explanation levels + the glue
between the other learning modules.

Two modes (the spec's "TWO DISTINCT MODES"):

  * repair   — the existing diagnostic orchestration (untouched). This
               module does NOT change repair behavior; it only tracks
               which mode the CLI is in so 'learning' commands route to
               this subsystem and repair commands route to the existing
               flow.
  * learning — this subsystem. Teaches concepts, asks Socratic
               questions, evaluates reasoning, generates exercises,
               tracks mastery, schedules review.

Three explanation levels (the spec's "LEARNING PREFERENCES"):

  * hint     — a small clue, no solution.
  * guided   — ask a question and explain the reasoning step by step.
  * full     — the complete technical explanation.

The DEFAULT in learning mode is hint/guided, NOT full disclosure. The
user can always request the full solution explicitly ('learning
explain').

Provenance: every full explanation cites the source (engine, KB fact
with tier, or confirmed repair case). The module never invents a
numerical reference value — it pulls it from the exercise row, which
the exercise generator computed via an engine or sourced from the KB.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import db, skill_assessment as SA
from .exercise_generator import ExerciseGenerator
from .reasoning_evaluator import ReasoningEvaluator
from .socratic_engine import SocraticEngine
from .adaptive_engine import AdaptiveEngine
from .spaced_review import SpacedReview


# Explanation levels, lowest to highest disclosure.
class ExplanationLevel:
    HINT = "hint"
    GUIDED = "guided"
    FULL = "full"
    ALL = ("hint", "guided", "full")


@dataclass
class LearningSession:
    """Holds the per-CLI-session learning state. The mode is module-level
    (the CLI is a single-user REPL), but the profile and the in-memory
    Socratic paths live on this object."""
    profile_id: int
    profile_name: str
    level: str = "guided"           # default level per spec
    mode: str = "learning"          # 'learning' or 'repair'
    socratic: SocraticEngine = None  # type: ignore[assignment]
    assessment: SA.SkillAssessment = None  # type: ignore[assignment]
    evaluator: ReasoningEvaluator = None  # type: ignore[assignment]
    generator: ExerciseGenerator = None  # type: ignore[assignment]
    adaptive: AdaptiveEngine = None  # type: ignore[assignment]
    review: SpacedReview = None  # type: ignore[assignment]

    @classmethod
    def create(cls, profile_name: str = "default",
               level: str = "guided") -> "LearningSession":
        prof = db.get_or_create_profile(profile_name,
                                         default_level=level)
        pid = int(prof["id"])
        sess = cls(profile_id=pid, profile_name=profile_name,
                   level=level, mode="learning")
        sess.socratic = SocraticEngine(profile_id=pid)
        sess.assessment = SA.SkillAssessment(profile_id=pid)
        sess.evaluator = ReasoningEvaluator(profile_id=pid)
        sess.generator = ExerciseGenerator(profile_id=pid)
        sess.adaptive = AdaptiveEngine(profile_id=pid)
        sess.review = SpacedReview(profile_id=pid)
        return sess

    def set_level(self, level: str) -> None:
        if level not in ExplanationLevel.ALL:
            raise ValueError(f"level must be one of {ExplanationLevel.ALL}")
        self.level = level
        # Persist the default level on the profile.
        db.database.execute(
            "UPDATE learning_profiles SET default_level=?, updated_at=? "
            "WHERE id=?", (level, db.database.now_iso(), self.profile_id))

    def set_mode(self, mode: str) -> None:
        if mode not in ("repair", "learning"):
            raise ValueError("mode must be 'repair' or 'learning'")
        self.mode = mode

    # -- Hint / Guided / Full for a given exercise ----------------------
    def explain(self, exercise: Dict[str, Any],
                level: Optional[str] = None) -> str:
        """Return the explanation of an exercise at the given level
        (or the session's current level). Never discloses the full
        numeric answer at hint level; at guided level it walks the
        reasoning without the final number; at full level it gives the
        complete solution with provenance."""
        lvl = level or self.level
        prompt = exercise.get("prompt", "")
        formulas = exercise.get("formulas", "")
        unit = exercise.get("unit", "")
        ref = exercise.get("reference_solution", "")
        source_kind = exercise.get("source_kind", "")
        source_ref = exercise.get("source_ref", "")
        assumptions = exercise.get("assumptions", "")
        if lvl == ExplanationLevel.HINT:
            # A small clue: name the relevant formula/category, but
            # NOT the answer.
            clue = formulas.split(";")[0] if formulas else ""
            return (f"Hint: this is a {exercise.get('skill_key','')} "
                    f"exercise. Relevant formula: {clue or '(see the skill)'}. "
                    f"State the principle and your substitution; the answer "
                    f"is not shown at hint level. "
                    f"Use 'learning explain' for the guided or full solution.")
        if lvl == ExplanationLevel.GUIDED:
            steps = ["Guided walkthrough (step by step):"]
            if formulas:
                steps.append(f"1. The formula(s): {formulas}")
            if assumptions:
                steps.append(f"2. Assumptions: {assumptions}")
            steps.append("3. Substitute the given values, keeping units.")
            steps.append("4. Compute and carry the units into the result.")
            steps.append("5. Sanity-check the magnitude.")
            steps.append("The final number is intentionally not shown at "
                         "guided level — work it out, then verify with "
                         "'learning explain full' or submit your answer.")
            return "\n".join(steps)
        # FULL
        lines = ["Full solution:"]
        if formulas:
            lines.append(f"Formula(s): {formulas}")
        if assumptions:
            lines.append(f"Assumptions: {assumptions}")
        if ref:
            lines.append(f"Reference solution: {ref} {unit}".strip())
        if source_kind or source_ref:
            lines.append(f"Provenance: {source_kind}"
                         + (f" ({source_ref})" if source_ref else ""))
        # For KB-sourced exercises, cite the tier.
        if source_kind == "kb":
            lines.append("Note: the reference is a knowledge-base fact; "
                        "check its tier — community-tier facts are not "
                        "guaranteed limits.")
        return "\n".join(lines)

    # -- Attempt flow --------------------------------------------------
    def attempt(self, exercise: Dict[str, Any], user_text: str,
                hints_used: int = 0) -> Dict[str, Any]:
        """Evaluate a user's answer to an exercise and record the
        right kind of mastery evidence."""
        return self.evaluator.evaluate_and_record(
            exercise, user_text, hints_used=hints_used,
            level_used=self.level)

    # -- Profile / progress --------------------------------------------
    def skills(self) -> List[Dict[str, Any]]:
        out = []
        for m in self.assessment.all_mastery():
            cat = db.get_skill_category(m.skill_key) or {}
            out.append({
                "key": m.skill_key, "label": cat.get("label", m.skill_key),
                "level": m.level, "independent": m.independent_solves,
                "distinct": m.distinct_exercises_solved,
                "with_hints": m.solves_with_hints,
                "new_context": m.new_context_demos, "views": m.views,
                "rationale": m.rationale,
            })
        return out

    def progress(self) -> Dict[str, Any]:
        ms = self.assessment.all_mastery()
        return {
            "skills_tracked": len(ms),
            "novice": sum(1 for m in ms if m.level == "novice"),
            "apprentice": sum(1 for m in ms if m.level == "apprentice"),
            "practitioner": sum(1 for m in ms if m.level == "practitioner"),
            "expert": sum(1 for m in ms if m.level == "expert"),
            "total_independent_solves": sum(m.independent_solves for m in ms),
            "total_views": sum(m.views for m in ms),
        }

    def history(self, skill_key: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.assessment.history(skill_key)
