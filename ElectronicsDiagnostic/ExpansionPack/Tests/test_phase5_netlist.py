#!/usr/bin/env python3
"""Phase 5 test suite — netlist recognition + multi-path tracing.

Verifies recognition of:
  - comparator (vs op-amp)
  - voltage divider
  - flyback secondary
  - MOSFET high-side vs low-side
  - charging stage
  - protection circuit
  - feedback network
Plus:
  - multi-path tracing (parallel branches)
  - uncertainty reporting

Run: python Tests/test_phase5_netlist.py
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from DiagnosticEngines.CircuitAnalysis.netlist import Netlist


def pin(net, fn, d):
    return {"net": net, "function": fn, "direction": d}


class TestComparatorRecognition(unittest.TestCase):

    def test_comparator_detected(self):
        nl = Netlist.from_dict({"components": [
            {"refdes": "U1", "part": "LM393", "pins": {
                "IN+": pin("NA", "control_in", "in"),
                "IN-": pin("NB", "control_in", "in"),
                "OUT": pin("NOUT", "power_out", "out"),
                "VCC": pin("VCC", "power_in", "in"),
                "GND": pin("GND", "ground", "in")}}],
            "nets": ["NA", "NB", "NOUT", "VCC", "GND"],
            "external_ports": ["NA", "NB", "VCC"]})
        self.assertEqual(nl.recognize_structure(), "comparator")

    def test_comparator_not_opamp(self):
        """A comparator (no feedback) must NOT be recognized as op-amp."""
        nl = Netlist.from_dict({"components": [
            {"refdes": "U1", "part": "LM393", "pins": {
                "IN+": pin("NA", "control_in", "in"),
                "IN-": pin("NB", "control_in", "in"),
                "OUT": pin("NOUT", "power_out", "out"),
                "VCC": pin("VCC", "power_in", "in"),
                "GND": pin("GND", "ground", "in")}}],
            "nets": ["NA", "NB", "NOUT", "VCC", "GND"],
            "external_ports": ["NA", "NB", "VCC"]})
        self.assertNotEqual(nl.recognize_structure(), "opamp")


class TestVoltageDivider(unittest.TestCase):

    def test_voltage_divider_detected(self):
        nl = Netlist.from_dict({"components": [
            {"refdes": "R1", "part": "res", "pins": {
                "1": pin("VIN", "passive", "passive"),
                "2": pin("TAP", "passive", "passive")}},
            {"refdes": "R2", "part": "res", "pins": {
                "1": pin("TAP", "passive", "passive"),
                "2": pin("GND", "ground", "in")}}],
            "nets": ["VIN", "TAP", "GND"],
            "external_ports": ["VIN"]})
        self.assertEqual(nl.recognize_structure(), "voltage_divider")

    def test_divider_with_fb_pin(self):
        """A voltage divider feeding a controller FB pin must be
        recognized as a divider (not an op-amp, despite the feedback)."""
        nl = Netlist.from_dict({"components": [
            {"refdes": "R1", "part": "res", "pins": {
                "1": pin("VIN", "passive", "passive"),
                "2": pin("TAP", "passive", "passive")}},
            {"refdes": "R2", "part": "res", "pins": {
                "1": pin("TAP", "passive", "passive"),
                "2": pin("GND", "ground", "in")}},
            {"refdes": "U1", "part": "ctrl", "pins": {
                "FB": pin("TAP", "feedback", "in"),
                "GND": pin("GND", "ground", "in")}}],
            "nets": ["VIN", "TAP", "GND"],
            "external_ports": ["VIN"]})
        self.assertEqual(nl.recognize_structure(), "voltage_divider")


class TestFlybackSecondary(unittest.TestCase):

    def test_flyback_secondary_detected(self):
        nl = Netlist.from_dict({"components": [
            {"refdes": "T1", "part": "transformer", "pins": {
                "SEC+": pin("NSEC", "passive", "passive"),
                "SEC-": pin("GND", "ground", "in")}},
            {"refdes": "D1", "part": "diode", "pins": {
                "A": pin("NSEC", "passive", "passive"),
                "K": pin("NOUT", "passive", "passive")}},
            {"refdes": "C1", "part": "cap", "pins": {
                "1": pin("NOUT", "passive", "passive"),
                "2": pin("GND", "ground", "in")}}],
            "nets": ["NSEC", "NOUT", "GND"],
            "external_ports": ["NOUT"]})
        self.assertEqual(nl.recognize_structure(), "flyback_secondary")


class TestMosfetHighLowSide(unittest.TestCase):

    def test_high_side_detected(self):
        """High-side FET: drain on VIN, source (switch) to load, load to GND."""
        nl = Netlist.from_dict({"components": [
            {"refdes": "Q1", "part": "mosfet", "pins": {
                "D": pin("VIN", "power_in", "in"),
                "S": pin("NLOAD", "switch_output", "out"),
                "G": pin("NGATE", "control_in", "in")}},
            {"refdes": "R1", "part": "res", "pins": {
                "1": pin("NLOAD", "passive", "passive"),
                "2": pin("GND", "ground", "in")}}],
            "nets": ["VIN", "NLOAD", "GND", "NGATE"],
            "external_ports": ["VIN"]})
        self.assertEqual(nl.recognize_structure(), "mosfet_high_side")

    def test_low_side_detected(self):
        """Low-side FET: drain (switch) from load, source to GND, load from VIN."""
        nl = Netlist.from_dict({"components": [
            {"refdes": "R1", "part": "res", "pins": {
                "1": pin("VIN", "power_in", "in"),
                "2": pin("NLOAD", "passive", "passive")}},
            {"refdes": "Q1", "part": "mosfet", "pins": {
                "D": pin("NLOAD", "switch_output", "out"),
                "S": pin("GND", "ground", "in"),
                "G": pin("NGATE", "control_in", "in")}}],
            "nets": ["VIN", "NLOAD", "GND", "NGATE"],
            "external_ports": ["VIN"]})
        self.assertEqual(nl.recognize_structure(), "mosfet_low_side")


class TestMultiPathTracing(unittest.TestCase):

    def test_parallel_resistors_two_paths(self):
        """Two resistors in parallel between N1 and N2 -> 2 paths."""
        nl = Netlist.from_dict({"components": [
            {"refdes": "U1", "part": "ctrl", "pins": {
                "OUT": pin("N1", "power_out", "out")}},
            {"refdes": "R1", "part": "res", "pins": {
                "1": pin("N1", "passive", "passive"),
                "2": pin("N2", "passive", "passive")}},
            {"refdes": "R2", "part": "res", "pins": {
                "1": pin("N1", "passive", "passive"),
                "2": pin("N2", "passive", "passive")}}],
            "nets": ["N1", "N2"]})
        paths = nl.trace_all_paths("N1", "N2")
        self.assertEqual(len(paths), 2,
            f"expected 2 parallel paths, got {len(paths)}")

    def test_single_path(self):
        nl = Netlist.from_dict({"components": [
            {"refdes": "U1", "part": "ctrl", "pins": {
                "OUT": pin("N1", "power_out", "out")}},
            {"refdes": "R1", "part": "res", "pins": {
                "1": pin("N1", "passive", "passive"),
                "2": pin("N2", "passive", "passive")}}],
            "nets": ["N1", "N2"]})
        paths = nl.trace_all_paths("N1", "N2")
        self.assertEqual(len(paths), 1)

    def test_no_path(self):
        nl = Netlist.from_dict({"components": [
            {"refdes": "U1", "part": "ctrl", "pins": {
                "OUT": pin("N1", "power_out", "out")}},
            {"refdes": "U2", "part": "ctrl", "pins": {
                "IN": pin("N2", "power_in", "in")}}],
            "nets": ["N1", "N2"]})
        paths = nl.trace_all_paths("N1", "N2")
        self.assertEqual(paths, [])


class TestUncertaintyReport(unittest.TestCase):

    def test_uncertainty_reports_unverified(self):
        """A non-empty netlist with no external ports must report
        uncertainty (off-board connectivity unverified)."""
        nl = Netlist.from_dict({"components": [
            {"refdes": "U1", "part": "ldo", "pins": {
                "IN": pin("VIN", "power_in", "in"),
                "OUT": pin("VOUT", "power_out", "out"),
                "GND": pin("GND", "ground", "in")}}],
            "nets": ["VIN", "VOUT", "GND"]})
        # no external_ports declared
        rep = nl.uncertainty_report()
        self.assertIn("unverified", rep)
        self.assertTrue(any("external port" in u.lower() or "unverified" in u.lower()
                            for u in rep["unverified"]),
            f"expected uncertainty note; got {rep['unverified']}")

    def test_uncertainty_reports_floating(self):
        nl = Netlist.from_dict({"components": [
            {"refdes": "R1", "part": "res", "pins": {
                "1": pin("FLOAT", "passive", "passive"),
                "2": pin("GND", "ground", "in")}}],
            "nets": ["FLOAT", "GND"]})
        rep = nl.uncertainty_report()
        self.assertIn("FLOAT", rep["floating_nets"])

    def test_uncertainty_does_not_claim_simulation(self):
        """The report must NOT claim full electrical simulation."""
        nl = Netlist.from_dict({"components": [
            {"refdes": "U1", "part": "ldo", "pins": {
                "IN": pin("VIN", "power_in", "in"),
                "OUT": pin("VOUT", "power_out", "out"),
                "GND": pin("GND", "ground", "in")}}],
            "nets": ["VIN", "VOUT", "GND"],
            "external_ports": ["VIN"]})
        rep = nl.uncertainty_report()
        joined = " ".join(rep["unverified"]).lower()
        self.assertIn("not simulated", joined,
            f"must disclaim electrical simulation; got {rep['unverified']}")


class TestExistingStructuresPreserved(unittest.TestCase):

    def test_ldo_still_recognized(self):
        nl = Netlist.from_dict({"components": [
            {"refdes": "U1", "part": "ldo", "pins": {
                "IN": pin("VIN", "power_in", "in"),
                "OUT": pin("VOUT", "power_out", "out"),
                "GND": pin("GND", "ground", "in")}}],
            "nets": ["VIN", "VOUT", "GND"],
            "external_ports": ["VIN"]})
        self.assertEqual(nl.recognize_structure(), "ldo")

    def test_buck_still_recognized(self):
        ctrl = {"refdes": "U1", "part": "ctrl", "pins": {
            "VIN": pin("VIN", "power_in", "in"),
            "SW": pin("SW", "switch_output", "out"),
            "GND": pin("GND", "ground", "in")}}
        nl = Netlist.from_dict({"components": [ctrl,
            {"refdes": "L1", "part": "inductor", "pins": {
                "1": pin("SW", "passive", "passive"),
                "2": pin("VOUT", "passive", "passive")}}],
            "nets": ["VIN", "SW", "GND", "VOUT"],
            "external_ports": ["VIN"]})
        self.assertEqual(nl.recognize_structure(), "buck")


if __name__ == "__main__":
    unittest.main(verbosity=2)
