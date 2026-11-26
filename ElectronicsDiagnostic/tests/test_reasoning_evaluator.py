"""Tests for the Engineering Reasoning Evaluator + Exercise Generator.

The central rule under test: a correct final answer reached by wrong
reasoning is NOT counted as correct. Plus: exercises always carry a
known reference solution, and the reference is computed by a
deterministic engine (not invented).
"""
import re
import pytest

from eda.learning_system import ReasoningEvaluator, ExerciseGenerator
from eda.learning_system import db as LDB


def _ohm_exercise(gen, skill="ohm_kirchhoff", seed=10):
    exs = gen.generate(skill, n=1, seed=seed)
    assert exs
    return exs[0]


def _ohm_answer(ex):
    """Build a correct, well-reasoned answer for an ohm exercise."""
    m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", ex.prompt)
    m_i = re.search(r"(\d+(?:\.\d+)?)\s*A", ex.prompt)
    v, i = float(m_v.group(1)), float(m_i.group(1))
    ref = v / i
    return (f"Using Ohm's law R = V/I, with V={v} V and I={i} A, so "
            f"R = {v}/{i} = {ref:.4g} Ω. Therefore the load resistance "
            f"is {ref:.4g} Ω.")


# ---- Reasoning quality --------------------------------------------------

def test_correct_answer_with_reasoning_is_correct(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    ev = ReasoningEvaluator()
    ex = _ohm_exercise(gen)
    ans = _ohm_answer(ex)
    r = ev.evaluate(ex.__dict__, ans, hints_used=0)
    assert r.correct
    assert r.answer_in_range


def test_correct_number_but_no_reasoning_is_not_correct(tmp_app):
    """The spec's central rule: do not reward a correct answer for
    incorrect (or absent) reasoning."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    ev = ReasoningEvaluator()
    ex = _ohm_exercise(gen)
    m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", ex.prompt)
    m_i = re.search(r"(\d+(?:\.\d+)?)\s*A", ex.prompt)
    v, i = float(m_v.group(1)), float(m_i.group(1))
    ref = v / i
    bare = f"{ref:.4g} Ω"  # bare number, no reasoning
    r = ev.evaluate(ex.__dict__, bare, hints_used=0)
    assert not r.correct, "bare number must not be correct"
    assert r.answer_in_range  # the number itself IS in range
    assert any("reasoning" in i.lower() or "principle" in i.lower()
               for i in r.issues)


def test_wrong_number_with_good_reasoning_is_not_correct(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    ev = ReasoningEvaluator()
    ex = _ohm_exercise(gen)
    m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", ex.prompt)
    m_i = re.search(r"(\d+(?:\.\d+)?)\s*A", ex.prompt)
    v, i = float(m_v.group(1)), float(m_i.group(1))
    wrong = (f"Using Ohm's law R = V/I, with V={v} V and I={i} A, so "
             f"R = {v*i:.4g} Ω (multiplied instead of divided). "
             f"Therefore the load is {v*i:.4g} Ω.")
    r = ev.evaluate(ex.__dict__, wrong, hints_used=0)
    assert not r.correct
    assert not r.answer_in_range


def test_wrong_units_flagged(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    ev = ReasoningEvaluator()
    ex = _ohm_exercise(gen)
    m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", ex.prompt)
    m_i = re.search(r"(\d+(?:\.\d+)?)\s*A", ex.prompt)
    v, i = float(m_v.group(1)), float(m_i.group(1))
    ref = v / i
    # Right number, wrong unit.
    ans = (f"Using Ohm's law R = V/I, with V={v} V and I={i} A, so "
           f"R = {ref:.4g} H (henries). Therefore the load is {ref:.4g} H.")
    r = ev.evaluate(ex.__dict__, ans, hints_used=0)
    assert any("unit" in i.lower() for i in r.issues) or not r.correct


def test_unsupported_assumption_flagged(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    ev = ReasoningEvaluator()
    ex = _ohm_exercise(gen)
    m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", ex.prompt)
    m_i = re.search(r"(\d+(?:\.\d+)?)\s*A", ex.prompt)
    v, i = float(m_v.group(1)), float(m_i.group(1))
    ref = v / i
    ans = (f"Assume the rail is exactly 3.3V. Using Ohm's law R = V/I, "
           f"with V={v} V and I={i} A, so R = {ref:.4g} Ω. Therefore the "
           f"load is {ref:.4g} Ω.")
    r = ev.evaluate(ex.__dict__, ans, hints_used=0)
    assert any("unsupported assumption" in i.lower() for i in r.issues)


def test_measurement_misinterpretation_flagged(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    ev = ReasoningEvaluator()
    # Build a measurement-interp exercise via the fault-isolation path
    # which references measurements; use a synthetic prompt that contains
    # the "0V = no voltage" pattern without a ground reference.
    ex_dict = {
        "id": 0, "skill_key": "measurement_interp", "difficulty": 2,
        "source_kind": "synthetic", "prompt": "Interpret this reading.",
        "reference_solution": "", "valid_range": None, "unit": "",
        "formulas": "", "assumptions": "", "solution_hidden": True,
    }
    r = ev.evaluate(ex_dict, "The 0 V reading means no voltage present.",
                    hints_used=0)
    assert any("misinterpret" in i.lower() or "ground" in i.lower()
               for i in r.issues)


def test_feedback_is_specific(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    ev = ReasoningEvaluator()
    ex = _ohm_exercise(gen)
    r = ev.evaluate(ex.__dict__, "42", hints_used=0)
    assert r.feedback
    assert "Reasoning score" in r.feedback
    assert len(r.issues) > 0  # specific issues, not generic praise


# ---- Exercise correctness (reference is engine-computed) --------------

def test_ohm_exercise_reference_is_exact(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    ex = _ohm_exercise(gen, seed=10)
    # Recompute R = V/I from the prompt and compare to the reference.
    m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", ex.prompt)
    m_i = re.search(r"(\d+(?:\.\d+)?)\s*A", ex.prompt)
    v, i = float(m_v.group(1)), float(m_i.group(1))
    expected = v / i
    # The reference is stored to 4 significant figures, so allow a small
    # relative tolerance matching that rounding.
    assert abs(float(ex.reference_solution) - expected) / expected < 1e-3
    assert ex.unit == "Ω"
    assert ex.source_kind == "engine"


def test_divider_exercise_reference_is_exact(tmp_app):
    from eda import schemas as S
    from eda.engines import base as E
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    exs = gen.generate("voltage_divider", n=1, seed=10)
    assert exs
    ex = exs[0]
    # Pull Vin, R1, R2 out of the prompt and recompute via the engine.
    m_v = re.search(r"Vin=(\d+(?:\.\d+)?)\s*V", ex.prompt)
    m_r1 = re.search(r"R1=(\d+)\s*Ω", ex.prompt)
    m_r2 = re.search(r"R2=(\d+)\s*Ω", ex.prompt)
    vin, r1, r2 = float(m_v.group(1)), int(m_r1.group(1)), int(m_r2.group(1))
    res = E.call("electrical_math", "voltage_divider",
                  {"V_in": S.Quantity.parse(vin, "V"),
                   "R1": S.Quantity.parse(r1, "Ω"),
                   "R2": S.Quantity.parse(r2, "Ω")})
    assert res.ok
    # The reference is stored to 4 significant figures, so allow a small
    # relative tolerance matching that rounding (not exact equality).
    assert abs(float(ex.reference_solution) - res.value.magnitude) / \
           res.value.magnitude < 1e-3


def test_rc_exercise_has_valid_range(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    exs = gen.generate("rc_rl", n=1, seed=10)
    assert exs
    ex = exs[0]
    assert ex.valid_range is not None
    # The RC exercise uses a unit-aware range (accepts both s and ms).
    assert "min_s" in ex.valid_range and "max_s" in ex.valid_range
    assert "min_ms" in ex.valid_range and "max_ms" in ex.valid_range
    assert "accept_units" in ex.valid_range
    # tau = R*C; recompute.
    m_r = re.search(r"R=(\d+)\s*Ω", ex.prompt)
    m_c = re.search(r"C=([\d.]+)\s*µF", ex.prompt)
    r, c = int(m_r.group(1)), float(m_c.group(1)) * 1e-6
    tau = r * c
    # The unit-aware range carries a seconds band (min_s/max_s).
    assert ex.valid_range["min_s"] <= tau <= ex.valid_range["max_s"]
    # And the equivalent ms band.
    assert ex.valid_range["min_ms"] <= tau * 1000 <= ex.valid_range["max_ms"]


def test_exercise_solution_hidden_by_default(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    ex = _ohm_exercise(gen)
    assert ex.solution_hidden is True


# ---- Provenance --------------------------------------------------------

def test_exercise_carries_source_ref(tmp_app):
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    ex = _ohm_exercise(gen)
    assert ex.source_kind == "engine"
    assert "electrical_math" in ex.source_ref


# ---- Held-out cases (not used during development of the evaluator) ----
# These are deliberately different exercise/answer shapes than the ones
# above; the evaluator must still grade them correctly.

HELD_OUT = [
    # (answer_text, expected_correct)
    # A correct reasoned answer with the connective "therefore".
    ("Ohm: R = V/I. V=5, I=2, so R = 5/2 = 2.5 Ω. Therefore 2.5 Ω.", True),
    # A correct number with the WRONG law invoked.
    ("Using R = V*I, with V=5 V and I=2 A, R = 10 Ω. Therefore 10 Ω.", False),
    # No number at all.
    ("I think it's about 2 ohms give or take.", False),  # no reasoning chain
]


@pytest.mark.parametrize("answer,expected", HELD_OUT)
def test_held_out_cases(tmp_app, answer, expected):
    """Held-out answers the evaluator was NOT tuned on."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    gen = ExerciseGenerator()
    # Build a fixed exercise (V=5, I=2) by seeding the generator
    # deterministically and picking one with V=5, I=2 — or just build
    # a synthetic exercise dict with reference 2.5.
    ex_dict = {
        "id": 0, "skill_key": "ohm_kirchhoff", "difficulty": 2,
        "source_kind": "engine", "prompt": "V=5 V, I=2 A. Find R.",
        "reference_solution": "2.5", "valid_range": None, "unit": "Ω",
        "formulas": "R = V / I", "assumptions": "",
        "solution_hidden": True,
    }
    ev = ReasoningEvaluator()
    r = ev.evaluate(ex_dict, answer, hints_used=0)
    assert r.correct == expected, (
        f"held-out answer graded wrong: {answer!r} -> correct={r.correct}, "
        f"expected={expected}, issues={r.issues}")
