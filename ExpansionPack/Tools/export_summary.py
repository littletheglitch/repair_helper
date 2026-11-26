#!/usr/bin/env python3
"""Export a human + machine-readable summary of the Expansion Pack.

Walks the pack, counts records, runs the validators, and writes
a summary file (default: download/summary.json inside the pack) plus a
Markdown report (download/summary.md).

Pure standard library.
"""
from __future__ import annotations

import json
import runpy
import sys
from datetime import datetime, timezone
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK_ROOT))

from DiagnosticEngines.core import load_engine  # noqa: E402
from DiagnosticEngines.core.component_db import ComponentDatabase  # noqa: E402


def _run_validator(script_name: str) -> dict:
    """Run one of the validators as a module and capture its exit code."""
    script = PACK_ROOT / "Tools" / script_name
    try:
        # Capture stdout by importing + calling main directly.
        import importlib.util
        spec = importlib.util.spec_from_file_location(script_name[:-3], script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # Save/restore stdout to capture the printed summary.
        import io
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = mod.main()
        finally:
            sys.stdout = old
        return {"exit_code": rc, "output": buf.getvalue()}
    except Exception as e:  # noqa: BLE001
        return {"exit_code": 99, "output": f"validator crashed: {e}"}


def build_summary() -> dict:
    db = ComponentDatabase.load(PACK_ROOT)
    engine = load_engine()
    db_summary = db.summary()
    rail_families = sorted(engine.all_rails().keys())
    rule_count = len(engine.all_rules())
    rules_by_topology: dict[str, int] = {}
    for r in engine.all_rules():
        t = r.get("topology", "?")
        rules_by_topology[t] = rules_by_topology.get(t, 0) + 1

    val_db = _run_validator("validate_database.py")
    val_rules = _run_validator("validate_rules.py")

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pack_root": str(PACK_ROOT),
        "schema_version": "1.0.0",
        "counts": {
            "ic_records": db_summary["ic_count"],
            "mosfet_records": db_summary["mosfet_count"],
            "replacement_mappings": db_summary["replacement_mapping_count"],
            "rail_families": len(rail_families),
            "diagnostic_rules": rule_count,
        },
        "ic_by_category": db_summary["ic_by_category"],
        "mosfet_by_channel": db_summary["mosfet_by_channel"],
        "rules_by_topology": rules_by_topology,
        "rail_families": rail_families,
        "validation": {
            "validate_database": {
                "exit_code": val_db["exit_code"],
                "passed": val_db["exit_code"] == 0,
            },
            "validate_rules": {
                "exit_code": val_rules["exit_code"],
                "passed": val_rules["exit_code"] == 0,
            },
        },
    }


def to_markdown(summary: dict) -> str:
    lines = []
    lines.append("# Electronics Diagnostic Expansion Pack — Summary\n")
    lines.append(f"Generated: `{summary['generated_at_utc']}`\n")
    lines.append(f"Schema version: `{summary['schema_version']}`\n")
    lines.append("## Counts\n")
    for k, v in summary["counts"].items():
        lines.append(f"- **{k}**: {v}")
    lines.append("\n## IC records by category\n")
    for k, v in sorted(summary["ic_by_category"].items()):
        lines.append(f"- {k}: {v}")
    lines.append("\n## MOSFET records by channel\n")
    for k, v in summary["mosfet_by_channel"].items():
        lines.append(f"- {k}: {v}")
    lines.append("\n## Diagnostic rules by topology\n")
    for k, v in sorted(summary["rules_by_topology"].items()):
        lines.append(f"- {k}: {v}")
    lines.append("\n## Rail families\n")
    for r in summary["rail_families"]:
        lines.append(f"- {r}")
    lines.append("\n## Validation\n")
    for name, res in summary["validation"].items():
        flag = "PASS" if res["passed"] else "FAIL"
        lines.append(f"- **{name}**: {flag} (exit code {res['exit_code']})")
    return "\n".join(lines) + "\n"


def main() -> int:
    summary = build_summary()
    out_dir = PACK_ROOT / "download"
    out_dir.mkdir(exist_ok=True)
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    with open(out_dir / "summary.md", "w", encoding="utf-8") as f:
        f.write(to_markdown(summary))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
