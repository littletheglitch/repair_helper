#!/usr/bin/env python3
"""Phase 1 test matrix — framework Measurement migration.

Verifies the power-rail engine correctly distinguishes:
  - NOT_MEASURED vs MEASURED_ZERO vs INVALID vs IN_RANGE vs ABOVE_RANGE (OL)
  - 0V with VIN absent (expected) vs 0V with VIN present (real fault)
  - OL resistance (open, not short)
  - invalid voltage input
  - missing measurement
  - conflicting readings
  - multiple simultaneous faults
  - invalid units
  - measurements with incomplete metadata
  - unmeasured resistance NEVER proves a short is absent

Run: python Tests/test_phase1_measurement_states.py
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from DiagnosticEngines.core.engine import (
    load_engine, Measurement, DiagnosticEngine,
)
from DiagnosticEngines.core.framework.measurement import (
    Measurement as FM, MeasurementKind as MK, MeasurementState as MS,
    MeasurementCondition as MC,
)


def V(name, val, cond=MC.POWERED):
    return FM(name=name, kind=MK.VOLTAGE, state=MS.IN_RANGE, value=val,
              condition=cond)


def Z(name, cond=MC.POWERED):
    return FM(name=name, kind=MK.VOLTAGE, state=MS.MEASURED_ZERO, value=0.0,
              condition=cond)


def N(name, cond=MC.POWERED):
    return FM(name=name, kind=MK.VOLTAGE, state=MS.NOT_MEASURED, value=None,
              condition=cond)


def INV(name, cond=MC.POWERED):
    return FM(name=name, kind=MK.VOLTAGE, state=MS.INVALID, value=None,
              condition=cond)


def OL(name, kind=MK.VOLTAGE, cond=MC.POWERED):
    return FM(name=name, kind=kind, state=MS.ABOVE_RANGE, value=None,
              condition=cond)


def R_OL(name):
    return FM(name=name, kind=MK.RESISTANCE, state=MS.ABOVE_RANGE, value=None,
              condition=MC.UNPOWERED)


def R(name, val):
    return FM(name=name, kind=MK.RESISTANCE, state=MS.IN_RANGE, value=val,
              condition=MC.UNPOWERED)


def L(name, val):
    return FM(name=name, kind=MK.LOGIC_LEVEL, state=MS.IN_RANGE, value=val,
              condition=MC.POWERED)


class TestMeasurementStateFacts(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = load_engine()
        cls.rail = cls.eng.all_rails()["+3VALW"]

    def facts(self, ms):
        return DiagnosticEngine._facts_from_measurements(ms, self.rail)

    def test_measured_zero_distinct_from_not_measured(self):
        f = self.facts([Z("rail_voltage")])
        self.assertIs(f["rail_voltage_zero"], True)
        self.assertIs(f["rail_voltage_present"], False)
        self.assertIs(f["rail_voltage_measured"], True)

    def test_not_measured_distinct_from_zero(self):
        f = self.facts([N("rail_voltage")])
        self.assertIs(f["rail_voltage_measured"], None)
        self.assertIs(f["rail_voltage_zero"], None)
        self.assertIs(f["rail_voltage_present"], None)

    def test_invalid_distinct_from_zero_and_not_measured(self):
        f = self.facts([INV("rail_voltage")])
        self.assertIs(f["rail_voltage_invalid"], True)
        self.assertIs(f["rail_voltage_zero"], None)
        self.assertIs(f["rail_voltage_measured"], True)
        self.assertIs(f["rail_voltage_present"], None)

    def test_overload_distinct_from_in_range_high(self):
        f = self.facts([OL("rail_voltage")])
        self.assertIs(f["rail_voltage_overload"], True)
        self.assertIs(f["rail_voltage_out_of_range"], True)
        self.assertIs(f["rail_voltage_present"], None)
        # An in-range high reading (3.6V) is NOT overload
        f2 = self.facts([V("rail_voltage", 3.6)])
        self.assertIs(f2["rail_voltage_overload"], False)


class TestZeroVWithVin(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = load_engine()

    def test_0v_with_vin_absent_is_expected(self):
        """0V rail with VIN absent → VIN_ABSENT rule (the 0V is the
        expected consequence of no input, NOT a rail fault)."""
        r = self.eng.diagnose("+3VALW", [Z("rail_voltage"), Z("vin")])
        self.assertEqual(r.fired_rule_id, "RULE_FAULT_VIN_ABSENT")

    def test_0v_with_vin_present_is_real_fault(self):
        """0V rail with VIN present AND the upstream chain healthy
        (VCC/EN present, switching present) → a REAL fault rule fires
        (NOT the VIN-absent rule). The more specific
        RAIL_DEAD_WITH_SWITCHING (output-stage fault) fires here because
        switching is present but the rail is dead."""
        r = self.eng.diagnose("+3VALW", [
            Z("rail_voltage"), V("vin", 19.0),
            V("vcc", 5.0), L("en", True), L("switching", True)])
        # Must be a fault rule, NOT the VIN-absent rule.
        self.assertIn("RAIL_DEAD", r.fired_rule_id or "")
        self.assertNotEqual(r.fired_rule_id, "RULE_FAULT_VIN_ABSENT")

    def test_0v_with_vin_present_never_fires_vin_absent(self):
        """0V rail with VIN present must NEVER fire the VIN_ABSENT rule
        — the 0V here is a real fault, not the expected consequence of
        no input. With switching present, RAIL_DEAD_WITH_SWITCHING fires
        (output-stage fault); with switching unmeasured, the chain asks
        for more measurements. Either way, NOT VIN_ABSENT."""
        r1 = self.eng.diagnose("+3VALW", [
            Z("rail_voltage"), V("vin", 19.0),
            V("vcc", 5.0), L("en", True), L("switching", True)])
        self.assertNotEqual(r1.fired_rule_id, "RULE_FAULT_VIN_ABSENT")
        r2 = self.eng.diagnose("+3VALW", [
            Z("rail_voltage"), V("vin", 19.0),
            V("vcc", 5.0), L("en", True)])
        self.assertNotEqual(r2.fired_rule_id, "RULE_FAULT_VIN_ABSENT")

    def test_0v_with_vin_not_measured_requires_info(self):
        """0V rail with VIN not measured → cannot conclude; the engine
        must NOT assume VIN is present or absent."""
        r = self.eng.diagnose("+3VALW", [Z("rail_voltage"), N("vin")])
        # Either requires_info (VIN unmeasured) or surfaces the chain.
        # It must NOT fire RAIL_ZERO_WITH_VIN (which needs vin_present).
        self.assertNotEqual(r.fired_rule_id, "RULE_FAULT_RAIL_ZERO_WITH_VIN")


class TestOverloadResistance(unittest.TestCase):

    def test_ol_resistance_is_open_not_short(self):
        """An OL resistance reading means the rail is OPEN (very high R),
        not shorted. rail_resistance_low must be False."""
        eng = load_engine()
        rail = eng.all_rails()["+3VALW"]
        facts = DiagnosticEngine._facts_from_measurements(
            [R_OL("rail_resistance")], rail)
        self.assertIs(facts["rail_resistance_low"], False)
        self.assertIs(facts["rail_resistance_overload"], True)

    def test_low_resistance_is_suspect_short(self):
        facts = DiagnosticEngine._facts_from_measurements(
            [R("rail_resistance", 5.0)], eng_all_rails()["+3VALW"])
        self.assertIs(facts["rail_resistance_low"], True)


class TestInvalidVoltageInput(unittest.TestCase):

    def test_invalid_voltage_surfaces_rule_and_note(self):
        eng = load_engine()
        r = eng.diagnose("+3VALW", [INV("rail_voltage")])
        self.assertEqual(r.fired_rule_id, "RULE_FAULT_INVALID_READING")
        self.assertTrue(any("INVALID_READINGS" in n for n in r.notes))


class TestMissingMeasurement(unittest.TestCase):

    def test_no_measurements_surfaces_chain_or_requires_info(self):
        eng = load_engine()
        r = eng.diagnose("+3VALW", [])
        # With nothing measured, the resistance-first chain fires
        # (it asks to measure resistance) or requires_info.
        self.assertTrue(r.fired_rule_id is not None or r.requires_info)


class TestConflictingReadings(unittest.TestCase):

    def test_conflicting_voltage_clears_all_facts(self):
        eng = load_engine()
        rail = eng.all_rails()["+3VALW"]
        facts = DiagnosticEngine._facts_from_measurements(
            [V("rail_voltage", 3.3), V("rail_voltage", 0.0)], rail)
        self.assertIsNone(facts["rail_voltage"])
        self.assertIsNone(facts["rail_voltage_measured"])
        self.assertIsNone(facts["rail_voltage_present"])
        self.assertIsNone(facts["rail_voltage_zero"])
        self.assertEqual(len(facts["_conflicts"]), 1)

    def test_conflict_surfaces_in_notes(self):
        eng = load_engine()
        r = eng.diagnose("+3VALW", [
            V("rail_voltage", 3.3), V("rail_voltage", 0.0)])
        self.assertTrue(any("CONFLICTING_EVIDENCE" in n for n in r.notes))


class TestMultipleSimultaneousFaults(unittest.TestCase):

    def test_two_independent_faults_both_surfaced(self):
        eng = load_engine()
        # ripple excessive (priority 10) + vin absent (priority 20)
        r = eng.diagnose("+3VALW", [
            V("rail_voltage", 3.3), V("ripple", 120.0), Z("vin")])
        self.assertEqual(r.fired_rule_id, "RULE_RIPPLE_EXCESSIVE")
        self.assertTrue(any("SIMULTANEOUS" in n for n in r.notes))


class TestInvalidUnits(unittest.TestCase):

    def test_resistance_with_powered_condition_flagged(self):
        """A resistance reading taken POWERED is invalid data —
        resistance is only meaningful UNPOWERED. The framework's
        comparable_to() refuses to compare them."""
        m1 = R("rail_resistance", 5.0)  # UNPOWERED
        m2 = FM(name="rail_resistance", kind=MK.RESISTANCE,
                state=MS.IN_RANGE, value=5.0, condition=MC.POWERED)
        self.assertFalse(m1.comparable_to(m2),
            "Powered vs unpowered resistance must not be comparable")

    def test_voltage_with_wrong_unit_not_crash(self):
        """A voltage measurement with an empty/unit mismatch must not
        crash the engine."""
        eng = load_engine()
        m = FM(name="rail_voltage", kind=MK.VOLTAGE, state=MS.IN_RANGE,
               value=3.3, unit="A", condition=MC.POWERED)  # wrong unit
        r = eng.diagnose("+3VALW", [m])
        # Should still process (unit is informational) — no crash
        self.assertIsNotNone(r)


class TestIncompleteMetadata(unittest.TestCase):

    def test_measurement_without_instrument_or_technician(self):
        """A measurement with only name/kind/state/value (no instrument,
        no technician, no location) must still be processed (no crash,
        a rule fires or requires_info is surfaced)."""
        eng = load_engine()
        m = FM(name="rail_voltage", kind=MK.VOLTAGE, state=MS.IN_RANGE,
               value=3.3, condition=MC.POWERED)
        self.assertEqual(m.instrument, "")
        self.assertIsNone(m.technician)
        r = eng.diagnose("+3VALW", [m])
        # The point: incomplete metadata must not crash the engine.
        # With only rail_voltage measured (no VIN/resistance), the
        # chain correctly asks to check the adapter (VIN unconfirmed).
        self.assertTrue(r.fired_rule_id is not None or r.requires_info)

    def test_dict_payload_normalized(self):
        """A dict measurement (from the host adapter) must be normalized
        to framework form."""
        eng = load_engine()
        from DiagnosticEngines.core.engine import _normalize_measurement
        m = _normalize_measurement({
            "name": "rail_voltage", "kind": "voltage",
            "state": "in_range", "value": 3.3, "condition": "powered"})
        self.assertEqual(m.state, MS.IN_RANGE)
        self.assertEqual(m.value, 3.3)


class TestUnmeasuredResistanceNeverProvesNoShort(unittest.TestCase):

    def test_unmeasured_resistance_rail_short_is_none(self):
        eng = load_engine()
        rail = eng.all_rails()["+3VALW"]
        facts = DiagnosticEngine._facts_from_measurements([], rail)
        self.assertIsNone(facts.get("rail_short"),
            "rail_short must be None (unknown) when resistance is unmeasured")
        self.assertIsNone(facts.get("rail_resistance_low"),
            "rail_resistance_low must be None when unmeasured")

    def test_no_confirmed_short_true_when_unmeasured(self):
        """no_confirmed_short is True (chain proceeds) but the engine
        must NOT claim a short was ruled out."""
        eng = load_engine()
        rail = eng.all_rails()["+3VALW"]
        facts = DiagnosticEngine._facts_from_measurements([], rail)
        self.assertIs(facts["no_confirmed_short"], True)
        # And a diagnosis with unmeasured resistance surfaces the caveat
        r = eng.diagnose("+3VALW", [V("rail_voltage", 3.3)])
        self.assertTrue(any("NOT measured" in n or "not ruled out" in n.lower()
                            for n in r.notes),
            f"Expected a 'short not ruled out' caveat; notes={r.notes}")


def eng_all_rails():
    return load_engine().all_rails()


if __name__ == "__main__":
    unittest.main(verbosity=2)
