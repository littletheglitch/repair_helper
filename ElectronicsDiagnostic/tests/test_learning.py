"""Tests for the controlled learning workflow."""
import json

from eda import learning as L, sessions, measurements as M, database, knowledge_base


def _seed_session():
    s = sessions.create("Lenovo G50-80", "G50-80 board")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    M.add(s.id, M.parse_line("measure +5VALW = 0V"))
    return s


def test_record_case_requires_session(tmp_app):
    r = L.record_case(999999, "short", [], "measured", "fixed")
    assert not r["ok"]


def test_record_case_requires_fault(tmp_app):
    s = _seed_session()
    r = L.record_case(s.id, "", [], "t", "o")
    assert not r["ok"]


def test_record_technician_confirmed_case(tmp_app):
    s = _seed_session()
    r = L.record_case(s.id, "shorted +5VALW capacitor",
                      ["C45"], "resistance back to 480ohm after replacing C45",
                      "board powers on", technician_confirmed=True)
    assert r["ok"]
    assert r["version"] == 1
    # The case is persisted.
    rows = database.query_all("SELECT * FROM repair_cases")
    assert rows
    assert rows[0]["technician_confirmed"] == 1


def test_confirmed_case_enters_verified_kb(tmp_app):
    s = _seed_session()
    L.record_case(s.id, "shorted cap", ["C45"], "resistance check",
                  "fixed", technician_confirmed=True)
    knowledge_base.load_builtin()
    # Look for a technician-confirmed tier fact about this case.
    facts = knowledge_base.facts_by_tier("technician-confirmed")
    assert any("Technician-confirmed repair" in f["fact"] for f in facts)


def test_unverified_case_does_not_enter_verified_kb(tmp_app):
    s = _seed_session()
    L.record_case(s.id, "shorted cap", ["C45"], "resistance check",
                  "fixed", technician_confirmed=False)
    facts = knowledge_base.facts_by_tier("technician-confirmed")
    assert not any("shorted cap" in f["fact"] for f in facts)


def test_case_versioning(tmp_app):
    s = _seed_session()
    L.record_case(s.id, "fault_v1", [], "t", "o", case_key="case-A")
    L.record_case(s.id, "fault_v2", [], "t", "o", case_key="case-A")
    latest = L.get_case("case-A")
    assert latest["version"] == 2
    assert latest["actual_fault"] == "fault_v2"


def test_case_revert_preserves_audit_trail(tmp_app):
    s = _seed_session()
    L.record_case(s.id, "fault_v1", [], "t", "o", case_key="case-B")
    L.record_case(s.id, "fault_v2", [], "t", "o", case_key="case-B")
    r = L.revert_case("case-B", to_version=1)
    assert r["ok"]
    latest = L.get_case("case-B")
    # New version = 3, content matches v1.
    assert latest["version"] == 3
    assert latest["actual_fault"] == "fault_v1"
    # Audit trail still has all versions.
    trail = L.case_audit_trail("case-B")
    assert len(trail) == 3


def test_list_cases_returns_latest_version(tmp_app):
    s = _seed_session()
    L.record_case(s.id, "f1", [], "t", "o", case_key="C")
    L.record_case(s.id, "f2", [], "t", "o", case_key="C")
    cases = L.list_cases()
    assert len(cases) == 1  # only the latest version per case_key
    assert cases[0]["actual_fault"] == "f2"


def test_list_confirmed_only(tmp_app):
    s = _seed_session()
    L.record_case(s.id, "confirmed", [], "t", "o", case_key="X",
                  technician_confirmed=True)
    L.record_case(s.id, "unverified", [], "t", "o", case_key="Y",
                  technician_confirmed=False)
    cases = L.list_cases(technician_confirmed_only=True)
    keys = {c["case_key"] for c in cases}
    assert "X" in keys and "Y" not in keys


def test_audit_trail_is_auditable(tmp_app):
    s = _seed_session()
    L.record_case(s.id, "f1", [], "t", "o", case_key="Z")
    L.record_case(s.id, "f2", [], "t", "o", case_key="Z")
    trail = L.case_audit_trail("Z")
    assert len(trail) == 2
    assert trail[0]["version"] == 1
    assert trail[1]["version"] == 2
