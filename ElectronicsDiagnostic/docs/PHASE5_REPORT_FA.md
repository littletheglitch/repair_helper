# Phase 5 Report — Practical Validation, Integration Hardening, Production Readiness

Phase 5 makes the existing system more reliable in actual use. It prioritizes
integration, failure handling, reproducibility, and realistic diagnostic
workflows over adding unrelated features. No subsystem was rewritten; only
defective parts were fixed and tests were added.

## Audit (Task 1)

The audit inspected the actual implementation (not just reports). Eight real
defects were found:

1. **Engine provenance dropped in user-facing text** — `Orchestration.to_text`
   printed only tool name + ok/ERR; the unit/provenance/value_si survived in
   `to_dict` but the CLI uses `to_text`, so the user never saw the source.
2. **Ground-reference dropped on measurement parse** — `measurements.parse_line`
   always returned `reference=""`; the regex captured `point` but no `ref=`
   group, even though `identify_missing_context` flagged the missing ref as a
   gap.
3. **Malformed tool-call JSON silently skipped** — `tool_interface.parse_tool_calls`
   swallowed `JSONDecodeError` with `continue`, so a broken model output was
   indistinguishable from "no tool call, here is the final answer".
4. **`shlex.split(line, posix=True)` on Windows** (`cli.py:1342`) — treated
   backslash paths as escapes, breaking drag-and-drop paths like `D:\dir\file`.
5. **Windows `stop()` killed only the parent** (`llm_backend.py:316`) —
   `terminate()` left orphaned llama-server worker threads.
6. **Dotted rail names (`+3.3V`, `+1.2V`) did not parse** — the name char class
   lacked `.` (pre-existing, surfaced while fixing #2).
7. **No iteration-limit / repeated-tool-call safety** — *found to already
   exist* (`orchestrator.py:231` `max_iterations` cap + exhaustion note); not
   a defect.
8. **`_divergence` disabled** (`orchestrator.py:430`) — *found to be an
   intentional, documented decision* (numbers caught by unsourced-claim
   check); not a defect.

Items 1–6 were fixed. Items 7–8 were verified as non-defects.

## Implemented (Task 2 — Harden local LLM integration)

All changes are surgical, no subsystem rewrite:

- **`orchestrator.py`** — `to_text` now renders each tool call's value, unit,
  and provenance (e.g. `next_best_test.recommend -> ok: ... [source: ...]`).
  Added malformed-tool-call surfacing: when `parse_tool_calls` returns no valid
  calls but `TI.last_malformed` is non-empty, the orchestrator feeds a
  corrective `user` message back to the model (capped by `MAX_MALFORMED_STREAK=3`
  to avoid infinite loops), then falls back to the rule engine with an honest
  note rather than treating the broken output as a final answer.
- **`tool_interface.py`** — `parse_tool_calls` now records malformed blocks on
  a module-level `last_malformed` list (with the block + the parser error) so
  the orchestrator can surface them. Prose final answers are NOT flagged
  (only blocks that look like attempted tool calls).
- **`llm_backend.py`** — Windows `stop()` now uses `taskkill /F /T /PID` to
  kill the whole process tree (falling back to `terminate()` if taskkill is
  unavailable), fixing orphaned-worker cleanup.
- **`measurements.py`** — `_MEASURE_RE` now captures `| ref=<ground>` and
  `| state=<state>`; the name class accepts `.` so `+3.3V`, `+1.2V` parse.
  `parse_line` returns the captured `reference` and `board_state`.
- **`cli.py`** — `shlex.split` now uses `posix=(os.name != "nt")` so Windows
  backslash paths survive tokenization.

The existing safety properties were verified, not changed: model-unavailable
returns `None` (no fabrication); the iteration cap already exists; engine
results carry provenance verbatim through `ToolResult.to_dict`; the 8GB
target's 1.5B Q4_K_M recommendation is already in `model_registry.py`.

## Tested

Full suite: **706 tests pass** (671 Phase 4.2 baseline + 35 new Phase 5
tests). No regressions. All offline.

| Test file | n | Covers |
|---|---|---|
| `test_phase5_diagnostic_workflows.py` | 8 | Task 3: 6 realistic scenarios + evidence-update + no-confirmed-claim |
| `test_phase5_next_test_quality.py` | 5 | Task 4: transparent gain, changes w/ evidence, outcome branches, skips-taken, no fake prob |
| `test_phase5_learning_integration.py` | 7 | Task 5: preserves measurements/symptoms, unconfirmed stays unconfirmed, confirmed-only-via-mechanism, no KB contamination, hidden perturbed exercise, read-only session |
| `test_phase5_hardware_validation.py` | 5 | Task 6: harness honesty contract (deterministic PASS, hardware NOT_RUN, no fabricated numbers, exit 0) |
| `test_phase5_heldout_adverse.py` | 10 | Task 7: malformed meas, conflicting, unknown rail, model-fail, malformed tool-call, truncation, empty session, iteration limit, unknown tool, invalid args |

### Task 3 — Realistic diagnostic workflow tests (synthetic, labeled)

8 scenarios, all clearly labeled `SYNTHETIC`/`held-out`. No real confirmed
case was relabeled. Each verifies the system: separates facts from
hypotheses, names missing info, avoids inventing board-specific values,
selects a useful next test, explains why, updates hypotheses on new
evidence, and does NOT claim a confirmed repair without evidence.

### Task 4 — Next-test decision quality

Verified the existing `next_best_test` logic is **already** evidence-based
(no fix needed): `gain = len(distinguishes) * (1 + 0.1 * len(outcomes))`,
rationale names the distinguished hypotheses, outcomes describe branch
effects, tests already-taken are skipped, and the assumptions explicitly
state "heuristic is transparent, not a calibrated probability". 5 tests
prove it is not a canned sequence.

### Task 5 — Learning integration

Verified the existing `repair_to_learning.convert` is correct (no fix
needed): it carries symptoms/measurements verbatim, sets
`confirmed_root_cause=None` when outcome≠"fixed" or unconfirmed, only
discloses via the technician-confirmed mechanism, and does not add KB
facts. The follow-up exercise hides its solution and uses perturbed values.
7 tests confirm.

## Not tested (honestly)

- **Real local inference on the target i7 6th gen / 8 GB machine**: the
  sandbox has no GGUF model and no llama.cpp binary. The hardware validation
  harness (`scripts/phase5_hardware_validation.py`) implements the full
  check (model load, first-token latency, generation speed, peak memory,
  tool-call success, deterministic round-trip) but marks the
  hardware-dependent items **NOT RUN** with a reason. No latency/speed/
  memory number is fabricated. The deterministic checks (calc round-trip,
  tool-call, context/limits) PASS in any environment. Re-run on the target
  to obtain real numbers.
- **Real-world diagnostic accuracy**: the 706 tests verify the system's
  *discipline* (separates facts/hypotheses, doesn't fabricate, updates on
  evidence), NOT that it correctly diagnoses real boards. A pass is not a
  claim of production diagnostic accuracy.
- **Technician-confirmed repair cases**: only the existing confirmation
  mechanism (`learning.record_case(technician_confirmed=True)`) admits a
  case to the verified KB; no synthetic case was relabeled as confirmed.

## Known limitations

1. **Rule-based reasoning evaluator**: the reasoning checks (Phase 4.2) are
   regex heuristics. A sufficiently subtle wrong reasoning with all the right
   keywords could slip through. The 56 Phase 4.2 reasoning + 10 Phase 5
   held-out adverse cases pass, but this is a strong signal, not proof of
   perfection.
2. **Datasheet registry is small** (11 values, 3 parts, Phase 4.2):
   datasheet exercises are limited to verified values; expanding is a
   content task, not a code task.
3. **`_divergence` is intentionally disabled** (`orchestrator.py:430`):
   the rule engine and orchestrator usually agree on fault classes; number
   divergence is caught by the unsourced-claim check. This is documented
   in code.
4. **First-token latency is not split from total**: llama-server's
   `/v1/chat/completions` returns total `usage`, not a first-token timing.
   The harness reports total round-trip latency + tokens/s.
5. **Windows RSS detection**: `_peak_rss_mb` reads `/proc/<pid>/status`
   (Linux only); on Windows the harness marks peak memory NOT_RUN.

## Remaining blockers (require physical hardware / real GGUF / technician)

1. **Real llama.cpp inference on the target machine** — run
   `python scripts/phase5_hardware_validation.py` on the i7 6th gen / 8 GB
   machine with a 1.5B Q4_K_M GGUF in `Models/` and `llama-server.exe` in
   `Engine/` or PATH. The harness will then report real load success,
   first-token latency, generation speed, and peak memory.
2. **Real-world diagnostic validation** — the synthetic fixtures verify
   discipline, not accuracy. Accuracy requires technician-validated
   diagnoses on real boards, recorded via the existing confirmation
   mechanism.
3. **Datasheet registry expansion** — add verified values (with part,
   revision, page, source URL) to `DATASHEET_REGISTRY` as content becomes
   available. No value may be added without a cited source.

## Modified and added files

### Modified (this phase)
- `src/eda/orchestrator.py` — provenance in `to_text`; malformed-tool-call surfacing + streak cap.
- `src/eda/tool_interface.py` — `parse_tool_calls` records malformed blocks on `last_malformed`.
- `src/eda/llm_backend.py` — Windows `stop()` uses `taskkill /T` for tree kill.
- `src/eda/measurements.py` — `_MEASURE_RE` captures `ref=`/`state=`; name class accepts `.`.
- `src/eda/cli.py` — `shlex.split` non-posix on Windows.

### Added (this phase)
- `scripts/phase5_hardware_validation.py` — Windows hardware validation harness.
- `tests/test_phase5_diagnostic_workflows.py` — Task 3 (8 tests).
- `tests/test_phase5_next_test_quality.py` — Task 4 (5 tests).
- `tests/test_phase5_learning_integration.py` — Task 5 (7 tests).
- `tests/test_phase5_hardware_validation.py` — Task 6 (5 tests).
- `tests/test_phase5_heldout_adverse.py` — Task 7 (10 tests).
- `docs/PHASE5_REPORT_FA.md` — this report.

## Next priorities

1. Run `scripts/phase5_hardware_validation.py` on the target machine with a
   real 1.5B Q4_K_M GGUF to obtain real load/latency/speed/memory numbers.
2. Validate diagnostic accuracy on real boards via the technician-confirmed
   `learning.record_case` mechanism (the only path to the verified KB).
3. Expand `DATASHEET_REGISTRY` with verified values as content becomes
   available (each with part, revision, page, source URL).

## Definition of Done — status

The implemented changes are present in the repository; tests have actually
been executed (706 pass, 0 regressions); untestable hardware-dependent
items are honestly marked NOT RUN. The assistant is NOT claimed
production-ready merely because unit tests pass — real inference on the
target and technician-validated diagnostic accuracy remain pending.
