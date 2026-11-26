"""Offline importer: ingest the Collector's Knowledge JSON export into
the Electronics Diagnostic Assistant.

The importer reads the Collector's ``knowledge`` export directory and
copies each ``*.json`` file into the assistant's ``Knowledge/`` directory
with a versioned stem, so the assistant's ``knowledge_base.load_builtin()``
picks it up on next startup with NO code change to the assistant.

If the user passes ``--force``, the importer also clears the
assistant's ``knowledge_sources`` registry rows for the Collector's
stems so a refresh re-ingests (otherwise the assistant skips already-
loaded stems).

The importer NEVER touches the assistant's database schema or source
code. It only:
1. Verifies each export file is valid JSON matching the expected schema.
2. Copies them into the assistant's Knowledge/ directory.
3. (Optional) Clears the assistant's knowledge_sources rows for the
   imported stems so a re-load picks them up.

The importer is run OFFLINE. It reads files from disk and writes files
to disk; it does not use the network.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


# The assistant's root (where its Knowledge/ dir lives). Default to the
# known install path; the user can override with KC_ASSISTANT_ROOT.
def assistant_root() -> Path:
    import os
    env = os.environ.get("KC_ASSISTANT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path("/home/z/my-project/download/ElectronicsDiagnostic")


def assistant_knowledge_dir() -> Path:
    return assistant_root() / "Knowledge"


def assistant_db_path() -> Path:
    return assistant_root() / "Config" / "eda.sqlite3"


def validate_export_file(path: Path) -> Dict[str, Any]:
    """Validate that a file is a Collector Knowledge export matching the
    assistant's schema. Returns a dict with ok/missing_fields/warnings."""
    result = {"ok": False, "file": str(path), "missing": [], "warnings": [],
              "stem": path.stem, "fact_count": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        result["error"] = f"cannot read/parse: {e}"
        return result
    if "sections" not in data:
        result["missing"].append("sections")
    if not isinstance(data.get("sections", []), list):
        result["missing"].append("sections (must be list)")
    # Check each section has category/topic/facts.
    for i, sec in enumerate(data.get("sections", [])):
        if "category" not in sec:
            result["missing"].append(f"sections[{i}].category")
        if "facts" not in sec:
            result["missing"].append(f"sections[{i}].facts")
        for j, fact in enumerate(sec.get("facts", [])):
            if "text" not in fact:
                result["missing"].append(f"sections[{i}].facts[{j}].text")
            else:
                result["fact_count"] += 1
    if result["missing"]:
        return result
    # Warn about facts missing provenance/tier.
    for sec in data.get("sections", []):
        for fact in sec.get("facts", []):
            if not fact.get("tier") and not fact.get("provenance"):
                result["warnings"].append("fact has no tier or provenance")
    result["ok"] = True
    return result


def import_export(export_dir: Path, force: bool = False,
                  dry_run: bool = False) -> Dict[str, Any]:
    """Import all ``*.json`` files from ``export_dir`` into the assistant's
    Knowledge directory. Returns a summary.

    ``export_dir`` is the Collector's ``knowledge`` export dir
    (config.export_dir() / 'knowledge' by default).

    ``force=True`` clears the assistant's knowledge_sources rows for the
    imported stems so a re-load re-ingests them.

    ``dry_run=True`` validates without copying.
    """
    export_dir = Path(export_dir)
    if not export_dir.exists():
        return {"ok": False, "error": f"export dir not found: {export_dir}"}
    # Only source-knowledge JSON files; skip the manifest and any non-kc_* files.
    files = sorted(f for f in export_dir.glob("*.json")
                   if f.name != "manifest.json" and f.name.startswith("kc_"))
    if not files:
        return {"ok": False, "error": f"no kc_*.json files in {export_dir}"}
    # Validate every file first.
    validations = [validate_export_file(f) for f in files]
    bad = [v for v in validations if not v["ok"]]
    if bad:
        return {"ok": False, "error": "validation failed",
                "bad_files": bad}
    if dry_run:
        return {"ok": True, "dry_run": True, "files": len(files),
                "facts": sum(v["fact_count"] for v in validations)}
    # Copy into the assistant's Knowledge dir.
    dest = assistant_knowledge_dir()
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for v in validations:
        src = Path(v["file"])
        dst = dest / src.name
        shutil.copy2(src, dst)
        copied.append({"stem": v["stem"], "dest": str(dst),
                        "facts": v["fact_count"]})
    # Optionally clear the assistant's knowledge_sources rows for these
    # stems so the next load_builtin picks them up.
    cleared = 0
    if force:
        try:
            db = assistant_db_path()
            if db.exists():
                conn = sqlite3.connect(str(db))
                for v in validations:
                    cur = conn.execute(
                        "DELETE FROM knowledge_sources WHERE file_stem=?",
                        (v["stem"],))
                    cleared += cur.rowcount or 0
                conn.commit()
                conn.close()
        except sqlite3.Error as e:
            return {"ok": True, "copied": copied,
                    "force_warning": f"could not clear knowledge_sources: {e}",
                    "cleared": cleared}
    return {"ok": True, "copied": copied, "cleared": cleared,
            "total_facts": sum(v["fact_count"] for v in validations)}


def render_import_report(r: Dict[str, Any]) -> str:
    if not r.get("ok"):
        lines = [f"Import FAILED: {r.get('error')}"]
        for v in r.get("bad_files", []):
            lines.append(f"  {v['file']}: missing {v['missing']}")
        return "\n".join(lines)
    if r.get("dry_run"):
        return (f"Dry run OK: {r['files']} file(s), {r['facts']} fact(s) "
                "would be imported.")
    lines = [f"Imported {len(r['copied'])} file(s) "
             f"({r['total_facts']} facts) into "
             f"{assistant_knowledge_dir()}."]
    for c in r["copied"]:
        lines.append(f"  {c['stem']}: {c['facts']} facts -> {c['dest']}")
    if r.get("cleared"):
        lines.append(f"Cleared {r['cleared']} knowledge_sources row(s) "
                    "(next assistant startup will re-ingest).")
    lines.append("\nNext: start the assistant; it ingests the files via "
                "knowledge_base.load_builtin().")
    return "\n".join(lines)
