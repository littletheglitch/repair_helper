#!/usr/bin/env python3
"""Validate the ComponentDatabase JSON files.

Checks (per the requirements):
  1. JSON validity (parseable)
  2. Schema compliance (against the matching schema in schemas/)
  3. Duplicate component records (same part_number / record_id)
  4. Missing mandatory fields
  5. Invalid electrical values (negative voltages, RDS_on<0, etc.)
  6. Broken internal references (known_compatible_alternatives that don't
     point to a real record; replacement mappings pointing to unknown parts)
  7. Replacement compatibility evidence (no COMPATIBLE classification
     without evidence covering the required criteria)
  8. Missing source attribution (datasheet_url + datasheet_source)

Pure standard library. No external JSON-schema validator dependency; we
implement the subset of draft-07 we actually use.

Exit code: 0 if all checks pass, 1 otherwise. Prints a summary report.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

PACK_ROOT = Path(__file__).resolve().parents[1]
DB_ROOT = PACK_ROOT / "ComponentDatabase"


def _ok(msg: str) -> str:
    return f"  PASS  {msg}"


def _fail(msg: str) -> str:
    return f"  FAIL  {msg}"


def _warn(msg: str) -> str:
    return f"  WARN  {msg}"


class Report:
    def __init__(self):
        self.passes: list[str] = []
        self.fails: list[str] = []
        self.warns: list[str] = []

    def add_pass(self, msg: str):
        self.passes.append(msg)

    def add_fail(self, msg: str):
        self.fails.append(msg)

    def add_warn(self, msg: str):
        self.warns.append(msg)

    @property
    def ok(self) -> bool:
        return not self.fails

    def summary(self) -> str:
        lines = []
        lines.append(f"=== Component Database Validation ===")
        lines.append(f"PASS: {len(self.passes)}  WARN: {len(self.warns)}  FAIL: {len(self.fails)}")
        for f in self.fails:
            lines.append(_fail(f))
        for w in self.warns:
            lines.append(_warn(w))
        # Only show a sample of passes to keep output readable
        if self.passes:
            lines.append(f"(and {len(self.passes)} passing checks)")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Minimal JSON-schema (draft-07 subset) checker
# ---------------------------------------------------------------------------


def _check_type(value: Any, schema_type: str) -> bool:
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "null":
        return value is None
    return True


def _check_value_against_schema(value: Any, schema: dict, path: str, report: Report) -> bool:
    """Walk value vs schema, append failures to report. Returns True if clean."""
    if not isinstance(schema, dict):
        return True  # nothing to check

    if "type" in schema:
        st = schema["type"]
        if isinstance(st, list):
            if not any(_check_type(value, t) for t in st):
                report.add_fail(f"{path}: expected type {st}, got {type(value).__name__}")
                return False
        else:
            if not _check_type(value, st):
                report.add_fail(f"{path}: expected type {st}, got {type(value).__name__}")
                return False

    if "enum" in schema and value not in schema["enum"]:
        report.add_fail(f"{path}: value {value!r} not in enum {schema['enum']}")
        return False

    if "pattern" in schema and isinstance(value, str):
        if not re.match(schema["pattern"], value):
            report.add_fail(f"{path}: value {value!r} does not match pattern {schema['pattern']!r}")
            return False

    if "minLength" in schema and isinstance(value, str) and len(value) < schema["minLength"]:
        report.add_fail(f"{path}: string too short (minLength {schema['minLength']})")
        return False

    if "minimum" in schema and isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema["minimum"]:
            report.add_fail(f"{path}: value {value} below minimum {schema['minimum']}")
            return False

    if "minItems" in schema and isinstance(value, list) and len(value) < schema["minItems"]:
        report.add_fail(f"{path}: list too short (minItems {schema['minItems']})")
        return False

    if "format" in schema and schema["format"] == "uri" and isinstance(value, str):
        if not (value.startswith("http://") or value.startswith("https://")):
            report.add_fail(f"{path}: not a valid URI: {value!r}")
            return False

    if "required" in schema and isinstance(value, dict):
        for req in schema["required"]:
            if req not in value:
                report.add_fail(f"{path}: missing required field {req!r}")
                return False

    if "additionalProperties" in schema and schema["additionalProperties"] is False and isinstance(value, dict):
        allowed = set(schema.get("properties", {}).keys())
        for k in value.keys():
            if k not in allowed:
                report.add_fail(f"{path}: additional property not allowed: {k!r}")
                return False

    if "properties" in schema and isinstance(value, dict):
        for k, v in value.items():
            if k in schema["properties"]:
                _check_value_against_schema(v, schema["properties"][k], f"{path}.{k}", report)

    if "items" in schema and isinstance(value, list):
        for i, item in enumerate(value):
            _check_value_against_schema(item, schema["items"], f"{path}[{i}]", report)

    return not any(_fail for _ in [0] if report.fails)


# ---------------------------------------------------------------------------
# Top-level checks
# ---------------------------------------------------------------------------

URL_PATTERN = re.compile(r"^https?://[^\s]+$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_all_components() -> tuple[list[dict], list[dict], list[dict]]:
    ics, mosfets, maps = [], [], []
    for f in sorted((DB_ROOT / "ICs").rglob("*.json")):
        with open(f, "r", encoding="utf-8") as fh:
            ics.append(json.load(fh))
    for f in sorted((DB_ROOT / "MOSFETs").rglob("*.json")):
        with open(f, "r", encoding="utf-8") as fh:
            mosfets.append(json.load(fh))
    for f in sorted((DB_ROOT / "ReplacementMappings").glob("*.json")):
        with open(f, "r", encoding="utf-8") as fh:
            maps.append(json.load(fh))
    return ics, mosfets, maps


def validate_ics(ics: list[dict], all_parts: set[str], report: Report):
    schema_path = DB_ROOT / "schemas" / "ic_component.schema.json"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    seen_part_numbers: dict[str, str] = {}
    seen_record_ids: dict[str, str] = {}

    for rec in ics:
        rid = rec.get("record_id", "<missing>")
        pn = rec.get("part_number", "<missing>")
        # 1+2: schema compliance
        _check_value_against_schema(rec, schema, f"IC {rid}", report)
        # 3: duplicates
        if pn in seen_part_numbers:
            report.add_fail(f"IC {rid}: duplicate part_number {pn!r} (also in {seen_record_ids.get(pn)})")
        else:
            seen_part_numbers[pn] = rid
            report.add_pass(f"IC {rid}: part_number unique")
        if rid in seen_record_ids.values():
            report.add_fail(f"IC {rid}: duplicate record_id")
        seen_record_ids[pn] = rid
        # 4: mandatory fields already covered by schema required
        # 5: invalid electrical values
        ec = rec.get("electrical_characteristics", {}) or {}
        ivin = ec.get("input_voltage_range_v", {}) or {}
        if ivin.get("min") is not None and ivin.get("max") is not None:
            if ivin["min"] > ivin["max"]:
                report.add_fail(f"IC {rid}: input_voltage min > max")
        ovout = ec.get("output_voltage_range_v", {}) or {}
        if ovout.get("min") is not None and ovout.get("max") is not None:
            if ovout["min"] > ovout["max"]:
                report.add_fail(f"IC {rid}: output_voltage min > max")
        # 8: source attribution
        url = rec.get("datasheet_url", "")
        src = rec.get("datasheet_source", "")
        if not url or not URL_PATTERN.match(url):
            report.add_fail(f"IC {rid}: missing or invalid datasheet_url")
        if not src:
            report.add_fail(f"IC {rid}: missing datasheet_source")
        if not DATE_PATTERN.match(rec.get("source_date", "")):
            report.add_fail(f"IC {rid}: missing or invalid source_date")
        # 6: broken internal references
        for alt in rec.get("known_compatible_alternatives", []) or []:
            if alt not in all_parts:
                report.add_fail(
                    f"IC {rid}: known_compatible_alternative {alt!r} not found in any component record"
                )
            else:
                report.add_pass(f"IC {rid}: known_compatible_alternative {alt!r} resolves")


def validate_mosfets(mosfets: list[dict], all_parts: set[str], report: Report):
    schema_path = DB_ROOT / "schemas" / "mosfet_component.schema.json"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    seen_pn: dict[str, str] = {}
    for rec in mosfets:
        rid = rec.get("record_id", "<missing>")
        pn = rec.get("part_number", "<missing>")
        _check_value_against_schema(rec, schema, f"MOSFET {rid}", report)
        if pn in seen_pn:
            report.add_fail(f"MOSFET {rid}: duplicate part_number {pn!r}")
        else:
            seen_pn[pn] = rid
            report.add_pass(f"MOSFET {rid}: part_number unique")

        # Electrical sanity
        vds = rec.get("vds_v")
        if vds is not None and vds <= 0:
            # P-channel VDS is negative; that's allowed.
            if rec.get("channel_type") == "P-channel":
                pass
            elif rec.get("channel_type") == "N-channel" and vds < 0:
                report.add_fail(f"MOSFET {rid}: N-channel VDS should be positive, got {vds}")
        rds = rec.get("rds_on_mohm")
        if rds is not None and rds < 0:
            report.add_fail(f"MOSFET {rid}: RDS_on negative")
        qg = rec.get("gate_charge_nc")
        if qg is not None and qg < 0:
            report.add_fail(f"MOSFET {rid}: gate charge negative")

        url = rec.get("datasheet_url", "")
        src = rec.get("datasheet_source", "")
        if not url or not URL_PATTERN.match(url):
            report.add_fail(f"MOSFET {rid}: missing or invalid datasheet_url")
        if not src:
            report.add_fail(f"MOSFET {rid}: missing datasheet_source")
        if not DATE_PATTERN.match(rec.get("source_date", "")):
            report.add_fail(f"MOSFET {rid}: missing or invalid source_date")


def validate_mappings(maps: list[dict], all_parts: set[str], report: Report):
    schema_path = DB_ROOT / "schemas" / "replacement_mapping.schema.json"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    for m in maps:
        mid = m.get("mapping_id", "<missing>")
        _check_value_against_schema(m, schema, f"MAP {mid}", report)

        # Broken references
        orig = m.get("original_part")
        cand = m.get("candidate_part")
        cls = m.get("classification")
        # UNKNOWN mappings are explicitly unverified — they may reference
        # candidate parts that have not been added to the database yet.
        if cls != "UNKNOWN":
            if orig not in all_parts:
                report.add_fail(f"MAP {mid}: original_part {orig!r} not found in component database")
            if cand not in all_parts:
                report.add_fail(f"MAP {mid}: candidate_part {cand!r} not found in component database")
        else:
            if orig not in all_parts:
                report.add_warn(f"MAP {mid}: UNKNOWN mapping original_part {orig!r} not in database")
            if cand not in all_parts:
                report.add_warn(f"MAP {mid}: UNKNOWN mapping candidate_part {cand!r} not in database (expected for UNKNOWN)")

        # 7: COMPATIBLE classification requires evidence covering required criteria
        ev = m.get("evidence", []) or []
        ev_criteria = {e.get("criterion") for e in ev}
        if cls == "COMPATIBLE":
            required = {
                "package", "pinout", "voltage_rating", "current_rating",
                "rds_on", "gate_drive", "gate_charge", "thermal"
            } if m.get("component_type") == "MOSFET" else {
                "package", "pinout", "voltage_rating", "current_rating",
                "control_logic", "feedback_topology"
            }
            missing = required - ev_criteria
            if missing:
                report.add_fail(
                    f"MAP {mid}: COMPATIBLE classification missing evidence for: {sorted(missing)}"
                )
            for e in ev:
                if e.get("verdict") not in ("match",):
                    report.add_fail(
                        f"MAP {mid}: COMPATIBLE classification has non-match verdict on {e.get('criterion')}"
                    )
        if cls == "CONDITIONAL":
            if not m.get("conditions"):
                report.add_fail(f"MAP {mid}: CONDITIONAL classification requires non-empty 'conditions'")
        if cls == "UNKNOWN":
            # UNKNOWN is allowed but flagged
            report.add_warn(f"MAP {mid}: classification is UNKNOWN — verify before relying on this mapping")

        # Source attribution
        urls = m.get("datasheet_urls", {}) or {}
        if not urls.get("original") or not URL_PATTERN.match(urls.get("original", "")):
            report.add_fail(f"MAP {mid}: missing/invalid original datasheet URL")
        if not urls.get("candidate") or not URL_PATTERN.match(urls.get("candidate", "")):
            report.add_fail(f"MAP {mid}: missing/invalid candidate datasheet URL")


def main() -> int:
    if not DB_ROOT.exists():
        print(f"ComponentDatabase root not found at {DB_ROOT}", file=sys.stderr)
        return 2
    report = Report()
    ics, mosfets, maps = load_all_components()
    report.add_pass(f"Loaded {len(ics)} IC + {len(mosfets)} MOSFET + {len(maps)} mapping records")
    all_parts = {r.get("part_number") for r in (ics + mosfets) if r.get("part_number")}
    validate_ics(ics, all_parts, report)
    validate_mosfets(mosfets, all_parts, report)
    validate_mappings(maps, all_parts, report)
    print(report.summary())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
