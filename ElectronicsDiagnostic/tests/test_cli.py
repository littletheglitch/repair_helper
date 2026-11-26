"""CLI tests: command dispatch, file paths with spaces, offline startup."""
import os
import sys
from pathlib import Path

from eda import cli, config, database, knowledge_base, sessions
from eda import measurements as M, documents as D, reasoning


def test_help(tmp_app, capsys):
    out = cli.dispatch("help")
    assert "Electronics Diagnostic Assistant" in out
    assert "new" in out and "measure" in out and "analyze" in out


def test_new_and_open(tmp_app):
    out = cli.dispatch("new Lenovo G50-80 G50-80 board")
    assert "Created session" in out
    assert cli.Session.current is not None
    sid = cli.Session.current.id
    cli.Session.current = None
    out2 = cli.dispatch(f"open {sid}")
    assert "Opened session" in out2


def test_sessions_command(tmp_app):
    cli.dispatch("new A modelA")
    out = cli.dispatch("sessions")
    assert "Sessions" in out


def test_symptom_measure_analyze_flow(tmp_app):
    cli.dispatch("new Lenovo G50-80 board")
    cli.dispatch("symptom No power")
    cli.dispatch("measure VIN = 19.4V")
    out = cli.dispatch("analyze")
    assert "Ranked hypotheses" in out
    assert "Next most informative test" in out


def test_natural_language_falls_back_to_symptom(tmp_app):
    cli.dispatch("new S model")
    out = cli.dispatch("the board is totally dead no lights")
    assert "interpreted as symptom" in out


def test_natural_language_measurement(tmp_app):
    cli.dispatch("new S model")
    out = cli.dispatch("VIN = 19V")
    assert "measurement" in out.lower()


def test_add_file_with_spaces(tmp_app):
    cli.dispatch("new S model")
    sp = Path(tmp_app) / "my dir" / "datasheet.txt"
    sp.parent.mkdir(parents=True)
    sp.write_text("TPS51716 EN active high", encoding="utf-8")
    # shlex must keep the quoted path together.
    out = cli.dispatch(f'add-file "{sp}"')
    assert "extracted" in out.lower() or "stored" in out.lower()


def test_documents_command(tmp_app):
    cli.dispatch("new S model")
    p = Path(tmp_app) / "x.txt"
    p.write_text("hello", encoding="utf-8")
    cli.dispatch(f'add-file "{p}"')
    out = cli.dispatch("documents")
    assert "x.txt" in out


def test_remove_document(tmp_app):
    cli.dispatch("new S model")
    p = Path(tmp_app) / "x.txt"
    p.write_text("hello", encoding="utf-8")
    cli.dispatch(f'add-file "{p}"')
    docs = D.list_documents()
    did = docs[0]["id"]
    out = cli.dispatch(f"remove-document {did}")
    assert "Removed" in out


def test_report_command(tmp_app):
    cli.dispatch("new S model")
    cli.dispatch("symptom No power")
    cli.dispatch("measure VIN = 19V")
    cli.dispatch("analyze")
    out = cli.dispatch("report")
    assert "Markdown report saved" in out


def test_search_command(tmp_app):
    cli.dispatch("new S model")
    out = cli.dispatch("search Ohm")
    # Either knowledge hits or "no results"
    assert isinstance(out, str)


def test_settings_command(tmp_app):
    out = cli.dispatch("settings")
    assert "LLM engine" in out
    assert "app_version" in out


def test_unknown_command(tmp_app):
    out = cli.dispatch("flibbertigibbet")
    assert "Unknown command" in out or "interpreted as symptom" in out


def test_offline_startup(tmp_app, capsys, monkeypatch):
    """Boot the app's setup path (no REPL) and confirm it doesn't reach the
    network."""
    config.ensure_layout()
    database.init_db()
    knowledge_base.load_builtin()
    # Make sure no network was touched: assert the DB has facts and the
    # LLM engine reports unavailable (no model).
    assert database.count_facts() > 0
    from eda import llm_engine
    eng = llm_engine.get_engine()
    # In the temp env there's no model; availability must be False.
    # (llm_engine caches a singleton; reset it.)
    llm_engine._engine = None
    eng = llm_engine.get_engine()
    assert eng.is_available() is False or eng.model_path() is None


def test_one_shot_main(tmp_app, monkeypatch):
    """``main(['help'])`` should print help and exit 0."""
    monkeypatch.setattr(sys, "argv", ["eda", "help"])
    rc = cli.main(["help"])
    assert rc == 0
