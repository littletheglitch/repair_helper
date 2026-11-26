"""Phase 6, Task 5 — blind and held-out evaluation framework.

Builds on the validation framework (Task 3) to evaluate cases that were
NOT used during development. This module provides:

  * A HELD-OUT REGISTRY: cases are marked ``held_out=True`` and tagged
    with ``dataset`` (dev / held_out / workshop) and a ``source`` string.
    The registry tracks PROVENANCE (who created the case, when, from what).
  * A DUPLICATE / LEAKAGE CHECK: prevents the same evidence (same board +
    same measurement set) from appearing in BOTH a dev fixture and a
    held-out case.
  * ADVERSE-CASE CATEGORIES: each held-out case carries an
    ``adverse_kind`` tag in {conflicting_measurements, missing_reference,
    unknown_component, insufficient_evidence, normal} so the evaluation
    covers concrete failure modes.
  * A REPRODUCIBLE EVALUATION PROCEDURE: ``run_evaluation`` walks the
    held-out set, runs the assistant's prediction (via a caller-supplied
    predictor function), records it blind, then reveals + scores.

Honesty:
  * Synthetic cases stay explicitly synthetic (``case_type='synthetic'``);
    the framework never relabels a synthetic case as real.
  * The blind view (from validation_framework) hides the confirmed root
    cause until the prediction is frozen, so the assistant cannot see the
    answer.
  * ``detect_leakage`` checks that no held-out case's evidence fingerprint
    collides with a dev fixture's fingerprint. A collision is reported, not
    silently ignored.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

from . import validation_framework as VF
from . import database, sessions, measurements as M
from . import diagnostic_metrics as DM


# ---------------------------------------------------------------------------
# Registry tables
# ---------------------------------------------------------------------------

ADVERSE_KINDS = ("normal", "conflicting_measurements", "missing_reference",
                 "unknown_component", "insufficient_evidence")


def _ensure_tables() -> None:
    database.execute("""
        CREATE TABLE IF NOT EXISTS held_out_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            validation_case_id INTEGER NOT NULL UNIQUE,
            dataset TEXT NOT NULL,
            adverse_kind TEXT NOT NULL,
            source TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            fingerprint TEXT,
            FOREIGN KEY(validation_case_id)
              REFERENCES validation_cases(id) ON DELETE CASCADE
        )
    """)
    database.execute("""
        CREATE INDEX IF NOT EXISTS idx_ho_dataset
        ON held_out_registry(dataset)
    """)


# ---------------------------------------------------------------------------
# Fingerprint (for leakage detection)
# ---------------------------------------------------------------------------

def _fingerprint(session_id: Optional[int]) -> str:
    """A stable fingerprint of a case's evidence (symptoms + measurements),
    so the same evidence cannot appear in both a dev fixture and a held-out
    case undetected. Order-independent."""
    if not session_id:
        return ""
    syms = sorted(sessions.symptoms(session_id))
    ms = sorted(
        (m.name, round(m.value, 4) if m.value is not None else None,
         m.unit, m.point)
        for m in M.list_for(session_id))
    blob = json.dumps({"s": syms, "m": ms}, sort_keys=True,
                       ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_held_out(validation_case_id: int,
                      dataset: str = "held_out",
                      adverse_kind: str = "normal",
                      source: str = "",
                      created_by: str = ""
                      ) -> Dict[str, Any]:
    """Register an existing validation case as held-out.

    Args:
        validation_case_id: the VF case id (must exist).
        dataset: 'dev' | 'held_out' | 'workshop'. Only 'held_out' cases
            are used in the held-out evaluation.
        adverse_kind: one of ADVERSE_KINDS.
        source: provenance string (e.g. "workshop 2026-01, technician Ali").
        created_by: who registered it.
    """
    _ensure_tables()
    if adverse_kind not in ADVERSE_KINDS:
        return {"ok": False, "error": f"invalid adverse_kind '{adverse_kind}'",
                "valid": list(ADVERSE_KINDS)}
    case = VF.get_case(validation_case_id)
    if not case:
        return {"ok": False, "error": f"no validation case {validation_case_id}"}
    fp = _fingerprint(case.session_id)
    try:
        database.execute(
            """INSERT INTO held_out_registry(
                validation_case_id, dataset, adverse_kind, source,
                created_by, created_at, fingerprint)
               VALUES(?,?,?,?,?,?,?)""",
            (validation_case_id, dataset, adverse_kind, source,
             created_by, database.now_iso(), fp))
    except Exception:
        return {"ok": False, "error": "already registered"}
    return {"ok": True, "fingerprint": fp}


def register_dev_fixture(validation_case_id: int,
                          source: str = "",
                          created_by: str = "") -> Dict[str, Any]:
    """Register a validation case as a DEV fixture (the opposite of
    held-out). Used so the leakage check can compare against dev."""
    return register_held_out(validation_case_id, dataset="dev",
                             adverse_kind="normal", source=source,
                             created_by=created_by)


# ---------------------------------------------------------------------------
# Leakage detection
# ---------------------------------------------------------------------------

def detect_leakage() -> Dict[str, Any]:
    """Check that no held-out case's evidence fingerprint collides with a
    dev fixture's fingerprint. Returns the list of collisions (empty = OK).

    A collision means the SAME evidence appears in both dev and held-out,
    which would let the assistant have memorized the answer.
    """
    _ensure_tables()
    rows = database.query_all(
        "SELECT validation_case_id, dataset, fingerprint FROM "
        "held_out_registry WHERE fingerprint != ''")
    dev_fps: Dict[str, List[int]] = {}
    held_fps: Dict[str, List[int]] = {}
    for r in rows or []:
        if r["dataset"] == "dev":
            dev_fps.setdefault(r["fingerprint"], []).append(
                r["validation_case_id"])
        elif r["dataset"] == "held_out":
            held_fps.setdefault(r["fingerprint"], []).append(
                r["validation_case_id"])
    collisions = []
    for fp, held_ids in held_fps.items():
        if fp in dev_fps:
            collisions.append({
                "fingerprint": fp,
                "dev_case_ids": dev_fps[fp],
                "held_out_case_ids": held_ids,
            })
    return {"ok": True, "collisions": collisions,
            "leakage": len(collisions) > 0}


# ---------------------------------------------------------------------------
# Held-out listing + adverse-kind coverage
# ---------------------------------------------------------------------------

def list_held_out() -> List[Dict[str, Any]]:
    _ensure_tables()
    rows = database.query_all(
        "SELECT * FROM held_out_registry WHERE dataset='held_out' ORDER BY id")
    return rows or []


def adverse_coverage() -> Dict[str, int]:
    """Count held-out cases per adverse kind, to confirm the set covers
    the required failure modes (conflicting, missing-ref, unknown, etc.)."""
    _ensure_tables()
    rows = database.query_all(
        "SELECT adverse_kind, COUNT(*) AS n FROM held_out_registry "
        "WHERE dataset='held_out' GROUP BY adverse_kind")
    cov = {k: 0 for k in ADVERSE_KINDS}
    for r in rows or []:
        cov[r["adverse_kind"]] = r["n"]
    return cov


# ---------------------------------------------------------------------------
# Reproducible evaluation procedure
# ---------------------------------------------------------------------------

PredictorFn = Callable[[Dict[str, Any]], Dict[str, Any]]
"""A predictor takes the blind case view (from VF.get_case_for_prediction)
and returns {"hypotheses": [...], "next_tests": [...], "answer_text": str,
(optional) "audit": dict, (optional) "prediction_round_2": [...]}.
The blind view does NOT contain the confirmed root cause."""


def run_evaluation(case_ids: List[int],
                   predictor: PredictorFn,
                   audits: Optional[Dict[int, Dict[str, Any]]] = None,
                   answers: Optional[Dict[int, str]] = None
                   ) -> Dict[str, Any]:
    """Run the reproducible held-out evaluation.

    For each case:
      1. Get the BLIND view (confirmed root cause hidden).
      2. Call the predictor (the assistant) — it sees only symptoms +
         measurements, never the answer.
      3. Record the prediction (freezes it; immutable).
      4. If the case has a confirmed root cause already (C case), reveal
         is a no-op; if not, the caller must reveal separately. We do NOT
         auto-reveal — that is the technician's job.
      5. Score with diagnostic_metrics.evaluate_case.

    Returns the aggregate (with explicit denominators) + per-case reports.

    Reproducibility: the predictor is a pure function of the blind view,
    so the same blind view + same predictor => same prediction. The
    framework records the frozen prediction so re-runs detect drift.
    """
    audits = audits or {}
    answers = answers or {}
    per_case: List[Dict[str, Any]] = []
    evaluated_ids: List[int] = []
    for cid in case_ids:
        case = VF.get_case(cid)
        if not case:
            per_case.append({"case_id": cid, "ok": False,
                             "error": "no such case"})
            continue
        bv = VF.get_case_for_prediction(cid)
        if not bv["ok"]:
            per_case.append({"case_id": cid, "ok": False,
                             "error": bv.get("error")})
            continue
        # If already frozen (e.g. a re-run), do NOT overwrite the prediction.
        if not bv["view"]["prediction_frozen"]:
            pred = predictor(bv["view"])
            rec = VF.record_prediction(
                cid,
                pred.get("hypotheses", []),
                pred.get("next_tests", []))
            if not rec.get("ok") and "frozen" not in rec.get("error", "").lower():
                per_case.append({"case_id": cid, "ok": False,
                                 "error": rec.get("error")})
                continue
            answers[cid] = pred.get("answer_text", "")
            audits[cid] = pred.get("audit")
        # Score.
        sc = DM.evaluate_case(cid, audits.get(cid), answers.get(cid, ""),
                               prediction_round_2=None)
        per_case.append(sc)
        evaluated_ids.append(cid)
    agg = DM.aggregate(evaluated_ids, audits=audits, answers=answers)
    return {"ok": True, "aggregate": agg, "per_case": per_case}


# ---------------------------------------------------------------------------
# Provenance report
# ---------------------------------------------------------------------------

def provenance_report() -> Dict[str, Any]:
    """Report the dataset composition + provenance of all registered cases."""
    _ensure_tables()
    rows = database.query_all(
        """SELECT r.validation_case_id, r.dataset, r.adverse_kind,
           r.source, r.created_by, r.created_at, c.case_key, c.case_type
           FROM held_out_registry r
           JOIN validation_cases c ON c.id = r.validation_case_id
           ORDER BY r.id""")
    entries = []
    for r in rows or []:
        entries.append({
            "case_id": r["validation_case_id"],
            "case_key": r["case_key"],
            "case_type": r["case_type"],
            "dataset": r["dataset"],
            "adverse_kind": r["adverse_kind"],
            "source": r["source"],
            "created_by": r["created_by"],
            "created_at": r["created_at"],
        })
    by_dataset = {"dev": 0, "held_out": 0, "workshop": 0}
    for e in entries:
        by_dataset[e["dataset"]] = by_dataset.get(e["dataset"], 0) + 1
    return {"entries": entries, "by_dataset": by_dataset,
            "adverse_coverage": adverse_coverage()}
