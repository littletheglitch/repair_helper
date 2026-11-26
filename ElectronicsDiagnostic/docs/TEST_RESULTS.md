# Test Results

## Summary

```
platform linux -- Python 3.12.16
pytest-9.0.2
collected 74 items
74 passed in 0.58s
```

All 74 automated tests pass in this build environment (Linux x86_64,
Python 3.12, SQLite 3.53 with FTS5).

## Test files and what they cover

| File                        | Tests | Required coverage                                                |
|-----------------------------|-------|------------------------------------------------------------------|
| tests/test_database.py      | 5     | Database initialization, FTS5 availability, FK on, db path        |
| tests/test_sessions.py      | 7     | Session persistence, list, close/reopen, symptoms, notes, timeline, cascade |
| tests/test_measurements.py  | 8     | Measurement parsing, normalization, persistence, no-overwrite, context gaps |
| tests/test_documents.py     | 11    | Import, SHA-256, duplicate detection, PDF extraction, BoardView stored-only, BIOS stored-only, indexing, search, removal, path-with-spaces, missing-file, status |
| tests/test_retrieval.py     | 4     | Knowledge search, case-insensitive, combined search, empty query  |
| tests/test_knowledge_base.py| 6     | Load, idempotent, force-reload, categories, provenance, all 28 required topics present |
| tests/test_reasoning.py     | 10    | Sample case (no premature diagnosis), next-test ladder, schematic request, persistence, safety forbidden + warning + caution, document-request render, no-invented-pin-numbers |
| tests/test_reporting.py     | 3     | Markdown required sections, save, optional PDF                    |
| tests/test_llm_engine.py    | 5     | Missing model, missing runtime, disabled backend, availability report, paraphrase returns None |
| tests/test_cli.py           | 15    | help, new/open/sessions, symptom+measure+analyze flow, NL fallback, NL measurement, add-file with spaces, documents, remove-document, report, search, settings, unknown command, offline startup, one-shot main |

## How to reproduce

```bash
cd ElectronicsDiagnostic
python3 -m venv build/venv
source build/venv/bin/activate     # Windows: build\venv\Scripts\activate
pip install -e ".[test,pdf]"
python -m pytest tests/ -v
```

## Sample case (the spec's example)

The spec mandates a sample test case:

> Laptop motherboard has no power.
> Adapter input = 19V.
> +3VALW = 3.3V.
> +5VALW = 0V.
> The system should identify that the evidence is insufficient to declare
> a failed component and request the next useful test based on the
> available topology.
> Do not fabricate a successful test result.

This case is asserted by `tests/test_reasoning.py::test_sample_case_no_power`.
Running the engine on it manually (see `docs/ARCHITECTURE.md` and the
README's example session) yields, in order:

1. **First `analyze`** with VIN=19V, +3VALW=3.3V, +5VALW=0V:
   - Top hypothesis: "Adapter input path open" (score 0.60)
   - "Always-on converter failure" (0.45) with +3VALW contradicting
   - "Short on a power rail" (0.35) with missing resistance-to-ground
   - "EC held off / missing ACOK" (0.25) with missing ACOK
   - **confirmed_diagnosis = None** (the engine does NOT declare a failed component)
   - **next-test = resistance to ground on the dead +5VALW rail**

2. After the user adds `measure resistance_to_ground = 480ohm` (normal),
   the second `analyze` escalates to:
   - **next-test = Measure ACOK/ACIN** at the EC pin
   - **needs_document = the schematic**, with the exact fields the spec
     example lists (EC ACOK/ACIN pin, charger ACOK pin, EN/PGOOD pins).
     The render matches the spec's TPS51716 example request style.

3. Safety cautions are emitted on both steps: resistance cautions on the
   first (power off, discharge caps), live-measurement caution on the
   second (one hand away, stated ground reference).

No measurement, pin number, or voltage value was invented by the engine.
The schematic request is only made after the cheaper measurements were
obtained and shown to be insufficient — exactly as the spec requires.

## Honest gaps

- The Windows EXE was not built in this Linux sandbox (PyInstaller is
  not installed here). The Python entry point used by the tests is the
  same entry point the EXE uses (`eda/__main__.py` → `eda.cli:main`), so
  the behaviour tested here is the behaviour the EXE will have. The
  build is reproducible with `scripts/build_windows.bat` on a Windows
  machine.
- A live `llama.cpp` inference call against a real GGUF was not executed
  here (no model file in this sandbox). The LLM wrapper is tested for
  graceful degradation when the model or runtime is missing; the
  subprocess invocation follows the documented `llama.cpp` CLI contract.
- Hardware-specific performance on the 6th-gen i7 / 8 GB target was not
  benchmarked. Defaults are conservative; see `docs/MODEL_PREPARATION.md`.
