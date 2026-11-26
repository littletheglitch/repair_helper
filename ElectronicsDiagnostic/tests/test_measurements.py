"""Measurement parsing and persistence tests."""
from eda import measurements as M, sessions


def test_parse_basic_volt(tmp_app):
    p = M.parse_line("measure VIN = 19.4V")
    assert p["name"] == "VIN"
    assert p["value"] == 19.4
    assert p["unit"] == "V"


def test_parse_with_point(tmp_app):
    p = M.parse_line("measure VIN = 19.4V @ board input")
    assert p["point"] == "board input"


def test_parse_resistance_ohm(tmp_app):
    p = M.parse_line("measure resistance_to_ground = 12.4ohm")
    assert p["name"] == "resistance_to_ground"
    assert p["value"] == 12.4
    assert p["unit"] == "Ω"


def test_parse_lowercase_unit(tmp_app):
    p = M.parse_line("measure +3VALW = 3.31V")
    assert p["name"] == "+3VALW"
    assert p["value"] == 3.31
    assert p["unit"] == "V"


def test_parse_garbage_returns_none(tmp_app):
    assert M.parse_line("hello world") is None
    assert M.parse_line("measure xyz") is None


def test_persistence_no_overwrite(tmp_app):
    s = sessions.create("S", "")
    a = M.parse_line("measure VIN = 19.4V")
    b = M.parse_line("measure VIN = 18.9V")
    M.add(s.id, a)
    M.add(s.id, b)
    ms = M.list_for(s.id)
    assert len(ms) == 2
    # Latest of name VIN is 18.9.
    latest = M.get(s.id, "VIN")
    assert latest.value == 18.9


def test_identify_missing_context_voltage(tmp_app):
    s = sessions.create("S", "")
    p = M.parse_line("measure VIN = 19.4V")
    M.add(s.id, p)
    gaps = M.identify_missing_context(s.id)
    # adapter state and reference are missing -> at least 2 gaps.
    assert len(gaps) >= 1


def test_all_named_latest(tmp_app):
    s = sessions.create("S", "")
    M.add(s.id, M.parse_line("measure VIN = 19.4V"))
    M.add(s.id, M.parse_line("measure +3VALW = 3.31V"))
    named = M.all_named(s.id)
    assert "VIN" in named and "+3VALW" in named
