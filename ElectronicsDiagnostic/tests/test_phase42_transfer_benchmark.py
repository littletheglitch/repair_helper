"""Phase 4.2 — run the independent transfer benchmark.

>= 100 transfer tests across all 16 skills. Reports the ACTUAL per-
skill pass rate (correct reasoned answer must pass; wrong answer must
fail). Asserts a per-skill bar.
"""
import pytest

from tests.phase42_transfer_benchmark import run_transfer_benchmark


def test_transfer_benchmark_meets_minimum_size():
    """The benchmark must cover >= 100 cases (16 skills x 8 seeds x 2
    answer-types)."""
    res = run_transfer_benchmark()
    print(f"\n=== Transfer benchmark: {res['total_cases']} skill-seed cases ===")
    print(f"correct pass rate: {res['correct_pass_rate']:.0%}")
    print(f"wrong reject rate: {res['wrong_reject_rate']:.0%}")
    print(f"invalid exercises: {res['total_invalid']}")
    for sk, d in res["per_skill"].items():
        cp = d["correct_pass"] / max(d["correct_total"], 1)
        wr = d["wrong_reject"] / max(d["wrong_total"], 1)
        print(f"  {sk}: correct {d['correct_pass']}/{d['correct_total']} "
              f"({cp:.0%}); wrong rejected {d['wrong_reject']}/{d['wrong_total']} "
              f"({wr:.0%})")
    # 16 skills x 8 seeds = 128 cases (each tests a correct and a wrong
    # answer) -> the benchmark exceeds 100 transfer tests.
    assert res["total_cases"] >= 100, (
        f"need >= 100 transfer cases, have {res['total_cases']}")


def test_transfer_benchmark_no_invalid_exercises():
    """No skill may produce an invalid (implausible) reference across
    all seeds."""
    res = run_transfer_benchmark()
    assert res["total_invalid"] == 0, (
        f"{res['total_invalid']} invalid exercises detected; "
        f"see per_skill breakdown")


@pytest.mark.parametrize("seed", [10, 11, 12, 13, 14, 15, 16, 17])
def test_transfer_correct_reasoned_answers_pass(tmp_app, seed):
    """For each skill, a correct reasoned answer to the transfer
    exercise must be graded correct. This is the per-skill positive
    correctness check."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    from tests.phase42_transfer_benchmark import TRANSFER_ANSWER_BUILDERS
    from eda.learning_system import ExerciseGenerator, ReasoningEvaluator
    gen = ExerciseGenerator()
    ev = ReasoningEvaluator()
    fails = []
    for skill, builder in TRANSFER_ANSWER_BUILDERS.items():
        exs = gen.generate_transfer(skill, n=1, seed=seed)
        if not exs:
            continue
        ex = exs[0]
        ans = builder(ex)
        if ans is None:
            continue
        r = ev.evaluate(ex.__dict__, ans, hints_used=0)
        if not r.correct:
            fails.append((skill, r.issues[:2]))
    assert not fails, f"correct transfer answers failed: {fails}"


@pytest.mark.parametrize("seed", [10, 11, 12, 13, 14, 15, 16, 17])
def test_transfer_wrong_answers_rejected(tmp_app, seed):
    """A deliberately wrong answer to each transfer exercise must be
    rejected."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    from eda.learning_system import ExerciseGenerator, ReasoningEvaluator
    from tests.phase42_transfer_benchmark import TRANSFER_ANSWER_BUILDERS
    gen = ExerciseGenerator()
    ev = ReasoningEvaluator()
    leaks = []
    for skill in TRANSFER_ANSWER_BUILDERS:
        exs = gen.generate_transfer(skill, n=1, seed=seed)
        if not exs:
            continue
        ex = exs[0]
        wrong = "The answer is 999. Therefore 999."
        r = ev.evaluate(ex.__dict__, wrong, hints_used=0)
        if r.correct:
            leaks.append(skill)
    assert not leaks, f"wrong answers wrongly accepted for: {leaks}"
