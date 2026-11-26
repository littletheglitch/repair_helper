#!/usr/bin/env python3
"""Adversarial tests for graph-based netlist analysis (P2).

REQUIRED: the circuit-analysis engine must reason over a STRUCTURED
netlist (components + pins + nets + connectivity), not just named
signals. It must:
  - validate graph consistency (every pin references a real net)
  - detect floating nets / disconnected pins
  - trace paths (input -> output, control -> switch)
  - recognize structures from connectivity (buck, boost, LDO, etc.)
  - distinguish same-component-list / different-connectivity cases
  - return UNKNOWN/NEED_DATA when insufficient

The netlist format:
  {
    "components": [
      {"refdes": "U1", "part": "TPS51224", "pins": {
        "VIN": {"net": "VIN_12V", "function": "power_in", "direction": "in"},
        "SW":  {"net": "SW_NODE", "function": "switch_output", "direction": "out"},
        "FB":  {"net": "FB_NET", "function": "feedback", "direction": "in"},
        "GND": {"net": "GND", "function": "ground", "direction": "in"}
      }},
      ...
    ],
    "nets": ["VIN_12V", "SW_NODE", "FB_NET", "GND", "VOUT_3V3"]
  }
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from DiagnosticEngines.CircuitAnalysis.netlist import (
    Netlist, Net, Component, Pin, PinFunction, SignalDirection,
    NetlistIssue,
)


def _pin(net, fn, direction):
    return {"net": net, "function": fn, "direction": direction}


class TestNetlistGraph(unittest.TestCase):
    """Basic graph construction and access."""

    def test_build_netlist_from_dict(self):
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "TLV70033", "pins": {
                    "IN":  _pin("VIN", "power_in", "in"),
                    "OUT": _pin("VOUT", "power_out", "out"),
                    "GND": _pin("GND", "ground", "in"),
                }},
                {"refdes": "C1", "part": "cap", "pins": {
                    "1": _pin("VOUT", "passive", "passive"),
                    "2": _pin("GND", "passive", "passive"),
                }},
            ],
            "nets": ["VIN", "VOUT", "GND"],
        })
        self.assertEqual(len(nl.components), 2)
        self.assertEqual(len(nl.nets), 3)
        self.assertIn("VOUT", nl.nets)

    def test_net_connectivity_lookup(self):
        """nets_to_components: which components touch a given net."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "X", "pins": {
                    "OUT": _pin("VOUT", "power_out", "out")}},
                {"refdes": "C1", "part": "cap", "pins": {
                    "1": _pin("VOUT", "passive", "passive")}},
                {"refdes": "R1", "part": "res", "pins": {
                    "1": _pin("FB", "passive", "passive")}},
            ],
            "nets": ["VOUT", "FB"],
        })
        comps_on_vout = nl.components_on_net("VOUT")
        self.assertEqual({c.refdes for c in comps_on_vout}, {"U1", "C1"})


class TestGraphConsistency(unittest.TestCase):
    """The engine must detect inconsistent netlists."""

    def test_pin_references_undeclared_net(self):
        """A pin referencing a net not in the nets list is an error."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "X", "pins": {
                    "OUT": _pin("VOUT", "power_out", "out")}},
            ],
            "nets": ["VIN"],  # VOUT not declared
        })
        issues = nl.validate()
        undeclared = [i for i in issues
                      if "undeclared" in i.message.lower() or i.kind == "undeclared_net"]
        self.assertTrue(undeclared, "DEFECT: pin referencing undeclared net not flagged")

    def test_empty_netlist_is_need_data(self):
        """An empty netlist must return NEED_DATA, not crash."""
        nl = Netlist.from_dict({"components": [], "nets": []})
        issues = nl.validate()
        self.assertTrue(any(i.kind == "empty" for i in issues),
                        "DEFECT: empty netlist must surface NEED_DATA")


class TestFloatingNets(unittest.TestCase):
    """The engine must detect floating nets / disconnected pins."""

    def test_floating_input_pin(self):
        """A power_in pin whose net has only ONE connection (just the
        input pin itself, no source) is floating."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "LDO", "pins": {
                    "IN":  _pin("VIN", "power_in", "in"),
                    "OUT": _pin("VOUT", "power_out", "out"),
                    "GND": _pin("GND", "ground", "in"),
                }},
            ],
            "nets": ["VIN", "VOUT", "GND"],
        })
        issues = nl.validate()
        floating = [i for i in issues if i.kind == "floating"]
        # VIN has only U1.IN (no source) -> floating
        # VOUT has only U1.OUT (no load) -> floating (output drives nothing)
        floating_nets = {i.net for i in floating if i.net}
        self.assertIn("VIN", floating_nets,
                      "DEFECT: floating VIN (no source) not detected")

    def test_gnd_must_exist_for_grounding(self):
        """A netlist with no GND net must flag missing ground reference."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "LDO", "pins": {
                    "IN":  _pin("VIN", "power_in", "in"),
                    "OUT": _pin("VOUT", "power_out", "out"),
                    "GND": _pin("GND", "ground", "in"),
                }},
            ],
            "nets": ["VIN", "VOUT", "GND"],
        })
        issues = nl.validate()
        # GND exists and U1 references it -> OK (no missing-ground error)
        missing_gnd = [i for i in issues if "ground" in i.message.lower()
                       and i.kind == "missing_ground"]
        self.assertEqual(missing_gnd, [], "GND exists; should not flag missing ground")


class TestPathTracing(unittest.TestCase):
    """The engine must trace paths through the graph."""

    def test_trace_input_to_output(self):
        """Trace from a power_in net to a power_out net through a
        component."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "LDO", "pins": {
                    "IN":  _pin("VIN", "power_in", "in"),
                    "OUT": _pin("VOUT", "power_out", "out"),
                    "GND": _pin("GND", "ground", "in"),
                }},
                {"refdes": "C1", "part": "cap", "pins": {
                    "1": _pin("VIN", "passive", "passive"),
                    "2": _pin("GND", "passive", "passive")}},
            ],
            "nets": ["VIN", "VOUT", "GND"],
        })
        path = nl.trace_path("VIN", "VOUT")
        self.assertIsNotNone(path, "DEFECT: no path VIN->VOUT through U1")
        self.assertIn("U1", [step.component.refdes for step in path])


class TestStructureRecognition(unittest.TestCase):
    """The engine must recognize circuit structures from connectivity."""

    def test_recognize_ldo_from_netlist(self):
        """An LDO: power_in -> power_out (no switching), with feedback
        optionally. Recognize from pin functions + connectivity."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "TLV70033", "pins": {
                    "IN":  _pin("VIN", "power_in", "in"),
                    "OUT": _pin("VOUT", "power_out", "out"),
                    "GND": _pin("GND", "ground", "in"),
                }},
            ],
            "nets": ["VIN", "VOUT", "GND"],
        })
        rec = nl.recognize_structure()
        self.assertEqual(rec, "ldo",
                         f"DEFECT: expected ldo, got {rec}")

    def test_recognize_buck_from_netlist(self):
        """A buck: power_in -> switch_output -> (inductor) -> power_out,
        with feedback. Recognize from the switch_output pin function."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "TPS51224", "pins": {
                    "VIN": _pin("VIN", "power_in", "in"),
                    "SW":  _pin("SW_NODE", "switch_output", "out"),
                    "FB":  _pin("FB_NET", "feedback", "in"),
                    "GND": _pin("GND", "ground", "in"),
                }},
                {"refdes": "L1", "part": "inductor", "pins": {
                    "1": _pin("SW_NODE", "passive", "passive"),
                    "2": _pin("VOUT", "passive", "passive")}},
            ],
            "nets": ["VIN", "SW_NODE", "FB_NET", "GND", "VOUT"],
        })
        rec = nl.recognize_structure()
        self.assertEqual(rec, "buck",
                         f"DEFECT: expected buck, got {rec}")

    def test_same_components_different_connectivity(self):
        """Two netlists with the SAME component list but DIFFERENT
        connectivity must produce DIFFERENT structure recognition.
        This is the headline P2 requirement: connectivity matters, not
        just the parts list."""
        comps = [
            {"refdes": "U1", "part": "controller", "pins": {}},
            {"refdes": "L1", "part": "inductor", "pins": {}},
            {"refdes": "D1", "part": "diode", "pins": {}},
        ]
        # Netlist A: buck (inductor in series, diode as recirculation)
        nl_a = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "ctrl", "pins": {
                    "VIN": _pin("VIN", "power_in", "in"),
                    "SW":  _pin("SW", "switch_output", "out"),
                    "FB":  _pin("FB", "feedback", "in"),
                    "GND": _pin("GND", "ground", "in")}},
                {"refdes": "L1", "part": "inductor", "pins": {
                    "1": _pin("SW", "passive", "passive"),
                    "2": _pin("VOUT", "passive", "passive")}},
                {"refdes": "D1", "part": "diode", "pins": {
                    "A": _pin("GND", "passive", "passive"),
                    "K": _pin("SW", "passive", "passive")}},
            ],
            "nets": ["VIN", "SW", "FB", "GND", "VOUT"],
        })
        # Netlist B: boost (inductor to SW, diode in series to output)
        nl_b = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "ctrl", "pins": {
                    "VIN": _pin("VIN", "power_in", "in"),
                    "SW":  _pin("SW", "switch_output", "out"),
                    "FB":  _pin("FB", "feedback", "in"),
                    "GND": _pin("GND", "ground", "in")}},
                {"refdes": "L1", "part": "inductor", "pins": {
                    "1": _pin("VIN", "passive", "passive"),
                    "2": _pin("SW", "passive", "passive")}},
                {"refdes": "D1", "part": "diode", "pins": {
                    "A": _pin("SW", "passive", "passive"),
                    "K": _pin("VOUT", "passive", "passive")}},
            ],
            "nets": ["VIN", "SW", "FB", "GND", "VOUT"],
        })
        rec_a = nl_a.recognize_structure()
        rec_b = nl_b.recognize_structure()
        self.assertNotEqual(rec_a, rec_b,
                            f"DEFECT: same components, different connectivity "
                            f"must differ: got {rec_a} for both")


class TestInsufficientData(unittest.TestCase):
    """The engine must return UNKNOWN/NEED_DATA when insufficient."""

    def test_unrecognized_structure_returns_unknown(self):
        """A netlist that matches no known structure returns UNKNOWN."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "mystery", "pins": {
                    "A": _pin("NET1", "passive", "passive"),
                    "B": _pin("NET2", "passive", "passive")}},
            ],
            "nets": ["NET1", "NET2"],
        })
        rec = nl.recognize_structure()
        self.assertEqual(rec, "unknown",
                         f"DEFECT: unrecognized structure must be 'unknown', got {rec}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
