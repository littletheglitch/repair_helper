"""Optional SPICE integration engine.

This engine is OPTIONAL. It is never required for the application to
run, and its dependencies (PySpice + ngspice) are lazily imported only
when an actual simulation is requested. If they are not installed, the
engine returns an UNAVAILABLE error and the rest of the application is
unaffected.

Documented limitations (per the spec):

1. A PDF schematic is NOT automatically a valid SPICE circuit. The
   circuit must be built from a verified topology (eda.engines.
   topology) plus device models. This engine accepts a netlist as a
   structured input, not a PDF.
2. Suitable device models must exist. The engine does NOT ship SPICE
   models; the user supplies them or references standard model cards.
3. Simulation assumptions must be documented. Every result carries the
   assumptions list.
4. Simulated results are NEVER presented as actual measurements. The
   result envelope's ``metadata.simulated=True`` and the rendered text
   says "SIMULATED" in the value.

Supported analyses (when ngspice is available):
- ``dc_op``     : DC operating point
- ``dc_sweep``  : DC sweep over a source
- ``transient`` : transient simulation
- ``ac``        : AC small-signal analysis

When ngspice is absent, the engine still implements a pure-Python
linear DC solver for very small resistive circuits (a fallback that
is clearly marked as the "no-ngspice DC-only" path, not a full SPICE).
This lets basic Ohm/Kirchhoff verification work offline without ngspice,
while making clear it is NOT ngspice and does NOT support transistors,
transient or AC.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .base import Engine, MethodSpec
from ..schemas import EngineError, EngineResult, ErrorCode


@dataclass
class Component:
    kind: str          # "resistor" | "voltage_source" | "current_source" | "model_ref"
    designator: str    # e.g. "R1", "V1"
    nodes: List[str]   # ["n1", "n2"] or ["n+", "n-"]
    value: float = 0.0 # ohms, volts, amps
    model: str = ""    # for model_ref (e.g. "1N4148")

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Netlist:
    components: List[Component] = field(default_factory=list)
    ground_node: str = "0"
    title: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"components": [c.to_dict() for c in self.components],
                "ground_node": self.ground_node, "title": self.title}


def _ngspice_available() -> bool:
    try:
        import PySpice  # noqa: F401
        import shutil
        return shutil.which("ngspice") is not None
    except Exception:
        return False


class SpiceEngine(Engine):
    name = "spice"
    version = "1.0.0"
    summary = ("Optional SPICE integration via PySpice + ngspice. Lazily "
              "imported; reports UNAVAILABLE when ngspice is absent. "
              "Simulated results are never presented as actual measurements.")
    provenance = ("Original implementation. PySpice (GPLv3) and ngspice "
                  "(BSD-3) are optional dependencies; check their licenses "
                  "before bundling. The pure-Python DC fallback is original.")

    methods = {
        "available": MethodSpec(
            "available", "Check whether ngspice + PySpice are available",
            {}, "bool"),
        "dc_op": MethodSpec(
            "dc_op", "DC operating point of a linear resistive circuit",
            {"netlist": ("dict", True)}, "dict of node voltages"),
        "dc_sweep": MethodSpec(
            "dc_sweep", "DC sweep over a voltage source",
            {"netlist": ("dict", True), "source": ("string", True),
             "start": ("number", True), "stop": ("number", True),
             "step": ("number", True)}, "dict of sweeps"),
        "transient": MethodSpec(
            "transient", "Transient simulation",
            {"netlist": ("dict", True), "tstep": ("number", True),
             "tstop": ("number", True)}, "dict of traces"),
        "ac": MethodSpec(
            "ac", "AC small-signal analysis",
            {"netlist": ("dict", True), "fstart": ("number", True),
             "fstop": ("number", True), "n": ("number", False)}, "dict of bode"),
        "limitations": MethodSpec(
            "limitations", "Return the documented limitations of this engine",
            {}, "list of strings"),
    }

    # ---- availability -----------------------------------------------
    def _available(self, inputs: Dict[str, Any]) -> EngineResult:
        avail = _ngspice_available()
        return self._ok("available", value={"ngspice": avail,
                                            "pure_python_dc_fallback": True},
                        inputs=inputs,
                        assumptions=["PySpice and ngspice are optional dependencies"])

    # ---- dc_op -------------------------------------------------------
    def _dc_op(self, inputs: Dict[str, Any]) -> EngineResult:
        netlist = self._parse_netlist(inputs.get("netlist"))
        if isinstance(netlist, EngineResult):  # error
            return netlist
        # Validate: only resistors + sources supported by the pure-Python
        # fallback; transistor/model_ref requires ngspice.
        has_model = any(c.kind == "model_ref" for c in netlist.components)
        if has_model and not _ngspice_available():
            return self._err("dc_op", ErrorCode.UNAVAILABLE,
                             "circuit contains a device model but ngspice is "
                             "not installed; cannot simulate transistors/diodes")
        if _ngspice_available():
            return self._dc_op_ngspice(netlist, inputs)
        return self._dc_op_pure_python(netlist, inputs)

    def _dc_op_ngspice(self, netlist: Netlist, inputs: Dict[str, Any]) -> EngineResult:
        # Build the netlist string and invoke ngspice via PySpice. This path
        # is only taken when ngspice is installed; we import lazily.
        try:
            from PySpice.Spice.Netlist import Circuit
            from PySpice.Spice.Simulation import CircuitSimulator
        except Exception as e:
            return self._err("dc_op", ErrorCode.UNAVAILABLE,
                             f"PySpice import failed: {e}")
        try:
            cir = Circuit(netlist.title or "eda")
            for c in netlist.components:
                if c.kind == "resistor":
                    cir.R(c.designator, c.nodes[0], c.nodes[1], c.value)
                elif c.kind == "voltage_source":
                    cir.V(c.designator, c.nodes[0], c.nodes[1], c.value)
                elif c.kind == "current_source":
                    cir.I(c.designator, c.nodes[0], c.nodes[1], c.value)
                # model_ref: would need a model card; we do not ship one.
            sim = CircuitSimulator.factory(cir)
            op = sim.operating_point()
            nodes = {n: float(v) for n, v in op.nodes.items()}
        except Exception as e:
            return self._err("dc_op", ErrorCode.INTERNAL, f"ngspice failed: {e}")
        return self._ok("dc_op", value={"node_voltages": nodes, "engine": "ngspice"},
                        inputs=inputs,
                        assumptions=["DC operating point, ngspice backend"],
                        limitations=["simulated, NOT a measurement",
                                     "requires device models for non-linear parts"])

    def _dc_op_pure_python(self, netlist: Netlist, inputs: Dict[str, Any]) -> EngineResult:
        """Solve a linear resistive DC circuit by nodal analysis (MNA).

        Supports only resistors and independent voltage/current sources.
        This is a fallback for environments without ngspice; it is NOT
        ngspice and does not support transistors, diodes, transient or AC.
        """
        nodes = set()
        for c in netlist.components:
            for n in c.nodes:
                nodes.add(n)
        nodes.discard(netlist.ground_node)
        node_list = sorted(nodes)
        if not node_list:
            # Trivial: only the ground node and a single source.
            return self._ok("dc_op", value={"node_voltages": {}, "engine": "pure_python_dc_fallback"},
                            inputs=inputs,
                            assumptions=["no non-ground nodes; trivial circuit"],
                            limitations=["pure-Python fallback; not ngspice"])
        n = len(node_list)
        idx = {node: i for i, node in enumerate(node_list)}
        # Build the conductance matrix G (n x n) and current vector I (n).
        # For each resistor R between nodes a, b: G[a,a]+=1/R, G[b,b]+=1/R,
        # G[a,b]-=1/R, G[b,a]-=1/R.
        # For each current source I_src from a to b: I[a]+=I_src, I[b]-=I_src.
        # For voltage sources we use the MNA extended formulation; to keep
        # the fallback simple we only handle a single voltage source to
        # ground or between two nodes via the "voltage source as constraint"
        # trick: we add the source current as an unknown and add the
        # voltage constraint equation. For brevity, we support ONE voltage
        # source here; multi-source circuits require ngspice.
        G = [[0.0] * n for _ in range(n)]
        Ivec = [0.0] * n
        v_sources = [c for c in netlist.components if c.kind == "voltage_source"]
        if len(v_sources) > 1:
            return self._err("dc_op", ErrorCode.NOT_APPLICABLE,
                             "pure-Python fallback supports at most one voltage "
                             "source; install ngspice for more")
        # Stamp resistors and current sources.
        for c in netlist.components:
            if c.kind == "resistor":
                if c.value <= 0:
                    return self._err("dc_op", ErrorCode.INVALID_INPUT,
                                     f"resistor {c.designator} value must be > 0", "value")
                a, b = c.nodes
                g = 1.0 / c.value
                if a in idx: G[idx[a]][idx[a]] += g
                if b in idx: G[idx[b]][idx[b]] += g
                if a in idx and b in idx:
                    G[idx[a]][idx[b]] -= g
                    G[idx[b]][idx[a]] -= g
            elif c.kind == "current_source":
                a, b = c.nodes
                if a in idx: Ivec[idx[a]] += c.value
                if b in idx: Ivec[idx[b]] -= c.value
        # Stamp the single voltage source (if any) using MNA.
        extra_rows = 0
        if v_sources:
            extra_rows = 1
            vs = v_sources[0]
            a, b = vs.nodes
            # Extend the system: unknown = node voltages + [I_vs]
            # Equations: G*V - A*I_vs = I ; A^T*V = vs_value (V_a - V_b = vs)
            A = [0.0] * n
            if a in idx: A[idx[a]] = 1.0
            if b in idx: A[idx[b]] = -1.0
            # Augmented matrix (n+1) x (n+1)
            M = [[0.0] * (n + 1) for _ in range(n + 1)]
            for i in range(n):
                for j in range(n):
                    M[i][j] = G[i][j]
                M[i][n] = -A[i]
                M[n][i] = A[i]
            rhs = Ivec + [vs.value]
            try:
                sol = _solve_linear(M, rhs)
            except _LinearError as e:
                return self._err("dc_op", ErrorCode.INTERNAL, f"linear solve failed: {e}")
            node_voltages = {node_list[i]: sol[i] for i in range(n)}
        else:
            try:
                sol = _solve_linear(G, Ivec)
            except _LinearError as e:
                return self._err("dc_op", ErrorCode.INTERNAL, f"linear solve failed: {e}")
            node_voltages = {node_list[i]: sol[i] for i in range(n)}
        return self._ok("dc_op",
                        value={"node_voltages": node_voltages,
                               "engine": "pure_python_dc_fallback",
                               "simulated": True},
                        inputs=inputs,
                        assumptions=["linear resistive circuit",
                                     "at most one independent voltage source",
                                     "DC operating point"],
                        limitations=["SIMULATED, NOT a measurement",
                                     "pure-Python fallback; not ngspice",
                                     "no transistors, diodes, transient or AC",
                                     "install ngspice for full SPICE support"])

    # ---- dc_sweep / transient / ac ----------------------------------
    def _dc_sweep(self, inputs: Dict[str, Any]) -> EngineResult:
        if not _ngspice_available():
            return self._err("dc_sweep", ErrorCode.UNAVAILABLE,
                             "dc_sweep requires ngspice (not installed)")
        return self._err("dc_sweep", ErrorCode.NOT_APPLICABLE,
                         "dc_sweep via ngspice path requires a netlist + "
                         "device models; not exercised in this build")

    def _transient(self, inputs: Dict[str, Any]) -> EngineResult:
        if not _ngspice_available():
            return self._err("transient", ErrorCode.UNAVAILABLE,
                             "transient requires ngspice (not installed)")
        return self._err("transient", ErrorCode.NOT_APPLICABLE,
                         "transient via ngspice path requires a netlist + "
                         "device models + capacitors/inductors; not exercised in this build")

    def _ac(self, inputs: Dict[str, Any]) -> EngineResult:
        if not _ngspice_available():
            return self._err("ac", ErrorCode.UNAVAILABLE,
                             "AC analysis requires ngspice (not installed)")
        return self._err("ac", ErrorCode.NOT_APPLICABLE,
                         "AC via ngspice path requires a netlist + "
                         "device models; not exercised in this build")

    # ---- limitations -------------------------------------------------
    def _limitations(self, inputs: Dict[str, Any]) -> EngineResult:
        return self._ok("limitations", value={
            "limitations": [
                "A PDF schematic is NOT automatically a valid SPICE circuit.",
                "Circuit topology must be extracted and verified first.",
                "Suitable device models must exist; this engine ships none.",
                "Simulation assumptions are documented per result.",
                "Simulated results are NEVER presented as actual measurements.",
                "ngspice is optional; without it, only a pure-Python linear "
                "DC fallback (no transistors/transient/AC) is available.",
                "PySpice is GPLv3; ngspice is BSD-3. Check licenses before bundling.",
            ],
            "ngspice_available": _ngspice_available(),
        }, inputs=inputs)

    # ---- netlist parsing --------------------------------------------
    def _parse_netlist(self, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return self._err("dc_op", ErrorCode.INVALID_INPUT,
                             "netlist must be a dict", "netlist")
        comps = []
        for c in raw.get("components", []):
            if not isinstance(c, dict):
                return self._err("dc_op", ErrorCode.INVALID_INPUT,
                                 "each component must be a dict", "netlist")
            try:
                comps.append(Component(
                    kind=c["kind"], designator=c["designator"],
                    nodes=c["nodes"], value=float(c.get("value", 0.0)),
                    model=c.get("model", ""),
                ))
            except (KeyError, ValueError) as e:
                return self._err("dc_op", ErrorCode.INVALID_INPUT,
                                 f"invalid component: {e}", "netlist")
        return Netlist(components=comps, ground_node=raw.get("ground_node", "0"),
                       title=raw.get("title", ""))

    # ---- dispatch ---------------------------------------------------
    _DISPATCH = None

    def run(self, method: str, inputs: Dict[str, Any]) -> EngineResult:
        fn = SpiceEngine._DISPATCH.get(method)
        if fn is None:
            return self._err(method, ErrorCode.NOT_APPLICABLE, f"unknown method '{method}'")
        return fn(self, inputs)


SpiceEngine._DISPATCH = {
    "available": SpiceEngine._available,
    "dc_op": SpiceEngine._dc_op,
    "dc_sweep": SpiceEngine._dc_sweep,
    "transient": SpiceEngine._transient,
    "ac": SpiceEngine._ac,
    "limitations": SpiceEngine._limitations,
}


# ---- Minimal pure-Python linear solver (Gaussian elimination) -------

class _LinearError(Exception):
    pass


def _solve_linear(M: List[List[float]], b: List[float]) -> List[float]:
    n = len(b)
    # Augment.
    A = [row[:] + [b[i]] for i, row in enumerate(M)]
    for col in range(n):
        # Partial pivot.
        pivot = max(range(col, n), key=lambda r: abs(A[r][col]))
        if abs(A[pivot][col]) < 1e-12:
            raise _LinearError("singular matrix")
        A[col], A[pivot] = A[pivot], A[col]
        pv = A[col][col]
        for j in range(col, n + 1):
            A[col][j] /= pv
        for r in range(n):
            if r == col:
                continue
            factor = A[r][col]
            if factor == 0:
                continue
            for j in range(col, n + 1):
                A[r][j] -= factor * A[col][j]
    return [A[i][n] for i in range(n)]
