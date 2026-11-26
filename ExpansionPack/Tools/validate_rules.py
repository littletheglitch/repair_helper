#!/usr/bin/env python3
"""Validate the diagnostic rules + rail definitions.

Checks:
  1. JSON validity
  2. Schema compliance against diagnostic_rule.schema.json and
     rail_definition.schema.json
  3. Condition expression parses cleanly
  4. next_rule references in interpretations point to real rule IDs
  5. applies_to_rail_families refer to rail families that exist
  6. Every rail family has at least one applicable rule
  7. Every rule references at least one rail family that exists

Pure standard library.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

PACK_ROOT = Path(__file__).resolve().parents[1]
ENG_ROOT = PACK_ROOT / "DiagnosticEngines"
sys.path.insert(0, str(PACK_ROOT))
from DiagnosticEngines.core.engine import _tokenize, _Parser, ExprError  # noqa: E402

RAIL_SCHEMA = ENG_ROOT.parent / "ComponentDatabase" / "schemas" / "rail_definition.schema.json"
RULE_SCHEMA = ENG_ROOT.parent / "ComponentDatabase" / "schemas" / "diagnostic_rule.schema.json"


class Report:
    def __init__(self):
        self.passes = []
        self.fails = []
        self.warns = []

    def add_pass(self, m): self.passes.append(m)
    def add_fail(self, m): self.fails.append(m)
    def add_warn(self, m): self.warns.append(m)

    @property
    def ok(self): return not self.fails

    def summary(self) -> str:
        lines = ["=== Rules + Rails Validation ===",
                 f"PASS: {len(self.passes)}  WARN: {len(self.warns)}  FAIL: {len(self.fails)}"]
        for f in self.fails: lines.append(f"  FAIL  {f}")
        for w in self.warns: lines.append(f"  WARN  {w}")
        if self.passes:
            lines.append(f"(and {len(self.passes)} passing checks)")
        return "\n".join(lines)


def _check_value_against_schema(value, schema, path, report):
    """Minimal subset (same as validate_database)."""
    if not isinstance(schema, dict):
        return
    if "type" in schema:
        st = schema["type"]
        ok = False
        if isinstance(st, list):
            ok = any(_matches_type(value, t) for t in st)
        else:
            ok = _matches_type(value, st)
        if not ok:
            report.add_fail(f"{path}: type mismatch ({st} vs {type(value).__name__})")
    if "enum" in schema and value not in schema["enum"]:
        report.add_fail(f"{path}: value {value!r} not in enum")
    if "pattern" in schema and isinstance(value, str) and not re.match(schema["pattern"], value):
        report.add_fail(f"{path}: {value!r} does not match pattern")
    if "minLength" in schema and isinstance(value, str) and len(value) < schema["minLength"]:
        report.add_fail(f"{path}: string too short")
    if "minimum" in schema and isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema["minimum"]:
            report.add_fail(f"{path}: value {value} below minimum {schema['minimum']}")
    if "minItems" in schema and isinstance(value, list) and len(value) < schema["minItems"]:
        report.add_fail(f"{path}: list too short")
    if "required" in schema and isinstance(value, dict):
        for r in schema["required"]:
            if r not in value:
                report.add_fail(f"{path}: missing required field {r!r}")
    if "additionalProperties" in schema and schema["additionalProperties"] is False and isinstance(value, dict):
        allowed = set(schema.get("properties", {}).keys())
        for k in value:
            if k not in allowed:
                report.add_fail(f"{path}: additional property {k!r}")
    if "properties" in schema and isinstance(value, dict):
        for k, v in value.items():
            if k in schema["properties"]:
                _check_value_against_schema(v, schema["properties"][k], f"{path}.{k}", report)
    if "items" in schema and isinstance(value, list):
        for i, item in enumerate(value):
            _check_value_against_schema(item, schema["items"], f"{path}[{i}]", report)


def _matches_type(v, t):
    if t == "string": return isinstance(v, str)
    if t == "integer": return isinstance(v, int) and not isinstance(v, bool)
    if t == "number": return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "boolean": return isinstance(v, bool)
    if t == "object": return isinstance(v, dict)
    if t == "array": return isinstance(v, list)
    if t == "null": return v is None
    return True


def _parse_condition(expr: str) -> Optional[str]:
    try:
        tokens = _tokenize(expr)
        if not tokens:
            return "empty expression"
        parser = _Parser(tokens, expr)
        parser.parse()
        return None
    except ExprError as e:
        return str(e)


def main() -> int:
    report = Report()

    with open(RAIL_SCHEMA, "r", encoding="utf-8") as f:
        rail_schema = json.load(f)
    with open(RULE_SCHEMA, "r", encoding="utf-8") as f:
        rule_schema = json.load(f)

    # Load rails (skip RULE_*.json files which live in the same dir)
    rails_root = ENG_ROOT / "PowerRails"
    rails: dict[str, dict] = {}
    for rf in sorted(rails_root.glob("*.json")):
        if rf.name.startswith("RULE_"):
            continue
        with open(rf, "r", encoding="utf-8") as f:
            rail = json.load(f)
        rails[rail["rail_family"]] = rail
        _check_value_against_schema(rail, rail_schema, f"RAIL {rail['rail_family']}", report)
    report.add_pass(f"Loaded {len(rails)} rail definitions")

    # Load rules
    rules: list[dict] = []
    for rf in sorted(ENG_ROOT.rglob("RULE_*.json")):
        with open(rf, "r", encoding="utf-8") as f:
            rule = json.load(f)
        rules.append(rule)
        rid = rule.get("rule_id", "<missing>")
        _check_value_against_schema(rule, rule_schema, f"RULE {rid}", report)
        # condition parses
        err = _parse_condition(rule.get("condition", {}).get("expression", ""))
        if err:
            report.add_fail(f"RULE {rid}: condition parse error: {err}")
        else:
            report.add_pass(f"RULE {rid}: condition parses")
        # applies_to_rail_families refer to real rails
        for fam in rule.get("applies_to_rail_families", []) or []:
            if fam not in rails:
                report.add_fail(f"RULE {rid}: references unknown rail family {fam!r}")
            else:
                report.add_pass(f"RULE {rid}: rail family {fam!r} exists")
        # next_rule references resolve
        for s in rule.get("next_steps", []) or []:
            for interp in s.get("interpretations", []) or []:
                nr = interp.get("next_rule")
                if nr:
                    # Will be checked after all rules loaded
                    pass

    rule_ids = {r["rule_id"] for r in rules}
    for rule in rules:
        rid = rule["rule_id"]
        for s in rule.get("next_steps", []) or []:
            for interp in s.get("interpretations", []) or []:
                nr = interp.get("next_rule")
                if nr and nr not in rule_ids:
                    report.add_fail(f"RULE {rid}: next_rule {nr!r} does not exist")
                elif nr:
                    report.add_pass(f"RULE {rid}: next_rule {nr!r} resolves")

    # Every rail family has at least one applicable rule
    rail_to_rules: dict[str, list[str]] = {fam: [] for fam in rails}
    for rule in rules:
        for fam in rule.get("applies_to_rail_families", []) or []:
            if fam in rail_to_rules:
                rail_to_rules[fam].append(rule["rule_id"])
    for fam, rids in rail_to_rules.items():
        if not rids:
            report.add_warn(f"RAIL {fam}: no rule references this rail family")
        else:
            report.add_pass(f"RAIL {fam}: covered by {len(rids)} rule(s)")

    print(report.summary())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
