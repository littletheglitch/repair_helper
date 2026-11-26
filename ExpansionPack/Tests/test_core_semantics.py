#!/usr/bin/env python3
"""Adversarial tests for core fact-state semantics (Priority 0).

These tests encode the REQUIRED behavior. The first run, before any
fix, demonstrates the defect: an unmeasured resistance is treated as
"not shorted" (rail_short=False), which is a logical error — an
unmeasured resistance can neither prove nor disprove a short.

Three-state semantics REQUIRED:
  - NOT_MEASURED  : no reading taken   -> fact is None (unknown)
  - MEASURED_ZERO : reading is 0.0     -> fact is False (confirmed absent)
  - IN_RANGE      : reading is a value -> fact is True/False per threshold
  - INVALID       : reading unusable   -> fact is None (unknown)

An unmeasured `rail_short` MUST propagate as None (unknown), so rules
gated on `NOT rail_short` evaluate to None (requires_info), NOT True.

Derived facts (ripple_excessive, rail_resistance_low) are SUSPECTED,
not confirmed. They must not be treated as independently measured.
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from DiagnosticEngines.core.engine import load_engine, Measurement


def M(name, value, measured=True):
    return Measurement(name=name, value=value, measured=measured)


class TestRailShortThreeState(unittest.TestCase):
    """The headline defect: rail_short must be three-state."""

    @classmethod
    def setUpClass(cls):
        cls.eng = load_engine()

    def test_unmeasured_rail_short_is_unknown_not_false(self):
        """An unmeasured rail_short must NOT default to False.

        With VIN/EN/switching measured and rail_voltage zero, the
        chain rules gate on `NOT rail_short`. If rail_short defaults
        to False, `NOT False` = True and a fault rule fires as if the
        absence of a short were confirmed. That is a logical error:
        the technician never measured resistance.

        REQUIRED: the fact namespace must show rail_short=None (unknown),
        and any rule whose condition depends on `NOT rail_short` must
        evaluate to None (requires_info), not fire.
        """
        from DiagnosticEngines.core.engine import DiagnosticEngine
        rail = self.eng.all_rails()["+3VALW"]
        facts = DiagnosticEngine._facts_from_measurements(
            [M("rail_voltage", 0.0), M("vin", 19.0), M("en", True),
             M("switching", True)],
            rail,
        )
        self.assertIsNone(
            facts.get("rail_short"),
            f"DEFECT: rail_short defaults to {facts.get('rail_short')!r} "
            f"when unmeasured. An unmeasured resistance cannot prove a "
            f"rail is not shorted; it must be None (unknown).",
        )

    def test_measured_false_rail_short_is_confirmed_false(self):
        """When the technician measured rail_short=False (resistance
        normal), the fact is confirmed False."""
        from DiagnosticEngines.core.engine import DiagnosticEngine
        rail = self.eng.all_rails()["+3VALW"]
        facts = DiagnosticEngine._facts_from_measurements(
            [M("rail_voltage", 0.0), M("rail_short", False)],
            rail,
        )
        self.assertFalse(facts.get("rail_short"))

    def test_measured_true_rail_short_is_confirmed_true(self):
        """When the technician measured rail_short=True (short
        confirmed), the fact is confirmed True."""
        from DiagnosticEngines.core.engine import DiagnosticEngine
        rail = self.eng.all_rails()["+3VALW"]
        facts = DiagnosticEngine._facts_from_measurements(
            [M("rail_voltage", 0.0), M("rail_short", True)],
            rail,
        )
        self.assertTrue(facts.get("rail_short"))


class TestFactProvenance(unittest.TestCase):
    """Derived facts must be distinguishable from measured facts."""

    @classmethod
    def setUpClass(cls):
        cls.eng = load_engine()

    def test_ripple_excessive_is_derived_not_measured(self):
        """ripple_excessive is DERIVED from a ripple measurement, not
        independently measured. The fact namespace must record its
        provenance as 'derived'."""
        from DiagnosticEngines.core.engine import DiagnosticEngine
        rail = self.eng.all_rails()["+3VALW"]
        facts = DiagnosticEngine._facts_from_measurements(
            [M("ripple", 120.0)], rail,
        )
        self.assertTrue(facts.get("ripple_excessive"))
        # Provenance: derived facts recorded in a provenance map
        prov = facts.get("_provenance", {})
        self.assertEqual(
            prov.get("ripple_excessive"), "derived",
            "DEFECT: ripple_excessive provenance must be 'derived', "
            f"got {prov.get('ripple_excessive')!r}",
        )

    def test_rail_resistance_low_is_suspected_not_confirmed(self):
        """rail_resistance_low is a SUSPECT (low resistance might be a
        short OR a normal low-impedance rail). It must be marked
        'suspected', not 'confirmed'."""
        from DiagnosticEngines.core.engine import DiagnosticEngine
        rail = self.eng.all_rails()["+3VALW"]
        facts = DiagnosticEngine._facts_from_measurements(
            [M("rail_resistance", 5.0)], rail,
        )
        self.assertTrue(facts.get("rail_resistance_low"))
        prov = facts.get("_provenance", {})
        self.assertEqual(
            prov.get("rail_resistance_low"), "suspected",
            "DEFECT: rail_resistance_low provenance must be 'suspected'",
        )

    def test_measured_voltage_is_confirmed(self):
        """A directly-measured voltage fact is 'confirmed'."""
        from DiagnosticEngines.core.engine import DiagnosticEngine
        rail = self.eng.all_rails()["+3VALW"]
        facts = DiagnosticEngine._facts_from_measurements(
            [M("rail_voltage", 3.3)], rail,
        )
        prov = facts.get("_provenance", {})
        self.assertEqual(
            prov.get("rail_voltage"), "confirmed",
            f"measured voltage provenance must be 'confirmed', got {prov.get('rail_voltage')!r}",
        )


class TestConflictingEvidence(unittest.TestCase):
    """Contradictory measurements must surface CONFLICTING_EVIDENCE."""

    @classmethod
    def setUpClass(cls):
        cls.eng = load_engine()

    def test_rail_present_and_zero_conflict(self):
        """If two measurements of the same rail disagree (one in-range,
        one zero), the engine must flag CONFLICTING_EVIDENCE, not pick
        one arbitrarily."""
        from DiagnosticEngines.core.engine import DiagnosticEngine
        rail = self.eng.all_rails()["+3VALW"]
        # Two readings of rail_voltage: 3.3V (in range) and 0.0V (zero)
        facts = DiagnosticEngine._facts_from_measurements(
            [Measurement(name="rail_voltage", value=3.3, measured=True),
             Measurement(name="rail_voltage", value=0.0, measured=True)],
            rail,
        )
        # The engine must detect the conflict.
        conflicts = facts.get("_conflicts", [])
        self.assertTrue(
            conflicts,
            "DEFECT: contradictory rail_voltage readings (3.3V and 0V) "
            "must produce a CONFLICTING_EVIDENCE entry.",
        )


class TestAssertedFactsScope(unittest.TestCase):
    """Follow-on facts asserted by a rule must not leak across sessions."""

    @classmethod
    def setUpClass(cls):
        cls.eng = load_engine()

    def test_asserted_facts_do_not_persist(self):
        """asserted_facts passed to one diagnose() call must not affect
        a subsequent call on the same engine instance."""
        # Call 1: assert overload=True
        r1 = self.eng.diagnose(
            "+3VALW",
            [Measurement(name="rail_voltage", value=2.5, measured=True)],
            asserted_facts={"overload": True},
        )
        # Call 2: no asserted_facts — must NOT see overload
        r2 = self.eng.diagnose(
            "+3VALW",
            [Measurement(name="rail_voltage", value=2.5, measured=True)],
        )
        self.assertNotIn(
            "OVERLOAD", r2.fired_rule_id or "",
            "DEFECT: asserted fact 'overload' leaked from call 1 to call 2.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
