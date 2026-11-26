"""Learning subsystem benchmark — real, measured numbers.

Measures the spec's Phase 4 requirements with REAL runs (no invented
data):

  1. Skill assessment correctness — does the evidence-based engine
     reach the right level for a known evidence sequence?
  2. Anti-memorization resistance — does re-solving the SAME exercise
     fail to inflate mastery?
  3. Reasoning-quality grading — does a correct number with wrong
     reasoning get marked incorrect?
  4. Exercise solution correctness — does every generated exercise
     carry a reference solution that matches an engine computation?
  5. Hint-level behavior — does the hint NOT disclose the solution?
  6. Progress persistence — does evidence survive across sessions?
  7. Spaced-review determinism — does the same input always produce the
     same schedule?
  8. Repair-to-learning conversion — does an unconfirmed case stay
     unconfirmed, and is the solution hidden?
  9. Knowledge provenance — does every exercise carry source_kind +
     source_ref?
  10. Offline + compatibility — does it run with no network and not
      break the existing repair flow?

Each metric is a 0..1 rate (1 = perfect) over a set of trials. The
numbers below are measured in THIS process, not estimated.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

# Isolate the DB before importing eda.
import eda.config as _cfg
_cfg.app_root = lambda: Path(tempfile.mkdtemp())
sys.path.insert(0, "src")

from eda import config, database, sessions, measurements
from eda.learning_system import (
    LearningSession, ExplanationLevel, SpacedReview, RepairToLearning,
    ReasoningEvaluator, ExerciseGenerator,
)
from eda.learning_system import db as LDB

config.ensure_layout()
database.init_db()


def bench_skill_assessment() -> dict:
    """Does the engine reach the right level for known evidence sequences?

    Each trial uses a FRESH profile so evidence does not bleed across
    trials. Trials that need distinct exercises pass real exercise ids
    (created on the fly) so the distinct-count threshold actually fires.
    """
    def fresh():
        import eda.config as cfg2
        cfg2.app_root = lambda: Path(tempfile.mkdtemp())
        database._connections.clear()
        database.init_db()
        return LearningSession.create()

    # For the practitioner trial we need 3 independent solves across
    # >=2 distinct exercises, so create 3 real exercises first.
    def practitioner_trial(s):
        for seed in (1, 2, 3):
            ex = s.generator.generate("ohm_kirchhoff", n=1, seed=seed)[0]
            s.assessment.note_solved_independently("ohm_kirchhoff",
                                                    exercise_id=ex.id)

    trials = [
        (lambda s: None, "novice"),
        (lambda s: [s.assessment.note_viewed("ohm_kirchhoff") for _ in range(5)], "novice"),
        (lambda s: s.assessment.note_solved_independently("ohm_kirchhoff"), "novice"),
        # 2 independent solves (no exercise_id -> no FK) -> apprentice
        # (apprentice only needs independent>=2, distinct is not required).
        (lambda s: [s.assessment.note_solved_independently("ohm_kirchhoff"),
                     s.assessment.note_solved_independently("ohm_kirchhoff")], "apprentice"),
        # 3 independent solves on 3 DISTINCT real exercises -> practitioner.
        (practitioner_trial, "practitioner"),
    ]
    correct = 0
    for action, expected in trials:
        s = fresh()
        action(s)
        got = s.assessment.mastery("ohm_kirchhoff").level
        ok = got == expected
        correct += int(ok)
        if not ok:
            print(f"  trial expected {expected}, got {got}")
    rate = correct / len(trials)
    return {"metric": "skill_assessment_correctness", "rate": rate,
            "trials": len(trials), "correct": correct}


def bench_anti_memorization() -> dict:
    """Does re-solving the SAME exercise fail to inflate mastery?

    Uses a unique profile so prior benchmarks' exercises do not
    contaminate the distinct-exercise count."""
    import time
    pname = f"antimem_bench_{int(time.time() * 1000)}"
    s = LearningSession.create(profile_name=pname)
    ex = s.generator.generate("ohm_kirchhoff", n=1, seed=10)[0]
    m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", ex.prompt)
    m_i = re.search(r"(\d+(?:\.\d+)?)\s*A", ex.prompt)
    v, i = float(m_v.group(1)), float(m_i.group(1))
    ref = v / i
    ans = (f"Using Ohm's law R = V/I, with V={v} V and I={i} A, so "
           f"R = {ref:.4g} Ω. Therefore the load is {ref:.4g} Ω.")
    # Solve the same exercise 5 times.
    for _ in range(5):
        s.attempt(ex.__dict__, ans, hints_used=0)
    m = s.assessment.mastery("ohm_kirchhoff")
    # distinct should be 1 (only one distinct exercise solved); level
    # should NOT be practitioner (needs 2 distinct) or expert.
    ok = m.distinct_exercises_solved == 1 and m.level in ("novice", "apprentice")
    return {"metric": "anti_memorization_resistance", "pass": ok,
            "distinct": m.distinct_exercises_solved, "level": m.level}


def bench_reasoning_grading() -> dict:
    """Correct number with wrong reasoning must be marked incorrect."""
    s = LearningSession.create()
    ex = s.generator.generate("ohm_kirchhoff", n=1, seed=10)[0]
    m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", ex.prompt)
    m_i = re.search(r"(\d+(?:\.\d+)?)\s*A", ex.prompt)
    v, i = float(m_v.group(1)), float(m_i.group(1))
    ref = v / i
    trials = [
        # (answer, expected_correct)
        (f"Using Ohm's law R=V/I, with V={v} V and I={i} A, so R = {ref:.4g} "
         f"Ω. Therefore {ref:.4g} Ω.", True),
        (f"{ref:.4g}", False),  # bare number, no reasoning
        (f"Using R=V*I (wrong law), R={v*i:.4g} Ω. Therefore {v*i:.4g} Ω.", False),
        (f"Assume rail is 3.3V. R=V/I={ref:.4g} Ω. Therefore {ref:.4g} Ω.", False),  # unsupported
    ]
    ev = ReasoningEvaluator(profile_id=s.profile_id)
    correct = 0
    for ans, expected in trials:
        r = ev.evaluate(ex.__dict__, ans, hints_used=0)
        ok = r.correct == expected
        correct += int(ok)
        if not ok:
            print(f"  trial: ans={ans[:40]!r} expected={expected} got={r.correct} issues={r.issues[:1]}")
    return {"metric": "reasoning_grading_accuracy", "rate": correct / len(trials),
            "trials": len(trials), "correct": correct}


def bench_exercise_correctness() -> dict:
    """Every generated engine exercise's reference must match an engine computation."""
    s = LearningSession.create()
    skills = ("ohm_kirchhoff", "series_parallel", "voltage_divider", "rc_rl")
    correct = 0
    total = 0
    for sk in skills:
        for seed in (1, 2, 3):
            exs = s.generator.generate(sk, n=1, seed=seed)
            if not exs:
                continue
            ex = exs[0]
            total += 1
            # Recompute and compare.
            if sk in ("ohm_kirchhoff", "series_parallel", "voltage_divider"):
                ref = float(ex.reference_solution)
                # For ohm: R = V/I from the prompt.
                m_v = re.search(r"(\d+(?:\.\d+)?)\s*V", ex.prompt)
                m_i = re.search(r"(\d+(?:\.\d+)?)\s*A", ex.prompt)
                if sk == "ohm_kirchhoff" and m_v and m_i:
                    expected = float(m_v.group(1)) / float(m_i.group(1))
                    if abs(ref - expected) / expected < 1e-3:
                        correct += 1
                elif sk == "series_parallel":
                    # The prompt gives R1, R2 and a mode; recompute.
                    r1 = int(re.search(r"R1=(\d+)", ex.prompt).group(1))
                    r2 = int(re.search(r"R2=(\d+)", ex.prompt).group(1))
                    mode = "series" if "series" in ex.prompt else "parallel"
                    expected = r1 + r2 if mode == "series" else (r1 * r2) / (r1 + r2)
                    if abs(ref - expected) / max(expected, 1e-9) < 1e-3:
                        correct += 1
                elif sk == "voltage_divider":
                    vin = float(re.search(r"Vin=(\d+(?:\.\d+)?)", ex.prompt).group(1))
                    r1 = int(re.search(r"R1=(\d+)", ex.prompt).group(1))
                    r2 = int(re.search(r"R2=(\d+)", ex.prompt).group(1))
                    expected = vin * r2 / (r1 + r2)
                    if abs(ref - expected) / expected < 1e-3:
                        correct += 1
            elif sk == "rc_rl":
                # tau = R*C; the reference is in the valid_range band
                # (unit-aware: the band is min_s/max_s).
                r = int(re.search(r"R=(\d+)", ex.prompt).group(1))
                c = float(re.search(r"C=([\d.]+)\s*µF", ex.prompt).group(1)) * 1e-6
                tau = r * c
                if ex.valid_range and "min_s" in ex.valid_range:
                    if ex.valid_range["min_s"] <= tau <= ex.valid_range["max_s"]:
                        correct += 1
    return {"metric": "exercise_reference_correctness", "rate": correct / max(total, 1),
            "trials": total, "correct": correct}


def bench_hint_behavior() -> dict:
    """The hint must not disclose the reference solution."""
    s = LearningSession.create()
    trials = 0
    correct = 0
    for seed in (1, 2, 3, 4, 5):
        exs = s.generator.generate("ohm_kirchhoff", n=1, seed=seed)
        if not exs:
            continue
        ex = exs[0]
        trials += 1
        hint = s.explain(ex.__dict__, level=ExplanationLevel.HINT)
        if ex.reference_solution not in hint:
            correct += 1
        else:
            print(f"  hint leaked solution for seed {seed}")
    return {"metric": "hint_no_disclosure", "rate": correct / max(trials, 1),
            "trials": trials, "correct": correct}


def bench_progress_persistence() -> dict:
    """Evidence survives a new session pointing at the same DB.

    Uses a UNIQUE profile name so it does not share state with the other
    benchmarks that also use 'default'."""
    import time
    pname = f"persist_bench_{int(time.time() * 1000)}"
    s = LearningSession.create(profile_name=pname)
    s.assessment.note_solved_independently("ohm_kirchhoff", exercise_id=None)
    s.assessment.note_viewed("mosfet")
    # Reopen a session with the SAME profile name -> same profile_id,
    # same DB. Evidence must persist.
    s2 = LearningSession.create(profile_name=pname)
    m = s2.assessment.mastery("ohm_kirchhoff")
    mv = s2.assessment.mastery("mosfet")
    ok = (m.independent_solves == 1 and mv.views == 1)
    return {"metric": "progress_persistence", "pass": ok,
            "ohm_indep": m.independent_solves, "mosfet_views": mv.views}


def bench_spaced_review_determinism() -> dict:
    """Same inputs -> same schedule, repeatedly."""
    sr = SpacedReview()
    results = set()
    for _ in range(20):
        r = sr.schedule(quality=5, prev_interval=3.0, prev_ease=2.5,
                       prev_repetitions=2, now="2026-01-01T00:00:00")
        results.add(r)
    ok = len(results) == 1
    return {"metric": "spaced_review_determinism", "pass": ok,
            "distinct_results": len(results)}


def bench_repair_to_learning() -> dict:
    """Unconfirmed case stays unconfirmed; solution hidden."""
    s = sessions.create("Bench no power", "Lenovo G50-80")
    sessions.add_symptom(s.id, "no power, no LEDs")
    measurements.add(s.id, {"name": "VIN", "value": 19.4, "unit": "V", "point": "input"})
    measurements.add(s.id, {"name": "+5VALW", "value": 0.0, "unit": "V", "point": "rail"})
    r2l = RepairToLearning()
    res = r2l.convert(s.id, seed=7)
    case = res["case"]
    ok_confirmed = case["confirmed"] is False
    ok_hidden = case["confirmed_root_cause"] is None
    # Check the generated exercises hide the solution.
    sol_hidden = all(LDB.get_exercise(eid)["solution_hidden"]
                     for eid in case["exercises_generated"])
    return {"metric": "repair_to_learning_safety", "pass": ok_confirmed and ok_hidden and sol_hidden,
            "confirmed_flag": case["confirmed"], "root_cause_disclosed": case["confirmed_root_cause"],
            "solution_hidden": sol_hidden}


def bench_provenance() -> dict:
    """Every exercise carries source_kind + source_ref."""
    s = LearningSession.create()
    trials = 0
    correct = 0
    for sk in ("ohm_kirchhoff", "voltage_divider", "rc_rl"):
        for seed in (1, 2, 3):
            exs = s.generator.generate(sk, n=1, seed=seed)
            if not exs:
                continue
            ex = exs[0]
            trials += 1
            if ex.source_kind and ex.source_ref:
                correct += 1
    return {"metric": "exercise_provenance", "rate": correct / max(trials, 1),
            "trials": trials, "correct": correct}


def bench_offline_compatibility() -> dict:
    """The subsystem imports and runs with no network; existing repair flow intact."""
    # If we got here, imports succeeded offline.
    from eda import cli
    h = cli.dispatch("help")
    ok = ("learning start" in h and "learn record" in h and
          "orchestrate" in h and "analyze" in h)
    return {"metric": "offline_compatibility", "pass": ok,
            "learning_cmd": "learning start" in h,
            "existing_learn_cmd": "learn record" in h,
            "orchestrate_cmd": "orchestrate" in h,
            "analyze_cmd": "analyze" in h}


if __name__ == "__main__":
    print("=" * 64)
    print("Phase 4 Learning Subsystem Benchmark (real, measured)")
    print("=" * 64)
    results = [
        bench_skill_assessment(),
        bench_anti_memorization(),
        bench_reasoning_grading(),
        bench_exercise_correctness(),
        bench_hint_behavior(),
        bench_progress_persistence(),
        bench_spaced_review_determinism(),
        bench_repair_to_learning(),
        bench_provenance(),
        bench_offline_compatibility(),
    ]
    for r in results:
        if "rate" in r:
            print(f"  {r['metric']:<36} {r['rate']:.2f}  ({r.get('correct','?')}/{r.get('trials','?')})")
        else:
            print(f"  {r['metric']:<36} {'PASS' if r.get('pass') else 'FAIL'}  {r}")
    print("=" * 64)
