"""Database migrations and integrity checks.

Phase 1 used ``CREATE TABLE IF NOT EXISTS`` only. Phase 2 adds a
proper migration layer so future schema changes are reversible and
auditable, while preserving the existing schema and data.

Design:

* Migrations are version-numbered and idempotent. Each migration has a
  ``up`` and a ``down`` (down is best-effort; some changes cannot be
  reversed, in which case down records the limitation and skips).
* The current schema version is stored in ``PRAGMA user_version``.
* ``init_db`` (in database.py) still creates everything from scratch
  for new installs; ``migrate()`` here is called after ``init_db`` to
  apply incremental changes to existing installs.
* Integrity check and recovery procedures are provided: ``integrity``,
  ``recover``, ``backup``, ``restore``.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config
from . import database


# Schema version history:
#   v0 : Phase 1 initial schema (CREATE IF NOT EXISTS).
#   v1 : add tier column to knowledge_facts; add datasheet_params,
#        power_sequences, power_sequence_nodes, hypotheses_v2, nets,
#        pins, connections, reference_ranges, measurement_validations,
#        benchmark_cases, repair_cases tables.
#   v2 : Phase 4 learning subsystem — learning_profiles, skill_categories,
#        skill_assessments, learning_sessions, exercises, exercise_responses,
#        evaluation_feedback, mastery_evidence, learning_goals,
#        review_schedule. All append-only where it matters; no silent
#        overwrite of prior assessments.
SCHEMA_VERSION = 2


# Each migration: (version, up_sql_list, down_sql_list, description)
# up_sql_list is a list of statements; each is run separately so we can
# swallow "duplicate column" / "already exists" errors safely.
MIGRATIONS: List[Tuple[int, List[str], List[str], str]] = [
    (
        1,
        [
            # knowledge_facts: add tier column (default = "verified-reference")
            # to preserve Phase 1 facts.
            "ALTER TABLE knowledge_facts ADD COLUMN tier TEXT DEFAULT 'verified-reference'",
            # datasheet params
            """CREATE TABLE IF NOT EXISTS datasheet_params (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER,
                part_number TEXT NOT NULL,
                manufacturer TEXT,
                package TEXT,
                pin_count INTEGER,
                parameter TEXT NOT NULL,
                value TEXT,
                unit TEXT,
                min_value TEXT,
                typ_value TEXT,
                max_value TEXT,
                rating_type TEXT,
                page INTEGER,
                section TEXT,
                original_text TEXT,
                extraction_confidence REAL DEFAULT 0.0,
                verification_status TEXT DEFAULT 'unverified',
                extracted_at TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE SET NULL
            )""",
            # power sequences (per-board models)
            """CREATE TABLE IF NOT EXISTS power_sequences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                board_model TEXT,
                description TEXT,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS power_sequence_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sequence_id INTEGER NOT NULL,
                node_key TEXT NOT NULL,
                node_type TEXT NOT NULL,
                display_name TEXT,
                state TEXT,
                depends_on TEXT,
                enable_signal TEXT,
                power_good_signal TEXT,
                nominal_voltage TEXT,
                ordinal INTEGER DEFAULT 0,
                FOREIGN KEY(sequence_id) REFERENCES power_sequences(id) ON DELETE CASCADE
            )""",
            # hypotheses v2 (structured)
            """CREATE TABLE IF NOT EXISTS hypotheses_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                hypothesis_key TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'plausible',
                confidence_basis TEXT,
                supporting TEXT,
                contradicting TEXT,
                required_evidence TEXT,
                suggested_test TEXT,
                rank INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )""",
            # circuit topology
            """CREATE TABLE IF NOT EXISTS nets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sequence_id INTEGER,
                name TEXT NOT NULL,
                kind TEXT,
                origin TEXT,
                FOREIGN KEY(sequence_id) REFERENCES sessions(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS pins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                component_ref TEXT NOT NULL,
                pin TEXT NOT NULL,
                net_id INTEGER,
                role TEXT,
                FOREIGN KEY(net_id) REFERENCES nets(id) ON DELETE SET NULL
            )""",
            """CREATE TABLE IF NOT EXISTS connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sequence_id INTEGER,
                from_pin_id INTEGER,
                to_pin_id INTEGER,
                net_id INTEGER,
                source TEXT,
                page INTEGER,
                verified INTEGER DEFAULT 0,
                FOREIGN KEY(sequence_id) REFERENCES sessions(id) ON DELETE CASCADE,
                FOREIGN KEY(from_pin_id) REFERENCES pins(id) ON DELETE CASCADE,
                FOREIGN KEY(to_pin_id) REFERENCES pins(id) ON DELETE CASCADE,
                FOREIGN KEY(net_id) REFERENCES nets(id) ON DELETE SET NULL
            )""",
            # reference ranges + measurement validations
            """CREATE TABLE IF NOT EXISTS reference_ranges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                lo REAL,
                hi REAL,
                unit TEXT,
                operating_condition TEXT,
                source TEXT,
                tier TEXT DEFAULT 'verified-reference',
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS measurement_validations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                measurement_id INTEGER,
                verdict TEXT,
                findings TEXT,
                validated_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )""",
            # benchmark + learning
            """CREATE TABLE IF NOT EXISTS benchmark_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_key TEXT NOT NULL UNIQUE,
                title TEXT,
                board_model TEXT,
                symptoms TEXT,
                measurements TEXT,
                permitted_documents TEXT,
                expected_fault TEXT,
                held_out INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS repair_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_key TEXT NOT NULL,
                session_id INTEGER,
                symptoms TEXT,
                measurements TEXT,
                actual_fault TEXT,
                replaced_components TEXT,
                confirming_test TEXT,
                outcome TEXT,
                technician_confirmed INTEGER DEFAULT 0,
                version INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE SET NULL
            )""",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_repair_cases_kv ON repair_cases(case_key, version)",
            "CREATE INDEX IF NOT EXISTS idx_ds_part ON datasheet_params(part_number)",
            "CREATE INDEX IF NOT EXISTS idx_hv2_session ON hypotheses_v2(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_nets_seq ON nets(sequence_id)",
            "CREATE INDEX IF NOT EXISTS idx_pins_net ON pins(net_id)",
            "CREATE INDEX IF NOT EXISTS idx_conn_seq ON connections(sequence_id)",
            "CREATE INDEX IF NOT EXISTS idx_rranges_name ON reference_ranges(name)",
        ],
        [
            # DOWN: cannot safely drop the tier column on SQLite < 3.35.
            # We leave the column in place; reverting Phase 2 requires a
            # full restore from backup. This is the documented limitation.
        ],
        "Phase 2: tier column, datasheet_params, power_sequences, hypotheses_v2, "
        "topology tables, reference_ranges, benchmark/repair cases.",
    ),
    (
        2,
        [
            # ---- Learning profiles ----------------------------------------
            # One row per technician (a single-user app, but the profile row
            # lets us version preferences and track the active profile).
            """CREATE TABLE IF NOT EXISTS learning_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                default_level TEXT DEFAULT 'guided',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            # ---- Skill categories (seeded, not user-editable) -------------
            # The spec's 16 skill domains. 'difficulty_order' is the
            # canonical ordering used by the adaptive engine to pick the
            # next concept; it is NOT a mastery score.
            """CREATE TABLE IF NOT EXISTS skill_categories (
                key TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                description TEXT,
                difficulty_order INTEGER NOT NULL
            )""",
            # ---- Skill assessments (append-only; never overwritten) ------
            # Every assessment is a new row. Mastery is computed from the
            # EVIDENCE in mastery_evidence, not from a single score, so a
            # lucky guess never becomes "mastery".
            """CREATE TABLE IF NOT EXISTS skill_assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                skill_key TEXT NOT NULL,
                level TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                exercise_id INTEGER,
                note TEXT,
                assessed_at TEXT NOT NULL,
                FOREIGN KEY(profile_id) REFERENCES learning_profiles(id),
                FOREIGN KEY(skill_key) REFERENCES skill_categories(key),
                FOREIGN KEY(exercise_id) REFERENCES exercises(id)
            )""",
            # ---- Learning sessions ---------------------------------------
            """CREATE TABLE IF NOT EXISTS learning_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                mode TEXT DEFAULT 'learning',
                focus_skill TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                FOREIGN KEY(profile_id) REFERENCES learning_profiles(id)
            )""",
            # ---- Exercises (catalog; one row per exercise def) -----------
            # 'reference_solution' is the deterministic answer (from an
            # engine or a known derivation). 'valid_range' is a JSON
            # envelope for exercises with a tolerance band. 'source_kind'
            # records provenance: kb | engine | datasheet | confirmed_case |
            # synthetic. 'solution_hidden' means the user is expected to
            # solve it independently; the CLI must not auto-disclose it.
            """CREATE TABLE IF NOT EXISTS exercises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exercise_key TEXT NOT NULL UNIQUE,
                skill_key TEXT NOT NULL,
                difficulty INTEGER DEFAULT 2,
                source_kind TEXT NOT NULL,
                source_ref TEXT,
                prompt TEXT NOT NULL,
                reference_solution TEXT,
                valid_range TEXT,
                unit TEXT,
                formulas TEXT,
                assumptions TEXT,
                solution_hidden INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY(skill_key) REFERENCES skill_categories(key)
            )""",
            # ---- Exercise responses (append-only) -----------------------
            """CREATE TABLE IF NOT EXISTS exercise_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exercise_id INTEGER NOT NULL,
                profile_id INTEGER NOT NULL,
                response_text TEXT,
                answer_value TEXT,
                level_used TEXT DEFAULT 'guided',
                hints_used INTEGER DEFAULT 0,
                solved_independently INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(exercise_id) REFERENCES exercises(id),
                FOREIGN KEY(profile_id) REFERENCES learning_profiles(id)
            )""",
            # ---- Evaluation feedback (append-only) ----------------------
            """CREATE TABLE IF NOT EXISTS evaluation_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                response_id INTEGER NOT NULL,
                correct INTEGER,
                reasoning_score REAL,
                feedback TEXT,
                issues TEXT,
                evaluator TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(response_id) REFERENCES exercise_responses(id)
            )""",
            # ---- Mastery evidence (the ONLY thing that raises mastery) --
            # 'kind' ∈ {viewed, attempted, solved_with_hints,
            #  solved_independently, demonstrated_in_new_context}.
            # Only the last two raise mastery meaningfully.
            """CREATE TABLE IF NOT EXISTS mastery_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                skill_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                exercise_id INTEGER,
                context_tag TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(profile_id) REFERENCES learning_profiles(id),
                FOREIGN KEY(skill_key) REFERENCES skill_categories(key),
                FOREIGN KEY(exercise_id) REFERENCES exercises(id)
            )""",
            # ---- Learning goals ------------------------------------------
            """CREATE TABLE IF NOT EXISTS learning_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                skill_key TEXT NOT NULL,
                target_level TEXT,
                created_at TEXT NOT NULL,
                achieved_at TEXT,
                FOREIGN KEY(profile_id) REFERENCES learning_profiles(id),
                FOREIGN KEY(skill_key) REFERENCES skill_categories(key)
            )""",
            # ---- Review schedule (spaced repetition) ---------------------
            # 'due_at' is computed by the spaced_review scheduler. The
            # 'interval_days' / 'ease_factor' are the SM-2 state; updates
            # create a new row (versioned), the latest row wins.
            """CREATE TABLE IF NOT EXISTS review_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                skill_key TEXT NOT NULL,
                exercise_id INTEGER,
                interval_days REAL DEFAULT 1.0,
                ease_factor REAL DEFAULT 2.5,
                repetitions INTEGER DEFAULT 0,
                due_at TEXT NOT NULL,
                last_reviewed_at TEXT,
                version INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY(profile_id) REFERENCES learning_profiles(id),
                FOREIGN KEY(skill_key) REFERENCES skill_categories(key),
                FOREIGN KEY(exercise_id) REFERENCES exercises(id)
            )""",
            # Indexes for the common read paths.
            "CREATE INDEX IF NOT EXISTS idx_assessments_profile_skill "
            "ON skill_assessments(profile_id, skill_key, assessed_at)",
            "CREATE INDEX IF NOT EXISTS idx_evidence_profile_skill "
            "ON mastery_evidence(profile_id, skill_key, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_responses_exercise "
            "ON exercise_responses(exercise_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_reviews_due "
            "ON review_schedule(profile_id, due_at)",
        ],
        [],
        "Phase 4: learning subsystem tables (profiles, skill categories, "
        "assessments, sessions, exercises, responses, feedback, mastery "
        "evidence, goals, review schedule). Append-only history; no silent "
        "overwrite.",
    ),
]


def current_version() -> int:
    conn = database.connect()
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    return int(v)


def set_version(v: int) -> None:
    database.execute(f"PRAGMA user_version = {int(v)}")


def _column_exists(table: str, column: str) -> bool:
    conn = database.connect()
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(c["name"] == column for c in cols)


def migrate() -> Dict[str, Any]:
    """Apply all pending migrations. Returns a report dict.

    Each migration's statements are run individually; statements that
    fail with "duplicate column" or "already exists" are swallowed
    because the migration is idempotent on re-run.
    """
    report: Dict[str, Any] = {"from": current_version(), "to": SCHEMA_VERSION,
                              "applied": [], "skipped": [], "errors": []}
    if current_version() >= SCHEMA_VERSION:
        return report
    for ver, ups, downs, desc in MIGRATIONS:
        if ver <= current_version():
            continue
        for stmt in ups:
            try:
                database.execute(stmt)
                report["applied"].append(stmt.split()[0:3])
            except sqlite3.Error as e:
                msg = str(e)
                if "duplicate column" in msg or "already exists" in msg:
                    report["skipped"].append(msg)
                else:
                    report["errors"].append({"stmt": stmt[:80], "error": msg})
        set_version(ver)
        report["applied"].append(f"set user_version = {ver}")
    report["to"] = current_version()
    return report


# ---- Integrity check / recovery --------------------------------------

def integrity_check() -> Dict[str, Any]:
    """Run PRAGMA integrity_check and foreign_key_check. Returns a report."""
    conn = database.connect()
    ic = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    fk = [tuple(r) for r in conn.execute("PRAGMA foreign_key_check").fetchall()]
    return {
        "integrity_check": ic,
        "foreign_key_violations": [list(r) for r in fk],
        "ok": ic == ["ok"] and not fk,
        "schema_version": current_version(),
    }


def recover() -> Dict[str, Any]:
    """Best-effort recovery: run integrity_check; if 'ok', nothing to do.
    If not ok, attempt to rebuild indexes and re-run. This never deletes
    user data; severe corruption requires restore from backup."""
    report: Dict[str, Any] = {"before": integrity_check()}
    if report["before"]["ok"]:
        report["action"] = "none"
        report["after"] = report["before"]
        return report
    conn = database.connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("REINDEX")
        conn.commit()
    except sqlite3.Error as e:
        report.setdefault("errors", []).append(str(e))
    report["after"] = integrity_check()
    report["action"] = "reindex" if report["after"]["ok"] else "manual_restore_required"
    return report


def backup(dest: Optional[Path] = None) -> Path:
    """Copy the DB file to a backup. Returns the backup path."""
    src = config.db_path()
    if dest is None:
        dest = src.parent / (src.name + ".backup")
    shutil.copy2(src, dest)
    return dest


def restore(src: Path) -> Dict[str, Any]:
    """Restore the DB from a backup file. Re-runs migrations after."""
    dst = config.db_path()
    if not Path(src).exists():
        return {"ok": False, "error": f"backup not found: {src}"}
    # Reset in-memory connections so the file is not locked.
    database._connections.clear()
    shutil.copy2(src, dst)
    # Re-run init+migrate to ensure consistency.
    database.init_db()
    migrate()
    return {"ok": True, "restored_from": str(src), "to": str(dst)}
