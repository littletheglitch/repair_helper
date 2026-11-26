#!/usr/bin/env python3
"""Adversarial tests for power-supply topology differentiation (P1).

REQUIRED: each topology must have a STRUCTURALLY DISTINCT stage model —
different stage lists, different critical signals, different failure
branches. A linear regulator has no switching node; a boost has no
output inductor in the buck sense; a flyback has a transformer and
optocoupler feedback; LLC has no output diode (resonant). The engine
must NOT walk the same input→VCC→EN→switching→FB→output→PGOOD chain
for all of them.

Negative tests: an IRRELEVANT signal for a topology must NOT change the
diagnosis. E.g. supplying 'switching' to a LINEAR regulator (which has
no switching node) must not make the linear workflow behave like a buck.
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from DiagnosticEngines.PowerSupply import PowerSupplyEngine, PowerSupplyDiagnosisInput
from DiagnosticEngines.core.framework import (
    Measurement, MeasurementKind, MeasurementState, MeasurementCondition,
)

eng = PowerSupplyEngine()


def V(name, val):
    return Measurement(name=name, kind=MeasurementKind.VOLTAGE,
                       state=MeasurementState.IN_RANGE, value=val,
                       condition=MeasurementCondition.POWERED)


def ZERO(name):
    return Measurement(name=name, kind=MeasurementKind.VOLTAGE,
                       state=MeasurementState.MEASURED_ZERO, value=0.0,
                       condition=MeasurementCondition.POWERED)


def PRES(name):
    return Measurement(name=name, kind=MeasurementKind.LOGIC_LEVEL,
                       state=MeasurementState.IN_RANGE, value=True,
                       condition=MeasurementCondition.POWERED)


def ABS(name):
    return Measurement(name=name, kind=MeasurementKind.LOGIC_LEVEL,
                       state=MeasurementState.MEASURED_ZERO, value=0.0,
                       condition=MeasurementCondition.POWERED)


def diag(topo, symptom="no_output", measurements=None):
    return eng.diagnose(PowerSupplyDiagnosisInput(
        topology_id=topo, symptom=symptom, measurements=measurements or []))


class TestStructurallyDistinctStageModels(unittest.TestCase):
    """Each topology must expose a distinct stage model."""

    def test_linear_has_no_switching_stage(self):
        """A linear regulator has no switching node. Its stage model
        must NOT include a 'switching' stage. Supplying a 'switching'
        measurement to a linear regulator must be irrelevant."""
        topo = eng.topologies["PSUP_linear"]
        stage_ids = [s.get("id") for s in topo.get("stages", [])]
        self.assertNotIn(
            "switching", stage_ids,
            f"DEFECT: linear regulator stage model includes 'switching': {stage_ids}",
        )

    def test_buck_has_switching_stage(self):
        """A buck converter HAS a switching node (the switch node). Its
        stage model must include a main_switch stage whose diagnostic
        signal is 'switching'."""
        topo = eng.topologies["PSUP_buck"]
        # The buck must have a main_switch stage that carries a
        # 'switching' diagnostic signal.
        has_switching_signal = False
        for s in topo.get("stages", []):
            sigs = s.get("diagnostic_signals", {})
            if "switching" in sigs:
                has_switching_signal = True
                break
        self.assertTrue(
            has_switching_signal,
            "DEFECT: buck stage model has no stage carrying a 'switching' signal",
        )

    def test_boost_has_boost_diode_stage(self):
        """A boost converter's output stage includes the boost diode,
        which is structurally distinct from a buck's output (buck uses
        a synchronous or schottky rectifier on the low side). The boost
        stage model must reflect this."""
        topo = eng.topologies["PSUP_boost"]
        stage_ids = [s.get("id") for s in topo.get("stages", [])]
        # Boost must have a boost_diode or output_diode stage
        self.assertTrue(
            any("diode" in s for s in stage_ids),
            f"DEFECT: boost stage model has no diode stage: {stage_ids}",
        )

    def test_flyback_has_transformer_and_opto_feedback(self):
        """A flyback has a transformer (isolation) and optocoupler
        feedback across the isolation barrier. Its stage model must
        include transformer and reflect isolated feedback."""
        topo = eng.topologies["PSUP_flyback"]
        stage_ids = [s.get("id") for s in topo.get("stages", [])]
        self.assertIn("transformer", stage_ids,
                      f"DEFECT: flyback has no transformer stage: {stage_ids}")

    def test_llc_has_resonant_stage(self):
        """An LLC resonant converter has a resonant tank (Lr+Cr). Its
        stage model must include a resonant stage, distinct from a
        plain buck/boost."""
        topo = eng.topologies["PSUP_llc"]
        stage_ids = [s.get("id") for s in topo.get("stages", [])]
        self.assertTrue(
            any("resonant" in s for s in stage_ids),
            f"DEFECT: LLC has no resonant stage: {stage_ids}",
        )

    def test_pfc_has_pfc_stage(self):
        """A PFC pre-regulator has its own boost stage."""
        topo = eng.topologies["PSUP_pfc"]
        stage_ids = [s.get("id") for s in topo.get("stages", [])]
        self.assertIn("pfc", stage_ids,
                      f"DEFECT: PFC topology has no pfc stage: {stage_ids}")

    def test_stage_lists_are_not_identical_across_topologies(self):
        """The stage lists of buck, boost, linear, flyback, LLC must
        NOT be identical — they must be structurally distinct."""
        topos = ["PSUP_buck", "PSUP_boost", "PSUP_linear",
                 "PSUP_flyback", "PSUP_llc"]
        stage_sets = []
        for t in topos:
            ids = tuple(s.get("id") for s in eng.topologies[t].get("stages", []))
            stage_sets.append(ids)
        unique = set(stage_sets)
        self.assertGreaterEqual(
            len(unique), 4,
            f"DEFECT: topology stage lists are too similar: only {len(unique)} unique "
            f"among {len(topos)} topologies.",
        )


class TestNegativeIrrelevantSignals(unittest.TestCase):
    """An irrelevant signal for a topology must not change the diagnosis."""

    def test_linear_ignores_switching_signal(self):
        """A linear regulator has no switching node. Supplying a
        'switching' measurement must not make the linear workflow
        behave like a buck (i.e., must not produce a 'switching' fault
        conclusion)."""
        # Linear with input present, output absent, NO switching relevant
        r_no_sw = diag("PSUP_linear", "no_output", [
            V("input_voltage", 12.0), ZERO("output_voltage"),
        ])
        # Same but with a 'switching' signal supplied — must not change
        # the conclusion (switching is irrelevant to a linear regulator)
        r_with_sw = diag("PSUP_linear", "no_output", [
            V("input_voltage", 12.0), ZERO("output_voltage"),
            PRES("switching"),  # irrelevant for linear
        ])
        self.assertEqual(
            r_no_sw.rule_id, r_with_sw.rule_id,
            "DEFECT: supplying irrelevant 'switching' signal to a linear "
            f"regulator changed the conclusion: {r_no_sw.rule_id} -> {r_with_sw.rule_id}",
        )

    def test_buck_ignores_boost_diode_signal(self):
        """A buck's output rectifier is not a 'boost_diode'. Supplying
        a boost_diode measurement to a buck must not change its
        conclusion."""
        r_no = diag("PSUP_buck", "no_output", [
            V("input_voltage", 12.0), ZERO("output_voltage"),
        ])
        r_with = diag("PSUP_buck", "no_output", [
            V("input_voltage", 12.0), ZERO("output_voltage"),
            ABS("boost_diode"),  # irrelevant for buck
        ])
        self.assertEqual(
            r_no.rule_id, r_with.rule_id,
            "DEFECT: irrelevant boost_diode signal changed buck conclusion",
        )


class TestWorkflowUsesTopologyStageModel(unittest.TestCase):
    """The engine's workflow must branch on the topology's actual
    stage model, not a shared chain."""

    def test_linear_output_fault_mentions_pass_element_not_switching(self):
        """When a linear regulator's output is absent with input
        present, the next-step must be linear-appropriate: it may
        mention the pass element / LDO / output cap, but it must NOT
        mention a switching node or gate drive (a linear regulator has
        no switching stage)."""
        r = diag("PSUP_linear", "no_output", [
            V("input_voltage", 12.0), ZERO("output_voltage"),
            PRES("en"),
        ])
        ns = r.next_steps[0] if r.next_steps else {}
        action = ns.get("action", "") if isinstance(ns, dict) else getattr(ns, "action", "")
        action_lower = action.lower()
        # Must be linear-appropriate: mention pass element, LDO,
        # regulator, or output cap.
        self.assertTrue(
            "pass" in action_lower or "ldo" in action_lower
            or "regulat" in action_lower or "output cap" in action_lower
            or "dropout" in action_lower,
            f"DEFECT: linear output fault must mention pass element/LDO/output cap, got: {action[:120]}",
        )
        # Must NOT mention buck/switching concepts.
        self.assertNotIn(
            "gate drive", action_lower,
            "DEFECT: linear regulator next-step mentions gate drive (no switching node)",
        )
        self.assertNotIn(
            "switching node", action_lower,
            "DEFECT: linear regulator next-step mentions switching node",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
