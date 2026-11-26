"""Controlled learning workflow.

When a repair is completed, the technician may record the case into the
verified repair-case knowledge base. The rules are strict:

* ONLY technician-confirmed cases enter the verified knowledge base.
* Unverified information (LLM-generated, forum suggestions, hypotheses)
  is NEVER automatically promoted to verified status.
* Every update is versioned, traceable, reversible and auditable.
* The LLM may never convert its own answers into verified knowledge.

Storage:
- ``repair_cases`` table (added by migration v1) holds the case data
  with ``technician_confirmed=1`` and a ``version`` counter.
- Updates create a new version row, not an in-place overwrite, so any
  change is reversible by reverting to the prior version.

Provenance tier for learned cases: ``technician-confirmed`` (the highest
authority below manufacturer-spec).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import database, sessions, measurements as M
from . import knowledge_base


@dataclass
class CaseRecord:
    case_key: str
    session_id: Optional[int]
    symptoms: List[str]
    measurements: List[Dict[str, Any]]
    actual_fault: str
    replaced_components: List[str]
    confirming_test: str
    outcome: str
    technician_confirmed: bool = False
    version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def record_case(session_id: int, actual_fault: str,
                replaced_components: List[str], confirming_test: str,
                outcome: str, technician_confirmed: bool = True,
                case_key: str = "") -> Dict[str, Any]:
    """Record a completed repair case. Requires technician confirmation.

    Returns the stored record dict. If ``technician_confirmed`` is False
    the case is stored but flagged unverified; it does NOT enter the
    verified knowledge base.
    """
    s = sessions.get(session_id)
    if not s:
        return {"ok": False, "error": f"no session {session_id}"}
    if not actual_fault:
        return {"ok": False, "error": "actual_fault required"}
    syms = sessions.symptoms(session_id)
    ms = M.list_for(session_id)
    m_data = [{"name": m.name, "value": m.value, "unit": m.unit,
               "point": m.point, "reference": m.reference,
               "adapter_state": m.adapter_state, "battery_state": m.battery_state}
              for m in ms]
    if not case_key:
        case_key = f"case_session_{session_id}"
    # Versioning: if a case with this key exists, increment version.
    existing = database.query_one(
        "SELECT MAX(version) AS v FROM repair_cases WHERE case_key=?",
        (case_key,),
    )
    version = (existing["v"] + 1) if (existing and existing["v"]) else 1
    database.execute(
        "INSERT INTO repair_cases(case_key, session_id, symptoms, measurements, "
        "actual_fault, replaced_components, confirming_test, outcome, "
        "technician_confirmed, version, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (case_key, session_id, json.dumps(syms), json.dumps(m_data),
         actual_fault, json.dumps(replaced_components), confirming_test,
         outcome, int(technician_confirmed), version,
         database.now_iso(), database.now_iso()),
    )
    # Only technician-confirmed cases promote to the verified KB.
    if technician_confirmed:
        database.import_fact(
            category="repair-case",
            topic=f"{s.board_model or 'unknown board'}",
            fact=(f"Technician-confirmed repair: symptom={'; '.join(syms)}; "
                  f"actual_fault={actual_fault}; "
                  f"replaced={', '.join(replaced_components) or 'none'}; "
                  f"confirming_test={confirming_test}; outcome={outcome}."),
            provenance="technician-confirmed",
            confidence="verified",
            source_title=f"Repair case {case_key} v{version}",
            tier="technician-confirmed",
        )
    rec = CaseRecord(
        case_key=case_key, session_id=session_id, symptoms=syms,
        measurements=m_data, actual_fault=actual_fault,
        replaced_components=replaced_components,
        confirming_test=confirming_test, outcome=outcome,
        technician_confirmed=technician_confirmed, version=version,
    )
    return {"ok": True, "record": rec.to_dict(), "version": version}


def list_cases(technician_confirmed_only: bool = False) -> List[Dict[str, Any]]:
    """List recorded repair cases (latest version of each case_key)."""
    sql = ("SELECT * FROM repair_cases r WHERE version = "
           "(SELECT MAX(version) FROM repair_cases WHERE case_key = r.case_key)")
    if technician_confirmed_only:
        sql += " AND technician_confirmed=1"
    sql += " ORDER BY case_key"
    rows = database.query_all(sql)
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["symptoms"] = json.loads(d.get("symptoms") or "[]")
            d["measurements"] = json.loads(d.get("measurements") or "[]")
            d["replaced_components"] = json.loads(d.get("replaced_components") or "[]")
        except json.JSONDecodeError:
            pass
        out.append(d)
    return out


def get_case(case_key: str, version: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Get a specific case (latest version, or a specific version)."""
    if version is None:
        row = database.query_one(
            "SELECT * FROM repair_cases WHERE case_key=? "
            "ORDER BY version DESC LIMIT 1", (case_key,)
        )
    else:
        row = database.query_one(
            "SELECT * FROM repair_cases WHERE case_key=? AND version=?",
            (case_key, version),
        )
    if not row:
        return None
    d = dict(row)
    try:
        d["symptoms"] = json.loads(d.get("symptoms") or "[]")
        d["measurements"] = json.loads(d.get("measurements") or "[]")
        d["replaced_components"] = json.loads(d.get("replaced_components") or "[]")
    except json.JSONDecodeError:
        pass
    return d


def revert_case(case_key: str, to_version: int) -> Dict[str, Any]:
    """Revert a case to an earlier version by re-storing that version's
    data as a new (higher) version. The audit trail is preserved; nothing
    is deleted."""
    old = get_case(case_key, to_version)
    if not old:
        return {"ok": False, "error": f"no version {to_version} for {case_key}"}
    latest = database.query_one(
        "SELECT MAX(version) AS v FROM repair_cases WHERE case_key=?", (case_key,)
    )
    new_version = (latest["v"] + 1) if (latest and latest["v"]) else 1
    # Re-serialize any list/dict fields (get_case parsed them).
    def _ser(v):
        return v if isinstance(v, str) else json.dumps(v)
    database.execute(
        "INSERT INTO repair_cases(case_key, session_id, symptoms, measurements, "
        "actual_fault, replaced_components, confirming_test, outcome, "
        "technician_confirmed, version, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (case_key, old["session_id"], _ser(old["symptoms"]), _ser(old["measurements"]),
         old["actual_fault"], _ser(old["replaced_components"]), old["confirming_test"],
         old["outcome"], old["technician_confirmed"], new_version,
         old["created_at"], database.now_iso()),
    )
    return {"ok": True, "reverted_to": to_version, "new_version": new_version}


def case_audit_trail(case_key: str) -> List[Dict[str, Any]]:
    """Return every version of a case, oldest first (auditable)."""
    rows = database.query_all(
        "SELECT case_key, version, actual_fault, technician_confirmed, "
        "created_at, updated_at FROM repair_cases WHERE case_key=? "
        "ORDER BY version", (case_key,)
    )
    return [dict(r) for r in rows]
