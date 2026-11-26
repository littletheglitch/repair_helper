# Data Dictionary

Reference for every data structure and JSON schema in the pack.

## Core framework (DiagnosticEngines/core/framework/)

### Case
Top-level container for one repair job. Fields: case_id, platform
('laptop'/'desktop'/'power_supply'/'other'), manufacturer, device_model,
board_model, symptoms[], initial_observations[], technician_notes[],
status (CaseStatus enum), created_at_utc, updated_at_utc,
confirmed_failure, repair_action, final_verification, technician,
custom_fields.

### Measurement
One measurement. Fields: name, kind (MeasurementKind enum: voltage/
resistance/continuity/diode/current/frequency/temperature/waveform/
logic_level/custom), state (MeasurementState enum: NOT_MEASURED/
MEASURED_ZERO/BELOW_RANGE/ABOVE_RANGE/IN_RANGE/INVALID/NOT_APPLICABLE/
UNKNOWN), value, unit, location, reference_point, condition
(MeasurementCondition: POWERED/UNPOWERED/PARTIALLY_POWERED/UNKNOWN),
instrument, timestamp_utc, technician, verified, uncertainty, notes,
measurement_id.

### Evidence
Fields: evidence_id, hypothesis_id, type (EvidenceType: SUPPORTING/
CONTRADICTING/MISSING/AMBIGUOUS/BACKGROUND), strength (EvidenceStrength:
WEAK/MODERATE/STRONG/DEFINITIVE), description, measurement_id,
rule_source (mandatory), source, source_kind, notes, created_at_utc.

### Hypothesis
Fields: hypothesis_id, statement, engine, rule_source (mandatory),
status (HypothesisStatus: PROPOSED/POSSIBLE/PROBABLE/CONFIRMED/
CONTRADICTED/REJECTED/UNCERTAIN), evidence[], proposed_at_utc,
updated_at_utc, notes.

### DiagnosticSession
Fields: case, measurements[], hypotheses[], conclusions[],
safety_violations[], created_at_utc, updated_at_utc.

### EngineConclusion
Fields: engine, rule_id, next_steps[], new_hypotheses[], new_evidence[],
safety_violations[], requires_info, requires_info_note, notes[],
concluded_at_utc, conclusion_id.

## Component database (ComponentDatabase/)

### IC record (schemas/ic_component.schema.json)
Fields: record_id, part_number, manufacturer, category, package,
pin_count, pinout, pin_functions, electrical_characteristics
(input_voltage_range_v, output_voltage_range_v, maximum_ratings,
operating_conditions, quiescent_current_ma, switching_frequency_khz),
typical_applications, common_circuit_configurations, important_pins,
datasheet_url, datasheet_source, source_date, verification_status
(verified/partial/unknown), known_compatible_alternatives,
conditional_alternatives, incompatible_alternatives, replacement_notes,
tags, schema_version.

### MOSFET record (schemas/mosfet_component.schema.json)
Fields: record_id, part_number, manufacturer, channel_type (N-channel/
P-channel), package, pin_count, pinout (drain/source/gate), vds_v,
id_a, rds_on_mohm, vgs_th_v, vgs_max_v, gate_charge_nc, thermal
(r_theta_ja_c_per_w, r_theta_jc_c_per_w, operating_temp_range_c),
switching_characteristics, technology, typical_applications,
datasheet_url, datasheet_source, source_date, verification_status,
replacement_notes, tags, schema_version.

### Discrete semiconductor (schemas/discrete_semiconductor.schema.json)
For diode/Schottky/TVS/Zener/BJT/ESD. Fields: record_id, part_number,
manufacturer, category, package, pin_count, pinout,
electrical_characteristics (vrrm_v, if_avg_a, vf_v, vbr_v, vc_v, pp_w,
vceo_v, ic_a, hfe_min, pd_w, thermal), typical_applications,
datasheet_url, datasheet_source, source_date, verification_status,
replacement_notes, tags, schema_version.

### Analog IC (schemas/analog_ic.schema.json)
For op-amp/comparator/supervisor/reset/current-sense/protection/gate-
driver/interface/clock/optocoupler/power-stage. Fields: record_id,
part_number, manufacturer, category, package, pin_count, pinout,
electrical_characteristics (supply_voltage_range_v, supply_current_ma,
bandwidth_mhz, slew_rate_v_per_us, offset_voltage_mv, ctr_min,
isolation_voltage_vrms, threshold_v, reset_delay_ms, gate_drive_a,
propagation_delay_ns), typical_applications, datasheet_url,
datasheet_source, source_date, verification_status, replacement_notes,
tags, schema_version.

### Replacement mapping (schemas/replacement_mapping.schema.json)
Fields: mapping_id, original_part, candidate_part, component_type (IC/
MOSFET), classification (COMPATIBLE/CONDITIONAL/NOT_RECOMMENDED/UNKNOWN),
evidence[] (criterion, verdict, note), conditions[], datasheet_urls
(original, candidate), datasheet_sources, verification_status,
verification_notes, source_date, schema_version.

## Diagnostic rules + rails (DiagnosticEngines/)

### Rail definition (schemas/rail_definition.schema.json)
Fields: rail_family, description, typical_voltage_v (min/typ/max/notes),
typical_topology, always_on, common_controllers, platform
(laptop/desktop/universal), psu_signal_notes, cable_note,
upstream_rails, downstream_rails, measurement_keys, schema_version.

### Diagnostic rule (schemas/diagnostic_rule.schema.json)
Fields: rule_id, topology, applies_to_rail_families, condition
(expression, notes), next_steps[] (step, action, measure
(what/where/instrument/expected/safety), interpretations[] (if_result/
then/set_facts/next_rule), requires_info, requires_info_note),
rail_voltage_hint_v, controller_hint, references, schema_version.

## Failure pattern DB (DiagnosticEngines/core/failure_pattern_db.py)

### RepairCase
Fields: case_id, device_manufacturer, device_model, board_model,
platform, symptoms[], initial_observations[], measurements[],
diagnostic_path[], confirmed_faulty_component, root_cause,
repair_action, final_verification, technician_notes, source
(technician/forum/service_manual), confidence (confirmed/probable/
unresolved), date, rails_involved[], components_involved[], topology,
failure_pattern, promotion_status (pending/approved/rejected),
promotion_notes.

## Knowledge platform (DiagnosticEngines/core/knowledge_platform.py)

### KnowledgeItem
Fields: item_id, title, source_title, source_author, source_edition,
source_publication_date, source_page_or_section, source_url,
local_source_reference, extraction_status (pending/extracted/failed),
verification_status (unverified/verified/partial), category,
technical_classification, content_text, tags, extracted_at_utc.

## Counts

- IC records: 20
- MOSFET records: 5
- Discrete semiconductor records: 7
- Analog IC records: 12
- Replacement mappings: 9
- Rail families: 25
- Diagnostic rules: 39
- Power-supply topologies: 15
- Circuit topologies: 36
- Repair cases: 1
