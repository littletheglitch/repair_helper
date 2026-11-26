#!/usr/bin/env python3
"""Independent validation suite (Priority 5).

Exercises the engines across UNSEEN measurement combinations, simultaneous
faults, missing/conflicting evidence, boundary values, invalid records,
similar names/different roles, same parts/different netlists, and cases
where UNKNOWN is the correct answer.

All tests are SYNTHETIC — no real hardware. The goal is to verify the
engines reason correctly over the new P0-P4 machinery:
  - three-state fact semantics (P0)
  - topology-driven power-supply workflows (P1)
  - graph-based netlist recognition (P2)
  - per-category replacement classification (P3)
  - ranked knowledge retrieval (P4)

This suite is INDEPENDENT: it does not reuse the adversarial tests from
P0-P4. It uses fresh scenarios designed to break assumptions.
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from DiagnosticEngines.core.engine import load_engine, Measurement, DiagnosticEngine
from DiagnosticEngines.PowerSupply import PowerSupplyEngine, PowerSupplyDiagnosisInput
from DiagnosticEngines.CircuitAnalysis.netlist import Netlist
from DiagnosticEngines.CircuitAnalysis import CircuitAnalysisEngine
from DiagnosticEngines.core.replacement_engine import ReplacementEngine
from DiagnosticEngines.core.component_db import ComponentDatabase
from DiagnosticEngines.core.framework import (
    Measurement as FwMeasurement, MeasurementKind, MeasurementState,
    MeasurementCondition,
)


def M(name, value, measured=True):
    return Measurement(name=name, value=value, measured=measured)


def V(name, val):
    return FwMeasurement(name=name, kind=MeasurementKind.VOLTAGE,
                         state=MeasurementState.IN_RANGE, value=val,
                         condition=MeasurementCondition.POWERED)


def ZERO(name):
    return FwMeasurement(name=name, kind=MeasurementKind.VOLTAGE,
                         state=MeasurementState.MEASURED_ZERO, value=0.0,
                         condition=MeasurementCondition.POWERED)


def PRES(name):
    return FwMeasurement(name=name, kind=MeasurementKind.LOGIC_LEVEL,
                         state=MeasurementState.IN_RANGE, value=True,
                         condition=MeasurementCondition.POWERED)


def ABS(name):
    return FwMeasurement(name=name, kind=MeasurementKind.LOGIC_LEVEL,
                         state=MeasurementState.MEASURED_ZERO, value=0.0,
                         condition=MeasurementCondition.POWERED)


def pin(net, fn, d):
    return {"net": net, "function": fn, "direction": d}


# =========================================================================
# P0: three-state semantics across unseen combinations
# =========================================================================

class TestP0ThreeStateUnseenCombos(unittest.TestCase):
    """Unseen measurement combinations exercising three-state semantics."""

    @classmethod
    def setUpClass(cls):
        cls.eng = load_engine()

    def test_zero_rail_with_all_upstream_good_no_short_measured(self):
        """Rail zero, VIN+VCC+EN+switching all good, rail_short NOT
        measured. The engine must localize (output-stage fault) AND
        surface the 'short not ruled out' caveat — it must NOT silently
        assume no short."""
        r = self.eng.diagnose("+3VALW", [
            M("rail_voltage", 0.0), M("vin", 19.0), M("vcc", 5.0),
            M("en", True), M("switching", True),
        ])
        self.assertEqual(r.fired_rule_id, "RULE_FAULT_RAIL_DEAD_WITH_SWITCHING")
        caveat = [n for n in r.notes if "NOT measured" in n or "CAVEAT" in n]
        self.assertTrue(caveat, "DEFECT: must caveat that rail_short was not measured")

    def test_measured_no_short_no_caveat(self):
        """When rail_short=False is MEASURED, the caveat must NOT appear
        (the technician confirmed no short)."""
        r = self.eng.diagnose("+3VALW", [
            M("rail_voltage", 0.0), M("vin", 19.0), M("vcc", 5.0),
            M("en", True), M("switching", True), M("rail_short", False),
        ])
        caveat = [n for n in r.notes if "NOT measured" in n or "CAVEAT" in n]
        self.assertFalse(caveat, "DEFECT: caveat must NOT appear when rail_short=False measured")

    def test_conflicting_voltage_readings_surface_conflict(self):
        """Two rail_voltage readings (3.3V and 0V) -> CONFLICTING_EVIDENCE."""
        r = self.eng.diagnose("+3VALW", [
            M("rail_voltage", 3.3), M("rail_voltage", 0.0),
        ])
        conflict = [n for n in r.notes if "CONFLICTING" in n]
        self.assertTrue(conflict, "DEFECT: conflicting readings must surface CONFLICTING_EVIDENCE")

    def test_unknown_rail_family_returns_need_data(self):
        """An unknown rail family -> requires_info (not a crash)."""
        r = self.eng.diagnose("+BOGUS_RAIL", [M("rail_voltage", 3.3)])
        self.assertTrue(r.requires_info)
        self.assertIsNone(r.fired_rule_id)


# =========================================================================
# P1: topology-specific combinations
# =========================================================================

class TestP1TopologySpecificCombos(unittest.TestCase):
    """Topology-specific measurement combinations."""

    @classmethod
    def setUpClass(cls):
        cls.eng = PowerSupplyEngine()

    def test_llc_resonant_tank_failure_localizes_to_resonant_stage(self):
        """An LLC with input good but resonant_freq wrong must reference
        the resonant_tank stage (LLC-unique). LLC is mains, so the
        safety gate blocks primary-side probing — but the resonant_tank
        fault is flagged in the fault-pattern notes (it manifests on
        the secondary side via output shape/ripple). The engine must
        NOT silently treat it as a generic no-output."""
        r = self.eng.diagnose(PowerSupplyDiagnosisInput(
            topology_id="PSUP_llc", symptom="no_output",
            measurements=[V("input_voltage", 380), ZERO("output_voltage"),
                          PRES("switching"), ABS("resonant_freq")]))
        # The LLC fault pattern explicitly lists resonant_tank as a
        # likely stage (distinctive to LLC). Verify it appears in notes.
        all_text = " ".join(r.notes) + " " + (r.rule_id or "")
        self.assertIn(
            "resonant", all_text.lower(),
            f"DEFECT: LLC must reference resonant_tank (LLC-unique stage), "
            f"got rule_id={r.rule_id} notes={r.notes[:2]}",
        )

    def test_charger_adapter_sense_failure_localizes_to_adapter_sense(self):
        """A charger with ACIN absent must localize to adapter_sense
        (charger-unique stage)."""
        r = self.eng.diagnose(PowerSupplyDiagnosisInput(
            topology_id="PSUP_battery_charger", symptom="no_charge",
            measurements=[V("input_voltage", 19.0), ABS("acin")]))
        self.assertIn("adapter_sense", r.rule_id.lower(),
                      f"DEFECT: charger must localize to adapter_sense, got {r.rule_id}")

    def test_atx_standby_failure_localizes_to_standby_supply(self):
        """An ATX with 5VSB absent must localize to standby_supply."""
        r = self.eng.diagnose(PowerSupplyDiagnosisInput(
            topology_id="PSUP_atx", symptom="no_output",
            measurements=[ZERO("vsb_voltage")]))
        # ATX is mains -> safety gate first; the conclusion should still
        # reference the standby stage in notes or rule_id.
        self.assertTrue(
            any("standby" in n.lower() or "5vsb" in n.lower() for n in r.notes)
            or "standby" in r.rule_id.lower(),
            f"DEFECT: ATX must reference standby, got {r.rule_id} / {r.notes[:1]}",
        )


# =========================================================================
# P2: same parts / different netlists
# =========================================================================

class TestP2SamePartsDifferentNetlists(unittest.TestCase):
    """The headline P2 case: same components, different connectivity."""

    def _ctrl(self, sw_net):
        return {"refdes": "U1", "part": "ctrl", "pins": {
            "VIN": pin("VIN", "power_in", "in"),
            "SW": pin(sw_net, "switch_output", "out"),
            "FB": pin("FB", "feedback", "in"),
            "GND": pin("GND", "ground", "in")}}

    def test_buck_vs_boost_same_parts(self):
        """Same U1+L1+D1, but L1/D1 wired differently -> buck vs boost."""
        # Buck: L1 from SW to VOUT; D1 from GND to SW (recirculation)
        buck = Netlist.from_dict({
            "components": [self._ctrl("SW"),
                {"refdes": "L1", "part": "inductor", "pins": {
                    "1": pin("SW", "passive", "passive"),
                    "2": pin("VOUT", "passive", "passive")}},
                {"refdes": "D1", "part": "diode", "pins": {
                    "A": pin("GND", "passive", "passive"),
                    "K": pin("SW", "passive", "passive")}}],
            "nets": ["VIN", "SW", "FB", "GND", "VOUT"]})
        # Boost: L1 from VIN to SW; D1 from SW to VOUT (series)
        boost = Netlist.from_dict({
            "components": [self._ctrl("SW"),
                {"refdes": "L1", "part": "inductor", "pins": {
                    "1": pin("VIN", "passive", "passive"),
                    "2": pin("SW", "passive", "passive")}},
                {"refdes": "D1", "part": "diode", "pins": {
                    "A": pin("SW", "passive", "passive"),
                    "K": pin("VOUT", "passive", "passive")}}],
            "nets": ["VIN", "SW", "FB", "GND", "VOUT"]})
        self.assertEqual(buck.recognize_structure(), "buck")
        self.assertEqual(boost.recognize_structure(), "boost")
        self.assertNotEqual(buck.recognize_structure(), boost.recognize_structure())

    def test_unrecognized_structure_is_unknown(self):
        """A valid but unrecognized netlist -> 'unknown' (not a guess)."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "mystery", "pins": {
                    "A": pin("N1", "passive", "passive"),
                    "B": pin("N2", "passive", "passive")}}],
            "nets": ["N1", "N2"]})
        self.assertEqual(nl.recognize_structure(), "unknown")

    def test_floating_net_detected(self):
        """A net with one connection -> floating warning."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "LDO", "pins": {
                    "IN": pin("VIN", "power_in", "in"),
                    "OUT": pin("VOUT", "power_out", "out"),
                    "GND": pin("GND", "ground", "in")}}],
            "nets": ["VIN", "VOUT", "GND"]})
        issues = nl.validate()
        self.assertTrue(any(i.kind == "floating" for i in issues))


# =========================================================================
# P3: invalid records, similar names / different roles
# =========================================================================

class TestP3InvalidRecordsAndSimilarNames(unittest.TestCase):
    """Invalid component records + similar names/different roles."""

    @classmethod
    def setUpClass(cls):
        cls.eng = ReplacementEngine()
        cls.db = ComponentDatabase.load(PACK)
        cls.aon = cls.db.find_part("AON6500")

    def test_record_missing_all_specs_is_insufficient_data(self):
        """A candidate with NO specs at all -> INSUFFICIENT_DATA."""
        cand = {"part_number": "EMPTYSPEC", "channel_type": "N"}
        out = self.eng._eval_mosfet(dict(self.aon), cand)
        cls = self.eng._derive_classification(out, "MOSFET")
        self.assertEqual(cls, "INSUFFICIENT_DATA",
                         f"DEFECT: empty-spec record must be INSUFFICIENT_DATA, got {cls}")

    def test_similar_name_different_category_is_not_recommended(self):
        """A part with a similar name but different category (e.g. a
        'AON6500X' that's actually a P-channel) must NOT be COMPATIBLE."""
        cand = dict(self.aon)
        cand["part_number"] = "AON6500X"
        cand["channel_type"] = "P"  # WRONG channel (different role)
        out = self.eng._eval_mosfet(dict(self.aon), cand)
        cls = self.eng._derive_classification(out, "MOSFET")
        self.assertEqual(cls, "NOT_RECOMMENDED",
                         f"DEFECT: similar name + wrong channel must be NOT_RECOMMENDED, got {cls}")

    def test_bjt_vs_mosfet_cross_category_is_unknown(self):
        """A BJT cannot replace a MOSFET -> UNKNOWN (cross-category)."""
        bjt = self.db.find_part("2N3904")
        if bjt and self.aon:
            ev = self.eng.evaluate(self.aon["part_number"], bjt["part_number"])
            self.assertEqual(ev.classification, "UNKNOWN",
                             f"DEFECT: BJT vs MOSFET must be UNKNOWN, got {ev.classification}")

    def test_boundary_vds_exact_match_is_compatible(self):
        """A candidate with VDS EXACTLY equal to original (boundary) is
        a match, not a mismatch."""
        cand = dict(self.aon)
        cand["part_number"] = "EXACTVDS"
        cand["vds_v"] = self.aon["vds_v"]  # exact boundary
        out = self.eng._eval_mosfet(dict(self.aon), cand)
        vds_crit = [c for c in out if c.criterion == "vds"][0]
        self.assertEqual(vds_crit.verdict, "match",
                         "DEFECT: exact VDS boundary must be match")

    def test_zener_wrong_vz_is_not_recommended(self):
        """A Zener with a different Vz is NOT_RECOMMENDED (a 5.1V Zener
        is not a substitute for a 12V Zener — different reference)."""
        zener = self.db.find_part("1N5231B")  # 5.1V
        if not zener:
            self.skipTest("1N5231B not in DB")
        cand = dict(zener)
        cand["part_number"] = "12VZENER"
        ec = dict(cand.get("electrical_characteristics", {}))
        ec["vz_v"] = 12.0  # A3-1 fix: Vz now in vz_v (12V vs 5.1V)
        ec["vrrm_v"] = 12.0  # keep vrrm_v consistent
        cand["electrical_characteristics"] = ec
        out = self.eng._eval_discrete(dict(zener), cand)
        cls = self.eng._derive_classification(out, "ZENER")
        self.assertEqual(cls, "NOT_RECOMMENDED",
                         f"DEFECT: Zener with different Vz must be NOT_RECOMMENDED, got {cls}")


# =========================================================================
# P4: knowledge retrieval edge cases
# =========================================================================

class TestP4KnowledgeEdgeCases(unittest.TestCase):
    """Knowledge retrieval edge cases."""

    @classmethod
    def setUpClass(cls):
        from DiagnosticEngines.core.knowledge_platform import (
            KnowledgeStore, KnowledgeRetriever, KnowledgeItem)
        cls.store = KnowledgeStore()
        cls.store.add(KnowledgeItem(
            title="Test verified item", content_text="buck converter design",
            source_author="TI", verification_status="verified", category="datasheet"))
        cls.store.add(KnowledgeItem(
            title="Test unverified item", content_text="buck converter rumor",
            source_author="anon", verification_status="unverified", category="forum_post"))
        cls.retriever = KnowledgeRetriever(cls.store)

    def test_verified_only_filter(self):
        """verified_only=True returns only verified items."""
        results = self.retriever.search("buck converter", verified_only=True)
        mine = [r for r in results if r.item.title.startswith("Test")]
        self.assertTrue(all(r.item.verification_status == "verified" for r in mine))
        self.assertTrue(any(r.item.title == "Test verified item" for r in mine))

    def test_empty_query_returns_empty(self):
        """An empty query returns no results (not all items)."""
        results = self.retriever.search("")
        self.assertEqual(results, [])

    def test_no_token_overlap_returns_empty(self):
        """A query with no token overlap returns empty."""
        results = self.retriever.search("zzzzz_nonsense_zzzzz")
        # only our test items (no overlap)
        mine = [r for r in results if r.item.title.startswith("Test")]
        self.assertEqual(mine, [])


# =========================================================================
# UNKNOWN-correct cases (the engine must say UNKNOWN when appropriate)
# =========================================================================

class TestUnknownCorrectCases(unittest.TestCase):
    """Cases where UNKNOWN / NEED_DATA is the CORRECT answer."""

    def test_unknown_circuit_family_is_need_data(self):
        """An unrecognized circuit family -> requires_info."""
        eng = CircuitAnalysisEngine()
        from DiagnosticEngines.CircuitAnalysis import CircuitAnalysisInput
        r = eng.analyze(CircuitAnalysisInput(circuit_family="CCT_bogus"))
        self.assertTrue(r.requires_info)

    def test_unknown_topology_recognition_is_unknown(self):
        """An unrecognized netlist structure -> 'unknown'."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "X1", "part": "mystery", "pins": {
                    "1": pin("N1", "passive", "passive")}}],
            "nets": ["N1"]})
        self.assertEqual(nl.recognize_structure(), "unknown")

    def test_empty_netlist_is_need_data(self):
        """An empty netlist -> NEED_DATA, not a crash."""
        nl = Netlist.from_dict({"components": [], "nets": []})
        issues = nl.validate()
        self.assertTrue(any(i.kind == "empty" for i in issues))


if __name__ == "__main__":
    unittest.main(verbosity=2)
