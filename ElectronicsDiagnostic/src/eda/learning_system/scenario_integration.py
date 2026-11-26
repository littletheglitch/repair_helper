"""Phase 4.2 — wire the 24 validated technician scenarios (from Phase 4.1)
into the exercise generator as gradable exercises.

Each scenario becomes an Exercise with:
  * skill_key        — mapped from the scenario's category + content
  * reference_solution — the scenario's reference answer (hidden)
  * source_kind      — "scenario" (synthetic) or "confirmed_case" (repair)
  * source_ref       — the scenario_id + source provenance string
  * solution_hidden  — True (revealed only on explicit user request)
  * formulas/assumptions — carried from the scenario's source

Synthetic scenarios (source in textbook/datasheet/measurement with
synthetic=True) are kept distinct from confirmed-repair scenarios
(source == confirmed_case). They never share an exercise_key prefix.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Map each scenario to its canonical skill_key. The scenario's own
# `category` field is a coarse bucket; this map uses the scenario_id to
# pick the precise skill from the 16 skill_categories.
SCENARIO_SKILL_MAP: Dict[str, str] = {
    # measurement -> measurement_interp
    "M1": "measurement_interp", "M2": "measurement_interp",
    "M3": "measurement_interp", "M4": "measurement_interp",
    "M5": "measurement_interp", "M6": "mosfet",
    "M7": "datasheet_interp", "M8": "measurement_interp",
    # datasheet -> datasheet_interp
    "D1": "datasheet_interp", "D2": "datasheet_interp",
    "D3": "battery_charging", "D4": "mosfet",
    "D5": "buck_boost", "D6": "buck_boost",
    # component -> the specific component skill
    "C1": "mosfet", "C2": "diodes_bjt", "C3": "fault_isolation",
    "C4": "diodes_bjt", "C5": "buck_boost", "C6": "opamps_comparators",
    # troubleshooting -> fault_isolation
    "T1": "fault_isolation", "T2": "power_sequencing",
    "T3": "battery_charging", "T4": "fault_isolation",
}


def _load_scenarios() -> List[Dict[str, Any]]:
    """Load the 24 scenarios from the Phase 4.1 data file. The data file
    lives under tests/; we import it without depending on the tests
    package being on sys.path (robust to install layout)."""
    here = Path(__file__).resolve().parent
    # Walk up to the repo root and find tests/phase41_technician_scenarios.py
    # The repo root is 3 levels up from src/eda/learning_system/.
    repo_root = here.parents[3]
    cand_paths = [
        repo_root / "tests" / "phase41_technician_scenarios.py",
        here.parent.parent.parent.parent / "tests" / "phase41_technician_scenarios.py",
        here.parent.parent.parent / "tests" / "phase41_technician_scenarios.py",
    ]
    for p in cand_paths:
        if p.exists():
            spec = importlib.util.spec_from_file_location(
                "phase41_technician_scenarios", str(p))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.SCENARIOS
    # Fallback: try a normal import (tests package on path).
    try:
        from tests.phase41_technician_scenarios import SCENARIOS  # type: ignore
        return SCENARIOS
    except Exception:
        return []


def scenario_to_exercise(scenario: Dict[str, Any],
                          profile_id: int,
                          seed: Optional[int] = None) -> Dict[str, Any]:
    """Convert one scenario dict to an Exercise-shaped dict suitable for
    db.upsert_exercise. The reference solution is the scenario's
    reference_answer (kept hidden)."""
    sid = scenario["scenario_id"]
    skill_key = SCENARIO_SKILL_MAP.get(sid, "fault_isolation")
    is_confirmed = (scenario.get("source") == "confirmed_case")
    # Distinct key prefixes so synthetic and confirmed never collide.
    prefix = "scenario_confirmed" if is_confirmed else "scenario_synth"
    exercise_key = f"{skill_key}_{prefix}_{sid}_{profile_id}_{seed or 0}"
    source_kind = "confirmed_case" if is_confirmed else "scenario"
    source_ref = f"{sid}; source={scenario.get('source','')}; " \
                 f"ref={scenario.get('source_ref','')}"
    return {
        "exercise_key": exercise_key,
        "skill_key": skill_key,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "prompt": scenario["prompt"],
        # The reference solution is the full reasoning answer; it is
        # graded on reasoning (the evaluator checks for the reasoning
        # connectives and the skill's principle), not on exact match.
        "reference_solution": scenario["reference_answer"],
        "valid_range": None,
        "unit": "",
        "formulas": "",
        "assumptions": (f"reference answer is graded on reasoning; "
                        f"synthetic={scenario.get('synthetic')}"),
        "difficulty": 3,
        "solution_hidden": True,
    }


def list_scenario_exercises(profile_id: int,
                              seed: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return all 24 scenarios as exercise dicts."""
    scenarios = _load_scenarios()
    return [scenario_to_exercise(s, profile_id, seed) for s in scenarios]
