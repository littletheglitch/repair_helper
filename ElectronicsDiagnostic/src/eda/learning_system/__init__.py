"""Phase 4 — Engineering Learning & Skill Development subsystem.

A separate learning subsystem integrated with (but NOT replacing) the
existing diagnostic architecture. The goal is to make the technician
progressively capable of independently understanding, analyzing, and
troubleshooting electronic circuits — not dependent on the AI.

Two modes:
  * Repair Mode  — the existing diagnostic orchestration (untouched).
  * Learning Mode — this subsystem: teaches concepts, asks Socratic
    questions, evaluates reasoning, generates exercises, tracks
    evidence-based mastery, and schedules spaced review.

This package is deliberately separate from the existing
``eda.learning`` module (which records technician-confirmed repair
cases into the knowledge base). There is NO name clash: this package
is ``eda.learning_system``; the existing one is ``eda.learning``.

Design rules (from the spec):
  * Mastery is NEVER inferred from reading or viewing an explanation.
    Only independent solutions and demonstrations in a new context
    raise mastery.
  * Deterministic engines compute every reference value; the LLM may
    not invent numerical reference solutions.
  * Exercises always have a known reference solution OR a documented
    valid range.
  * Repair-to-learning conversion never discloses the original repair's
    solution when the user is expected to solve it independently, and
    never marks an uncertain repair outcome as confirmed.
  * Everything is offline; no cloud; modules are lightweight and
    lazy-loaded.

Public API (re-exported here for convenience):
  * SkillAssessment  — evidence-based mastery levels.
  * ReasoningEvaluator — grades user reasoning, gives feedback.
  * SocraticEngine — one focused question at a time.
  * ExerciseGenerator — exercises from KB / engines / cases / synthetic.
  * AdaptiveEngine — picks the next concept/exercise.
  * SpacedReview — performance-based review scheduler.
  * RepairToLearning — converts a completed session into a learning case.
  * LearningSession — a learning session lifecycle + mode switch.
"""
from __future__ import annotations

from .db import (  # noqa: F401
    ensure_schema, get_or_create_profile, seed_skill_categories,
    SKILL_CATEGORIES, LEVELS,
)
from .skill_assessment import SkillAssessment, MasteryLevel  # noqa: F401
from .reasoning_evaluator import ReasoningEvaluator, Evaluation  # noqa: F401
from .socratic_engine import SocraticEngine, SocraticTurn  # noqa: F401
from .exercise_generator import ExerciseGenerator, Exercise  # noqa: F401
from .adaptive_engine import AdaptiveEngine  # noqa: F401
from .spaced_review import SpacedReview, ReviewItem  # noqa: F401
from .repair_to_learning import RepairToLearning  # noqa: F401
from .learning_session import LearningSession, ExplanationLevel  # noqa: F401

__all__ = [
    "ensure_schema", "get_or_create_profile", "seed_skill_categories",
    "SKILL_CATEGORIES", "LEVELS",
    "SkillAssessment", "MasteryLevel",
    "ReasoningEvaluator", "Evaluation",
    "SocraticEngine", "SocraticTurn",
    "ExerciseGenerator", "Exercise",
    "AdaptiveEngine",
    "SpacedReview", "ReviewItem",
    "RepairToLearning",
    "LearningSession", "ExplanationLevel",
]
