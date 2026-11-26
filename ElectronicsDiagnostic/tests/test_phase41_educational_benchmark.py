"""Phase 4.1 — Educational benchmark.

Evaluates Hint, Guided, and Full explanation levels SEPARATELY
(the spec's section 4). Measures:
  * Unwanted disclosure: Hint and Guided must NOT contain the reference
    solution value. Full MUST contain it.
  * Mastery memorization-resistance: re-solving the SAME exercise
    repeatedly must NOT inflate the distinct-exercise count, and
    mastery must not jump to a level that requires distinct exercises.
  * Anti-memorization with the INDEPENDENT set: a user who memorizes
    one exercise's answer must still fail a DIFFERENT exercise on the
    same skill (the transfer shapes from D10).
"""
import re
import pytest

from eda.learning_system import LearningSession, ExplanationLevel
from eda.learning_system import ExerciseGenerator


# ---- Hint / Guided / Full disclosure (spec section 4) ----------------

def test_hint_never_discloses_solution(tmp_app):
    """Across many exercises, the Hint level must never contain the
    reference solution value."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    leaks = 0
    total = 0
    for sk in ("ohm_kirchhoff", "voltage_divider", "series_parallel"):
        for seed in range(1, 11):
            exs = sess.generator.generate(sk, n=1, seed=seed)
            if not exs:
                continue
            ex = exs[0]
            total += 1
            hint = sess.explain(ex.__dict__, level=ExplanationLevel.HINT)
            # The hint must not contain the literal reference value as a
            # standalone number. (It may contain the formula, which is fine.)
            if ex.reference_solution and ex.reference_solution in hint:
                # Allow if the reference is a short substring of a formula
                # (e.g. "2.5" inside "R2/(R1+R2)") — check word boundary.
                if re.search(r"(?<![\w/])" + re.escape(ex.reference_solution)
                             + r"(?!\w)", hint):
                    leaks += 1
    assert leaks == 0, f"hint leaked the solution {leaks}/{total} times"
    assert total >= 20


def test_guided_never_discloses_final_number(tmp_app):
    """Guided walks the reasoning but must NOT show the final answer
    number."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    leaks = 0
    total = 0
    for sk in ("ohm_kirchhoff", "voltage_divider", "series_parallel"):
        for seed in range(1, 11):
            exs = sess.generator.generate(sk, n=1, seed=seed)
            if not exs:
                continue
            ex = exs[0]
            total += 1
            guided = sess.explain(ex.__dict__, level=ExplanationLevel.GUIDED)
            if ex.reference_solution and re.search(
                    r"(?<![\w/])" + re.escape(ex.reference_solution)
                    + r"(?!\w)", guided):
                leaks += 1
    assert leaks == 0, f"guided leaked the solution {leaks}/{total} times"
    assert total >= 20


def test_full_discloses_solution_with_provenance(tmp_app):
    """Full MUST disclose the reference solution AND the provenance."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    for sk in ("ohm_kirchhoff", "voltage_divider"):
        exs = sess.generator.generate(sk, n=1, seed=5)
        ex = exs[0]
        full = sess.explain(ex.__dict__, level=ExplanationLevel.FULL)
        assert ex.reference_solution in full, "full must disclose the solution"
        assert "Provenance" in full or "engine" in full.lower(), \
            "full must cite provenance"


def test_default_level_is_not_full(tmp_app):
    """The default explanation level must be hint or guided, not full."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    assert sess.level in ("hint", "guided")


# ---- Mastery memorization-resistance (independent set) ----------------

def test_repeated_same_exercise_does_not_inflate_distinct(tmp_app):
    """Solving the SAME exercise 5 times must keep the distinct count
    at 1 — the anti-memorization guarantee."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    ex = sess.generator.generate("ohm_kirchhoff", n=1, seed=10)[0]
    m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", ex.prompt)
    m_i = re.search(r"(\d+(?:\.\d+)?)\s*A", ex.prompt)
    v, i = float(m_v.group(1)), float(m_i.group(1))
    ref = v / i
    ans = (f"Using Ohm's law R = V/I, with V={v} V and I={i} A, so "
           f"R = {ref:.4g} Ω. Therefore the load is {ref:.4g} Ω.")
    for _ in range(5):
        sess.attempt(ex.__dict__, ans, hints_used=0)
    m = sess.assessment.mastery("ohm_kirchhoff")
    assert m.distinct_exercises_solved == 1
    # Level must NOT be practitioner (needs 2 distinct) or expert.
    assert m.level in ("novice", "apprentice")


def test_memorized_answer_fails_transfer_exercise(tmp_app):
    """A user who memorized exercise A's answer must FAIL a transfer
    exercise B on the same skill — the transfer shape poses a different
    question, so the memorized number is wrong."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    # Generate the base exercise and a transfer exercise.
    base = sess.generator.generate("ohm_kirchhoff", n=1, seed=10)[0]
    trans = sess.generator.generate_transfer("ohm_kirchhoff", n=1, seed=11)[0]
    # The base answer (R = V/I). The transfer asks for I = V/R, so the
    # base's number is NOT the transfer's answer.
    m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", base.prompt)
    m_i = re.search(r"(\d+(?:\.\d+)?)\s*A", base.prompt)
    v, i = float(m_v.group(1)), float(m_i.group(1))
    memorized = v / i  # the base answer
    # Submit the memorized number to the TRANSFER exercise (with reasoning).
    bad_ans = (f"Using Ohm's law, the answer is {memorized:.4g} Ω. "
               f"Therefore {memorized:.4g} Ω.")
    ev = sess.evaluator.evaluate(trans.__dict__, bad_ans, hints_used=0)
    # The memorized number is the wrong quantity for the transfer
    # (transfer asks for current in A, not resistance in Ω).
    assert not ev.correct, ("memorized answer must FAIL the transfer "
                            "exercise; transfer poses a different question")


def test_independent_reasoning_answers_grade_correctly_on_transfer(tmp_app):
    """A correct, reasoned answer to a transfer exercise (the inverse
    shape) must pass — proving the evaluator handles the transfer
    shapes, not just the base shape."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    sess = LearningSession.create()
    trans = sess.generator.generate_transfer("ohm_kirchhoff", n=1, seed=11)[0]
    # The transfer asks "given R and V, find I". Parse R and V from the
    # prompt and compute I = V/R.
    m_r = re.search(r"(\d+(?:\.\d+)?)\s*Ω", trans.prompt)
    m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", trans.prompt)
    r, v = float(m_r.group(1)), float(m_v.group(1))
    i = v / r
    ans = (f"Using Ohm's law I = V/R, with V={v} V and R={r} Ω, so "
           f"I = {v}/{r} = {i:.4g} A. Therefore the current is {i:.4g} A.")
    ev = sess.evaluator.evaluate(trans.__dict__, ans, hints_used=0)
    assert ev.correct, f"correct transfer answer should pass; issues={ev.issues}"


# ---- Spaced review with the independent set ---------------------------

def test_review_after_mistake_shortens_interval_deterministic(tmp_app):
    """The spaced-review scheduler shortens the interval after a mistake
    and is deterministic (independent of the test set)."""
    from eda.learning_system import SpacedReview
    database = pytest.importorskip("eda.database")
    database.init_db()
    sr = SpacedReview()
    a = sr.schedule(quality=5, prev_interval=6.0, prev_ease=2.5,
                   prev_repetitions=2, now="2026-01-01T00:00:00")
    b = sr.schedule(quality=5, prev_interval=6.0, prev_ease=2.5,
                   prev_repetitions=2, now="2026-01-01T00:00:00")
    assert a == b  # deterministic
    fail = sr.schedule(quality=1, prev_interval=a[0], prev_ease=a[1],
                       prev_repetitions=a[2], now="2026-01-01T00:00:00")
    assert fail[0] < a[0]  # shortened
    assert fail[2] == 0     # repetitions reset
