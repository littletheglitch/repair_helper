"""Tests for the electrical mathematics engine.

Reference answers are computed in the test itself by an independent,
trivial method (e.g. plain float arithmetic), never by calling the
engine. This is the engine-validation rule: the engine never generates
its own expected answers.
"""
import math
import pytest

from eda.engines import base as E
from eda.engines import electrical_math as EM
from eda import schemas as S
from eda import validation as V


# Helper: build a Quantity input dict for the engine.
def q(num, unit):
    return S.Quantity.parse(num, unit)


# ---- Ohm's law --------------------------------------------------------

def test_ohm_voltage_basic():
    r = E.call("electrical_math", "ohm_voltage",
               {"I": q(2, "A"), "R": q(5, "Ω")})
    assert r.ok
    assert r.value.magnitude == 10.0  # independent: 2 * 5 = 10
    assert r.value.dimension == S._Dim.voltage()
    assert "V = I * R" in r.formulas


def test_ohm_voltage_with_prefixed_input():
    # 2 mA * 5 kΩ = 10 V  (2e-3 * 5e3 = 10)
    r = E.call("electrical_math", "ohm_voltage",
               {"I": q(2, "mA"), "R": q(5, "kΩ")})
    assert r.ok
    assert math.isclose(r.value.magnitude, 10.0, rel_tol=1e-12)


def test_ohm_current_basic():
    r = E.call("electrical_math", "ohm_current",
               {"V": q(10, "V"), "R": q(5, "Ω")})
    assert r.ok
    assert math.isclose(r.value.magnitude, 2.0, rel_tol=1e-12)
    assert r.value.dimension == S._Dim.current()


def test_ohm_resistance_basic():
    r = E.call("electrical_math", "ohm_resistance",
               {"V": q(10, "V"), "I": q(2, "A")})
    assert r.ok
    assert math.isclose(r.value.magnitude, 5.0, rel_tol=1e-12)
    assert r.value.dimension == S._Dim.resistance()


def test_ohm_current_zero_resistance_is_short():
    r = E.call("electrical_math", "ohm_current",
               {"V": q(5, "V"), "R": q(0, "Ω")})
    assert not r.ok
    assert r.error.code == S.ErrorCode.OUT_OF_RANGE
    assert "division by zero" in r.error.message.lower()


def test_ohm_resistance_zero_current_is_open():
    r = E.call("electrical_math", "ohm_resistance",
               {"V": q(5, "V"), "I": q(0, "A")})
    assert not r.ok
    assert r.error.code == S.ErrorCode.OUT_OF_RANGE


def test_ohm_voltage_dimensional_mismatch():
    r = E.call("electrical_math", "ohm_voltage",
               {"I": q(2, "V"), "R": q(5, "Ω")})  # I should be current
    assert not r.ok
    assert r.error.code == S.ErrorCode.INVALID_INPUT


def test_ohm_voltage_missing_input():
    r = E.call("electrical_math", "ohm_voltage", {"I": q(2, "A")})
    assert not r.ok
    assert r.error.code == S.ErrorCode.MISSING_INPUT


# ---- Power ------------------------------------------------------------

def test_power_vi():
    r = E.call("electrical_math", "power_vi",
               {"V": q(12, "V"), "I": q(0.5, "A")})
    assert r.ok
    assert math.isclose(r.value.magnitude, 6.0, rel_tol=1e-12)  # 12 * 0.5 = 6
    assert r.value.dimension == S._Dim.power()


def test_power_ir():
    r = E.call("electrical_math", "power_ir",
               {"I": q(2, "A"), "R": q(3, "Ω")})
    assert r.ok
    assert math.isclose(r.value.magnitude, 12.0, rel_tol=1e-12)  # 4 * 3 = 12


def test_power_v2r():
    r = E.call("electrical_math", "power_v2r",
               {"V": q(10, "V"), "R": q(5, "Ω")})
    assert r.ok
    assert math.isclose(r.value.magnitude, 20.0, rel_tol=1e-12)  # 100 / 5 = 20


# ---- Series / parallel -----------------------------------------------

def test_series_resistors():
    r = E.call("electrical_math", "series_resistors",
               {"R": [q(10, "Ω"), q(20, "Ω"), q(30, "Ω")]})
    assert r.ok
    assert math.isclose(r.value.magnitude, 60.0, rel_tol=1e-12)


def test_parallel_resistors_two():
    # 10 Ω || 10 Ω = 5 Ω  (independent: 1/(1/10+1/10) = 5)
    r = E.call("electrical_math", "parallel_resistors",
               {"R": [q(10, "Ω"), q(10, "Ω")]})
    assert r.ok
    assert math.isclose(r.value.magnitude, 5.0, rel_tol=1e-12)


def test_parallel_resistors_three():
    # 1/(1/3 + 1/6 + 1/2) = 1/(0.333+0.167+0.5) = 1/1.0 = 1.0
    r = E.call("electrical_math", "parallel_resistors",
               {"R": [q(3, "Ω"), q(6, "Ω"), q(2, "Ω")]})
    assert r.ok
    assert math.isclose(r.value.magnitude, 1.0, rel_tol=1e-12)


def test_parallel_with_zero_ohm_is_zero():
    r = E.call("electrical_math", "parallel_resistors",
               {"R": [q(0, "Ω"), q(100, "Ω")]})
    assert r.ok
    assert r.value.magnitude == 0.0


def test_series_empty_list():
    r = E.call("electrical_math", "series_resistors", {"R": []})
    assert not r.ok
    assert r.error.code == S.ErrorCode.MISSING_INPUT


def test_series_dimensional_mismatch():
    r = E.call("electrical_math", "series_resistors",
               {"R": [q(10, "Ω"), q(5, "V")]})
    assert not r.ok
    assert r.error.code == S.ErrorCode.INVALID_INPUT


# ---- Dividers --------------------------------------------------------

def test_voltage_divider_equal_resistors():
    # V_in * R2/(R1+R2) = 12 * 10/(10+10) = 6
    r = E.call("electrical_math", "voltage_divider",
               {"V_in": q(12, "V"), "R1": q(10, "Ω"), "R2": q(10, "Ω")})
    assert r.ok
    assert math.isclose(r.value.magnitude, 6.0, rel_tol=1e-12)


def test_voltage_divider_asymmetric():
    # 5 * 3/(2+3) = 3
    r = E.call("electrical_math", "voltage_divider",
               {"V_in": q(5, "V"), "R1": q(2, "kΩ"), "R2": q(3, "kΩ")})
    assert r.ok
    assert math.isclose(r.value.magnitude, 3.0, rel_tol=1e-12)


def test_current_divider():
    # I_total * R2/(R1+R2) = 10 * 30/(20+30) = 6 (through R1)
    r = E.call("electrical_math", "current_divider",
               {"I_total": q(10, "A"), "R1": q(20, "Ω"), "R2": q(30, "Ω")})
    assert r.ok
    assert math.isclose(r.value.magnitude, 6.0, rel_tol=1e-12)


# ---- KVL / KCL -------------------------------------------------------

def test_kvl_balanced_loop():
    # +5 +5 -10 = 0
    r = E.call("electrical_math", "kvl_loop",
               {"V": [q(5, "V"), q(5, "V"), q(-10, "V")]})
    assert r.ok
    assert math.isclose(r.value.magnitude, 0.0, abs_tol=1e-12)


def test_kvl_unbalanced_loop():
    # 5 + 5 - 8 = 2 (residual indicates an error)
    r = E.call("electrical_math", "kvl_loop",
               {"V": [q(5, "V"), q(5, "V"), q(-8, "V")]})
    assert r.ok
    assert math.isclose(r.value.magnitude, 2.0, rel_tol=1e-12)
    assert any("residual" in f.lower() for f in r.limitations)


def test_kcl_balanced_node():
    # 3 + 2 - 5 = 0
    r = E.call("electrical_math", "kcl_node",
               {"I": [q(3, "A"), q(2, "A"), q(-5, "A")]})
    assert r.ok
    assert math.isclose(r.value.magnitude, 0.0, abs_tol=1e-12)


def test_kcl_dimensional_mismatch():
    r = E.call("electrical_math", "kcl_node",
               {"I": [q(3, "A"), q(2, "V")]})
    assert not r.ok
    assert r.error.code == S.ErrorCode.INVALID_INPUT


# ---- Engine result envelope shape -----------------------------------

def test_result_includes_formulas_assumptions_provenance():
    r = E.call("electrical_math", "ohm_voltage",
               {"I": q(2, "A"), "R": q(5, "Ω")})
    assert r.ok
    assert r.formulas and "V = I * R" in r.formulas
    assert r.assumptions
    assert r.provenance


def test_result_to_dict_serializable():
    import json
    r = E.call("electrical_math", "ohm_voltage",
               {"I": q(2, "A"), "R": q(5, "Ω")})
    d = r.to_dict()
    json.dumps(d)
    assert d["value"] == "10V"
    assert d["value_si"] == 10.0


# ---- Boundary and extreme inputs ------------------------------------

def test_extreme_large_values():
    # 1e6 V / 1e6 Ω = 1 A — must not overflow
    r = E.call("electrical_math", "ohm_current",
               {"V": q(1e6, "V"), "R": q(1e6, "Ω")})
    assert r.ok
    assert math.isclose(r.value.magnitude, 1.0, rel_tol=1e-9)


def test_extreme_small_values():
    # 1 µV / 1 µΩ = 1 A
    r = E.call("electrical_math", "ohm_current",
               {"V": q(1, "µV"), "R": q(1, "µΩ")})
    assert r.ok
    assert math.isclose(r.value.magnitude, 1.0, rel_tol=1e-6)


def test_negative_voltage_is_accepted():
    r = E.call("electrical_math", "ohm_current",
               {"V": q(-5, "V"), "R": q(5, "Ω")})
    assert r.ok
    assert math.isclose(r.value.magnitude, -1.0, rel_tol=1e-12)


# ---- Machine-readable validation report ------------------------------

def test_validation_report_for_ohm_voltage_passes():
    cases = [
        V.Case("ohm_v_normal", "ohm_voltage",
               {"I": q(2, "A"), "R": q(5, "Ω")},
               expected=S.Quantity.parse(10, "V"), tolerance=1e-12, kind="normal"),
        V.Case("ohm_v_prefixed", "ohm_voltage",
               {"I": q(2, "mA"), "R": q(5, "kΩ")},
               expected=S.Quantity.parse(10, "V"), tolerance=1e-12, kind="normal"),
        V.Case("ohm_v_zero_r", "ohm_current",
               {"V": q(5, "V"), "R": q(0, "Ω")},
               expected=("out_of_range",), kind="invalid"),
        V.Case("ohm_v_missing", "ohm_voltage",
               {"I": q(2, "A")},
               expected=("missing_input",), kind="invalid"),
    ]
    report = V.run_cases("electrical_math", cases)
    assert report.summary == "PASS", V.render_report_text(report)
    assert report.passed == 4
