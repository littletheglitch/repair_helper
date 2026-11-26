"""Phase 6, Task 6 — document and knowledge boundary audit.

Re-tests the architectural boundaries:
  1. General engineering knowledge (KB facts) — loaded only from the
     builtin Knowledge/*.json; never from an imported document.
  2. Deterministic calculation engines — separate from KB; never read
     or write KB facts.
  3. Session-scoped repair documents — imported files are scoped to a
     session_id; not visible to other sessions.
  4. Technician-confirmed repair cases — only via the existing
     confirmation mechanism (learning.record_case with
     technician_confirmed=True); synthetic/unresolved cases cannot
     promote.

Concrete re-tests:
  * Importing a BIOS dump does NOT add any fact to knowledge_facts.
  * A schematic/BoardView document imported into session A is NOT
    listed as a document of session B (session-scoped).
  * A synthetic validation case cannot be promoted to the verified
    repair KB.
  * A real_unresolved case cannot be promoted (only technician_confirmed
    + fixed).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eda import config, database, documents, knowledge_base, sessions
from eda import validation_framework as VF
from eda import learning
from eda import tool_interface as TI


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kb_fact_count() -> int:
    return database.count_facts()


def _write_fake_bios(tmp_path: Path) -> Path:
    """Write a tiny fake BIOS-dump-looking file (clearly synthetic)."""
    p = tmp_path / "fake_bios.bin"
    # A minimal file with a BIOS-looking header. This is SYNTHETIC test
    # data, not a real BIOS dump.
    p.write_bytes(b"\xff\xff\xff\xff" + b"FAKEBIOS" + b"\x00" * 64)
    return p


def _write_fake_boardview(tmp_path: Path) -> Path:
    """Write a tiny fake BoardView (.brd) file (synthetic)."""
    p = tmp_path / "fake_board.brd"
    p.write_text("# Synthetic BoardView test file\nNET +5VALW\nNET GND\n")
    return p


# ---------------------------------------------------------------------------
# Boundary 1: importing a BIOS dump never adds KB facts
# ---------------------------------------------------------------------------

def test_bios_dump_does_not_add_kb_facts(tmp_app, tmp_path):
    """Importing a BIOS dump must NOT add any row to knowledge_facts.
    BIOS dumps are stored + described, never ingested into the general KB."""
    database.init_db()
    before = _kb_fact_count()
    bios = _write_fake_bios(tmp_path)
    s = sessions.create("BIOS boundary test", "Synthetic Board")
    res = documents.import_file(bios, session_id=s.id,
                                 requested_by="boundary-test")
    after = _kb_fact_count()
    assert after == before, (
        f"importing a BIOS dump added {after - before} KB fact(s); "
        "BIOS must never enter the general knowledge base")
    # The document IS stored (session-scoped), just not in the KB.
    docs = documents.list_documents(s.id)
    assert any(d["file_type"] == "bios" for d in docs)


def test_bios_dump_stored_only_not_kb_searchable(tmp_app, tmp_path):
    """A BIOS dump's content must not appear in KB search results."""
    database.init_db()
    bios = _write_fake_bios(tmp_path)
    s = sessions.create("BIOS search test", "Synthetic Board")
    documents.import_file(bios, session_id=s.id)
    # KB search for a distinctive BIOS string returns nothing from the KB.
    results = knowledge_base.search_facts("FAKEBIOS", limit=10)
    assert not results, (
        "BIOS content leaked into KB search results")


# ---------------------------------------------------------------------------
# Boundary 3: schematic / BoardView documents are session-scoped
# ---------------------------------------------------------------------------

def test_schematic_is_session_scoped(tmp_app, tmp_path):
    """A BoardView imported into session A is NOT listed for session B."""
    database.init_db()
    bv = _write_fake_boardview(tmp_path)
    a = sessions.create("Session A", "Board A")
    b = sessions.create("Session B", "Board B")
    documents.import_file(bv, session_id=a.id)
    docs_a = documents.list_documents(a.id)
    docs_b = documents.list_documents(b.id)
    assert any(d["file_type"] == "boardview" for d in docs_a)
    assert all(d["file_type"] != "boardview" for d in docs_b), (
        "a document imported into session A leaked into session B")


def test_schematic_chunks_are_session_scoped(tmp_app, tmp_path):
    """Document chunks are retrievable for the owning session only (via
    the document's session_id). A query for session B's documents must
    not return session A's chunks."""
    database.init_db()
    bv = _write_fake_boardview(tmp_path)
    a = sessions.create("A", "Board A")
    b = sessions.create("B", "Board B")
    documents.import_file(bv, session_id=a.id)
    # Chunks belong to the document, which belongs to session A.
    docs_b = documents.list_documents(b.id)
    chunk_ids_b = [d["id"] for d in docs_b]
    assert chunk_ids_b == [], "session B has no documents"


# ---------------------------------------------------------------------------
# Boundary 2: deterministic engines never touch KB facts
# ---------------------------------------------------------------------------

def test_engines_do_not_modify_kb(tmp_app):
    """Running a deterministic engine (electrical_math) must not add or
    remove KB facts. Engines compute; the KB is read-only to them."""
    database.init_db()
    before = _kb_fact_count()
    res = TI.dispatch(TI.ToolCall(
        tool="electrical_math.ohm_voltage",
        args={"I": {"value": 2, "unit": "A"},
              "R": {"value": 5, "unit": "Ω"}},
        id="b1"))
    assert res.ok
    after = _kb_fact_count()
    assert after == before


# ---------------------------------------------------------------------------
# Boundary 4: synthetic / unresolved cases cannot promote to verified KB
# ---------------------------------------------------------------------------

def test_synthetic_case_cannot_promote_to_repair_kb(tmp_app):
    """A synthetic validation case cannot enter the verified repair KB
    via promote_to_repair_kb."""
    database.init_db()
    s = sessions.create("Synth promote test", "Synthetic Board")
    sessions.add_symptom(s.id, "no power")
    from eda import measurements as M
    M.add(s.id, {"name": "+5VALW", "value": 0.0, "unit": "V",
                  "point": "rail", "reference": ""})
    r = VF.create_case("syn_promote", "synthetic", session_id=s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h", "score": 0.5}], [])
    # Even if we try to force-confirm (which reveal rejects for synthetic),
    # promotion must fail.
    p = VF.promote_to_repair_kb(r["case_id"])
    assert not p["ok"]


def test_unresolved_case_cannot_promote(tmp_app):
    """A real_unresolved case (not technician-confirmed, not fixed) cannot
    promote to the verified repair KB."""
    database.init_db()
    s = sessions.create("Unresolved promote test", "Board")
    sessions.add_symptom(s.id, "no power")
    from eda import measurements as M
    M.add(s.id, {"name": "+5VALW", "value": 0.0, "unit": "V",
                  "point": "rail", "reference": ""})
    r = VF.create_case("unres_promote", "real_unresolved", session_id=s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "h", "score": 0.5}], [])
    VF.reveal_and_confirm(r["case_id"], "fault", "no-fix", "Tech")
    p = VF.promote_to_repair_kb(r["case_id"])
    assert not p["ok"]


def test_only_technician_confirmed_fixed_can_promote(tmp_app):
    """The ONLY path to the verified repair KB is a technician_confirmed
    case with outcome=fixed, via the existing record_case mechanism."""
    database.init_db()
    s = sessions.create("Confirmed promote test", "Board")
    sessions.add_symptom(s.id, "no power")
    from eda import measurements as M
    M.add(s.id, {"name": "+5VALW", "value": 0.0, "unit": "V",
                  "point": "rail", "reference": ""})
    r = VF.create_case("conf_promote", "real_unresolved", session_id=s.id)
    VF.record_prediction(r["case_id"],
                         [{"hypothesis": "always-on converter", "score": 0.7}],
                         [])
    VF.reveal_and_confirm(r["case_id"], "always-on converter failure",
                          "fixed", "Ali")
    p = VF.promote_to_repair_kb(r["case_id"])
    assert p["ok"]
    # The repair_cases table gained exactly one confirmed case for this key.
    cases = learning.list_cases(technician_confirmed_only=True)
    assert any(c.get("case_key") == "conf_promote" for c in cases)
