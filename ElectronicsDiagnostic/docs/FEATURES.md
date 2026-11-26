# Implemented Features and Known Limitations

## Implemented features

### Milestone 1 — CLI, database, sessions, measurements
- ✅ Portable path resolution; all data stays inside the app root.
- ✅ SQLite schema with FTS5 over document chunks and knowledge facts;
  WAL journal; foreign keys on; thread-local connections.
- ✅ Repair sessions: create, open, list, close, reopen, notes, symptoms,
  full timeline; cascade delete on session removal.
- ✅ Measurement manager: free-text `measure NAME = VALUE UNIT [@ point]`
  parsing with unit normalization (V, mV, A, mA, µA, Ω, kΩ, Hz, etc.);
  never overwrites previous entries; identifies context gaps (adapter
  state, reference point, board state for resistance).
- ✅ CLI with 16 explicit commands plus natural-language fallback
  (a line with `=` and a digit → measurement; any other line → symptom).

### Milestone 2 — Local LLM integration
- ✅ LLM engine with three backends: `llama_cpp` (subprocess to
  `llama-cli`), `openai_api` (local OpenAI-compatible server),
  `disabled`.
- ✅ Honest availability report (model file, runtime, available flag).
- ✅ Graceful `None` fallback when the model or runtime is missing; the
  CLI uses the rule engine's text unchanged.
- ✅ The LLM is only a paraphraser; its system prompt forbids inventing
  facts. The rule engine is the source of truth.
- ✅ One-time, confirmation-gated model preparation script with SHA-256
  verification; recommends a 1.5B Q4_K_M GGUF for the 8 GB CPU-only target.

### Milestone 3 — Document import, extraction, search
- ✅ Format-aware import: PDF (PyMuPDF + pypdf fallback), plain text
  (multi-encoding), BoardView (ASCII parsed, binary stored-only), BIOS
  (stored-only with hash, no semantic analysis), images (stored, OCR
  optional), unknown extension (best-effort text).
- ✅ SHA-256 hashing and duplicate detection by content.
- ✅ Chunking with page markers and 1000/100 overlap; FTS5 indexing.
- ✅ Status tracking: received / extracted / partially_extracted /
  stored_only / failed.
- ✅ On-demand keyword search over the document library and the
  knowledge base, with FTS5 snippet highlighting and a LIKE fallback.
- ✅ Scanned-PDF detection (text-per-page ratio).
- ✅ The application NEVER scans the user's drives; only files the user
  explicitly submits are processed.
- ✅ `remove-document` deletes a file and its chunks and FTS entries.

### Milestone 4 — Evidence-based reasoning + document requests
- ✅ Diagnostic reasoning engine with transparent hypothesis scoring
  (supporting +0.2, contradicting −0.25, missing −0.05, base per rule).
- ✅ Seven rule families: input path, always-on converter, EC enable,
  shorted rail, out-of-regulation rail, BIOS/SPI, charger/battery.
- ✅ Topology-aware next-test ladder:
  VIN → always-on rails → resistance on dead rail → ACOK (+ schematic
  request) → run rails (+ VRM datasheet request) → resistance sweep.
- ✅ Confirmed diagnosis requires ≥2 supporting and zero contradicting
  above the threshold; otherwise only "probable" is reported.
- ✅ Missing-evidence list with which hypothesis each item would affect.
- ✅ Safety rules: forbidden-action regex filter (short battery,
  bypass protection, disable OCP/OVP, uncontrolled injection,
  incompatible supply, forced charge, screwdriver discharge); warning
  filter for live measurements; caution generator.
- ✅ Document-request manager: explicit, justified single-file requests
  with the exact fields the engine wants to extract, matching the
  spec's example request style.

### Milestone 5 — Reports, testing, packaging
- ✅ Markdown report with every required section: motherboard/model,
  date, symptoms, measurements, timeline, hypotheses + ranking,
  supporting/contradicting, missing info, next tests, documents
  referenced, confirmed vs probable diagnosis.
- ✅ Optional ReportLab PDF report (HTML-safe escaping, CJK-capable font
  registration).
- ✅ 74 automated tests covering every required component.
- ✅ PyInstaller spec + Windows build script + Linux/macOS dev script.
- ✅ README, CHANGELOG, LICENSE, INSTALL, MODEL_PREPARATION,
  OFFLINE_USAGE, LIMITATIONS, ARCHITECTURE, TEST_RESULTS, this file.

## Known limitations and unimplemented features

The application says so plainly when something is not implemented;
nothing is faked.

- ❌ **OCR** is an optional capability and is OFF by default. The
  architecture supports it (`ocr_enabled` setting,
  `ocr_recommended` flag), but no OCR engine is wired in. Scanned PDFs
  and images are stored with a clear "OCR recommended" note.
- ❌ **Binary BoardView parsing** (`.fz`, `.bvr`, `.brd`): stored with
  hash and size; the assistant says the format is not parsed and asks
  for a screenshot or an ASCII export. ASCII `.asc`/`.brd2` are parsed
  as text.
- ❌ **BIOS/firmware semantic analysis**: stored with hash and size;
  the assistant explicitly says no semantic parser is implemented.
- ❌ **Schematic netlist understanding**: vector-PDF text and component
  references are extracted; the assistant does NOT claim to understand
  the circuit from the image. When pin mapping is needed it requests
  the schematic.
- ❌ **Forum/community ingestion pipeline**: the built-in KB contains
  concise rewritten summaries with provenance/confidence labels. No
  automated scraper. A `knowledge_base.load_builtin()` mechanism exists
  for future curated JSON datasets.
- ❌ **Vector retrieval / embeddings**: intentionally not used. On an
  8 GB CPU-only target a keyword + reranking approach is faster and
  deterministic.
- ❌ **Windows EXE**: the build script and PyInstaller spec are
  provided; the EXE was not built in this Linux sandbox. Reproducible
  on a Windows machine with `scripts/build_windows.bat`.
- ❌ **Live llama.cpp inference** was not exercised in this sandbox
  (no model file present). The wrapper is tested for graceful
  degradation; the subprocess invocation follows the documented CLI
  contract.
- ❌ **Hardware-specific performance** on the 6th-gen i7 / 8 GB target
  was not benchmarked. Defaults are conservative; tuning guidance is in
  `docs/MODEL_PREPARATION.md`.
- ❌ **Graphical interface**: not required by the spec; the interface is
  CMD/terminal-based as specified.

## Where each requirement is met (traceability)

| Spec requirement                                       | Where                                       |
|--------------------------------------------------------|---------------------------------------------|
| Standalone Windows EXE                                 | scripts/eda.spec, scripts/build_windows.bat|
| CMD/terminal interface                                 | src/eda/cli.py                              |
| Portable self-contained distribution                   | docs/INSTALL.md, run.bat, run.sh           |
| Local inference runtime (llama.cpp)                     | src/eda/llm_engine.py, Engine/             |
| GGUF model, one-time preparation                       | scripts/prepare_model.py                   |
| Small quantized model, 1.5B-class Q4                    | docs/MODEL_PREPARATION.md                   |
| SQLite + FTS5                                           | src/eda/database.py                         |
| Modular architecture (10 components)                    | src/eda/*.py, docs/ARCHITECTURE.md          |
| Built-in electronics knowledge base                     | Knowledge/*.json, src/eda/knowledge_base.py|
| 28 required KB topics                                   | tests/test_knowledge_base.py                |
| Curated forum/manufacturer knowledge with provenance    | Knowledge/*.json (provenance + confidence) |
| Document request system (never auto-scan)               | src/eda/document_requests.py, src/eda/cli.py|
| Format-aware PDF processing                             | src/eda/documents.py                        |
| Datasheet extraction (qualitative, not whole tables)    | src/eda/documents.py                        |
| Schematic text extraction, page refs                    | src/eda/documents.py (page markers)        |
| BoardView: known formats via parsers, honest otherwise  | src/eda/documents.py                       |
| BIOS: hash + size, no semantic analysis                 | src/eda/documents.py                        |
| Document status (received/extracted/etc.)              | src/eda/documents.py                        |
| Evidence-based reasoning (4 categories)                 | src/eda/reasoning.py                        |
| Rank hypotheses, supporting/contradicting/missing       | src/eda/reasoning.py                        |
| Next most informative test                              | src/eda/reasoning.py                        |
| Do not invent values/pins                              | src/eda/reasoning.py, src/eda/safety.py     |
| Request documents only when genuinely needed            | src/eda/reasoning.py, src/eda/document_requests.py |
| Cross-restart session persistence                       | src/eda/sessions.py (SQLite)               |
| Measurement manager with full fields                    | src/eda/measurements.py                     |
| Repair sessions (new/open/list/notes/...)               | src/eda/sessions.py, src/eda/cli.py         |
| 16 commands + natural language                          | src/eda/cli.py                             |
| Drag-and-drop file paths, spaces handled                | src/eda/cli.py (shlex)                     |
| RAM/storage optimization (mmap, chunking, FTS, on-demand)| docs/ARCHITECTURE.md, src/eda/*.py         |
| Models dir independent of Documents dir                 | src/eda/config.py                          |
| Markdown + optional PDF report                         | src/eda/reporting.py                        |
| Confirmed vs probable diagnosis                        | src/eda/reasoning.py, src/eda/reporting.py  |
| Electrical safety rules                                | src/eda/safety.py                           |
| Automated tests (all required components)               | tests/ (74 tests)                          |
| Sample diagnostic test case                            | tests/test_reasoning.py::test_sample_case  |
| Milestones, CHANGELOG, README                          | CHANGELOG.md, README.md                    |
| Final delivery (source, EXE script, ZIP, docs)          | this folder + ZIP in download/             |
