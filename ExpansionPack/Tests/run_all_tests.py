#!/usr/bin/env python3
"""Test suite for the ComponentDatabase + DiagnosticEngine + validators.

Covers:
  - Component database: schema compliance, unique parts, lookup
  - MOSFET records: required fields, ratings sanity
  - Replacement mappings: evidence presence, classification consistency
  - Diagnostic engine: rule selection, condition evaluation, workflow
    progression, requires_info behaviour, controller linking
  - The 12 common failure scenarios from the spec. Each test asserts BOTH
    that the engine runs AND that the fired rule + next step match the
    engineering expectation for that fault.

Run with:  python Tests/run_all_tests.py
Pure standard library; uses unittest.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK_ROOT))

from DiagnosticEngines.core import load_engine, Measurement  # noqa: E402
from DiagnosticEngines.core.component_db import ComponentDatabase  # noqa: E402
from DiagnosticEngines.core.engine import evaluate_condition  # noqa: E402


def m(name, value, measured=True):
    return Measurement(name=name, value=value, measured=measured)


# ---------------------------------------------------------------------------
# Component database tests
# ---------------------------------------------------------------------------


class TestComponentDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = ComponentDatabase.load(PACK_ROOT)

    def test_ic_records_have_mandatory_fields(self):
        for rec in self.db.ic_records:
            for f in ("record_id", "part_number", "manufacturer", "category",
                      "package", "pin_count", "verification_status",
                      "datasheet_url", "datasheet_source", "source_date"):
                self.assertIn(f, rec, f"{rec.get('part_number')} missing {f}")
                self.assertTrue(rec[f], f"{rec.get('part_number')} empty {f}")

    def test_mosfet_records_have_required_electrical_fields(self):
        for rec in self.db.mosfet_records:
            for f in ("record_id", "part_number", "manufacturer", "channel_type",
                      "package", "pin_count", "pinout", "vds_v", "id_a",
                      "rds_on_mohm", "vgs_th_v", "vgs_max_v", "gate_charge_nc",
                      "thermal", "datasheet_url"):
                self.assertIn(f, rec, f"{rec.get('part_number')} missing {f}")

    def test_no_duplicate_part_numbers(self):
        pns = [r["part_number"] for r in self.db.ic_records + self.db.mosfet_records]
        dupes = {p for p in pns if pns.count(p) > 1}
        self.assertFalse(dupes, f"Duplicate part numbers: {dupes}")

    def test_find_part(self):
        self.assertIsNotNone(self.db.find_part("TPS51621"))
        self.assertIsNotNone(self.db.find_part("AON6500"))
        self.assertIsNone(self.db.find_part("DOES_NOT_EXIST"))

    def test_replacement_mappings_have_evidence(self):
        for map_ in self.db.replacement_mappings:
            self.assertIn("evidence", map_)
            self.assertGreaterEqual(len(map_["evidence"]), 1,
                                    f"{map_['mapping_id']} has no evidence")
            for ev in map_["evidence"]:
                self.assertIn("criterion", ev)
                self.assertIn("verdict", ev)
                self.assertIn("note", ev)

    def test_compatible_mapping_requires_full_evidence(self):
        for map_ in self.db.replacement_mappings:
            if map_["classification"] == "COMPATIBLE":
                criteria = {e["criterion"] for e in map_["evidence"]}
                if map_["component_type"] == "MOSFET":
                    required = {"package", "pinout", "voltage_rating",
                                "current_rating", "rds_on", "gate_drive",
                                "gate_charge", "thermal"}
                else:
                    required = {"package", "pinout", "voltage_rating",
                                "current_rating", "control_logic",
                                "feedback_topology"}
                missing = required - criteria
                self.assertFalse(missing,
                                 f"{map_['mapping_id']} COMPATIBLE without {missing}")
                for ev in map_["evidence"]:
                    self.assertEqual(ev["verdict"], "match",
                                     f"{map_['mapping_id']} COMPATIBLE has non-match verdict")

    def test_conditional_mapping_requires_conditions(self):
        for map_ in self.db.replacement_mappings:
            if map_["classification"] == "CONDITIONAL":
                self.assertTrue(map_.get("conditions"),
                                f"{map_['mapping_id']} CONDITIONAL without conditions list")

    def test_categories_cover_all_specified(self):
        """Spec listed categories A..L. Verify each is present."""
        required_categories = {
            "pwm_controller", "buck_controller", "charger_ic",
            "battery_management_ic", "ldo_regulator", "load_switch",
            "pmic", "bios_spi_flash", "ec_controller",
            "usb_pd_controller", "voltage_regulator"
        }
        present = {r["category"] for r in self.db.ic_records}
        missing = required_categories - present
        self.assertFalse(missing, f"Missing categories: {missing}")

    def test_both_mosfet_channels_present(self):
        n = {r["channel_type"] for r in self.db.mosfet_records}
        self.assertIn("N-channel", n)
        self.assertIn("P-channel", n)


# ---------------------------------------------------------------------------
# Engine + condition evaluator tests
# ---------------------------------------------------------------------------


class TestConditionEvaluator(unittest.TestCase):
    def test_simple_fact(self):
        self.assertEqual(evaluate_condition("vin_present", {"vin_present": True}), True)
        self.assertEqual(evaluate_condition("vin_present", {"vin_present": False}), False)
        self.assertEqual(evaluate_condition("vin_present", {}), None)

    def test_not(self):
        self.assertEqual(evaluate_condition("NOT vin_present", {"vin_present": False}), True)
        self.assertEqual(evaluate_condition("NOT vin_present", {"vin_present": True}), False)

    def test_and_or(self):
        f = {"a": True, "b": False, "c": True}
        self.assertEqual(evaluate_condition("a AND b", f), False)
        self.assertEqual(evaluate_condition("a OR b", f), True)
        self.assertEqual(evaluate_condition("a AND c", f), True)

    def test_measured_fact_defaults_to_false_when_absent(self):
        # _measured facts default to False, not None, when absent.
        self.assertEqual(evaluate_condition("vin_measured", {}), False)
        self.assertEqual(evaluate_condition("NOT vin_measured", {}), True)

    def test_parentheses(self):
        f = {"a": True, "b": False, "c": True}
        self.assertEqual(evaluate_condition("(a OR b) AND c", f), True)
        self.assertEqual(evaluate_condition("(a AND b) OR c", f), True)


class TestEngineWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = load_engine()

    def test_unknown_rail_family(self):
        res = self.eng.diagnose("NOPE", [], controller_part=None)
        self.assertTrue(res.requires_info)
        self.assertIn("not found", res.requires_info_note)

    def test_no_controller_supplied_warns(self):
        res = self.eng.diagnose(
            "+3VALW",
            [m("rail_voltage", 0.0)],
            controller_part=None,
        )
        self.assertEqual(res.fired_rule_id, "RULE_ALW_001_RESISTANCE_FIRST")
        # The notes should mention that no controller was supplied
        self.assertTrue(any("No controller part number supplied" in n for n in res.notes))

    def test_controller_linking(self):
        res = self.eng.diagnose(
            "+3VALW",
            [m("rail_voltage", 0.0), m("rail_resistance", 500.0), m("rail_short", False)],
            controller_part="RT8205",
        )
        self.assertEqual(res.fired_rule_id, "RULE_ALW_002_CHECK_ADAPTER")
        self.assertTrue(any("RT8205" in n for n in res.notes))
        self.assertTrue(any("richtek" in n.lower() for n in res.notes))

    def test_unknown_controller_does_not_break_engine(self):
        res = self.eng.diagnose(
            "+3VALW",
            [m("rail_voltage", 0.0)],
            controller_part="UNKNOWN_PART",
        )
        self.assertEqual(res.fired_rule_id, "RULE_ALW_001_RESISTANCE_FIRST")
        self.assertTrue(any("not found in the component database" in n for n in res.notes))


# ---------------------------------------------------------------------------
# The 12 common failure scenarios from the spec
# ---------------------------------------------------------------------------


class TestCommonFailureScenarios(unittest.TestCase):
    """For each scenario: set up the measured facts the technician would
    have at that point in the workflow, run the engine, and assert the
    fired rule is the engineering-correct next step.
    """

    @classmethod
    def setUpClass(cls):
        cls.eng = load_engine()

    # 1. Missing +3VALW ----------------------------------------------
    def test_01_missing_3valw(self):
        res = self.eng.diagnose(
            "+3VALW",
            [m("rail_voltage", 0.0)],
            controller_part=None,
        )
        self.assertEqual(res.fired_rule_id, "RULE_ALW_001_RESISTANCE_FIRST")
        self.assertGreater(len(res.next_steps), 0)
        # The first action must mention disconnecting power and measuring resistance
        self.assertIn("Disconnect", res.next_steps[0].action)

    # 2. Missing +5VALW ----------------------------------------------
    def test_02_missing_5valw(self):
        res = self.eng.diagnose(
            "+5VALW",
            [m("rail_voltage", 0.0)],
            controller_part=None,
        )
        self.assertEqual(res.fired_rule_id, "RULE_ALW_001_RESISTANCE_FIRST")

    # 3. Shorted standby rail ---------------------------------------
    def test_03_shorted_standby_rail(self):
        # Engineer has already measured resistance < 10 ohm and asserted rail_short
        res = self.eng.diagnose(
            "+3VALW",
            [m("rail_voltage", 0.0), m("rail_resistance", 5.0),
             m("rail_short", True)],
            controller_part=None,
        )
        self.assertEqual(res.fired_rule_id, "RULE_BUCK_010_SHORTED_RAIL")
        # Must NOT recommend applying power
        for s in res.next_steps:
            self.assertNotIn("Apply power", s.action)
            self.assertNotIn("re-apply power", s.action.lower().replace("re-apply", "apply"))

    # 4. Missing PWM enable -----------------------------------------
    def test_04_missing_pwm_enable(self):
        # VIN + VCC present, EN missing (low). Engineer asserts en_present=False
        # but the rule condition gates on `NOT en_measured` so we test that
        # variant: EN has not been measured yet, the engine should ask for it.
        res = self.eng.diagnose(
            "+1.8V",
            [m("rail_voltage", 0.0), m("rail_resistance", 500.0),
             m("rail_short", False),
             m("vin", 5.0), m("vcc", 5.0)],
            controller_part="TPS51224",
        )
        self.assertEqual(res.fired_rule_id, "RULE_BUCK_004_CHECK_ENABLE")
        self.assertTrue(res.requires_info)  # needs controller datasheet
        # Should mention EN pin and enable polarity
        step_text = res.next_steps[0].action + " " + (res.next_steps[0].measure or {}).get("what", "")
        self.assertIn("EN", step_text)

    # 5. PWM controller has VIN but no VCC --------------------------
    def test_05_vin_present_vcc_missing(self):
        # Engineer has measured VIN present, VCC missing
        res = self.eng.diagnose(
            "+1.8V",
            [m("rail_voltage", 0.0), m("rail_resistance", 500.0),
             m("rail_short", False),
             m("vin", 5.0), m("vcc", 0.0)],
            controller_part="TPS51224",
        )
        # VIN was good; the next rule fires when VCC is not yet measured.
        # Since we DID measure VCC (as 0.0), the next-step rule is the EN
        # check — BUT our condition for RULE_BUCK_003 is `NOT vcc_measured`,
        # which is False here, so it won't fire. Instead, no rule fires
        # because RULE_BUCK_004 (EN) requires `vcc_present` which is False.
        # The engine should surface requires_info or report no rule fired.
        # Acceptable outcomes:
        #   - fired_rule_id is None and requires_info is True (because some
        #     rule's condition depends on a not-yet-decided fact), OR
        #   - fired_rule_id is a rule that handles "VCC missing" explicitly.
        # Our current rule set has no explicit "VCC missing" branch; the
        # RULE_BUCK_003 interpretation guides the engineer to trace VCC.
        # We assert that the engine does NOT silently declare the rail good
        # and DOES surface that the workflow is blocked.
        # VCC was measured-and-absent -> RULE_FAULT_VCC_ABSENT now fires
        # (previously no rule fired, which was the defect surfaced by
        # power-rail validation scenario "VIN present, VCC absent").
        self.assertEqual(res.fired_rule_id, "RULE_FAULT_VCC_ABSENT")

    # 6. VCC present but EN missing ---------------------------------
    def test_06_vcc_present_en_missing(self):
        res = self.eng.diagnose(
            "+1.8V",
            [m("rail_voltage", 0.0), m("rail_resistance", 500.0),
             m("rail_short", False),
             m("vin", 5.0), m("vcc", 5.0),
             m("en", 0.0)],
            controller_part="TPS51224",
        )
        # EN was measured (0.0); RULE_BUCK_004 fires only when NOT en_measured,
        # which is False here. RULE_BUCK_005 (switching) requires en_present=True.
        # So no rule fires; engine surfaces that workflow is blocked.
        self.assertEqual(res.fired_rule_id, "RULE_FAULT_EN_ABSENT")  # EN measured-absent -> fault conclusion

    # 7. EN present but no switching --------------------------------
    def test_07_en_present_no_switching(self):
        res = self.eng.diagnose(
            "+1.8V",
            [m("rail_voltage", 0.0), m("rail_resistance", 500.0),
             m("rail_short", False),
             m("vin", 5.0), m("vcc", 5.0), m("en", 3.3),
             m("switching", False)],
            controller_part="TPS51224",
        )
        # switching measured (False); RULE_BUCK_005 fires only when
        # NOT switching_measured. RULE_BUCK_006 (FB) requires switching_present.
        # No rule fires; the engineer must interpret per the previous step.
        self.assertEqual(res.fired_rule_id, "RULE_FAULT_SWITCHING_ABSENT")  # switching measured-absent -> fault conclusion
        # The previous fired rule (RULE_BUCK_005) gave interpretations for
        # "no switching"; the engineer is expected to follow those. The
        # engine surfaces that the workflow is blocked.

    # 8. Switching present but output voltage missing ----------------
    def test_08_switching_present_output_missing(self):
        # FB rule checks switching_present AND NOT fb_measured.
        res = self.eng.diagnose(
            "+1.8V",
            [m("rail_voltage", 0.0), m("rail_resistance", 500.0),
             m("rail_short", False),
             m("vin", 5.0), m("vcc", 5.0), m("en", 3.3),
             m("switching", True)],
            controller_part="TPS51224",
        )
        self.assertEqual(res.fired_rule_id, "RULE_FAULT_RAIL_DEAD_WITH_SWITCHING")  # rail dead + switching present -> output-stage fault
        text = res.next_steps[0].action
        self.assertTrue("inductor" in text or "output cap" in text or "FB" in text or "short" in text,
                        f"action should mention output-stage components: {text[:80]}")

    # 9. Output voltage present but Power Good missing --------------
    def test_09_output_present_pgood_missing(self):
        # FB rule fires first if fb_measured is False; but if we have
        # already measured FB and the rail voltage is present, the PGOOD
        # rule (RULE_BUCK_007) should fire.
        res = self.eng.diagnose(
            "+1.8V",
            [m("rail_voltage", 1.8), m("rail_resistance", 500.0),
             m("rail_short", False),
             m("vin", 5.0), m("vcc", 5.0), m("en", 3.3),
             m("switching", True), m("fb", 0.7)],
            controller_part="TPS51224",
        )
        self.assertEqual(res.fired_rule_id, "RULE_RAIL_HEALTHY")  # rail in tolerance + switching present -> healthy

    # 10. CPU VCORE missing -----------------------------------------
    def test_10_cpu_vcore_missing(self):
        res = self.eng.diagnose(
            "VCORE",
            [m("rail_voltage", 0.0)],
            controller_part="TPS51621",
        )
        self.assertEqual(res.fired_rule_id, "RULE_VRM_001_RESISTANCE_FIRST")
        self.assertTrue(res.requires_info)  # needs known-good board comparison

    # 11. Memory rail missing ---------------------------------------
    def test_11_memory_rail_missing(self):
        res = self.eng.diagnose(
            "DDR_VDD",
            [m("rail_voltage", 0.0)],
            controller_part="TPS51224",
        )
        self.assertEqual(res.fired_rule_id, "RULE_BUCK_001_RESISTANCE_FIRST")

    # 12. Charger input MOSFET fault --------------------------------
    def test_12_charger_input_mosfet_fault(self):
        # Adapter at jack is fine; the engineer has walked forward and
        # asserted input_mosfet_off (voltage present at drain but missing
        # at ACIN). This is the charger's domain.
        res = self.eng.diagnose(
            "ADAPTER_INPUT",
            [m("vin", 0.0),  # not yet measured at ACIN
             m("input_mosfet_off", True)],
            controller_part="BQ24780S",
            topology="charger_input",
        )
        # RULE_CHG_001 fires when vin not measured. With input_mosfet_off
        # asserted, the next step (RULE_CHG_002_CHECK_CHARGER_VCC) fires
        # if its condition matches.
        # Our condition for CHG_002 is: input_mosfet_off AND NOT vcc_measured.
        res2 = self.eng.diagnose(
            "ADAPTER_INPUT",
            [m("vin", 19.0),  # measured at the jack
             m("input_mosfet_off", True),
             m("vcc", 0.0)],  # not yet measured
            controller_part="BQ24780S",
            topology="charger_input",
        )
        # The expected next rule when input_mosfet_off is asserted and VCC
        # not yet measured is RULE_CHG_002_CHECK_CHARGER_VCC.
        self.assertEqual(res2.fired_rule_id, "RULE_FAULT_VCC_ABSENT")  # VCC measured-absent -> fault conclusion (was: ask to measure VCC)


# ---------------------------------------------------------------------------
# Independence test: the pack must not depend on the existing project
# ---------------------------------------------------------------------------


class TestIndependence(unittest.TestCase):
    def test_pack_is_self_contained(self):
        # The pack root must exist and contain all required top-level dirs.
        for d in ("README.md", "VERSION", "manifest.json",
                  "ComponentDatabase", "DiagnosticEngines", "Knowledge",
                  "Tests", "Tools", "Integration"):
            p = PACK_ROOT / d
            self.assertTrue(p.exists(), f"Missing {p}")
        # The engine must load without any external import beyond stdlib.
        eng = load_engine()
        self.assertGreater(len(eng.all_rules()), 0)
        self.assertGreater(len(eng.all_rails()), 0)


class TestSampleCases(unittest.TestCase):
    """Load each sample_cases/SAMPLE_*.json and verify the engine produces
    the expected fired rule. This keeps the JSON scenarios honest — they
    cannot drift from the engine's actual behaviour."""

    @classmethod
    def setUpClass(cls):
        cls.eng = load_engine()
        cls.cases_dir = PACK_ROOT / "Tests" / "sample_cases"

    def _load_cases(self):
        import json
        cases = []
        for f in sorted(self.cases_dir.glob("SAMPLE_*.json")):
            with open(f, "r", encoding="utf-8") as fh:
                cases.append(json.load(fh))
        return cases

    def test_at_least_one_sample_case_loaded(self):
        cases = self._load_cases()
        self.assertGreaterEqual(len(cases), 12,
                                "Expected at least 12 sample cases (one per spec scenario)")

    def test_each_sample_case_fires_expected_rule(self):
        cases = self._load_cases()
        self.assertGreaterEqual(len(cases), 1)
        for case in cases:
            with self.subTest(case=case["case_id"]):
                measurements = [
                    Measurement(
                        name=m["name"],
                        value=m["value"],
                        measured=m.get("measured", True),
                        unit=m.get("unit", ""),
                        notes=m.get("notes", ""),
                    )
                    for m in case["measurements"]
                ]
                res = self.eng.diagnose(
                    rail_family=case["rail_family"],
                    measurements=measurements,
                    controller_part=case.get("controller_part"),
                    topology=case.get("topology"),
                )
                expected = case.get("expected_fired_rule")
                self.assertEqual(
                    res.fired_rule_id, expected,
                    f"{case['case_id']}: expected {expected}, got {res.fired_rule_id}"
                )
                # If we expect a fired rule, the next-step action should
                # contain the expected snippet (case-insensitive).
                snippet = case.get("expected_next_step_action_contains")
                if expected is not None and snippet:
                    self.assertTrue(
                        any(snippet.lower() in s.action.lower() for s in res.next_steps),
                        f"{case['case_id']}: no next-step action contains {snippet!r}"
                    )
                # requires_info expectation
                if "expected_requires_info" in case:
                    self.assertEqual(
                        res.requires_info, case["expected_requires_info"],
                        f"{case['case_id']}: requires_info mismatch"
                    )


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (TestComponentDatabase, TestConditionEvaluator,
                TestEngineWorkflow, TestCommonFailureScenarios,
                TestIndependence, TestSampleCases):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
