"""Phase 6, Task 1 — hardware validation harness improvements.

Verifies the Phase 6 additions over the Phase 5 harness:
  * CPU model and logical processor count are reported.
  * Total AND available physical RAM are reported.
  * time_to_first_token_ms exists and is NOT_MEASURED (not fabricated)
    when no model/stream is available.
  * peak_working_set_mb exists (replaces the Linux-only peak_memory_mb).
  * cpu_utilization_pct exists and is NOT_RUN without a real process.
  * clean_shutdown exists and is NOT_RUN without a model.
  * The harness never reports a fabricated latency/memory value.
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
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    return json.loads(proc.stdout.strip()), proc.returncode


def test_harness_reports_cpu_model_and_count(tmp_app):
    res, _ = _run_harness()
    by_name = {c["name"]: c for c in res["checks"]}
    assert by_name["cpu_model"]["status"] == "PASS"
    assert isinstance(by_name["cpu_model"]["value"], str)
    assert by_name["cpu_logical_count"]["status"] == "PASS"
    assert isinstance(by_name["cpu_logical_count"]["value"], int)
    assert by_name["cpu_logical_count"]["value"] >= 1


def test_harness_reports_total_and_available_ram(tmp_app):
    res, _ = _run_harness()
    by_name = {c["name"]: c for c in res["checks"]}
    # Both total and available should be detectable in the sandbox.
    assert by_name["total_memory_mb"]["status"] == "PASS"
    assert by_name["total_memory_mb"]["value"] > 0
    assert by_name["available_memory_mb"]["status"] == "PASS"
    assert by_name["available_memory_mb"]["value"] > 0
    # Available must not exceed total.
    assert by_name["available_memory_mb"]["value"] <= by_name["total_memory_mb"]["value"]


def test_harness_ttft_check_exists_and_not_fabricated(tmp_app):
    """time_to_first_token_ms must exist and, with no model, be NOT_RUN
    with no fabricated value."""
    res, _ = _run_harness()
    by_name = {c["name"]: c for c in res["checks"]}
    assert "time_to_first_token_ms" in by_name
    c = by_name["time_to_first_token_ms"]
    assert c["status"] in ("PASS", "NOT_RUN")
    if c["status"] == "NOT_RUN":
        assert c["value"] is None, "NOT_RUN TTFT must not carry a value"


def test_harness_peak_working_set_check_exists(tmp_app):
    """peak_working_set_mb replaces the Linux-only peak_memory_mb and
    exists as a check name."""
    res, _ = _run_harness()
    by_name = {c["name"]: c for c in res["checks"]}
    assert "peak_working_set_mb" in by_name
    c = by_name["peak_working_set_mb"]
    assert c["status"] in ("PASS", "NOT_RUN")
    if c["status"] == "NOT_RUN":
        assert c["value"] is None


def test_harness_cpu_utilization_check_exists(tmp_app):
    res, _ = _run_harness()
    by_name = {c["name"]: c for c in res["checks"]}
    assert "cpu_utilization_pct" in by_name
    c = by_name["cpu_utilization_pct"]
    assert c["status"] in ("PASS", "NOT_RUN")
    if c["status"] == "NOT_RUN":
        assert c["value"] is None
        assert c["detail"]  # states why (e.g. Linux-only)


def test_harness_clean_shutdown_check_exists(tmp_app):
    res, _ = _run_harness()
    by_name = {c["name"]: c for c in res["checks"]}
    assert "clean_shutdown" in by_name
    c = by_name["clean_shutdown"]
    assert c["status"] in ("PASS", "NOT_RUN", "FAIL")
    if c["status"] == "NOT_RUN":
        assert c["value"] is None


def test_harness_model_tool_call_check_exists_and_distinct(tmp_app):
    """model_tool_call is a distinct check from tool_call_success:
    tool_call_success hand-constructs a ToolCall (engine-only test);
    model_tool_call prompts the MODEL to emit one (model+engine test).
    In the sandbox (no model) model_tool_call is NOT_RUN with a reason,
    never fabricated."""
    res, _ = _run_harness()
    by_name = {c["name"]: c for c in res["checks"]}
    assert "tool_call_success" in by_name
    assert "model_tool_call" in by_name
    # tool_call_success runs in the sandbox (engine-only, no model).
    assert by_name["tool_call_success"]["status"] == "PASS"
    # model_tool_call needs a real model; NOT_RUN in the sandbox.
    mtc = by_name["model_tool_call"]
    assert mtc["status"] in ("PASS", "NOT_RUN", "FAIL")
    if mtc["status"] == "NOT_RUN":
        assert mtc["value"] is None
        assert mtc["detail"]  # states the reason (no model)


def test_harness_model_tool_call_not_fabricated(tmp_app):
    """model_tool_call, when NOT_RUN, must not carry a value."""
    res, _ = _run_harness()
    by_name = {c["name"]: c for c in res["checks"]}
    mtc = by_name["model_tool_call"]
    if mtc["status"] == "NOT_RUN":
        assert mtc["value"] is None


def test_harness_total_inference_latency_not_fabricated(tmp_app):
    res, _ = _run_harness()
    by_name = {c["name"]: c for c in res["checks"]}
    c = by_name["total_inference_latency_ms"]
    if c["status"] == "NOT_RUN":
        assert c["value"] is None


def test_harness_all_not_run_carry_no_value(tmp_app):
    """No NOT_RUN check anywhere in the harness may carry a fabricated
    numeric measurement value."""
    res, _ = _run_harness()
    for c in res["checks"]:
        if c["status"] == "NOT_RUN":
            # memory_target_8gb is advisory and carries the available-mem
            # reading as its value; that is not a fabrication (it is the
            # detected value, not a model measurement). Exclude it.
            if c["name"] == "memory_target_8gb":
                continue
            # All other NOT_RUN checks must have value=None.
            assert c["value"] is None, (
                f"{c['name']} is NOT_RUN but carries value {c['value']}")
