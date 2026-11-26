# Windows Deployment & Validation Procedure

A repeatable, offline procedure for the target Windows 64-bit / i7 6th
gen / 8 GB RAM / CPU-only machine. No internet access is required after
the one-time preparation.

> Every command below was verified against the actual CLI in this
> repository. Paths, working directory, expected output, and the
> REPL-vs-one-shot distinction are documented as they really behave.

---

## 0. Repository layout (verified)

The editable install resolves all data paths relative to the source
checkout's `ElectronicsDiagnostic/` folder (via `config.app_root()`),
**not** the current working directory. So:

```
ElectronicsDiagnosticSuite/
└── ElectronicsDiagnostic/          <- pip install -e runs here
    ├── src/eda/                    <- the package
    ├── Engine/                     <- place llama-server.exe here
    ├── Models/                     <- place the .gguf here
    ├── Logs/                       <- eda.log is written here (auto-created)
    ├── Config/                     <- settings.json + eda.sqlite3 (auto-created)
    ├── Knowledge/                  <- built-in engineering KB (read-only)
    ├── scripts/                    <- phase5_hardware_validation.py, etc.
    └── docs/                       <- this file
```

`app_root()` is computed from the module path, so the model, engine,
logs, and DB always live in this `ElectronicsDiagnostic/` folder
regardless of where you launch `eda` from. Model files are NOT bundled
into any executable and can be replaced independently.

---

## 1. One-time preparation (online, once)

1. Install Python 3.10+ (64-bit) and add it to PATH.
2. Install the assistant package in editable mode **from the
   `ElectronicsDiagnosticSuite` folder**:
   ```
   pip install -e ./ElectronicsDiagnostic
   ```
   This installs the `eda` console entry point and the two required
   dependencies (`pypdf`, `PyMuPDF`). Optional extras:
   `pip install -e "./ElectronicsDiagnostic[ocr,pdf]"` (adds
   `pytesseract`/`Pillow` for OCR and `reportlab` for PDF reports).
3. Place `llama-server.exe` in
   `ElectronicsDiagnosticSuite\ElectronicsDiagnostic\Engine\`.
4. Place a 1.5B Q4_K_M GGUF (e.g.
   `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf`) in
   `ElectronicsDiagnosticSuite\ElectronicsDiagnostic\Models\`.

### Offline wheelhouse (if the target has no internet at install time)

On a machine WITH internet, download wheels for the target platform
into a folder, then move that folder to the target:

```
# On an internet-connected Windows machine of the same Python version:
pip download -d ./wheelhouse -e ./ElectronicsDiagnostic
# (or) pip download -d ./wheelhouse pypdf PyMuPDF

# On the offline target:
pip install --no-index --find-links ./wheelhouse -e ./ElectronicsDiagnostic
```
Do not add packaging complexity (no custom bundler); the standard
wheelhouse is enough.

---

## 2. Repeatable launch (offline, every session)

All commands are run from `ElectronicsDiagnosticSuite\ElectronicsDiagnostic`.

### 2a. Pre-flight check (offline; no model needed)

```
python -m eda.deployment_readiness
```
Reports Python version, required/optional dependencies, llama.cpp
discovery, model file path + size + quantization, and config sanity.
`Overall: READY` means all checks pass; `NOT_READY` lists the specific
failure(s) with a printed fix. Add `--json` for machine-readable output,
`--strict` to exit non-zero when NOT_READY (useful in scripts).

Expected in the sandbox / before placing the model: `Overall: NOT_READY`
with failures `llama.cpp executable missing` and `model file missing` —
this is correct, not a bug.

### 2b. Apply a resource profile (no model needed)

```
eda model-profile            # show all profiles + the safe selection order
eda model-profile balanced   # apply balanced (the recommended start)
```
Profiles (CPU-only, 1B–1.5B Q4_K_M class):

| Profile | n_ctx | threads | batch | expected RAM* |
|---|---|---|---|---|
| `low_memory` | 1024 | 2 | 256 | ~1.2 GB |
| `balanced` | 2048 | 4 | 512 | ~1.5–2 GB |
| `diagnostic` | 4096 | 4 | 512 | ~2.2–2.8 GB |

\* Expected-RAM figures are ASSUMPTIONS, not measurements. The real
number is the harness's `peak_working_set_mb` on the target.

**Safe selection (do not guess from free RAM):**
1. Start with `balanced`.
2. Run the harness (2c); read the MEASURED `peak_working_set_mb`.
3. If it leaves little headroom for the OS, switch to `low_memory`.
4. Switch to `diagnostic` ONLY if the balanced run showed safe measured
   headroom — it uses more RAM. Do not pick it from a guessed free-RAM
   threshold.

Applying a profile writes `settings.json` and does not reload a
running model. To inspect/change a single value: `eda model-settings`
(shows current) or `eda model-settings n_threads 4` (sets one).
Valid keys: `n_ctx, n_threads, n_batch, n_gpu_layers, temperature,
max_tokens, timeout_seconds, startup_timeout_seconds, server_port,
server_host`.

### 2c. Hardware validation harness (needs the model + llama-server.exe)

```
python scripts\phase5_hardware_validation.py
```
Checks (all real; unavailable metrics are `NOT_RUN` with a reason,
never fabricated):

- `cpu_model`, `cpu_logical_count`, `total_memory_mb`,
  `available_memory_mb`
- `model_file`, `model_size_mb`, `model_quantization`,
  `llama_executable`, `llama_version`
- `n_ctx`, `n_threads`, `n_gpu_layers`, `max_tokens`, `timeout_seconds`,
  `fallback_to_rules`
- `deterministic_calc_round_trip` — the real electrical-math engine
  (V = I*R = 2A*5Ω = 10V). Always runs.
- `model_load` — real `llama-server` start + `/health` readiness poll.
- `total_inference_latency_ms`, `time_to_first_token_ms` (via SSE
  streaming; `NOT_MEASURED` if the server does not stream — never
  relabeled from total latency), `generation_tokens_per_s` (tokens/s
  from the server's `usage.completion_tokens`, not estimated).
- `cpu_utilization_pct` (sampled on Linux via `/proc`; `NOT_MEASURED`
  on Windows via stdlib — the harness does not estimate it).
- `peak_working_set_mb` — real Windows `GetProcessMemoryInfo` via
  ctypes (Linux: `/proc/<pid>/status` VmHWM). **This is the number to
  read for profile selection.**
- `tool_call_success` — engine-only: a hand-constructed `ToolCall`
  dispatched through the real engine (no model needed).
- `model_tool_call` — **model+engine**: prompts the loaded model to
  emit a fenced `tool_call` block, parses it with
  `tool_interface.parse_tool_calls`, dispatches it, and verifies the
  engine returns 10 V. `NOT_RUN` if no model is loaded; `FAIL` if the
  model cannot emit a parseable tool call.
- `clean_shutdown` — verifies the server process is gone after
  `stop()` (no orphan).

Expected in the sandbox: `PASS=12 FAIL=0 NOT_RUN=14` (the NOT_RUN items
need a real GGUF + `llama-server.exe`).

### 2d. Start the assistant

```
eda
```
With no arguments this opens the **interactive REPL**. The banner shows
the root path, the LLM backend availability, and the loaded KB fact
count. Type `help` for the command list.

> **REPL vs one-shot — important.** `Session.current` is an in-process
> pointer. A sequence like `eda new ...` then `eda measure ...` as
> **separate process calls** does NOT share an active session (each
> call starts a fresh process with no current session). To record
> symptoms/measurements/orchestrate a case you must either:
> - run them **inside the REPL** (one process), or
> - use `eda open <id>` is also process-local, so the REPL is the
>   practical way for multi-step sessions.
> One-shot mode (`eda <command> <args>`) works for commands that do
> not need an active session: `model-profile`, `model-settings`,
> `deployment_readiness` (via `python -m`), `sessions`, `new`.

> **Title with `#`** — `#` starts a shell/shlex comment. A title like
> `"Lenovo G50-80 #001"` works **inside the REPL** but as a one-shot
> `eda new "Lenovo G50-80 #001"` the `#001` is stripped by the outer
> shell. Use a title without `#` for one-shot, or run in the REPL.

---

## 3. If the model fails to load

- `model_load FAIL` / NOT_RUN "server did not become ready":
  - `Engine\llama-server.exe --version` — confirm the binary runs.
  - Confirm the GGUF is valid (re-download the 1.5B Q4_K_M if corrupt;
    a corrupt file makes the server exit immediately).
  - `eda model-profile low_memory` (smaller context) and re-run.
- `peak_working_set_mb` close to available RAM: switch to
  `low_memory`; if still over, the 8 GB target cannot run this model —
  use a smaller model class.
- `n_threads > cpu_count` warning in deployment readiness: run
  `eda model-settings n_threads <your cpu count>` (balanced sets 4;
  the target i7 6th gen has 4 cores / 8 logical — 4 is fine, 8 may
  oversubscribe).
- `model_tool_call FAIL` "model emitted no parseable tool_call block":
  the model cannot use the tool interface with this prompt — this is a
  real capability gap, not a harness bug. Try a lower temperature or a
  different instruct-tuned 1.5B model.

---

## 4. Technician validation workflow (offline)

The validation framework is a Python API (`eda.validation_framework`,
`eda.held_out_evaluation`), not yet wired into the REPL `orchestrate`
command. `orchestrate` runs the LLM diagnostic loop and prints the
result; it does **not** by itself record a blind prediction into the
validation framework. To validate a real repair case you record the
session's evidence in the REPL, then run the validation framework in
Python.

### 4a. Record evidence (REPL)

```
eda
repair> new "Lenovo G50-80 workshop 001" Lenovo G50-80
repair> symptom no power, no LEDs when adapter inserted
repair> measure VIN = 19.4 V @ DC jack | ref=GND
repair> measure +5VALW = 0 V @ always-on rail | ref=GND
repair> orchestrate --backend llama_server
repair> sessions        # note the session id (e.g. #1)
```

> These two measurements (VIN present, +5VALW = 0 V) do **not** by
> themselves establish a shorted capacitor — +5VALW could be not
> enabled, the input path could be open, or the rail could be shorted.
> The assistant's hypotheses must name the missing measurement
> (resistance-to-ground) before any short is asserted. The example
> below reflects that.

### 4b. Blind validation (Python; runnable as-is)

The complete, verified example is in
`scripts/run_blind_validation_example.py`. Run it:

```
python scripts\run_blind_validation_example.py
```

It executes the full lifecycle against the real API (no placeholders):

1. Creates a session with the two measurements above.
2. Creates a `real_unresolved` validation case linked to it.
3. Gets the **blind** view (confirmed root cause is hidden) and records
   the assistant's hypotheses + a resistance-to-ground next test that
   **distinguishes** the competing "short" vs "not enabled"
   hypotheses — then **freezes** the prediction.
4. Records the technician's measured result (12 ohm → supports short).
5. **Reveals and confirms** the root cause (allowed only because the
   prediction is frozen; rejected otherwise).
6. Asserts the prediction is **immutable** after reveal.
7. **Promotes** only this confirmed, `fixed` case to the verified
   repair KB (via the existing `learning.record_case`).
8. Asserts synthetic and unresolved cases **cannot** be promoted.
9. Registers the case as held-out, runs `detect_leakage` (no collision
   with dev fixtures), and runs the blind evaluation with a predictor
   that sees only the blind view.

The exact code (see the script for the full version):

```python
from eda import config, database, sessions, measurements
from eda import validation_framework as VF
from eda import held_out_evaluation as HE

database.init_db()

# 1. Session + evidence
s = sessions.create("Lenovo G50-80 workshop 001", "Lenovo G50-80")
sessions.add_symptom(s.id, "no power, no LEDs when adapter inserted")
measurements.add(s.id, {"name": "VIN", "value": 19.4, "unit": "V",
                        "point": "DC jack", "reference": "GND"})
measurements.add(s.id, {"name": "+5VALW", "value": 0.0, "unit": "V",
                        "point": "always-on rail", "reference": "GND"})

# 2. Real-unresolved case linked to the session
r = VF.create_case("workshop_2026_001", "real_unresolved",
                   session_id=s.id, board_model="Lenovo G50-80",
                   technician="Ali")
case_id = r["case_id"]

# 3. Blind view (root cause hidden) + record + freeze the prediction
bv = VF.get_case_for_prediction(case_id)
assert "confirmed_root_cause" not in bv["view"]   # blind

hypotheses = [
    {"hypothesis": "+5VALW regulator not enabled (enable signal absent)",
     "score": 0.45,
     "supporting": ["+5VALW=0V", "VIN=19.4V (adapter ok)"],
     "contradicting": [],
     "missing": ["+5VALW enable signal voltage",
                "resistance_to_ground on +5VALW"]},
    {"hypothesis": "+5VALW short to ground (low-impedance fault)",
     "score": 0.35,
     "supporting": ["+5VALW=0V"],
     "contradicting": ["VIN present (no over-current shutdown observed)"],
     "missing": ["resistance_to_ground on +5VALW", "current draw on VIN"]},
    {"hypothesis": "open input path between jack and +5VALW stage",
     "score": 0.20,
     "supporting": [],
     "contradicting": ["VIN=19.4V at jack (input path to jack ok)"],
     "missing": ["voltage at +5VALW stage input"]},
]
next_tests = [
    {"test": "resistance_to_ground on +5VALW (board unpowered, adapter disconnected)",
     "rationale": "A low resistance (<~50 ohm) supports a short to ground; "
                 "a high resistance rules it out and points to the "
                 "enable/path hypotheses.",
     "distinguishes": ["+5VALW short to ground",
                      "+5VALW regulator not enabled"]},
]
VF.record_prediction(case_id, hypotheses, next_tests)   # freezes

# 4. Technician records the actual result
VF.record_result(case_id, "resistance_to_ground on +5VALW",
                 "12 ohm", "low impedance measured")

# 5. Reveal + confirm (only allowed because prediction is frozen)
VF.reveal_and_confirm(case_id,
    confirmed_root_cause="shorted +5VALW bulk decoupling capacitor",
    outcome="fixed", technician="Ali")

# 6. Immutable after reveal
over = VF.record_prediction(case_id, [{"hypothesis": "x", "score": 0.9}], [])
assert not over["ok"]   # "frozen"

# 7. Promote ONLY this confirmed, fixed case to the verified KB
VF.promote_to_repair_kb(case_id)   # -> learning.record_case(technician_confirmed=True)

# 8. Synthetic / unresolved cannot promote (asserted in the script)
```

### 4c. Held-out evaluation

```python
from eda import held_out_evaluation as HE
HE.register_held_out(case_id, dataset="held_out",
                     source="workshop 2026-01, technician Ali",
                     created_by="Ali")
HE.detect_leakage()   # {} collisions = no held-out evidence is also a dev fixture
res = HE.run_evaluation([case_id, ...], predictor=my_predictor)
# my_predictor(blind_view) sees symptoms+measurements ONLY (no root cause)
# and returns {"hypotheses": [...], "next_tests": [...], "answer_text": "..."}
print(res["aggregate"])   # explicit denominators, e.g. "1/1"; no single accuracy %
```

---

## 5. Troubleshooting

- `"No module named eda"`: run `pip install -e ./ElectronicsDiagnostic`
  from the `ElectronicsDiagnosticSuite` folder.
- `eda --help` returns "Unknown command '--help'": there is no `--help`
  flag; run `eda` (no args) to enter the REPL and type `help`.
- A Windows path with spaces: quote it — `add-file "D:\My Files\board.sch"`.
  The dispatcher uses non-posix `shlex` on Windows so backslashes are
  not treated as escapes; quotes protect spaces.
- Logs are written to `Logs\eda.log` (offline; no cloud handlers).
- The model is loaded lazily — only when `orchestrate` (or the harness)
  starts `llama-server`. `eda ram` shows the running process's RSS.

---

## 6. What is NOT tested in this environment (honest)

- **Real local inference on the i7 6th gen / 8 GB target**: no GGUF or
  `llama-server.exe` in the build sandbox. The harness marks those
  checks `NOT_RUN` with a reason; no latency/throughput/memory number is
  fabricated. Re-run the harness on the target with a real 1.5B Q4_K_M
  GGUF.
- **Real-world diagnostic accuracy**: the test suite verifies the
  system's discipline (blind evaluation, no fabrication, hypothesis
  update on evidence, boundary enforcement), not that it correctly
  diagnoses real boards. Passing the tests is not a production-readiness
  claim.
- **Technician-confirmed real cases**: only `learning.record_case(
  technician_confirmed=True)` admits to the verified KB; no synthetic
  case was relabeled as real.
