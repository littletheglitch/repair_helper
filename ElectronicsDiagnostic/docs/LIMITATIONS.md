# Limitations and Unimplemented Features

This document is honest about what the Electronics Diagnostic Assistant
**does** and **does not** do. Nothing is claimed to work that has not been
implemented and tested.

## Implemented and tested

| Capability                                           | Tests                               |
|-----------------------------------------------------|-------------------------------------|
| SQLite schema + FTS5 initialization                 | test_database.py                    |
| Session persistence, list, close, cascade delete    | test_sessions.py                    |
| Measurement parsing, normalization, persistence    | test_measurements.py                |
| Document import, SHA-256, duplicate detection       | test_documents.py                   |
| PDF selectable-text extraction + chunking + FTS5    | test_documents.py                   |
| Path-with-spaces handling                            | test_documents.py, test_cli.py      |
| Knowledge-base loading (28 required topics)         | test_knowledge_base.py              |
| Keyword search over KB + documents                   | test_retrieval.py                    |
| Evidence-based reasoning, sample case               | test_reasoning.py                    |
| Safety rules (forbidden + warning actions)          | test_reasoning.py                    |
| Document-request manager with justification         | test_reasoning.py                    |
| Markdown report with all required sections          | test_reporting.py                    |
| LLM engine: missing model / runtime / disabled      | test_llm_engine.py                   |
| CLI commands + natural-language fallback            | test_cli.py                          |
| Offline startup                                      | test_cli.py                          |

74 tests, all passing in this build environment (Linux, Python 3.12).

## Implemented but optional / off by default

- **PDF report generation** via ReportLab: implemented; optional
  dependency (`pip install -e ".[pdf]"`). The Markdown report is always
  available. `test_reporting.py::test_pdf_report_optional` tolerates
  the absence of reportlab.
- **OCR for scanned PDFs and images**: the architecture supports it (a
  `ocr_enabled` setting and an `ocr_recommended` flag in the document
  metadata), but no OCR engine is wired in by default. To enable, install
  `pytesseract` and a Tesseract binary, set
  `settings.documents.ocr_enabled = true`, and add an OCR pass in
  `eda/documents.py:_extract_pdf`. Without that, scanned PDFs are flagged
  `partially_extracted` (or `stored_only`) with a clear note recommending
  OCR — the assistant does **not** pretend to have read them.

## Not implemented (explicitly)

The following are **not** implemented and the application says so
plainly when they come up, rather than silently faking them:

- **Binary BoardView parsing** (`.fz`, `.bvr`, `.brd`): stored with hash
  and size; the assistant tells the user the format is not parsed and
  asks for a screenshot or an ASCII export. ASCII `.asc`/`.brd2` are
  parsed as text. `test_documents.py::test_boardview_binary_stored_only`
  asserts this honesty.
- **BIOS/firmware semantic analysis**: stored with hash and size; the
  assistant explicitly says no semantic parser is implemented.
  `test_documents.py::test_bios_stored_only` asserts this honesty.
- **Schematic netlist understanding**: vector-PDF text and component
  references are extracted; the assistant does **not** claim to
  understand the circuit from the image. When pin mapping is needed it
  requests the schematic and uses the pin numbers the user reports.
- **Forum/community ingestion pipeline**: the built-in knowledge base
  contains concise, rewritten summaries of technician-reported
  experience with provenance and confidence labels. There is no
  automated scraper. A `knowledge_base.load_builtin()` mechanism exists
  for future curated JSON datasets.
- **Vector retrieval / embeddings**: intentionally not used. On an 8 GB
  CPU-only target a keyword + reranking approach is faster and
  deterministic. The retrieval module uses SQLite FTS5 with a LIKE
  fallback.

## What was actually tested in this build environment

- **Platform**: Linux x86_64, Python 3.12, SQLite 3.53 with FTS5.
- **Tests**: 74 pass (`python -m pytest tests/ -q`).
- **CLI smoke**: `python -m eda help` and the sample-case flow work.
- **PDF extraction**: a tiny generated PDF round-trips through PyMuPDF
  into chunks and is found by FTS5 search.
- **ReportLab PDF report**: optional; the Markdown report is always
  available.
- **End-to-end CLI session**: create → symptom → measure → analyze →
  next-test → add-file → report verified by `tests/test_cli.py`.

## What was NOT tested in this build environment

- **The Windows EXE**: `scripts/build_windows.bat` and
  `scripts/eda.spec` are provided and follow the standard PyInstaller
  onefolder pattern, but PyInstaller is not available in this Linux
  sandbox, so the EXE was not built here. The build instructions in
  `docs/INSTALL.md` are reproducible on a Windows machine with Python
  3.10+ and pip. The Python entry point (`python -m eda`) is identical
  to the EXE's entry point, so the behaviour tested here is the
  behaviour the EXE will have.
- **llama.cpp inference**: `eda/llm_engine.py` invokes `llama-cli` as a
  subprocess; the wrapper is tested for graceful degradation when the
  model or runtime is missing (`tests/test_llm_engine.py`). A live
  inference call against a real GGUF was not exercised in this sandbox
  because no model file is present; the wrapper's subprocess invocation
  follows the documented `llama.cpp` CLI contract.
- **Hardware-specific performance**: the 6th-gen i7 / 8 GB target was not
  available for benchmarking. The defaults (`n_ctx=2048`, 4 threads,
  1.5B Q4_K_M) are chosen to fit the target conservatively; see
  `docs/MODEL_PREPARATION.md` for tuning guidance.

## Known limitations of the rule engine

The reasoning engine is deterministic and topology-aware, but it is
not magic:

- It reasons about the **generic laptop power architecture**
  (always-on rails, run rails, EC, VRM, BIOS/SPI) and a set of common
  fault patterns. It cannot know board-specific rail names or pin
  numbers without the schematic; when it needs them, it asks.
- It will not "confirm" a diagnosis until it has at least two
  independent supporting measurements and no contradicting evidence
  above the threshold. On incomplete evidence it always asks for more.
- It does not invent voltage values. Expected values are given only
  when supported by a generic rule (e.g. "+3VALW ~3.3 V") or are
  qualified as "typical" with provenance. Part-specific thresholds
  require the datasheet, which the engine requests when needed.

---

# Phase 2 limitations (additional)

## Implemented and tested in Phase 2
- Common schemas with SI dimensional analysis (Quantity).
- Tool Router with versioned, schema-validated, structured-error APIs.
- 7 computational engines (electrical_math, measurement_validation,
  datasheet, power_sequence, hypotheses, topology, next_best_test) +
  optional SPICE (with pure-Python DC fallback).
- Engine validation framework with machine-readable reports.
- 6-tier knowledge provenance.
- Blind benchmark + controlled technician-confirmed learning workflow.
- DB migrations + integrity/backup/restore/recover.
- 286 tests, all passing.

## Not implemented / honest about it (Phase 2)
- **OCR** still optional and off by default (unchanged from Phase 1).
- **Binary BoardView parsing / PDF schematic understanding / BIOS semantic
  analysis**: still NOT implemented. The topology engine says so plainly
  and asks for a screenshot or ASCII netlist export. It never claims to
  parse an image or binary file into a circuit graph.
- **Datasheet extraction is keyword+regex**: it finds clearly-formatted
  parameter rows and classifies them by section heading. Ambiguous rows
  are marked `verification_status='unverified'`. It does NOT parse
  application-circuit diagrams or functional block diagrams.
- **Power-sequence default model is generic Intel-platform**: real
  boards define their own via `engine power_sequence save_sequence`.
  The engine does not infer a specific rail voltage or signal polarity
  without supporting documentation; nominal voltages are display-only.
- **Hypothesis probabilities are qualitative**: the spec forbids
  fabricated numerical probabilities without a calibrated statistical
  basis. The hypothesis manager reports a qualitative `confidence_basis`
  ("N supporting; M contradicting; K missing") and never a number.
- **Next-best-test catalog is curated**: the candidate tests are
  board-generic. Board-specific tests require the topology + datasheet
  engines to be populated.
- **SPICE is optional**: ngspice + PySpice are not bundled. Without them,
  only a pure-Python linear DC fallback (MNA, one voltage source, no
  transistors/transient/AC) is available. The engine reports UNAVAILABLE
  honestly and the fallback is clearly marked as not-ngspice. PySpice is
  GPLv3 and ngspice is BSD-3; check their licenses before bundling.
- **Blind benchmark is small (4 built-in cases)**: it is a demonstration
  of the methodology, not a statistically significant evaluation. The
  dev/held-out split is honest (held-out never used for tuning).
- **Windows EXE not built in this sandbox**: PyInstaller unavailable
  here; the Python entry point tested is the EXE's entry point.

## What Phase 2 deliberately does NOT do
- The LLM is never allowed to alter a deterministic engine's returned
  value. It receives `result.render()` and may only paraphrase.
- The LLM may never convert its own answers into verified knowledge.
  Only technician-confirmed cases enter the verified KB.
- The application never scans the user's drives (unchanged from Phase 1).
- The application never depends on internet during normal operation
  (unchanged from Phase 1).
