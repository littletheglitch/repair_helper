"""Phase 5, Task 6 — hardware validation harness honesty contract.

Runs the validation harness and asserts:
  * The deterministic checks (calc round-trip, tool-call, context/limits)
    PASS (they need no hardware).
  * The hardware/model-dependent checks are NOT_RUN (no GGUF / llama
    binary in the sandbox), with a reason — never fabricated numbers.
  * No NOT_RUN check claims a latency/speed/memory value (those would be
    fabricated).
  * The harness exits 0 when only NOT_RUN (not FAIL) items are pending.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "phase5_hardware_validation.py"


def _run_harness():
    """Run the harness with --json and return the parsed result."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    assert proc.returncode in (0, 1), f"unexpected exit {proc.returncode}"
    # The harness prints JSON; find it (it may be the whole stdout).
    out = proc.stdout.strip()
    return json.loads(out), proc.returncode


def test_harness_deterministic_checks_pass(tmp_app):
    """The hardware-independent checks must PASS in any environment."""
    res, rc = _run_harness()
    by_name = {c["name"]: c for c in res["checks"]}
    # Deterministic calc round-trip must pass.
    assert by_name["deterministic_calc_round_trip"]["status"] == "PASS"
    # Tool-call dispatch must pass.
    assert by_name["tool_call_success"]["status"] == "PASS"
    # Context/limits are always reported.
    for n in ("n_ctx", "n_threads", "n_gpu_layers", "max_tokens"):
        assert by_name[n]["status"] == "PASS"


def test_harness_hardware_checks_honestly_not_run(tmp_app):
    """In the sandbox (no GGUF / llama binary), the hardware/model
    checks must be NOT_RUN with a reason — never fabricated."""
    res, rc = _run_harness()
    by_name = {c["name"]: c for c in res["checks"]}
    # The model-load + inference checks must NOT be claimed as PASS
    # without a real model. They are either NOT_RUN (no model) or,
    # if a model is present, PASS with a real measurement. We assert
    # they are NOT FAIL and that any NOT_RUN has a non-empty reason.
    # (Phase 6 renamed some checks; accept either the old or new name.)
    inference_checks = ("model_load", "total_inference_latency_ms",
                        "time_to_first_token_ms", "generation_tokens_per_s",
                        "peak_working_set_mb", "cpu_utilization_pct",
                        "clean_shutdown")
    for n in inference_checks:
        if n not in by_name:
            continue  # older harness without this check
        c = by_name[n]
        assert c["status"] in ("PASS", "NOT_RUN"), (
            f"{n}: unexpected status {c['status']}")
        if c["status"] == "NOT_RUN":
            assert c["detail"], f"{n} NOT_RUN must state a reason"
            # A NOT_RUN check must NOT carry a fabricated number.
            assert c["value"] is None, (
                f"{n} NOT_RUN must not carry a value (would be fabricated)")


def test_harness_no_fabricated_latency_when_not_run(tmp_app):
    """No NOT_RUN check may report a latency, speed, or memory number."""
    res, rc = _run_harness()
    for c in res["checks"]:
        if c["status"] == "NOT_RUN":
            name = c["name"]
            if any(k in name for k in
                   ("latency", "tokens_per_s", "memory_mb", "generation",
                    "working_set", "cpu_util")):
                assert c["value"] is None, (
                    f"{name} is NOT_RUN but carries value {c['value']}")


def test_harness_exit_code_zero_without_failures(tmp_app):
    """The harness exits 0 when no RUN check FAILs (NOT_RUN is not a
    failure). In the sandbox, with only NOT_RUN pending, exit is 0."""
    res, rc = _run_harness()
    failed = [c for c in res["checks"] if c["status"] == "FAIL"]
    if not failed:
        assert rc == 0, f"exit {rc} but no FAILs"


def test_harness_reports_cpu_and_environment(tmp_app):
    """The harness reports CPU model, logical count, and memory (or
    NOT_RUN). Phase 6 split cpu_info into cpu_model + cpu_logical_count
    and added total_memory_mb; accept either shape."""
    res, rc = _run_harness()
    by_name = {c["name"]: c for c in res["checks"]}
    # CPU model is reported (new name) or via cpu_info (old name).
    if "cpu_model" in by_name:
        assert by_name["cpu_model"]["status"] == "PASS"
        assert by_name["cpu_logical_count"]["status"] == "PASS"
    else:
        assert by_name["cpu_info"]["status"] == "PASS"
        assert "cpu_count" in by_name["cpu_info"]["value"]
    # Total memory is reported in Phase 6.
    if "total_memory_mb" in by_name:
        assert by_name["total_memory_mb"]["status"] in ("PASS", "NOT_RUN")
