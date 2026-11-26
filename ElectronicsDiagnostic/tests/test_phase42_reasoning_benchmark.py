"""Phase 4.2 — run the independent reasoning benchmark (>= 50 tests)."""
import pytest

from tests.phase42_reasoning_benchmark import REASONING_CASES
from eda.learning_system import ReasoningEvaluator


@pytest.mark.parametrize("case_id,ex,response,expected", REASONING_CASES)
def test_reasoning_case(tmp_app, case_id, ex, response, expected):
    """Each reasoning case must be graded correctly."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    ev = ReasoningEvaluator()
    r = ev.evaluate(dict(ex), response, hints_used=0)
    assert r.correct == expected, (
        f"[{case_id}] expected {expected}, got {r.correct}\n"
        f"  issues={r.issues}")


def test_reasoning_benchmark_size_and_accuracy(tmp_app, capsys):
    """The benchmark must have >= 50 cases and meet an accuracy bar."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    ev = ReasoningEvaluator()
    ok = 0
    for case_id, ex, response, expected in REASONING_CASES:
        r = ev.evaluate(dict(ex), response, hints_used=0)
        if r.correct == expected:
            ok += 1
    n = len(REASONING_CASES)
    print(f"\n=== Reasoning benchmark: {n} cases, {ok}/{n} correct ===")
    assert n >= 50, f"need >= 50 reasoning cases, have {n}"
    assert ok / n >= 0.90, f"accuracy {ok/n:.0%} below 90% bar"


def test_reasoning_benchmark_multistep_breakdown():
    """Multi-step cases: correct multi-step answers pass, single-step fail."""
    from eda.learning_system import ReasoningEvaluator
    ev = ReasoningEvaluator()
    ms = [c for c in REASONING_CASES if c[0].startswith("MS")]
    correct_pass = 0
    wrong_fail = 0
    for case_id, ex, response, expected in ms:
        r = ev.evaluate(dict(ex), response, hints_used=0)
        if expected and r.correct:
            correct_pass += 1
        if not expected and not r.correct:
            wrong_fail += 1
    assert correct_pass + wrong_fail == len(ms)


def test_reasoning_benchmark_cc_breakdown():
    """Correlation-causation cases: the distinction passes; symptom-as-
    cause fails."""
    ev = ReasoningEvaluator()
    cc = [c for c in REASONING_CASES if c[0].startswith("CC")]
    for case_id, ex, response, expected in cc:
        r = ev.evaluate(dict(ex), response, hints_used=0)
        assert r.correct == expected, f"[{case_id}] {r.issues}"


def test_reasoning_benchmark_datasheet_breakdown():
    """Datasheet value-class cases."""
    ev = ReasoningEvaluator()
    ds = [c for c in REASONING_CASES if c[0].startswith("DS")]
    for case_id, ex, response, expected in ds:
        r = ev.evaluate(dict(ex), response, hints_used=0)
        assert r.correct == expected, f"[{case_id}] {r.issues}"
