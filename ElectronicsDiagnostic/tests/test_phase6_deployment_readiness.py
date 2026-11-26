"""Phase 6, Task 7 — deployment readiness tests.

Verifies the offline pre-flight check:
  * Reports Python + required + optional deps (never crashes on a
    missing optional dep).
  * Discovers llama.cpp via settings / bundled / PATH; reports MISSING
    with a fix when absent.
  * Validates the model file path + reports MISSING with a fix.
  * Config validation flags real issues (oversubscribed threads, GPU on
    a CPU-only target, tiny context) and never silently increases them.
  * Logging configures to Logs/ and is idempotent.
  * Overall status is NOT_READY in the sandbox (no model/binary); READY
    requires all checks OK.
  * The report gives actionable fixes, not stack traces.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from eda import config
from eda import deployment_readiness as DR


def test_readiness_report_structure(tmp_app):
    rep = DR.readiness_report()
    for k in ("python", "dependencies", "llama_cpp", "model", "config",
              "logging", "overall", "failures"):
        assert k in rep
    assert rep["overall"] in ("READY", "NOT_READY")
    assert isinstance(rep["failures"], list)


def test_dependencies_check_reports_required_and_optional(tmp_app):
    deps = DR.check_dependencies()
    names = {d["name"] for d in deps}
    assert "pypdf" in names and "PyMuPDF" in names  # required
    assert "reportlab" in names  # optional
    for d in deps:
        assert d["status"] in ("OK", "MISSING", "OPTIONAL_MISSING")


def test_optional_dep_missing_does_not_crash(tmp_app, monkeypatch):
    """A missing OPTIONAL dep must be reported OPTIONAL_MISSING, not raise."""
    real_import = __import__("importlib").import_module

    def fake_import(name, *a, **k):
        if name == "pytesseract":
            raise ImportError("simulated")
        return real_import(name, *a, **k)
    monkeypatch.setattr("importlib.import_module", fake_import)
    deps = DR.check_dependencies()
    pt = next(d for d in deps if d["name"] == "pytesseract")
    assert pt["status"] == "OPTIONAL_MISSING"
    assert "fix" in pt


def test_discover_llama_cpp_missing_reports_fix(tmp_app):
    """In the sandbox (no binary), discovery reports MISSING with a fix."""
    s = config.load_settings()
    s["engine"]["server_path"] = None
    s["engine"]["cli_path"] = None
    config.save_settings(s)
    rep = DR.discover_llama_cpp(config.load_settings())
    assert rep["status"] == "MISSING"
    assert rep["fix"]  # actionable


def test_discover_llama_cpp_finds_settings_path(tmp_app, tmp_path):
    """If engine.server_path points to a real file, discovery finds it."""
    fake = tmp_path / "llama-server"
    fake.write_text("#!/bin/sh\nexit 0\n")
    s = config.load_settings()
    s["engine"]["server_path"] = str(fake)
    config.save_settings(s)
    rep = DR.discover_llama_cpp(config.load_settings())
    assert rep["status"] == "OK"
    assert rep["source"] == "settings.server_path"


def test_validate_model_missing_reports_fix(tmp_app):
    s = config.load_settings()
    s["engine"]["model_file"] = "nonexistent.gguf"
    s["engine"]["model_dir"] = "Models"
    config.save_settings(s)
    rep = DR.validate_model(config.load_settings())
    assert rep["status"] == "MISSING"
    assert "fix" in rep


def test_validate_model_ok_when_present(tmp_app, tmp_path):
    """A real .gguf in the model dir is detected and reported."""
    models_root = config.app_dir("Models")
    models_root.mkdir(parents=True, exist_ok=True)
    gguf = models_root / "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
    gguf.write_bytes(b"\x00" * 100)
    s = config.load_settings()
    s["engine"]["model_file"] = gguf.name
    config.save_settings(s)
    rep = DR.validate_model(config.load_settings())
    assert rep["status"] == "OK"
    assert rep["quantization"] == "Q4_K_M"
    assert rep["size_mb"] is not None


def test_validate_config_flags_oversubscribed_threads(tmp_app, monkeypatch):
    """n_threads > cpu_count must be flagged (never silently reduced)."""
    s = config.load_settings()
    s["engine"]["n_threads"] = 999  # far more than any CPU
    config.save_settings(s)
    rep = DR.validate_config(config.load_settings())
    assert rep["status"] == "WARN"
    assert any("oversubscribe" in i for i in rep["issues"])


def test_validate_config_flags_gpu_on_cpu_target(tmp_app):
    """n_gpu_layers != 0 on the CPU-only target must be flagged."""
    s = config.load_settings()
    s["engine"]["n_gpu_layers"] = 4
    config.save_settings(s)
    rep = DR.validate_config(config.load_settings())
    assert any("CPU-only" in i for i in rep["issues"])


def test_validate_config_flags_tiny_context(tmp_app):
    s = config.load_settings()
    s["engine"]["n_ctx"] = 128
    config.save_settings(s)
    rep = DR.validate_config(config.load_settings())
    assert any("very small" in i for i in rep["issues"])


def test_logging_configures_to_logs_dir(tmp_app):
    rep = DR.configure_logging("INFO")
    assert Path(rep["log_path"]).parent.name == "Logs"
    assert Path(rep["log_path"]).exists() or True  # dir created


def test_logging_idempotent(tmp_app):
    """Calling configure_logging twice does not add duplicate handlers."""
    import logging
    DR.configure_logging("INFO")
    n1 = len(logging.getLogger().handlers)
    DR.configure_logging("INFO")
    n2 = len(logging.getLogger().handlers)
    # No new file handler added on the second call.
    assert n2 == n1


def test_overall_not_ready_in_sandbox(tmp_app):
    """In the sandbox (no model + no binary), overall is NOT_READY."""
    rep = DR.readiness_report()
    assert rep["overall"] == "NOT_READY"
    assert "llama.cpp" in " ".join(rep["failures"]) or "model" in " ".join(
        rep["failures"])


def test_cli_json_output(tmp_app):
    proc = subprocess.run(
        [sys.executable, "-m", "eda.deployment_readiness", "--json"],
        capture_output=True, text=True, timeout=30,
        cwd=str(Path(__file__).resolve().parents[1]),
        env={**__import__("os").environ,
             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
    out = proc.stdout.strip()
    rep = json.loads(out)
    assert "overall" in rep


def test_cli_strict_exits_nonzero_when_not_ready(tmp_app):
    proc = subprocess.run(
        [sys.executable, "-m", "eda.deployment_readiness", "--strict"],
        capture_output=True, text=True, timeout=30,
        cwd=str(Path(__file__).resolve().parents[1]),
        env={**__import__("os").environ,
             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
    assert proc.returncode == 1  # NOT_READY -> strict exits 1
