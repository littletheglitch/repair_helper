"""LLM engine tests: missing model, missing runtime, disabled backend,
graceful fallback to the rule engine."""
from eda import llm_engine, config


def test_missing_model_returns_none(tmp_app, monkeypatch):
    # No model file in the temp Models dir.
    eng = llm_engine.LLMEngine()
    assert eng.model_path() is None
    assert not eng.is_available()
    r = eng.generate("hello")
    assert r is None


def test_missing_runtime_returns_none(tmp_app, monkeypatch):
    # Place a dummy .gguf so the model is "found" but no CLI exists.
    (config.app_dir("Models")).mkdir(parents=True, exist_ok=True)
    (config.app_dir("Models") / "fake.gguf").write_bytes(b"\x00" * 64)
    eng = llm_engine.LLMEngine()
    assert eng.model_path() is not None
    # CLI is missing in the test environment.
    assert eng.cli_path() is None or not eng.is_available()
    r = eng.generate("hello")
    # Either None or an LLMResponse noting failure.
    if r is not None:
        assert r.note or r.text == ""


def test_disabled_backend(tmp_app, monkeypatch):
    settings = config.DEFAULT_SETTINGS.copy()
    settings["engine"] = {"backend": "disabled"}
    monkeypatch.setattr(config, "load_settings", lambda: settings)
    eng = llm_engine.LLMEngine(settings)
    assert not eng.is_available()
    assert eng.generate("hi") is None


def test_availability_report_honest(tmp_app):
    eng = llm_engine.LLMEngine()
    rep = eng.availability_report()
    assert "Available" in rep
    assert "Falling back" in rep or "Available: True" in rep


def test_paraphrase_returns_none_without_model(tmp_app):
    out = llm_engine.paraphrase_analysis("Some analysis text", ["no power"])
    assert out is None
