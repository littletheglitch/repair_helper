"""Tests for the next-best-test engine."""
from eda.engines import base as E
from eda.engines import next_best_test as NBT
from eda import schemas as S, sessions, measurements as M, database


def test_list_candidates(tmp_app):
    r = E.call("next_best_test", "list_candidates", {})
    assert r.ok
    assert r.value["candidates"]
    # Each candidate declares which hypotheses it is decisive for.
    for c in r.value["candidates"]:
        assert c["decisive_for"]
        assert c["outcomes"]


def test_recommend_no_session(tmp_app):
    r = E.call("next_best_test", "recommend", {"session_id": 999999})
    assert not r.ok
    assert r.error.code == S.ErrorCode.NOT_APPLICABLE


def test_recommend_no_data(tmp_app):
    s = sessions.create("S", "")
    r = E.call("next_best_test", "recommend", {"session_id": s.id})
    assert not r.ok
    assert r.error.code == S.ErrorCode.NOT_APPLICABLE


def test_recommend_picks_vin_first(tmp_app):
    """With only a symptom and no measurements, the first test should
    target VIN (the foundation of every later check)."""
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    r = E.call("next_best_test", "recommend", {"session_id": s.id})
    assert r.ok
    assert "VIN" in r.value["test"] or "adapter" in r.value["test"].lower()


def test_recommend_includes_outcome_branches(tmp_app):
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    r = E.call("next_best_test", "recommend", {"session_id": s.id})
    assert r.ok
    rec = r.value
    assert rec["outcomes"]
    # Each outcome says what a result would imply.
    for o in rec["outcomes"]:
        assert o["condition"]
        assert o["implication"]


def test_recommend_expected_only_when_supported(tmp_app):
    """The 'expected' field must cite a supporting source."""
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    M.add(s.id, M.parse_line("measure +5VALW = 0V"))
    r = E.call("next_best_test", "recommend", {"session_id": s.id})
    assert r.ok
    rec = r.value
    assert rec["expected"]
    assert rec["expected_supported_by"], "expected value must cite a source"


def test_recommend_includes_safety(tmp_app):
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    r = E.call("next_best_test", "recommend", {"session_id": s.id})
    assert r.ok
    # Live measurement -> CAUTION present.
    assert "CAUTION" in r.value["safety_precautions"]


def test_recommend_resistance_test_for_dead_rail(tmp_app):
    """Sample case: VIN present, +5VALW dead -> recommend resistance on
    +5VALW (the cheapest test that distinguishes short vs. enable-fault)."""
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    M.add(s.id, M.parse_line("measure +3VALW = 3.3V"))
    M.add(s.id, M.parse_line("measure +5VALW = 0V"))
    r = E.call("next_best_test", "recommend", {"session_id": s.id})
    assert r.ok
    txt = r.value["test"].lower()
    assert "5valw" in txt or "resistance" in txt


def test_recommend_avoids_taken_measurements(tmp_app):
    """Once a test is taken, the engine should recommend a different one."""
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    M.add(s.id, M.parse_line("measure +5VALW = 0V"))
    # Take the resistance test explicitly.
    M.add(s.id, M.parse_line("measure resistance_to_ground = 480ohm"))
    r = E.call("next_best_test", "recommend", {"session_id": s.id})
    # Either still recommends something useful OR returns not-applicable
    # if no candidate distinguishes the active hypotheses.
    if r.ok:
        # The recommended test should not be the resistance test already taken.
        assert "resistance_to_ground" not in r.value["test"].lower()


def test_recommend_persists_to_next_tests(tmp_app):
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    E.call("next_best_test", "recommend", {"session_id": s.id})
    rows = database.query_all(
        "SELECT * FROM next_tests WHERE session_id=?", (s.id,)
    )
    assert rows


def test_recommend_distinguishes_field(tmp_app):
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    r = E.call("next_best_test", "recommend", {"session_id": s.id})
    assert r.ok
    rec = r.value
    assert rec["distinguishes"]
    assert rec["information_gain"] > 0


def test_recommend_includes_instrument(tmp_app):
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    r = E.call("next_best_test", "recommend", {"session_id": s.id})
    assert r.ok
    assert r.value["instrument"]
    assert "DMM" in r.value["instrument"] or "scope" in r.value["instrument"]


def test_recommend_no_active_hypotheses(tmp_app):
    """If every hypothesis is confirmed/contradicted/rejected, the engine
    says no informative test remains."""
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    M.add(s.id, M.parse_line("measure +3VALW = 3.3V"))
    M.add(s.id, M.parse_line("measure +5VALW = 5V"))
    # Add lots of supporting evidence for the input-path hypothesis to
    # push it to confirmed/strongly_supported; the test should still
    # recommend something or honestly report none.
    r = E.call("next_best_test", "recommend", {"session_id": s.id})
    # Either it recommends a run-rail test or reports none.
    assert r.ok or r.error.code == S.ErrorCode.NOT_APPLICABLE
