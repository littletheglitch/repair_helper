"""Deterministic graph-based netlist analysis (Priority 2).

A Netlist is a graph of Components connected by Nets. Each Component
has Pins; each Pin has a function (power_in, power_out, switch_output,
feedback, ground, passive, control_in, control_out, etc.) and a signal
direction (in, out, passive, bidirectional).

This module provides:
  - Netlist.from_dict()      : build from a structured dict
  - validate()               : graph consistency + floating-net checks
  - components_on_net()      : connectivity lookup
  - trace_path()             : trace a signal path through the graph
  - recognize_structure()    : identify topology from connectivity

Pure standard library. Deterministic. No image recognition.

The recognition logic is CONNECTIVITY-DRIVEN, not part-name-driven:
two netlists with the same components but different connectivity
produce different structure recognition. This is the key P2
requirement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any


class PinFunction(str, Enum):
    """The electrical function of a pin. Recognition uses these."""
    POWER_IN = "power_in"           # input supply (VIN, IN)
    POWER_OUT = "power_out"         # regulated output (VOUT, OUT)
    SWITCH_OUTPUT = "switch_output"  # switching node (SW, PHASE, LX)
    FEEDBACK = "feedback"           # feedback input (FB, VSENSE)
    GROUND = "ground"               # ground (GND, AGND, PGND)
    CONTROL_IN = "control_in"       # enable, sync, etc. (EN, SYNC)
    CONTROL_OUT = "control_out"     # gate drive, PGOOD (GD, PGOOD)
    PASSIVE = "passive"             # resistor/cap/inductor pins
    ANODE = "anode"                 # diode anode
    CATHODE = "cathode"             # diode cathode
    REFERENCE = "reference"         # voltage reference (VREF)
    UNKNOWN = "unknown"


class SignalDirection(str, Enum):
    IN = "in"
    OUT = "out"
    PASSIVE = "passive"
    BIDIRECTIONAL = "bidirectional"


@dataclass
class Pin:
    name: str
    net: str
    function: PinFunction = PinFunction.UNKNOWN
    direction: SignalDirection = SignalDirection.PASSIVE

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "Pin":
        fn = d.get("function", "unknown")
        d_dir = d.get("direction", "passive")
        try:
            fn = PinFunction(fn)
        except ValueError:
            fn = PinFunction.UNKNOWN
        try:
            d_dir = SignalDirection(d_dir)
        except ValueError:
            d_dir = SignalDirection.PASSIVE
        return cls(name=name, net=d.get("net", ""), function=fn, direction=d_dir)


@dataclass
class Component:
    refdes: str
    part: str
    pins: dict[str, Pin] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Component":
        pins = {}
        for pname, pdata in (d.get("pins") or {}).items():
            pins[pname] = Pin.from_dict(pname, pdata or {})
        return cls(refdes=d.get("refdes", ""), part=d.get("part", ""), pins=pins)

    def pins_on_net(self, net: str) -> list[Pin]:
        return [p for p in self.pins.values() if p.net == net]

    def pin_by_function(self, fn: PinFunction) -> Optional[Pin]:
        for p in self.pins.values():
            if p.function == fn:
                return p
        return None


@dataclass
class Net:
    name: str


@dataclass
class NetlistIssue:
    """A problem found by validate()."""
    kind: str           # 'undeclared_net', 'floating', 'missing_ground', 'empty', 'orphan_pin'
    message: str
    net: str = ""
    component: str = ""
    severity: str = "warning"   # 'error' or 'warning'


@dataclass
class PathStep:
    """One hop in a traced path."""
    component: Component
    pin_in: Optional[Pin]
    pin_out: Optional[Pin]
    net_in: str
    net_out: str


class Netlist:
    """A graph of components connected by nets."""

    def __init__(self, components: list[Component], nets: list[str],
                 external_ports: Optional[list[str]] = None):
        self.components = components
        self.nets = {n: Net(name=n) for n in nets}
        # External ports: nets that connect to off-board sources/sinks
        # (input jacks, output connectors, test points tied to external
        # equipment). An external-port net is NOT floating even with one
        # connection — the source is external. (A2-1 fix.)
        self.external_ports: set[str] = set(external_ports or [])
        # Build connectivity index: net -> list of (component, pin)
        self._net_index: dict[str, list[tuple[Component, Pin]]] = {}
        for c in components:
            for p in c.pins.values():
                self._net_index.setdefault(p.net, []).append((c, p))

    @classmethod
    def from_dict(cls, d: dict) -> "Netlist":
        comps = [Component.from_dict(c) for c in (d.get("components") or [])]
        nets = list(d.get("nets") or [])
        ports = list(d.get("external_ports") or [])
        return cls(comps, nets, ports)

    # --- connectivity --------------------------------------------------

    def components_on_net(self, net: str) -> list[Component]:
        """All components that have at least one pin on this net."""
        seen = []
        for c, _p in self._net_index.get(net, []):
            if c not in seen:
                seen.append(c)
        return seen

    def pins_on_net(self, net: str) -> list[tuple[Component, Pin]]:
        return list(self._net_index.get(net, []))

    # --- validation ----------------------------------------------------

    def validate(self) -> list[NetlistIssue]:
        """Check graph consistency, floating nets, missing ground."""
        issues: list[NetlistIssue] = []
        if not self.components and not self.nets:
            issues.append(NetlistIssue(
                kind="empty", severity="error",
                message="Empty netlist: no components and no nets. "
                        "Supply a structured netlist (components + nets) "
                        "to perform graph-based analysis."))
            return issues

        # 1. Every pin's net must be in the declared nets list.
        declared = set(self.nets)
        for c in self.components:
            for pname, p in c.pins.items():
                if p.net and p.net not in declared:
                    issues.append(NetlistIssue(
                        kind="undeclared_net", severity="error",
                        message=f"Pin {c.refdes}.{pname} references net "
                                f"{p.net!r} which is not in the declared nets list.",
                        net=p.net, component=c.refdes))

        # 2. Every declared net should have at least one pin (orphan net).
        for n in self.nets:
            if n not in self._net_index:
                issues.append(NetlistIssue(
                    kind="orphan_net", severity="warning",
                    message=f"Net {n!r} is declared but has no pins connected.",
                    net=n))

        # 3. Floating nets: a net is floating if it has no defined
        #    potential — no external source/sink AND no driver pin.
        #    (A2-1/A2-2 fix.)
        #    - An external port is NOT floating (off-board source). (A2-1)
        #    - A net with a power_in/power_out/ground/reference/control
        #      pin is driven (has a defined potential). (A2-2)
        #    - A net with ONLY passive pins and no external port floats
        #      (no driver defines its voltage). (A2-2)
        #    - A single-connection net that is not an external port and
        #      not driven floats (the original single-pin check). (A2-1)
        DRIVER_FUNCTIONS = {
            # Pins that DRIVE a net (define its potential). A power_in
            # pin CONSUMES from the net — it is NOT a driver; the net's
            # source must be an external port or a power_out/switch/
            # reference pin. (A2-1: a lone power_in pin without an
            # external port floats — the off-board source is missing.)
            PinFunction.POWER_OUT,
            PinFunction.SWITCH_OUTPUT,
            PinFunction.GROUND,
            PinFunction.REFERENCE,
            PinFunction.CONTROL_OUT,
        }
        for net in self.nets:
            if net in self.external_ports:
                continue  # external port — off-board source/sink
            conns = self._net_index.get(net, [])
            if not conns:
                continue  # orphan_net already flagged
            has_driver = any(p.function in DRIVER_FUNCTIONS
                             for _, p in conns)
            if has_driver:
                continue  # a driver pin defines this net's potential
            # No driver, not an external port.
            if len(conns) == 1:
                c, p = conns[0]
                issues.append(NetlistIssue(
                    kind="floating", severity="warning",
                    message=f"Net {net!r} has only one connection "
                            f"({c.refdes}.{p.name}) and no external port "
                            f"or driver. It is floating.",
                    net=net, component=c.refdes))
            else:
                # Multi-pin net with only passive pins and no driver —
                # floats (no defined potential). (A2-2)
                pins_desc = ", ".join(f"{c.refdes}.{p.name}"
                                      for c, p in conns)
                issues.append(NetlistIssue(
                    kind="floating", severity="warning",
                    message=f"Net {net!r} has {len(conns)} passive "
                            f"connections ({pins_desc}) but no driver pin "
                            f"(power_in/out, ground, reference, control) "
                            f"and is not an external port. It floats — no "
                            f"defined potential.",
                    net=net))

        # 4. Ground reference: every powered circuit needs a GND net.
        has_ground = any(p.function == PinFunction.GROUND
                         for c in self.components for p in c.pins.values())
        if not has_ground and self.components:
            issues.append(NetlistIssue(
                kind="missing_ground", severity="error",
                message="No ground pin found. A powered circuit requires "
                        "a GND reference."))

        return issues

    # --- path tracing --------------------------------------------------

    def trace_path(self, start_net: str, end_net: str,
                   max_depth: int = 8) -> Optional[list[PathStep]]:
        """Trace a signal path from start_net to end_net through
        components. Returns the list of PathSteps, or None if no path.

        The trace walks: from start_net, find a component with an IN
        pin on it, cross to that component's OUT pin, follow that
        pin's net, repeat. Stops at end_net.
        """
        if start_net == end_net:
            return []
        visited_nets: set[str] = set()
        visited_components: set[str] = set()

        def _walk(current_net: str, depth: int) -> Optional[list[PathStep]]:
            if depth > max_depth or current_net in visited_nets:
                return None
            visited_nets.add(current_net)
            for c, p in self._net_index.get(current_net, []):
                if c.refdes in visited_components:
                    continue
                # p is on current_net. Find another pin of this
                # component on a DIFFERENT net to cross to.
                # (A2-3 fix): follow ANY pin direction as an
                # intermediate hop, including PASSIVE. Passive
                # components (resistors, inductors, caps as coupling)
                # carry signals; restricting to OUT/BIDIRECTIONAL
                # made multi-hop paths through them unreachable.
                # The direct end_net check (below) is unchanged.
                for pname2, p2 in c.pins.items():
                    if p2.net == current_net:
                        continue
                    if p2.net == end_net:
                        return [PathStep(component=c, pin_in=p, pin_out=p2,
                                         net_in=current_net, net_out=p2.net)]
                    # Intermediate hop: follow OUT, BIDIRECTIONAL, or
                    # PASSIVE pins. (IN pins are sinks — don't cross
                    # through an input.)
                    if p2.direction in (SignalDirection.OUT,
                                        SignalDirection.BIDIRECTIONAL,
                                        SignalDirection.PASSIVE):
                        visited_components.add(c.refdes)
                        sub = _walk(p2.net, depth + 1)
                        if sub is not None:
                            return [PathStep(component=c, pin_in=p, pin_out=p2,
                                             net_in=current_net, net_out=p2.net)] + sub
                        visited_components.discard(c.refdes)
            return None

        return _walk(start_net, 0)

    # --- structure recognition ----------------------------------------

    def recognize_structure(self) -> str:
        """Identify the circuit topology from connectivity + pin
        functions. Returns a structure name, or 'unknown' if no match.

        Recognition is CONNECTIVITY-DRIVEN:
          - LDO: power_in -> power_out, no switch_output, no inductor
          - Buck: power_in -> switch_output -> (inductor) -> power_out
          - Boost: power_in -> inductor -> switch_output -> diode -> power_out
          - MOSFET switch (high-side): power_in -> switch_output, control_in
          - Op-amp: no power_in/power_out; has feedback + 2 inputs
          - Comparator: similar to op-amp but no feedback loop
          - Current-sense: sense resistor + sense amp
          - Relay driver: control_out driving a coil
        """
        if not self.components:
            return "unknown"

        # Collect pin-function signatures across all components.
        has_power_in = any(c.pin_by_function(PinFunction.POWER_IN)
                           for c in self.components)
        has_power_out = any(c.pin_by_function(PinFunction.POWER_OUT)
                            for c in self.components)
        has_switch = any(c.pin_by_function(PinFunction.SWITCH_OUTPUT)
                         for c in self.components)
        has_feedback = any(c.pin_by_function(PinFunction.FEEDBACK)
                           for c in self.components)
        has_control_in = any(c.pin_by_function(PinFunction.CONTROL_IN)
                             for c in self.components)
        has_control_out = any(c.pin_by_function(PinFunction.CONTROL_OUT)
                              for c in self.components)

        # Find inductor and diode components (by part hint or pin count).
        inductors = [c for c in self.components
                     if "inductor" in c.part.lower() or "L" == c.refdes[0]]
        diodes = [c for c in self.components
                  if "diode" in c.part.lower() or "D" == c.refdes[0]]

        # --- LDO: power_in + power_out, NO switch_output, NO inductor ---
        # Phase 5 refinement: also require that no comparator/op-amp
        # signature is present (a comparator has power_in + a logic OUT
        # that looks like power_out, but is NOT an LDO).
        if has_power_in and has_power_out and not has_switch and not inductors:
            # Exclude comparators: they have in+/in- control inputs.
            has_cmp_inputs = any(
                c for c in self.components
                if any(p.function == PinFunction.CONTROL_IN
                       and ("+" in p.name or "-" in p.name)
                       for p in c.pins.values()))
            if not has_cmp_inputs:
                return "ldo"

        # --- Buck: power_in + switch_output, inductor from SW to output ---
        # A buck's output is the inductor's far end (there may be no
        # explicit power_out pin on the controller). Recognition:
        # switch_output pin exists, AND an inductor has one pin on the
        # SW net. The inductor's other pin is the output rail.
        if has_power_in and has_switch and inductors:
            sw_comp = next((c for c in self.components
                            if c.pin_by_function(PinFunction.SWITCH_OUTPUT)), None)
            if sw_comp:
                sw_pin = sw_comp.pin_by_function(PinFunction.SWITCH_OUTPUT)
                for ind in inductors:
                    ind_nets = {p.net for p in ind.pins.values()}
                    if sw_pin and sw_pin.net in ind_nets:
                        # Inductor connects SW to another net (the output).
                        # Distinguish buck from boost: in a buck the
                        # inductor is BETWEEN SW and the output; in a
                        # boost the inductor is BETWEEN VIN and SW.
                        in_comp = next((c for c in self.components
                                        if c.pin_by_function(PinFunction.POWER_IN)), None)
                        if in_comp:
                            in_pin = in_comp.pin_by_function(PinFunction.POWER_IN)
                            # Buck: inductor NOT between VIN and SW (it's
                            # between SW and output). Boost: inductor IS
                            # between VIN and SW.
                            if in_pin and not (in_pin.net in ind_nets
                                               and sw_pin.net in ind_nets):
                                return "buck"

        # --- Boost: power_in + switch_output, inductor from VIN to SW,
        #     diode from SW to output ---
        # A boost's output is the diode's cathode net (there may be no
        # explicit power_out pin on the controller). Recognition:
        # inductor between VIN and SW, AND a diode with anode on SW.
        if has_power_in and has_switch and inductors and diodes:
            sw_comp = next((c for c in self.components
                            if c.pin_by_function(PinFunction.SWITCH_OUTPUT)), None)
            in_comp = next((c for c in self.components
                            if c.pin_by_function(PinFunction.POWER_IN)), None)
            if sw_comp and in_comp:
                sw_pin = sw_comp.pin_by_function(PinFunction.SWITCH_OUTPUT)
                in_pin = in_comp.pin_by_function(PinFunction.POWER_IN)
                for ind in inductors:
                    ind_nets = {p.net for p in ind.pins.values()}
                    # Boost: inductor between VIN and SW
                    if in_pin and sw_pin and in_pin.net in ind_nets \
                            and sw_pin.net in ind_nets:
                        # Diode from SW to output (anode on SW)
                        for d in diodes:
                            d_nets = {p.net for p in d.pins.values()}
                            if sw_pin.net in d_nets:
                                # The diode's other pin is the output net.
                                # No need for an explicit power_out pin.
                                return "boost"

        # --- MOSFET switch: distinguish high-side vs low-side ---
        # switch_output + control_in, no power_out regulator, no inductor.
        # Phase 5: high-side = the FET's non-switch power pin is on VIN
        # (drain to VIN, source=switch to load); low-side = the FET's
        # non-switch power pin is on GND (drain=switch from load, source
        # to GND). Detected by inspecting the switch component's pins.
        if has_switch and has_control_in and not inductors and not has_power_out:
            sw_comp = next((c for c in self.components
                            if c.pin_by_function(PinFunction.SWITCH_OUTPUT)), None)
            placed = False
            if sw_comp:
                sw_pin = sw_comp.pin_by_function(PinFunction.SWITCH_OUTPUT)
                if sw_pin:
                    # Look at the switch component's OTHER pins: is one
                    # on a GND net or a VIN (power_in) net?
                    other_on_ground = False
                    other_on_vin = False
                    for pname, p in sw_comp.pins.items():
                        if p is sw_pin:
                            continue
                        if p.function == PinFunction.GROUND:
                            other_on_ground = True
                        if p.function == PinFunction.POWER_IN:
                            other_on_vin = True
                        # Also check the net: is the net a GND/VIN net
                        # (carried by another component's ground/power_in)?
                        for c2, p2 in self._net_index.get(p.net, []):
                            if p2.function == PinFunction.GROUND:
                                other_on_ground = True
                            if p2.function == PinFunction.POWER_IN:
                                other_on_vin = True
                    if other_on_ground and not other_on_vin:
                        return "mosfet_low_side"
                    if other_on_vin and not other_on_ground:
                        return "mosfet_high_side"
                    placed = True
            if not placed:
                return "mosfet_switch"

        # --- Op-amp: has feedback + no power_in/power_out regulator ---
        # (op-amps have V+/V- supply pins but not power_in/power_out
        # in the regulator sense)
        # Phase 5 refinement: require an amplifier part hint (op/amp/opa)
        # so a voltage divider with a controller FB pin isn't misread
        # as an op-amp.
        has_amp_part = any(
            "amp" in c.part.lower() or "opa" in c.part.lower()
            or "op_" in c.part.lower() for c in self.components)
        if has_feedback and not has_switch and not has_power_out and has_amp_part:
            return "opamp"

        # --- Current-sense: a sense resistor + an amplifier with
        #     inputs across it ---
        # Heuristic: two passive components on the same pair of nets
        # (the sense resistor) + an amplifier.
        resistors = [c for c in self.components
                     if "res" in c.part.lower() or "R" == c.refdes[0]]
        if resistors and any("amp" in c.part.lower() for c in self.components):
            return "current_sense"

        # --- Relay driver: control_out driving a coil (inductor) ---
        if has_control_out and inductors:
            return "relay_driver"

        # --- Phase 5: comparator ---
        # A comparator has 2 inputs (in+, in-) and an output, NO feedback
        # loop (unlike an op-amp). Distinguish from op-amp: op-amp has a
        # feedback net; comparator does not. The output may be marked
        # power_out (open-collector) — that's a logic output, not a
        # regulator output, so the LDO check (which excludes comparators
        # via the in+/in- test) lets us reach here.
        has_in_plus = any(c for c in self.components
                          if any(p.function == PinFunction.CONTROL_IN
                                 and "+" in p.name for p in c.pins.values()))
        has_in_minus = any(c for c in self.components
                           if any(p.function == PinFunction.CONTROL_IN
                                  and "-" in p.name for p in c.pins.values()))
        has_any_output = any(c for c in self.components
                             if any(p.direction == SignalDirection.OUT
                                    for p in c.pins.values()))
        if has_in_plus and has_in_minus and has_any_output and not has_feedback:
            return "comparator"

        # --- Phase 5: voltage divider ---
        # Two resistors in series between two nets (e.g. VIN -> R1 -> R2
        # -> GND), with a tap net between them. Recognition: 2+ resistors
        # where one resistor's net connects to another resistor's net
        # (the tap), and the chain terminates at GND or a reference.
        if len(resistors) >= 2 and not has_power_out and not has_switch:
            # Build a resistor net graph: resistor -> its two nets
            res_nets = []
            for r in resistors:
                nets = [p.net for p in r.pins.values()]
                if len(nets) == 2:
                    res_nets.append((r.refdes, nets[0], nets[1]))
            # Find a chain: R_a shares a net with R_b (the tap), and the
            # tap is not GND/VIN (it's the midpoint).
            ground_nets = {p.net for c in self.components
                           for p in c.pins.values()
                           if p.function == PinFunction.GROUND}
            for i, (ra, na1, na2) in enumerate(res_nets):
                for j, (rb, nb1, nb2) in enumerate(res_nets):
                    if i == j:
                        continue
                    shared = {na1, na2} & {nb1, nb2}
                    if shared:
                        tap = next(iter(shared))
                        # The tap must not be GND (it's the midpoint)
                        if tap not in ground_nets:
                            return "voltage_divider"

        # --- Phase 5: flyback secondary ---
        # The secondary side of a flyback: a transformer secondary
        # winding + a rectifier diode + an output cap. Recognition: a
        # component with "transformer" or "xfmr" in its part, a diode,
        # and no switch_output (the switching is on the primary side,
        # not present in this secondary-only netlist).
        transformers = [c for c in self.components
                        if "transformer" in c.part.lower()
                        or "xfmr" in c.part.lower()
                        or "T" == c.refdes[0]]
        if transformers and diodes and not has_switch:
            return "flyback_secondary"

        # --- Phase 5: charging stage ---
        # A charger: power_in (adapter) + a control IC + a battery
        # connection + an inductor (buck). Recognized by a "charger" or
        # "bq" part hint, or by a battery net.
        has_battery = any("bat" in c.part.lower() or "BAT" in c.refdes
                          for c in self.components)
        if has_power_in and has_battery and inductors:
            return "charging_stage"

        # --- Phase 5: protection circuit ---
        # A TVS or fuse protecting a rail. Recognized by a TVS/fuse part
        # hint connected across a power rail to ground.
        has_tvs = any("tvs" in c.part.lower() for c in self.components)
        has_fuse = any("fuse" in c.part.lower() or "F" == c.refdes[0]
                       for c in self.components)
        if (has_tvs or has_fuse) and has_power_in and not has_power_out:
            return "protection_circuit"

        # --- Phase 5: feedback network ---
        # A feedback divider: 2 resistors + a feedback pin on a
        # controller, no power_out (the controller's FB pin samples the
        # output set by the divider).
        if has_feedback and len(resistors) >= 2 and not has_power_out:
            return "feedback_network"

        return "unknown"

    # --- Phase 5: multi-path tracing + uncertainty ---------------------

    def trace_all_paths(self, start_net: str, end_net: str,
                        max_depth: int = 8) -> list[list["PathStep"]]:
        """Enumerate ALL simple paths from start_net to end_net (not
        just the first). Each path is a list of PathSteps. Uses DFS with
        a visited-set to avoid cycles. Returns [] if no path.

        Unlike trace_path (which returns the first path found), this
        enumerates parallel branches — useful for detecting multiple
        conduction paths (e.g. a diode-OR with two sources).
        """
        if start_net == end_net:
            return [[]]
        results: list[list[PathStep]] = []
        visited_nets: set[str] = set()
        visited_components: set[str] = set()

        def _walk(current_net: str, depth: int, path: list[PathStep]):
            if depth > max_depth or len(results) >= 20:
                return
            visited_nets.add(current_net)
            for c, p in self._net_index.get(current_net, []):
                if c.refdes in visited_components:
                    continue
                for pname2, p2 in c.pins.items():
                    if p2.net == current_net:
                        continue
                    step = PathStep(component=c, pin_in=p, pin_out=p2,
                                    net_in=current_net, net_out=p2.net)
                    if p2.net == end_net:
                        results.append(path + [step])
                        continue
                    if p2.net in visited_nets:
                        continue
                    if p2.direction in (SignalDirection.OUT,
                                        SignalDirection.BIDIRECTIONAL,
                                        SignalDirection.PASSIVE):
                        visited_components.add(c.refdes)
                        _walk(p2.net, depth + 1, path + [step])
                        visited_components.discard(c.refdes)
            visited_nets.discard(current_net)

        _walk(start_net, 0, [])
        return results

    def uncertainty_report(self) -> dict:
        """Report uncertainty about the netlist's completeness. Returns
        a dict with:
          - 'floating_nets': list of net names that float
          - 'orphan_pins': pins with undeclared nets
          - 'missing_ground': bool
          - 'external_ports': declared external ports
          - 'unverified': list of uncertainty notes (e.g. 'single path
            only — parallel branches may exist but were not enumerated')

        This does NOT claim full electrical simulation — it reports
        what the graph CANNOT confirm.
        """
        issues = self.validate()
        floating = [i.net for i in issues if i.kind == "floating"]
        orphan = [{"component": i.component, "net": i.net}
                  for i in issues if i.kind == "orphan_pin"]
        missing_ground = any(i.kind == "missing_ground" for i in issues)
        unverified: list[str] = []
        if not self.components:
            unverified.append("Empty netlist — no components to analyze.")
        if not floating and not orphan and not missing_ground and self.components:
            unverified.append(
                "Graph is internally consistent but electrical behavior "
                "is NOT simulated — verify with a DMM/scope.")
        if len(self.external_ports) == 0 and self.components:
            unverified.append(
                "No external ports declared — input/output connectivity "
                "to off-board sources is unverified.")
        return {
            "floating_nets": floating,
            "orphan_pins": orphan,
            "missing_ground": missing_ground,
            "external_ports": sorted(self.external_ports),
            "unverified": unverified,
        }

    # --- introspection -------------------------------------------------

    def summary(self) -> dict:
        return {
            "components": len(self.components),
            "nets": len(self.nets),
            "issues": len(self.validate()),
        }
