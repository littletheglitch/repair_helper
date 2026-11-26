"""Phase 6, Task 3 — end-to-end diagnostic validation framework.

A reproducible framework for technician-led validation of real repair
cases. It supports the full case lifecycle:

  1. create a validation case (type A=synthetic, B=real-unresolved,
     C=technician-confirmed),
  2. record symptoms + measurements (with units, reference, board state,
     timestamps — reused from the existing session/measurements tables),
  3. record the assistant's hypotheses BEFORE the diagnosis is revealed
     (blind evaluation: the confirmed root cause is NOT exposed until
     the prediction is frozen),
  4. record recommended next tests + rationale,
  5. record actual test results,
  6. record the final technician-confirmed root cause + outcome,
  7. compare the assistant's reasoning with the confirmed result,
  8. export a structured JSON or Markdown report.

Boundaries (enforced in code):
  * A case is created with a ``case_type`` in {synthetic, real_unresolved,
    technician_confirmed}. Only ``technician_confirmed`` cases may later
    enter the verified repair KB via the existing ``learning.record_case``
    mechanism; this module NEVER auto-promotes.
  * The confirmed root cause is stored SEPARATELY from the assistant's
    prediction and is NOT exposed by ``get_case_for_prediction`` (the
    blind view) until ``freeze_prediction`` has been called. This
    prevents leaking the answer into the assistant's context before its
    prediction is recorded.
  * Synthetic cases are explicitly labeled; they can never be marked
    ``technician_confirmed`` (a synthetic case has no real board).

This module is additive: it creates two new tables via migration v3 and
reuses the existing sessions/measurements tables for evidence. It does
NOT modify the existing repair_cases table or learning subsystem.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from . import config, database, sessions, measurements as M


# ---------------------------------------------------------------------------
# Case types
# ---------------------------------------------------------------------------
CASE_TYPES = ("synthetic", "real_unresolved", "technician_confirmed")


# ---------------------------------------------------------------------------
# Migration: add validation tables (idempotent, run on first use)
# ---------------------------------------------------------------------------

def _ensure_tables() -> None:
    """Create the validation tables if they do not exist. Idempotent."""
    database.execute("""
        CREATE TABLE IF NOT EXISTS validation_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_key TEXT NOT NULL UNIQUE,
            case_type TEXT NOT NULL,
            session_id INTEGER,
            board_model TEXT,
            technician TEXT,
            confirmed_root_cause TEXT,
            outcome TEXT,
            prediction_frozen INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE SET NULL
        )
    """)
    database.execute("""
        CREATE TABLE IF NOT EXISTS validation_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            validation_case_id INTEGER NOT NULL,
            hypothesis TEXT,
            score REAL,
            supporting TEXT,
            contradicting TEXT,
            missing TEXT,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY(validation_case_id)
              REFERENCES validation_cases(id) ON DELETE CASCADE
        )
    """)
    database.execute("""
        CREATE TABLE IF NOT EXISTS validation_next_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            validation_case_id INTEGER NOT NULL,
            test TEXT,
            rationale TEXT,
            distinguishes TEXT,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY(validation_case_id)
              REFERENCES validation_cases(id) ON DELETE CASCADE
        )
    """)
    database.execute("""
        CREATE TABLE IF NOT EXISTS validation_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            validation_case_id INTEGER NOT NULL,
            test_name TEXT,
            observed TEXT,
            notes TEXT,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY(validation_case_id)
              REFERENCES validation_cases(id) ON DELETE CASCADE
        )
    """)


# ---------------------------------------------------------------------------
# Case lifecycle
# ---------------------------------------------------------------------------

@dataclass
class ValidationCase:
    id: int
    case_key: str
    case_type: str
    session_id: Optional[int]
    board_model: str
    technician: str
    confirmed_root_cause: Optional[str]
    outcome: Optional[str]
    prediction_frozen: bool
    created_at: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def create_case(case_key: str, case_type: str,
                board_model: str = "",
                technician: str = "",
                session_id: Optional[int] = None,
                confirmed_root_cause: Optional[str] = None,
                outcome: Optional[str] = None) -> Dict[str, Any]:
    """Create a validation case.

    Args:
        case_key: a unique stable key (e.g. "workshop_2026_001").
        case_type: one of CASE_TYPES.
        board_model: the board model string.
        technician: the technician's name/id (for C cases).
        session_id: the existing session whose symptoms/measurements are
            the evidence. If None, you should create one and update.
        confirmed_root_cause: the real root cause, IF known at creation
            time. For blind evaluation, leave None and set it later via
            reveal_and_confirm(). Storing it here does NOT expose it to
            the prediction path (see get_case_for_prediction).
        outcome: 'fixed' / 'no-fix' / 'pending' etc.

    Returns:
        {"ok": bool, "case_id": int, "error": str | None}
    """
    _ensure_tables()
    if case_type not in CASE_TYPES:
        return {"ok": False, "error": f"invalid case_type '{case_type}'",
                "valid_types": list(CASE_TYPES)}
    if case_type == "synthetic" and confirmed_root_cause and technician:
        # A synthetic case has no real technician; do not allow it to be
        # created as confirmed. (Synthetic confirmed_root_cause is the
        # exercise's hidden answer, NOT a real repair confirmation.)
        if outcome == "fixed" and technician:
            return {"ok": False, "error":
                    "a synthetic case cannot be marked as a confirmed "
                    "real repair; use real_unresolved or a non-fixed outcome"}
    now = database.now_iso()
    try:
        database.execute(
            """INSERT INTO validation_cases(
                case_key, case_type, session_id, board_model, technician,
                confirmed_root_cause, outcome, prediction_frozen,
                created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (case_key, case_type, session_id, board_model, technician,
             confirmed_root_cause, outcome, 0, now, now))
    except sqlite3.IntegrityError:
        return {"ok": False, "error": f"case_key '{case_key}' already exists"}
    row = database.query_one(
        "SELECT id FROM validation_cases WHERE case_key=?", (case_key,))
    return {"ok": True, "case_id": row["id"] if row else None}


def get_case(case_id: int) -> Optional[ValidationCase]:
    _ensure_tables()
    row = database.query_one(
        "SELECT * FROM validation_cases WHERE id=?", (case_id,))
    if not row:
        return None
    return ValidationCase(
        id=row["id"], case_key=row["case_key"], case_type=row["case_type"],
        session_id=row["session_id"], board_model=row["board_model"] or "",
        technician=row["technician"] or "",
        confirmed_root_cause=row["confirmed_root_cause"],
        outcome=row["outcome"], prediction_frozen=bool(row["prediction_frozen"]),
        created_at=row["created_at"], updated_at=row["updated_at"])


def get_case_by_key(case_key: str) -> Optional[ValidationCase]:
    _ensure_tables()
    row = database.query_one(
        "SELECT * FROM validation_cases WHERE case_key=?", (case_key,))
    if not row:
        return None
    return get_case(row["id"])


# ---------------------------------------------------------------------------
# Blind evaluation: the prediction view hides the confirmed root cause
# ---------------------------------------------------------------------------

@dataclass
class BlindCaseView:
    """The case as seen by the assistant BEFORE its prediction is frozen.
    The confirmed_root_cause is intentionally absent."""
    case_id: int
    case_key: str
    case_type: str
    board_model: str
    symptoms: List[str]
    measurements: List[Dict[str, Any]]
    prediction_frozen: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_case_for_prediction(case_id: int) -> Dict[str, Any]:
    """Return the BLIND view of a case for the assistant to predict on.

    The confirmed_root_cause is NOT included. If the prediction is
    already frozen, the returned dict has ``prediction_frozen=True`` so
    the caller knows no further prediction should be recorded.

    Returns:
        {"ok": bool, "view": dict, "error": str | None}
    """
    _ensure_tables()
    c = get_case(case_id)
    if not c:
        return {"ok": False, "error": f"no validation case {case_id}"}
    syms: List[str] = []
    ms: List[Dict[str, Any]] = []
    if c.session_id:
        syms = sessions.symptoms(c.session_id)
        ms = [{"name": m.name, "value": m.value, "unit": m.unit,
               "point": m.point, "reference": m.reference,
               "board_state": m.board_state, "adapter_state": m.adapter_state,
               "battery_state": m.battery_state, "taken_at": m.taken_at}
              for m in M.list_for(c.session_id)]
    view = BlindCaseView(
        case_id=c.id, case_key=c.case_key, case_type=c.case_type,
        board_model=c.board_model, symptoms=syms, measurements=ms,
        prediction_frozen=c.prediction_frozen)
    # NOTE: confirmed_root_cause is deliberately omitted.
    return {"ok": True, "view": view.to_dict()}


# ---------------------------------------------------------------------------
# Recording the assistant's prediction (hypotheses + next tests)
# ---------------------------------------------------------------------------

def record_prediction(case_id: int,
                      hypotheses: List[Dict[str, Any]],
                      next_tests: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Record the assistant's hypotheses and recommended next tests.

    This FREEZES the prediction: after this call the confirmed root
    cause may be revealed. The frozen prediction is immutable; calling
    again raises/returns an error.

    Args:
        hypotheses: list of {"hypothesis": str, "score": float,
                            "supporting": [..], "contradicting": [..],
                            "missing": [..]}
        next_tests: list of {"test": str, "rationale": str,
                             "distinguishes": [..]}
    """
    _ensure_tables()
    c = get_case(case_id)
    if not c:
        return {"ok": False, "error": f"no validation case {case_id}"}
    if c.prediction_frozen:
        return {"ok": False, "error":
                "prediction already frozen; cannot overwrite a recorded "
                "prediction (blind-evaluation integrity). Use a new case."}
    now = database.now_iso()
    for h in hypotheses:
        database.execute(
            """INSERT INTO validation_predictions(
                validation_case_id, hypothesis, score, supporting,
                contradicting, missing, recorded_at)
               VALUES(?,?,?,?,?,?,?)""",
            (case_id, h.get("hypothesis", ""), float(h.get("score", 0.0)),
             json.dumps(h.get("supporting", []), ensure_ascii=False),
             json.dumps(h.get("contradicting", []), ensure_ascii=False),
             json.dumps(h.get("missing", []), ensure_ascii=False),
             now))
    for t in next_tests:
        database.execute(
            """INSERT INTO validation_next_tests(
                validation_case_id, test, rationale, distinguishes,
                recorded_at)
               VALUES(?,?,?,?,?)""",
            (case_id, t.get("test", ""), t.get("rationale", ""),
             json.dumps(t.get("distinguishes", []), ensure_ascii=False),
             now))
    # Freeze the prediction.
    database.execute(
        "UPDATE validation_cases SET prediction_frozen=1, updated_at=? "
        "WHERE id=?", (now, case_id))
    return {"ok": True, "frozen": True}


def freeze_prediction(case_id: int) -> Dict[str, Any]:
    """Mark a case's prediction as frozen without recording hypotheses
    (used when the assistant gave no structured prediction). After this
    the confirmed cause may be revealed."""
    _ensure_tables()
    c = get_case(case_id)
    if not c:
        return {"ok": False, "error": f"no validation case {case_id}"}
    if c.prediction_frozen:
        return {"ok": True, "frozen": True, "already": True}
    database.execute(
        "UPDATE validation_cases SET prediction_frozen=1, updated_at=? "
        "WHERE id=?", (database.now_iso(), case_id))
    return {"ok": True, "frozen": True}


# ---------------------------------------------------------------------------
# Recording actual test results (after the prediction is frozen)
# ---------------------------------------------------------------------------

def record_result(case_id: int, test_name: str, observed: str,
                  notes: str = "") -> Dict[str, Any]:
    """Record an actual test result observed by the technician."""
    _ensure_tables()
    c = get_case(case_id)
    if not c:
        return {"ok": False, "error": f"no validation case {case_id}"}
    database.execute(
        """INSERT INTO validation_results(
            validation_case_id, test_name, observed, notes, recorded_at)
           VALUES(?,?,?,?,?)""",
        (case_id, test_name, observed, notes, database.now_iso()))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Revealing the confirmed root cause + outcome (technician-confirmed only)
# ---------------------------------------------------------------------------

def reveal_and_confirm(case_id: int, confirmed_root_cause: str,
                       outcome: str,
                       technician: str = "") -> Dict[str, Any]:
    """Record the technician-confirmed root cause and outcome.

    Safety:
      * The prediction MUST be frozen first (blind evaluation). If not,
        this returns an error: the assistant's prediction must be
        recorded before the answer is revealed.
      * A synthetic case CANNOT be confirmed as a real repair.
      * This does NOT promote the case into the verified repair KB.
        Promotion happens ONLY via ``promote_to_repair_kb`` which calls
        the existing ``learning.record_case`` with
        technician_confirmed=True.
    """
    _ensure_tables()
    c = get_case(case_id)
    if not c:
        return {"ok": False, "error": f"no validation case {case_id}"}
    if not c.prediction_frozen:
        return {"ok": False, "error":
                "prediction not frozen; record or freeze the assistant's "
                "prediction BEFORE revealing the confirmed root cause "
                "(blind-evaluation integrity)."}
    if c.case_type == "synthetic":
        return {"ok": False, "error":
                "a synthetic case cannot be confirmed as a real repair"}
    if not confirmed_root_cause:
        return {"ok": False, "error": "confirmed_root_cause required"}
    database.execute(
        "UPDATE validation_cases SET confirmed_root_cause=?, outcome=?, "
        "technician=?, case_type='technician_confirmed', updated_at=? "
        "WHERE id=?",
        (confirmed_root_cause, outcome, technician or c.technician,
         database.now_iso(), case_id))
    return {"ok": True}


def promote_to_repair_kb(case_id: int) -> Dict[str, Any]:
    """Promote a technician-confirmed case into the verified repair KB.

    This is the ONLY path. It requires:
      * case_type == 'technician_confirmed',
      * prediction frozen (blind evaluation done),
      * a confirmed root cause and a 'fixed' outcome,
      * a linked session with symptoms + measurements.
    It then calls the existing ``learning.record_case`` with
    ``technician_confirmed=True`` so the existing confirmation mechanism
    is the gatekeeper — this module never writes to repair_cases directly.
    """
    from . import learning
    _ensure_tables()
    c = get_case(case_id)
    if not c:
        return {"ok": False, "error": f"no validation case {case_id}"}
    if c.case_type != "technician_confirmed":
        return {"ok": False, "error":
                f"case_type is {c.case_type}; only technician_confirmed "
                "cases may be promoted"}
    if not c.prediction_frozen:
        return {"ok": False, "error": "prediction not frozen"}
    if not c.confirmed_root_cause:
        return {"ok": False, "error": "no confirmed root cause"}
    if c.outcome != "fixed":
        return {"ok": False, "error":
                f"outcome is {c.outcome}; only 'fixed' may be promoted"}
    if not c.session_id:
        return {"ok": False, "error": "no linked session"}
    # Delegate to the existing confirmation mechanism. This is the ONLY
    # place a confirmed case enters the verified repair KB.
    rec = learning.record_case(
        c.session_id, c.confirmed_root_cause,
        replaced_components=[], confirming_test="validation_framework",
        outcome="fixed", technician_confirmed=True,
        case_key=c.case_key)
    if not rec.get("ok"):
        return {"ok": False, "error": rec.get("error", "record_case failed")}
    return {"ok": True, "repair_case": rec}


# ---------------------------------------------------------------------------
# Comparison + export
# ---------------------------------------------------------------------------

def get_predictions(case_id: int) -> List[Dict[str, Any]]:
    rows = database.query_all(
        "SELECT * FROM validation_predictions WHERE validation_case_id=? "
        "ORDER BY id", (case_id,))
    out = []
    for r in rows or []:
        out.append({
            "hypothesis": r["hypothesis"], "score": r["score"],
            "supporting": json.loads(r["supporting"] or "[]"),
            "contradicting": json.loads(r["contradicting"] or "[]"),
            "missing": json.loads(r["missing"] or "[]"),
            "recorded_at": r["recorded_at"],
        })
    return out


def get_next_tests(case_id: int) -> List[Dict[str, Any]]:
    rows = database.query_all(
        "SELECT * FROM validation_next_tests WHERE validation_case_id=? "
        "ORDER BY id", (case_id,))
    out = []
    for r in rows or []:
        out.append({
            "test": r["test"], "rationale": r["rationale"],
            "distinguishes": json.loads(r["distinguishes"] or "[]"),
            "recorded_at": r["recorded_at"],
        })
    return out


def get_results(case_id: int) -> List[Dict[str, Any]]:
    rows = database.query_all(
        "SELECT * FROM validation_results WHERE validation_case_id=? "
        "ORDER BY id", (case_id,))
    out = []
    for r in rows or []:
        out.append({"test_name": r["test_name"], "observed": r["observed"],
                     "notes": r["notes"], "recorded_at": r["recorded_at"]})
    return out


def compare_with_confirmed(case_id: int) -> Dict[str, Any]:
    """Compare the frozen prediction's top hypothesis with the confirmed
    root cause. Returns a structured comparison (not a single score)."""
    c = get_case(case_id)
    if not c:
        return {"ok": False, "error": f"no validation case {case_id}"}
    preds = get_predictions(case_id)
    if not preds:
        return {"ok": True, "comparison": None,
                "reason": "no prediction recorded"}
    if not c.confirmed_root_cause:
        return {"ok": True, "comparison": None,
                "reason": "no confirmed root cause yet (still blind or unresolved)"}
    # Simple lexical overlap: does the top hypothesis mention the
    # confirmed root cause's key words? (Transparent heuristic, not a
    # semantic match. The technician should review the comparison.)
    top = max(preds, key=lambda p: p.get("score", 0.0))
    conf_low = (c.confirmed_root_cause or "").lower()
    hyp_low = (top.get("hypothesis", "") or "").lower()
    # Token overlap on significant words.
    conf_words = {w for w in conf_low.split() if len(w) > 3}
    hyp_words = {w for w in hyp_low.split() if len(w) > 3}
    overlap = conf_words & hyp_words
    overlap_ratio = (len(overlap) / len(conf_words)) if conf_words else 0.0
    return {"ok": True, "comparison": {
        "confirmed_root_cause": c.confirmed_root_cause,
        "top_hypothesis": top.get("hypothesis", ""),
        "top_score": top.get("score", 0.0),
        "word_overlap": sorted(overlap),
        "overlap_ratio": round(overlap_ratio, 3),
        "technician_should_review": True,  # never auto-score
    }}


def export_case(case_id: int, fmt: str = "json") -> Dict[str, Any]:
    """Export a full case (evidence + prediction + results + comparison)
    as a dict (fmt='json') or a Markdown string (fmt='markdown').

    Returns:
        {"ok": bool, "content": str|dict, "error": str|None}
    """
    _ensure_tables()
    c = get_case(case_id)
    if not c:
        return {"ok": False, "error": f"no validation case {case_id}"}
    syms = sessions.symptoms(c.session_id) if c.session_id else []
    ms = [{"name": m.name, "value": m.value, "unit": m.unit,
           "point": m.point, "reference": m.reference,
           "board_state": m.board_state, "taken_at": m.taken_at}
          for m in (M.list_for(c.session_id) if c.session_id else [])]
    preds = get_predictions(case_id)
    ntests = get_next_tests(case_id)
    results = get_results(case_id)
    comp = compare_with_confirmed(case_id).get("comparison")
    bundle = {
        "case": c.to_dict(),
        "symptoms": syms,
        "measurements": ms,
        "predictions": preds,
        "next_tests": ntests,
        "results": results,
        "comparison": comp,
    }
    if fmt == "json":
        return {"ok": True, "content": bundle}
    if fmt == "markdown":
        return {"ok": True, "content": _to_markdown(bundle)}
    return {"ok": False, "error": f"unknown format '{fmt}'"}


def _to_markdown(b: Dict[str, Any]) -> str:
    c = b["case"]
    lines = [
        f"# Validation Case: {c['case_key']}",
        "",
        f"- **Type**: {c['case_type']}",
        f"- **Board**: {c['board_model'] or '(unspecified)'}",
        f"- **Technician**: {c['technician'] or '(unspecified)'}",
        f"- **Prediction frozen**: {c['prediction_frozen']}",
        f"- **Confirmed root cause**: "
        f"{c['confirmed_root_cause'] or '(not yet revealed / unresolved)'}",
        f"- **Outcome**: {c['outcome'] or '(pending)'}",
        "",
        "## Symptoms",
    ]
    for s in b["symptoms"]:
        lines.append(f"- {s}")
    lines.append("")
    lines.append("## Measurements")
    if not b["measurements"]:
        lines.append("(none)")
    for m in b["measurements"]:
        lines.append(
            f"- {m['name']} = {m['value']} {m['unit']} @ {m['point']}"
            + (f" | ref={m['reference']}" if m.get("reference") else "")
            + (f" | state={m['board_state']}" if m.get("board_state") else ""))
    lines.append("")
    lines.append("## Assistant prediction (frozen)")
    if not b["predictions"]:
        lines.append("(no structured prediction recorded)")
    for i, p in enumerate(b["predictions"], 1):
        lines.append(f"{i}. **{p['hypothesis']}** (score {p['score']})")
        if p.get("supporting"):
            lines.append(f"   - supporting: {p['supporting']}")
        if p.get("missing"):
            lines.append(f"   - missing: {p['missing']}")
    lines.append("")
    lines.append("## Recommended next tests")
    for t in b["next_tests"]:
        lines.append(f"- {t['test']}")
        if t.get("rationale"):
            lines.append(f"  - rationale: {t['rationale']}")
        if t.get("distinguishes"):
            lines.append(f"  - distinguishes: {t['distinguishes']}")
    lines.append("")
    lines.append("## Actual test results")
    if not b["results"]:
        lines.append("(none recorded)")
    for r in b["results"]:
        lines.append(f"- {r['test_name']}: {r['observed']}"
                     + (f" ({r['notes']})" if r.get("notes") else ""))
    lines.append("")
    lines.append("## Comparison with confirmed root cause")
    comp = b["comparison"]
    if not comp:
        lines.append("(not available: no prediction or no confirmed cause)")
    else:
        lines.append(f"- confirmed: {comp['confirmed_root_cause']}")
        lines.append(f"- top hypothesis: {comp['top_hypothesis']}"
                     f" (score {comp['top_score']})")
        lines.append(f"- word overlap: {comp['word_overlap']}"
                     f" (ratio {comp['overlap_ratio']})")
        lines.append("- **technician must review** (this is a transparent "
                     "lexical heuristic, not an auto-score).")
    return "\n".join(lines)


def list_cases(case_type: Optional[str] = None) -> List[ValidationCase]:
    _ensure_tables()
    if case_type:
        rows = database.query_all(
            "SELECT id FROM validation_cases WHERE case_type=? ORDER BY id",
            (case_type,))
    else:
        rows = database.query_all(
            "SELECT id FROM validation_cases ORDER BY id")
    return [get_case(r["id"]) for r in rows or []]
