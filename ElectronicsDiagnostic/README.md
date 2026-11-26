# Electronics Diagnostic Assistant

A fully offline electronics and laptop-motherboard troubleshooting assistant
for an experienced board-level repair technician. It is **not** a chatbot and
**not** a document search engine: it runs an **evidence-based diagnostic
reasoning engine** that ranks fault hypotheses, lists the evidence supporting
and contradicting each, identifies missing evidence, and recommends the single
most informative next measurement — exactly the way a senior technician thinks.

It is designed to run on a low-power target:

- Intel Core i7 6th generation, integrated graphics only
- 8 GB system RAM, Windows 64-bit
- CPU-only inference (no CUDA, no dedicated GPU)
- No internet connection, no cloud API, no subscription, no account

The reasoning engine is deterministic and rule-based; a small local LLM (a
1.5B-class Q4 GGUF model via `llama.cpp`) is used **only to paraphrase** the
engine's output. The LLM is never allowed to invent measurements, pin
numbers, voltage values, or component connections. If the LLM or the model is
absent, the application falls back to the rule engine's text and still works.

---

## 1. Quick start

### Option A — Run from source (no build)

```bash
cd ElectronicsDiagnostic
python3 -m venv build/venv
source build/venv/bin/activate     # Windows: build\venv\Scripts\activate
pip install -e ".[pdf,test]"
python -m eda
```

You get the interactive REPL (`repair> `). Type `help` for the command list.

### Option B — Build the standalone Windows EXE

On a Windows machine with Python 3.10+ and pip:

```bat
scripts\build_windows.bat
```

The output is in `dist\ElectronicsDiagnostic\`. Copy that folder's contents
into the application root (next to `Models\`, `Knowledge\`, etc.) and you have a
portable, self-contained `ElectronicsDiagnostic.exe`.

### Option C — One-time model preparation (for full LLM assistance)

The model weights and the `llama.cpp` runtime are deliberately **not** bundled
(too large, platform-specific). Prepare them once:

```bash
python scripts/prepare_model.py --list
python scripts/prepare_model.py --model qwen2.5-1.5b-instruct-q4_k_m --yes
```

This downloads a ~1.0 GB Q4_K_M GGUF into `Models/` and updates
`Config/settings.json`. Then place a `llama.cpp` `llama-cli.exe` (or `main.exe`)
into `Engine/`, or set `settings.engine.cli_path`. See
[docs/MODEL_PREPARATION.md](docs/MODEL_PREPARATION.md).

> Without the model, the application still works. Every diagnostic is produced
> by the rule engine; the LLM only adds a short human-readable explanation when
> it is available. This is verified by `tests/test_llm_engine.py`.

---

## 2. What it does

For each motherboard repair, the assistant maintains four separate evidence
categories, exactly as the specification requires:

| Category         | What goes in it                                              |
|------------------|-------------------------------------------------------------|
| Measured facts   | Voltages, resistances, currents, signals you actually took  |
| Hypotheses       | Ranked faults, with supporting and contradicting evidence  |
| Missing evidence | Information that, if obtained, would move the ranking      |
| Next test        | The single most informative measurement or inspection      |

### Example session (the spec's sample case)

```
repair> new Lenovo G50-80 G50-80 board
repair> symptom No power
repair> measure VIN = 19V
repair> measure +3VALW = 3.3V
repair> measure +5VALW = 0V
repair> analyze
repair> next-test
repair> add-file "D:\Repair\G50_schematic.pdf"
repair> report
```

The engine refuses to declare a failed component on this evidence (because
+5VALW = 0 V while VIN is present is consistent with several distinct faults:
shorted +5VALW rail, failed standby converter, missing EN, missing ACOK). It
asks for the **resistance to ground on +5VALW** first — the cheapest
measurement that most differentiates the top hypotheses. This is asserted by
`tests/test_reasoning.py::test_sample_case_no_power`.

### Safety

The assistant will never recommend shorting battery terminals, bypassing
protections, disabling OCP/OVP, uncontrolled power injection, or incompatible
supplies. Resistance measurements always come with a caution to disconnect
power and discharge bulk capacitors. Live measurements always specify a
ground reference. Verified by `tests/test_reasoning.py::test_safety_*`.

---

## 3. Project layout

```
ElectronicsDiagnostic/
  ElectronicsDiagnostic.exe      (built by scripts/build_windows.bat)
  run.bat / run.sh               convenience launchers
  pyproject.toml                 package + console-script entry point
  README.md, CHANGELOG.md, LICENSE
  docs/
    INSTALL.md                   install + first-run
    MODEL_PREPARATION.md         one-time model setup
    OFFLINE_USAGE.md             running fully offline
    LIMITATIONS.md               what is and is not implemented
    ARCHITECTURE.md              module map
  src/eda/                       the application
    config.py                    paths, settings
    database.py                   SQLite + FTS5 schema and helpers
    knowledge_base.py             built-in KB loader
    sessions.py                   repair sessions
    measurements.py               measurement parser + persistence
    documents.py                  PDF/text/BoardView/BIOS import + chunks
    retrieval.py                  on-demand keyword search (KB + documents)
    document_requests.py          document-request manager
    reasoning.py                  evidence-based diagnostic engine
    safety.py                     safety rules
    llm_engine.py                 llama.cpp / OpenAI-API wrapper, fallback
    reporting.py                  Markdown + PDF report
    cli.py                        command interface + REPL
  Knowledge/                     curated JSON knowledge files
  Models/                        GGUF model goes here
  Engine/                        llama.cpp CLI goes here
  Documents/                     user-imported files (never auto-scanned)
  Sessions/ Reports/ Config/ Logs/ Temp/
  tests/                         74 automated tests
  scripts/
    prepare_model.py              one-time model download
    eda.spec                      PyInstaller spec
    build_windows.bat             Windows build
    build_dev.sh                  Linux/macOS dev
```

---

## 4. Commands

```
help                            new <title> [model]      open [<id>]
sessions                        close                    symptom <text>
measure <name> = <value>[unit] [@ point]
analyze                         next-test
add-file <path>                 documents                remove-document <id>
search <query>                  notes [<text>]           report [--pdf]
settings [show]                 exit
```

Natural-language input is also accepted: a line with `=` and a number is
parsed as a measurement; any other unrecognized line is treated as a symptom
if a session is open.

### Measurement input examples

```
measure VIN = 19.4V
measure +3VALW = 3.31V
measure +5VALW = 0V
measure resistance_to_ground = 12.4ohm
measure ACOK = 0V @ EC pin 12
```

Units are normalized: `v`, `volts` → `V`; `mv` → `mV`; `ohm`, `ω` → `Ω`;
`kohm` → `kΩ`; etc.

---

## 5. Testing

```bash
python -m pytest tests/ -q
```

74 tests cover: database initialization, FTS5, session persistence,
measurement parsing and persistence, document import + duplicate detection +
SHA-256 + path-with-spaces, PDF text extraction, document indexing, search,
knowledge-base loading, reasoning engine, the sample case, safety rules,
document requests, report generation, LLM missing-model / missing-runtime /
disabled-backend handling, file-path-with-spaces, and offline startup.

---

## 6. Document handling — privacy by design

The application **never** scans the user's drives, never indexes directories
without permission, never reaches the external archive. It only opens files
the user **explicitly** submits with `add-file <path>` (or drag-and-drops into
the CMD window). Each imported file is hashed (SHA-256), chunked, and indexed
in SQLite FTS5. Duplicate detection by SHA-256 means a file already imported
is reused, not re-stored. Use `remove-document <id>` to delete an imported
file and its index.

Every document carries a status: `received`, `extracted`,
`partially_extracted`, `stored_only`, or `failed`. Binary BoardView and BIOS
files are stored with their hashes and sizes; their formats are **not**
claimed to be parsed (the assistant says so plainly).

---

## 7. License

MIT. See [LICENSE](LICENSE). The bundled curated knowledge in `Knowledge/`
is original prose written for this project; it does not reproduce any
copyrighted forum post or manual.

---

# Phase 2 — Modular Engineering Computation Architecture

Phase 2 upgrades the assistant from a rule-based engine into a modular
engineering system: independent computational engines controlled by a
Tool Router, with the local LLM acting as orchestrator and paraphraser
(never as the calculator).

See [docs/PHASE2_GAP_REPORT.md](docs/PHASE2_GAP_REPORT.md) for the
architecture and gap report, and [docs/CHANGELOG_PHASE2.md](docs/CHANGELOG_PHASE2.md)
for the full change list.

## New engine commands

```
engines                              List registered engines
engine <name> <method> [--key val]   Call an engine method
validate <name> <val> <unit> [--lo N --hi N --condition S]
hypotheses                           Structured hypothesis manager (6 statuses)
next-best-test                       Information-gain next test
sequence [default|list|analyze|get] Power-sequence analysis
topology <add|netlist|opens|shorts|unsupported> ...
benchmark [run|--held-out]            Blind diagnostic benchmark
learn record <fault> [--replaced C --confirming T --outcome O --unverified]
db <integrity|backup|recover|version>
```

The Phase 1 commands (`new`, `open`, `measure`, `analyze`, `next-test`,
`add-file`, `report`, etc.) are unchanged.

## Engines

| Engine                 | What it does                                              |
|------------------------|----------------------------------------------------------|
| electrical_math        | Ohm, power, series/parallel, dividers, KVL/KCL (SI-checked)|
| measurement_validation | Range comparison, contradiction detection                 |
| datasheet              | Structured parameter extraction with provenance           |
| power_sequence         | Per-board dependency graph, G3-S0, missing prerequisites |
| hypotheses             | 6 explicit statuses, strong confirmation gate            |
| topology               | Circuit graph; honest about unsupported formats          |
| next_best_test        | Information-gain selection, outcome branches, safety     |
| spice (optional)      | ngspice via PySpice + pure-Python DC fallback             |

Every engine returns a structured `EngineResult` with formulas,
assumptions, limitations, and provenance. The LLM may paraphrase but
must not alter the returned value.

## Testing

286 tests pass. Run the benchmark:

```
python -m eda benchmark --held-out
```

See [docs/TEST_RESULTS_PHASE2.md](docs/TEST_RESULTS_PHASE2.md) for the
honest benchmark metrics (top-1 50%, top-3 100%, unsupported-claims 0%
on the held-out set).
