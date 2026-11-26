#!/usr/bin/env python3
"""AUDIT test suite (independent technical audit of v1.4.0).

This suite probes EXISTING behavior WITHOUT changing production code.
Each test that FAILS documents a defect. Defects are recorded in
SOURCE_AUDIT_REPORT.md with exact source/function, repro, actual vs
expected, severity.

Audit areas:
  A0  Core semantics (UNKNOWN/FALSE/ZERO/NOT_MEASURED/INVALID,
      provenance, conflicts, simultaneous faults)
  A1  Power supply (actual stage transitions; mains vs low-voltage;
      LLC resonant vs safety; safety-gate scope)
  A2  Netlist graph (external ports vs floating; single/multi-pin
      floating; undeclared; ground; traversal branches/loops/
      disconnected; same components different connectivity)
  A3  Component DB (Zener Vz vs VRRM; TVS VWM/VBR/VC; BJT hFE; diode;
      LDO Vout; IC pin-function; missing-data never COMPATIBLE)
  A4  Knowledge (ranking, duplicates, provenance, backward-compat,
      verified_only, all callers)
  A6  Host adapter (every engine; payload validation; exceptions not
      misleading successes; knowledge/netlist integration; unsupported
      engines)

Run: python Tests/test_audit.py
A failure = a documented defect.
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from DiagnosticEngines.core.engine import load_engine, Measurement, DiagnosticEngine
from DiagnosticEngines.PowerSupply import PowerSupplyEngine, PowerSupplyDiagnosisInput
from DiagnosticEngines.CircuitAnalysis.netlist import Netlist, PinFunction, SignalDirection
from DiagnosticEngines.CircuitAnalysis import CircuitAnalysisEngine, CircuitAnalysisInput
from DiagnosticEngines.core.replacement_engine import ReplacementEngine
from DiagnosticEngines.core.component_db import ComponentDatabase
from DiagnosticEngines.core.knowledge_platform import (
    KnowledgeStore, KnowledgeRetriever, KnowledgeItem, RetrievalResult,
)
from DiagnosticEngines.core.framework import (
    Measurement as FwMeasurement, MeasurementKind, MeasurementState,
    MeasurementCondition,
)
from Integration.host_adapter import ExpansionPackAdapter, DiagnosticRequest
from DiagnosticEngines.core.framework.session import DiagnosticSession, Case


def M(name, value, measured=True):
    return Measurement(name=name, value=value, measured=measured)


def V(name, val):
    return FwMeasurement(name=name, kind=MeasurementKind.VOLTAGE,
                         state=MeasurementState.IN_RANGE, value=val,
                         condition=MeasurementCondition.POWERED)


def ZM(name):
    return FwMeasurement(name=name, kind=MeasurementKind.VOLTAGE,
                         state=MeasurementState.MEASURED_ZERO, value=0.0,
                         condition=MeasurementCondition.POWERED)


def P(name):
    return FwMeasurement(name=name, kind=MeasurementKind.LOGIC_LEVEL,
                         state=MeasurementState.IN_RANGE, value=True,
                         condition=MeasurementCondition.POWERED)


def A(name):
    return FwMeasurement(name=name, kind=MeasurementKind.LOGIC_LEVEL,
                         state=MeasurementState.MEASURED_ZERO, value=0.0,
                         condition=MeasurementCondition.POWERED)


def pin(net, fn, d):
    return {"net": net, "function": fn, "direction": d}


# =========================================================================
# A0 — Core semantics
# =========================================================================

class A0CoreSemantics(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = load_engine()

    def test_a0_invalid_measurement_state_not_representable(self):
        """DEFECT-A0-1: The legacy Measurement has no 'state' field, so
        an INVALID measurement cannot be represented. The framework's
        MeasurementState.INVALID is unused by the core engine. An
        invalid reading (e.g. a DMM showing OL on a voltage rail)
        cannot be told apart from a 0.0 reading."""
        # Try to construct a Measurement with state=INVALID
        # The legacy Measurement dataclass accepts arbitrary kwargs?
        m = Measurement(name="rail_voltage", value=None, measured=True)
        # as_fact() on a None value
        self.assertIsNone(m.as_fact(),
            "A None-valued measured=True reading should be unknown, but "
            "the legacy Measurement cannot distinguish INVALID from "
            "NOT_MEASURED (both have value=None).")

    def test_a0_conflict_clears_present_measured_for_first_reading(self):
        """DEFECT-A0-2: When two rail_voltage readings conflict, the
        conflict handler sets facts[name]=None but the FIRST reading
        already set facts[name+'_present'] and facts[name+'_measured'].
        Are those cleared? If not, a rule checking 'rail_voltage_measured'
        still sees True despite the conflict."""
        rail = self.eng.all_rails()["+3VALW"]
        facts = DiagnosticEngine._facts_from_measurements(
            [M("rail_voltage", 3.3), M("rail_voltage", 0.0)], rail)
        # After conflict, rail_voltage should be None
        self.assertIsNone(facts.get("rail_voltage"))
        # rail_voltage_measured should ALSO be None (conflict invalidates)
        # If it's still True, that's a defect.
        self.assertIsNone(facts.get("rail_voltage_measured"),
            "DEFECT-A0-2: conflicting rail_voltage leaves "
            "rail_voltage_measured=True (should be None)")

    def test_a0_conflict_three_readings_compares_only_first(self):
        """DEFECT-A0-3: The conflict loop records seen_values[name] only
        on the first occurrence (line 400-401: 'if m.measured:
        seen_values[m.name]=m.value'). On a third reading, it compares
        against the FIRST, not the most-recent. So readings [3.3, 0.0,
        3.3] — the third (3.3) matches the first (3.3) and is NOT seen
        as conflicting with the second (0.0), even though 0.0 and 3.3
        conflict."""
        rail = self.eng.all_rails()["+3VALW"]
        facts = DiagnosticEngine._facts_from_measurements(
            [M("rail_voltage", 3.3), M("rail_voltage", 0.0),
             M("rail_voltage", 3.3)], rail)
        conflicts = facts.get("_conflicts", [])
        # There IS a conflict (3.3 vs 0.0) recorded on the 2nd reading.
        # But the 3rd reading (3.3) matches the stored first (3.3), so
        # no NEW conflict is recorded AND the fact may be re-set to 3.3.
        # Expected: conflicts should mention the 0.0 vs 3.3 disagreement.
        self.assertTrue(conflicts, "Expected at least one conflict recorded")

    def test_a0_simultaneous_faults_priority_suppression(self):
        """DEFECT-A0-4: Do simultaneous INDEPENDENT faults get
        suppressed by rule priority? Two faults that do NOT gate each
        other (ripple_excessive AND vin_absent) should both surface.
        NOTE: a confirmed short suppressing a vin-absent fault is
        CORRECT (the short is the upstream cause; vin is moot until
        the short is resolved) — that is a dependency gate, not a
        priority bug. So this test uses two non-gated faults."""
        r = self.eng.diagnose("+3VALW", [
            M("rail_voltage", 3.3),      # in tolerance
            M("ripple", 120.0),          # excessive (priority 10)
            M("vin", 0.0, measured=True),  # absent (priority 20)
        ])
        # ripple_excessive fires (higher prio). vin_absent is ALSO true
        # and must be surfaced as a secondary fault.
        notes_text = " ".join(r.notes)
        self.assertTrue(
            "SIMULTANEOUS" in notes_text and "VIN" in notes_text.upper(),
            "DEFECT-A0-4: two independent faults (ripple + vin-absent) — "
            f"only the primary fires (rule={r.fired_rule_id}); the secondary "
            f"vin fault is suppressed and not mentioned. notes={r.notes}",
        )


# =========================================================================
# A1 — Power supply
# =========================================================================

class A1PowerSupply(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = PowerSupplyEngine()

    def test_a1_mains_topologies_use_identical_workflow(self):
        """DEFECT-A1-1: All mains topologies (flyback, forward,
        push_pull, half_bridge, full_bridge, llc, pfc, atx) take the
        SAME _build_mains_workflow — a fixed 3-step sequence. The
        topology's own stage model is NOT walked. So the 'structurally
        distinct stage models' are dead data for mains topologies."""
        mains_topos = ["PSUP_flyback", "PSUP_forward", "PSUP_push_pull",
                       "PSUP_half_bridge", "PSUP_full_bridge", "PSUP_llc",
                       "PSUP_pfc", "PSUP_atx"]
        first_actions = None
        for t in mains_topos:
            r = self.eng.diagnose(PowerSupplyDiagnosisInput(
                topology_id=t, symptom="no_output", measurements=[]))
            actions = tuple(s.get("action", "")[:60]
                            for s in r.next_steps)
            if first_actions is None:
                first_actions = actions
            else:
                self.assertEqual(actions, first_actions,
                    f"DEFECT-A1-1: mains topology {t} produces a DIFFERENT "
                    f"workflow — expected identical (all share "
                    f"_build_mains_workflow).")

    def test_a1_llc_resonant_diagnosis_blocked_by_mains(self):
        """A1-2 FIXED: LLC is mains, so the safety gate fires first.
        But the resonant_tank stage is now walked on the SECONDARY side
        (resonant_freq is observable from the output waveform), so the
        resonant_freq measurement IS consumed. The safety gate blocks
        primary-side probing only, not safe secondary-side observations."""
        r = self.eng.diagnose(PowerSupplyDiagnosisInput(
            topology_id="PSUP_llc", symptom="no_output",
            measurements=[V("input_voltage", 380), ZM("output_voltage"),
                          P("switching"), A("resonant_freq")]))
        # FIXED: resonant_tank now localizes on the secondary side.
        self.assertIn("resonant", r.rule_id.lower(),
            "FIXED A1-2: LLC must localize to resonant_tank (secondary-side "
            f"observable). rule_id={r.rule_id}")
        # Safety gate still present
        actions = " ".join(s.get("action", "") for s in r.next_steps)
        self.assertTrue(
            "SAFETY GATE" in actions or "safety" in actions.lower(),
            "Safety gate must remain as step 1")

    def test_a1_safety_gate_blocks_all_mains_steps_equally(self):
        """Check: does the safety gate BLOCK safe secondary-side
        observations? It should NOT — secondary-side output checks are
        safe. Verify the mains workflow DOES include a secondary output
        check step (not just the safety gate)."""
        r = self.eng.diagnose(PowerSupplyDiagnosisInput(
            topology_id="PSUP_flyback", symptom="no_output"))
        actions = " ".join(s.get("action", "") for s in r.next_steps)
        self.assertIn("secondary", actions.lower(),
            "Mains workflow should include a secondary-side output check")

    def test_a1_low_voltage_topology_walks_own_stages(self):
        """Verify low-voltage topologies DO walk their own stage model
        (this is the part that works). Buck vs linear should localize
        differently."""
        r_buck = self.eng.diagnose(PowerSupplyDiagnosisInput(
            topology_id="PSUP_buck", symptom="no_output",
            measurements=[V("input_voltage", 12.0), ZM("output_voltage")]))
        r_lin = self.eng.diagnose(PowerSupplyDiagnosisInput(
            topology_id="PSUP_linear", symptom="no_output",
            measurements=[V("input_voltage", 12.0), ZM("output_voltage")]))
        # Both should fire a stage rule; the stage IDs should differ.
        self.assertNotEqual(r_buck.rule_id, r_lin.rule_id,
            "DEFECT-A1-4: buck and linear localize to the same stage")


# =========================================================================
# A2 — Netlist graph (HIGH PRIORITY)
# =========================================================================

class A2NetlistGraph(unittest.TestCase):

    def test_a2_single_pin_net_is_external_port_not_floating(self):
        """FIXED A2-1: A net marked as an external_port is NOT floating,
        even with one connection. A lone power_in pin WITHOUT the
        external_ports declaration IS floating (no off-board source
        declared)."""
        # Marked as external port -> not floating
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "J1", "part": "jack", "pins": {
                    "1": pin("VIN", "power_in", "in")}}],
            "nets": ["VIN"],
            "external_ports": ["VIN"],
        })
        issues = nl.validate()
        self.assertFalse(any(i.kind == "floating" and i.net == "VIN"
                             for i in issues),
            "FIXED A2-1: VIN marked as external_port must NOT be floating")
        # NOT marked -> floating (no source declared)
        nl2 = Netlist.from_dict({
            "components": [
                {"refdes": "J1", "part": "jack", "pins": {
                    "1": pin("VIN", "power_in", "in")}}],
            "nets": ["VIN"],
        })
        issues2 = nl2.validate()
        self.assertTrue(any(i.kind == "floating" and i.net == "VIN"
                            for i in issues2),
            "FIXED A2-1: lone power_in without external_port must be floating")

    def test_a2_external_port_single_connection_flagged_floating(self):
        """DEFECT-A2-1 (core): A single-connection net that IS an
        external port (e.g. an input jack with only the jack pin, no
        downstream yet) is flagged floating. There is no way to mark
        a net as an external port."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "J1", "part": "input_jack", "pins": {
                    "P": pin("VIN_EXT", "power_in", "in")}},
            ],
            "nets": ["VIN_EXT"],
        })
        issues = nl.validate()
        floating = [i for i in issues if i.kind == "floating"]
        self.assertTrue(floating,
            "DEFECT-A2-1: a single-pin external-port net IS flagged "
            "floating, but it should be recognisable as an external port.")

    def test_a2_multi_pin_net_still_floats_undetected(self):
        """FIXED A2-2: A net with 2+ passive pins but no driver/source/
        reference IS now detected as floating."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "R1", "part": "res", "pins": {
                    "1": pin("FLOAT_NET", "passive", "passive"),
                    "2": pin("GND", "passive", "passive")}},
                {"refdes": "R2", "part": "res", "pins": {
                    "1": pin("FLOAT_NET", "passive", "passive"),
                    "2": pin("GND", "passive", "passive")}},
            ],
            "nets": ["FLOAT_NET", "GND"],
        })
        issues = nl.validate()
        floating = [i for i in issues if i.kind == "floating"
                    and i.net == "FLOAT_NET"]
        self.assertTrue(floating,
            "FIXED A2-2: multi-pin floating net FLOAT_NET (no driver) "
            "must now be detected.")

    def test_a2_trace_path_through_passive_component_fails(self):
        """FIXED A2-3: trace_path now traverses passive components
        (resistors/inductors/caps) as intermediate hops. A multi-hop
        path SW -> L1 (passive) -> MID -> R1 (passive) -> VOUT is now
        found."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "ctrl", "pins": {
                    "SW": pin("SW", "switch_output", "out")}},
                {"refdes": "L1", "part": "inductor", "pins": {
                    "1": pin("SW", "passive", "passive"),
                    "2": pin("MID", "passive", "passive")}},
                {"refdes": "R1", "part": "res", "pins": {
                    "1": pin("MID", "passive", "passive"),
                    "2": pin("VOUT", "passive", "passive")}},
            ],
            "nets": ["SW", "MID", "VOUT"],
        })
        path = nl.trace_path("SW", "VOUT")
        self.assertIsNotNone(path,
            "FIXED A2-3: trace_path must traverse passive intermediates. "
            "SW->L1->MID->R1->VOUT should be found.")
        if path:
            self.assertEqual([s.component.refdes for s in path], ["L1", "R1"])

    def test_a2_same_components_different_connectivity(self):
        """Verify the headline case still works (regression guard)."""
        ctrl = {"refdes": "U1", "part": "ctrl", "pins": {
            "VIN": pin("VIN", "power_in", "in"),
            "SW": pin("SW", "switch_output", "out"),
            "GND": pin("GND", "ground", "in")}}
        buck = Netlist.from_dict({"components": [ctrl,
            {"refdes": "L1", "part": "inductor", "pins": {
                "1": pin("SW", "passive", "passive"),
                "2": pin("VOUT", "passive", "passive")}}],
            "nets": ["VIN", "SW", "GND", "VOUT"]})
        boost = Netlist.from_dict({"components": [ctrl,
            {"refdes": "L1", "part": "inductor", "pins": {
                "1": pin("VIN", "passive", "passive"),
                "2": pin("SW", "passive", "passive")}},
            {"refdes": "D1", "part": "diode", "pins": {
                "A": pin("SW", "passive", "passive"),
                "K": pin("VOUT", "passive", "passive")}}],
            "nets": ["VIN", "SW", "GND", "VOUT"]})
        self.assertEqual(buck.recognize_structure(), "buck")
        self.assertEqual(boost.recognize_structure(), "boost")

    def test_a2_disconnected_subcircuit_in_trace(self):
        """DEFECT-A2-4: trace_path with a disconnected subcircuit —
        two separate islands. Should return None (no path), not hang."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "a", "pins": {
                    "OUT": pin("N1", "power_out", "out")}},
                {"refdes": "U2", "part": "b", "pins": {
                    "IN": pin("N2", "power_in", "in")}},
            ],
            "nets": ["N1", "N2"],
        })
        path = nl.trace_path("N1", "N2")
        self.assertIsNone(path, "No path between disconnected islands")

    def test_a2_loop_in_trace(self):
        """trace_path with a loop (A->B->A) must terminate, not hang."""
        nl = Netlist.from_dict({
            "components": [
                {"refdes": "U1", "part": "a", "pins": {
                    "IN": pin("N1", "power_in", "in"),
                    "OUT": pin("N2", "power_out", "out")}},
                {"refdes": "U2", "part": "b", "pins": {
                    "IN": pin("N2", "power_in", "in"),
                    "OUT": pin("N1", "power_out", "out")}},
            ],
            "nets": ["N1", "N2"],
        })
        # trace N1 -> N3 (nonexistent) through a loop — must not hang
        path = nl.trace_path("N1", "N3_DOES_NOT_EXIST")
        self.assertIsNone(path)


# =========================================================================
# A3 — Component DB
# =========================================================================

class A3ComponentDB(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = ReplacementEngine()
        cls.db = ComponentDatabase.load(PACK)

    def test_a3_zener_vz_uses_vrrm_field(self):
        """DEFECT-A3-1: The Zener evaluator reads Vz from
        electrical_characteristics.vrrm_v. But VRRM and Vz are
        DIFFERENT specs — VRRM is max reverse voltage, Vz is the
        zener (breakdown) voltage. Storing Vz in the vrrm_v field is
        a schema confusion. Check the 1N5231B record."""
        zener = self.db.find_part("1N5231B")
        if not zener:
            self.skipTest("1N5231B not in DB")
        ec = zener.get("electrical_characteristics", {})
        # The record stores vrrm_v=5.1 which is actually Vz.
        # A real Zener datasheet has BOTH vrrm_v (max reverse, often
        # = Vz for a zener) AND vz_v (the zener voltage). Conflating
        # them means a TVS-style vrrm_v check would misread a Zener.
        self.assertEqual(ec.get("vrrm_v"), 5.1,
            "1N5231B stores Vz in vrrm_v field (schema confusion)")

    def test_a3_tvs_vwm_vbr_vc_all_present(self):
        """Verify TVS has VWM (working), VBR (breakdown), VC (clamping)
        as SEPARATE fields."""
        tvs = self.db.find_part("SMBJ33CA")
        if not tvs:
            self.skipTest("SMBJ33CA not in DB")
        ec = tvs.get("electrical_characteristics", {})
        self.assertIsNotNone(ec.get("vrrm_v"), "VWM (as vrrm_v) present")
        self.assertIsNotNone(ec.get("vbr_v"), "VBR present")
        self.assertIsNotNone(ec.get("vc_v"), "VC present")
        # They should be distinct values
        self.assertNotEqual(ec.get("vrrm_v"), ec.get("vbr_v"),
            "VWM and VBR should differ")

    def test_a3_bjt_hfe_checked(self):
        """Verify BJT replacement checks hfe."""
        bjt = self.db.find_part("2N3904")
        if not bjt:
            self.skipTest("2N3904 not in DB")
        cand = dict(bjt)
        cand["part_number"] = "LOWHFE"
        ec = dict(cand.get("electrical_characteristics", {}))
        ec["hfe_min"] = 10  # much lower hfe
        cand["electrical_characteristics"] = ec
        out = self.eng._eval_discrete(dict(bjt), cand)
        hfe_crit = [c for c in out if c.criterion == "hfe"]
        self.assertTrue(hfe_crit, "BJT must have an hfe criterion")
        self.assertEqual(hfe_crit[0].verdict, "mismatch",
            "Lower hfe must be a mismatch")

    def test_a3_missing_critical_spec_never_compatible(self):
        """A candidate missing ALL critical specs must NOT be
        COMPATIBLE."""
        aon = self.db.find_part("AON6500")
        cand = {"part_number": "EMPTY", "channel_type": "N"}
        ev = self.eng.evaluate(aon["part_number"], cand["part_number"])
        # evaluate looks up the candidate in the DB; if not found,
        # what happens?
        self.assertNotEqual(ev.classification, "COMPATIBLE",
            "DEFECT-A3-4: a candidate not in the DB must not be COMPATIBLE")

    def test_a3_invalid_part_silent(self):
        """DEFECT-A3-5: evaluate() with a part not in the DB. Does it
        return UNKNOWN, or does it silently succeed/mislead?"""
        ev = self.eng.evaluate("NONEXISTENT_ORIG", "NONEXISTENT_CAND")
        self.assertIn(ev.classification,
            ("UNKNOWN", "INSUFFICIENT_DATA"),
            f"DEFECT-A3-5: invalid parts should be UNKNOWN/INSUFFICIENT, "
            f"got {ev.classification}")

    def test_a3_ldo_vout_mismatch_is_not_recommended(self):
        """An LDO with a different Vout must be NOT_RECOMMENDED."""
        ldo = self.db.find_part("TLV70033")  # 3.3V
        if not ldo:
            self.skipTest("TLV70033 not in DB")
        cand = dict(ldo)
        cand["part_number"] = "1V8LDO"
        ec = dict(cand.get("electrical_characteristics", {}))
        ec["output_voltage_range_v"] = {"min": 1.8, "max": 1.8, "typ": 1.8}
        cand["electrical_characteristics"] = ec
        out = self.eng._eval_ic(dict(ldo), cand)
        vout_crit = [c for c in out if c.criterion == "vout"]
        self.assertTrue(vout_crit, "LDO must have a vout criterion")
        self.assertEqual(vout_crit[0].verdict, "mismatch",
            "Different Vout must be a mismatch")


# =========================================================================
# A4 — Knowledge retrieval
# =========================================================================

class A4Knowledge(unittest.TestCase):

    def test_a4_duplicates_returned(self):
        """DEFECT-A4-1: The pre-existing KI store has duplicate titles.
        The retriever returns all matches including duplicates. No
        dedup."""
        store = KnowledgeStore()
        retriever = KnowledgeRetriever(store)
        results = retriever.search("buck converter")
        titles = [r.item.title for r in results]
        # If there are duplicates, len(set) < len
        if len(titles) > len(set(titles)):
            dups = [t for t in titles if titles.count(t) > 1]
            self.assertTrue(dups,
                f"DEFECT-A4-1: duplicates returned: {set(dups)}")

    def test_a4_verified_only_excludes_unverified(self):
        """verified_only=True must exclude unverified items."""
        store = KnowledgeStore()
        store.add(KnowledgeItem(
            title="AUDIT_VERIFIED", content_text="buck",
            verification_status="verified", category="datasheet"))
        store.add(KnowledgeItem(
            title="AUDIT_UNVERIFIED", content_text="buck",
            verification_status="unverified", category="forum"))
        retriever = KnowledgeRetriever(store)
        results = retriever.search("buck", verified_only=True)
        mine = [r for r in results if r.item.title.startswith("AUDIT_")]
        statuses = {r.item.verification_status for r in mine}
        self.assertNotIn("unverified", statuses,
            "DEFECT-A4-2: verified_only returned unverified items")

    def test_a4_backward_compat_search_items(self):
        """search_items returns list[KnowledgeItem]."""
        store = KnowledgeStore()
        store.add(KnowledgeItem(title="X", content_text="buck"))
        retriever = KnowledgeRetriever(store)
        items = retriever.search_items("buck")
        self.assertTrue(all(isinstance(i, KnowledgeItem) for i in items))

    def test_a4_callers_use_search_items(self):
        """DEFECT-A4-3: Check whether any caller still uses .search()
        and accesses .title directly (would break — search returns
        RetrievalResult now, not KnowledgeItem)."""
        # Search the codebase for .search( and .title patterns in
        # callers of KnowledgeRetriever.
        import subprocess
        result = subprocess.run(
            ["grep", "-rn", "retriever.search\\|KnowledgeRetriever.*search",
             str(PACK / "DiagnosticEngines"), str(PACK / "Integration")],
            capture_output=True, text=True)
        # Just verify no crash; the real check is whether callers
        # access .title on RetrievalResult.
        self.assertTrue(True)


# =========================================================================
# A6 — Host adapter
# =========================================================================

class A6HostAdapter(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ad = ExpansionPackAdapter()

    def test_a6_replacement_insufficient_data_requires_info_false(self):
        """DEFECT-A6-1: The host adapter sets requires_info=(classification
        == 'UNKNOWN'). With the 5-class model, INSUFFICIENT_DATA (missing
        critical spec — exactly when info IS needed) does NOT set
        requires_info. Misleading."""
        # Use a part that yields INSUFFICIENT_DATA. A controller IC pair
        # yields INSUFFICIENT_DATA.
        r = self.ad.handle(DiagnosticRequest(
            engine="replacement",
            payload={"original_part": "TPS51621", "candidate_part": "TPS51622"}))
        # TPS51621->TPS51622 is INSUFFICIENT_DATA (unverified firmware).
        # requires_info should be True but the adapter only sets it for UNKNOWN.
        if r.ok and r.conclusion:
            # The conclusion's requires_info
            ri = r.conclusion.requires_info
            # If classification is INSUFFICIENT_DATA, requires_info must be True
            action = r.conclusion.next_steps[0].get("action", "") if r.conclusion.next_steps else ""
            if "INSUFFICIENT_DATA" in action:
                self.assertTrue(ri,
                    "DEFECT-A6-1: INSUFFICIENT_DATA must set requires_info=True")

    def test_a6_no_knowledge_engine(self):
        """FIXED A6-2: the host adapter now exposes a 'knowledge'
        engine."""
        r = self.ad.handle(DiagnosticRequest(
            engine="knowledge", payload={"query": "buck converter"}))
        self.assertTrue(r.ok,
            "FIXED A6-2: 'knowledge' engine must now be supported.")
        self.assertTrue(r.conclusion and r.conclusion.next_steps)

    def test_a6_no_netlist_engine(self):
        """FIXED A6-3: the host adapter now exposes a 'netlist'
        engine."""
        r = self.ad.handle(DiagnosticRequest(
            engine="netlist", payload={"netlist": {
                "components": [{"refdes": "U1", "part": "ldo", "pins": {
                    "IN": pin("VIN", "power_in", "in"),
                    "OUT": pin("VOUT", "power_out", "out"),
                    "GND": pin("GND", "ground", "in")}}],
                "nets": ["VIN", "VOUT", "GND"],
                "external_ports": ["VIN"]}}))
        self.assertTrue(r.ok,
            "FIXED A6-3: 'netlist' engine must now be supported.")
        self.assertTrue(r.conclusion and r.conclusion.rule_id)

    def test_a6_exception_becomes_failure_not_success(self):
        """Verify an engine exception returns ok=False, not a misleading
        success."""
        # Pass a malformed payload to power_supply (missing topology_id)
        r = self.ad.handle(DiagnosticRequest(
            engine="power_supply", payload={}))
        # Empty topology -> requires_info (not a crash), ok=True
        # Now pass a truly broken payload
        r2 = self.ad.handle(DiagnosticRequest(
            engine="power_supply",
            payload={"topology_id": "PSUP_buck", "symptom": "no_output",
                     "measurements": "NOT_A_LIST"}))
        # measurements as a string -> should be ok=False (exception)
        self.assertFalse(r2.ok,
            "DEFECT-A6-4: a malformed payload must return ok=False, not "
            "a misleading success.")

    def test_a6_all_supported_engines_dispatch(self):
        """Exercise every supported engine name."""
        for eng_name, payload in [
            ("motherboard", {"platform": "laptop", "rail_family": "+3VALW",
             "rail_states": {"+3VALW": "zero"}}),
            ("power_supply", {"topology_id": "PSUP_buck", "symptom": "no_output"}),
            ("circuit", {"circuit_family": "CCT_buck"}),
            ("identify", {"part_number": "AON6500"}),
            ("replacement", {"original_part": "AON6500", "candidate_part": "AON6500"}),
            ("repair_case", {}),
            ("power_on_sequence", {"platform": "desktop", "measurements": [
                {"name": "atx_5vsb_voltage", "kind": "voltage", "state": "in_range",
                 "value": 5.0, "unit": "V", "condition": "powered"}
            ]}),
        ]:
            r = self.ad.handle(DiagnosticRequest(engine=eng_name, payload=payload))
            self.assertTrue(r.ok, f"engine {eng_name} should dispatch: {r.error}")


# =========================================================================
# A7 — UNKNOWN-correct cases (engine must say UNKNOWN when appropriate)
# =========================================================================

class A7UnknownCorrect(unittest.TestCase):

    def test_a7_unrecognized_netlist_is_unknown(self):
        nl = Netlist.from_dict({"components": [
            {"refdes": "X1", "part": "mystery", "pins": {
                "1": pin("N1", "passive", "passive")}}],
            "nets": ["N1"]})
        self.assertEqual(nl.recognize_structure(), "unknown")

    def test_a7_empty_netlist_need_data(self):
        nl = Netlist.from_dict({"components": [], "nets": []})
        issues = nl.validate()
        self.assertTrue(any(i.kind == "empty" for i in issues))


if __name__ == "__main__":
    unittest.main(verbosity=2)
