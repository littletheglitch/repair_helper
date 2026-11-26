"""Tests for the structured hypothesis manager."""
from eda.engines import base as E
from eda.engines import hypotheses as H
from eda import schemas as S, sessions, measurements as M, database


def test_analyze_returns_structured_hypotheses(tmp_app):
    s = sessions.create("Lenovo G50-80", "G50-80 board")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    M.add(s.id, M.parse_line("measure +3VALW = 3.3V"))
    M.add(s.id, M.parse_line("measure +5VALW = 0V"))
    r = E.call("hypotheses", "analyze", {"session_id": s.id})
    assert r.ok
    hyps = r.value["hypotheses"]
    assert len(hyps) >= 2
    # Each hypothesis has a structured status.
    for h in hyps:
        assert h["status"] in (
            H.STATUS_CONFIRMED, H.STATUS_STRONGLY_SUPPORTED,
            H.STATUS_PLAUSIBLE, H.STATUS_UNCERTAIN,
            H.STATUS_CONTRADICTED, H.STATUS_REJECTED,
        )
        assert h["confidence_basis"]
        assert isinstance(h["supporting"], list)
        assert isinstance(h["contradicting"], list)
        assert isinstance(h["required_evidence"], list)


def test_no_confirmed_on_insufficient_evidence(tmp_app):
    """The sample case must NOT produce a confirmed diagnosis."""
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    M.add(s.id, M.parse_line("measure +3VALW = 3.3V"))
    M.add(s.id, M.parse_line("measure +5VALW = 0V"))
    r = E.call("hypotheses", "analyze", {"session_id": s.id})
    assert r.ok
    assert r.value["confirmed"] is None, "should not be confirmed on this evidence"


def test_status_re_ranking_confirmed_first(tmp_app):
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    M.add(s.id, M.parse_line("measure +3VALW = 3.3V"))
    M.add(s.id, M.parse_line("measure +5VALW = 0V"))
    r = E.call("hypotheses", "analyze", {"session_id": s.id})
    statuses = [h["status"] for h in r.value["hypotheses"]]
    # Contradicted hypotheses should appear after plausible ones.
    if H.STATUS_CONTRADICTED in statuses and H.STATUS_PLAUSIBLE in statuses:
        assert statuses.index(H.STATUS_PLAUSIBLE) < statuses.index(H.STATUS_CONTRADICTED)


def test_confirm_requires_existing_hypothesis(tmp_app):
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    E.call("hypotheses", "analyze", {"session_id": s.id})
    # Get the first hypothesis key.
    lst = E.call("hypotheses", "list", {"session_id": s.id})
    key = lst.value["hypotheses"][0]["hypothesis_key"]
    r = E.call("hypotheses", "confirm",
               {"session_id": s.id, "hypothesis_key": key,
                "basis": "technician visually confirmed"})
    assert r.ok
    # Confirming writes a 'confirmed' timeline entry.
    tl = sessions.timeline(s.id)
    assert any(t["kind"] == "confirmed" for t in tl)


def test_reject_marks_hypothesis_rejected(tmp_app):
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    E.call("hypotheses", "analyze", {"session_id": s.id})
    lst = E.call("hypotheses", "list", {"session_id": s.id})
    key = lst.value["hypotheses"][0]["hypothesis_key"]
    r = E.call("hypotheses", "reject",
               {"session_id": s.id, "hypothesis_key": key})
    assert r.ok
    lst2 = E.call("hypotheses", "list", {"session_id": s.id})
    statuses = {h["hypothesis_key"]: h["status"] for h in lst2.value["hypotheses"]}
    assert statuses[key] == H.STATUS_REJECTED


def test_confirm_nonexistent_hypothesis_errors(tmp_app):
    s = sessions.create("S", "")
    r = E.call("hypotheses", "confirm",
               {"session_id": s.id, "hypothesis_key": "no_such_hypothesis"})
    assert not r.ok
    assert r.error.code == S.ErrorCode.NOT_APPLICABLE


def test_confidence_basis_is_qualitative_not_numeric(tmp_app):
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    r = E.call("hypotheses", "analyze", {"session_id": s.id})
    for h in r.value["hypotheses"]:
        assert "no numerical probability" in h["confidence_basis"]


def test_analyze_missing_session(tmp_app):
    r = E.call("hypotheses", "analyze", {"session_id": 999999})
    assert not r.ok
    assert r.error.code == S.ErrorCode.NOT_APPLICABLE


def test_analyze_no_data_errors(tmp_app):
    s = sessions.create("S", "")
    r = E.call("hypotheses", "analyze", {"session_id": s.id})
    assert not r.ok
    assert r.error.code == S.ErrorCode.NOT_APPLICABLE


def test_hypotheses_persisted_to_v2_table(tmp_app):
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    E.call("hypotheses", "analyze", {"session_id": s.id})
    rows = database.query_all(
        "SELECT * FROM hypotheses_v2 WHERE session_id=?", (s.id,)
    )
    assert rows
    assert "status" in rows[0]
    assert "confidence_basis" in rows[0]
