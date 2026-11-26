"""Database access layer + seed data for the learning subsystem.

All learning tables are created by migration v2 (see ``migrations.py``).
This module provides typed helpers over those tables and seeds the
canonical skill categories once. It does NOT own the schema — it reads
and writes the tables that ``migrations.migrate()`` creates, so a fresh
install and an upgraded install both end up with the same tables.

Conventions enforced here:
  * Append-only history: assessments, responses, feedback, and mastery
    evidence are INSERTed, never UPDATEd. Nothing is silently overwritten.
  * Skill categories are seeded (not user-editable) so mastery is
    comparable across profiles and over time.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .. import database, migrations


# ---------------------------------------------------------------------------
# Skill categories — the 16 domains the spec asks us to track.
# ---------------------------------------------------------------------------

# (key, label, description, difficulty_order)
# difficulty_order is the canonical teaching order (easiest first); it is
# NOT a mastery score.
SKILL_CATEGORIES: List[Dict[str, Any]] = [
    {"key": "electrical_math", "label": "Electrical mathematics",
     "description": "Unit handling, SI base normalization, basic arithmetic of electrical quantities.",
     "difficulty_order": 1},
    {"key": "ohm_kirchhoff", "label": "Ohm's law and Kirchhoff's laws",
     "description": "V=IR, KVL loop residuals, KCL node residuals; applying them to a real circuit.",
     "difficulty_order": 2},
    {"key": "series_parallel", "label": "Series and parallel circuits",
     "description": "Combining resistors; equivalent resistance; current/voltage distribution.",
     "difficulty_order": 3},
    {"key": "voltage_divider", "label": "Voltage dividers",
     "description": "Vout = Vin*R2/(R1+R2); loading effects; when a divider is NOT a regulator.",
     "difficulty_order": 4},
    {"key": "rc_rl", "label": "RC / RL behavior",
     "description": "Time constants, charging/discharging, coupling vs. filter behavior.",
     "difficulty_order": 5},
    {"key": "mosfet", "label": "MOSFET operation",
     "description": "Vgs threshold, Rds(on), gate drive, body diode, common power-path topologies.",
     "difficulty_order": 6},
    {"key": "diodes_bjt", "label": "Diodes and BJTs",
     "description": "Forward drop, reverse breakdown, saturation, biasing, common failure modes.",
     "difficulty_order": 7},
    {"key": "opamps_comparators", "label": "Op-amps and comparators",
     "description": "Virtual short, feedback, open-loop gain, comparator hysteresis.",
     "difficulty_order": 8},
    {"key": "buck_boost", "label": "Buck / boost converters",
     "description": "Duty cycle vs. output, inductor current, freewheel diode, discontinuous mode.",
     "difficulty_order": 9},
    {"key": "pwm_controllers", "label": "PWM controllers",
     "description": "Feedback, compensation, soft-start, enable/UVLO, current limiting.",
     "difficulty_order": 10},
    {"key": "battery_charging", "label": "Battery charging circuits",
     "description": "CC/CV phases, termination, sense resistor, charger IC enable conditions.",
     "difficulty_order": 11},
    {"key": "power_sequencing", "label": "Power sequencing",
     "description": "Always-on rails, enable ordering, power-good signals, sequence faults.",
     "difficulty_order": 12},
    {"key": "datasheet_interp", "label": "Datasheet interpretation",
     "description": "Reading min/typ/max, conditions, guaranteed vs. typical, absolute max vs. operating.",
     "difficulty_order": 13},
    {"key": "schematic_topology", "label": "Schematic and topology analysis",
     "description": "Net tracing, rail origins, dependencies; NOT claiming understanding without extraction.",
     "difficulty_order": 14},
    {"key": "measurement_interp", "label": "Measurement interpretation",
     "description": "What a reading means, ground reference, range, contradiction detection.",
     "difficulty_order": 15},
    {"key": "fault_isolation", "label": "Fault isolation and root-cause analysis",
     "description": "Hypothesis ranking, decisive tests, distinguishing evidence, root cause vs. symptom.",
     "difficulty_order": 16},
]


# Evidence-based mastery levels, lowest to highest. These are the ONLY
# values that may appear in skill_assessments.level / mastery evidence.
# Mastery is computed from EVIDENCE (see skill_assessment.py), never
# from a single score.
LEVELS = ["novice", "apprentice", "practitioner", "expert"]


def ensure_schema() -> None:
    """Make sure the migration has run (creates the v2 tables). Safe to
    call on every learning command; idempotent."""
    try:
        migrations.migrate()
    except Exception:
        # Migrate swallows its own errors; re-running is harmless. The
        # tables are also created by init_db -> migrate on first run.
        pass
    seed_skill_categories()


def seed_skill_categories() -> int:
    """Insert the canonical skill categories if missing. Returns the
    number of categories present after seeding."""
    ensure_schema_tables()
    for c in SKILL_CATEGORIES:
        database.execute(
            "INSERT OR IGNORE INTO skill_categories(key, label, description, "
            "difficulty_order) VALUES (?,?,?,?)",
            (c["key"], c["label"], c["description"], c["difficulty_order"]),
        )
    row = database.query_one("SELECT COUNT(*) AS n FROM skill_categories")
    return int(row["n"]) if row else 0


def ensure_schema_tables() -> None:
    """Create the learning tables directly if migration somehow has not
    run (defensive; init_db calls migrate so this is a safety net)."""
    # The migrate() call in ensure_schema() handles this; this is a no-op
    # guard so importing this module never fails even before migrate.
    pass


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

def get_or_create_profile(name: str = "default",
                          default_level: str = "guided") -> Dict[str, Any]:
    """Return the profile row, creating it if missing."""
    ensure_schema()
    row = database.query_one(
        "SELECT * FROM learning_profiles WHERE name=?", (name,))
    if row:
        return dict(row)
    now = database.now_iso()
    database.execute(
        "INSERT INTO learning_profiles(name, default_level, created_at, "
        "updated_at) VALUES (?,?,?,?)",
        (name, default_level, now, now))
    row = database.query_one(
        "SELECT * FROM learning_profiles WHERE name=?", (name,))
    return dict(row)


def list_profiles() -> List[Dict[str, Any]]:
    return [dict(r) for r in database.query_all(
        "SELECT * FROM learning_profiles ORDER BY id")]


# ---------------------------------------------------------------------------
# Skill lookups
# ---------------------------------------------------------------------------

def list_skill_categories() -> List[Dict[str, Any]]:
    ensure_schema()
    return [dict(r) for r in database.query_all(
        "SELECT * FROM skill_categories ORDER BY difficulty_order")]


def get_skill_category(key: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    row = database.query_one(
        "SELECT * FROM skill_categories WHERE key=?", (key,))
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Append-only assessment / evidence helpers
# ---------------------------------------------------------------------------

def record_assessment(profile_id: int, skill_key: str, level: str,
                      evidence_type: str, exercise_id: Optional[int] = None,
                      note: str = "") -> int:
    """Insert a new assessment row. Never overwrites."""
    now = database.now_iso()
    cur = database.execute(
        "INSERT INTO skill_assessments(profile_id, skill_key, level, "
        "evidence_type, exercise_id, note, assessed_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (profile_id, skill_key, level, evidence_type, exercise_id, note, now))
    return int(cur.lastrowid)


def record_evidence(profile_id: int, skill_key: str, kind: str,
                    exercise_id: Optional[int] = None,
                    context_tag: str = "") -> int:
    """Insert a new mastery-evidence row. The ONLY inputs to mastery."""
    now = database.now_iso()
    cur = database.execute(
        "INSERT INTO mastery_evidence(profile_id, skill_key, kind, "
        "exercise_id, context_tag, created_at) VALUES (?,?,?,?,?,?)",
        (profile_id, skill_key, kind, exercise_id, context_tag, now))
    return int(cur.lastrowid)


def list_evidence(profile_id: int, skill_key: Optional[str] = None) -> List[Dict[str, Any]]:
    if skill_key:
        rows = database.query_all(
            "SELECT * FROM mastery_evidence WHERE profile_id=? AND skill_key=? "
            "ORDER BY created_at", (profile_id, skill_key))
    else:
        rows = database.query_all(
            "SELECT * FROM mastery_evidence WHERE profile_id=? "
            "ORDER BY created_at", (profile_id,))
    return [dict(r) for r in rows]


def list_assessments(profile_id: int,
                     skill_key: Optional[str] = None) -> List[Dict[str, Any]]:
    if skill_key:
        rows = database.query_all(
            "SELECT * FROM skill_assessments WHERE profile_id=? AND skill_key=? "
            "ORDER BY assessed_at DESC", (profile_id, skill_key))
    else:
        rows = database.query_all(
            "SELECT * FROM skill_assessments WHERE profile_id=? "
            "ORDER BY assessed_at DESC", (profile_id,))
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Exercise store
# ---------------------------------------------------------------------------

def upsert_exercise(exercise_key: str, skill_key: str, source_kind: str,
                    prompt: str, reference_solution: str = "",
                    valid_range: Optional[Dict[str, Any]] = None,
                    unit: str = "", formulas: str = "",
                    assumptions: str = "", difficulty: int = 2,
                    source_ref: str = "",
                    solution_hidden: bool = True) -> int:
    """Insert or update an exercise by its key. Returns the exercise id.

    D9 guard: if an exercise with this key already has user responses, we
    do NOT overwrite its prompt/reference_solution/valid_range — a param
    change must not invalidate the reference for in-flight attempts. Only
    the metadata that does not affect grading (source_ref, formulas,
    assumptions, solution_hidden) may be refreshed. If the generator
    wants a genuinely new exercise, it must use a new key (which the
    seed-specific keys already guarantee).
    """
    ensure_schema()
    now = database.now_iso()
    vr = json.dumps(valid_range) if valid_range else None
    existing = database.query_one(
        "SELECT id FROM exercises WHERE exercise_key=?", (exercise_key,))
    if existing:
        # Check whether any responses exist for this exercise.
        has_responses = database.query_one(
            "SELECT 1 AS x FROM exercise_responses WHERE exercise_id=? LIMIT 1",
            (existing["id"],))
        if has_responses:
            # In-flight protection: refresh only non-grading metadata.
            database.execute(
                "UPDATE exercises SET skill_key=?, difficulty=?, source_kind=?, "
                "source_ref=?, formulas=?, assumptions=?, solution_hidden=?, "
                "created_at=? WHERE exercise_key=?",
                (skill_key, difficulty, source_kind, source_ref, formulas,
                 assumptions, 1 if solution_hidden else 0, now, exercise_key))
            return int(existing["id"])
        database.execute(
            "UPDATE exercises SET skill_key=?, difficulty=?, source_kind=?, "
            "source_ref=?, prompt=?, reference_solution=?, valid_range=?, "
            "unit=?, formulas=?, assumptions=?, solution_hidden=?, "
            "created_at=? WHERE exercise_key=?",
            (skill_key, difficulty, source_kind, source_ref, prompt,
             reference_solution, vr, unit, formulas, assumptions,
             1 if solution_hidden else 0, now, exercise_key))
        return int(existing["id"])
    cur = database.execute(
        "INSERT INTO exercises(exercise_key, skill_key, difficulty, "
        "source_kind, source_ref, prompt, reference_solution, valid_range, "
        "unit, formulas, assumptions, solution_hidden, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (exercise_key, skill_key, difficulty, source_kind, source_ref, prompt,
         reference_solution, vr, unit, formulas, assumptions,
         1 if solution_hidden else 0, now))
    return int(cur.lastrowid)


def get_exercise(exercise_id: int) -> Optional[Dict[str, Any]]:
    row = database.query_one("SELECT * FROM exercises WHERE id=?", (exercise_id,))
    if not row:
        return None
    d = dict(row)
    if d.get("valid_range"):
        try:
            d["valid_range"] = json.loads(d["valid_range"])
        except (json.JSONDecodeError, TypeError):
            pass
    d["solution_hidden"] = bool(d.get("solution_hidden", 1))
    return d


def get_exercise_by_key(key: str) -> Optional[Dict[str, Any]]:
    row = database.query_one("SELECT * FROM exercises WHERE exercise_key=?", (key,))
    if not row:
        return None
    return get_exercise(int(row["id"]))


def list_exercises(skill_key: Optional[str] = None,
                   limit: int = 100) -> List[Dict[str, Any]]:
    if skill_key:
        rows = database.query_all(
            "SELECT * FROM exercises WHERE skill_key=? ORDER BY id LIMIT ?",
            (skill_key, limit))
    else:
        rows = database.query_all(
            "SELECT * FROM exercises ORDER BY id LIMIT ?", (limit,))
    out = []
    for r in rows:
        d = dict(r)
        if d.get("valid_range"):
            try:
                d["valid_range"] = json.loads(d["valid_range"])
            except (json.JSONDecodeError, TypeError):
                pass
        d["solution_hidden"] = bool(d.get("solution_hidden", 1))
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Responses + feedback
# ---------------------------------------------------------------------------

def record_response(exercise_id: int, profile_id: int,
                    response_text: str = "", answer_value: str = "",
                    level_used: str = "guided", hints_used: int = 0,
                    solved_independently: bool = False) -> int:
    now = database.now_iso()
    cur = database.execute(
        "INSERT INTO exercise_responses(exercise_id, profile_id, "
        "response_text, answer_value, level_used, hints_used, "
        "solved_independently, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (exercise_id, profile_id, response_text, answer_value, level_used,
         hints_used, 1 if solved_independently else 0, now))
    return int(cur.lastrowid)


def record_feedback(response_id: int, correct: Optional[bool],
                    reasoning_score: float, feedback: str,
                    issues: List[str], evaluator: str) -> int:
    now = database.now_iso()
    cur = database.execute(
        "INSERT INTO evaluation_feedback(response_id, correct, "
        "reasoning_score, feedback, issues, evaluator, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (response_id, None if correct is None else 1 if correct else 0,
         float(reasoning_score), feedback, json.dumps(issues), evaluator, now))
    return int(cur.lastrowid)


def list_responses(profile_id: int, exercise_id: Optional[int] = None) -> List[Dict[str, Any]]:
    if exercise_id:
        rows = database.query_all(
            "SELECT * FROM exercise_responses WHERE profile_id=? AND "
            "exercise_id=? ORDER BY created_at", (profile_id, exercise_id))
    else:
        rows = database.query_all(
            "SELECT * FROM exercise_responses WHERE profile_id=? "
            "ORDER BY created_at", (profile_id,))
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Goals + reviews
# ---------------------------------------------------------------------------

def add_goal(profile_id: int, skill_key: str,
             target_level: str = "practitioner") -> int:
    now = database.now_iso()
    cur = database.execute(
        "INSERT INTO learning_goals(profile_id, skill_key, target_level, "
        "created_at) VALUES (?,?,?,?)",
        (profile_id, skill_key, target_level, now))
    return int(cur.lastrowid)


def list_goals(profile_id: int) -> List[Dict[str, Any]]:
    return [dict(r) for r in database.query_all(
        "SELECT * FROM learning_goals WHERE profile_id=? ORDER BY created_at",
        (profile_id,))]


def upsert_review(profile_id: int, skill_key: str, exercise_id: Optional[int],
                  interval_days: float, ease_factor: float,
                  repetitions: int, due_at: str,
                  last_reviewed_at: str = "") -> int:
    """Versioned upsert: a new row is inserted (the latest row wins).
    Keeps the audit trail of prior schedules."""
    now = database.now_iso()
    # Version = max version for this (profile, skill, exercise) + 1
    existing = database.query_one(
        "SELECT MAX(version) AS v FROM review_schedule "
        "WHERE profile_id=? AND skill_key=? AND "
        "COALESCE(exercise_id,-1)=COALESCE(?, -1)",
        (profile_id, skill_key, exercise_id))
    version = (existing["v"] + 1) if (existing and existing["v"]) else 1
    cur = database.execute(
        "INSERT INTO review_schedule(profile_id, skill_key, exercise_id, "
        "interval_days, ease_factor, repetitions, due_at, last_reviewed_at, "
        "version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (profile_id, skill_key, exercise_id, interval_days, ease_factor,
         repetitions, due_at, last_reviewed_at, version, now))
    return int(cur.lastrowid)


def latest_review(profile_id: int, skill_key: str,
                  exercise_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    if exercise_id:
        row = database.query_one(
            "SELECT * FROM review_schedule WHERE profile_id=? AND skill_key=? "
            "AND exercise_id=? ORDER BY version DESC LIMIT 1",
            (profile_id, skill_key, exercise_id))
    else:
        row = database.query_one(
            "SELECT * FROM review_schedule WHERE profile_id=? AND skill_key=? "
            "ORDER BY version DESC LIMIT 1",
            (profile_id, skill_key))
    return dict(row) if row else None


def due_reviews(profile_id: int, now_iso: Optional[str] = None) -> List[Dict[str, Any]]:
    now_iso = now_iso or database.now_iso()
    rows = database.query_all(
        "SELECT * FROM review_schedule WHERE profile_id=? AND due_at<=? "
        "ORDER BY due_at", (profile_id, now_iso))
    return [dict(r) for r in rows]
