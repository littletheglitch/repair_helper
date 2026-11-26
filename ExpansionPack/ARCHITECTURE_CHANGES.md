# Architecture Changes — v1.4.1 → v1.5.0

This documents every significant architectural change in v1.5.0. Each
change names the exact limitation it addresses and the design decision.

## Phase 1 — Power-rail engine: framework Measurement migration

**Limitation addressed:** the legacy `Measurement(name, value, measured)`
conflated NOT_MEASURED, MEASURED_ZERO, INVALID, and OVERLOAD (OL) into
`value=None` or `value=0.0` with no way to distinguish them. A 0V
reading could not be told apart from an unmeasured slot or an invalid
reading.

**Change:** added a normalizer (`_normalize_measurement`) that maps the
legacy form AND the framework `Measurement(name, kind, state, value, ...)`
onto framework states. `_facts_from_measurements` now derives state-aware
facts: `<name>_zero`, `<name>_invalid`, `<name>_overload`,
`<name>_out_of_range`, in addition to the existing `<name>_present`/
`<name>_measured`. 0V (MEASURED_ZERO) → `present=False, zero=True`;
NOT_MEASURED → all None; INVALID → facts None + INVALID_READINGS note;
OVERLOAD → `overload=True`.

**Backward compatibility:** legacy `Measurement` payloads still work —
the normalizer maps `measured=False` → NOT_MEASURED, `value=0.0` →
MEASURED_ZERO, `value="OL"` → ABOVE_RANGE, etc. All 146 pre-v1.5 tests
pass unchanged.

**New rules:** `RULE_FAULT_INVALID_READING` (priority 5) and
`RULE_FAULT_OVERLOAD_READING` (priority 5) surface INVALID/OVERLOAD
states with a re-measure instruction.

## Phase 2 — Power-supply engine: deepened diagnostic workflow

**Limitation addressed:** stages had no `depends_on` (the walk ignored
dependencies); all mains topologies shared one fixed workflow; no
fault-type discrimination.

**Changes:**
1. Added `depends_on` to every stage in all 15 topologies. The dependency
   graph is acyclic and reflects real power flow (input → rectification →
   PFC → controller → switch → output → feedback → protection).
2. Added `_classify_fault_type` — classifies the case into
   startup_failure / protection_shutdown / missing_switching /
   unstable_output / overload / output_stage_failure / unknown.
3. Added `_stage_dependency_state` + `_build_stage_health` — a
   dependency gate that refuses to diagnose a downstream stage as
   confirmed-defective when an upstream dependency is unknown/failed.
4. Mains topologies now walk their OWN secondary-side stages (resonant_tank
   for LLC, rectification_secondary for flyback) while preserving the
   safety gate (primary-side signals blocked).
5. Added `wrong_voltage` + `unstable` fault patterns to low-voltage
   topologies.

## Phase 3 — Component database expansion + replacement intelligence

**Limitation addressed:** ~40 components was insufficient; the
replacement engine crashed on null fields and masked critical-spec
mismatches behind unknown pinout.

**Changes:**
1. Added 32 verified component records: PWM controllers (UC3842/3/4/5,
   SG3525, TL494, OB2269), laptop controllers (TPS51125, TPS51225,
   TPS51216, TPS51716), charger controllers (BQ24725A, ISL6251), MOSFETs
   (SI2302, IRLR7843, IRF540N, SI2301, IRLR9303, AO4407), LDOs, supervisors,
   comparators, op-amps, diodes, zeners, TVS. DB grew 51 → 83 components.
2. Made `_eval_analog_ic` and `_eval_ic` null-safe (records may carry
   `min=null` with `max` set).
3. Added `CRITICAL_MISMATCH_SPECS` to `_derive_classification`: a
   mismatch on vout/vds/vz/vrrm/forward_current/continuous_current is a
   hard NOT_RECOMMENDED regardless of unknowns on non-critical specs.
4. Routed new categories (`ldo`, `multi_rail_buck_controller`,
   `voltage_supervisor`) to `_eval_ic`.

## Phase 4 — Motherboard power-sequence diagnosis

**Limitation addressed:** the sequence had 10 stages with no explicit
dependencies; a single missing output was treated as "IC is dead"; no
evidence ranking; no next-measurement-by-info-value.

**Changes:**
1. Expanded `_LAPTOP_STAGES` from 10 → 17 stages: added adapter_input,
   input_protection, charger_ic, battery_path, rtc, vrm_gpu, bios_spi,
   ram_init. Each stage has `depends_on`, `alternative_hypotheses`,
   `next_measurement`, `next_measurement_info_value`.
2. Added a dependency-respecting walk: DEPENDENCY CAVEAT when upstream
   is unverified.
3. Added alternative hypotheses per stage — a single missing output
   surfaces 3-4 alternative causes instead of concluding "IC is dead".
4. Added evidence-ranked hypotheses: primary STRONG; alternatives WEAK.
5. Added next-measurement-by-info-value.
6. Backward-compat skip logic: a stage with NO supplied measurements is
   skipped if a later stage HAS measurements.

## Phase 5 — Netlist recognition

**Limitation addressed:** only 7 structures recognized; no multi-path
tracing; no uncertainty reporting.

**Changes:**
1. Added recognizers: comparator, voltage_divider, flyback_secondary,
   mosfet_high_side, mosfet_low_side, charging_stage, protection_circuit,
   feedback_network. Refined LDO/op-amp checks to avoid false-positives.
2. Added `trace_all_paths` — enumerates ALL simple paths (parallel
   branches), caps at 20.
3. Added `uncertainty_report` — floating nets, orphan pins, missing
   ground, external ports, explicit "NOT simulated" notes.

## Phase 6 — Knowledge quality

**Limitation addressed:** no synonym expansion; duplicates returned; no
entity relationships.

**Changes:**
1. Added `_SYNONYMS` map + bidirectional `_expand_synonyms`: "step-down"
   ↔ "buck", 20 common pairs.
2. Added dedup to `search`: identical title+content_text collapse to the
   highest-scored copy.
3. Added `KnowledgeGraph` + `KnowledgeLink` + `build_seed_graph`: 22
   curated entity relationships (component↔topology↔failure↔procedure).

## Phase 7 — Validation + schema alignment

**Changes:**
1. Added `Tests/run_validation_suite.py` — categorized runner (unit /
   integration / adversarial / knowledge-db / synthetic / hardware-
   required) + offline + backward-compat verification.
2. Aligned all new component records with `ic_component.schema.json`
   and `analog_ic.schema.json` (record_id prefixes, category enums,
   pin_functions as objects, ec field restrictions, verification_status).
3. Fixed INVALID/OVERLOAD rules: removed `_phase1`, schema_version 1.0.0,
   applies_to_rail_families restricted to known rails.

## Files changed

- `DiagnosticEngines/core/engine.py` — normalizer, state-aware facts,
  INVALID/OVERLOAD rules, simultaneous-fault scan
- `DiagnosticEngines/core/replacement_engine.py` — null-safe supply
  voltage, critical-mismatch priority, new category routing
- `DiagnosticEngines/core/power_on_sequence.py` — 17 stages w/ deps,
  alternative hypotheses, evidence ranking, next-measurement, skip logic
- `DiagnosticEngines/core/knowledge_platform.py` — synonyms, dedup,
  KnowledgeGraph
- `DiagnosticEngines/CircuitAnalysis/netlist.py` — new recognizers,
  trace_all_paths, uncertainty_report
- `DiagnosticEngines/PowerSupply/engine.py` — fault-type classifier,
  dependency gates, mains secondary-side walk
- `DiagnosticEngines/PowerSupply/topologies/*.json` — depends_on + side
- `ComponentDatabase/` — 32 new component records, schema-aligned
- `Tests/` — 6 new phase test files + validation runner
