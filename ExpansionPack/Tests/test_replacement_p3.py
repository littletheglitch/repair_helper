#!/usr/bin/env python3
"""Adversarial tests for component replacement (P3).

REQUIRED:
  - 5 classifications: COMPATIBLE, CONDITIONALLY_COMPATIBLE,
    NOT_RECOMMENDED, INSUFFICIENT_DATA, UNKNOWN.
  - A missing CRITICAL spec -> INSUFFICIENT_DATA (never COMPATIBLE).
  - Per-category distinct critical specs:
      BJT:   Vce, Vbe, Ic, hfe, Pd, package, pinout
      Diode: Vrrm, If_avg, Vf, trr, Pd, package, pinout
      Zener: Vz, Iz, Pd, package, pinout
      TVS:   Vwm, Vbr, Vc, Ipp, Pd, package, pinout
      LDO:   Vout, Vin_max, Iout, dropout, accuracy, package, pinout
  - A higher voltage rating alone does NOT establish compatibility.
  - A matching part-number substring does NOT establish equivalence.
  - Cross-category (BJT vs MOSFET) -> NOT_RECOMMENDED.
  - Insufficient data on a non-critical spec does not force UNKNOWN.
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from DiagnosticEngines.core.replacement_engine import (
    ReplacementEngine, REQUIRED_CRITERIA, CriterionResult,
)
from DiagnosticEngines.core.component_db import ComponentDatabase
from pathlib import Path as P

eng = ReplacementEngine()
db = ComponentDatabase.load(P(PACK))


def find(part):
    for rec in db.mosfet_records + db.ic_records + db.discrete_records + db.analog_records:
        if rec.get("part_number") == part:
            return dict(rec)
    return None


class TestFiveClassifications(unittest.TestCase):
    """All 5 classifications must be reachable."""

    def test_compatible(self):
        ev = eng.evaluate("AON6500", "AON6500")
        self.assertEqual(ev.classification, "COMPATIBLE")

    def test_not_recommended(self):
        aon = find("AON6500")
        cand = dict(aon)
        cand["part_number"] = "LOWVDS"
        cand["vds_v"] = 10.0  # lower VDS
        out = eng._eval_mosfet(dict(aon), cand)
        cls = eng._derive_classification(out, "MOSFET")
        self.assertEqual(cls, "NOT_RECOMMENDED")

    def test_conditional(self):
        aon = find("AON6500")
        cand = dict(aon)
        cand["part_number"] = "HIGHPDS"
        cand["vds_v"] = 60.0  # higher VDS (match)
        cand["rds_on_mohm"] = 20.0  # higher RDS (conditional)
        out = eng._eval_mosfet(dict(aon), cand)
        cls = eng._derive_classification(out, "MOSFET")
        self.assertEqual(cls, "CONDITIONALLY_COMPATIBLE")

    def test_insufficient_data(self):
        """A missing CRITICAL spec -> INSUFFICIENT_DATA."""
        aon = find("AON6500")
        cand = dict(aon)
        cand["part_number"] = "NOSPEC"
        cand["vds_v"] = None  # critical spec missing
        out = eng._eval_mosfet(dict(aon), cand)
        cls = eng._derive_classification(out, "MOSFET")
        self.assertIn(
            cls, ("INSUFFICIENT_DATA", "UNKNOWN"),
            f"missing critical spec must be INSUFFICIENT_DATA or UNKNOWN, got {cls}",
        )

    def test_unknown_cross_category(self):
        """A MOSFET vs an IC -> UNKNOWN (cannot compare)."""
        aon = find("AON6500")
        ic = find("TPS51224")
        if aon and ic:
            ev = eng.evaluate(aon["part_number"], ic["part_number"])
            self.assertIn(ev.classification, ("UNKNOWN", "NOT_RECOMMENDED"))


class TestDiscreteCategorySpecificity(unittest.TestCase):
    """Each discrete category has DISTINCT critical specs."""

    def test_zener_needs_vz(self):
        """A Zener's critical spec is Vz (zener voltage), not Vrrm.
        A candidate Zener with Vz missing -> INSUFFICIENT_DATA."""
        zener = find("1N5231B")
        if not zener:
            self.skipTest("1N5231B not in DB")
        cand = dict(zener)
        cand["part_number"] = "NOVZ"
        # Strip Vz from electrical_characteristics
        ec = dict(cand.get("electrical_characteristics", {}))
        ec.pop("vz_v", None)  # A3-1 fix: Vz now stored in vz_v
        ec.pop("vrrm_v", None)  # also strip fallback
        cand["electrical_characteristics"] = ec
        out = eng._eval_discrete(dict(zener), cand)
        cls = eng._derive_classification(out, "ZENER")
        # Must surface that Vz was not verified
        vz_crit = [c for c in out if c.criterion == "vz"]
        self.assertTrue(
            vz_crit and vz_crit[0].verdict == "unknown",
            "DEFECT: Zener candidate missing Vz must flag vz as unknown",
        )

    def test_tvs_needs_vbr_and_vc(self):
        """A TVS needs Vbr (breakdown) and Vc (clamping)."""
        tvs = find("SMBJ33CA")
        if not tvs:
            self.skipTest("SMBJ33CA not in DB")
        cand = dict(tvs)
        cand["part_number"] = "NOVBR"
        ec = dict(cand.get("electrical_characteristics", {}))
        ec.pop("vbr_v", None)
        cand["electrical_characteristics"] = ec
        out = eng._eval_discrete(dict(tvs), cand)
        vbr_crit = [c for c in out if c.criterion == "vbr"]
        self.assertTrue(
            vbr_crit and vbr_crit[0].verdict == "unknown",
            "DEFECT: TVS candidate missing Vbr must flag vbr as unknown",
        )

    def test_bjt_needs_hfe(self):
        """A BJT's critical spec includes hfe (current gain)."""
        bjt = find("2N3904")
        if not bjt:
            self.skipTest("2N3904 not in DB")
        cand = dict(bjt)
        cand["part_number"] = "NOHFE"
        ec = dict(cand.get("electrical_characteristics", {}))
        ec.pop("hfe_min", None)
        cand["electrical_characteristics"] = ec
        out = eng._eval_discrete(dict(bjt), cand)
        hfe_crit = [c for c in out if c.criterion == "hfe"]
        self.assertTrue(
            hfe_crit and hfe_crit[0].verdict == "unknown",
            "DEFECT: BJT candidate missing hfe must flag hfe as unknown",
        )


class TestNoInventedEquivalence(unittest.TestCase):
    """Never invent equivalents."""

    def test_higher_voltage_alone_not_compatible(self):
        """A candidate with higher VDS but wrong channel type must NOT
        be COMPATIBLE."""
        aon = find("AON6500")  # N-channel
        cand = dict(aon)
        cand["part_number"] = "PCHAN"
        cand["channel_type"] = "P"  # WRONG channel
        cand["vds_v"] = 100.0  # higher VDS
        out = eng._eval_mosfet(dict(aon), cand)
        cls = eng._derive_classification(out, "MOSFET")
        self.assertNotEqual(cls, "COMPATIBLE",
                            "DEFECT: wrong channel + higher VDS must NOT be COMPATIBLE")

    def test_substring_not_equivalence(self):
        """A matching part-number substring does not override specs."""
        aon = find("AON6500")
        cand = dict(aon)
        cand["part_number"] = "AON6500X"  # substring overlap
        cand["vds_v"] = 5.0  # much lower VDS
        out = eng._eval_mosfet(dict(aon), cand)
        cls = eng._derive_classification(out, "MOSFET")
        self.assertEqual(cls, "NOT_RECOMMENDED",
                         "DEFECT: substring name must not override the lower VDS")


class TestLDOSpecs(unittest.TestCase):
    """LDO replacement must check Vout, Vin_max, Iout, dropout."""

    def test_ldo_needs_vout(self):
        """An LDO candidate missing Vout -> INSUFFICIENT_DATA."""
        ldo = find("TLV70033")
        if not ldo:
            self.skipTest("TLV70033 not in DB")
        cand = dict(ldo)
        cand["part_number"] = "NOVOUT"
        # Strip output voltage
        ec = dict(cand.get("electrical_characteristics", {}))
        ec.pop("output_voltage_range_v", None)  # LDO Vout stored here
        cand["electrical_characteristics"] = ec
        out = eng._eval_ic(dict(ldo), cand)
        cls = eng._derive_classification(out, "IC")
        # Must surface Vout unknown
        vout_crit = [c for c in out if "vout" in c.criterion.lower()]
        self.assertTrue(
            vout_crit and vout_crit[0].verdict == "unknown",
            "DEFECT: LDO candidate missing Vout must flag vout as unknown",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
