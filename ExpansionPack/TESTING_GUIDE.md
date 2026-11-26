# Testing Guide

## Test suites

The pack ships two test suites:

### `Tests/run_all_tests.py` — original suite (33 tests)
Covers the original component database, condition evaluator, engine
workflow, the 12 common failure scenarios, sample cases, and pack
independence. Run with:
```bash
python Tests/run_all_tests.py
```

### `Tests/run_expanded_tests.py` — expanded suite (85 tests)
Covers the Core Diagnostic Framework, safety policy, 18 motherboard
scenarios, power-supply engine, circuit analysis, component
identification, replacement engine, measurement intelligence, failure
pattern DB, knowledge platform, LLM orchestrator, host adapter,
resource benchmarks, and regression of the original suite. Run with:
```bash
python Tests/run_expanded_tests.py
```

## Validators

### `Tools/validate_database.py`
Checks JSON validity, schema compliance, duplicate records, missing
mandatory fields, invalid electrical values, broken internal references,
replacement compatibility evidence, and source attribution.
```bash
python Tools/validate_database.py    # exit 0 = clean
```

### `Tools/validate_rules.py`
Checks JSON validity, schema compliance, condition expression parsing,
next_rule reference resolution, rail-family reference resolution, and
per-rail rule coverage.
```bash
python Tools/validate_rules.py       # exit 0 = clean
```

## Summary exporter

### `Tools/export_summary.py`
Walks the pack, counts records, runs both validators, and writes
`download/summary.json` + `download/summary.md`.
```bash
python Tools/export_summary.py
```

## Test categories (per spec)

The expanded suite covers:
- **Unit tests** — framework data classes, condition evaluator.
- **Schema validation** — via the validators.
- **Database integrity tests** — duplicate detection, broken refs.
- **Diagnostic rule tests** — each rule's fired-condition is asserted.
- **Integration tests** — host adapter dispatches to all 7 engines.
- **Regression tests** — original 12 scenarios re-run via the bridge.
- **Offline execution tests** — no network calls; pure stdlib.
- **Safety tests** — mains/charged-cap/bypass/injection/powered-
  resistance all blocked.
- **Resource benchmarks** — engine load + diagnose timing.
- **Independent engineering reference cases** — the 18 motherboard
  scenarios assert the engineering-correct fired rule, not just
  no-crash.

## Test design rules

- A test that merely confirms no exception occurred is insufficient.
  Every scenario test asserts the specific fired rule ID and/or the
  next-step action content.
- Missing-data tests verify that NOT_MEASURED is never treated as zero.
- Contradiction tests verify that the hypothesis engine downgrades
  confidence when contradicting evidence appears.
- Unknown-input tests verify that the engine returns `requires_info`
  rather than guessing.

## Continuous integration

Run all four commands as a CI gate:
```bash
python Tools/validate_database.py && python Tools/validate_rules.py && python Tests/run_all_tests.py && python Tests/run_expanded_tests.py
```
All must exit 0.
