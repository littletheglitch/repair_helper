"""Tests for the power-sequence engine."""
from eda.engines import base as E
from eda.engines import power_sequence as PS
from eda import schemas as S, sessions, measurements as M, database


def test_default_sequence_returns_nodes():
    r = E.call("power_sequence", "default_sequence", {})
    assert r.ok
    assert isinstance(r.value, list)
    keys = {n["node_key"] for n in r.value}
    assert "vin" in keys and "3valw" in keys and "5valw" in keys
    assert "vcore" in keys and "reset_deassert" in keys


def test_default_sequence_has_dependency_graph():
    r = E.call("power_sequence", "default_sequence", {})
    deps = {n["node_key"]: n.get("depends_on", []) for n in r.value}
    # 3valw depends on vin; vcore depends on 3vs and 5vs.
    assert "vin" in deps["3valw"]
    assert "3vs" in deps["vcore"] and "5vs" in deps["vcore"]


def test_save_and_get_custom_sequence(tmp_app):
    custom = [
        {"node_key": "vin", "node_type": "rail", "display_name": "DC in",
         "state": "S5", "depends_on": [], "nominal_voltage": "20V", "ordinal": 1},
        {"node_key": "3valw", "node_type": "rail", "display_name": "+3VALW",
         "state": "S5", "depends_on": ["vin"], "nominal_voltage": "3.3V", "ordinal": 2},
    ]
    r = E.call("power_sequence", "save_sequence",
               {"name": "board-A", "nodes": custom, "board_model": "A",
                "description": "custom board A sequence"})
    assert r.ok
    g = E.call("power_sequence", "get_sequence", {"name": "board-A"})
    assert g.ok
    assert len(g.value) == 2
    assert g.value[0]["node_key"] == "vin"


def test_get_sequence_falls_back_to_default(tmp_app):
    r = E.call("power_sequence", "get_sequence", {"name": "nonexistent"})
    assert r.ok
    # Returns the default generic sequence.
    assert isinstance(r.value, list) and r.value


def test_analyze_missing_session(tmp_app):
    r = E.call("power_sequence", "analyze", {"session_id": 999999})
    assert not r.ok
    assert r.error.code == S.ErrorCode.NOT_APPLICABLE


def test_analyze_no_measurements(tmp_app):
    s = sessions.create("S", "")
    r = E.call("power_sequence", "analyze", {"session_id": s.id})
    assert r.ok
    # All nodes unknown; apparent state G3.
    assert r.value["apparent_state"] == "G3"
    assert r.value["unknown"]


def test_analyze_vin_present_only(tmp_app):
    s = sessions.create("S", "")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    r = E.call("power_sequence", "analyze", {"session_id": s.id})
    assert r.ok
    assert "vin" in r.value["alive"]
    # 3valw/5valw unknown -> blocking vcore path.
    assert "3valw" in r.value["unknown"]


def test_analyze_detects_sequence_violation(tmp_app):
    """If vcore is alive but 3valw is dead, that's a sequence violation."""
    s = sessions.create("S", "")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    M.add(s.id, M.parse_line("measure +3VALW = 0V"))   # dead
    M.add(s.id, M.parse_line("measure +5VALW = 5V"))   # alive
    M.add(s.id, M.parse_line("measure VCORE = 1.0V"))  # alive
    r = E.call("power_sequence", "analyze", {"session_id": s.id})
    assert r.ok
    # vcore alive but 3vs is unknown -> violation
    assert any("vcore" in v and "3vs" in v for v in r.value["sequence_violations"])


def test_analyze_apparent_state_progresses(tmp_app):
    s = sessions.create("S", "")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    M.add(s.id, M.parse_line("measure +3VALW = 3.3V"))
    M.add(s.id, M.parse_line("measure +5VALW = 5V"))
    r = E.call("power_sequence", "analyze", {"session_id": s.id})
    assert r.ok
    # With always-on rails alive, apparent state is at least S5.
    assert r.value["apparent_state"] in ("S5", "S3", "S0")


def test_analyze_suggests_next_measurement(tmp_app):
    s = sessions.create("S", "")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    r = E.call("power_sequence", "analyze", {"session_id": s.id})
    assert r.ok
    # Next measurement should target an unknown node whose prereq is alive.
    nt = r.value["next_measurement"]
    assert nt is not None
    assert nt["node"]


def test_analyze_does_not_invent_polarity(tmp_app):
    """The engine must not infer signal polarity without documentation;
    nominal voltages are display-only."""
    r = E.call("power_sequence", "default_sequence", {})
    assert r.ok
    for n in r.value:
        if n["node_type"] == "signal":
            # Signals carry no nominal_voltage in the default model.
            assert n.get("nominal_voltage", "") == ""
