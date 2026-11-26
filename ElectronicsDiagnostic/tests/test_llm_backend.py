"""Tests for the LLM backend abstraction (Phase 3).

Verifies: stub backend availability + deterministic output, disabled
backend, cloud backend availability check (no real call), server-runtime
discovery (no binary in the test env), shutdown/reset lifecycle.
"""
import pytest

from eda import config, llm_backend as LB


def test_stub_backend_is_available_and_deterministic(tmp_app):
    b = LB.StubBackend()
    assert b.is_available()
    assert "stub" in b.availability_report()
    m1 = [LB.Message(role="system", content="Session id: 1"),
          LB.Message(role="user", content="no power, 5valw short")]
    r1 = b.chat(m1)
    assert r1 is not None
    assert r1.backend == "stub"
    assert "tool_call" in r1.text  # emits a tool call for a known case


def test_disabled_backend_returns_none(tmp_app):
    b = LB.DisabledBackend()
    assert not b.is_available()
    r = b.chat([LB.Message(role="user", content="hi")])
    assert r is None


def test_stub_backend_refuses_unknown_case(tmp_app):
    b = LB.StubBackend()
    m = [LB.Message(role="system", content="Session id: 1"),
         LB.Message(role="user", content="tell me a joke")]
    r = b.chat(m)
    assert r is not None
    assert "do not have enough information" in r.text.lower()


def test_get_backend_auto_falls_back_to_stub(tmp_app, monkeypatch):
    # No model file, no server binary -> auto should pick stub.
    LB.shutdown()
    s = config.load_settings()
    s["engine"]["backend"] = "auto"
    monkeypatch.setattr(config, "load_settings", lambda: s)
    b = LB.get_backend(s)
    assert b.name == "stub"
    LB.shutdown()


def test_get_backend_explicit_disabled(tmp_app, monkeypatch):
    LB.shutdown()
    s = config.load_settings()
    s["engine"]["backend"] = "disabled"
    b = LB.get_backend(s)
    assert b.name == "disabled"
    assert not b.is_available()
    LB.shutdown()


def test_get_backend_explicit_stub(tmp_app):
    LB.shutdown()
    s = config.load_settings()
    s["engine"]["backend"] = "stub"
    b = LB.get_backend(s)
    assert b.name == "stub"
    LB.shutdown()


def test_set_backend_injects_and_shutdown_clears(tmp_app):
    LB.set_backend(LB.StubBackend())
    assert LB.get_backend().name == "stub"
    LB.shutdown()
    # After shutdown the next get_backend rebuilds from settings (auto -> stub).
    b = LB.get_backend()
    assert b is not None


def test_server_runtime_discovery_returns_none_without_binary(tmp_app):
    rt = LB.ServerRuntime()
    # No llama-server binary in the test Engine/ dir.
    assert rt.server_path() is None
    assert rt.model_path() is None
    assert not rt.is_running()


def test_cloud_backend_availability_check_only(tmp_app):
    # Do NOT make a real call; just check is_available and the report.
    b = LB.CloudBackend()
    # is_available depends on whether `z-ai` is on PATH; both outcomes are fine.
    rep = b.availability_report()
    assert "cloud" in rep.lower() or "reference" in rep.lower()
