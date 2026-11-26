"""Tests for the Skill Assessment Engine (Phase 4).

Core guarantees:
  * Mastery is evidence-based; viewing an explanation does NOT raise it.
  * Only independent solves (and new-context demonstrations) raise mastery.
  * Levels: novice < apprentice < practitioner < expert, with the
    documented evidence thresholds.
  * Anti-memorization: re-solving the SAME exercise does not increase
    the distinct-exercise count.
"""
import re
import pytest

from eda.learning_system import LearningSession, SkillAssessment
from eda.learning_system import db as LDB


def _solve_independent(sess, skill, seed):
    """Generate an exercise for `skill` and submit a correct, reasoned answer."""
    exs = sess.generator.generate(skill, n=1, seed=seed)
    assert exs, f"no exercise generated for {skill}"
    ex = exs[0]
    # Build a correct, reasoned answer for the ohm exercises (the
    # generator gives V and I; ref is R = V/I).
    if "ohm" in ex.prompt.lower() or "resistance" in ex.prompt.lower():
        m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", ex.prompt)
        m_i = re.search(r"(\d+(?:\.\d+)?)\s*A", ex.prompt)
        if m_v and m_i:
            v, i = float(m_v.group(1)), float(m_i.group(1))
            ref = v / i
            ans = (f"Using Ohm's law R = V/I, with V={v} V and I={i} A, "
                   f"so R = {v}/{i} = {ref:.4g} Ω. Therefore the load "
                   f"resistance is {ref:.4g} Ω.")
            sess.attempt(ex.__dict__, ans, hints_used=0)
            return ex
    # For non-ohm exercises, just record the evidence directly so the
    # test can still verify mastery math.
    sess.assessment.note_solved_independently(skill, exercise_id=ex.id)
    return ex


def test_skill_categories_seeded(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    cats = LDB.list_skill_categories()
    assert len(cats) == 16
    keys = {c["key"] for c in cats}
    # Spot-check the spec's domains.
    for k in ("electrical_math", "ohm_kirchhoff", "voltage_divider",
              "mosfet", "fault_isolation", "measurement_interp",
              "power_sequencing", "datasheet_interp"):
        assert k in keys


def test_mastery_starts_at_novice(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    m = sess.assessment.mastery("ohm_kirchhoff")
    assert m.level == "novice"
    assert m.independent_solves == 0


def test_viewing_does_not_raise_mastery(tmp_app):
    """The central anti-memorization guarantee."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    for _ in range(10):
        sess.assessment.note_viewed("ohm_kirchhoff")
    m = sess.assessment.mastery("ohm_kirchhoff")
    assert m.level == "novice", "viewing must NOT raise mastery"
    assert m.views == 10
    assert m.independent_solves == 0


def test_attempting_without_solving_does_not_raise_mastery(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    for _ in range(5):
        sess.assessment.note_attempted("ohm_kirchhoff")
    m = sess.assessment.mastery("ohm_kirchhoff")
    assert m.level == "novice"


def test_solved_with_hints_partially_counts(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    # 1 independent + 2 with hints -> apprentice (per the threshold).
    # exercise_id=None to avoid FK constraints (these are synthetic
    # evidence rows for the mastery-math test).
    sess.assessment.note_solved_independently("ohm_kirchhoff", exercise_id=None)
    sess.assessment.note_solved_with_hints("ohm_kirchhoff", exercise_id=None)
    sess.assessment.note_solved_with_hints("ohm_kirchhoff", exercise_id=None)
    m = sess.assessment.mastery("ohm_kirchhoff")
    assert m.level == "apprentice"


def test_apprentice_after_two_independent_distinct(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    _solve_independent(sess, "ohm_kirchhoff", seed=10)
    _solve_independent(sess, "ohm_kirchhoff", seed=20)
    m = sess.assessment.mastery("ohm_kirchhoff")
    assert m.level == "apprentice"
    assert m.independent_solves == 2
    assert m.distinct_exercises_solved == 2


def test_practitioner_after_three_distinct(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    for seed in (10, 20, 30):
        _solve_independent(sess, "ohm_kirchhoff", seed=seed)
    m = sess.assessment.mastery("ohm_kirchhoff")
    assert m.level == "practitioner"


def test_anti_memorization_repeated_same_exercise(tmp_app):
    """Re-solving the SAME exercise does not increase the distinct count
    and so cannot inflate mastery."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    ex = _solve_independent(sess, "ohm_kirchhoff", seed=10)
    distinct_before = sess.assessment.mastery("ohm_kirchhoff").distinct_exercises_solved
    # Solve the SAME exercise 5 more times.
    for _ in range(5):
        sess.assessment.note_solved_independently("ohm_kirchhoff",
                                                   exercise_id=ex.id)
    m = sess.assessment.mastery("ohm_kirchhoff")
    assert m.distinct_exercises_solved == distinct_before  # unchanged
    # Independent count went up but distinct did not, so the level
    # should not jump to expert (which needs 4 distinct + 2 new-context).
    assert m.level in ("apprentice", "practitioner")


def test_expert_requires_consolidation(tmp_app):
    """Expert needs 5 independent solves across 4 distinct exercises
    AND 2 new-context demonstrations — a single lucky run cannot reach it."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    for seed in (10, 20, 30):
        _solve_independent(sess, "ohm_kirchhoff", seed=seed)
    assert sess.assessment.mastery("ohm_kirchhoff").level == "practitioner"
    # A 4th distinct solve -> still practitioner (new_context fires but
    # only 1 new-context demo, and we need 2 for expert).
    _solve_independent(sess, "ohm_kirchhoff", seed=40)
    assert sess.assessment.mastery("ohm_kirchhoff").level == "practitioner"
    # 5th distinct -> expert now (5 indep, 4 distinct, 2 new-context).
    _solve_independent(sess, "ohm_kirchhoff", seed=50)
    assert sess.assessment.mastery("ohm_kirchhoff").level == "expert"


def test_snapshot_history_is_append_only(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    sess.assessment.snapshot("ohm_kirchhoff", note="first")
    _solve_independent(sess, "ohm_kirchhoff", seed=10)
    _solve_independent(sess, "ohm_kirchhoff", seed=20)
    sess.assessment.snapshot("ohm_kirchhoff", note="second")
    rows = sess.assessment.history("ohm_kirchhoff")
    assert len(rows) >= 2
    # Both snapshots are present (append-only).
    notes = {r["note"] for r in rows}
    assert "first" in notes and "second" in notes


def test_all_mastery_covers_every_category(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    ms = sess.assessment.all_mastery()
    assert len(ms) == 16
    assert all(m.level == "novice" for m in ms)
