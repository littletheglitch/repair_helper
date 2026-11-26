#!/usr/bin/env python3
"""Phase 4 test suite — deepened motherboard power-sequence diagnosis.

Verifies:
  - Explicit stage dependencies (depends_on)
  - Missing-vs-failed distinction (unmeasured ≠ failed)
  - No single-output IC-failure inference (alternative hypotheses)
  - Evidence-ranked hypotheses (primary STRONG + alternatives WEAK)
  - Next-measurement by info value + safety
  - Dependency caveat when upstream is unverified
  - Platform variations (laptop vs desktop)
  - New stages: adapter detection, input protection, charger IC,
    battery path, RTC, VRM GPU, BIOS/SPI, RAM init

Run: python Tests/test_phase4_motherboard.py
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from DiagnosticEngines.core.power_on_sequence import (
    PowerOnSequenceEngine, PowerOnSequenceInput,
)
from DiagnosticEngines.core.framework import (
    Measurement as FM, MeasurementKind as MK, MeasurementState as MS,
    MeasurementCondition as MC,
)


def V(n, v):
    return FM(name=n, kind=MK.VOLTAGE, state=MS.IN_RANGE, value=v,
              condition=MC.POWERED)


def Z(n):
    return FM(name=n, kind=MK.VOLTAGE, state=MS.MEASURED_ZERO, value=0.0,
              condition=MC.POWERED)


def L(n, v=True):
    return FM(name=n, kind=MK.LOGIC_LEVEL, state=MS.IN_RANGE, value=v,
              condition=MC.POWERED)


class TestStageDependencies(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = PowerOnSequenceEngine()

    def test_laptop_stages_have_depends_on(self):
        stages = self.eng.stages_for("laptop")
        self.assertGreater(len(stages), 10)
        for s in stages:
            self.assertIsInstance(s.depends_on, list,
                f"{s.stage_id} missing depends_on")

    def test_new_stages_present(self):
        stages = {s.stage_id for s in self.eng.stages_for("laptop")}
        for sid in ("adapter_input", "input_protection", "charger_ic",
                    "battery_path", "rtc", "ec_alive", "power_button",
                    "main_enables", "memory", "cpu_vrm", "vrm_gpu",
                    "clock_reset", "bios_spi", "ram_init", "post",
                    "display"):
            self.assertIn(sid, stages, f"missing stage {sid}")

    def test_dependency_chain_is_acyclic(self):
        """The dependency graph must be acyclic (no circular deps)."""
        stages = {s.stage_id: s for s in self.eng.stages_for("laptop")}
        visiting = set()
        def visit(sid):
            if sid in visiting:
                return False
            visiting.add(sid)
            for dep in stages[sid].depends_on:
                if dep in stages and not visit(dep):
                    return False
            visiting.discard(sid)
            return True
        for sid in stages:
            self.assertTrue(visit(sid), f"circular dependency at {sid}")


class TestMissingVsFailed(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = PowerOnSequenceEngine()

    def test_unmeasured_stage_not_failed(self):
        """A stage with NO measurements supplied must NOT be reported
        as FAILED — it's missing data, not a confirmed failure."""
        # Only adapter_input supplied; everything else unmeasured.
        r = self.eng.diagnose(PowerOnSequenceInput(
            platform="laptop",
            measurements=[V("adapter_input_voltage", 19.0)]))
        # Should be NEED_DATA or healthy, NOT FAIL.
        self.assertNotIn("FAIL", r.rule_id or "",
            f"unmeasured stages must not be FAIL: {r.rule_id}")

    def test_measured_zero_is_failed(self):
        """A MEASURED_ZERO on a confirm measurement IS a failure."""
        r = self.eng.diagnose(PowerOnSequenceInput(
            platform="laptop",
            measurements=[V("adapter_input_voltage", 19.0),
                          V("post_fuse_voltage", 19.0),
                          Z("charger_vcc"),  # measured zero = failed
                          V("charger_acin", 19.0)]))
        self.assertIn("FAIL_charger_ic", r.rule_id or "")


class TestNoSingleOutputICFailureInference(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = PowerOnSequenceEngine()

    def test_alternative_hypotheses_surfaced(self):
        """When a stage fails, the conclusion must surface ALTERNATIVE
        hypotheses — not just 'the IC is dead'. A single missing
        output does not prove the IC is defective."""
        r = self.eng.diagnose(PowerOnSequenceInput(
            platform="laptop",
            measurements=[V("adapter_input_voltage", 19.0),
                          V("post_fuse_voltage", 19.0),
                          Z("charger_vcc"), V("charger_acin", 19.0)]))
        self.assertTrue(any("ALTERNATIVE" in n for n in r.notes),
            f"alternative hypotheses not surfaced; notes={r.notes}")

    def test_evidence_ranked_hypotheses(self):
        """The primary hypothesis is STRONG; alternatives are WEAK."""
        r = self.eng.diagnose(PowerOnSequenceInput(
            platform="laptop",
            measurements=[V("adapter_input_voltage", 19.0),
                          V("post_fuse_voltage", 19.0),
                          Z("charger_vcc"), V("charger_acin", 19.0)]))
        self.assertGreater(len(r.new_hypotheses), 1,
            "must have primary + alternative hypotheses")
        # Primary (first) is STRONG; at least one alternative is WEAK.
        strengths = []
        for h in r.new_hypotheses:
            for e in h.evidence:
                strengths.append(e.strength)
        from DiagnosticEngines.core.framework import EvidenceStrength
        self.assertIn(EvidenceStrength.STRONG, strengths)
        self.assertIn(EvidenceStrength.WEAK, strengths)


class TestNextMeasurementByInfoValue(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = PowerOnSequenceEngine()

    def test_next_measurement_in_action(self):
        """The failed-stage action must include a RECOMMENDED NEXT
        MEASUREMENT with an info-value explanation."""
        r = self.eng.diagnose(PowerOnSequenceInput(
            platform="laptop",
            measurements=[V("adapter_input_voltage", 19.0),
                          V("post_fuse_voltage", 19.0),
                          Z("charger_vcc"), V("charger_acin", 19.0)]))
        self.assertTrue(r.next_steps)
        action = r.next_steps[0]["action"]
        self.assertIn("RECOMMENDED NEXT MEASUREMENT", action)
        self.assertIn("Why:", action,
            "next measurement must explain WHY (info value)")


class TestDependencyCaveat(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = PowerOnSequenceEngine()

    def test_dependency_caveat_when_upstream_unverified(self):
        """When a stage fails but an upstream dependency is unmeasured,
        the conclusion must surface a DEPENDENCY CAVEAT — the
        localization is provisional."""
        # cpu_vrm fails (vcore=0) but memory (a dependency) is unmeasured.
        r = self.eng.diagnose(PowerOnSequenceInput(
            platform="laptop",
            measurements=[V("adapter_input_voltage", 19.0),
                          V("post_fuse_voltage", 19.0),
                          V("charger_vcc", 5.0), V("charger_acin", 19.0),
                          V("plus_3valw_voltage", 3.3),
                          V("plus_5valw_voltage", 5.0),
                          V("vccrtc_voltage", 3.0),
                          L("ec_heartbeat"), L("power_button_pressed"),
                          L("ec_responded_to_button"), L("main_enables_high"),
                          Z("vcore_voltage")]))  # cpu_vrm failed
        self.assertIn("FAIL_cpu_vrm", r.rule_id or "")
        self.assertTrue(any("DEPENDENCY CAVEAT" in n for n in r.notes),
            f"dependency caveat not surfaced; notes={r.notes}")


class TestPlatformVariations(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = PowerOnSequenceEngine()

    def test_laptop_and_desktop_differ(self):
        """Laptop and desktop stages must differ (not identical)."""
        lap = {s.stage_id for s in self.eng.stages_for("laptop")}
        desk = {s.stage_id for s in self.eng.stages_for("desktop")}
        self.assertNotEqual(lap, desk,
            "laptop and desktop stages must differ")

    def test_laptop_has_charger_desktop_has_atx(self):
        lap = {s.stage_id for s in self.eng.stages_for("laptop")}
        desk = {s.stage_id for s in self.eng.stages_for("desktop")}
        self.assertIn("charger_ic", lap)
        self.assertIn("atx_5vsb", desk)

    def test_desktop_sequence_has_explicit_platform_stages(self):
        desk = {s.stage_id for s in self.eng.stages_for("desktop")}
        self.assertEqual(sum(1 for stage in self.eng.stages_for("desktop") if not stage.optional), 14)
        self.assertTrue({"ps_on", "main_rails", "pwr_ok", "eps12v", "cpu_vrm", "memory", "pch", "bios_spi", "pcie_gpu_aux", "post", "display"}.issubset(desk))
        optional = {stage.stage_id for stage in self.eng.stages_for("desktop") if stage.optional}
        self.assertEqual(optional, {"peripheral_usb", "peripheral_audio", "peripheral_lan"})


class TestBackwardCompat(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.eng = PowerOnSequenceEngine()

    def test_old_measurement_set_still_works(self):
        """A measurement set using only the OLD names (adapter_input,
        standby, ec, etc.) must still produce a valid diagnosis —
        the new early stages are skipped when unmeasured."""
        r = self.eng.diagnose(PowerOnSequenceInput(
            platform="laptop",
            measurements=[V("adapter_input_voltage", 19.0),
                          V("plus_3valw_voltage", 3.3),
                          V("plus_5valw_voltage", 5.0),
                          L("ec_heartbeat"), L("power_button_pressed"),
                          L("ec_responded_to_button"), L("main_enables_high"),
                          V("ddr_vdd_voltage", 1.2), V("vcore_voltage", 1.0),
                          L("reset_deasserted"), L("cpu_clock_present"),
                          L("post_progressing"),
                          L("display_backlight_on"), L("display_data_present")]))
        # Should be HEALTHY (all old-name stages confirmed; new stages
        # with no measurements skipped).
        self.assertIn("HEALTHY", r.rule_id or "",
            f"old measurement set should be healthy; got {r.rule_id}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
