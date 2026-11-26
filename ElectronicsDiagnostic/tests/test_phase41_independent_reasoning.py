"""Phase 4.1 — run the INDEPENDENT reasoning test set.

This test runs 51 responses (independent of the dev set) against the
evaluator and reports the ACTUAL pass rate per category. The spec says:
'Do not claim educational effectiveness based solely on unit tests' —
so this test prints the per-category confusion matrix and asserts a
minimum bar, but it does NOT claim the evaluator is perfect.
"""
import pytest

from eda.learning_system import ReasoningEvaluator
from tests.phase41_independent_reasoning_set import CASES, FIXED_EXERCISE


@pytest.mark.parametrize("category,response,expected", CASES)
def test_independent_reasoning_case(tmp_app, category, response, expected):
    """Each independent case must be graded correctly."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    ev = ReasoningEvaluator()
    r = ev.evaluate(dict(FIXED_EXERCISE), response, hints_used=0)
    assert r.correct == expected, (
        f"[cat {category}] response={response!r}\n"
        f"  expected correct={expected}, got correct={r.correct}\n"
        f"  answer_in_range={r.answer_in_range}, extracted={r.answer_value} {r.answer_unit}\n"
        f"  issues={r.issues}")


def test_independent_set_category_summary(tmp_app, capsys):
    """Run every case and print the per-category confusion matrix.
    Asserts an overall accuracy bar but reports the real numbers."""
    database = pytest.importorskip("eda.database")
    database.init_db()
    ev = ReasoningEvaluator()
    by_cat: dict = {}
    total_correct = 0
    for category, response, expected in CASES:
        r = ev.evaluate(dict(FIXED_EXERCISE), response, hints_used=0)
        ok = (r.correct == expected)
        by_cat.setdefault(category, {"n": 0, "ok": 0})
        by_cat[category]["n"] += 1
        by_cat[category]["ok"] += int(ok)
        total_correct += int(ok)
    # Print the matrix (visible in the test session with -s).
    lines = ["", "=== Independent reasoning set — per-category ==="]
    for cat in sorted(by_cat):
        d = by_cat[cat]
        lines.append(f"  cat {cat}: {d['ok']}/{d['n']} correct "
                     f"({d['ok']/d['n']:.0%})")
    lines.append(f"  TOTAL: {total_correct}/{len(CASES)} "
                f"({total_correct/len(CASES):.0%})")
    print("\n".join(lines))
    # The honest bar: the evaluator must get at least 85% of the
    # independent cases right. We do NOT claim 100% — some heuristic
    # cases (e.g. 'I think it's 2.5 because...') are deliberately
    # borderline and the rule-based evaluator may miss them. This is a
    # documented limitation, not a hidden failure.
    assert total_correct / len(CASES) >= 0.85, (
        f"independent-set accuracy {total_correct/len(CASES):.0%} below "
        f"the 85% bar; see the per-category breakdown above")
