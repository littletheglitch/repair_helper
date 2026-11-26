"""Tests for the optional SPICE engine.

These tests run without ngspice installed. They assert the engine
reports UNAVAILABLE honestly and that the pure-Python DC fallback
solves a simple resistive divider correctly (verified by an
independent calculation in the test, never by the engine itself).
"""
import math
import pytest

from eda.engines import base as E
from eda.engines import spice as SP
from eda import schemas as S


def _divider_netlist(v_in: float, r1: float, r2: float) -> dict:
    return {
        "title": "resistive divider",
        "ground_node": "0",
        "components": [
            {"kind": "voltage_source", "designator": "V1",
             "nodes": ["in", "0"], "value": v_in},
            {"kind": "resistor", "designator": "R1",
             "nodes": ["in", "out"], "value": r1},
            {"kind": "resistor", "designator": "R2",
             "nodes": ["out", "0"], "value": r2},
        ],
    }


def test_available_reports_ngspice_status():
    r = E.call("spice", "available", {})
    assert r.ok
    assert "ngspice" in r.value
    assert r.value["pure_python_dc_fallback"] is True


def test_limitations_lists_required_warnings():
    r = E.call("spice", "limitations", {})
    assert r.ok
    lims = r.value["limitations"]
    assert any("PDF schematic" in l for l in lims)
    assert any("NEVER presented as actual" in l for l in lims)
    assert any("extracted and verified" in l for l in lims)
    assert any("device models" in l for l in lims)


def test_dc_op_divider_uses_fallback_and_is_correct():
    # Independent reference: V_out = V_in * R2/(R1+R2)
    v_in, r1, r2 = 12.0, 10.0, 10.0
    expected = v_in * r2 / (r1 + r2)
    r = E.call("spice", "dc_op", {"netlist": _divider_netlist(v_in, r1, r2)})
    assert r.ok
    assert r.value["engine"] in ("pure_python_dc_fallback", "ngspice")
    v_out = r.value["node_voltages"]["out"]
    assert math.isclose(v_out, expected, rel_tol=1e-9)


def test_dc_op_result_marks_simulated_not_measurement():
    r = E.call("spice", "dc_op", {"netlist": _divider_netlist(5, 100, 100)})
    assert r.ok
    assert r.value.get("simulated") is True or r.value.get("engine") == "ngspice"
    assert any("SIMULATED" in l or "not ngspice" in l
               for l in r.limitations) or r.value.get("engine") == "ngspice"


def test_dc_op_invalid_netlist():
    r = E.call("spice", "dc_op", {"netlist": "not a dict"})
    assert not r.ok
    assert r.error.code == S.ErrorCode.INVALID_INPUT


def test_dc_op_missing_netlist():
    r = E.call("spice", "dc_op", {})
    assert not r.ok
    assert r.error.code in (S.ErrorCode.INVALID_INPUT, S.ErrorCode.MISSING_INPUT)


def test_dc_op_zero_resistance_rejected():
    nl = _divider_netlist(5, 0, 100)  # R1 = 0 -> short
    r = E.call("spice", "dc_op", {"netlist": nl})
    assert not r.ok
    assert r.error.code == S.ErrorCode.INVALID_INPUT


def test_dc_op_multiple_voltage_sources_rejected_in_fallback():
    nl = _divider_netlist(5, 10, 10)
    nl["components"].append({"kind": "voltage_source", "designator": "V2",
                              "nodes": ["out", "0"], "value": 3})
    r = E.call("spice", "dc_op", {"netlist": nl})
    # Either rejected by fallback or solved by ngspice if present.
    if not _ngspice_present():
        assert not r.ok
        assert r.error.code in (S.ErrorCode.NOT_APPLICABLE, S.ErrorCode.UNAVAILABLE)


def test_dc_op_with_model_ref_requires_ngspice():
    nl = _divider_netlist(5, 10, 10)
    nl["components"].append({"kind": "model_ref", "designator": "D1",
                              "nodes": ["out", "0"], "model": "1N4148"})
    r = E.call("spice", "dc_op", {"netlist": nl})
    if not _ngspice_present():
        assert not r.ok
        assert r.error.code == S.ErrorCode.UNAVAILABLE


def test_transient_unavailable_without_ngspice():
    r = E.call("spice", "transient",
               {"netlist": _divider_netlist(5, 100, 100),
                "tstep": 1e-6, "tstop": 1e-3})
    if not _ngspice_present():
        assert not r.ok
        assert r.error.code == S.ErrorCode.UNAVAILABLE


def test_ac_unavailable_without_ngspice():
    r = E.call("spice", "ac",
               {"netlist": _divider_netlist(5, 100, 100),
                "fstart": 1, "fstop": 1e6, "n": 10})
    if not _ngspice_present():
        assert not r.ok
        assert r.error.code == S.ErrorCode.UNAVAILABLE


def test_dc_sweep_unavailable_without_ngspice():
    r = E.call("spice", "dc_sweep",
               {"netlist": _divider_netlist(5, 100, 100),
                "source": "V1", "start": 0, "stop": 12, "step": 1})
    if not _ngspice_present():
        assert not r.ok
        assert r.error.code == S.ErrorCode.UNAVAILABLE


def _ngspice_present() -> bool:
    return SP._ngspice_available()
