# Architecture

## Module map

```
src/eda/
  config.py            paths + settings (portable, no network)
  database.py          SQLite schema + FTS5 + helpers (thread-local)
  knowledge_base.py    loads Knowledge/*.json -> knowledge_facts
  sessions.py          repair sessions (create/open/list/close/notes/symptoms/timeline)
  measurements.py      parse "measure NAME = VALUE UNIT [@ point]" + persist
  documents.py         format-aware import: PDF/text/BoardView/BIOS/image
                       SHA-256 dedup, chunking, FTS5 indexing, status tracking
  retrieval.py         on-demand keyword search over KB + documents
  document_requests.py explicit, justified single-file document requests
  reasoning.py         evidence-based diagnostic engine (the heart)
  safety.py            forbidden/warning action filters + caution generator
  llm_engine.py        llama.cpp / OpenAI-API wrapper, graceful fallback
  reporting.py         Markdown + optional ReportLab PDF report
  cli.py               16 commands + natural-language fallback + REPL
  __main__.py          console-script entry point
```

## Data flow on `analyze`

```
User input -> cli.dispatch("analyze")
  -> sessions.symptoms(sid), measurements.list_for(sid)
  -> reasoning.analyze(sid, symptoms, measurements)
       1. measurements.identify_missing_context (adapter/ref/board-state gaps)
       2. retrieval.search_knowledge(symptom keyword) for KB context
       3. _build_hypotheses: 7 rule families, each with
          supporting / contradicting / missing lists + score
       4. _choose_next_test: topology-aware priority ladder
            VIN -> always-on rails -> resistance on dead rail
            -> ACOK (+ schematic request) -> run rails (+ VRM datasheet)
            -> resistance sweep
       5. safety.check_action on the next test; attach caution
       6. _confirmable: requires >=2 supporting, 0 contradicting above 0.4
       7. _persist: hypotheses, missing_evidence, next_tests, timeline
  -> llm_engine.paraphrase_analysis (optional; None if no model)
  -> Analysis.to_text() printed to the user
```

## Evidence categories (per spec)

Every session maintains, in SQLite:

- **Measured facts**: `measurements` table (name, value, unit, point,
  reference, board/adapter/battery state, note, taken_at). Never
  overwritten; duplicates allowed for comparison.
- **Hypotheses**: `hypotheses` table (rank, fault, rationale,
  supporting, contradicting, confidence). Cleared and re-derived on
  every `analyze` so the ranking always reflects current evidence.
- **Missing evidence**: `missing_evidence` table.
- **Next test**: `next_tests` table (test, where, reference, expected,
  rationale, priority).

Plus a `timeline` table that records every event for the report.

## Document handling — privacy by design

```
User: add-file "D:\path with spaces\schematic.pdf"
  -> cli.cmd_add_file -> shlex.split (preserves quoted paths)
  -> documents.import_file(path, session_id)
       1. resolve + verify exists
       2. SHA-256 + size
       3. duplicate check by SHA-256 (link to session if dup)
       4. detect_file_type by extension
       5. copy into Documents/<sha[:12]>_<safe_name>
       6. extract:
            pdf:  PyMuPDF (fallback pypdf); detect scanned
            text: multi-encoding fallback
            boardview ASCII: parsed as text; binary: stored_only
            bios: stored_only + hash (no semantic analysis)
            image: stored_only; OCR optional
       7. chunk_text (page markers, 1000/100 overlap)
       8. insert document_chunks (trigger updates chunks_fts)
       9. status: received/extracted/partially_extracted/stored_only/failed
```

The application **never** scans the user's drives. Only files the user
explicitly submits with `add-file` are processed.

## LLM engine — graceful degradation

```
cli.cmd_analyze -> llm_engine.paraphrase_analysis(analysis_text, symptoms)
  -> LLMEngine.is_available?
       backend disabled        -> False
       backend llama_cpp       -> model_path() and cli_path() both non-None?
       backend openai_api      -> api_base set?
  -> if not available: return None  (CLI uses rule-engine text)
  -> if available: generate (subprocess for llama_cpp, urllib for openai_api)
  -> on any exception: return None  (CLI uses rule-engine text)
```

The LLM is **only** a paraphraser. Its system prompt forbids inventing
measurements, pin numbers, voltage values, or component names. The rule
engine is the source of truth.

## RAM and storage budget on the 8 GB target

- Knowledge base: ~28 KB JSON, ~150 facts, fully in SQLite. <10 MB RAM.
- Document library: chunked + FTS5; on-demand retrieval. The full
  archive is never in RAM.
- Model: 1.5B Q4_K_M GGUF, ~1.0 GB on disk, ~1.5–2 GB RAM at n_ctx=2048.
- KV cache: limited by `n_ctx=2048`.
- mmap: `llama.cpp` mmaps the model by default; only the active pages
  are resident.

Total steady-state RAM with the model loaded: ~2.5–3 GB, leaving ~5 GB
for the OS and the user's other tools on the 8 GB target.

---

# Phase 2 — Modular Engineering Computation Architecture

Phase 2 adds a layer of independent computational engines controlled by
a Tool Router, on top of the Phase 1 rule engine (preserved as one
evidence source). The LLM is an orchestrator and paraphraser; it never
alters a deterministic engine's returned value.

## Module map (Phase 2 additions)

```
src/eda/
  schemas.py            Quantity (SI base + prefixes + dimensional analysis),
                        ComponentRef/PinRef/NetRef, EngineResult/EngineError
  engines/
    base.py             Engine base class + registry (Tool Router)
    electrical_math.py  Ohm, Kirchhoff, series/parallel, dividers, power
    measurement_validation.py  range comparison, contradiction detection
    datasheet.py        structured datasheet parameter extraction
    power_sequence.py   per-board power-sequence dependency graph (G3-S0)
    hypotheses.py       structured hypothesis manager with 6 statuses
    topology.py         circuit graph (components/pins/nets)
    next_best_test.py   information-gain next-test selection
    spice.py            optional ngspice integration + pure-Python DC fallback
  validation.py         engine validation framework (machine-readable reports)
  migrations.py         schema versioning + integrity/backup/restore/recover
  benchmark.py          blind diagnostic benchmark
  learning.py           controlled technician-confirmed learning workflow
```

## Tool Router contract

Every engine returns an ``EngineResult`` envelope:

```
engine, version, method, inputs, value, value_si, unit,
formulas, assumptions, limitations, provenance, error, metadata
```

- The LLM receives ``result.render()`` and may paraphrase; it must not
  alter ``value`` or ``value_si``.
- Errors are structured (``EngineError`` with ``code``, ``message``,
  ``field``); engines never raise.
- Inputs are validated against the engine's declared ``input_schema``
  before dispatch; missing or wrong-typed inputs return ``missing_input``
  / ``invalid_input`` errors.

## Engine validation framework

``eda.validation.run_cases(engine_name, cases)`` produces a
machine-readable ``Report`` with: total, passed, failed, max_error,
tolerance, environment, engine version, and per-case details. Reference
answers are computed independently in the tests, never by the engine.

## 6-tier knowledge provenance

``knowledge_facts.tier`` is one of:
``manufacturer-spec``, ``verified-reference``, ``technician-confirmed``,
``community``, ``unverified``, ``general-principle``.
``search_facts(query, tier=...)`` filters by tier; the reasoning engines
prefer higher-authority tiers when relevant.

## Hypothesis statuses

``confirmed`` / ``strongly_supported`` / ``plausible`` / ``uncertain`` /
``contradicted`` / ``rejected``. Confirmation requires >=3 supporting,
zero contradicting, and zero required-evidence — stronger than the
Phase 1 rule engine's gate. A hypothesis is never confirmed just because
it is the highest-ranked.

## Migration layer

``init_db`` runs ``migrations.migrate()`` after creating the base schema.
``PRAGMA user_version`` tracks the schema version. ``db integrity``,
``db backup``, ``db recover``, ``db version`` CLI commands expose
integrity checks and recovery. Migrations are idempotent; down-migrations
are documented limitations (SQLite < 3.35 cannot DROP COLUMN).
