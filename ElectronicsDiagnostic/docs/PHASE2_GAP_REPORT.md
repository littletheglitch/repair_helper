# Phase 2 — Architecture & Gap Report (§18 first action)

This document is the inspection report required before any Phase 2 work.
It is produced **after** reading the existing source. It is the contract for
everything that follows: no working component is rewritten without a
technical reason stated here.

## A. Current architecture (as built in Phase 1)

```
src/eda/
  config.py            paths + settings (portable, no network)
  database.py          SQLite + FTS5; 10 tables, 5 triggers, 3 indexes
  knowledge_base.py    loads Knowledge/*.json -> knowledge_facts
  sessions.py          repair sessions (create/open/list/close/notes/symptoms/timeline)
  measurements.py      parse "measure NAME = VALUE UNIT [@ point]" + persist
  documents.py         format-aware import: PDF/text/BoardView/BIOS/image
  retrieval.py         on-demand keyword search over KB + documents
  document_requests.py explicit, justified single-file document requests
  reasoning.py         rule-based evidence engine (7 rule families, 6-rung ladder)
  safety.py            forbidden/warning action filters + caution generator
  llm_engine.py        llama.cpp / OpenAI-API wrapper, graceful fallback
  reporting.py         Markdown + optional ReportLab PDF report
  cli.py               16 commands + natural-language fallback + REPL
  __main__.py          console-script entry point
```

74 tests pass. SQLite schema is `CREATE TABLE IF NOT EXISTS` only — there is
no migration framework and no `user_version` PRAGMA. The reasoning engine
is the source of truth; the LLM is only a paraphraser and is forbidden by
system prompt from inventing values.

## B. Existing modules and their actual status

| Module           | Status | Notes |
|------------------|--------|-------|
| config           | stable | no changes needed |
| database         | stable | schema is idempotent-create only; needs migration support for Phase 2 |
| knowledge_base   | stable | 4 JSON files, 60 facts; needs the 6-tier provenance model + datasheet fields |
| sessions         | stable | no changes needed |
| measurements     | stable | needs validation against reference ranges + comparison engine |
| documents        | stable | needs datasheet-extraction hooks + topology-extraction hooks |
| retrieval        | stable | FTS5 keyword; vector retrieval deliberately not used |
| document_requests| stable | reused as-is |
| reasoning        | stable | 7 rule families; will be **wrapped** (not replaced) by the new hypothesis manager; the rule engine becomes one of several evidence sources |
| safety           | stable | reused as-is; extended by the next-best-test engine |
| llm_engine       | stable | reused as-is; gains an orchestrator role via the Tool Router |
| reporting        | stable | no changes needed |
| cli              | stable | gains new `engine` commands but existing commands preserved |

## C. Missing components compared with this specification

1. **No common schemas.** There is no formal unit/quantity type, no component/pin/net identifier, no structured engine-result envelope. Values are loose floats with string units; dimensional checking does not exist.
2. **No Tool Router / Engine Interface.** There is one rule engine (`reasoning.analyze`); there is no registry, versioning, or LLM-callable dispatch.
3. **No engine validation framework.** Tests verify behaviour but do not produce a machine-readable test report (totals, tolerances, environment, engine version).
4. **No electrical mathematics engine.** Ohm/Kirchhoff/series/parallel are facts in the KB, not computable.
5. **No measurement validation engine.** `identify_missing_context` flags missing adapter/reference but does not compare values against verified reference ranges or detect contradictions across operating conditions.
6. **Knowledge base lacks the 6-tier provenance model** (general principles / manufacturer specs / verified references / community / technician-confirmed / unverified) and lacks datasheet-field schema.
7. **No datasheet intelligence engine.** Datasheets are imported and indexed as text chunks; no structured extraction of pinout, ratings, thresholds, etc.
8. **No circuit topology engine.** There is no graph of components/pins/nets; the rule engine uses rail-name heuristics only.
9. **No power-sequence engine.** Sequencing is implicit in the rule engine's next-test ladder; there is no configurable per-board dependency graph and no G3/S5/S4/S3/S0 state model.
10. **Hypothesis manager lacks explicit statuses** (Confirmed / Strongly supported / Plausible / Uncertain / Contradicted / Rejected) and lacks structured fields (probability basis, required additional evidence, suggested test, status).
11. **No next-best-test engine.** The 6-rung ladder is a fixed priority queue; it does not compute information gain across competing hypotheses.
12. **No SPICE integration.**
13. **No blind benchmark.**
14. **No controlled learning workflow.**
15. **No DB migration / integrity-check / recovery procedures.**

## D. Critical technical risks

- **Schema migration without breaking 74 tests.** The existing schema has no version column. Adding new tables is safe (`CREATE IF NOT EXISTS`). Adding columns to existing tables requires a guarded `ALTER TABLE`. The `knowledge_ad` trigger is patched at source-load time (database.py lines 211-218) and must not be touched.
- **`reasoning._persist` wipes `hypotheses`, `missing_evidence`, `next_tests` on every `analyze`.** Any new hypothesis manager that writes to those tables will be overwritten unless it either (a) extends the DELETE list, (b) writes to a new table, or (c) replaces `reasoning.analyze` at the CLI layer. We choose (b)+(c): new engines write to new tables; the CLI `analyze` command gains an orchestrator path that calls the new hypothesis manager, and `reasoning.analyze` becomes one evidence source. The existing `analyze` text output and the 74 tests stay green because the rule engine is preserved as a fallback.
- **LLM must never alter a deterministic engine's returned value.** The Tool Router returns machine-readable `EngineResult` objects; the LLM receives the result envelope as text and may only paraphrase. This is enforced by the result schema (the LLM is given the rendered result, not a value it can mutate).
- **Offline + low RAM.** SPICE is optional and lazily loaded. No engine loads the full KB or document archive. All engines are pure functions over their inputs plus on-demand DB/KB retrieval.
- **Unit safety.** A float-with-unit-string model is too loose (the existing `identify_missing_context` already has a unit-normalization inconsistency). Phase 2 introduces a `Quantity` type with SI base + engineering prefixes and dimensional analysis; the existing `measurements` table keeps its columns, and the new validation engine reads `unit` strings through a single `Quantity.from_record` parser.

## E. Prioritized implementation plan

| Priority | Milestone | What ships | Compatibility |
|----------|-----------|-----------|---------------|
| P0 | Common schemas + Tool Router + validation framework | `eda/schemas.py`, `eda/engines/base.py`, `eda/engines/registry.py`, `eda/validation.py`, `tests/test_schemas.py`, `tests/test_engines_base.py` | None broken; new modules only |
| P1 | Electrical math engine + measurement validation engine | `eda/engines/electrical_math.py`, `eda/engines/measurement_validation.py`, their tests, and a machine-readable validation report | New tables `reference_ranges`, `measurement_validations`; existing `measurements` columns untouched |
| P2 | Structured knowledge (6 tiers) + datasheet intelligence | Extend `knowledge_facts` with a `tier` column (guarded ALTER), new tables `datasheet_params`, `eda/engines/datasheet.py`, KB JSON schema bump | Loader is backward-compatible; existing facts default to `tier='verified-reference'` |
| P3 | Power-sequence engine + evidence-based hypothesis manager | `eda/engines/power_sequence.py`, `eda/engines/hypotheses.py`, new tables `power_sequences`, `power_sequence_nodes`, `hypotheses_v2`; CLI `analyze` routes through the new manager with the rule engine as evidence source | Existing `reasoning.analyze` and tests untouched; new `analyze2`/orchestrator path added, then wired into `analyze` behind a flag |
| P4 | Circuit topology engine | `eda/engines/topology.py`, new tables `nets`, `pins`, `connections`; honest about unsupported formats | No change to existing import flow; topology is built only from explicitly-verified connections or supported ASCII formats |
| P5 | Next-best-test engine | `eda/engines/next_best_test.py`, information-gain over competing hypotheses | Wraps the existing ladder; the existing `next-test` command output is preserved when the new engine has no better option |
| P6 | Blind benchmark + controlled learning | `eda/benchmark.py`, `eda/learning.py`, new tables `benchmark_cases`, `repair_cases`; CLI `benchmark` command | New only |
| P7 | Optional SPICE | `eda/engines/spice.py`, lazy import of `PySpice`/ngspice; documented limitations; `spice` disabled by default | Lazy import; if ngspice absent, engine reports unavailable and the rest of the app is unaffected |

## F. Estimated resource requirements

- **Code**: ~3,500 new lines of Python (engines + schemas + tests), bringing the project to ~7,400 lines.
- **Tests**: ~80 new tests, bringing the total to ~154.
- **New DB tables**: 10 (`reference_ranges`, `measurement_validations`, `datasheet_params`, `power_sequences`, `power_sequence_nodes`, `hypotheses_v2`, `nets`, `pins`, `connections`, `benchmark_cases`, `repair_cases`).
- **New dependencies**: none required for P0-P6. P7 (SPICE) adds an optional `PySpice` + ngspice dependency, lazily imported and never required.
- **RAM**: engines are pure functions; the only new RAM cost is the in-memory graph for topology (bounded by the board, typically <1 MB). SPICE, when used, runs in a subprocess.
- **Disk**: new tables add <1 MB for typical use.

## G. Tests required to verify each milestone

| Milestone | Tests |
|-----------|-------|
| P0 | `test_schemas.py` (Quantity arithmetic, unit conversion, dimensional analysis, component/pin/net IDs, EngineResult envelope, error envelope); `test_engines_base.py` (registry, versioning, routing, invalid-input rejection); `test_validation.py` (machine-readable report: totals/passed/failed/tolerance/env/version) |
| P1 | `test_electrical_math.py` (Ohm, Kirchhoff, series/parallel, power, with normal/boundary/invalid/extreme inputs; reference answers computed independently in the test, never by the engine); `test_measurement_validation.py` (range comparison, contradiction detection, operating-condition separation, missing-context surfacing) |
| P2 | `test_datasheet.py` (extraction of pinout/ratings/thresholds from a sample PDF, provenance fields, ambiguity flagging); `test_knowledge_base.py` extended for the 6 tiers |
| P3 | `test_power_sequence.py` (configurable graph, G3/S5/S4/S3/S0, missing-prerequisite detection, sequence-violation detection); `test_hypotheses.py` (explicit statuses, probability basis, required evidence, confirmation gate stronger than the rule engine's) |
| P4 | `test_topology.py` (graph build, net membership, rail-origination query, open/short reasoning, honest "not parsed" for unsupported formats) |
| P5 | `test_next_best_test.py` (information-gain selection across competing hypotheses; safety filter integration; expected-result-only-when-supported) |
| P6 | `test_benchmark.py` (blind cases, held-out separation, metrics); `test_learning.py` (technician-confirmed-only ingestion, versioning, reversibility) |
| P7 | `test_spice.py` (DC/transient/AC when ngspice present; graceful unavailable when absent) |

## Compatibility contract

- The 74 existing tests must remain green at every milestone.
- The existing CLI commands (`new`, `open`, `sessions`, `symptom`, `measure`,
  `analyze`, `next-test`, `add-file`, `documents`, `search`, `notes`,
  `report`, `remove-document`, `settings`, `exit`) keep their output shape.
- New commands (`engine`, `engines`, `validate`, `topology`, `sequence`,
  `benchmark`) are additive.
- The `analyze` command's text output is preserved; the new hypothesis
  manager is reached via `analyze --v2` first, then made default only after
  its tests show it dominates the rule engine on the blind benchmark.
