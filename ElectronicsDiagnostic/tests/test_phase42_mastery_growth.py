"""Phase 4.2 — mastery growth and memorization resistance.

Item 5 of the spec:
  * mastery increases only via valid independent exercises;
  * transferring to a new circuit/operating condition is measured;
  * memorizing the answer or repeating the same exercise does NOT
    increase mastery.
"""
import re
import pytest

from eda.learning_system import LearningSession, ExplanationLevel
from eda.learning_system import ExerciseGenerator


def _ohm_answer(ex):
    m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", ex.prompt)
    m_i = re.search(r"(\d+(?:\.\d+)?)\s*A", ex.prompt)
    if not (m_v and m_i):
        return None
    v, i = float(m_v.group(1)), float(m_i.group(1))
    ref = v / i
    return (f"Using Ohm's law R = V/I, with V={v} V and I={i} A, so "
            f"R = {ref:.4g} Ω. Therefore the load is {ref:.4g} Ω.")


def test_repeated_same_exercise_does_not_raise_distinct(tmp_app):
    """Solving the SAME base exercise 5 times keeps distinct count at 1."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    ex = sess.generator.generate("ohm_kirchhoff", n=1, seed=10)[0]
    ans = _ohm_answer(ex)
    for _ in range(5):
        sess.attempt(ex.__dict__, ans, hints_used=0)
    m = sess.assessment.mastery("ohm_kirchhoff")
    assert m.distinct_exercises_solved == 1
    assert m.level in ("novice", "apprentice")


def test_distinct_transfer_exercises_raise_distinct(tmp_app):
    """Solving TWO distinct exercises (base + transfer shape) on the
    same skill raises the distinct count to 2 — mastery grows via
    independent exercises, not repetition."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    # 1) Solve the base ohm exercise.
    ex1 = sess.generator.generate("ohm_kirchhoff", n=1, seed=10)[0]
    sess.attempt(ex1.__dict__, _ohm_answer(ex1), hints_used=0)
    m1 = sess.assessment.mastery("ohm_kirchhoff")
    assert m1.distinct_exercises_solved == 1
    # 2) Solve the transfer exercise (inverse shape).
    ex2 = sess.generator.generate_transfer("ohm_kirchhoff", n=1, seed=11)[0]
    # The transfer asks for I given R,V; build a correct reasoned answer.
    m_r = re.search(r"(\d+(?:\.\d+)?)\s*Ω", ex2.prompt)
    m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", ex2.prompt)
    if m_r and m_v:
        r, v = float(m_r.group(1)), float(m_v.group(1))
        i = v / r
        ans2 = (f"Using Ohm's law I = V/R, with V={v} V and R={r} Ω, so "
                f"I = {v}/{r} = {i:.4g} A. Therefore the current is "
                f"{i:.4g} A.")
        sess.attempt(ex2.__dict__, ans2, hints_used=0)
    m2 = sess.assessment.mastery("ohm_kirchhoff")
    assert m2.distinct_exercises_solved >= 2, (
        "mastery distinct must grow when solving a DIFFERENT (transfer) "
        "exercise, not by repeating the same one")


def test_memorized_answer_fails_transfer_in_session(tmp_app):
    """A user who memorized the base exercise's answer and submits it
    to the transfer exercise gets it WRONG — mastery does not grow from
    memorization."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    base = sess.generator.generate("ohm_kirchhoff", n=1, seed=10)[0]
    trans = sess.generator.generate_transfer("ohm_kirchhoff", n=1, seed=11)[0]
    # Build the base's numeric answer.
    m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", base.prompt)
    m_i = re.search(r"(\d+(?:\.\d+)?)\s*A", base.prompt)
    v, i = float(m_v.group(1)), float(m_i.group(1))
    memorized = v / i
    # Submit the memorized NUMBER (with reasoning) to the transfer.
    bad = (f"Using Ohm's law, the answer is {memorized:.4g} Ω. "
           f"Therefore {memorized:.4g} Ω.")
    ev = sess.evaluator.evaluate(trans.__dict__, bad, hints_used=0)
    assert not ev.correct
    # And after the failed attempt, distinct count stays 0 (no mastery).
    sess.attempt(trans.__dict__, bad, hints_used=0)
    m = sess.assessment.mastery("ohm_kirchhoff")
    assert m.distinct_exercises_solved == 0


def test_scenario_exercises_are_distinct_from_transfer(tmp_app):
    """The scenario exercises (item 1) and transfer exercises (item 2)
    have distinct exercise keys — solving one does not count as the
    other. Both contribute to mastery only when solved correctly."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    scens = gen.generate_scenarios(n=3, seed=7)
    trans = gen.generate_transfer("fault_isolation", n=1, seed=11)
    scen_keys = {e.exercise_key for e in scens}
    trans_keys = {e.exercise_key for e in trans}
    # No overlap.
    assert not (scen_keys & trans_keys)
    # Scenario keys carry the scenario prefix; transfer keys carry
    # the _transfer_ infix.
    assert all("scenario" in k for k in scen_keys)
    assert all("_transfer_" in k for k in trans_keys)


def test_datasheet_exercises_cite_part_revision_page(tmp_app):
    """Every datasheet exercise must cite part number, revision, and
    page — no value without a valid source."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    exs = gen.generate_datasheet()
    assert len(exs) >= 10
    for e in exs:
        # source_ref must contain 'rev', 'p<digit>', and a URL.
        sr = e.source_ref
        assert "rev" in sr
        assert re.search(r"p\d+", sr)
        assert "http" in sr
        # prompt must cite the part and page.
        assert re.search(r"datasheet \(rev .+?, p\d+\)", e.prompt)
