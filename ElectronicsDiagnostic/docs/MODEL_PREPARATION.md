# Model Preparation & LLM Runtime (Phase 3 — LLM-driven diagnosis)

This document describes the upgraded LLM architecture. The LLM is no
longer a paraphraser; it is the **diagnostic orchestrator**. It receives
the symptom + measurements, detects gaps and contradictions, calls the
specialized engines via a JSON tool-calling protocol, interprets their
structured output, compares hypotheses, and recommends the next test.
Deterministic calculations and technical specs come ONLY from the
engines and the Knowledge Base; the LLM is forbidden from inventing
voltages, pinouts, or component values — the audit enforces this.

Target hardware: Intel Core i7 6th gen, integrated GPU, 8 GB RAM,
Windows 64-bit, CPU-only.

## 1. Architecture (new)

```
                        ┌──────────────────────────────────┐
   symptoms/measurements │           Context Manager        │
   ─────────────────────▶│  (KB retrieval, token budget,    │
                        │   structured summary, no-delete)  │
                        └──────────────┬───────────────────┘
                                       │ assembled context
                                       ▼
   ┌────────────┐   tool calls   ┌──────────────────────────┐
   │   LLM      │◀──────────────│   Diagnostic Orchestrator │
   │  (Backend) │──────────────▶│   (bounded loop, audit)   │
   └────────────┘  tool results └───────────┬──────────────┘
   persistent                       tool calls│
   llama-server                     ▼
   or stub/cloud              ┌──────────────────────┐
                              │  Tool Calling Iface  │
                              │  (JSON, provenance,  │
                              │   structured errors) │
                              └──────────┬───────────┘
                                         ▼
                              ┌──────────────────────┐
                              │   Tool Router +      │
                              │   Specialized Engines│
                              │  (electrical_math,   │
                              │   measurement_valid, │
                              │   next_best_test,    │
                              │   power_sequence, …) │
                              └──────────────────────┘
```

The deterministic rule engine still runs on every diagnosis as a
cross-check and fallback. If the LLM backend is unavailable, the
orchestrator returns the rule engine's analysis unchanged.

## 2. Backends

The orchestrator is backend-agnostic. The backend is chosen by
`settings.engine.backend` (or `auto`):

| Backend | When to use | Model loaded once? |
|---|---|---|
| `llama_server` (default target) | Real GGUF on the Windows machine | Yes — `llama-server` runs once, every request goes over HTTP to the same loaded model. This is the fix for the original one-process-per-request design. |
| `llama_cli` (legacy) | Backwards-compatible one-shot `llama-cli` subprocess | No — reloads the model every request (slow; kept for compatibility). |
| `stub` | Tests, and the no-model fallback. Deterministic; perfect tool-caller. | No model. |
| `cloud` | REFERENCE ONLY (this sandbox). `z-ai chat` → glm-4-plus. Not the target deployment. | n/a (remote). |
| `disabled` | Rule-engine-only. | No model. |

Switch backend: `eda backend show` / `eda backend llama_server` /
`eda backend stub` / `eda backend disabled` / `eda backend stop`.

## 3. Model classes considered

The spec asks us to consider 1B-1.5B, 3B-4B, and 7B-8B. We do NOT
default to the largest. Honest per-class estimates for the 8 GB / i7
6th-gen target:

| Class | Disk (Q4_K_M) | RAM @ n_ctx=2048 | CPU tok/s (6th-gen i7, est.) | Verdict for 8 GB target |
|---|---|---|---|---|
| 1B-1.5B | 0.7-1.1 GB | 1.5-2 GB | 8-15 | **Recommended.** Leaves ~6 GB for the OS. |
| 3B-4B | 1.8-2.6 GB | 3-4 GB | 2-4 | Usable, tight on RAM. |
| 7B-8B | 4.0-5.5 GB | 5-6 GB | 0.5-1.5 | NOT recommended on the 8 GB target — heavy swap. |

Candidate GGUF models in the registry (`eda models list`):

- `qwen2.5-1.5b-instruct-q4_k_m` (~1.0 GB) — recommended
- `llama-3.2-1b-instruct-q4_k_m` (~0.9 GB)
- `qwen2.5-3b-instruct-q4_k_m` (~2.0 GB)
- `phi-3.5-mini-instruct-q4_k_m` (~2.5 GB)
- `qwen2.5-7b-instruct-q4_k_m` (~4.7 GB)
- `llama-3.1-8b-instruct-q4_k_m` (~4.9 GB)

**The RAM/tok-s numbers above are estimates. You must measure the real
numbers on your Windows target — see §6.** This sandbox is Linux and
cannot run real GGUF, so the numbers in `docs/LLM_BENCHMARK_RESULTS.json`
are from the stub and cloud-reference backends only.

## 4. Installing the runtime and a model (Windows target)

### Step 1 — get the llama.cpp runtime

The persistent backend needs `llama-server` (recommended) or
`llama-cli` (legacy). Get a prebuilt Windows CPU binary:

- Official: https://github.com/ggerganov/llama.cpp/releases — look for
  `llama-{version}-bin-win-cpu-x64.zip`.
- Unzip and place `llama-server.exe` (and `llama-cli.exe` if you want
  the legacy path) into `ElectronicsDiagnostic\Engine\`.
- The app auto-detects binaries in `Engine\` and on `PATH`. You can
  also set `settings.engine.cli_path` / `server_path` to an absolute
  path.

### Step 2 — download a model (explicit only)

Models live in `ElectronicsDiagnostic\Models\` and are **never** bundled
into the EXE. Downloads require explicit confirmation and a SHA-256
check:

```
python -m eda models list
python -m eda models download qwen2.5-1.5b-instruct-q4_k_m
   # asks "Proceed? [y/N]"; prints SHA-256 after download; updates settings.json
```

Or with `scripts/prepare_model.py`:

```
python scripts/prepare_model.py --list
python scripts/prepare_model.py --model qwen2.5-1.5b-instruct-q4_k_m --yes
```

If the target machine has no internet, run the download on any
machine with internet, then copy `Models\*.gguf` and the updated
`Config\settings.json` to the target. Or use the portable bundle
(§5).

### Step 3 — tune runtime settings

```
python -m eda model-settings                # show current
python -m eda model-settings n_ctx 4096     # larger context (uses more RAM)
python -m eda model-settings n_threads 6    # 6th-gen i7 has 4c/8t
python -m eda model-settings n_batch 1024   # prompt eval batch
python -m eda model-settings n_gpu_layers 0 # CPU-only (target has no CUDA)
```

### Step 4 — verify

```
python -m eda backend show
python -m eda ram                 # shows resident RAM of the loaded model
python -m eda tools               # lists the tools the orchestrator can call
```

`backend show` should report `Available: True` and the paths to the
model and `llama-server`. `ram` shows the resident set size of the
running `llama-server` process.

## 5. Moving models to another machine (portability)

```
python -m eda models bundle D:\portable_eda_bundle
# Copy D:\portable_eda_bundle to the target, then on the target:
python -m eda models import D:\portable_eda_bundle
```

The bundle includes a `bundle_manifest.json` with the SHA-256 of each
model; `import` re-verifies the hash on the target and refuses to
register a model whose hash does not match.

## 6. Swapping models

To switch the active model without re-downloading:

```
python -m eda model-settings model_file Qwen2.5-7B-Instruct-Q4_K_M.gguf
python -m eda backend stop         # stop the currently-loaded server
python -m eda backend show         # rebuild with the new model
```

Or edit `Config\settings.json` → `engine.model_file`, then `backend stop`.

## 7. Running the orchestrator (the new diagnosis)

```
python -m eda new "Lenovo G50-80 no power" "Lenovo G50-80"
python -m eda symptom "no power, no LEDs"
python -m eda measure VIN = 19.4V @ board input
python -m eda measure +5VALW = 0V
python -m eda context           # see what the orchestrator will see
python -m eda orchestrate       # LLM-driven diagnosis via tool calls
```

`orchestrate` outputs: the LLM's final answer (ranked hypotheses with
citations, recommended next test, stated gaps), the tool trace, the
audit (unsourced-claim flags, contradiction findings), and the
backend/latency/tok-per-s line.

## 8. The benchmark

```
python -m eda llm-benchmark --backends stub,cloud
# On the Windows target, after a model is loaded:
python -m eda llm-benchmark --backends stub,llama_server
```

The benchmark runs 4 cases (a no-power short, an input-open gap, a
contradictory-measurements case, and a missing-critical-measurement
case) and scores each backend on: engine selection, next-test match
vs. the deterministic gold, gap detection, contradiction detection,
unsourced-claim count, refusal discipline, RAM, and speed.

Results measured in this sandbox (NOT the target — see the JSON file
for the caveat):

| Backend | EngSel | NextTest | GapDet | ContrDet | Unsourced | Refusal | AvgMs | Tok/s |
|---|---|---|---|---|---|---|---|---|
| stub (reference) | 1.00 | 0.75 | 0.00 | 0.00 | 0.25 | 0.00 | 1.8 | 92714 |
| cloud (glm-4-plus, reference) | 1.00 | 0.75 | 1.00 | 1.00 | 0.00 | 1.00 | 21151 | 42.3 |

The stub is the upper bound for *tool-calling correctness* and the
lower bound for *narrative discipline*. The cloud reference proves the
architecture works end-to-end with a real model: it called
`measurement_validation` on the contradiction case, detected the
contradiction, refused to confirm a fault, and emitted zero
unsourced technical claims.

The cloud's latency is dominated by the `z-ai chat` CLI subprocess
overhead per request, not by model inference; a local `llama_server`
would have different latency characteristics (model load once, then
fast per-request). You must measure the real GGUF numbers on the
Windows target.

## 9. What the LLM is and is not allowed to do

The orchestrator's system prompt (see `eda/orchestrator.py`) forbids
the LLM from inventing voltages, currents, resistances, pinouts,
component part numbers, or datasheet values. Every such claim in the
final answer must cite a tool result or a Knowledge Base fact. The
audit (`_audit`) extracts numeric/part-number claims from the final
answer and verifies each one has a matching provenance in the
conversation history; unsourced claims are flagged and the answer is
annotated. The deterministic rule engine always runs as a cross-check.

## 10. Limitations (honest)

- **GGUF numbers are estimates, not measurements.** This sandbox is
  Linux and runs no real GGUF. The RAM/tok-s numbers in §3 are
  engineering estimates from public llama.cpp benchmarks; the real
  numbers for YOUR 8 GB / i7 6th-gen target MUST be measured with
  `eda llm-benchmark --backends llama_server` on the target after
  loading each model class. The biggest model is NOT assumed best —
  §3 explains why 7B-8B is not recommended for the 8 GB target.
- **Cloud backend is a reference, not the deployment.** The `cloud`
  backend uses the `z-ai chat` CLI (glm-4-plus) and exists only to
  prove the architecture works with a real model. The target uses
  `llama_server` with a local GGUF.
- **The stub backend is a perfect tool-caller but a weak explainer.**
  It calls the right engines but its final answers are terse tool
  summaries; it does not narrate gaps or refuse gracefully. Real
  models score higher on the narrative metrics (see §8).
- **Audit is heuristic.** The unsourced-claim detector matches
  numeric/part-number patterns against the sourced-value set; a
  novel claim phrased without a recognizable number may slip through.
  This is a deliberate trade-off to keep the audit deterministic and
  offline.
- **No DB mutation by the LLM.** The tool catalog deliberately omits
  mutating methods (`hypotheses.confirm`, `power_sequence.save`,
  `topology.add_*`). The LLM may only read/compute; only the
  technician (via the CLI) mutates the DB.
