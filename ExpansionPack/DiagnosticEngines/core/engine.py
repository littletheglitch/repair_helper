"""Deterministic power-rail diagnostic engine.

Pure standard library. Loads:
  * Rail definitions from DiagnosticEngines/PowerRails/*.json
  * Diagnostic rules from DiagnosticEngines/<topology>/RULE_*.json
  * Component records from ComponentDatabase/ICs/**, ComponentDatabase/MOSFETs/**
  * Replacement mappings from ComponentDatabase/ReplacementMappings/*.json

The engine is deterministic: given the same input (rail family + topology +
identified controller + measured facts) it always produces the same output.
It does NOT call an LLM, and it does NOT require network access at runtime.

The engine NEVER invents pin numbers or expected voltages. When a rule needs
controller-specific data the engine returns `requires_info=True` with a note
that the controller datasheet is required.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

# Framework Measurement (state-aware). The power-rail engine accepts
# BOTH the legacy Measurement (name/value/measured) and the framework
# Measurement (name/kind/state/value/...). A normalizer maps the legacy
# form onto framework states so all downstream fact derivation is
# state-driven. (Phase 1 migration.)
from DiagnosticEngines.core.framework.measurement import (
    Measurement as FwMeasurement,
    MeasurementKind,
    MeasurementState,
    MeasurementCondition,
)


# ---------------------------------------------------------------------------
# Boolean expression evaluator over a fact namespace
# ---------------------------------------------------------------------------
#
# Grammar (right-to-left precedence, simplest practical form):
#   expr   := term (("AND" | "OR") term)*
#   term   := "NOT" term | factor
#   factor := "(" expr ")" | IDENT["=" ("True"|"False")]
#
# IDENT names follow: [a-z_][a-z0-9_]* and refer to facts in the measurement
# set. Each fact is True / False / None (None == not measured). The evaluator
# propagates None as 'unknown' (rule does not fire; engine surfaces
# requires_info=True).
#
# Operators AND/OR/NOT must be uppercase to avoid clashing with fact names.


_TOKEN_RE = re.compile(
    r"\s*(?P<LP>\()|(?P<RP>\))|(?P<AND>\bAND\b)|(?P<OR>\bOR\b)|(?P<NOT>\bNOT\b)|(?P<EQ>\=)|(?P<BOOL>\bTrue\b|\bFalse\b)|(?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)"
)


class ExprError(ValueError):
    pass


def _values_conflict(a: Any, b: Any) -> bool:
    """Return True if two measurement values of the same signal
    genuinely disagree.

    Booleans compare directly. Numerics compare by value (3.3 == 3.3).
    A None (not-measured) never conflicts with anything. Strings compare
    exactly. Two readings that round to the same value do not conflict.
    """
    if a is None or b is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) != bool(b)
    # Try numeric comparison
    try:
        return float(a) != float(b)
    except (TypeError, ValueError):
        return a != b


def _tokenize(s: str) -> list[tuple[str, str]]:
    tokens = []
    pos = 0
    while pos < len(s):
        m = _TOKEN_RE.match(s, pos)
        if not m:
            if s[pos].isspace():
                pos += 1
                continue
            raise ExprError(f"Unexpected character {s[pos]!r} at {pos} in {s!r}")
        pos = m.end()
        for kind in ("LP", "RP", "AND", "OR", "NOT", "EQ", "BOOL", "IDENT"):
            v = m.group(kind)
            if v is not None:
                tokens.append((kind, v))
                break
    return tokens


# Ternary truth table:
#   None == "unknown / not measured"
#   True / False == measured value
def _and(a, b):
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return True


def _or(a, b):
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return False


def _not(a):
    if a is None:
        return None
    return not a


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], source: str):
        self.tokens = tokens
        self.pos = 0
        self.source = source

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def next(self):
        t = self.peek()
        self.pos += 1
        return t

    def expect(self, kind: str):
        k, v = self.next()
        if k != kind:
            raise ExprError(f"Expected {kind}, got {k}={v!r} in {self.source!r}")
        return v

    def parse(self):
        node = self.parse_expr()
        if self.pos != len(self.tokens):
            raise ExprError(f"Trailing tokens in {self.source!r}")
        return node

    def parse_expr(self):
        left = self.parse_term()
        while self.peek()[0] in ("AND", "OR"):
            op = self.next()[0]
            right = self.parse_term()
            left = (op, left, right)
        return left

    def parse_term(self):
        k, v = self.peek()
        if k == "NOT":
            self.next()
            return ("NOT", self.parse_term())
        return self.parse_factor()

    def parse_factor(self):
        k, v = self.peek()
        if k == "LP":
            self.next()
            inner = self.parse_expr()
            self.expect("RP")
            return inner
        if k == "IDENT":
            self.next()
            # optional '= True' / '= False'
            if self.peek()[0] == "EQ":
                self.next()
                boolval = self.expect("BOOL")
                return ("LIT_BOOL", v, boolval == "True")
            return ("IDENT", v)
        raise ExprError(f"Unexpected token {k}={v!r} in {self.source!r}")


def _eval(node, facts: dict[str, Optional[bool]]) -> Optional[bool]:
    if node is None:
        return None
    kind = node[0]
    if kind == "IDENT":
        # Meta-facts (provenance, conflicts) are internal bookkeeping,
        # never part of a rule condition. A rule that references them
        # is malformed; treat as unknown rather than corrupting logic
        # with a dict/list value.
        if node[1].startswith("_"):
            return None
        if node[1] in facts:
            return facts[node[1]]
        # Semantic default: a fact whose name ends in '_measured' is False
        # when absent (it simply was not measured). Any other unknown fact
        # is None (unknown) so the engine surfaces 'requires more info'.
        if node[1].endswith("_measured"):
            return False
        return None
    if kind == "LIT_BOOL":
        return node[2]
    if kind == "NOT":
        return _not(_eval(node[1], facts))
    if kind == "AND":
        return _and(_eval(node[1], facts), _eval(node[2], facts))
    if kind == "OR":
        return _or(_eval(node[1], facts), _eval(node[2], facts))
    raise ExprError(f"Unknown node kind {kind!r}")


def evaluate_condition(expression: str, facts: dict[str, Optional[bool]]) -> Optional[bool]:
    """Evaluate a boolean expression over a fact namespace.

    Returns True / False / None. None means "expression depends on an
    unmeasured fact" — the engine treats this as 'requires more info'.
    """
    tokens = _tokenize(expression)
    if not tokens:
        raise ExprError("Empty expression")
    parser = _Parser(tokens, expression)
    tree = parser.parse()
    return _eval(tree, facts)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Measurement:
    """A measurement supplied to the engine.

    Each measurement is a fact key + value. Boolean facts are True/False;
    numeric facts are floats (their presence is also tracked so a rule can
    say `NOT vin_measured`).
    """

    name: str
    value: Any  # True, False, float, or None
    measured: bool = True  # False for explicitly-asserted "not measured"
    unit: str = ""
    notes: str = ""

    def as_fact(self) -> Optional[bool]:
        """Return the boolean interpretation used by rule conditions.

        * Boolean values pass through.
        * Numeric values: presence is True; absence is False.
        * `_measured` / `_present` style keys are derived by the engine.
        """
        if isinstance(self.value, bool):
            return self.value
        if self.value is None:
            return None
        # numeric: truthiness means "value present and non-zero"
        try:
            return float(self.value) != 0.0
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Measurement normalization (Phase 1 migration)
# ---------------------------------------------------------------------------
#
# The power-rail engine historically consumed the legacy `Measurement`
# (name/value/measured), which conflated several distinct states:
#   - a genuine 0.0V reading (MEASURED_ZERO)
#   - an unmeasured slot (NOT_MEASURED)
#   - an invalid/OL reading (INVALID / ABOVE_RANGE)
# were all represented as value=None or value=0.0 with no way to tell
# them apart. This made it impossible to distinguish "the rail reads
# 0V because VIN is absent" from "the rail reads 0V but VIN is present
# (a real fault)" or "the meter showed OL".
#
# The framework `Measurement` carries an explicit `state` enum that
# resolves this. The normalizer below accepts EITHER form and produces
# a framework Measurement, so all downstream fact derivation is
# state-driven. Legacy payloads keep working unchanged.


def _coerce_measurement_state(state: Any) -> MeasurementState:
    """Coerce a string/enum to MeasurementState, defaulting to UNKNOWN."""
    if isinstance(state, MeasurementState):
        return state
    if isinstance(state, str):
        try:
            return MeasurementState(state)
        except ValueError:
            return MeasurementState.UNKNOWN
    return MeasurementState.UNKNOWN


def _coerce_measurement_kind(kind: Any) -> MeasurementKind:
    if isinstance(kind, MeasurementKind):
        return kind
    if isinstance(kind, str):
        try:
            return MeasurementKind(kind)
        except ValueError:
            return MeasurementKind.CUSTOM
    return MeasurementKind.CUSTOM


def _normalize_measurement(m: Any) -> FwMeasurement:
    """Map a legacy or framework Measurement onto a framework Measurement.

    Legacy form (name/value/measured/unit/notes):
      - measured=False                      -> NOT_MEASURED, value=None
      - measured=True, value is None        -> INVALID (taken but unusable)
      - measured=True, value is bool        -> IN_RANGE (logic level)
      - measured=True, value == 0/0.0       -> MEASURED_ZERO
      - measured=True, value is numeric     -> IN_RANGE
      - measured=True, value == "OL"/"ol"   -> ABOVE_RANGE (overload)
      - measured=True, value is other str   -> try float, else INVALID

    Framework form: pass through (with str->enum coercion).
    """
    if isinstance(m, FwMeasurement):
        return m
    if isinstance(m, Measurement):
        if not m.measured:
            return FwMeasurement(
                name=m.name, kind=MeasurementKind.CUSTOM,
                state=MeasurementState.NOT_MEASURED, value=None,
                unit=m.unit, notes=m.notes,
            )
        v = m.value
        # Explicit overload string
        if isinstance(v, str) and v.strip().upper() in ("OL", "O.L", "O/L", "OVERLOAD"):
            return FwMeasurement(
                name=m.name, kind=MeasurementKind.CUSTOM,
                state=MeasurementState.ABOVE_RANGE, value=None,
                unit=m.unit, notes=m.notes or "Overload (OL) reading",
            )
        if v is None:
            # measured=True but no value -> invalid reading
            return FwMeasurement(
                name=m.name, kind=MeasurementKind.CUSTOM,
                state=MeasurementState.INVALID, value=None,
                unit=m.unit, notes=m.notes or "Measured but value missing",
            )
        if isinstance(v, bool):
            return FwMeasurement(
                name=m.name, kind=MeasurementKind.LOGIC_LEVEL,
                state=MeasurementState.IN_RANGE, value=v,
                unit=m.unit, notes=m.notes,
            )
        if isinstance(v, (int, float)):
            if float(v) == 0.0:
                return FwMeasurement(
                    name=m.name, kind=MeasurementKind.CUSTOM,
                    state=MeasurementState.MEASURED_ZERO, value=0.0,
                    unit=m.unit, notes=m.notes,
                )
            return FwMeasurement(
                name=m.name, kind=MeasurementKind.CUSTOM,
                state=MeasurementState.IN_RANGE, value=float(v),
                unit=m.unit, notes=m.notes,
            )
        if isinstance(v, str):
            f = DiagnosticEngine._numeric(v)
            if f is not None:
                if f == 0.0:
                    return FwMeasurement(
                        name=m.name, kind=MeasurementKind.CUSTOM,
                        state=MeasurementState.MEASURED_ZERO, value=0.0,
                        unit=m.unit, notes=m.notes)
                return FwMeasurement(
                    name=m.name, kind=MeasurementKind.CUSTOM,
                    state=MeasurementState.IN_RANGE, value=f,
                    unit=m.unit, notes=m.notes)
            # non-numeric string that isn't OL -> invalid
            return FwMeasurement(
                name=m.name, kind=MeasurementKind.CUSTOM,
                state=MeasurementState.INVALID, value=None,
                unit=m.unit, notes=m.notes or f"Unusable value {v!r}",
            )
        # unknown type
        return FwMeasurement(
            name=m.name, kind=MeasurementKind.CUSTOM,
            state=MeasurementState.INVALID, value=None,
            unit=m.unit, notes=m.notes,
        )
    if isinstance(m, dict):
        # dict payload (e.g. from host adapter) -> build framework Measurement
        return FwMeasurement(
            name=m["name"],
            kind=_coerce_measurement_kind(m.get("kind", "custom")),
            state=_coerce_measurement_state(m.get("state", "not_measured")),
            value=m.get("value"),
            unit=m.get("unit", ""),
            notes=m.get("notes", ""),
        )
    raise TypeError(f"Cannot normalize measurement of type {type(m)!r}")


def _normalize_measurements(measurements: list) -> list[FwMeasurement]:
    """Normalize a mixed list of legacy/framework/dict measurements."""
    return [_normalize_measurement(m) for m in (measurements or [])]


@dataclass
class NextStep:
    step: int
    action: str
    measure: Optional[dict] = None
    interpretations: list[dict] = field(default_factory=list)
    requires_info: bool = False
    requires_info_note: str = ""


@dataclass
class DiagnosisResult:
    rail_family: str
    topology: str
    controller_part: Optional[str]
    fired_rule_id: Optional[str]
    next_steps: list[NextStep]
    requires_info: bool
    requires_info_note: str
    facts_evaluated: dict[str, Optional[bool]]
    candidate_rules: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Rule selection + engine
# ---------------------------------------------------------------------------


class DiagnosticEngine:
    """Loads all rules + rails and produces deterministic diagnoses.

    The engine NEVER assumes a controller from the rail name. The caller
    must supply the identified controller (if known) when running a
    diagnosis; the engine uses it to enrich the result (datasheet URL,
    pinout, replacement candidates) but never to short-circuit the
    measurement-driven workflow.
    """

    def __init__(self, pack_root: Path):
        self.pack_root = pack_root
        self.rules: list[dict] = []
        self.rails: dict[str, dict] = {}
        self._load_rules()
        self._load_rails()

    # --- loading ----------------------------------------------------------

    def _load_rules(self):
        engines_root = self.pack_root / "DiagnosticEngines"
        for rule_file in sorted(engines_root.rglob("RULE_*.json")):
            with open(rule_file, "r", encoding="utf-8") as f:
                self.rules.append(json.load(f))

    def _load_rails(self):
        rails_root = self.pack_root / "DiagnosticEngines" / "PowerRails"
        for rail_file in sorted(rails_root.glob("*.json")):
            if rail_file.name.startswith("RULE_"):
                continue
            with open(rail_file, "r", encoding="utf-8") as f:
                rail = json.load(f)
            self.rails[rail["rail_family"]] = rail

    # --- fact derivation --------------------------------------------------

    @staticmethod
    def _facts_from_measurements(
        measurements: list[Measurement], rail: Optional[dict]
    ) -> dict[str, Optional[bool]]:
        """Build the fact namespace the rule expressions evaluate against.

        Accepts BOTH the legacy `Measurement` (name/value/measured) and
        the framework `Measurement` (name/kind/state/value/...). All
        inputs are normalized to framework form first, so fact derivation
        is state-driven (Phase 1 migration).

        For each measurement the engine derives:
          * `<name>_present`   — True if a non-zero value was measured
                                 (IN_RANGE with non-zero value, or a
                                 True logic level). False for
                                 MEASURED_ZERO (0V is absent voltage).
                                 None for NOT_MEASURED / INVALID.
          * `<name>_measured`  — True if a real reading was taken (any
                                 state except NOT_MEASURED /
                                 NOT_APPLICABLE), including ZERO,
                                 INVALID, and OVERLOAD. None for
                                 NOT_MEASURED.
          * `<name>_zero`      — True ONLY for MEASURED_ZERO (a genuine
                                 0.0 reading). Distinguishes "reads 0V"
                                 from "not measured" and "invalid".
          * `<name>_invalid`   — True for INVALID (taken but unusable).
          * `<name>_overload`  — True for ABOVE_RANGE (meter OL).
          * `<name>_out_of_range` — True for BELOW_RANGE or ABOVE_RANGE.
          * `<name>`           — the raw boolean fact (backward-compat:
                                 same as `_present`).

        For the rail's output voltage measurement the engine ALSO derives
        numeric-range facts using the rail's `typical_voltage_v` tolerance:
          * `rail_voltage_low`         — True if value < min (and non-zero)
          * `rail_voltage_high`        — True if value > max
          * `rail_voltage_in_tolerance`— True if min <= value <= max

        A measurement that was not taken NEVER produces a derived
        numeric-range fact — the fact is None, so rules depending on it
        surface requires_info=True. A zero-volt reading is NOT 'low' —
        it is 'absent' (present=False, zero=True). An INVALID or
        OVERLOAD reading produces no numeric-range facts either.
        """
        # Normalize all inputs to framework form (Phase 1).
        fms = _normalize_measurements(measurements)
        facts: dict[str, Optional[bool]] = {}
        provenance: dict[str, str] = {}
        conflicts: list[str] = []
        invalid_notes: list[str] = []

        # Extract the rail's voltage tolerance, if available.
        v_tol = (rail or {}).get("typical_voltage_v") or {}
        v_min = v_tol.get("min")
        v_max = v_tol.get("max")

        # NOTE: rail_short is NOT defaulted. An unmeasured resistance
        # cannot prove a rail is not shorted. rail_short stays None
        # (unknown) until the technician measures it.

        # Track which signal names we've already seen, to detect
        # conflicting duplicate measurements.
        seen_values: dict[str, Any] = {}

        for m in fms:
            name = m.name
            state = m.state
            is_measured = state not in (
                MeasurementState.NOT_MEASURED,
                MeasurementState.NOT_APPLICABLE,
            )
            is_zero = (state == MeasurementState.MEASURED_ZERO)
            is_invalid = (state == MeasurementState.INVALID)
            is_overload = (state == MeasurementState.ABOVE_RANGE)
            is_below = (state == MeasurementState.BELOW_RANGE)
            is_out_of_range = is_overload or is_below
            is_in_range = (state == MeasurementState.IN_RANGE)

            # _present: True only for a non-zero in-range reading or a
            # True logic level. MEASURED_ZERO -> False (0V is absent).
            # INVALID / NOT_MEASURED -> None.
            if is_measured and not is_invalid:
                if is_zero:
                    present = False
                elif m.value is True:
                    present = True
                elif isinstance(m.value, bool):
                    present = m.value
                elif isinstance(m.value, (int, float)):
                    present = float(m.value) != 0.0
                else:
                    # OVERLOAD / BELOW_RANGE / non-numeric: reading taken
                    # but not a normal value -> present unknown (None).
                    present = None
            elif is_invalid:
                present = None
            else:
                present = None  # NOT_MEASURED

            # --- conflict detection ---
            if name in seen_values and is_measured:
                prev = seen_values[name]
                prev_was_zero = (prev == "_zero_marker_")
                cur_is_zero = is_zero
                # Conflict if both measured and disagree (zero vs non-zero
                # is a disagreement; two different non-zero values too).
                disagree = False
                if prev_was_zero and not cur_is_zero:
                    disagree = True
                elif not prev_was_zero and cur_is_zero:
                    disagree = True
                elif not prev_was_zero and not cur_is_zero:
                    if _values_conflict(prev, m.value):
                        disagree = True
                if disagree:
                    conflicts.append(
                        f"Conflicting measurements for '{name}': "
                        f"{prev!r} vs {m.value!r} (state={state.value}). "
                        f"Re-measure to resolve."
                    )
                    for key in (name, f"{name}_measured", f"{name}_present",
                                f"{name}_zero", f"{name}_invalid",
                                f"{name}_overload", f"{name}_out_of_range"):
                        facts[key] = None
                        provenance[key] = "conflicting"
                    continue
            if is_measured:
                seen_values[name] = (
                    "_zero_marker_" if is_zero else m.value
                )

            # Derive the fact namespace for this measurement.
            facts[f"{name}_present"] = present
            facts[f"{name}_measured"] = True if is_measured else None
            facts[f"{name}_zero"] = True if is_zero else (
                False if is_measured and not is_invalid else None
            )
            facts[f"{name}_invalid"] = True if is_invalid else (
                False if is_measured else None
            )
            facts[f"{name}_overload"] = True if is_overload else (
                False if is_measured and not is_invalid else None
            )
            facts[f"{name}_out_of_range"] = True if is_out_of_range else (
                False if is_measured and not is_invalid else None
            )
            # backward-compat: raw name == present
            facts[name] = present
            if is_measured:
                prov = "invalid" if is_invalid else "confirmed"
                for suf in ("", "_present", "_measured", "_zero",
                            "_invalid", "_overload", "_out_of_range"):
                    provenance[name + suf] = prov
            if is_invalid:
                invalid_notes.append(
                    f"Measurement '{name}' is INVALID"
                    + (f" ({m.notes})" if m.notes else "")
                    + " — re-measure before relying on it."
                )

            # --- numeric-range facts for the rail output voltage ---
            # Only derive for IN_RANGE voltage readings (not ZERO,
            # INVALID, or OVERLOAD) when we have a tolerance.
            is_rail_voltage = name in ("rail_voltage", "vout",
                                       "output_voltage") \
                or (rail is not None and name == rail.get("rail_family", ""))
            if is_rail_voltage and is_in_range and m.value is not None:
                val = DiagnosticEngine._numeric(m.value)
                if val is not None and val != 0.0 and v_min is not None and v_max is not None:
                    facts["rail_voltage_low"] = val < v_min
                    facts["rail_voltage_high"] = val > v_max
                    facts["rail_voltage_in_tolerance"] = (v_min <= val <= v_max)
                    provenance["rail_voltage_low"] = "derived"
                    provenance["rail_voltage_high"] = "derived"
                    provenance["rail_voltage_in_tolerance"] = "derived"

            # --- instability fact ---
            if name == "rail_voltage_stable" and is_measured and not is_invalid:
                stable = present
                facts["rail_voltage_unstable"] = (not stable) if stable is not None else None
                provenance["rail_voltage_unstable"] = "derived"

            # --- ripple fact ---
            if name == "ripple" and is_in_range and m.value is not None:
                ripple_limit = (rail or {}).get("ripple_limit_mv")
                if ripple_limit is None:
                    ripple_limit = 50.0  # conservative default mV
                val = DiagnosticEngine._numeric(m.value)
                if val is not None:
                    facts["ripple_excessive"] = val > ripple_limit
                    provenance["ripple_excessive"] = "derived"

            # --- rail_resistance_low fact ---
            # SUSPECTED, not confirmed. An OVERLOAD (OL) resistance
            # reading means VERY high resistance (open) -> not low.
            if name == "rail_resistance" and is_in_range and m.value is not None:
                val = DiagnosticEngine._numeric(m.value)
                if val is not None:
                    facts["rail_resistance_low"] = val < 10.0
                    provenance["rail_resistance_low"] = "suspected"
            elif name == "rail_resistance" and is_overload:
                # OL on resistance = open circuit = very high R = not low
                facts["rail_resistance_low"] = False
                provenance["rail_resistance_low"] = "confirmed"

        # --- no_confirmed_short derived fact ---
        # True when rail_short is False OR unmeasured (unknown). False
        # ONLY when rail_short was measured True. This lets the chain
        # proceed without falsely asserting a short was ruled out; the
        # conclusion notes a short was not ruled out when unmeasured.
        rail_short_val = facts.get("rail_short")  # None / False / True
        facts["no_confirmed_short"] = (rail_short_val is not True)
        provenance["no_confirmed_short"] = "derived"

        # Expose provenance, conflicts, and invalid-reading notes.
        facts["_provenance"] = provenance
        facts["_conflicts"] = conflicts
        facts["_invalid_readings"] = invalid_notes
        return facts

    @staticmethod
    def _numeric(value: Any) -> Optional[float]:
        """Coerce a measurement value to float, or None if not numeric."""
        if value is None:
            return None
        if isinstance(value, bool):
            return None  # booleans are not voltages
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # --- diagnosis --------------------------------------------------------

    def diagnose(
        self,
        rail_family: str,
        measurements: list[Measurement],
        controller_part: Optional[str] = None,
        topology: Optional[str] = None,
        asserted_facts: Optional[dict[str, Optional[bool]]] = None,
    ) -> DiagnosisResult:
        """Run the diagnostic workflow.

        The engine selects candidate rules by rail family and (if given)
        topology, evaluates each rule's condition, and returns the first
        rule whose condition is True. If no rule fires but a candidate
        references unmeasured facts, the engine returns requires_info=True.
        """
        rail = self.rails.get(rail_family)
        if rail is None:
            return DiagnosisResult(
                rail_family=rail_family,
                topology=topology or "unknown",
                controller_part=controller_part,
                fired_rule_id=None,
                next_steps=[],
                requires_info=True,
                requires_info_note=f"Rail family {rail_family!r} not found in the database. Available: {sorted(self.rails)}",
                facts_evaluated={},
                notes=[],
            )

        rail_topology = topology or rail.get("typical_topology", "")
        facts = self._facts_from_measurements(measurements, rail)

        # Merge caller-asserted facts (set by a previous rule's
        # interpretation in a multi-step workflow). These let follow-on
        # rules fire without the technician re-measuring.
        if asserted_facts:
            for k, v in asserted_facts.items():
                facts[k] = v

        # select candidate rules: a rule applies to this case when the rail
        # family is listed in its `applies_to_rail_families`. We do NOT
        # additionally filter by topology, because some rules (e.g. shorted
        # rail) are valid across multiple topologies and are reused.
        # Rules marked "follow_on": true are EXCLUDED from the standalone
        # candidate set — they are only reachable via next_rule from
        # another rule, because their condition depends on an internal
        # asserted fact (e.g. 'overload') that is never measured directly
        # by the technician. They ARE included when the caller passes the
        # asserted fact via `asserted_facts` (multi-step workflow).
        candidates: list[dict] = []
        for rule in self.rules:
            if rail_family not in rule.get("applies_to_rail_families", []):
                continue
            if rule.get("follow_on", False):
                # Include only if the caller asserted at least one fact
                # this rule's condition depends on. We approximate by
                # checking if any asserted_facts key appears in the
                # condition expression.
                if not asserted_facts:
                    continue
                cond_expr = rule.get("condition", {}).get("expression", "")
                if not any(fn in cond_expr for fn in asserted_facts):
                    continue
            candidates.append(rule)

        if not candidates:
            return DiagnosisResult(
                rail_family=rail_family,
                topology=rail_topology,
                controller_part=controller_part,
                fired_rule_id=None,
                next_steps=[],
                requires_info=True,
                requires_info_note=f"No diagnostic rules found for rail_family={rail_family!r} topology={rail_topology!r}. This combination is not covered by the rule database.",
                facts_evaluated=facts,
                notes=[],
            )

        fired: Optional[dict] = None
        any_unmeasured: bool = False
        unmeasured_rule_ids: list[str] = []

        # Stable order: by priority (lower fires first), then rule_id.
        # Priority lets critical/numeric rules preempt the linear chain:
        #   10 = safety/numeric faults (overvoltage, ripple, unstable)
        #   20 = measured-absent fault conclusions (VIN/VCC/EN/switching absent)
        #   30 = healthy conclusion
        #   50 = the linear measurement chain (default; backward-compat)
        #   70 = follow-on rules (overload identify)
        # Rules without an explicit priority default to 50.
        for rule in sorted(candidates, key=lambda r: (r.get("priority", 50), r["rule_id"])):
            try:
                result = evaluate_condition(rule["condition"]["expression"], facts)
            except ExprError as e:
                return DiagnosisResult(
                    rail_family=rail_family,
                    topology=rail_topology,
                    controller_part=controller_part,
                    fired_rule_id=rule["rule_id"],
                    next_steps=[],
                    requires_info=True,
                    requires_info_note=f"Invalid condition in rule {rule['rule_id']!r}: {e}",
                    facts_evaluated=facts,
                    candidate_rules=[r["rule_id"] for r in candidates],
                )
            if result is True:
                fired = rule
                break
            if result is None:
                any_unmeasured = True
                unmeasured_rule_ids.append(rule["rule_id"])

        if fired is None:
            note = (
                f"No rule fired. The following candidate rules depend on "
                f"measurements that have not been supplied: {unmeasured_rule_ids}. "
                f"Supply those measurements to advance the workflow."
                if any_unmeasured
                else "No rule fired and no rule is waiting on missing measurements. "
                     "The measurement combination may indicate a healthy rail or a "
                     "scenario not covered by the rule database."
            )
            return DiagnosisResult(
                rail_family=rail_family,
                topology=rail_topology,
                controller_part=controller_part,
                fired_rule_id=None,
                next_steps=[],
                requires_info=any_unmeasured,
                requires_info_note=note,
                facts_evaluated=facts,
                candidate_rules=[r["rule_id"] for r in candidates],
            )

        # Build the result with the fired rule's next_steps.
        steps: list[NextStep] = []
        for s in fired["next_steps"]:
            steps.append(
                NextStep(
                    step=s["step"],
                    action=s["action"],
                    measure=s.get("measure"),
                    interpretations=s.get("interpretations", []),
                    requires_info=s.get("requires_info", False),
                    requires_info_note=s.get("requires_info_note", ""),
                )
            )

        # If the controller is known, attach component info via notes.
        notes: list[str] = []
        if controller_part:
            component = self._lookup_component(controller_part)
            if component is not None:
                notes.append(
                    f"Identified controller: {controller_part} "
                    f"({component.get('manufacturer', '?')}). "
                    f"Datasheet: {component.get('datasheet_url', 'N/A')}"
                )
                pinout_known = bool(component.get("pinout"))
                notes.append(
                    f"Controller pinout verified: {pinout_known}. "
                    f"Use the datasheet to map VIN/VCC/EN/FB/PGOOD pin numbers."
                )
            else:
                notes.append(
                    f"Controller {controller_part!r} not found in the component "
                    f"database. Use the manufacturer datasheet directly."
                )
        else:
            notes.append(
                "No controller part number supplied. The next step requires the "
                "controller identity; identify it from the board marking before "
                "continuing."
            )

        # --- three-state evidence notes ---
        # Surface the provenance of the fired rule's key facts, the
        # "short not ruled out" caveat, and any conflicting evidence.
        provenance = facts.get("_provenance", {})
        conflicts = facts.get("_conflicts", [])
        # Conflicting evidence takes precedence: if two readings of the
        # same signal disagree, the technician must re-measure before
        # any conclusion is trusted.
        if conflicts:
            notes.append("CONFLICTING_EVIDENCE: " + " | ".join(conflicts)
                         + " Re-measure the conflicting signal before acting on "
                         "this conclusion.")
        # --- invalid readings (Phase 1) ---
        # A measurement flagged INVALID (taken but unusable) must not be
        # silently dropped. Surface it so the technician re-measures.
        invalid_readings = facts.get("_invalid_readings", [])
        if invalid_readings:
            notes.append("INVALID_READINGS: " + " | ".join(invalid_readings)
                         + " Do not act on a conclusion that depends on an "
                         "invalid reading until it is re-measured.")
        # --- simultaneous faults (A0-4 fix) ---
        # The engine fires ONE rule (highest priority). But a real board
        # can have MULTIPLE independent measured-true faults (e.g. a
        # confirmed short AND an absent input). Scanning the other
        # candidate rules for any whose condition is ALSO True surfaces
        # secondary faults instead of silently dropping them. This does
        # NOT change which rule fires (priority is preserved); it only
        # adds a note so the technician knows about additional faults.
        secondary_faults: list[str] = []
        for rule in candidates:
            if rule["rule_id"] == fired["rule_id"]:
                continue
            try:
                res = evaluate_condition(rule["condition"]["expression"], facts)
            except ExprError:
                continue
            # Only surface rules that are CONFIRMED True (not None/
            # requires_info) AND that represent a fault conclusion
            # (skip the healthy rule and follow-on rules that depend on
            # asserted facts the technician didn't set).
            if res is True and rule.get("rule_id", "").startswith("RULE_FAULT"):
                secondary_faults.append(rule["rule_id"])
        if secondary_faults:
            notes.append(
                "SIMULTANEOUS FAULTS: in addition to the primary conclusion "
                f"({fired['rule_id']}), the following fault(s) are ALSO "
                f"measured-true and should be investigated: "
                f"{secondary_faults}. Priority determined the primary; do "
                f"not ignore the secondary fault(s)."
            )
        # If rail_short was never measured, the conclusion rests on the
        # assumption that a short is not confirmed — it does NOT prove
        # the rail is not shorted. State this honestly.
        if provenance.get("rail_short") not in ("confirmed",):
            rail_short_val = facts.get("rail_short")
            if rail_short_val is None:
                notes.append(
                    "CAVEAT: rail-to-ground resistance was NOT measured. The "
                    "conclusion assumes a short is not confirmed; it does not "
                    "prove the rail is not shorted. Measure resistance to ground "
                    "(unpowered) to rule out a short."
                )
        # Label suspected vs confirmed facts in the fired rule's domain.
        suspected = [k for k, v in provenance.items()
                     if v == "suspected" and facts.get(k) is True]
        if suspected:
            notes.append(
                "Suspected (not confirmed): " + ", ".join(suspected) +
                ". These are derived from measurements that suggest but do "
                "not prove a fault; confirm with a direct test."
            )

        return DiagnosisResult(
            rail_family=rail_family,
            topology=rail_topology,
            controller_part=controller_part,
            fired_rule_id=fired["rule_id"],
            next_steps=steps,
            requires_info=any(s.requires_info for s in steps),
            requires_info_note="; ".join(
                s.requires_info_note for s in steps if s.requires_info_note
            ),
            facts_evaluated=facts,
            candidate_rules=[r["rule_id"] for r in candidates],
            notes=notes,
        )

    # --- component lookup -------------------------------------------------

    def _lookup_component(self, part_number: str) -> Optional[dict]:
        """Search IC and MOSFET records by part number."""
        for sub in ("ICs", "MOSFETs"):
            root = self.pack_root / "ComponentDatabase" / sub
            if not root.exists():
                continue
            for f in root.rglob("*.json"):
                try:
                    with open(f, "r", encoding="utf-8") as fh:
                        rec = json.load(fh)
                except (OSError, json.JSONDecodeError):
                    continue
                if rec.get("part_number") == part_number:
                    return rec
        return None

    # --- rule introspection (used by tests + validator) ------------------

    def all_rules(self) -> list[dict]:
        return list(self.rules)

    def all_rails(self) -> dict[str, dict]:
        return dict(self.rails)


# ---------------------------------------------------------------------------
# Convenience loader
# ---------------------------------------------------------------------------


def load_engine(pack_root: Optional[Path] = None) -> DiagnosticEngine:
    """Load the engine from a pack root. Defaults to the parent of this file."""
    if pack_root is None:
        pack_root = Path(__file__).resolve().parents[2]
    return DiagnosticEngine(pack_root)
