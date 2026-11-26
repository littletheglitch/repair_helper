# Scenario Validation Report

This report documents the diagnostic validation & hardening pass. Every
scenario was run against the real engine code (not mocked). All outputs
are actual engine responses. Synthetic test measurements are clearly
distinguished from real hardware observations — NO real hardware was
used; all measurements are synthetic.

## Validation methodology

Six validation scripts were written, each targeting a specific spec
requirement. Each script runs realistic scenarios, prints the actual
fired rule / hypothesis / next-step, and asserts correct behavior.
Defects found were fixed in the ENGINE LOGIC (not by weakening tests).
Test expectations that encoded the OLD DEFECTIVE behavior were updated
to assert the CORRECT new behavior — each such update is documented and
makes the assertion STRICTER (specific fault conclusion vs None).

## 1. Power Rail Validation (12 scenarios)

Script: `scripts/validate_power_rail.py`

| # | Scenario | Fired rule | Correct? |
|---|----------|------------|----------|
| 1 | Normal voltage (3.30V) | RULE_RAIL_HEALTHY | ✓ |
| 2 | Zero, VIN absent | RULE_FAULT_VIN_ABSENT | ✓ |
| 3 | Zero, VIN present, EN absent | RULE_FAULT_EN_ABSENT | ✓ |
| 4 | Zero, VIN+EN present, no switching | RULE_FAULT_SWITCHING_ABSENT | ✓ |
| 5 | Zero, switching present | RULE_FAULT_RAIL_DEAD_WITH_SWITCHING | ✓ |
| 6 | Undervoltage (2.5V) | RULE_VOLTAGE_LOW_OUTPUT | ✓ |
| 7 | Overvoltage (3.6V) | RULE_VOLTAGE_HIGH_OUTPUT | ✓ |
| 8 | Unstable | RULE_VOLTAGE_UNSTABLE | ✓ |
| 9 | Excessive ripple | RULE_RIPPLE_EXCESSIVE | ✓ |
| 10 | Output collapsing under load | RULE_VOLTAGE_LOW_OUTPUT (no overload inference) | ✓ |
| 11 | Possible short (5Ω) | RULE_RESISTANCE_LOW_SUSPECT (suspect, not confirmed) | ✓ |
| 12 | Missing measurements | ALW_002 (asks for next, requires_info) | ✓ |

Key constraints verified:
- Scenarios 2-5 produce DIFFERENT rules (zero-voltage is NOT one fault).
- Scenario 10 does NOT infer overload from low voltage alone — it asks
  for load current measurement.
- Scenario 11 flags a SHORT SUSPECT, not a confirmed short — requires
  thermal confirmation.

## 2. Motherboard Power-On Validation (15 scenarios)

Script: `scripts/validate_motherboard.py`

| # | Scenario | Result | Correct? |
|---|----------|--------|----------|
| 1 | Adapter not detected | FAIL_adapter_input | ✓ |
| 2 | No standby rails | FAIL_standby_rails | ✓ |
| 3 | Standby OK, EC not responding | FAIL_ec_alive | ✓ |
| 4 | Power button not detected | FAIL_power_button | ✓ |
| 5 | EC receives button, no sequence | FAIL_power_button | ✓ |
| 6 | Missing regulator enable | FAIL_main_enables | ✓ |
| 7 | VRM failure | FAIL_cpu_vrm | ✓ |
| 8 | Missing reset | FAIL_clock_reset | ✓ |
| 9 | No POST | FAIL_post | ✓ |
| 10 | POST without display | NEED_DATA_display (partial measurement) | ✓ |
| 11 | Intermittent shutdown (symptom) | SYMPTOM_SHUTDOWN | ✓ |
| 12 | Only adapter measured | NEED_DATA (NOT a false FAIL) | ✓ |
| 13 | Adapter+standby only | NEED_DATA_ec (NOT false EC failure) | ✓ |
| 14 | Desktop: only 5VSB | NEED_DATA_front_panel (NOT false failure) | ✓ |
| 15 | Combo: standby zero + shutdown symptom | FAIL_standby (measured > symptom) | ✓ |

Key constraint verified: the engine does NOT claim a stage failed when
its required measurement is missing (scenarios 12-14 return NEED_DATA,
not FAIL). Measured failures take priority over symptoms (scenario 15).

## 3. Power Supply Topology Validation

Script: `scripts/validate_power_supply.py`

Honest finding: low-voltage topologies (buck, boost, linear, buck_boost)
previously shared an IDENTICAL generic workflow. This was a real
limitation. FIXED: the workflow now injects topology-specific stage
context and component guidance. A buck's feedback-stage failure mentions
"FB divider + internal reference"; a boost's mentions "output diode
(schottky)"; a flyback's mentions "optocoupler + TL431 + transformer".

Mains topologies (flyback, LLC, PFC) correctly hit the SAFETY GATE
(hard BLOCK on primary-side probing) — genuinely different from
low-voltage topologies.

Remaining limitation (reported honestly): the workflow still walks a
common signal chain (input→VCC→EN→switching→FB→output→PGOOD). The
topology-specific differentiation is in the GUIDANCE TEXT, not in
distinct stage-check sequences per topology. Marked PARTIAL.

## 4. Circuit Analysis Validation

Script: `scripts/validate_circuit_analysis.py`

Same topology (CCT_buck) with different measurements produces 3
DISTINCT conclusions (HEALTHY / INPUT_MISSING / OUTPUT_MISSING).
Control-signal and feedback relationships are checked (MOSFET gate,
op-amp feedback). Topology recognition from component list works.

Honest limitation: the engine checks NAMED SIGNALS (input/output/control/
feedback) but does NOT do full netlist-level node-graph traversal. It
cannot reason about arbitrary connections a technician supplies. Marked
PARTIAL for structured-relationship depth.

## 5. Component Replacement Validation

Script: `scripts/validate_replacement.py`

| Test | Result | Correct? |
|------|--------|----------|
| Same part → COMPATIBLE | COMPATIBLE | ✓ |
| Missing VDS → UNKNOWN | UNKNOWN | ✓ |
| Higher VDS + bad RDS → CONDITIONAL | CONDITIONAL | ✓ |
| Substring name + lower VDS → NOT_RECOMMENDED | NOT_RECOMMENDED | ✓ |
| All 12 MOSFET criteria checked | 12/12 | ✓ |

All spec constraints verified:
- Missing critical spec → INSUFFICIENT_DATA (UNKNOWN), not COMPATIBLE.
- Higher voltage rating alone does NOT establish compatibility.
- Substring name match does NOT override spec comparison.

## 6. Adversarial / Regression Validation (9 tests)

Script: `scripts/validate_adversarial.py`

| # | Test | Result | Correct? |
|---|------|--------|----------|
| 1 | Contradictory (zero rail + switching) | FAULT_RAIL_DEAD_WITH_SWITCHING | ✓ |
| 2 | Invalid values (negative/NaN/string) | No crash; graceful | ✓ |
| 3 | Impossible rail dependency | dependency issue flagged | ✓ |
| 4 | Missing values (None) | NOT_MEASURED != zero | ✓ |
| 5 | Wrong units (resistance powered) | invalid-context flagged | ✓ |
| 6 | Multiple simultaneous faults | most upstream localised | ✓ |
| 7 | Follow-on rules across calls | reachable via asserted_facts | ✓ |
| 8 | No fact leakage across sessions | fresh instance = no stale facts | ✓ |
| 9 | Incomplete component record | UNKNOWN | ✓ |

## Test commands and outputs (actual)

### Validators
```
$ python Tools/validate_database.py
  WARN  MAP MAP_TI_TLV70033_TO_TI_TLV70033_REPLACEMENT_NOTE: classification is UNKNOWN — verify before relying on this mapping
(and 26 passing checks)

$ python Tools/validate_rules.py
PASS: 515  WARN: 0  FAIL: 0
(and 515 passing checks)
```

### Test suites
```
$ python Tests/run_all_tests.py
Ran 33 tests in 0.024s

OK

$ python Tests/run_expanded_tests.py
Ran 113 tests in 0.084s

OK
```

Total: **146 tests pass**, **515 rule-checks pass**, **26 DB-checks pass**
(2 expected WARN).

## Synthetic vs real hardware

ALL scenarios use SYNTHETIC measurements. No real hardware, DMM, or
oscilloscope was used. The diagnostic LOGIC is verified (correct rule
fires for a given measurement set), but:
- Measurement VALUES are synthetic.
- Rail voltage tolerances are typical, not board-specific.
- Power-on sequence stages model the common case.
- Real hardware validation requires a physical board + instruments.
