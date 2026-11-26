# Changelog

## v1.5.0 — 2026-11-25

Engineering phase: deepen diagnostic reasoning, component knowledge, and
practical repair usefulness. 350 tests pass (up from 246 in v1.4.1).

### Phase 1 — Power-rail Measurement migration
- **Added:** normalizer accepting both legacy `Measurement` and framework
  `Measurement` (state-aware: NOT_MEASURED / MEASURED_ZERO / INVALID /
  IN_RANGE / ABOVE_RANGE / BELOW_RANGE).
- **Added:** state-aware derived facts: `<name>_zero`, `<name>_invalid`,
  `<name>_overload`, `<name>_out_of_range`.
- **Added:** `RULE_FAULT_INVALID_READING` + `RULE_FAULT_OVERLOAD_READING`
  rules (priority 5) surfacing INVALID/OVERLOAD states.
- **Fixed:** 0V (MEASURED_ZERO) no longer confused with NOT_MEASURED or
  INVALID; OL resistance correctly reads as open (not short).
- **Preserved:** backward compat with legacy `Measurement` payloads.

### Phase 2 — Power-supply engine
- **Added:** `depends_on` to every stage in all 15 topologies (acyclic
  dependency graph reflecting real power flow).
- **Added:** `_classify_fault_type` — startup_failure / protection_shutdown
  / missing_switching / unstable_output / overload / output_stage_failure.
- **Added:** dependency gate — refuses to diagnose a downstream stage as
  confirmed-defective when an upstream dependency is unknown/failed.
- **Added:** mains topologies now walk their OWN secondary-side stages
  (LLC resonant_tank, flyback rectification_secondary) while preserving
  the safety gate.
- **Added:** `wrong_voltage` + `unstable` fault patterns to low-voltage
  topologies.

### Phase 3 — Component database + replacement intelligence
- **Added:** 32 verified component records (PWM: UC3842/3/4/5, SG3525,
  TL494, OB2269; laptop: TPS51125, TPS51225, TPS51216, TPS51716;
  charger: BQ24725A, ISL6251; MOSFETs: SI2302, IRLR7843, IRF540N, SI2301,
  IRLR9303, AO4407; LDOs: AP2112K-3.3, RT9193-33, AMS1117-1.8;
  supervisors: TPS3808G33, MAX811; comparators: LM393, LM311; op-amps:
  LM358, OPA2333; diodes: 1N4148, SS34, UF4007; zeners: 1N4733A, 1N5239B;
  TVS: SMBJ5.0CA, SMCJ33CA). DB grew 51 → 76 component records.
- **Fixed:** `_eval_analog_ic` + `_eval_ic` null-safe (no crash on
  `min=null`).
- **Added:** `CRITICAL_MISMATCH_SPECS` — vout/vds/vz/vrrm mismatch is a
  hard NOT_RECOMMENDED regardless of unknown pinout.
- **Added:** routing for new categories (ldo, multi_rail_buck_controller,
  voltage_supervisor) to `_eval_ic`.

### Phase 4 — Motherboard power-sequence
- **Added:** 7 new laptop stages (adapter_input, input_protection,
  charger_ic, battery_path, rtc, vrm_gpu, bios_spi, ram_init) — total 17.
- **Added:** `depends_on`, `alternative_hypotheses`, `next_measurement`,
  `next_measurement_info_value` on every stage.
- **Added:** dependency-respecting walk (DEPENDENCY CAVEAT when upstream
  unverified).
- **Added:** alternative hypotheses per stage — no single-output
  IC-failure inference (surfaces 3-4 alternatives instead of "IC dead").
- **Added:** evidence-ranked hypotheses (primary STRONG + alternatives WEAK).
- **Added:** next-measurement-by-info-value.
- **Preserved:** backward compat (old measurement sets skip new stages).

### Phase 5 — Netlist recognition
- **Added:** recognizers for comparator, voltage_divider, flyback_secondary,
  mosfet_high_side, mosfet_low_side, charging_stage, protection_circuit,
  feedback_network.
- **Refined:** LDO check excludes comparators; op-amp requires amplifier
  part hint (avoids false-positives on voltage dividers).
- **Added:** `trace_all_paths` — enumerates parallel branches (caps 20).
- **Added:** `uncertainty_report` — floating nets, orphan pins, missing
  ground, external ports, explicit "NOT simulated" disclaimer.

### Phase 6 — Knowledge quality
- **Added:** bidirectional synonym expansion (20 pairs: step-down↔buck,
  step-up↔boost, etc.).
- **Added:** dedup in `search` (identical title+content collapse to
  highest-scored).
- **Added:** `KnowledgeGraph` + `KnowledgeLink` + `build_seed_graph` —
  22 curated entity relationships (component↔topology↔failure↔procedure).
- **Preserved:** verified_only enforcement, provenance tiers.

### Phase 7 — Validation + schema alignment
- **Added:** `Tests/run_validation_suite.py` — categorized runner (unit /
  integration / adversarial / knowledge-db / synthetic / hardware-required)
  + offline + backward-compat verification.
- **Fixed:** all new component records aligned with `ic_component.schema.json`
  and `analog_ic.schema.json` (record_id prefixes, category enums,
  pin_functions as objects, ec field restrictions, verification_status).
- **Fixed:** INVALID/OVERLOAD rules (removed `_phase1`, schema_version
  1.0.0, applies_to known rails).
- **Added:** 6 new phase test files (104 new tests).

### Test totals
- **350 tests pass** (up from 246 in v1.4.1), 0 failures.
- **528 rule checks** pass, **41 DB checks** pass.
- Offline + backward-compat verified.

### Verification levels
- Source-inspected: all 7 engine modules + 76 component records (count corrected in v1.5.1).
- Synthetic-tested: all 350 tests.
- Unverified: real hardware behavior (impossible in sandbox).
- Hardware-required: measurement values, voltage tolerances, stage timing.

---

## v1.4.1 — 2026-11-25 (prior)

Independent technical audit. Fixed 11 defects (A0-2, A0-4, A1-1, A1-2,
A2-1, A2-2, A2-3, A3-1, A6-1, A6-2, A6-3). 246 tests pass.

## v1.4.0 — prior

P0-P5 implementation. 207 tests pass.


## v1.5.1 — Closeout remediation (2026-11-26)

- Corrected all maintained component-count references: 76 actual component records (35 IC + 5 MOSFET + 20 discrete semiconductor + 16 analog IC).
- Expanded desktop power-on coverage to 14 ordered core stages through display; added 3 optional post-display checks (USB/audio/LAN) that do not gate POST or a healthy core power-on result. Existing stage IDs/measurement names are preserved where possible.
- Added local document extraction/ingestion for HTML/HTM, DOCX, CSV, TXT/MD/RST/LOG and selectable-text PDF when PyMuPDF or pypdf is installed. Added SHA-256 duplicate suppression, extraction provenance, and visible relative image references from HTML. No network fetch, OCR or directory crawl.
- Kept BIOS/firmware binaries, schematics and BoardView outside the main knowledge ingestion path.
- Added an explicit power_on_sequence operation to the pack host adapter, and a tested read-only bridge into the master EDA Tool Router. The host bridge also exposes explicit one-file local ingestion.
- Demoted the shipped Lenovo T480 record to a non-promotable seed example because its board identifier is a placeholder and its claimed independent verification is not evidenced in the package. Default repair-case searches now exclude seed examples. Real verified repair-case count remains 0.
- Added regression tests for document extraction, deduplication, forbidden-category blocking and seed-case policy.

Validation scope: offline software tests only. No physical board/hardware validation is claimed.


Closeout validation: 360/360 pack tests and 291/291 master EDA host tests passed; 528 rule checks and 41 database checks passed (2 expected warnings). No physical hardware validation or real repair-case population is claimed.
