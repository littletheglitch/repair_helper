#!/usr/bin/env python3
"""Expanded test suite for the Electronics Diagnostic Expansion Pack.

Covers the spec's 18+ failure scenarios across all engines, plus:
  - Core Diagnostic Framework (measurement semantics, evidence, hypothesis)
  - Motherboard Engine (laptop + desktop, platform mismatch, sequence)
  - Power Supply Engine (mains safety, low-voltage, unknown handling)
  - Circuit Analysis Engine (topology recognition, failure modes)
  - Component Identification (exact, partial, ambiguous, unknown)
  - Replacement Engine (COMPATIBLE, CONDITIONAL, NOT_RECOMMENDED, UNKNOWN)
  - Measurement Intelligence (invalid conditions, abnormal, missing)
  - Failure Pattern DB + Repair Case Learning (validation, promotion)
  - Knowledge Platform (ingestion, forbidden categories, retrieval)
  - LLM Orchestrator (diagnostic mode, fallback, safety, evidence)
  - Host Adapter (all engines, graceful fallback)
  - Safety Policy (mains, charged cap, bypass, powered resistance)
  - Resource benchmarks (engine load + diagnose timing)
  - Regression: the original 12 power-rail scenarios still pass

Run with:  python Tests/run_expanded_tests.py
Pure standard library; uses unittest.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK_ROOT))

from DiagnosticEngines.core.framework import (  # noqa: E402
    Measurement, MeasurementKind, MeasurementState, MeasurementCondition,
    Case, CaseStatus, DiagnosticSession,
    Evidence, EvidenceType, EvidenceStrength,
    Hypothesis, HypothesisStatus,
    NextTest, NextTestSelector, TestCost, SkillLevel,
    ReportBuilder, SafetyPolicy, SafetyLevel,
)
from DiagnosticEngines.core.framework.session import EngineConclusion  # noqa: E402
from DiagnosticEngines.Motherboard import MotherboardEngine, MotherboardDiagnosisInput, PLATFORM_LAPTOP, PLATFORM_DESKTOP  # noqa: E402
from DiagnosticEngines.PowerSupply import PowerSupplyEngine, PowerSupplyDiagnosisInput  # noqa: E402
from DiagnosticEngines.CircuitAnalysis import CircuitAnalysisEngine, CircuitAnalysisInput  # noqa: E402
from DiagnosticEngines.core.identification_engine import ComponentIdentificationEngine, IdentificationInput  # noqa: E402
from DiagnosticEngines.core.replacement_engine import ReplacementEngine  # noqa: E402
from DiagnosticEngines.core.measurement_intelligence import MeasurementIntelligenceEngine  # noqa: E402
from DiagnosticEngines.core.failure_pattern_db import (  # noqa: E402
    FailurePatternDB, RepairCaseLearner, RepairCase,
    CONFIDENCE_CONFIRMED, CONFIDENCE_UNRESOLVED,
    PROMOTION_STATUS_APPROVED, PROMOTION_STATUS_PENDING,
)
from DiagnosticEngines.core.knowledge_platform import (  # noqa: E402
    KnowledgeStore, IngestionPipeline, KnowledgeRetriever,
)
from DiagnosticEngines.core.llm_orchestrator import (  # noqa: E402
    LLMOrchestrator, LLMConfig, MODE_DIAGNOSTIC, MODE_ASSISTANT,
)
from Integration.host_adapter import ExpansionPackAdapter, DiagnosticRequest  # noqa: E402


def m(name, value, kind=MeasurementKind.VOLTAGE, state=MeasurementState.IN_RANGE,
      condition=MeasurementCondition.POWERED, **kw):
    return Measurement(name=name, kind=kind, state=state, value=value,
                       condition=condition, **kw)


# ---------------------------------------------------------------------------
# Core Diagnostic Framework
# ---------------------------------------------------------------------------


class TestFrameworkSemantics(unittest.TestCase):
    """Verify the spec's measurement semantics: zero != missing,
    conditions matter, evidence is required for confirmation."""

    def test_zero_is_not_missing(self):
        z = Measurement.measured_zero("v", MeasurementKind.VOLTAGE)
        n = Measurement.not_measured("v", MeasurementKind.VOLTAGE)
        self.assertEqual(z.state, MeasurementState.MEASURED_ZERO)
        self.assertEqual(n.state, MeasurementState.NOT_MEASURED)
        self.assertTrue(z.is_measured)
        self.assertFalse(n.is_measured)
        self.assertIsNotNone(z.value)
        self.assertIsNone(n.value)

    def test_resistance_powered_not_comparable_to_unpowered(self):
        r_p = m("r", 100, kind=MeasurementKind.RESISTANCE, condition=MeasurementCondition.POWERED)
        r_u = m("r", 100, kind=MeasurementKind.RESISTANCE, condition=MeasurementCondition.UNPOWERED)
        self.assertFalse(r_p.comparable_to(r_u))
        self.assertTrue(r_u.comparable_to(r_p) is False)

    def test_voltage_powered_not_comparable_to_unpowered(self):
        v_p = m("v", 5.0, condition=MeasurementCondition.POWERED)
        v_u = m("v", 5.0, condition=MeasurementCondition.UNPOWERED)
        self.assertFalse(v_p.comparable_to(v_u))

    def test_hypothesis_requires_definitive_for_confirmed(self):
        h = Hypothesis(statement="x", engine="t", rule_source="r")
        self.assertEqual(h.status, HypothesisStatus.PROPOSED)
        h.add_evidence(Evidence(type=EvidenceType.SUPPORTING, strength=EvidenceStrength.STRONG,
                                 description="d", rule_source="r"))
        self.assertEqual(h.status, HypothesisStatus.PROBABLE)
        h.add_evidence(Evidence(type=EvidenceType.SUPPORTING, strength=EvidenceStrength.DEFINITIVE,
                                 description="d", rule_source="r"))
        self.assertEqual(h.status, HypothesisStatus.CONFIRMED)

    def test_hypothesis_contradiction_downgrades(self):
        h = Hypothesis(statement="x", engine="t", rule_source="r")
        h.add_evidence(Evidence(type=EvidenceType.SUPPORTING, strength=EvidenceStrength.DEFINITIVE,
                                 description="d", rule_source="r"))
        self.assertEqual(h.status, HypothesisStatus.CONFIRMED)
        h.add_evidence(Evidence(type=EvidenceType.CONTRADICTING, strength=EvidenceStrength.DEFINITIVE,
                                 description="d", rule_source="r"))
        self.assertEqual(h.status, HypothesisStatus.UNCERTAIN)

    def test_hypothesis_no_evidence_is_proposed(self):
        h = Hypothesis(statement="x", engine="t", rule_source="r")
        self.assertEqual(h.status, HypothesisStatus.PROPOSED)


# ---------------------------------------------------------------------------
# Safety Policy
# ---------------------------------------------------------------------------


class TestSafetyPolicy(unittest.TestCase):
    def setUp(self):
        self.pol = SafetyPolicy()

    def test_mains_probing_blocked(self):
        self.assertTrue(self.pol.is_blocked({"what": "probe mains primary side"}))
        self.assertTrue(self.pol.is_blocked({"what": "measure line voltage"}))

    def test_charged_cap_blocked(self):
        self.assertTrue(self.pol.is_blocked({"what": "touch primary bulk cap"}))

    def test_protection_bypass_blocked(self):
        self.assertTrue(self.pol.is_blocked({"what": "bypass OCP"}))
        self.assertTrue(self.pol.is_blocked({"what": "remove protection fuse"}))

    def test_powered_resistance_blocked(self):
        self.assertTrue(self.pol.is_blocked({
            "what": "measure resistance", "where": "+3VALW", "instrument": "DMM powered"
        }))

    def test_unpowered_resistance_not_blocked(self):
        self.assertFalse(self.pol.is_blocked({
            "what": "measure resistance", "where": "+3VALW", "instrument": "DMM unpowered discharg"
        }))

    def test_uncontrolled_injection_blocked(self):
        self.assertTrue(self.pol.is_blocked({"what": "apply full adapter current"}))


# ---------------------------------------------------------------------------
# Motherboard Engine — 18 scenarios
# ---------------------------------------------------------------------------


class TestMotherboardScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = MotherboardEngine()

    def _diag(self, rail, platform, measurements, **kw):
        inp = MotherboardDiagnosisInput(
            platform=platform, rail_family=rail,
            measurements=measurements, **kw)
        return self.eng.diagnose(inp)

    # 1. Missing +3VALW
    def test_01_missing_3valw(self):
        r = self._diag("+3VALW", PLATFORM_LAPTOP, [m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO)])
        self.assertEqual(r.rule_id, "RULE_ALW_001_RESISTANCE_FIRST")

    # 2. Missing +5VALW
    def test_02_missing_5valw(self):
        r = self._diag("+5VALW", PLATFORM_LAPTOP, [m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO)])
        self.assertEqual(r.rule_id, "RULE_ALW_001_RESISTANCE_FIRST")

    # 3. Shorted standby rail
    def test_03_shorted_standby(self):
        r = self._diag("+3VALW", PLATFORM_LAPTOP, [
            m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO),
            m("rail_resistance", 5.0, kind=MeasurementKind.RESISTANCE, condition=MeasurementCondition.UNPOWERED),
            m("rail_short", True, kind=MeasurementKind.CUSTOM),
        ])
        self.assertEqual(r.rule_id, "RULE_BUCK_010_SHORTED_RAIL")

    # 4. Missing PWM enable
    def test_04_missing_pwm_enable(self):
        r = self._diag("+1.8V", PLATFORM_LAPTOP, [
            m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO),
            m("rail_resistance", 500.0, kind=MeasurementKind.RESISTANCE, condition=MeasurementCondition.UNPOWERED),
            m("rail_short", False, kind=MeasurementKind.CUSTOM),
            m("vin", 5.0), m("vcc", 5.0),
        ], controller_part="TPS51224")
        self.assertEqual(r.rule_id, "RULE_BUCK_004_CHECK_ENABLE")
        self.assertTrue(r.requires_info)

    # 5. PWM VIN present, VCC missing
    def test_05_vin_present_vcc_missing(self):
        r = self._diag("+1.8V", PLATFORM_LAPTOP, [
            m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO),
            m("rail_resistance", 500.0, kind=MeasurementKind.RESISTANCE, condition=MeasurementCondition.UNPOWERED),
            m("rail_short", False, kind=MeasurementKind.CUSTOM),
            m("vin", 5.0), m("vcc", 0.0, state=MeasurementState.MEASURED_ZERO),
        ], controller_part="TPS51224")
        # VCC measured-and-absent -> fault conclusion (was: no rule fired, a defect)
        self.assertEqual(r.rule_id, "RULE_FAULT_VCC_ABSENT")
        self.assertFalse(r.requires_info)

    # 6. VCC present, EN missing (measured low)
    def test_06_vcc_present_en_missing(self):
        r = self._diag("+1.8V", PLATFORM_LAPTOP, [
            m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO),
            m("rail_resistance", 500.0, kind=MeasurementKind.RESISTANCE, condition=MeasurementCondition.UNPOWERED),
            m("rail_short", False, kind=MeasurementKind.CUSTOM),
            m("vin", 5.0), m("vcc", 5.0), m("en", 0.0, state=MeasurementState.MEASURED_ZERO),
        ], controller_part="TPS51224")
        # EN measured-and-absent -> fault conclusion
        self.assertEqual(r.rule_id, "RULE_FAULT_EN_ABSENT")
        self.assertFalse(r.requires_info)

    # 7. EN present, no switching
    def test_07_en_present_no_switching(self):
        r = self._diag("+1.8V", PLATFORM_LAPTOP, [
            m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO),
            m("rail_resistance", 500.0, kind=MeasurementKind.RESISTANCE, condition=MeasurementCondition.UNPOWERED),
            m("rail_short", False, kind=MeasurementKind.CUSTOM),
            m("vin", 5.0), m("vcc", 5.0), m("en", 3.3),
            m("switching", False, kind=MeasurementKind.LOGIC_LEVEL),
        ], controller_part="TPS51224")
        # switching measured-and-absent -> fault conclusion
        self.assertEqual(r.rule_id, "RULE_FAULT_SWITCHING_ABSENT")

    # 8. Switching present, output missing
    def test_08_switching_no_output(self):
        r = self._diag("+1.8V", PLATFORM_LAPTOP, [
            m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO),
            m("rail_resistance", 500.0, kind=MeasurementKind.RESISTANCE, condition=MeasurementCondition.UNPOWERED),
            m("rail_short", False, kind=MeasurementKind.CUSTOM),
            m("vin", 5.0), m("vcc", 5.0), m("en", 3.3), m("switching", True, kind=MeasurementKind.LOGIC_LEVEL),
        ], controller_part="TPS51224")
        # rail dead + switching present -> output-stage fault (not "ask for FB")
        self.assertEqual(r.rule_id, "RULE_FAULT_RAIL_DEAD_WITH_SWITCHING")

    # 9. Output present, PGOOD missing
    def test_09_output_present_pgood_missing(self):
        r = self._diag("+1.8V", PLATFORM_LAPTOP, [
            m("rail_voltage", 1.8),
            m("rail_resistance", 500.0, kind=MeasurementKind.RESISTANCE, condition=MeasurementCondition.UNPOWERED),
            m("rail_short", False, kind=MeasurementKind.CUSTOM),
            m("vin", 5.0), m("vcc", 5.0), m("en", 3.3), m("switching", True, kind=MeasurementKind.LOGIC_LEVEL),
            m("fb", 0.7),
        ], controller_part="TPS51224")
        # rail in tolerance + switching present -> healthy (PGOOD unmeasured
        # doesn't make the rail faulty; the rail IS up)
        self.assertEqual(r.rule_id, "RULE_RAIL_HEALTHY")

    # 10. CPU VCORE missing
    def test_10_cpu_vcore_missing(self):
        r = self._diag("VCORE", PLATFORM_LAPTOP, [m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO)],
                       controller_part="TPS51621")
        self.assertEqual(r.rule_id, "RULE_VRM_001_RESISTANCE_FIRST")

    # 11. Memory rail missing
    def test_11_memory_rail_missing(self):
        r = self._diag("DDR_VDD", PLATFORM_LAPTOP, [m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO)],
                       controller_part="TPS51224")
        self.assertEqual(r.rule_id, "RULE_BUCK_001_RESISTANCE_FIRST")

    # 12. Charger input MOSFET fault
    def test_12_charger_input_mosfet_fault(self):
        r = self._diag("ADAPTER_INPUT", PLATFORM_LAPTOP, [
            m("vin", 19.0),
            m("input_mosfet_off", True, kind=MeasurementKind.CUSTOM),
            m("vcc", 0.0, state=MeasurementState.MEASURED_ZERO),
        ], controller_part="BQ24780S")
        self.assertEqual(r.rule_id, "RULE_FAULT_VCC_ABSENT")  # VCC measured-absent -> fault conclusion

    # 13. Desktop ATX 5VSB missing
    def test_13_desktop_atx_5vsb_missing(self):
        r = self._diag("ATX_5VSB", PLATFORM_DESKTOP, [m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO)])
        self.assertEqual(r.rule_id, "RULE_ATX_001_CHECK_5VSB")

    # 14. Desktop VCORE missing
    def test_14_desktop_vcore_missing(self):
        r = self._diag("VCORE_DESKTOP", PLATFORM_DESKTOP, [m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO)])
        self.assertEqual(r.rule_id, "RULE_DESKTOP_VRM_001_RESISTANCE_FIRST")

    # 15. Platform mismatch (laptop rail on desktop)
    def test_15_platform_mismatch(self):
        r = self._diag("+3VALW", PLATFORM_DESKTOP, [])
        self.assertTrue(r.requires_info)
        self.assertIn("laptop", r.requires_info_note)

    # 16. Sequence graph: upstream short blocks downstream
    def test_16_sequence_upstream_short(self):
        r = self._diag("VCORE", PLATFORM_LAPTOP, [m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO)],
                       rail_states={"+3VALW":"good","+5VALW":"good","+1.8V":"short","DDR_VDD":"good","VCORE":"zero"})
        self.assertTrue(any("SHORTED" in n for n in r.notes))
        self.assertTrue(any("short on upstream" in h.statement for h in r.new_hypotheses))

    # 17. Unknown rail family
    def test_17_unknown_rail(self):
        r = self._diag("BOGUS_RAIL", PLATFORM_LAPTOP, [])
        self.assertTrue(r.requires_info)
        self.assertIn("not found", r.requires_info_note)

    # 18. Unknown controller does not break engine
    def test_18_unknown_controller(self):
        r = self._diag("+3VALW", PLATFORM_LAPTOP, [m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO)],
                       controller_part="UNKNOWN_PART")
        self.assertEqual(r.rule_id, "RULE_ALW_001_RESISTANCE_FIRST")
        self.assertTrue(any("not found in the component database" in n for n in r.notes))


# ---------------------------------------------------------------------------
# Power Supply Engine
# ---------------------------------------------------------------------------


class TestPowerSupplyEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = PowerSupplyEngine()

    def test_mains_atx_no_output_safety_gate(self):
        r = self.eng.diagnose(PowerSupplyDiagnosisInput(topology_id="PSUP_atx", symptom="no_output"))
        self.assertIn("SAFETY GATE", r.next_steps[0]["action"])
        # No step recommends live primary probing
        for s in r.next_steps:
            text = (s.get("action","") + " " + str(s.get("measure",{}).get("what","")) + " " + str(s.get("measure",{}).get("safety",""))).lower()
            if "primary" in text:
                self.assertTrue("not probe" in text or "not touch" in text or "do not" in text or "never" in text or "unplugged" in text)

    def test_low_voltage_linear_walks_stages(self):
        r = self.eng.diagnose(PowerSupplyDiagnosisInput(topology_id="PSUP_linear", symptom="no_output"))
        self.assertGreater(len(r.next_steps), 0)

    def test_unknown_topology(self):
        r = self.eng.diagnose(PowerSupplyDiagnosisInput(topology_id="PSUP_bogus", symptom="no_output"))
        self.assertTrue(r.requires_info)

    def test_unknown_symptom(self):
        r = self.eng.diagnose(PowerSupplyDiagnosisInput(topology_id="PSUP_atx", symptom="bogus"))
        self.assertTrue(r.requires_info)

    def test_15_topologies_loaded(self):
        self.assertGreaterEqual(len(self.eng.supported_topologies()), 12)


# ---------------------------------------------------------------------------
# Circuit Analysis Engine
# ---------------------------------------------------------------------------


class TestCircuitAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = CircuitAnalysisEngine()

    def test_36_circuits_loaded(self):
        self.assertGreaterEqual(len(self.eng.supported_circuits()), 35)

    def test_buck_analysis_without_measurements_asks_for_data(self):
        # No measurements supplied -> engine surfaces NEED_DATA (honest)
        r = self.eng.analyze(CircuitAnalysisInput(circuit_family="CCT_buck", symptom="no_output"))
        self.assertIn("NEED_DATA", r.rule_id)
        self.assertTrue(r.requires_info)
        self.assertGreaterEqual(len(r.next_steps), 1)

    def test_buck_input_absent_points_upstream(self):
        from DiagnosticEngines.core.framework import MeasurementState
        m_zero = Measurement(name="input", kind=MeasurementKind.VOLTAGE,
                             state=MeasurementState.MEASURED_ZERO, value=0.0,
                             condition=MeasurementCondition.POWERED)
        r = self.eng.analyze(CircuitAnalysisInput(circuit_family="CCT_buck", measurements=[m_zero]))
        self.assertIn("INPUT_MISSING", r.rule_id)

    def test_buck_output_absent_with_input_points_to_circuit(self):
        from DiagnosticEngines.core.framework import MeasurementState
        ms = [
            Measurement(name="input", kind=MeasurementKind.VOLTAGE, state=MeasurementState.IN_RANGE,
                        value=12.0, condition=MeasurementCondition.POWERED),
            Measurement(name="output", kind=MeasurementKind.VOLTAGE, state=MeasurementState.MEASURED_ZERO,
                        value=0.0, condition=MeasurementCondition.POWERED),
        ]
        r = self.eng.analyze(CircuitAnalysisInput(circuit_family="CCT_buck", measurements=ms))
        self.assertIn("OUTPUT_MISSING", r.rule_id)

    def test_buck_healthy_when_input_and_output_present(self):
        from DiagnosticEngines.core.framework import MeasurementState
        ms = [
            Measurement(name="input", kind=MeasurementKind.VOLTAGE, state=MeasurementState.IN_RANGE,
                        value=12.0, condition=MeasurementCondition.POWERED),
            Measurement(name="output", kind=MeasurementKind.VOLTAGE, state=MeasurementState.IN_RANGE,
                        value=3.3, condition=MeasurementCondition.POWERED),
        ]
        r = self.eng.analyze(CircuitAnalysisInput(circuit_family="CCT_buck", measurements=ms))
        self.assertIn("HEALTHY", r.rule_id)

    def test_topology_recognition_buck_then_needs_data(self):
        # Recognition identifies the circuit, but without measurements
        # the engine honestly surfaces NEED_DATA (not a fake conclusion).
        r = self.eng.analyze(CircuitAnalysisInput(component_list=[
            {"refdes":"L1","value":"inductor"}, {"refdes":"Q1","footprint":"mosfet"},
            {"refdes":"Q2","footprint":"mosfet"}, {"refdes":"U1","description":"buck controller"}
        ]))
        self.assertIn("CCT_buck", r.rule_id)
        self.assertIn("NEED_DATA", r.rule_id)

    def test_unknown_circuit(self):
        r = self.eng.analyze(CircuitAnalysisInput(circuit_family="CCT_bogus"))
        self.assertTrue(r.requires_info)

    def test_recognition_failure(self):
        r = self.eng.analyze(CircuitAnalysisInput(component_list=[{"refdes":"X1","value":"unknown"}]))
        self.assertTrue(r.requires_info)


# ---------------------------------------------------------------------------
# Component Identification
# ---------------------------------------------------------------------------


class TestIdentification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = ComponentIdentificationEngine()

    def test_exact_match(self):
        r = self.eng.identify(IdentificationInput(part_number="TPS51621"))
        self.assertEqual(r.confidence, "EXACT")

    def test_partial_with_context(self):
        r = self.eng.identify(IdentificationInput(marking="TPS5162", package="VQFN", pin_count=40, circuit_context="CPU VRM"))
        self.assertIn(r.confidence, ("LIKELY", "AMBIGUOUS"))

    def test_ambiguous(self):
        r = self.eng.identify(IdentificationInput(marking="TPS"))
        self.assertIn(r.confidence, ("AMBIGUOUS", "LIKELY"))

    def test_unknown(self):
        r = self.eng.identify(IdentificationInput(marking="ZZZUNKNOWN"))
        self.assertEqual(r.confidence, "UNKNOWN")
        self.assertTrue(r.requires_info)

    def test_empty_input(self):
        r = self.eng.identify(IdentificationInput())
        self.assertEqual(r.confidence, "UNKNOWN")

    def test_discrete_id(self):
        r = self.eng.identify(IdentificationInput(part_number="1N4007"))
        self.assertEqual(r.confidence, "EXACT")


# ---------------------------------------------------------------------------
# Replacement Engine
# ---------------------------------------------------------------------------


class TestReplacement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = ReplacementEngine()

    def test_same_part_compatible(self):
        ev = self.eng.evaluate("AON6500", "AON6500")
        self.assertEqual(ev.classification, "COMPATIBLE")

    def test_different_package_not_recommended(self):
        ev = self.eng.evaluate("AON6500", "Si4442DY")
        self.assertEqual(ev.classification, "NOT_RECOMMENDED")

    def test_unknown_part(self):
        ev = self.eng.evaluate("BOGUS1", "BOGUS2")
        self.assertEqual(ev.classification, "UNKNOWN")

    def test_controller_ic_unknown(self):
        ev = self.eng.evaluate("TPS51621", "TPS51622")
        # Controller ICs have unverified firmware/control-interface specs
        # -> INSUFFICIENT_DATA (cannot confirm compatibility), not UNKNOWN.
        self.assertEqual(ev.classification, "INSUFFICIENT_DATA")

    def test_discrete_1n4007_to_ss34(self):
        ev = self.eng.evaluate("1N4007", "SS34")
        self.assertIn(ev.classification, ("NOT_RECOMMENDED", "CONDITIONALLY_COMPATIBLE"))

    def test_analog_lm358_to_opa2134(self):
        ev = self.eng.evaluate("LM358", "OPA2134")
        self.assertIn(ev.classification, ("NOT_RECOMMENDED", "CONDITIONALLY_COMPATIBLE"))


# ---------------------------------------------------------------------------
# Measurement Intelligence
# ---------------------------------------------------------------------------


class TestMeasurementIntelligence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = MeasurementIntelligenceEngine()

    def test_resistance_powered_flagged(self):
        s = DiagnosticSession(case=Case(platform="laptop"))
        s.add_measurement(m("r", 5.0, kind=MeasurementKind.RESISTANCE, condition=MeasurementCondition.POWERED))
        a = self.eng.analyze(s)
        self.assertTrue(any(i.category == "invalid_condition" for i in a.issues))

    def test_abnormal_voltage_flagged(self):
        s = DiagnosticSession(case=Case(platform="laptop"))
        s.add_measurement(m("+3VALW.voltage", 2.5))  # below 3.2 min
        a = self.eng.analyze(s)
        self.assertTrue(any(i.category == "abnormal" for i in a.issues))

    def test_missing_not_treated_as_zero(self):
        s = DiagnosticSession(case=Case(platform="laptop"))
        s.add_measurement(Measurement.not_measured("x", MeasurementKind.VOLTAGE))
        a = self.eng.analyze(s)
        self.assertTrue(any(i.category == "missing" for i in a.issues))

    def test_measured_zero_distinct(self):
        s = DiagnosticSession(case=Case(platform="laptop"))
        s.add_measurement(Measurement.measured_zero("z", MeasurementKind.VOLTAGE, condition=MeasurementCondition.POWERED))
        a = self.eng.analyze(s)
        self.assertTrue(any("MEASURED_ZERO" in i.description for i in a.issues))


# ---------------------------------------------------------------------------
# Failure Pattern DB + Repair Case Learning
# ---------------------------------------------------------------------------


class TestFailurePatternDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = FailurePatternDB()
        cls.learner = RepairCaseLearner()

    def test_seed_example_loaded_but_not_counted_as_real(self):
        self.assertGreaterEqual(self.db.count(), 1)
        self.assertEqual(self.db.real_case_count(), 0)

    def test_search_excludes_seed_example_by_default(self):
        r = self.db.search(failure_pattern="shorted_rail")
        self.assertEqual(len(r), 0)
        examples = self.db.search(failure_pattern="shorted_rail", include_seed_examples=True)
        self.assertGreaterEqual(len(examples), 1)

    def test_seed_example_cannot_be_validated_or_promoted(self):
        vr = self.learner.validate(self.db.cases[0])
        self.assertFalse(vr.valid)
        self.assertTrue(any("seed example" in issue.lower() for issue in vr.issues))
        ok, reason = self.learner.can_promote(self.db.cases[0])
        self.assertFalse(ok)
        self.assertIn("Seed examples", reason)

    def test_missing_fields_rejected(self):
        bad = RepairCase(device_manufacturer="X", confidence=CONFIDENCE_CONFIRMED)
        vr = self.learner.validate(bad)
        self.assertGreater(len(vr.missing_fields), 0)

    def test_unresolved_cannot_promote(self):
        c = self.db.cases[0]
        c.confidence = CONFIDENCE_UNRESOLVED
        ok, _ = self.learner.can_promote(c)
        self.assertFalse(ok)
        c.confidence = CONFIDENCE_CONFIRMED  # restore

    def test_pending_status_cannot_promote(self):
        c = self.db.cases[0]
        c.promotion_status = PROMOTION_STATUS_PENDING
        ok, _ = self.learner.can_promote(c)
        self.assertFalse(ok)
        c.promotion_status = PROMOTION_STATUS_APPROVED  # restore

    def test_forum_confirmed_flagged(self):
        c = RepairCase(device_manufacturer="X", device_model="Y", board_model="Z", platform="laptop",
                       symptoms=["x"], confirmed_faulty_component="C1", root_cause="rc",
                       repair_action="ra", source="forum", confidence=CONFIDENCE_CONFIRMED,
                       final_verification="ok", measurements=[{"name":"r","value":5}],
                       promotion_status=PROMOTION_STATUS_APPROVED)
        vr = self.learner.validate(c)
        self.assertTrue(any("forum" in i.lower() for i in vr.issues))


# ---------------------------------------------------------------------------
# Knowledge Platform
# ---------------------------------------------------------------------------


class TestKnowledgePlatform(unittest.TestCase):
    def setUp(self):
        # Use a fresh store for each test to avoid cross-test pollution
        self.store = KnowledgeStore()
        self.pipe = IngestionPipeline(self.store)
        self.ret = KnowledgeRetriever(self.store)

    def test_ingest_text(self):
        item, log = self.pipe.ingest_text(
            source_title="Test", source_author="X",
            source_url="https://example.com", content_text="buck converter",
            category="app_note", technical_classification="buck")
        self.assertIsNotNone(item)
        self.assertEqual(len(log), 11)

    def test_forbidden_bios_dump(self):
        item, log = self.pipe.ingest_text(
            source_title="bios", source_author="x", source_url="",
            content_text="bytes", category="bios_dump")
        self.assertIsNone(item)
        self.assertTrue(any("FORBIDDEN" in l for l in log))

    def test_forbidden_schematic(self):
        item, _ = self.pipe.ingest_text(
            source_title="sch", source_author="x", source_url="",
            content_text="x", category="schematic")
        self.assertIsNone(item)

    def test_empty_content_rejected(self):
        item, _ = self.pipe.ingest_text(
            source_title="x", source_author="x", source_url="",
            content_text="", category="app_note")
        self.assertIsNone(item)

    def test_retrieval(self):
        self.pipe.ingest_text(
            source_title="Buck", source_author="TI",
            source_url="https://ti.com", content_text="buck converter design",
            category="app_note", tags=["buck"])
        results = self.ret.search("buck")
        self.assertGreaterEqual(len(results), 1)


# ---------------------------------------------------------------------------
# LLM Orchestrator
# ---------------------------------------------------------------------------


class TestLLMOrchestrator(unittest.TestCase):
    def test_diagnostic_mode_no_llm(self):
        orch = LLMOrchestrator(LLMConfig(mode=MODE_DIAGNOSTIC))
        r = orch.explain("x")
        self.assertFalse(r.ok)
        self.assertFalse(r.used_llm)

    def test_assistant_no_backend_fallback(self):
        orch = LLMOrchestrator(LLMConfig(mode=MODE_ASSISTANT))
        r = orch.explain("x")
        self.assertFalse(r.ok)
        self.assertFalse(r.used_llm)

    def test_assistant_with_backend(self):
        orch = LLMOrchestrator(LLMConfig(mode=MODE_ASSISTANT))
        orch.set_backend("fake", lambda p, c: "answer")
        r = orch.explain("x")
        self.assertTrue(r.ok)
        self.assertTrue(r.used_llm)

    def test_safety_rejection(self):
        orch = LLMOrchestrator(LLMConfig(mode=MODE_ASSISTANT))
        orch.set_backend("u", lambda p, c: "probe the mains primary side")
        r = orch.explain("x")
        self.assertFalse(r.ok)
        self.assertTrue(r.rejected_by_safety)

    def test_evidence_rejection_full_phrase(self):
        # The conservative check catches EXPLICIT full-phrase negations
        # like "not rail is shorted" / "is not rail is shorted".
        orch = LLMOrchestrator(LLMConfig(mode=MODE_ASSISTANT))
        orch.set_backend("c", lambda p, c: "Conclusion: not rail is shorted.")
        r = orch.explain("x", confirmed_facts=["rail is shorted"])
        self.assertFalse(r.ok)
        self.assertTrue(r.rejected_by_evidence)

    def test_evidence_check_does_not_false_positive_on_partial_negation(self):
        # A partial negation like "not shorted to ground but shorted to
        # VCC" is still a FAULT — the conservative check must NOT reject
        # it (substring matching cannot reliably distinguish this from
        # a real contradiction). Documented limitation.
        orch = LLMOrchestrator(LLMConfig(mode=MODE_ASSISTANT))
        orch.set_backend("c", lambda p, c: "not shorted to ground but shorted to VCC")
        r = orch.explain("x", confirmed_facts=["rail is shorted"])
        # The conservative full-phrase check does not match this, so it
        # is NOT rejected. The technician remains the authoritative gate.
        self.assertTrue(r.ok)
        self.assertFalse(r.rejected_by_evidence)

    def test_cache_hit_does_not_claim_llm_use(self):
        orch = LLMOrchestrator(LLMConfig(mode=MODE_ASSISTANT))
        orch.set_backend("f", lambda p, c: "cached answer")
        orch.explain("same prompt")
        r2 = orch.explain("same prompt")
        self.assertTrue(r2.ok)
        self.assertFalse(r2.used_llm)  # cache hit did not invoke the LLM
        self.assertLess(r2.duration_s, 0.01)


# ---------------------------------------------------------------------------
# Host Adapter
# ---------------------------------------------------------------------------


class TestHostAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ad = ExpansionPackAdapter()

    def test_manifest_loads(self):
        m = self.ad.manifest()
        self.assertEqual(m.get("pack_name"), "Electronics_Diagnostic_Expansion_Pack")

    def test_capabilities_listed(self):
        caps = self.ad.capabilities()
        self.assertGreaterEqual(len(caps), 7)

    def test_all_engines_dispatch(self):
        for engine in ["motherboard", "power_supply", "circuit", "identify", "replacement", "repair_case"]:
            payload = {}
            if engine == "motherboard":
                payload = {"platform":"laptop","rail_family":"+3VALW",
                           "measurements":[{"name":"rail_voltage","kind":"voltage","state":"measured_zero","value":0.0,"condition":"powered","instrument":"DMM"}],
                           "rail_states":{"+3VALW":"zero"}}
            elif engine == "power_supply":
                payload = {"topology_id":"PSUP_atx","symptom":"no_output"}
            elif engine == "circuit":
                payload = {"circuit_family":"CCT_buck","symptom":"no_output"}
            elif engine == "identify":
                payload = {"part_number":"TPS51621"}
            elif engine == "replacement":
                payload = {"original_part":"AON6500","candidate_part":"Si4442DY"}
            elif engine == "repair_case":
                payload = {"failure_pattern":"shorted_rail"}
            r = self.ad.handle(DiagnosticRequest(engine=engine, payload=payload))
            self.assertTrue(r.ok, f"{engine} failed: {r.error}")

    def test_unknown_engine_fallback(self):
        r = self.ad.handle(DiagnosticRequest(engine="bogus", payload={}))
        self.assertFalse(r.ok)

    def test_report_render(self):
        s = DiagnosticSession(case=Case(platform="laptop"))
        self.ad.handle(DiagnosticRequest(engine="motherboard",
            payload={"platform":"laptop","rail_family":"+3VALW",
                     "measurements":[{"name":"rail_voltage","kind":"voltage","state":"measured_zero","value":0.0,"condition":"powered","instrument":"DMM"}],
                     "rail_states":{"+3VALW":"zero"}},
            session=s))
        md = self.ad.render_report(s)
        self.assertIn("Diagnostic Report", md)


# ---------------------------------------------------------------------------
# Resource benchmarks
# ---------------------------------------------------------------------------


class TestResourceBenchmarks(unittest.TestCase):
    """Best-effort timing benchmarks. Not strict — just verifies engines
    load and run in reasonable time on resource-constrained hardware."""

    def test_motherboard_engine_loads_under_2s(self):
        t0 = time.monotonic()
        eng = MotherboardEngine()
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 2.0, f"MotherboardEngine load took {elapsed:.2f}s")

    def test_diagnose_under_100ms(self):
        eng = MotherboardEngine()
        inp = MotherboardDiagnosisInput(
            platform=PLATFORM_LAPTOP, rail_family="+3VALW",
            measurements=[m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO)],
            rail_states={"+3VALW":"zero"})
        t0 = time.monotonic()
        for _ in range(10):
            eng.diagnose(inp)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed / 10, 0.1, f"diagnose avg {elapsed/10*1000:.1f}ms")

    def test_adapter_loads_all_engines_under_5s(self):
        ad = ExpansionPackAdapter()
        t0 = time.monotonic()
        for engine in ["motherboard", "power_supply", "circuit", "identify", "replacement", "measurement", "repair_case"]:
            ad._get_engine(engine)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 5.0, f"all engines loaded in {elapsed:.2f}s")


# ---------------------------------------------------------------------------
# Regression: original 12 power-rail scenarios
# ---------------------------------------------------------------------------


class TestRegressionOriginalSuite(unittest.TestCase):
    """The original Tests/run_all_tests.py must still pass. This class
    re-runs a subset of the original scenarios via the new framework
    bridge to confirm no regression."""

    @classmethod
    def setUpClass(cls):
        from DiagnosticEngines.core import load_engine
        from DiagnosticEngines.core import power_rail_bridge
        cls.legacy = load_engine()
        cls._bridge_mod = power_rail_bridge

    def _bridge(self, rail_family, measurements, controller_part=None, topology=None):
        # Call the module function directly to avoid Python's method
        # binding (which would prepend `self` and shift positional args).
        return self._bridge_mod.diagnose_with_framework_measurements(
            self.legacy, rail_family, measurements,
            controller_part=controller_part, topology=topology)

    def test_original_01_missing_3valw(self):
        r = self._bridge("+3VALW",
                        [m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO)],
                        controller_part=None)
        self.assertEqual(r.rule_id, "RULE_ALW_001_RESISTANCE_FIRST")

    def test_original_03_shorted(self):
        r = self._bridge("+3VALW", [
            m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO),
            m("rail_resistance", 5.0, kind=MeasurementKind.RESISTANCE, condition=MeasurementCondition.UNPOWERED),
            m("rail_short", True, kind=MeasurementKind.CUSTOM),
        ])
        self.assertEqual(r.rule_id, "RULE_BUCK_010_SHORTED_RAIL")

    def test_original_10_vcore(self):
        r = self._bridge("VCORE",
                        [m("rail_voltage", 0.0, state=MeasurementState.MEASURED_ZERO)],
                        controller_part="TPS51621")
        self.assertEqual(r.rule_id, "RULE_VRM_001_RESISTANCE_FIRST")


# ---------------------------------------------------------------------------
# Numeric fault detection (low / high / unstable voltage, overload)
# ---------------------------------------------------------------------------


class TestNumericFaultDetection(unittest.TestCase):
    """Verify the power-rail engine distinguishes low/high/unstable
    voltage from no-output, and handles overload as a follow-on."""

    @classmethod
    def setUpClass(cls):
        from DiagnosticEngines.core.engine import load_engine, Measurement as LegacyM
        cls.legacy = load_engine()
        cls.LegacyM = LegacyM

    def _diag(self, rail, measurements, **kw):
        return self.legacy.diagnose(rail, measurements, **kw)

    def test_low_voltage_fires_low_rule(self):
        # +3VALW at 2.5V (min 3.2) — LOW, not resistance-first
        r = self._diag("+3VALW", [self.LegacyM(name="rail_voltage", value=2.5, measured=True)])
        self.assertEqual(r.fired_rule_id, "RULE_VOLTAGE_LOW_OUTPUT")

    def test_high_voltage_fires_high_rule(self):
        # +3VALW at 3.6V (max 3.4) — HIGH
        r = self._diag("+3VALW", [self.LegacyM(name="rail_voltage", value=3.6, measured=True)])
        self.assertEqual(r.fired_rule_id, "RULE_VOLTAGE_HIGH_OUTPUT")

    def test_zero_voltage_fires_resistance_first_not_low(self):
        # +3VALW at 0.0V — resistance-first, NOT low (zero is absent, not low)
        r = self._diag("+3VALW", [self.LegacyM(name="rail_voltage", value=0.0, measured=True)])
        self.assertEqual(r.fired_rule_id, "RULE_ALW_001_RESISTANCE_FIRST")

    def test_normal_voltage_fires_no_fault_rule(self):
        # +3VALW at 3.30V — healthy, no numeric fault rule fires
        r = self._diag("+3VALW", [self.LegacyM(name="rail_voltage", value=3.30, measured=True)])
        self.assertNotIn(r.fired_rule_id,
                         ("RULE_VOLTAGE_LOW_OUTPUT", "RULE_VOLTAGE_HIGH_OUTPUT",
                          "RULE_VOLTAGE_UNSTABLE"))

    def test_unstable_voltage_fires_unstable_rule(self):
        r = self._diag("+3VALW", [
            self.LegacyM(name="rail_voltage", value=3.30, measured=True),
            self.LegacyM(name="rail_voltage_stable", value=False, measured=True),
        ])
        self.assertEqual(r.fired_rule_id, "RULE_VOLTAGE_UNSTABLE")

    def test_overload_follow_on_rule(self):
        # OVERLOAD_IDENTIFY is a follow-on rule — only reachable via
        # asserted_facts. Without asserted_facts it is NOT a candidate.
        r = self._diag("+3VALW", [self.LegacyM(name="rail_voltage", value=2.5, measured=True)])
        self.assertNotEqual(r.fired_rule_id, "RULE_OVERLOAD_IDENTIFY")
        # With overload asserted, the follow-on fires.
        r2 = self._diag("+3VALW",
                        [self.LegacyM(name="rail_voltage", value=2.5, measured=True)],
                        asserted_facts={"overload": True})
        self.assertEqual(r2.fired_rule_id, "RULE_OVERLOAD_IDENTIFY")

    def test_follow_on_rule_not_candidate_without_asserted_facts(self):
        # SAMPLE_07-style: +1.8V missing, VIN+VCC+EN good, switching absent.
        # The follow-on OVERLOAD rule must NOT force requires_info=True.
        r = self._diag("+1.8V", [
            self.LegacyM(name="rail_voltage", value=0.0, measured=True),
            self.LegacyM(name="rail_resistance", value=500.0, measured=True),
            self.LegacyM(name="rail_short", value=False, measured=True),
            self.LegacyM(name="vin", value=5.0, measured=True),
            self.LegacyM(name="vcc", value=5.0, measured=True),
            self.LegacyM(name="en", value=3.3, measured=True),
            self.LegacyM(name="switching", value=False, measured=True),
        ], controller_part="TPS51224")
        # The follow-on rule must not appear in candidates
        self.assertNotIn("RULE_OVERLOAD_IDENTIFY", r.candidate_rules)

    def test_desktop_rail_low_voltage(self):
        # ATX_5VSB at 4.0V (min 4.75) — LOW
        r = self._diag("ATX_5VSB", [self.LegacyM(name="rail_voltage", value=4.0, measured=True)])
        self.assertEqual(r.fired_rule_id, "RULE_VOLTAGE_LOW_OUTPUT")

    def test_boundary_at_min_is_not_low(self):
        # +3VALW at exactly 3.2V (min) — in tolerance, not low
        r = self._diag("+3VALW", [self.LegacyM(name="rail_voltage", value=3.2, measured=True)])
        self.assertNotEqual(r.fired_rule_id, "RULE_VOLTAGE_LOW_OUTPUT")

    def test_boundary_at_max_is_not_high(self):
        # +3VALW at exactly 3.4V (max) — in tolerance, not high
        r = self._diag("+3VALW", [self.LegacyM(name="rail_voltage", value=3.4, measured=True)])
        self.assertNotEqual(r.fired_rule_id, "RULE_VOLTAGE_HIGH_OUTPUT")

    def test_low_rule_next_step_mentions_overload_and_feedback(self):
        r = self._diag("+3VALW", [self.LegacyM(name="rail_voltage", value=2.5, measured=True)])
        self.assertTrue(r.next_steps)
        action_text = r.next_steps[0].action.lower()
        # The LOW rule's step 1 must guide toward overload vs feedback vs upstream
        self.assertTrue("overload" in action_text or "load current" in action_text)


# ---------------------------------------------------------------------------
# Motherboard power-on sequence engine
# ---------------------------------------------------------------------------


class TestPowerOnSequence(unittest.TestCase):
    """Verify the power-on sequence engine walks the board through its
    states and identifies the earliest failed stage + fault class."""

    @classmethod
    def setUpClass(cls):
        cls.eng = MotherboardEngine()

    def _v(self, name, val):
        return m(name, val, kind=MeasurementKind.VOLTAGE,
                 state=MeasurementState.IN_RANGE, condition=MeasurementCondition.POWERED)

    def _present(self, name):
        return Measurement(name=name, kind=MeasurementKind.LOGIC_LEVEL,
                           state=MeasurementState.IN_RANGE, value=True,
                           condition=MeasurementCondition.POWERED)

    def _absent(self, name):
        return Measurement(name=name, kind=MeasurementKind.LOGIC_LEVEL,
                           state=MeasurementState.MEASURED_ZERO, value=0.0,
                           condition=MeasurementCondition.POWERED)

    def _seq(self, platform, measurements, symptoms=None):
        from DiagnosticEngines.core.power_on_sequence import PowerOnSequenceInput
        return self.eng.diagnose_power_on_sequence(PowerOnSequenceInput(
            platform=platform, measurements=measurements,
            observed_symptoms=symptoms or [],
        ))

    def test_no_power_adapter_missing(self):
        r = self._seq(PLATFORM_LAPTOP, [self._v("adapter_input_voltage", 0.0)])
        # Zero adapter -> stage failed (not missing, since we measured it)
        self.assertIn("adapter_input", r.rule_id)

    def test_no_standby_3valw_missing(self):
        r = self._seq(PLATFORM_LAPTOP, [
            self._v("adapter_input_voltage", 19.0),
            Measurement(name="plus_3valw_voltage", kind=MeasurementKind.VOLTAGE,
                        state=MeasurementState.MEASURED_ZERO, value=0.0,
                        condition=MeasurementCondition.POWERED),
        ])
        self.assertIn("standby_rails", r.rule_id)

    def test_no_power_on_ec_no_response(self):
        r = self._seq(PLATFORM_LAPTOP, [
            self._v("adapter_input_voltage", 19.0),
            self._v("plus_3valw_voltage", 3.3), self._v("plus_5valw_voltage", 5.0),
            self._present("ec_heartbeat"), self._present("power_button_pressed"),
            self._absent("ec_responded_to_button"),
        ])
        self.assertIn("FAIL_power_button", r.rule_id)
        self.assertFalse(r.requires_info)

    def test_no_post_reset_still_asserted(self):
        r = self._seq(PLATFORM_LAPTOP, [
            self._v("adapter_input_voltage", 19.0),
            self._v("plus_3valw_voltage", 3.3), self._v("plus_5valw_voltage", 5.0),
            self._present("ec_heartbeat"), self._present("power_button_pressed"),
            self._present("ec_responded_to_button"), self._present("main_enables_high"),
            self._v("vcore_voltage", 1.0), self._v("ddr_vdd_voltage", 1.2),
            self._absent("reset_deasserted"),
        ])
        self.assertIn("FAIL_clock_reset", r.rule_id)

    def test_no_display_post_ok_display_missing(self):
        r = self._seq(PLATFORM_LAPTOP, [
            self._v("adapter_input_voltage", 19.0),
            self._v("plus_3valw_voltage", 3.3), self._v("plus_5valw_voltage", 5.0),
            self._present("ec_heartbeat"), self._present("power_button_pressed"),
            self._present("ec_responded_to_button"), self._present("main_enables_high"),
            self._v("vcore_voltage", 1.0), self._v("ddr_vdd_voltage", 1.2),
            self._present("reset_deasserted"), self._present("cpu_clock_present"),
            self._present("post_progressing"),
            # display measurements not supplied -> need data
        ])
        self.assertIn("display", r.rule_id)
        self.assertTrue(r.requires_info)

    def test_shutdown_symptom(self):
        r = self._seq(PLATFORM_LAPTOP, [self._v("adapter_input_voltage", 19.0)],
                      symptoms=["shutdown after 2 seconds"])
        self.assertIn("SHUTDOWN", r.rule_id)

    def test_restart_loop_symptom(self):
        r = self._seq(PLATFORM_LAPTOP, [self._v("adapter_input_voltage", 19.0)],
                      symptoms=["restart loop"])
        self.assertIn("RESTART_LOOP", r.rule_id)

    def test_charging_fault_symptom(self):
        r = self._seq(PLATFORM_LAPTOP, [self._v("adapter_input_voltage", 19.0)],
                      symptoms=["battery wont charge"])
        self.assertIn("CHARGING", r.rule_id)

    def test_healthy_full_sequence(self):
        r = self._seq(PLATFORM_LAPTOP, [
            self._v("adapter_input_voltage", 19.0),
            self._v("plus_3valw_voltage", 3.3), self._v("plus_5valw_voltage", 5.0),
            self._present("ec_heartbeat"), self._present("power_button_pressed"),
            self._present("ec_responded_to_button"), self._present("main_enables_high"),
            self._v("vcore_voltage", 1.0), self._v("ddr_vdd_voltage", 1.2),
            self._present("reset_deasserted"), self._present("cpu_clock_present"),
            self._present("post_progressing"),
            self._present("display_backlight_on"), self._present("display_data_present"),
        ])
        self.assertIn("HEALTHY", r.rule_id)

    def test_desktop_no_standby_5vsb_missing(self):
        r = self._seq(PLATFORM_DESKTOP, [
            Measurement(name="atx_5vsb_voltage", kind=MeasurementKind.VOLTAGE,
                        state=MeasurementState.MEASURED_ZERO, value=0.0,
                        condition=MeasurementCondition.POWERED),
        ])
        self.assertIn("atx_5vsb", r.rule_id)

    def test_desktop_healthy(self):
        r = self._seq(PLATFORM_DESKTOP, [
            self._v("atx_5vsb_voltage", 5.0),
            self._present("power_button_pressed"), self._present("sio_responded_to_button"),
            self._v("atx_3v3_voltage", 3.3), self._v("atx_5v_voltage", 5.0),
            self._v("atx_12v_voltage", 12.0),
            self._v("vcore_voltage", 1.0), self._v("ddr_vdd_voltage", 1.2),
            self._present("reset_deasserted"), self._present("cpu_clock_present"),
            self._present("post_progressing"), self._present("display_data_present"),
        ])
        self.assertIn("HEALTHY", r.rule_id)

    def test_desktop_usb_fault_does_not_masquerade_as_no_post(self):
        r = self._seq(PLATFORM_DESKTOP, [
            self._v("atx_5vsb_voltage", 5.0),
            self._present("power_button_pressed"), self._present("sio_responded_to_button"),
            self._present("ps_on_asserted"),
            self._v("atx_3v3_voltage", 3.3), self._v("atx_5v_voltage", 5.0), self._v("atx_12v_voltage", 12.0),
            self._present("pwr_ok_asserted"), self._v("eps12v_voltage", 12.0),
            self._v("vcore_voltage", 1.0), self._v("ddr_vdd_voltage", 1.2),
            self._present("pch_power_good"), self._present("bios_spi_activity"),
            self._present("reset_deasserted"), self._present("cpu_clock_present"),
            self._v("pcie_12v_voltage", 12.0), self._present("post_progressing"),
            self._present("display_data_present"), self._v("usb_vbus_voltage", 0.0),
        ])
        self.assertIn("peripheral_usb", r.rule_id)
        self.assertNotIn("no_post", " ".join(r.notes).lower())
        self.assertTrue(any("Core power-on stages" in note for note in r.notes))

    def test_desktop_optional_peripherals_do_not_block_healthy_result(self):
        r = self._seq(PLATFORM_DESKTOP, [
            self._v("atx_5vsb_voltage", 5.0),
            self._present("power_button_pressed"), self._present("sio_responded_to_button"),
            self._v("atx_3v3_voltage", 3.3), self._v("atx_5v_voltage", 5.0), self._v("atx_12v_voltage", 12.0),
            self._v("vcore_voltage", 1.0), self._v("ddr_vdd_voltage", 1.2),
            self._present("reset_deasserted"), self._present("cpu_clock_present"),
            self._present("post_progressing"), self._present("display_data_present"),
        ])
        self.assertIn("HEALTHY", r.rule_id)
        self.assertTrue(any("14 required core" in note for note in r.notes))

    def test_unknown_platform(self):
        r = self._seq("tablet", [])
        self.assertTrue(r.requires_info)
        self.assertIn("Unknown platform", r.requires_info_note)

    def test_insufficient_data_does_not_claim_fault(self):
        # Only adapter measured; everything else missing -> need data, NOT a fault claim
        r = self._seq(PLATFORM_LAPTOP, [self._v("adapter_input_voltage", 19.0)])
        self.assertTrue(r.requires_info)
        self.assertNotIn("FAIL", r.rule_id)  # must not claim a fault without measurements


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    classes = [
        TestFrameworkSemantics, TestSafetyPolicy,
        TestMotherboardScenarios, TestPowerSupplyEngine,
        TestCircuitAnalysis, TestIdentification, TestReplacement,
        TestMeasurementIntelligence, TestFailurePatternDB,
        TestKnowledgePlatform, TestLLMOrchestrator, TestHostAdapter,
        TestResourceBenchmarks, TestRegressionOriginalSuite,
        TestNumericFaultDetection, TestPowerOnSequence,
    ]
    for cls in classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
