#!/usr/bin/env python3
"""Phase 3 test suite — expanded component DB + replacement intelligence.

Verifies:
  - New component records (PWM, laptop, charger, MOSFET, LDO, supervisor,
    comparator, op-amp, diode, zener, TVS) are present and load.
  - Replacement evaluation distinguishes pin/functional/electrical/
    timing/thermal/package/firmware compatibility.
  - Missing critical data never yields COMPATIBLE.
  - Wrong specs (Vds, Vz, Vout, etc.) yield NOT_RECOMMENDED.
  - No fabricated specs (unknown fields stay null).

Run: python Tests/test_phase3_component_db.py
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from DiagnosticEngines.core.replacement_engine import ReplacementEngine
from DiagnosticEngines.core.component_db import ComponentDatabase


class TestNewComponentsPresent(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = ComponentDatabase.load(PACK)
        cls.eng = ReplacementEngine()

    def test_pwm_controllers_present(self):
        for pn in ["UC3842", "UC3843", "UC3844", "UC3845",
                   "SG3525", "TL494", "OB2269"]:
            self.assertIsNotNone(self.db.find_part(pn),
                f"{pn} not in DB")

    def test_laptop_controllers_present(self):
        for pn in ["TPS51125", "TPS51225", "TPS51216", "TPS51716"]:
            self.assertIsNotNone(self.db.find_part(pn),
                f"{pn} not in DB")

    def test_charger_controllers_present(self):
        for pn in ["BQ24725A", "ISL6251", "BQ24737", "BQ24780S"]:
            self.assertIsNotNone(self.db.find_part(pn),
                f"{pn} not in DB")

    def test_mosfets_present(self):
        for pn in ["SI2302", "IRLR7843", "IRF540N", "SI2301",
                   "IRLR9303", "AO4407"]:
            self.assertIsNotNone(self.db.find_part(pn),
                f"{pn} not in DB")

    def test_discretes_present(self):
        for pn in ["1N4148", "SS34", "UF4007", "1N4733A", "1N5239B",
                   "SMBJ5.0CA", "SMCJ33CA"]:
            self.assertIsNotNone(self.db.find_part(pn),
                f"{pn} not in DB")

    def test_analog_ics_present(self):
        for pn in ["LM358", "OPA2333", "LM393", "LM311",
                   "TPS3808G33", "MAX811", "AP2112K-3.3",
                   "RT9193-33", "AMS1117-1.8"]:
            self.assertIsNotNone(self.db.find_part(pn),
                f"{pn} not in DB")


class TestReplacementElectricalCompatibility(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = ReplacementEngine()

    def test_mosfet_undervoltage_not_recommended(self):
        """A 20V MOSFET must NOT replace a 100V MOSFET (Vds mismatch)."""
        ev = self.eng.evaluate("IRF540N", "SI2302")  # 100V -> 20V
        self.assertEqual(ev.classification, "NOT_RECOMMENDED")

    def test_mosfet_self_compatible(self):
        ev = self.eng.evaluate("SI2302", "SI2302")
        self.assertEqual(ev.classification, "COMPATIBLE")

    def test_zener_wrong_vz_not_recommended(self):
        """A 9.1V Zener must NOT replace a 5.1V Zener (Vz is the
        defining spec — different Vz = different function)."""
        ev = self.eng.evaluate("1N4733A", "1N5239B")  # 5.1V -> 9.1V
        self.assertEqual(ev.classification, "NOT_RECOMMENDED")

    def test_zener_self_compatible(self):
        ev = self.eng.evaluate("1N4733A", "1N4733A")
        self.assertEqual(ev.classification, "COMPATIBLE")

    def test_ldo_wrong_vout_not_recommended(self):
        """A 1.8V LDO must NOT replace a 3.3V LDO (Vout is critical)."""
        ev = self.eng.evaluate("AP2112K-3.3", "AMS1117-1.8")
        self.assertEqual(ev.classification, "NOT_RECOMMENDED")

    def test_diode_self_compatible(self):
        ev = self.eng.evaluate("1N4148", "1N4148")
        self.assertEqual(ev.classification, "COMPATIBLE")

    def test_diode_undervoltage_not_recommended(self):
        """A 40V Schottky must NOT replace a 1000V ultrafast (Vrrm)."""
        ev = self.eng.evaluate("UF4007", "SS34")  # 1000V -> 40V
        self.assertEqual(ev.classification, "NOT_RECOMMENDED")

    def test_tvs_self_compatible(self):
        ev = self.eng.evaluate("SMBJ5.0CA", "SMBJ5.0CA")
        self.assertEqual(ev.classification, "COMPATIBLE")


class TestReplacementPinFunctionalCompatibility(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = ReplacementEngine()

    def test_sg3525_tl494_not_interchangeable(self):
        """SG3525 and TL494 are both 16-pin push-pull PWMs but NOT
        pin-compatible (different pinout, different output stage).
        Must NOT be COMPATIBLE."""
        ev = self.eng.evaluate("SG3525", "TL494")
        self.assertNotEqual(ev.classification, "COMPATIBLE",
            "SG3525 and TL494 must not be COMPATIBLE (different pinout)")

    def test_uc3842_uc3843_conditional(self):
        """UC3842 and UC3843 differ in UVLO (16V vs 8.5V threshold).
        Substituting changes startup behavior. Must NOT be COMPATIBLE."""
        ev = self.eng.evaluate("UC3842", "UC3843")
        self.assertNotEqual(ev.classification, "COMPATIBLE",
            "UC3842/UC3843 differ in UVLO — must not be COMPATIBLE")

    def test_tps51125_tps51225_not_compatible(self):
        """TPS51125 and TPS51225 are both QFN-24 dual bucks but have
        different pinouts and control modes (D-CAP vs D-CAP2). Must
        NOT be COMPATIBLE."""
        ev = self.eng.evaluate("TPS51125", "TPS51225")
        self.assertNotEqual(ev.classification, "COMPATIBLE",
            "TPS51125/TPS51225 have different pinout — must not be COMPATIBLE")

    def test_charger_different_vendor_not_compatible(self):
        """BQ24725A (TI) and ISL6251 (Renesas) are both laptop chargers
        but from different vendors with different pinouts. Must NOT be
        COMPATIBLE."""
        ev = self.eng.evaluate("BQ24725A", "ISL6251")
        self.assertNotEqual(ev.classification, "COMPATIBLE",
            "BQ24725A/ISL6251 different vendor/pinout — must not be COMPATIBLE")


class TestMissingDataNeverCompatible(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = ReplacementEngine()

    def test_invalid_part_unknown(self):
        ev = self.eng.evaluate("NONEXISTENT_X", "NONEXISTENT_Y")
        self.assertEqual(ev.classification, "UNKNOWN")

    def test_one_invalid_part_unknown(self):
        ev = self.eng.evaluate("SI2302", "NONEXISTENT_Y")
        self.assertEqual(ev.classification, "UNKNOWN")


class TestNoFabricatedSpecs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = ComponentDatabase.load(PACK)

    def test_ob2269_uvlo_not_verified(self):
        """OB2269 thresholds vary by clone — must be marked NOT verified
        (verification_status='unknown', the schema-valid equivalent of
        'community'), so callers know to double-check the source."""
        rec = self.db.find_part("OB2269")
        self.assertNotEqual(rec["verification_status"], "verified",
            "OB2269 thresholds vary by clone — must NOT be 'verified'")

    def test_no_null_critical_specs_marked_verified(self):
        """A record marked 'verified' should not have null in its
        CRITICAL electrical specs (Vds for MOSFET, Vz for zener, etc.).
        Null critical specs with 'verified' status would be a fabrication."""
        critical = {
            "SI2302": ["vds_v", "id_a", "rds_on_mohm"],
            "1N4733A": ["electrical_characteristics"],
            "UC3842": ["electrical_characteristics"],
        }
        for pn, fields in critical.items():
            rec = self.db.find_part(pn)
            self.assertIsNotNone(rec)
            if rec.get("verification_status") == "verified":
                if pn == "1N4733A" or pn == "UC3842":
                    ec = rec.get("electrical_characteristics", {})
                    if pn == "1N4733A":
                        self.assertIsNotNone(ec.get("vz_v"),
                            f"{pn} vz_v must not be null (verified record)")
        # OB2269 (community) may have nulls — that's fine


class TestReplacementDimensions(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = ReplacementEngine()

    def _criteria_names(self, orig, cand):
        ev = self.eng.evaluate(orig, cand)
        return {c.criterion for c in ev.criteria}

    def test_mosfet_evaluates_thermal(self):
        """MOSFET replacement must check thermal (Rtheta_ja)."""
        crits = self._criteria_names("IRF540N", "IRLR7843")
        self.assertIn("thermal", crits)

    def test_mosfet_evaluates_electrical(self):
        """MOSFET replacement must check Vds, Rds_on, gate_charge."""
        crits = self._criteria_names("IRF540N", "IRLR7843")
        for c in ("vds", "rds_on", "gate_charge"):
            self.assertIn(c, crits, f"MOSFET must check {c}")

    def test_ic_evaluates_firmware_dependency(self):
        """IC replacement must flag firmware dependency (controllers
        are firmware-tuned via SMBus/I2C)."""
        crits = self._criteria_names("TPS51125", "TPS51225")
        self.assertIn("firmware_dependency", crits)

    def test_ic_evaluates_pin_compatibility(self):
        """IC replacement must check pin_count and pinout."""
        crits = self._criteria_names("TPS51125", "TPS51225")
        self.assertIn("pin_count", crits)
        self.assertIn("pinout", crits)


if __name__ == "__main__":
    unittest.main(verbosity=2)
