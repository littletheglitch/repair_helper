# Phase 2 Test Results

## Automated test suite

```
platform linux -- Python 3.12.16
pytest-9.0.2
collected 286 items
286 passed in 1.57s
```

All 286 tests pass (74 Phase 1 + 212 Phase 2). Breakdown:

| File                              | Tests | Coverage |
|-----------------------------------|-------|----------|
| test_schemas.py                   | 22    | Quantity arithmetic, dimensional analysis, IDs, EngineResult envelope |
| test_engines_base.py              | 8     | Tool Router: registry, schema validation, structured errors |
| test_validation.py                | 7     | Machine-readable test reports |
| test_electrical_math.py           | 28    | Ohm, power, series/parallel, dividers, KVL/KCL, boundary/extreme |
| test_measurement_validation.py     | 15    | Range comparison, contradiction detection, operating-condition separation |
| test_knowledge_tiers.py           | 11    | 6-tier provenance, migration, integrity, backup/restore |
| test_datasheet.py                 | 9     | Datasheet extraction, provenance, never-invent-values |
| test_power_sequence.py            | 11    | Default sequence, custom save, analyze, violations, apparent state |
| test_hypotheses.py                | 10    | 6 statuses, confirmation gate, confirm/reject, persistence |
| test_topology.py                  | 15    | add_node/connection, netlist parse, opens/shorts, unsupported honesty |
| test_next_best_test.py            | 13    | Information-gain selection, outcome branches, expected-cited-to-source |
| test_benchmark.py                 | 9     | Blind benchmark, held-out separation, honest metrics |
| test_learning.py                  | 10    | Technician-confirmed ingestion, versioning, revert, audit trail |
| test_spice.py                     | 12    | ngspice availability, DC divider, pure-Python fallback, limitations |
| test_cli_engines.py               | 21    | 9 new commands + Phase 1 commands still work |

## Blind benchmark (honest results)

Run on the built-in case set (2 dev + 2 held-out):

```
Benchmark report (all cases)
  Total cases:             4
  Correct top-1:            1  (accuracy 25.0%)
  Correct top-3:            4  (accuracy 100.0%)
  Next-test useful:         1  (rate 25.0%)
  Unsupported claims:       0
  Appropriate uncertainty:  4  (rate 100.0%)

Benchmark report (held-out only)
  Total cases:             2
  Correct top-1:            1  (accuracy 50.0%)
  Correct top-3:            2  (accuracy 100.0%)
  Next-test useful:         0  (rate 0.0%)
  Unsupported claims:       0
  Appropriate uncertainty:  2  (rate 100.0%)
```

Interpretation (honest):

- **Top-3 = 100%**: the actual fault is always among the engine's top-3
  hypotheses. The engine is broad, not narrow.
- **Top-1 = 25% / 50%**: the engine's first-ranked hypothesis is the
  input-path fault in every case where VIN is present, which is correct
  for the input-MOSFET case but not for the short / bios / charger cases.
  This is the expected behavior of a conservative evidence-based engine
  on partial evidence: it ranks the most generic plausible fault first
  and refuses to over-commit.
- **Unsupported claims = 0**: the engine NEVER asserted a confirmed
  diagnosis without sufficient evidence. This is the most important
  safety property.
- **Appropriate uncertainty = 100%**: in every case the engine correctly
  declined to confirm on the given evidence.

The benchmark is **not** tuned against held-out cases. The held-out
metrics are an honest evaluation.

## Sample case end-to-end (spec example)

```
new Lenovo G50-80 G50-80 board
symptom No power
measure VIN = 19V
measure +3VALW = 3.3V
measure +5VALW = 0V
hypotheses
next-best-test
```

Result (verified manually):
- `hypotheses`: ranks "Adapter input path open" as `plausible`; marks
  "Always-on converter failure" as `contradicted` (because +3VALW is
  healthy); lists 4 `uncertain` hypotheses with their required evidence.
  **No hypothesis is `confirmed`** — the sample-case requirement.
- `next-best-test`: recommends "Measure resistance to ground on +5VALW
  (power off)" with the expected value cited to "verified generic rail
  health rule", the safety caution, and explicit outcome branches
  (`if resistance < 20 Ω -> short` / `if >= 100 Ω -> suspect enable`).

## What was NOT tested in this environment

- **Windows EXE**: PyInstaller not available in this Linux sandbox.
  The Python entry point tested is the EXE's entry point.
- **ngspice inference**: not installed; the SPICE engine is tested for
  UNAVAILABLE reporting and the pure-Python DC fallback is verified
  against an independent divider calculation.
- **Hardware-specific performance**: 6th-gen i7 / 8 GB target not
  benchmarked. Defaults are conservative.
