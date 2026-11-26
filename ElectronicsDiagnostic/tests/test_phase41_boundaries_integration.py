"""Phase 4.1 — Architectural boundary + integration tests.

D11/D12: assert that BIOS / schematic / BoardView documents are NOT
auto-imported to the general knowledge base, and that proprietary
repair docs stay scoped to their repair session. Also assert that the
learning subsystem does not distort evidence/measurements when it
reads a repair session, and that Phase 3 behavior (the LLM-driven
orchestrator) is unchanged.
"""
import json
import pytest

from eda import config, database, sessions, measurements, knowledge_base
from eda import documents, cli
from eda.learning_system import RepairToLearning, LearningSession


# ---- D11: documents are never auto-imported to the general KB -------

def test_importing_a_document_does_not_add_kb_facts(tmp_app, tmp_path):
    """Importing a BIOS/schematic/BoardView file must NOT add any fact to
    knowledge_facts. The KB only gains facts via explicit, sourced
    import_fact (technician-confirmed cases)."""
    database.init_db()
    before = database.count_facts()
    # Make a fake BIOS-like file and import it. The import may succeed
    # or fail (extraction may not be possible), but either way it must
    # NOT add a KB fact.
    bios = tmp_path / "fake_bios.bin"
    bios.write_bytes(b"\x00\x01\x02BIOS\xff" * 16)
    documents.import_file(bios)
    after = database.count_facts()
    assert after == before, ("importing a document must not add KB facts; "
                             f"before={before} after={after}")


def test_repair_to_learning_does_not_import_facts(tmp_app):
    """Converting a repair session to a learning case must NOT promote
    anything to the general KB (that is the existing learning.py
    record_case path, gated on technician confirmation, not this)."""
    database.init_db()
    before = database.count_facts()
    s = sessions.create("Boundary test", "Lenovo G50-80")
    sessions.add_symptom(s.id, "no power")
    measurements.add(s.id, {"name": "VIN", "value": 19.4, "unit": "V"})
    r2l = RepairToLearning()
    res = r2l.convert(s.id, seed=7)
    assert res["ok"]
    after = database.count_facts()
    assert after == before, ("repair-to-learning must not add KB facts; "
                             f"before={before} after={after}")


def test_only_technician_confirmed_cases_promote_to_kb(tmp_app):
    """The existing learning.record_case promotes ONLY technician-confirmed
    cases (confirmed=1) to the KB; unconfirmed cases do NOT enter the KB.
    This is the architectural boundary: confirmed repairs -> KB; learning
    exercises / unconfirmed -> never."""
    from eda import learning as repair_learning
    database.init_db()
    before = database.count_facts()
    s = sessions.create("Confirmed test", "Lenovo G50-80")
    sessions.add_symptom(s.id, "no power")
    measurements.add(s.id, {"name": "VIN", "value": 19.4, "unit": "V"})
    # Unconfirmed case -> must NOT add a KB fact.
    repair_learning.record_case(
        s.id, "shorted 5VALW", ["C123"], "resistance to ground",
        "fixed", technician_confirmed=False)
    assert database.count_facts() == before, "unconfirmed case leaked to KB"
    # Confirmed case -> DOES add a KB fact (category=repair-case).
    repair_learning.record_case(
        s.id, "shorted 5VALW", ["C123"], "resistance to ground",
        "fixed", technician_confirmed=True)
    after = database.count_facts()
    assert after == before + 1, ("confirmed case should add exactly one "
                                 "KB fact")
    # The added fact is a repair-case category, not a general one.
    facts = knowledge_base.search_facts("shorted 5VALW", limit=5)
    cats = {f.get("category") for f in facts}
    assert "repair-case" in cats


def test_documents_are_session_scoped(tmp_app, tmp_path):
    """A document imported for session A must not surface as belonging
    to session B."""
    database.init_db()
    s1 = sessions.create("Session A", "BoardA")
    s2 = sessions.create("Session B", "BoardB")
    f = tmp_path / "sch.txt"
    f.write_text("SCHEMATIC BoardA")
    documents.import_file(f, session_id=s1.id)
    docs_a = documents.list_documents(session_id=s1.id)
    docs_b = documents.list_documents(session_id=s2.id)
    assert len(docs_a) == 1
    assert len(docs_b) == 0, "document from session A leaked to session B"


# ---- Integration: evidence/measurements transferred without distortion ----

def test_repair_to_learning_preserves_measurements(tmp_app):
    """The measurements extracted by repair-to-learning must match the
    original session measurements exactly (no distortion)."""
    database.init_db()
    s = sessions.create("Distortion test", "Lenovo G50-80")
    sessions.add_symptom(s.id, "no power, no LEDs")
    measurements.add(s.id, {"name": "VIN", "value": 19.4, "unit": "V",
                            "point": "input"})
    measurements.add(s.id, {"name": "+5VALW", "value": 0.0, "unit": "V",
                            "point": "rail"})
    orig = {m.name: m.value for m in measurements.list_for(s.id)}
    r2l = RepairToLearning()
    res = r2l.convert(s.id, seed=7)
    case = res["case"]
    extracted = {m["name"]: m["value"] for m in case["measurements"]}
    assert extracted == orig, ("measurements distorted in transfer; "
                              f"orig={orig} extracted={extracted}")


def test_repair_to_learning_preserves_symptoms(tmp_app):
    """Symptoms must transfer verbatim."""
    database.init_db()
    s = sessions.create("Symptom test", "Lenovo G50-80")
    sessions.add_symptom(s.id, "no power, no LEDs")
    sessions.add_symptom(s.id, "buzzing sound")
    r2l = RepairToLearning()
    res = r2l.convert(s.id, seed=7)
    case = res["case"]
    assert "no power, no LEDs" in case["symptoms"]
    assert "buzzing sound" in case["symptoms"]


# ---- Phase 3 behavior unchanged --------------------------------------

def test_orchestrate_command_still_works(tmp_app):
    """The LLM-driven orchestrator (Phase 3) must still dispatch."""
    database.init_db()
    cli.dispatch("new TestBoard Lenovo")
    cli.dispatch("symptom no power")
    # orchestrate with the stub backend must not error.
    out = cli.dispatch("orchestrate --backend stub")
    # It should produce some output (a hypothesis or an error message
    # about missing measurements, but NOT a command-not-found).
    assert "Unknown command" not in out
    assert "Traceback" not in out


def test_analyze_command_still_works(tmp_app):
    """The rule-based analyze (Phase 1) must still work."""
    database.init_db()
    cli.dispatch("new TestBoard Lenovo")
    cli.dispatch("symptom no power")
    cli.dispatch("measure VIN = 19.4V")
    out = cli.dispatch("analyze")
    # The command must run without a traceback and produce analysis
    # output (it may start with a SAFETY preamble).
    assert "Traceback" not in out
    assert "Unknown command" not in out
    # With VIN measured and a no-power symptom, analyze should produce
    # at least one hypothesis or a no-fault note.
    low = out.lower()
    assert ("hypothesis" in low or "analysis" in low or "no fault" in low
            or "safety" in low)


def test_learn_record_still_works(tmp_app):
    """The existing 'learn record' command (Phase 1 repair-case recorder)
    must still work, distinct from the new 'learning' commands."""
    database.init_db()
    cli.dispatch("new TestBoard Lenovo")
    cli.dispatch("symptom no power")
    cli.dispatch("measure VIN = 19.4V")
    out = cli.dispatch("learn record shorted 5VALW --replaced C123 --confirming resistance --outcome fixed")
    assert "Recorded case" in out


def test_learning_and_learn_record_are_distinct(tmp_app):
    """The 'learning' family (Phase 4) and 'learn record' (Phase 1) are
    distinct commands — no overlap or conflict."""
    database.init_db()
    h = cli.dispatch("help")
    assert "learn record" in h
    assert "learning start" in h
    # The 'learning' command with no subcommand prints help, not an error.
    out = cli.dispatch("learning")
    assert "Subcommands" in out or "learning start" in out
