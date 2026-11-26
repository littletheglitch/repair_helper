"""Tests for the new Phase 3 CLI commands (additive; existing CLI
commands are unchanged and covered by the existing test_cli.py)."""
import pytest

from eda import cli, config, database


def test_help_lists_new_commands(tmp_app):
    database.init_db()
    h = cli.dispatch("help")
    for k in ("orchestrate", "backend", "models", "model-settings",
              "ram", "context", "tools", "llm-benchmark"):
        assert k in h, f"help missing new command '{k}'"


def test_tools_lists_catalog(tmp_app):
    database.init_db()
    out = cli.dispatch("tools")
    assert "electrical_math.ohm_voltage" in out
    assert "next_best_test.recommend" in out


def test_backend_show_reports_stub(tmp_app, monkeypatch):
    database.init_db()
    from eda import llm_backend as LB
    LB.shutdown()
    s = config.load_settings()
    s["engine"]["backend"] = "stub"
    monkeypatch.setattr(config, "load_settings", lambda: s)
    out = cli.dispatch("backend show")
    assert "stub" in out
    assert "Available: True" in out
    LB.shutdown()


def test_backend_unknown_returns_usage(tmp_app):
    out = cli.dispatch("backend notarealbackend")
    assert "Usage" in out


def test_models_list_lists_classes(tmp_app):
    database.init_db()
    out = cli.dispatch("models list")
    assert "1b-1.5b" in out
    assert "3b-4b" in out
    assert "7b-8b" in out
    assert "Qwen2.5-1.5B" in out
    assert "not bundled into the exe" in out.lower()


def test_models_unknown_subcommand(tmp_app):
    out = cli.dispatch("models bogus")
    assert "Usage" in out


def test_model_settings_show_and_set(tmp_app):
    database.init_db()
    out = cli.dispatch("model-settings")
    assert "n_ctx" in out
    out2 = cli.dispatch("model-settings n_ctx 3072")
    assert "n_ctx = 3072" in out2
    out3 = cli.dispatch("model-settings")
    assert "3072" in out3


def test_model_settings_unknown_key(tmp_app):
    out = cli.dispatch("model-settings bogus_key 5")
    assert "Unknown" in out


def test_ram_no_process(tmp_app):
    database.init_db()
    out = cli.dispatch("ram")
    assert "llama-server" in out or "process" in out


def test_context_requires_open_session(tmp_app):
    database.init_db()
    from eda.cli import Session
    Session.current = None
    out = cli.dispatch("context")
    # No session open -> empty or a hint.
    assert out == "" or "session" in out.lower()


def test_llm_benchmark_stub_runs(tmp_app):
    database.init_db()
    out = cli.dispatch("llm-benchmark --backends stub")
    assert "stub" in out
    assert "EngSel" in out  # the comparison header


def test_orchestrate_requires_session_and_data(tmp_app):
    database.init_db()
    from eda.cli import Session
    Session.current = None
    out = cli.dispatch("orchestrate")
    assert out == "" or "session" in out.lower()
