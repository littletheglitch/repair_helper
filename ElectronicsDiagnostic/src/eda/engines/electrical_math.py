"""Electrical mathematics engine.

Deterministic, dimensionally-checked electrical calculations. Every
method returns an :class:`EngineResult` envelope with the formula used,
the assumptions made, and the provenance of the method. The LLM
orchestrator may paraphrase the result but must not alter ``value`` or
``value_si``.

Implemented methods:
- ``ohm_voltage``   V = I * R
- ``ohm_current``   I = V / R
- ``ohm_resistance`` R = V / I
- ``power_vi``      P = V * I
- ``power_ir``      P = I^2 * R
- ``power_v2r``     P = V^2 / R
- ``series_resistors`` R_total = sum(R_i)
- ``parallel_resistors`` 1/R_total = sum(1/R_i)
- ``voltage_divider``   V_out = V_in * R2 / (R1 + R2)
- ``current_divider``   I_branch = I_total * R_other / (R1 + R2)
- ``kvl_loop``          sum(V) around a loop must be zero (returns residual)
- ``kcl_node``          sum(I) at a node must be zero (returns residual)

Inputs are :class:`Quantity` objects (or parseable (number, unit) pairs).
The engine never silently substitutes missing values; a missing input
returns a ``missing_input`` error.

Provenance: each method states the physical law it implements. The
implementation is original and uses only SI base arithmetic; no external
numerical library is required. Formulas are returned as human-readable
strings so the LLM can show its work to the technician.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from .base import Engine, MethodSpec
from ..schemas import (
    DimensionalError, EngineError, EngineResult, ErrorCode,
    Quantity, _Dim,
)


def _as_quantity(v: Any, dim_hint: _Dim = None) -> Quantity:
    """Coerce a value to a Quantity. Accepts a Quantity, a (number, unit)
    tuple, or a parseable string. Raises ValueError if not coercible."""
    if isinstance(v, Quantity):
        return v
    if isinstance(v, tuple) and len(v) == 2:
        return Quantity.parse(v[0], v[1])
    if isinstance(v, str):
        return Quantity.parse(v)
    raise ValueError(f"cannot coerce {v!r} to a Quantity")


def _expect_dim(q: Quantity, dim: _Dim, name: str) -> None:
    if q.dimension != dim:
        raise DimensionalError(
            f"input '{name}' has dimension {q.dimension.name()}, expected {dim.name()}"
        )


class ElectricalMathEngine(Engine):
    name = "electrical_math"
    version = "1.0.0"
    summary = ("Electrical mathematics: Ohm's law, power, series/parallel "
              "resistors, voltage/current dividers, KVL/KCL residual checks. "
              "All inputs and outputs are SI-checked Quantity objects.")
    provenance = ("Standard electrical-engineering laws (Ohm 1827, "
                  "Kirchhoff 1845); implementation is original, SI-base "
                  "arithmetic only, no external numerical library.")

    methods = {
        "ohm_voltage": MethodSpec(
            "ohm_voltage", "V = I * R from current and resistance",
            {"I": ("quantity", True), "R": ("quantity", True)}, "Quantity (V)"),
        "ohm_current": MethodSpec(
            "ohm_current", "I = V / R from voltage and resistance",
            {"V": ("quantity", True), "R": ("quantity", True)}, "Quantity (A)"),
        "ohm_resistance": MethodSpec(
            "ohm_resistance", "R = V / I from voltage and current",
            {"V": ("quantity", True), "I": ("quantity", True)}, "Quantity (Ω)"),
        "power_vi": MethodSpec(
            "power_vi", "P = V * I",
            {"V": ("quantity", True), "I": ("quantity", True)}, "Quantity (W)"),
        "power_ir": MethodSpec(
            "power_ir", "P = I^2 * R",
            {"I": ("quantity", True), "R": ("quantity", True)}, "Quantity (W)"),
        "power_v2r": MethodSpec(
            "power_v2r", "P = V^2 / R",
            {"V": ("quantity", True), "R": ("quantity", True)}, "Quantity (W)"),
        "series_resistors": MethodSpec(
            "series_resistors", "R_total = sum(R_i) for resistors in series",
            {"R": ("list", True)}, "Quantity (Ω)"),
        "parallel_resistors": MethodSpec(
            "parallel_resistors", "1/R_total = sum(1/R_i) for resistors in parallel",
            {"R": ("list", True)}, "Quantity (Ω)"),
        "voltage_divider": MethodSpec(
            "voltage_divider", "V_out = V_in * R2 / (R1 + R2)",
            {"V_in": ("quantity", True), "R1": ("quantity", True),
             "R2": ("quantity", True)}, "Quantity (V)"),
        "current_divider": MethodSpec(
            "current_divider", "I_branch = I_total * R_other / (R1 + R2)",
            {"I_total": ("quantity", True), "R1": ("quantity", True),
             "R2": ("quantity", True)}, "Quantity (A)"),
        "kvl_loop": MethodSpec(
            "kvl_loop", "sum of voltages around a closed loop (should be ~0)",
            {"V": ("list", True)}, "Quantity (V) residual"),
        "kcl_node": MethodSpec(
            "kcl_node", "sum of currents at a node (should be ~0)",
            {"I": ("list", True)}, "Quantity (A) residual"),
    }

    # ---- Ohm's law ---------------------------------------------------
    def _ohm_voltage(self, inputs: Dict[str, Any]) -> EngineResult:
        try:
            i = _as_quantity(inputs["I"])
            r = _as_quantity(inputs["R"])
            _expect_dim(i, _Dim.current(), "I")
            _expect_dim(r, _Dim.resistance(), "R")
        except (ValueError, DimensionalError) as e:
            return self._err("ohm_voltage", ErrorCode.INVALID_INPUT, str(e))
        v = i * r
        return self._ok("ohm_voltage", value=v, value_si=v.magnitude, unit="V",
                        inputs={"I": i.render(), "R": r.render()},
                        formulas=["V = I * R"],
                        assumptions=["linear resistor", "constant temperature"],
                        limitations=["does not apply to semiconductor junctions or active regulators"])

    def _ohm_current(self, inputs: Dict[str, Any]) -> EngineResult:
        try:
            v = _as_quantity(inputs["V"])
            r = _as_quantity(inputs["R"])
            _expect_dim(v, _Dim.voltage(), "V")
            _expect_dim(r, _Dim.resistance(), "R")
            if r.is_zero():
                return self._err("ohm_current", ErrorCode.OUT_OF_RANGE,
                                 "R = 0 Ω: division by zero (short circuit)", "R")
        except (ValueError, DimensionalError) as e:
            return self._err("ohm_current", ErrorCode.INVALID_INPUT, str(e))
        i = v / r
        return self._ok("ohm_current", value=i, value_si=i.magnitude, unit="A",
                        inputs={"V": v.render(), "R": r.render()},
                        formulas=["I = V / R"],
                        assumptions=["linear resistor"],
                        limitations=["R = 0 raises division-by-zero (short)"])

    def _ohm_resistance(self, inputs: Dict[str, Any]) -> EngineResult:
        try:
            v = _as_quantity(inputs["V"])
            i = _as_quantity(inputs["I"])
            _expect_dim(v, _Dim.voltage(), "V")
            _expect_dim(i, _Dim.current(), "I")
            if i.is_zero():
                return self._err("ohm_resistance", ErrorCode.OUT_OF_RANGE,
                                 "I = 0 A: division by zero (open circuit)", "I")
        except (ValueError, DimensionalError) as e:
            return self._err("ohm_resistance", ErrorCode.INVALID_INPUT, str(e))
        r = v / i
        return self._ok("ohm_resistance", value=r, value_si=r.magnitude, unit="Ω",
                        inputs={"V": v.render(), "I": i.render()},
                        formulas=["R = V / I"],
                        assumptions=["linear resistor"])

    # ---- Power -------------------------------------------------------
    def _power_vi(self, inputs: Dict[str, Any]) -> EngineResult:
        try:
            v = _as_quantity(inputs["V"]); i = _as_quantity(inputs["I"])
            _expect_dim(v, _Dim.voltage(), "V"); _expect_dim(i, _Dim.current(), "I")
        except (ValueError, DimensionalError) as e:
            return self._err("power_vi", ErrorCode.INVALID_INPUT, str(e))
        p = v * i
        return self._ok("power_vi", value=p, value_si=p.magnitude, unit="W",
                        inputs={"V": v.render(), "I": i.render()},
                        formulas=["P = V * I"],
                        assumptions=["DC or RMS values"])

    def _power_ir(self, inputs: Dict[str, Any]) -> EngineResult:
        try:
            i = _as_quantity(inputs["I"]); r = _as_quantity(inputs["R"])
            _expect_dim(i, _Dim.current(), "I"); _expect_dim(r, _Dim.resistance(), "R")
        except (ValueError, DimensionalError) as e:
            return self._err("power_ir", ErrorCode.INVALID_INPUT, str(e))
        p = (i * i) * r
        return self._ok("power_ir", value=p, value_si=p.magnitude, unit="W",
                        inputs={"I": i.render(), "R": r.render()},
                        formulas=["P = I^2 * R"],
                        assumptions=["linear resistor, RMS current"])

    def _power_v2r(self, inputs: Dict[str, Any]) -> EngineResult:
        try:
            v = _as_quantity(inputs["V"]); r = _as_quantity(inputs["R"])
            _expect_dim(v, _Dim.voltage(), "V"); _expect_dim(r, _Dim.resistance(), "R")
            if r.is_zero():
                return self._err("power_v2r", ErrorCode.OUT_OF_RANGE,
                                 "R = 0 Ω: division by zero", "R")
        except (ValueError, DimensionalError) as e:
            return self._err("power_v2r", ErrorCode.INVALID_INPUT, str(e))
        p = (v * v) / r
        return self._ok("power_v2r", value=p, value_si=p.magnitude, unit="W",
                        inputs={"V": v.render(), "R": r.render()},
                        formulas=["P = V^2 / R"],
                        assumptions=["linear resistor"])

    # ---- Series / parallel ------------------------------------------
    def _series(self, inputs: Dict[str, Any]) -> EngineResult:
        try:
            rs = [_as_quantity(x) for x in inputs["R"]]
            for k, q in enumerate(rs):
                _expect_dim(q, _Dim.resistance(), f"R[{k}]")
            if not rs:
                return self._err("series_resistors", ErrorCode.MISSING_INPUT,
                                 "empty resistor list", "R")
        except (ValueError, DimensionalError) as e:
            return self._err("series_resistors", ErrorCode.INVALID_INPUT, str(e))
        total = rs[0]
        for q in rs[1:]:
            total = total + q
        return self._ok("series_resistors", value=total, value_si=total.magnitude, unit="Ω",
                        inputs={"R": [q.render() for q in rs]},
                        formulas=["R_total = R1 + R2 + ... + Rn"],
                        assumptions=["all resistors carry the same current"])

    def _parallel(self, inputs: Dict[str, Any]) -> EngineResult:
        try:
            rs = [_as_quantity(x) for x in inputs["R"]]
            for k, q in enumerate(rs):
                _expect_dim(q, _Dim.resistance(), f"R[{k}]")
            if not rs:
                return self._err("parallel_resistors", ErrorCode.MISSING_INPUT,
                                 "empty resistor list", "R")
            if any(q.is_zero() for q in rs):
                # A zero-ohm parallel branch shorts the rail.
                return self._ok("parallel_resistors",
                                value=Quantity(0.0, _Dim.resistance(), "Ω"),
                                value_si=0.0, unit="Ω",
                                inputs={"R": [q.render() for q in rs]},
                                formulas=["1/R_total = 1/R1 + ... + 1/Rn"],
                                assumptions=["a zero-ohm branch dominates: R_total = 0 Ω"])
        except (ValueError, DimensionalError) as e:
            return self._err("parallel_resistors", ErrorCode.INVALID_INPUT, str(e))
        inv = sum(1.0 / q.magnitude for q in rs)
        if inv == 0.0:
            return self._err("parallel_resistors", ErrorCode.OUT_OF_RANGE,
                             "all resistors infinite", "R")
        total_mag = 1.0 / inv
        return self._ok("parallel_resistors",
                        value=Quantity(total_mag, _Dim.resistance(), "Ω"),
                        value_si=total_mag, unit="Ω",
                        inputs={"R": [q.render() for q in rs]},
                        formulas=["1/R_total = 1/R1 + ... + 1/Rn"],
                        assumptions=["all resistors share the same voltage"])

    # ---- Dividers ---------------------------------------------------
    def _voltage_divider(self, inputs: Dict[str, Any]) -> EngineResult:
        try:
            v_in = _as_quantity(inputs["V_in"])
            r1 = _as_quantity(inputs["R1"]); r2 = _as_quantity(inputs["R2"])
            _expect_dim(v_in, _Dim.voltage(), "V_in")
            _expect_dim(r1, _Dim.resistance(), "R1")
            _expect_dim(r2, _Dim.resistance(), "R2")
            if (r1 + r2).is_zero():
                return self._err("voltage_divider", ErrorCode.OUT_OF_RANGE,
                                  "R1 + R2 = 0 Ω", "R1")
        except (ValueError, DimensionalError) as e:
            return self._err("voltage_divider", ErrorCode.INVALID_INPUT, str(e))
        ratio = r2.magnitude / (r1.magnitude + r2.magnitude)
        v_out = Quantity(v_in.magnitude * ratio, _Dim.voltage(), "V")
        return self._ok("voltage_divider", value=v_out, value_si=v_out.magnitude, unit="V",
                        inputs={"V_in": v_in.render(), "R1": r1.render(), "R2": r2.render()},
                        formulas=["V_out = V_in * R2 / (R1 + R2)"],
                        assumptions=["no load on V_out (high impedance meter)"])

    def _current_divider(self, inputs: Dict[str, Any]) -> EngineResult:
        try:
            i_total = _as_quantity(inputs["I_total"])
            r1 = _as_quantity(inputs["R1"]); r2 = _as_quantity(inputs["R2"])
            _expect_dim(i_total, _Dim.current(), "I_total")
            _expect_dim(r1, _Dim.resistance(), "R1")
            _expect_dim(r2, _Dim.resistance(), "R2")
            if (r1 + r2).is_zero():
                return self._err("current_divider", ErrorCode.OUT_OF_RANGE,
                                  "R1 + R2 = 0 Ω", "R1")
        except (ValueError, DimensionalError) as e:
            return self._err("current_divider", ErrorCode.INVALID_INPUT, str(e))
        # I through R1 = I_total * R2 / (R1 + R2)
        i_branch = Quantity(i_total.magnitude * r2.magnitude / (r1.magnitude + r2.magnitude),
                            _Dim.current(), "A")
        return self._ok("current_divider", value=i_branch, value_si=i_branch.magnitude, unit="A",
                        inputs={"I_total": i_total.render(), "R1": r1.render(), "R2": r2.render()},
                        formulas=["I_R1 = I_total * R2 / (R1 + R2)"],
                        assumptions=["branch current through R1"])

    # ---- KVL / KCL --------------------------------------------------
    def _kvl(self, inputs: Dict[str, Any]) -> EngineResult:
        try:
            vs = [_as_quantity(x) for x in inputs["V"]]
            for k, q in enumerate(vs):
                _expect_dim(q, _Dim.voltage(), f"V[{k}]")
            if not vs:
                return self._err("kvl_loop", ErrorCode.MISSING_INPUT,
                                 "empty voltage list", "V")
        except (ValueError, DimensionalError) as e:
            return self._err("kvl_loop", ErrorCode.INVALID_INPUT, str(e))
        total = sum(q.magnitude for q in vs)
        residual = Quantity(total, _Dim.voltage(), "V")
        return self._ok("kvl_loop", value=residual, value_si=total, unit="V",
                        inputs={"V": [q.render() for q in vs]},
                        formulas=["sum(V_loop) = 0 (Kirchhoff's voltage law)"],
                        assumptions=["signed voltages, consistent loop direction"],
                        limitations=["a non-zero residual indicates a measurement or topology error"])

    def _kcl(self, inputs: Dict[str, Any]) -> EngineResult:
        try:
            is_ = [_as_quantity(x) for x in inputs["I"]]
            for k, q in enumerate(is_):
                _expect_dim(q, _Dim.current(), f"I[{k}]")
            if not is_:
                return self._err("kcl_node", ErrorCode.MISSING_INPUT,
                                 "empty current list", "I")
        except (ValueError, DimensionalError) as e:
            return self._err("kcl_node", ErrorCode.INVALID_INPUT, str(e))
        total = sum(q.magnitude for q in is_)
        residual = Quantity(total, _Dim.current(), "A")
        return self._ok("kcl_node", value=residual, value_si=total, unit="A",
                        inputs={"I": [q.render() for q in is_]},
                        formulas=["sum(I_node) = 0 (Kirchhoff's current law)"],
                        assumptions=["signed currents, currents entering are positive"],
                        limitations=["a non-zero residual indicates a measurement or topology error"])

    # ---- dispatch ---------------------------------------------------
    _DISPATCH = None  # set below

    def run(self, method: str, inputs: Dict[str, Any]) -> EngineResult:
        fn = ElectricalMathEngine._DISPATCH.get(method)
        if fn is None:
            return self._err(method, ErrorCode.NOT_APPLICABLE, f"unknown method '{method}'")
        return fn(self, inputs)


ElectricalMathEngine._DISPATCH = {
    "ohm_voltage": ElectricalMathEngine._ohm_voltage,
    "ohm_current": ElectricalMathEngine._ohm_current,
    "ohm_resistance": ElectricalMathEngine._ohm_resistance,
    "power_vi": ElectricalMathEngine._power_vi,
    "power_ir": ElectricalMathEngine._power_ir,
    "power_v2r": ElectricalMathEngine._power_v2r,
    "series_resistors": ElectricalMathEngine._series,
    "parallel_resistors": ElectricalMathEngine._parallel,
    "voltage_divider": ElectricalMathEngine._voltage_divider,
    "current_divider": ElectricalMathEngine._current_divider,
    "kvl_loop": ElectricalMathEngine._kvl,
    "kcl_node": ElectricalMathEngine._kcl,
}
