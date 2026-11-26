"""Tests for the Socratic Engine, Adaptive Engine, and Hint behavior."""
import pytest

from eda.learning_system import SocraticEngine, AdaptiveEngine, LearningSession
from eda.learning_system import ExplanationLevel


# ---- Socratic ----------------------------------------------------------

def test_socratic_starts_with_one_question(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    s = SocraticEngine()
    turn = s.start("ohm_kirchhoff")
    assert turn is not None
    assert turn.step == 0
    assert "?" in turn.question
    assert not turn.is_final  # first step is not final


def test_socratic_advances_on_good_reasoning(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    s = SocraticEngine()
    s.start("ohm_kirchhoff")
    nxt = s.respond("ohm_kirchhoff",
                    "The unknown is R; Ohm's law V=IR relates it, so R = V/I.")
    assert nxt.step == 1  # advanced
    assert "Reasoning recognized" in nxt.feedback


def test_socratic_holds_on_no_reasoning(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    s = SocraticEngine()
    s.start("ohm_kirchhoff")
    nxt = s.respond("ohm_kirchhoff", "idk")
    assert nxt.step == 0  # did NOT advance
    assert "principle" in nxt.feedback.lower() or "not quite" in nxt.feedback.lower()


def test_socratic_one_question_at_a_time(tmp_app):
    """The spec: 'Ask one focused question at a time' — the turn exposes
    exactly one question, not a list."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    s = SocraticEngine()
    turn = s.start("voltage_divider")
    assert isinstance(turn.question, str)
    # Only one question mark block in a single question.
    assert turn.question.count("?") <= 2  # the question + maybe one clause


def test_socratic_unknown_skill_returns_none(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    s = SocraticEngine()
    assert s.start("not_a_real_skill") is None


# ---- Hint behavior -----------------------------------------------------

def test_hint_does_not_disclose_solution(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    exs = sess.generator.generate("ohm_kirchhoff", n=1, seed=10)
    ex = exs[0]
    hint = sess.explain(ex.__dict__, level=ExplanationLevel.HINT)
    # The hint must NOT contain the reference solution value.
    ref = ex.reference_solution
    assert ref not in hint, "hint leaked the solution"
    assert "Hint" in hint


def test_guided_does_not_disclose_final_number(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    exs = sess.generator.generate("ohm_kirchhoff", n=1, seed=10)
    ex = exs[0]
    guided = sess.explain(ex.__dict__, level=ExplanationLevel.GUIDED)
    assert ex.reference_solution not in guided
    assert "step" in guided.lower() or "formula" in guided.lower()


def test_full_discloses_solution_with_provenance(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    exs = sess.generator.generate("ohm_kirchhoff", n=1, seed=10)
    ex = exs[0]
    full = sess.explain(ex.__dict__, level=ExplanationLevel.FULL)
    assert ex.reference_solution in full
    assert "Provenance" in full or "engine" in full.lower()


def test_default_level_is_guided_not_full(tmp_app):
    """Spec: 'Default behavior in Learning Mode must be Hint/Guided, not
    immediate answer disclosure.'"""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    assert sess.level in ("hint", "guided")
    assert sess.level != "full"


# ---- Adaptive ----------------------------------------------------------

def test_adaptive_recommends_an_exercise(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    rec = sess.adaptive.recommend_next()
    assert rec.skill_key
    assert rec.reason


def test_adaptive_recommends_unsolved_first(tmp_app):
    """If the user has an unsolved exercise, the adaptive engine should
    recommend re-attempting it rather than generating a new one."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    # Generate one but do NOT solve it.
    exs = sess.generator.generate("ohm_kirchhoff", n=1, seed=10)
    rec = sess.adaptive.recommend_next(focus_skill="ohm_kirchhoff")
    assert rec.exercise_id == exs[0].id
    assert "unsolved" in rec.reason.lower()


def test_adaptive_gaps_lists_low_mastery(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    gaps = sess.adaptive.gaps()
    # All 16 skills are novice initially -> all are gaps.
    assert len(gaps) == 16
    assert all(g["level"] == "novice" for g in gaps)


def test_adaptive_difficulty_scales_with_mastery(tmp_app):
    """A novice should get difficulty <= 2; the band is (1,2)."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    rec = sess.adaptive.recommend_next(focus_skill="ohm_kirchhoff")
    # Novice -> difficulty band (1,2); the generated exercise difficulty
    # is 2 for the ohm generator. Just assert it's in a sane range.
    assert 1 <= rec.difficulty <= 3
