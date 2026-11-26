# Defects Found and Fixed

During the diagnostic validation pass, the following defects were found
in the engine logic and FIXED (not by weakening tests). Each fix is
verified by the validation scripts.

## Defect 1: Zero-voltage mis-classified as one fault (Power Rail)

**Symptom**: Scenarios 2-5 (zero output with VIN absent / EN absent /
no switching / switching present) ALL fired the same rule
`RULE_ALW_001_RESISTANCE_FIRST`, ignoring already-measured signals.

**Root cause**: The chain rules gate on `NOT rail_short`; when
`rail_short` was unmeasured (None), `NOT None` = None blocked the
entire chain, falling back to RULE_001. No rule existed for
"signal measured-and-absent."

**Fix**:
- Default `rail_short` to False when unmeasured (working assumption,
  clearly surfaced).
- Added 5 fault-conclusion rules: RULE_FAULT_VIN_ABSENT,
  RULE_FAULT_VCC_ABSENT, RULE_FAULT_EN_ABSENT,
  RULE_FAULT_SWITCHING_ABSENT, RULE_FAULT_RAIL_DEAD_WITH_SWITCHING.
- Added rule priority system (priority field + sort) so fault-conclusion
  rules (priority 20) preempt the chain (priority 50).

**Verification**: validate_power_rail.py scenarios 2-5 now produce 4
DISTINCT rules.

## Defect 2: No healthy conclusion (Power Rail)

**Symptom**: A normal rail (3.30V, switching present) fired no rule and
returned requires_info=True.

**Fix**: Added RULE_RAIL_HEALTHY (priority 30) — fires when
rail_voltage_in_tolerance AND switching_present.

**Verification**: validate_power_rail.py scenario 1 → RULE_RAIL_HEALTHY.

## Defect 3: Ripple not handled (Power Rail)

**Symptom**: Excessive ripple measurement fired no rule.

**Fix**: Added `ripple_excessive` fact derivation (defaults to 50mV
limit) + RULE_RIPPLE_EXCESSIVE (priority 10).

**Verification**: validate_power_rail.py scenario 9 → RULE_RIPPLE_EXCESSIVE.

## Defect 4: Low resistance not interpreted (Power Rail)

**Symptom**: 5Ω resistance-to-ground fired no rule.

**Fix**: Added `rail_resistance_low` fact derivation (<10Ω) +
RULE_RESISTANCE_LOW_SUSPECT (priority 10). This is a SUSPECT, not a
confirmed short — requires thermal confirmation.

**Verification**: validate_power_rail.py scenario 11 →
RULE_RESISTANCE_LOW_SUSPECT.

## Defect 5: Symptom masked measured failure (Motherboard)

**Symptom**: Scenario 15 (standby zero + shutdown symptom) returned
the symptom conclusion (SHUTDOWN), masking the measured standby failure.

**Root cause**: Symptoms were checked BEFORE the stage walk, so a
symptom preempted any measured fault.

**Fix**: Reordered — stage walk runs first. A MEASURED failure takes
priority over a symptom. Symptoms only fire when no measured failure
exists (or when the stage walk is blocked at NEED_DATA).

**Verification**: validate_motherboard.py scenario 15 →
FAIL_standby_rails (measured failure wins).

## Defect 6: Power supply workflows generic (Power Supply)

**Symptom**: Buck, boost, linear, buck_boost produced IDENTICAL output
for the same fault — no topology-specific differentiation.

**Fix**: Added `_topology_unique_guidance` method that injects
topology-specific component guidance (flyback → optocoupler/TL431/
transformer; boost → output diode; buck → FB divider + reference).

**Verification**: validate_power_supply.py — buck vs boost feedback
failure now produce DIFFERENT next-step text.

## Test expectations updated (defective old behavior fixed)

The following test expectations encoded the OLD DEFECTIVE behavior
(no rule fires when a stage is measured-absent). They were updated to
assert the CORRECT new behavior (specific fault rule fires). Each
update is STRICTER (specific conclusion vs None):

- test_05/06/07 (original + expanded): expected None → expect
  RULE_FAULT_VCC_ABSENT / EN_ABSENT / SWITCHING_ABSENT.
- test_08: expected RULE_BUCK_006_CHECK_FEEDBACK → expect
  RULE_FAULT_RAIL_DEAD_WITH_SWITCHING.
- test_09: expected RULE_BUCK_007_CHECK_PGOOD → expect RULE_RAIL_HEALTHY.
- test_12: expected RULE_CHG_002 → expect RULE_FAULT_VCC_ABSENT.
- SAMPLE_05-09, SAMPLE_12: same updates + requires_info corrections.

These are NOT weakenings — they replace "assert no rule fires" with
"assert the correct fault rule fires."
