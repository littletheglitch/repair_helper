"""Retrieval and search tests."""
from eda import retrieval, knowledge_base, documents as D, sessions
from pathlib import Path


def test_knowledge_search_finds_facts(tmp_app):
    knowledge_base.load_builtin()
    hits = retrieval.search_knowledge("Ohm's law")
    assert hits
    assert any("Ohm" in h["fact"] for h in hits)


def test_knowledge_search_case_insensitive(tmp_app):
    knowledge_base.load_builtin()
    hits = retrieval.search_knowledge("MOSFET")
    assert hits


def test_search_all_combines(tmp_app):
    knowledge_base.load_builtin()
    p = Path(tmp_app) / "x.txt"
    p.write_text("ACOK goes high when adapter is valid", encoding="utf-8")
    D.import_file(p)
    res = retrieval.search_all("adapter ACOK")
    assert res["knowledge"]
    assert res["documents"]


def test_search_empty_query_returns_empty(tmp_app):
    assert retrieval.search_documents("") == []
    assert retrieval.search_knowledge("") == []
