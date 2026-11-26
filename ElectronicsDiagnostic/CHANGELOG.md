# Changelog

All notable changes to the Electronics Diagnostic Assistant are documented
here. The format is based on Keep a Changelog.

## [1.0.0] — 2026-11-23

### Added — Milestone 1 (CLI, database, sessions, measurements)
- `eda.config` — portable path resolution and settings with defaults.
- `eda.database` — SQLite schema with FTS5 over document chunks and knowledge
  facts; triggers keep FTS in sync; thread-local connections; WAL journal.
- `eda.sessions` — create, open, list, close, reopen, notes, symptoms,
  timeline; cascade delete.
- `eda.measurements` — free-text `measure NAME = VALUE UNIT [@ point]`
  parsing, normalization, persistence (never overwrites previous entries),
  context-gap identification.
- `eda.cli` — REPL with 16 explicit commands plus natural-language fallback.

### Added — Milestone 2 (local LLM integration)
- `eda.llm_engine` — three backends: `llama_cpp` (subprocess to `llama-cli`),
  `openai_api` (local OpenAI-compatible server), and `disabled`. Honest
  `availability_report`; graceful `None` fallback when the model or runtime
  is missing. The LLM only paraphrases the rule engine's analysis; it cannot
  invent facts (the prompt forbids it and the rule engine is the source of
  truth).
- `scripts/prepare_model.py` — one-time, confirmation-gated download of a
  Q4_K_M GGUF model into `Models/`, with SHA-256 verification.

### Added — Milestone 3 (document import, extraction, search)
- `eda.documents` — format-aware import: PDF (PyMuPDF with pypdf fallback),
  plain text, BoardView (ASCII `.asc`/`.brd2` parsed; binary `.fz`/`.bvr`
  stored with an honest "not parsed" note), BIOS/firmware (stored with
  SHA-256 and size; explicitly not analyzed), images (stored; OCR is an
  optional capability). SHA-256 dedup; chunking with page markers; FTS5
  indexing; status tracking; removal.
- `eda.retrieval` — on-demand FTS5 keyword search over the knowledge base
  and the document library, with a LIKE fallback. Vector retrieval is
  intentionally not used (deterministic, low-RAM).

### Added — Milestone 4 (evidence-based reasoning + document requests)
- `eda.reasoning` — the diagnostic engine. Transparent hypothesis scoring,
  supporting/contradicting/missing lists, persistence to the session, and a
  topology-aware next-test chooser that mirrors a real diagnostic flow
  (VIN → always-on rails → resistance on a dead rail → ACOK/schematic request
  → run rails → BIOS). Confirmed diagnosis requires ≥2 supporting and zero
  contradicting above the threshold.
- `eda.safety` — forbidden-action regex filter and live-measurement caution
  generator. The engine runs every next-test through the safety filter.
- `eda.document_requests` — explicit, justified single-file requests with
  the fields the engine wants to extract; the user must submit the file.

### Added — Milestone 5 (reports, testing, packaging)
- `eda.reporting` — Markdown report with all required sections (symptoms,
  measurements, timeline, hypotheses + ranking, supporting/contradicting,
  missing information, next tests, documents, confirmed vs probable
  diagnosis) and optional ReportLab PDF.
- `tests/` — 74 automated tests covering every required component.
- `scripts/eda.spec` — PyInstaller onefolder spec; `scripts/build_windows.bat`
  — Windows build script; `scripts/build_dev.sh` — Linux/macOS dev script.
- `docs/` — INSTALL, MODEL_PREPARATION, OFFLINE_USAGE, LIMITATIONS,
  ARCHITECTURE.
- `Knowledge/*.json` — curated built-in knowledge covering all the topics
  the specification lists, with provenance and confidence labels.

### Notes / limitations carried forward
- OCR is an optional capability and is off by default.
- Binary BoardView formats and BIOS semantic analysis are **not**
  implemented; files are stored with hashes and a clear "not parsed" note.
- The model GGUF and the `llama.cpp` runtime are deliberately not bundled
  with the source tree (too large, platform-specific). Use
  `scripts/prepare_model.py` once and drop a `llama.cpp` build into
  `Engine/`.
- The application was developed and its 74 tests pass on Linux. The
  Windows EXE is built with the provided `scripts/build_windows.bat` on a
  Windows machine; see `docs/LIMITATIONS.md` for what was actually tested
  in this build environment.

## [2.0.0] — 2026-11-23 (Phase 2)

Phase 2 adds a modular engineering computation architecture on top of
Phase 1. See docs/CHANGELOG_PHASE2.md for the full list. Highlights:

- 7 independent computational engines (electrical_math, measurement_
  validation, datasheet, power_sequence, hypotheses, topology,
  next_best_test) + optional SPICE, behind a Tool Router with versioned,
  schema-validated, structured-error APIs.
- Common schemas (Quantity with SI dimensional analysis; typed IDs;
  EngineResult envelope).
- 6-tier knowledge provenance; structured hypothesis manager with 6
  explicit statuses; configurable per-board power-sequence engine;
  circuit topology graph (honest about unsupported formats); information-
  gain next-best-test engine; blind benchmark; controlled technician-
  confirmed learning; DB migration + integrity + recovery.
- 212 new tests; 286 total, all passing. All 74 Phase 1 tests remain
  green; existing CLI commands unchanged.
