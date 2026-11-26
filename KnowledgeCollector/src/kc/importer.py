"""Portable, offline importer for Knowledge Collector exports.

The importer never assumes a Unix install path. It prefers KC_ASSISTANT_ROOT,
then discovers a sibling ElectronicsDiagnostic checkout, and otherwise asks
the operator to configure the root explicitly.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict


def assistant_root() -> Path:
    """Return the assistant root using a portable discovery order."""
    configured = os.environ.get("KC_ASSISTANT_ROOT")
    if configured and configured.strip():
        return Path(configured).expanduser().resolve()

    suite_root = Path(__file__).resolve().parents[3]
    candidates = [
        suite_root / "ElectronicsDiagnostic",
        Path.cwd() / "ElectronicsDiagnostic",
        Path.cwd(),
    ]
    seen = set()
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        if ((candidate / "src" / "eda").is_dir()
                or (candidate / "Knowledge").is_dir()
                or (candidate / "Config").is_dir()):
            return candidate

    raise FileNotFoundError(
        "Cannot locate the ElectronicsDiagnostic project. "
        "Set KC_ASSISTANT_ROOT to its project directory."
    )


def assistant_knowledge_dir() -> Path:
    return assistant_root() / "Knowledge"


def assistant_db_path() -> Path:
    return assistant_root() / "Config" / "eda.sqlite3"


def validate_export_file(path: Path) -> Dict[str, Any]:
    """Validate a KC Knowledge export without trusting its JSON structure."""
    result: Dict[str, Any] = {
        "ok": False, "file": str(path), "missing": [], "warnings": [],
        "stem": path.stem, "fact_count": 0,
    }
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        result["error"] = f"cannot read/parse: {exc}"
        return result

    if not isinstance(data, dict):
        result["missing"].append("top-level object")
        return result

    sections = data.get("sections")
    if not isinstance(sections, list):
        result["missing"].append("sections (must be list)")
        return result

    for i, section in enumerate(sections):
        if not isinstance(section, dict):
            result["missing"].append(f"sections[{i}] (must be object)")
            continue
        for field in ("category", "topic", "facts"):
            if field not in section:
                result["missing"].append(f"sections[{i}].{field}")
        facts = section.get("facts", [])
        if not isinstance(facts, list):
            result["missing"].append(f"sections[{i}].facts (must be list)")
            continue
        for j, fact in enumerate(facts):
            if not isinstance(fact, dict):
                result["missing"].append(f"sections[{i}].facts[{j}] (must be object)")
                continue
            text = fact.get("text")
            if not isinstance(text, str) or not text.strip():
                result["missing"].append(f"sections[{i}].facts[{j}].text")
            else:
                result["fact_count"] += 1
            if not fact.get("tier") and not fact.get("provenance"):
                result["warnings"].append(
                    f"sections[{i}].facts[{j}] has no tier or provenance"
                )

    result["ok"] = not result["missing"]
    return result


def _atomic_copy(src: Path, dst: Path) -> None:
    """Copy a file to a same-directory temporary, then atomically replace."""
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{dst.name}.", suffix=".tmp", dir=str(dst.parent),
            delete=False,
        ) as handle:
            temp_name = handle.name
            with src.open("rb") as source:
                shutil.copyfileobj(source, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, dst)
    except Exception:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise


def import_export(
    export_dir: Path, force: bool = False, dry_run: bool = False
) -> Dict[str, Any]:
    """Validate all kc_*.json files, then atomically copy them to Knowledge/."""
    export_dir = Path(export_dir).expanduser().resolve()
    if not export_dir.is_dir():
        return {"ok": False, "error": f"export dir not found: {export_dir}"}

    files = sorted(
        item for item in export_dir.glob("kc_*.json")
        if item.is_file() and item.name.lower() != "manifest.json"
    )
    if not files:
        return {"ok": False, "error": f"no kc_*.json files in {export_dir}"}

    validations = [validate_export_file(item) for item in files]
    bad = [item for item in validations if not item["ok"]]
    if bad:
        return {"ok": False, "error": "validation failed", "bad_files": bad}

    total_facts = sum(item["fact_count"] for item in validations)
    warning_count = sum(len(item["warnings"]) for item in validations)
    if dry_run:
        return {
            "ok": True, "dry_run": True, "files": len(files),
            "facts": total_facts, "warnings": warning_count,
            "source_dir": str(export_dir),
        }

    try:
        destination = assistant_knowledge_dir()
        destination.mkdir(parents=True, exist_ok=True)
    except (OSError, FileNotFoundError) as exc:
        return {"ok": False, "error": f"cannot prepare assistant Knowledge directory: {exc}"}

    copied = []
    try:
        for validation in validations:
            src = Path(validation["file"])
            dst = destination / src.name
            _atomic_copy(src, dst)
            copied.append({
                "stem": validation["stem"], "dest": str(dst),
                "facts": validation["fact_count"],
            })
    except OSError as exc:
        return {
            "ok": False, "error": f"copy failed: {exc}",
            "copied": copied, "destination": str(destination),
        }

    cleared = 0
    force_warning = ""
    if force:
        db_path = assistant_db_path()
        if not db_path.is_file():
            force_warning = f"assistant database not found: {db_path}"
        else:
            conn = None
            try:
                conn = sqlite3.connect(str(db_path), timeout=10)
                with conn:
                    table = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='knowledge_sources'"
                    ).fetchone()
                    if not table:
                        raise sqlite3.OperationalError(
                            "table knowledge_sources does not exist"
                        )
                    for validation in validations:
                        cur = conn.execute(
                            "DELETE FROM knowledge_sources WHERE file_stem=?",
                            (validation["stem"],),
                        )
                        cleared += max(cur.rowcount, 0)
            except sqlite3.Error as exc:
                force_warning = f"could not clear knowledge_sources: {exc}"
            finally:
                if conn is not None:
                    conn.close()

    result = {
        "ok": True, "copied": copied, "cleared": cleared,
        "total_facts": total_facts, "warnings": warning_count,
        "destination": str(destination),
    }
    if force_warning:
        result["force_warning"] = force_warning
    return result


def render_import_report(result: Dict[str, Any]) -> str:
    if not result.get("ok"):
        lines = [f"Import FAILED: {result.get('error', 'unknown error')}"]
        for item in result.get("bad_files", []):
            lines.append(f"  {item.get('file')}: {item.get('missing')}")
        for item in result.get("copied", []):
            lines.append(f"  copied before failure: {item.get('dest')}")
        return "\n".join(lines)

    if result.get("dry_run"):
        return (
            f"Dry run OK: {result['files']} file(s), {result['facts']} fact(s) "
            f"would be imported from {result['source_dir']}."
        )

    lines = [
        f"Imported {len(result['copied'])} file(s) "
        f"({result['total_facts']} facts) into {result['destination']}."
    ]
    for item in result["copied"]:
        lines.append(f"  {item['stem']}: {item['facts']} facts -> {item['dest']}")
    if result.get("cleared"):
        lines.append(
            f"Cleared {result['cleared']} knowledge_sources row(s); "
            "the assistant can re-ingest these files on next load."
        )
    if result.get("force_warning"):
        lines.append(f"WARNING: {result['force_warning']}")
    if result.get("warnings"):
        lines.append(f"Validation warnings: {result['warnings']}")
    return "\n".join(lines)
