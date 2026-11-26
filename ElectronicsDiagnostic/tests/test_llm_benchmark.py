"""Tests for the LLM benchmark harness (Phase 3).

Verifies the harness scores the stub backend deterministically and the
aggregate metrics compute.
"""
import pytest

from eda import database
from eda import llm_backend as LB
from eda import llm_benchmark as LBENCH


def test_run_backend_stub_produces_report(tmp_app):
    database.init_db()
    report = LBENCH.run_backend(LB.StubBackend(), reset_db=True)
    assert report.backend == "stub"
    assert len(report.case_scores) == len(LBENCH.CASES)
    # The stub is a perfect tool-caller: it calls a correct engine for every case.
    assert report.engine_selection_rate == 1.0


def test_aggregate_metrics_compute(tmp_app):
    database.init_db()
    report = LBENCH.run_backend(LB.StubBackend(), reset_db=True)
    report.aggregate()
    assert report.total_elapsed_ms >= 0
    assert report.avg_latency_ms >= 0.0
    assert report.avg_tokens_per_s >= 0.0


def test_render_report_produces_table(tmp_app):
    database.init_db()
    report = LBENCH.run_backend(LB.StubBackend(), reset_db=True)
    text = LBENCH.render_report([report])
    assert "Backend" in text
    assert "EngSel" in text  # header column
    assert "stub" in text


def test_cases_include_contradiction_and_gap(tmp_app):
    keys = {c.key for c in LBENCH.CASES}
    assert "contradictory_3valw" in keys
    assert "missing_critical_measurement" in keys
    # Each contradiction/gap case sets the right flag.
    contra = next(c for c in LBENCH.CASES if c.key == "contradictory_3valw")
    assert contra.has_contradiction
    assert contra.expect_refusal


def test_to_dict_serializes(tmp_app):
    import json
    database.init_db()
    report = LBENCH.run_backend(LB.StubBackend(), reset_db=True)
    d = report.to_dict()
    json.dumps(d, ensure_ascii=False)  # must not raise
    assert "engine_selection_rate" in d
    assert "cases" in d
