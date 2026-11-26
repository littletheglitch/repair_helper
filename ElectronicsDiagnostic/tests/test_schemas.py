"""Tests for the common schemas (Quantity, dimensions, identifiers,
EngineResult/EngineError envelopes)."""
import math
import pytest

from eda import schemas as S


# ---- Quantity parsing ------------------------------------------------

def test_quantity_parse_basic():
    q = S.Quantity.parse(19.4, "V")
    assert q.magnitude == 19.4
    assert q.dimension == S._Dim.voltage()


def test_quantity_parse_string_with_unit():
    q = S.Quantity.parse("19.4V")
    assert q.dimension == S._Dim.voltage()
    assert q.magnitude == 19.4


def test_quantity_parse_prefixed():
    q = S.Quantity.parse(3.3, "mV")
    # 3.3 mV = 0.0033 V in SI base
    assert math.isclose(q.magnitude, 0.0033, rel_tol=1e-12)
    assert q.dimension == S._Dim.voltage()


def test_quantity_parse_kohm():
    q = S.Quantity.parse(4.7, "kΩ")
    assert math.isclose(q.magnitude, 4700.0, rel_tol=1e-12)
    assert q.dimension == S._Dim.resistance()


def test_quantity_parse_micro():
    # µ and μ and u all map to 1e-6
    for u in ("µA", "μA", "uA"):
        q = S.Quantity.parse(2.2, u)
        assert math.isclose(q.magnitude, 2.2e-6, rel_tol=1e-12)
        assert q.dimension == S._Dim.current()


def test_quantity_parse_hz():
    q = S.Quantity.parse(100, "MHz")
    assert math.isclose(q.magnitude, 1e8, rel_tol=1e-12)
    assert q.dimension == S._Dim.frequency()


def test_quantity_parse_unknown_unit_raises():
    with pytest.raises(ValueError):
        S.Quantity.parse(1, "furlongs")


def test_quantity_parse_empty_unit_raises():
    with pytest.raises(ValueError):
        S.Quantity.parse(1, "")


def test_quantity_parse_garbage_string_raises():
    with pytest.raises(ValueError):
        S.Quantity.parse("not a number V")


def test_quantity_parse_negative_voltage():
    # negative voltage is physically meaningful across a reverse-biased diode
    q = S.Quantity.parse(-0.7, "V")
    assert q.magnitude == -0.7


def test_quantity_parse_zero():
    q = S.Quantity.parse(0, "V")
    assert q.magnitude == 0.0
    assert q.is_zero()


# ---- Quantity arithmetic and dimensional analysis ---------------------

def test_add_same_dimension():
    a = S.Quantity.parse(5, "V")
    b = S.Quantity.parse(3, "V")
    assert (a + b) == S.Quantity.parse(8, "V")


def test_add_different_dimension_raises():
    a = S.Quantity.parse(5, "V")
    b = S.Quantity.parse(3, "A")
    with pytest.raises(S.DimensionError):
        a + b


def test_subtract_same_dimension():
    a = S.Quantity.parse(5, "V")
    b = S.Quantity.parse(3, "V")
    assert (a - b) == S.Quantity.parse(2, "V")


def test_ohms_law_voltage():
    # V = I * R
    i = S.Quantity.parse(2, "A")
    r = S.Quantity.parse(5, "Ω")
    v = i * r
    assert v.dimension == S._Dim.voltage()
    assert math.isclose(v.magnitude, 10.0, rel_tol=1e-12)


def test_ohms_law_current():
    # I = V / R
    v = S.Quantity.parse(10, "V")
    r = S.Quantity.parse(5, "Ω")
    i = v / r
    assert i.dimension == S._Dim.current()
    assert math.isclose(i.magnitude, 2.0, rel_tol=1e-12)


def test_power_from_vi():
    v = S.Quantity.parse(12, "V")
    i = S.Quantity.parse(0.5, "A")
    p = v * i
    assert p.dimension == S._Dim.power()
    assert math.isclose(p.magnitude, 6.0, rel_tol=1e-12)


def test_dimensional_consistency_power_ir():
    # P = I^2 * R
    i = S.Quantity.parse(2, "A")
    r = S.Quantity.parse(3, "Ω")
    p = (i * i) * r
    assert p.dimension == S._Dim.power()
    assert math.isclose(p.magnitude, 12.0, rel_tol=1e-12)


def test_dimensional_error_adding_voltage_and_power():
    v = S.Quantity.parse(5, "V")
    p = S.Quantity.parse(3, "W")
    with pytest.raises(S.DimensionError):
        v + p


# ---- Rendering -------------------------------------------------------

def test_render_default():
    q = S.Quantity.parse(19.4, "V")
    assert "V" in q.render()


def test_render_with_explicit_unit():
    q = S.Quantity.parse(0.0033, "V")  # 0.0033 V
    # render as mV should give "3.3mV"
    assert "3.3" in q.render("mV") and "mV" in q.render("mV")


def test_render_wrong_dimension_raises():
    q = S.Quantity.parse(5, "V")
    with pytest.raises(S.DimensionError):
        q.render("A")


# ---- Identifiers -----------------------------------------------------

def test_component_ref_valid():
    c = S.ComponentRef("Q12")
    assert str(c) == "Q12"


def test_component_ref_invalid():
    with pytest.raises(ValueError):
        S.ComponentRef("12")
    with pytest.raises(ValueError):
        S.ComponentRef("")


def test_pin_ref_parse():
    p = S.PinRef.parse("U3.5")
    assert p.component == S.ComponentRef("U3")
    assert p.pin == "5"
    assert str(p) == "U3.5"


def test_pin_ref_invalid():
    with pytest.raises(ValueError):
        S.PinRef.parse("U3")  # missing pin


def test_net_ref_valid():
    n = S.NetRef("+3VALW")
    assert str(n) == "+3VALW"


def test_net_ref_rejects_slash():
    with pytest.raises(ValueError):
        S.NetRef("VIN/GND")


# ---- Engine result envelope ------------------------------------------

def test_engine_result_ok():
    r = S.EngineResult(engine="x", version="1", method="m", value=S.Quantity.parse(5, "V"))
    assert r.ok
    d = r.to_dict()
    assert d["engine"] == "x"
    assert d["value"] == "5V"


def test_engine_result_error():
    err = S.EngineError(S.ErrorCode.INVALID_INPUT, "bad", field="x")
    r = S.EngineResult(engine="x", version="1", method="m", error=err)
    assert not r.ok
    assert "invalid_input" in r.render()
    d = r.to_dict()
    assert d["error"]["code"] == "invalid_input"


def test_engine_result_render_includes_method_and_value():
    r = S.EngineResult(engine="x", version="1", method="ohm_voltage",
                       value=S.Quantity.parse(10, "V"),
                       formulas=["V = I * R"], assumptions=["linear resistor"])
    out = r.render()
    assert "10" in out and "ohm_voltage" in out and "V = I * R" in out
