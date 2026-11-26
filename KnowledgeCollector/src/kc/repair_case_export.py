"""Export human-reviewable repair-case candidates from collected forum posts.

No forum record is promoted automatically. All generated candidates remain
unresolved and pending review because classifier output is heuristic and a
reported fix is not independent verification.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from . import config, database


REPAIR_CLASSES = {
    "A_technician_confirmed_repair",
    "B_reported_repair_insufficient_verification",
}


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if value in (None, ""):
        return []
    return [value]


def _first_text(values: Any) -> str:
    for value in _as_list(values):
        if str(value).strip():
            return str(value).strip()
    return ""


def _case_candidate(record: dict, structured: dict, classification: dict) -> dict:
    identity = "\0".join(str(record.get(key) or "") for key in (
        "source_name", "thread_id", "post_id", "content_hash"
    ))
    digest = hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:16]

    measurements = []
    for item in _as_list(structured.get("measurements")):
        if isinstance(item, dict):
            measurements.append(item)
        else:
            measurements.append({"raw_observation": str(item)})

    symptoms = [str(x).strip() for x in _as_list(structured.get("reported_symptoms"))]
    components = [str(x).strip() for x in _as_list(structured.get("component_model"))]
    repair_actions = [str(x).strip() for x in _as_list(structured.get("repair_actions"))]
    proposed_tests = [str(x).strip() for x in _as_list(structured.get("proposed_tests"))]
    outcome = str(structured.get("reported_outcome") or "").strip()

    return {
        "case_id": f"RC_KC_{digest}",
        "device_manufacturer": "",
        "device_model": _first_text(structured.get("device_model")),
        "board_model": _first_text(structured.get("motherboard_model")),
        "platform": "",
        "symptoms": symptoms,
        "initial_observations": [],
        "measurements": measurements,
        "diagnostic_path": proposed_tests,
        "confirmed_faulty_component": "",
        "root_cause": "",
        "repair_action": " | ".join(repair_actions),
        "final_verification": "",
        "technician_notes": (
            str(record.get("text") or "")
            + (f"\nReported outcome (not independently verified): {outcome}" if outcome else "")
        ),
        "source": "forum",
        "source_title": str(record.get("title") or ""),
        "source_author": str(record.get("author") or ""),
        "source_url": str(record.get("url") or ""),
        "source_reference": (
            f"collector_source={record.get('source_name', '')}; "
            f"thread={record.get('thread_id', '')}; post={record.get('post_id', '')}; "
            f"record_id={record.get('id')}; class={classification.get('cls', '')}; "
            f"classification_note={classification.get('explanation', '')}; "
            f"content_hash={record.get('content_hash', '')}"
        ),
        "is_seed_example": False,
        "confidence": "unresolved",
        "date": str(record.get("published_at") or record.get("collected_at") or "")[:10],
        "rails_involved": [],
        "components_involved": components,
        "topology": "",
        "failure_pattern": "",
        "promotion_status": "pending",
        "promotion_notes": (
            "Imported as a candidate from a collected community post. "
            "Review the original thread, measurements, repair outcome and "
            "verification before completing or promoting this case."
        ),
    }


def export_candidates(
    out_dir: Path | None = None,
    source_name: str = "",
    include_all_relevant: bool = False,
) -> dict:
    """Export A/B repair posts as unresolved candidate records for review."""
    destination = Path(out_dir) if out_dir else config.export_dir() / "repair_case_candidates"
    destination = destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    sql = "SELECT * FROM records WHERE status='new'"
    params: tuple = ()
    if source_name:
        sql += " AND source_name=?"
        params = (source_name,)
    sql += " ORDER BY id"
    rows = database.query_all(sql, params)

    written = []
    skipped = 0
    for row in rows:
        structured = _as_dict(row.get("structured"))
        classification = _as_dict(row.get("classification"))
        cls = str(classification.get("cls") or "")
        if not include_all_relevant and cls not in REPAIR_CLASSES:
            skipped += 1
            continue

        has_repair_evidence = any((
            _as_list(structured.get("measurements")),
            _as_list(structured.get("repair_actions")),
            str(structured.get("reported_outcome") or "").strip(),
        ))
        if not has_repair_evidence:
            skipped += 1
            continue

        candidate = _case_candidate(row, structured, classification)
        path = destination / f"{candidate['case_id']}.json"
        payload = json.dumps(candidate, indent=2, ensure_ascii=False)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="\n", dir=str(destination),
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except OSError:
            if temp_name:
                Path(temp_name).unlink(missing_ok=True)
            raise
        written.append(path.name)

    manifest = {
        "schema_version": "1.0",
        "purpose": "Unresolved repair-case candidates; technician review required",
        "source_filter": source_name,
        "candidate_count": len(written),
        "skipped_count": skipped,
        "promotion_policy": "No candidate is automatically verified or promoted",
        "files": written,
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "ok": True,
        "directory": str(destination),
        "candidates": len(written),
        "skipped": skipped,
        "files": written,
    }
