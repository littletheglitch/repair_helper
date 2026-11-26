"""Power Supply Diagnostic Engine.

Diagnoses faults in standalone power supplies (ATX PSU, laptop adapter,
battery charger, DC-DC module, and the 12 SMPS topologies). This engine
is separate from the motherboard engine: a power supply is a self-
contained unit with its own input protection, switching, transformer,
and feedback chain.

Safety is the dominant concern. Mains-powered topologies (flyback,
forward, push-pull, half/full-bridge, LLC, PFC, ATX, laptop adapter)
have a live primary side. The engine:

  * Refuses to recommend primary-side probing. The SafetyPolicy blocks
    any next step that mentions 'mains', 'primary side', or 'line voltage'.
  * Always recommends secondary-side (isolated, low-voltage) measurements
    first.
  * For a no-output symptom on a mains PSU, the first recommended test is
    to measure the secondary-side output rails with the PSU unplugged from
    mains and the bulk cap discharged, THEN to check the standby rail at
    the output connector with mains applied — never to probe the primary.

The engine produces framework EngineConclusion objects so the host can
merge results into a DiagnosticSession.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import json
import sys

PACK_ROOT = Path(__file__).resolve().parents[2]
if str(PACK_ROOT) not in sys.path:
    sys.path.insert(0, str(PACK_ROOT))

from DiagnosticEngines.core.framework import (  # noqa: E402
    Measurement, MeasurementState, MeasurementKind, MeasurementCondition,
    Hypothesis, Evidence, EvidenceType, EvidenceStrength,
    SafetyPolicy, SafetyLevel,
)
from DiagnosticEngines.core.framework.session import EngineConclusion  # noqa: E402


@dataclass
class PowerSupplyDiagnosisInput:
    """Inputs the host passes to the power-supply engine."""
    topology_id: str                          # one of PSUP_* from the topology files
    symptom: str                              # one of the fault_patterns, e.g. 'no_output'
    measurements: list[Measurement] = field(default_factory=list)
    notes: str = ""


class PowerSupplyEngine:
    """Specialized engine for standalone power supplies."""

    def __init__(self, pack_root: Optional[Path] = None):
        self.pack_root = pack_root or PACK_ROOT
        self.topologies: dict[str, dict] = {}
        self._load_topologies()
        self.safety = SafetyPolicy()

    def _load_topologies(self) -> None:
        topo_dir = self.pack_root / "DiagnosticEngines" / "PowerSupply" / "topologies"
        if not topo_dir.exists():
            return
        for f in sorted(topo_dir.glob("*.json")):
            with open(f, "r", encoding="utf-8") as fh:
                t = json.load(fh)
            self.topologies[t["topology_id"]] = t

    # --- public API ----------------------------------------------------

    def supported_topologies(self) -> list[str]:
        return sorted(self.topologies.keys())

    def supported_symptoms(self, topology_id: str) -> list[str]:
        t = self.topologies.get(topology_id)
        if not t:
            return []
        return [fp["pattern"] for fp in t.get("fault_patterns", [])]

    def diagnose(self, inp: PowerSupplyDiagnosisInput) -> EngineConclusion:
        """Produce a diagnostic conclusion for a power-supply fault."""
        conclusion = EngineConclusion(
            engine="power_supply",
            rule_id=None,
            next_steps=[],
        )

        # 1. Validate topology
        topo = self.topologies.get(inp.topology_id)
        if topo is None:
            conclusion.requires_info = True
            conclusion.requires_info_note = (
                f"Unknown topology {inp.topology_id!r}. "
                f"Supported: {self.supported_topologies()}"
            )
            return conclusion

        conclusion.notes.append(f"Topology: {topo['name']} (mains_powered={topo['mains_powered']}, isolated={topo['isolated']})")

        # 2. Match symptom to fault pattern
        fault = next((fp for fp in topo.get("fault_patterns", [])
                      if fp["pattern"] == inp.symptom), None)
        if fault is None:
            conclusion.requires_info = True
            conclusion.requires_info_note = (
                f"Unknown symptom {inp.symptom!r} for topology {inp.topology_id!r}. "
                f"Supported: {self.supported_symptoms(inp.topology_id)}"
            )
            return conclusion
        conclusion.rule_id = f"PSUP_{inp.topology_id}_{inp.symptom}"
        conclusion.notes.append(f"Fault pattern: {inp.symptom}. Likely stages: {fault['likely_stages']}. {fault['notes']}")

        # Phase 2: top-level fault-type classification. Surface the
        # failure MODE (startup/protection/no-switch/unstable/overload/
        # output-stage) regardless of whether a stage localizes. This
        # gives the technician the narrative even when measurements are
        # incomplete (the stage walk may not fire, but the classifier
        # can still identify the mode from whatever was measured).
        by_name_top = {m.name: m for m in inp.measurements}
        fault_type = self._classify_fault_type(by_name_top, topo, inp.symptom)
        if fault_type != "unknown":
            conclusion.notes.append(
                f"Fault TYPE (preliminary): {fault_type}. "
                f"This classifies the failure MODE from the measured "
                f"signals; the stage walk below localizes the LOCATION."
            )

        # 3. Build the next-step chain. Mains topologies get a hard
        #    safety gate first; non-mains topologies start at input protection.
        mains = topo.get("mains_powered", False)
        if mains:
            conclusion = self._build_mains_workflow(conclusion, topo, fault, inp)
        else:
            conclusion = self._build_low_voltage_workflow(conclusion, topo, fault, inp)

        # 4. Add a hypothesis capturing the current suspicion.
        h = Hypothesis(
            statement=f"Power supply fault: {inp.symptom} on {topo['name']}. "
                      f"Likely stage(s): {', '.join(fault['likely_stages'])}.",
            engine="power_supply",
            rule_source=conclusion.rule_id,
        )
        # Background evidence: the symptom itself.
        h.add_evidence(Evidence(
            type=EvidenceType.BACKGROUND,
            strength=EvidenceStrength.WEAK,
            description=f"Observed symptom: {inp.symptom}",
            rule_source=conclusion.rule_id,
            source_kind="observation",
        ))
        conclusion.new_hypotheses.append(h)

        # 5. Safety-check the next steps.
        self._safety_check(conclusion)

        return conclusion

    # --- workflow builders --------------------------------------------

    def _build_mains_workflow(self, conclusion: EngineConclusion, topo: dict,
                              fault: dict, inp: PowerSupplyDiagnosisInput) -> EngineConclusion:
        """Mains PSU workflow. Hard safety gate first, then secondary-side
        measurements only. The engine never recommends primary-side probing."""
        # Hard safety gate as step 1.
        conclusion.next_steps.append({
            "step": 1,
            "action": (
                "SAFETY GATE: This is a mains-powered power supply. The primary "
                "side (input fuse, bridge rectifier, primary bulk cap, PWM "
                "controller, main switch, primary side of transformer) is at "
                "LIVE MAINS potential whenever the unit is plugged in. Do NOT "
                "probe the primary side. Unplug from mains. Wait 60 s and "
                "verify with a DMM that the primary bulk cap is discharged "
                "below 30 V before any further work."
            ),
            "measure": {
                "what": "Primary bulk capacitor voltage (discharge verification)",
                "where": "Primary bulk capacitor + and - terminals",
                "instrument": "DMM DC volts, 1000 V range",
                "expected": "Below 30 V after 60 s unplugged. If higher, the bleeder resistor is open or missing; wait longer or discharge through a proper bleeder.",
                "safety": "MAINS: never touch the primary side. Discharge verification only, with the unit UNPLUGGED."
            },
            "requires_info": False,
            "_stage": "safety_gate",
        })

        # Step 2: secondary-side output check (with mains applied, measuring
        # at the OUTPUT connector only — the secondary is isolated and safe).
        conclusion.next_steps.append({
            "step": 2,
            "action": (
                "With the PSU unplugged from mains and the output connector "
                "disconnected from any load, plug the PSU into mains. Measure "
                "the output voltage at the output connector. This is the "
                "secondary side, isolated from mains by the transformer."
            ),
            "measure": {
                "what": "Output voltage at the PSU output connector",
                "where": "Output connector (e.g. ATX 24-pin for an ATX PSU, barrel jack for a laptop adapter)",
                "instrument": "DMM DC volts",
                "expected": "Nominal output voltage per the PSU label (e.g. 19 V for a laptop adapter, 5VSB + 12V + 5V + 3.3V for an ATX PSU).",
                "safety": "Secondary side only. Do NOT open the PSU case; primary side stays live inside the case."
            },
            "interpretations": [
                {"if_result": "Output voltage present and correct", "then": "PSU output is good. The fault is in the load or the cable, not the PSU.", "set_facts": ["output_good=True"]},
                {"if_result": "Output voltage missing", "then": "PSU is not producing output. Proceed to step 3 (standby/auxiliary rail check).", "set_facts": ["output_missing=True"], "next_rule": f"PSUP_{inp.topology_id}_{inp.symptom}_STEP3"},
                {"if_result": "Output voltage present but wrong (low/high/pulsating)", "then": "PSU is running but out of regulation. Likely feedback or output-filter fault. The fault pattern matches: " + inp.symptom + ".", "set_facts": ["output_wrong=True"]}
            ],
            "requires_info": False,
            "_stage": "secondary_output_check",
        })

        # Step 3: auxiliary/standby rail check (still secondary side).
        conclusion.next_steps.append({
            "step": 3,
            "action": (
                "If the main output is missing, check the auxiliary/standby "
                "rail. For ATX PSUs this is 5VSB (pin 9 of the 24-pin). For "
                "laptop adapters there is usually no standby rail — skip this "
                "step. The auxiliary rail tells you whether the controller's "
                "startup circuit is alive."
            ),
            "measure": {
                "what": "Auxiliary / standby rail voltage",
                "where": "Standby output (ATX 5VSB) or the controller's VCC auxiliary winding (secondary side only)",
                "instrument": "DMM DC volts",
                "expected": "5VSB = 5.0 V for ATX; otherwise per the PSU service manual.",
                "safety": "Secondary side only. Do NOT probe the controller VCC pin on the primary side."
            },
            "interpretations": [
                {"if_result": "Standby rail present", "then": "Controller startup is alive. The fault is in the main switching stage or the main feedback loop. Recommend PSU replacement or factory service — primary-side diagnosis requires an isolation transformer and trained personnel.", "set_facts": ["standby_good=True"]},
                {"if_result": "Standby rail missing", "then": "Controller is not running. Likely startup resistor open, controller dead, or primary-side fuse open. Recommend PSU replacement — primary-side diagnosis is not safe in the field.", "set_facts": ["standbad_missing=True"]}
            ],
            "requires_info": False,
            "_stage": "standby_check",
        })

        conclusion.notes.append(
            "Mains PSU field diagnosis ends at the secondary side. Primary-side "
            "diagnosis requires an isolation transformer, current-limited bench "
            "supply, variac, and trained personnel. The engine does not "
            "recommend primary-side probing under any circumstances."
        )

        # --- topology-driven secondary-side stage walk (A1-1/A1-2 fix) ---
        # The fixed 3-step sequence above (safety gate + output check +
        # standby check) is the SAME for all mains topologies. To make
        # mains topologies structurally distinct, walk the topology's
        # OWN stage model — but ONLY for secondary-side signals. Primary-
        # side signals (bulk_dc, pfc_voltage, switching on the primary
        # FET, gate_drive) are blocked by the safety gate. Secondary-side
        # signals (output_voltage, vsb_voltage, pgood, resonant_freq
        # observed via output shape, fb on the secondary) are safe.
        # This lets the LLC's resonant_tank fault surface (resonant_freq
        # is observable from the secondary-side output waveform) without
        # violating the mains safety gate.
        PRIMARY_SIDE_SIGNALS = {
            "bulk_dc", "pfc_voltage", "gate_drive", "vref",
            # 'switching' on the primary FET is primary-side; but a
            # secondary-side switching observation (sync rectifier) is
            # safe. We treat 'switching' as primary here for safety; the
            # technician can observe output shape instead.
            "switching",
        }
        by_name = {m.name: m for m in inp.measurements}
        from DiagnosticEngines.core.framework import MeasurementState
        topo_stages = topo.get("stages", [])
        secondary_failed_stage = None
        secondary_missing: list[str] = []
        for stage in topo_stages:
            stage_id = stage["id"]
            sig_map = stage.get("diagnostic_signals", {})
            if not sig_map:
                continue
            # Skip primary-side stages entirely (safety gate).
            primary_signals_in_stage = {s for s in sig_map
                                        if s in PRIMARY_SIDE_SIGNALS}
            secondary_signals_in_stage = {s for s in sig_map
                                          if s not in PRIMARY_SIDE_SIGNALS}
            if not secondary_signals_in_stage:
                continue  # purely primary-side stage — blocked
            # Walk only the secondary-side signals of this stage.
            stage_failed = False
            failed_signal = None
            for signal_name in secondary_signals_in_stage:
                expected = sig_map[signal_name]
                expected_state = MeasurementState(expected) \
                    if isinstance(expected, str) else expected
                m = by_name.get(signal_name)
                if m is None or m.state == MeasurementState.NOT_MEASURED:
                    secondary_missing.append(signal_name)
                    continue
                if self._signal_failed(m, expected_state):
                    stage_failed = True
                    failed_signal = signal_name
                    break
            if stage_failed:
                secondary_failed_stage = (stage_id, failed_signal,
                                          stage.get("fail_note", ""))
                break
        if secondary_failed_stage:
            stage_id, signal_name, fail_note = secondary_failed_stage
            # Override the generic rule_id with the topology-specific stage.
            conclusion.rule_id = f"PSUP_{inp.topology_id}_{inp.symptom}_{stage_id}"
            # Phase 2: fault-type classification (mains/secondary side).
            by_name_m = {m.name: m for m in inp.measurements}
            fault_type = self._classify_fault_type(by_name_m, topo, inp.symptom)
            conclusion.notes.append(
                f"Fault TYPE: {fault_type}. Secondary-side fault localised "
                f"to stage '{stage_id}' (signal '{signal_name}'). {fail_note}"
            )
            # Phase 2: dependency caveat for mains. The secondary stage's
            # upstream primary dependencies (main_switch, transformer)
            # cannot be measured in the field (safety gate). So the
            # secondary localization is PROVISIONAL — the root cause may
            # be a primary-side fault (dead switch, open primary winding)
            # that manifests as a missing secondary signal. Recommend
            # factory service for primary-side diagnosis.
            topo_stage_m = next((s for s in topo.get("stages", [])
                                 if s.get("id") == stage_id), {})
            primary_deps = [d for d in (topo_stage_m.get("depends_on") or [])
                            if d in PRIMARY_SIDE_SIGNALS or d in
                            ("main_switch", "transformer", "pwm_controller",
                             "resonant_tank", "switching")]
            if primary_deps:
                conclusion.notes.append(
                    f"DEPENDENCY CAVEAT (mains): stage '{stage_id}' depends "
                    f"on primary-side stages {primary_deps} which cannot be "
                    f"measured under the safety gate. The secondary-side "
                    f"localization is provisional — the root cause may be a "
                    f"primary-side fault. Recommend factory service."
                )
            conclusion.next_steps.append({
                "step": 4,
                "action": (
                    f"Fault type: {fault_type}. Secondary-side stage "
                    f"'{stage_id}' failed (signal {signal_name}). {fail_note} "
                    f"This is a SECONDARY-side observation — safe to measure "
                    f"at the output connector or secondary-side test points. "
                    f"Do NOT probe the primary side."
                ),
                "measure": {
                    "what": f"Verify the {stage_id} stage (secondary side)",
                    "where": f"{signal_name} — secondary-side test point",
                    "instrument": "DMM + oscilloscope (secondary side only)",
                    "expected": f"{signal_name} at expected state",
                    "safety": "Secondary side only. The primary side stays "
                              "live inside the case — do not open it.",
                },
                "interpretations": [],
                "requires_info": False,
                "_stage": stage_id,
            })
        elif secondary_missing:
            conclusion.notes.append(
                f"Secondary-side signals not yet measured: {secondary_missing}. "
                f"Measure them at the output connector / secondary test points "
                f"to localise the fault without primary-side probing."
            )
        return conclusion

    def _topology_unique_guidance(self, topo: dict, stage_id: str) -> str:
        """Return topology-specific guidance for a failed stage.

        This is what makes the workflow topology-aware rather than
        generic: a flyback's feedback stage involves an optocoupler
        and transformer; a boost's feedback involves the output
        divider and the boost diode. The generic stage-check workflow
        identifies WHICH stage failed; this adds WHAT to look at
        within that stage for this specific topology.
        """
        topo_id = topo.get("topology_id", "")
        stages = {s.get("id"): s for s in topo.get("stages", [])}
        has_pfc = "pfc" in stages
        has_transformer = "transformer" in stages
        has_inductor = "inductor" in stages

        guidance = ""
        if stage_id == "feedback":
            if has_transformer:
                guidance = ("For this isolated topology, the feedback path "
                            "crosses the isolation barrier via an optocoupler "
                            "and a shunt regulator (TL431). Check the "
                            "optocoupler's CTR, the TL431 reference, and the "
                            "secondary-side feedback divider.")
            elif topo_id == "PSUP_boost":
                guidance = ("For a boost converter, feedback comes from the "
                            "output divider into the controller. Also check "
                            "the output diode (schottky) — if it is open, the "
                            "output cannot rise and FB stays low.")
            elif topo_id == "PSUP_buck":
                guidance = ("For a buck converter, check the FB divider and "
                            "the controller's internal reference. A buck's "
                            "FB pin voltage equals Vref when regulated.")
        elif stage_id == "switching" and has_transformer:
            guidance = ("For this transformer-isolated topology, the main "
                        "switch drives the transformer primary. Check the "
                        "primary-side switch FET, the transformer primary "
                        "winding for continuity, and the snubber (RCD clamp) "
                        "across the primary.")
        elif stage_id == "input_protection" and has_pfc:
            guidance = ("This topology has a PFC stage after input protection. "
                        "If input protection is good, the PFC stage (boost "
                        "pre-regulator) is the next suspect — check the PFC "
                        "switch and the PFC bulk cap voltage (~380V DC).")
        elif stage_id == "output" and topo_id == "PSUP_boost":
            guidance = ("For a boost converter, the output stage includes the "
                        "boost diode and output cap. An open boost diode means "
                        "no output; a shorted output cap loads the rail down.")
        elif stage_id == "output" and has_transformer:
            guidance = ("For this isolated topology, the output stage includes "
                        "the secondary-side rectifier (schottky or sync FET) "
                        "and the output inductor/cap. Check the secondary "
                        "rectifier for open, and the output cap for short.")
        return guidance

    def _build_low_voltage_workflow(self, conclusion: EngineConclusion, topo: dict,
                                    fault: dict, inp: PowerSupplyDiagnosisInput) -> EngineConclusion:
        """Low-voltage (non-mains) workflow. Real measurement-driven
        branching, not stage-name printing.

        The workflow walks the supply's stages in order, using the
        supplied measurements to identify the earliest failed stage.
        For each stage it emits a next-step that tells the technician
        what to measure and how to interpret the result.

        Stage signal mapping (canonical measurement names the host
        should supply):
          input_protection   -> 'input_voltage' (must be present)
          rectification      -> 'input_voltage' (same signal; rectifier
                                fault shows as no input past the bridge)
          input_filter       -> 'input_voltage' (filtered version)
          regulator          -> 'vcc' (controller supply) + 'en' (enable)
                                + 'switching' (gate-drive activity)
          output_filter      -> 'output_voltage' + 'output_ripple'
          feedback           -> 'fb' (feedback pin voltage)
          protection         -> 'pgood' (power-good signal)

        The workflow stops at the first stage whose confirm signal is
        measured-and-failed, or surfaces requires_info when a needed
        signal was not measured.
        """
        # Build a measurement lookup.
        by_name: dict[str, Measurement] = {}
        for m in inp.measurements:
            by_name[m.name] = m

        # TOPOLOGY-DRIVEN STAGE WALK (Priority 1 structural fix).
        # Instead of a shared input->VCC->EN->switching->FB->output->PGOOD
        # chain for every topology, walk THIS topology's own stage list
        # and use each stage's `diagnostic_signals` map. A linear
        # regulator's stages have no 'switching' signal; an LLC has a
        # 'resonant_tank' stage with 'resonant_freq'; a charger has an
        # 'adapter_sense' stage. The workflow is now structurally
        # distinct per topology, not just in guidance text.
        from DiagnosticEngines.core.framework import MeasurementState
        topo_stages = topo.get("stages", [])
        # If a topology has no stage definitions (shouldn't happen
        # after P1), fall back to a minimal generic chain for safety.
        if not topo_stages:
            topo_stages = [
                {"id": "input_protection",
                 "diagnostic_signals": {"input_voltage": "in_range"},
                 "fail_note": "No input voltage.",
                 "description": "Input protection",
                 "components": []},
                {"id": "output_filter",
                 "diagnostic_signals": {"output_voltage": "in_range"},
                 "fail_note": "Output missing.",
                 "description": "Output filter",
                 "components": []},
            ]

        fired_stage = None
        missing_signals: list[str] = []
        # PASS 1: find the EARLIEST stage with a MEASURED failure. A
        # measured failure (a signal measured-and-bad) localizes the
        # fault regardless of whether other stages' signals were
        # measured. We prefer a measured failure over missing data
        # because the technician DID observe a bad signal.
        for stage in topo_stages:
            stage_id = stage["id"]
            sig_map = stage.get("diagnostic_signals", {})
            if not sig_map:
                continue
            for signal_name, expected in sig_map.items():
                expected_state = MeasurementState(expected) \
                    if isinstance(expected, str) else expected
                m = by_name.get(signal_name)
                if m is None or m.state == MeasurementState.NOT_MEASURED:
                    continue
                if self._signal_failed(m, expected_state):
                    fired_stage = (stage_id, signal_name,
                                   stage.get("fail_note", f"Stage {stage_id} failed."))
                    break
            if fired_stage:
                break

        # PASS 2: if no measured failure, walk in order and surface the
        # first stage that has a missing signal (the workflow cannot
        # proceed past an unconfirmed stage). This is the requires_info
        # path: the technician must measure more.
        if not fired_stage:
            for stage in topo_stages:
                stage_id = stage["id"]
                sig_map = stage.get("diagnostic_signals", {})
                if not sig_map:
                    continue
                stage_has_failure = False
                stage_has_missing = False
                for signal_name, expected in sig_map.items():
                    expected_state = MeasurementState(expected) \
                        if isinstance(expected, str) else expected
                    m = by_name.get(signal_name)
                    if m is None or m.state == MeasurementState.NOT_MEASURED:
                        missing_signals.append(signal_name)
                        stage_has_missing = True
                        continue
                    if self._signal_failed(m, expected_state):
                        stage_has_failure = True
                if stage_has_failure:
                    # a measured failure here should have been caught
                    # in PASS 1; defensive
                    break
                if stage_has_missing:
                    break

        if fired_stage:
            stage_id, signal_name, fail_note = fired_stage
            # Phase 2: fault-type classification + dependency gate.
            fault_type = self._classify_fault_type(by_name, topo, inp.symptom)
            stage_health = self._build_stage_health(topo, by_name)
            dep_state = self._stage_dependency_state(
                next((s for s in topo.get("stages", [])
                      if s.get("id") == stage_id), {"depends_on": []}),
                stage_health)
            conclusion.rule_id = f"PSUP_{inp.topology_id}_{inp.symptom}_{stage_id}"
            # Pull topology-specific context for this stage from the
            # topology's stage list. This makes the workflow
            # topology-aware: a flyback mentions its transformer/PFC
            # stages where a buck does not.
            topo_stage = next((s for s in topo.get("stages", [])
                               if s.get("id") == stage_id), None)
            topo_stage_desc = ""
            topo_components = ""
            if topo_stage:
                topo_stage_desc = topo_stage.get("description", "")
                comps = topo_stage.get("components", [])
                if comps:
                    topo_components = " Stage components: " + ", ".join(comps) + "."
            conclusion.notes.append(
                f"Fault TYPE: {fault_type}. Localised to stage "
                f"'{stage_id}' (signal '{signal_name}'). {fail_note}"
            )
            # Dependency gate: if this stage's dependencies are unknown
            # or blocked, surface that the localization is CONDITIONAL —
            # the real fault may be upstream. Do NOT present a downstream
            # stage as confirmed-defective when upstream is unknown.
            if dep_state == "blocked":
                blocked_deps = [d for d in (topo_stage or {}).get("depends_on", [])
                                if stage_health.get(d) == "failed"]
                conclusion.notes.append(
                    f"DEPENDENCY GATE: stage '{stage_id}' depends on "
                    f"{blocked_deps}, which FAILED. The downstream "
                    f"localization is CONDITIONAL — fix the upstream "
                    f"fault first; the '{stage_id}' failure may be a "
                    f"consequence, not an independent fault."
                )
            elif dep_state == "unknown":
                unknown_deps = [d for d in (topo_stage or {}).get("depends_on", [])
                                if stage_health.get(d) == "unknown"]
                conclusion.notes.append(
                    f"DEPENDENCY GATE: stage '{stage_id}' depends on "
                    f"{unknown_deps}, which are UNMEASURED. The "
                    f"downstream localization is provisional — measure "
                    f"the upstream stages before concluding '{stage_id}' "
                    f"is defective."
                )
            if topo_stage_desc:
                conclusion.notes.append(f"Topology stage context: {topo_stage_desc}")
            # Topology-specific guidance: mention topology-unique stages.
            topo_unique = self._topology_unique_guidance(topo, stage_id)
            action_text = fail_note
            if topo_components:
                action_text += topo_components
            if topo_unique:
                action_text += " " + topo_unique
            conclusion.next_steps.append({
                "step": 1,
                "action": action_text,
                "measure": {
                    "what": f"Verify and diagnose the {stage_id} stage of the {topo['name']}",
                    "where": f"{signal_name} signal path",
                    "instrument": "DMM + oscilloscope",
                    "expected": f"{signal_name} at expected state (in_range)",
                    "safety": "Low-voltage. Disconnect input before resistance measurements.",
                },
                "interpretations": [
                    {"if_result": f"{stage_id} now good",
                     "then": "Re-run the full workflow to confirm downstream stages.",
                     "set_facts": [f"{stage_id}_good=True"]},
                    {"if_result": f"{stage_id} still failed",
                     "then": f"Replace the failed component(s) in the {stage_id} stage. "
                             f"Use the replacement engine to verify compatibility.",
                     "set_facts": [f"{stage_id}_fault=True"]},
                ],
                "requires_info": False,
            })
            # Update the hypothesis to point at the specific stage.
            if conclusion.new_hypotheses:
                h = conclusion.new_hypotheses[0]
                h.statement = (
                    f"Power supply fault: {inp.symptom} on {topo['name']}, "
                    f"localised to stage '{stage_id}' (signal '{signal_name}')."
                )
        else:
            # No measured failure. If signals are missing, surface them.
            if missing_signals:
                conclusion.requires_info = True
                conclusion.requires_info_note = (
                    f"Cannot localise the fault — the following signals were not "
                    f"measured: {missing_signals}. Measure them in order to advance "
                    f"the workflow."
                )
                conclusion.next_steps.append({
                    "step": 1,
                    "action": (
                        f"Measure the missing signals to localise the fault: "
                        f"{missing_signals}. Walk the stages in order: input -> "
                        f"VCC -> EN -> switching -> feedback -> output -> PGOOD."
                    ),
                    "measure": {
                        "what": ", ".join(missing_signals),
                        "where": "Per the stage list",
                        "instrument": "DMM + oscilloscope",
                        "expected": "Per the topology's expected behavior",
                        "safety": "Low-voltage. Disconnect input before resistance.",
                    },
                    "interpretations": [],
                    "requires_info": True,
                })
            else:
                # All signals measured and good — the supply is healthy
                # per the measurements. The symptom may be elsewhere.
                conclusion.rule_id = f"PSUP_{inp.topology_id}_{inp.symptom}_HEALTHY"
                conclusion.notes.append(
                    "All stage signals measured and good. The supply is healthy "
                    "per the supplied measurements. The symptom may be in the load "
                    "or the cabling, not the supply."
                )
                conclusion.next_steps.append({
                    "step": 1,
                    "action": (
                        "All supply stages are good. Check the load: disconnect the "
                        "load and re-measure the output. If output returns to nominal, "
                        "the load is faulty (overload or short). If output is still "
                        "wrong with no load, re-examine the feedback loop."
                    ),
                    "measure": {
                        "what": "Output voltage with load disconnected",
                        "where": "Output connector",
                        "instrument": "DMM DC volts",
                        "expected": "Nominal output voltage with no load.",
                        "safety": "Low-voltage.",
                    },
                    "interpretations": [
                        {"if_result": "Output good with no load",
                         "then": "The load is overloaded or shorted. Diagnose the load.",
                         "set_facts": ["load_fault=True"]},
                        {"if_result": "Output still wrong with no load",
                         "then": "Supply fault not caught by stage checks. Suspect "
                                 "feedback loop instability or compensation.",
                         "set_facts": ["supply_fault=True"]},
                    ],
                    "requires_info": False,
                })
                if conclusion.new_hypotheses:
                    conclusion.new_hypotheses[0].statement = (
                        f"Power supply {topo['name']} is healthy per measurements; "
                        f"symptom ({inp.symptom}) may be load-induced."
                    )
        return conclusion

    def _signal_failed(self, m: Measurement, expected_state) -> bool:
        """Return True if the measurement represents a FAILED signal
        (i.e. the stage did not complete). A NOT_MEASURED signal is NOT
        a failure — it's missing data (handled by the caller)."""
        from DiagnosticEngines.core.framework import MeasurementState
        if m.state == MeasurementState.NOT_MEASURED:
            return False  # missing, not failed
        if expected_state == MeasurementState.IN_RANGE:
            # A MEASURED_ZERO signal is a failed stage (signal absent).
            # An ABOVE_RANGE/BELOW_RANGE/INVALID is also failed.
            if m.state == MeasurementState.MEASURED_ZERO:
                return True
            if m.state in (MeasurementState.ABOVE_RANGE,
                           MeasurementState.BELOW_RANGE,
                           MeasurementState.INVALID):
                return True
            # IN_RANGE: check numeric value. A zero numeric value on a
            # voltage/logic signal is a failed stage.
            if m.state == MeasurementState.IN_RANGE:
                val = m.numeric_value
                if val is not None and val == 0.0:
                    return True
            return False
        return m.state != expected_state

    # --- Phase 2: fault-type classification + dependency-respecting walk --

    def _classify_fault_type(
        self, by_name: dict, topo: dict, symptom: str
    ) -> str:
        """Classify the case into a fault TYPE based on measurement
        patterns. Returns one of:
          startup_failure        — controller never started (no VCC/EN,
                                   no switching, output absent)
          protection_shutdown    — controller started then shut down
                                   (was switching, now pgood low / fault
                                   latched); output absent or pulsing
          missing_switching      — VCC + EN good but no switching
          unstable_output        — output present but wrong/oscillating
          overload               — output loaded down / current limit
          output_stage_failure   — switching present, output absent
                                   (rectifier/inductor/open)
          unknown                — insufficient data to classify

        This classification drives the diagnostic narrative and the
        next-measurement recommendation. It does NOT replace the
        stage-walk localization; it augments it with a fault-type label
        so the technician understands the failure MODE, not just the
        location.
        """
        from DiagnosticEngines.core.framework import MeasurementState as MS

        def st(name):
            m = by_name.get(name)
            return m.state if m is not None else MS.NOT_MEASURED

        def is_absent(name):
            s = st(name)
            return s in (MS.MEASURED_ZERO, MS.NOT_MEASURED) is False and \
                (s == MS.MEASURED_ZERO or (
                    s == MS.IN_RANGE and by_name[name].numeric_value == 0.0
                ))

        def present(name):
            s = st(name)
            if s == MS.IN_RANGE:
                v = by_name[name].numeric_value
                return v is not None and v != 0.0
            return False

        def measured(name):
            m = by_name.get(name)
            return m is not None and m.state not in (
                MS.NOT_MEASURED, MS.NOT_APPLICABLE)

        out_s = st("output_voltage")
        sw_s = st("switching")
        vcc_s = st("vcc")
        en_s = st("en")
        pgood_s = st("pgood")
        vref_s = st("vref")

        # output_stage_failure: switching present, output absent
        if present("switching") and (
            out_s == MS.MEASURED_ZERO or
            (out_s == MS.IN_RANGE and by_name["output_voltage"].numeric_value == 0.0)
        ):
            return "output_stage_failure"
        # missing_switching: VCC + EN good, no switching
        if present("vcc") and present("en") and (
            sw_s == MS.MEASURED_ZERO or
            (sw_s == MS.IN_RANGE and by_name["switching"].numeric_value == 0.0)
        ):
            return "missing_switching"
        # protection_shutdown: pgood measured low (fault latched) —
        # implies the controller ran then shut down. Distinguish from
        # startup_failure by pgood being measured LOW (not absent).
        if measured("pgood") and not present("pgood"):
            # pgood low + output absent = protection shutdown
            if out_s == MS.MEASURED_ZERO or (
                out_s == MS.IN_RANGE and by_name["output_voltage"].numeric_value == 0.0
            ):
                return "protection_shutdown"
        # unstable_output: output present but out of tolerance or
        # flagged unstable / excessive ripple
        if present("output_voltage"):
            rip = by_name.get("ripple")
            if rip and rip.state == MS.IN_RANGE and rip.numeric_value is not None \
                    and rip.numeric_value > 50.0:
                return "unstable_output"
            ovr = by_name.get("output_voltage")
            # out-of-range (but non-zero) = unstable/overvoltage
            if ovr and ovr.state in (MS.ABOVE_RANGE, MS.BELOW_RANGE):
                return "unstable_output"
            # symptom-driven: 'wrong_voltage' or 'overheating' with output present
            if symptom in ("wrong_voltage", "overheating"):
                return "unstable_output"
        # overload: output loaded down (low but non-zero) with switching present
        if present("switching") and out_s == MS.IN_RANGE:
            v = by_name["output_voltage"].numeric_value
            if v is not None and 0.0 < v < 1.0:
                return "overload"
        # startup_failure: no VCC or no EN (controller never started)
        if (measured("vcc") and not present("vcc")) or \
           (measured("en") and not present("en")):
            return "startup_failure"
        # default: startup_failure when output absent and nothing else
        # classifies
        if out_s == MS.MEASURED_ZERO or (
            out_s == MS.IN_RANGE and by_name.get("output_voltage") and
            by_name["output_voltage"].numeric_value == 0.0
        ):
            return "startup_failure"
        return "unknown"

    def _stage_dependency_state(
        self, stage: dict, stage_health: dict[str, str]
    ) -> str:
        """Return the dependency state for a stage:
          'ok'      — all dependencies confirmed healthy
          'blocked' — at least one dependency failed (don't diagnose
                      downstream)
          'unknown' — at least one dependency is unmeasured/unknown
                      (don't diagnose downstream as confirmed)
        stage_health maps stage_id -> 'healthy' / 'failed' / 'unknown'.
        """
        deps = stage.get("depends_on", []) or []
        if not deps:
            return "ok"
        states = [stage_health.get(d, "unknown") for d in deps]
        if any(s == "failed" for s in states):
            return "blocked"
        if any(s == "unknown" for s in states):
            return "unknown"
        return "ok"

    def _build_stage_health(
        self, topo: dict, by_name: dict
    ) -> dict[str, str]:
        """Classify each stage as 'healthy' / 'failed' / 'unknown' based
        on its diagnostic signals. A stage is 'failed' if ANY of its
        signals is measured-and-bad; 'healthy' if ALL its signals are
        measured-and-good; 'unknown' if any signal is unmeasured (and
        none measured-bad)."""
        from DiagnosticEngines.core.framework import MeasurementState as MS
        health = {}
        for stage in topo.get("stages", []):
            sig_map = stage.get("diagnostic_signals", {})
            if not sig_map:
                health[stage["id"]] = "unknown"
                continue
            any_bad = False
            any_missing = False
            for signal_name, expected in sig_map.items():
                m = by_name.get(signal_name)
                if m is None or m.state == MS.NOT_MEASURED:
                    any_missing = True
                    continue
                expected_state = MS(expected) if isinstance(expected, str) else expected
                if self._signal_failed(m, expected_state):
                    any_bad = True
                    break
            if any_bad:
                health[stage["id"]] = "failed"
            elif any_missing:
                health[stage["id"]] = "unknown"
            else:
                health[stage["id"]] = "healthy"
        return health

    # --- safety -------------------------------------------------------

    def _safety_check(self, conclusion: EngineConclusion) -> None:
        """Run the SafetyPolicy over each next step. Mains-side steps
        are tagged but NOT blocked (they are explicitly safety-gate
        steps that tell the technician NOT to probe the primary side).
        Other steps that trip a BLOCK are flagged."""
        for step in conclusion.next_steps:
            measure = step.get("measure") or {}
            proposed = {
                "what": measure.get("what", step.get("action", "")),
                "where": measure.get("where", ""),
                "instrument": measure.get("instrument", ""),
                "safety": measure.get("safety", ""),
                "action": step.get("action", ""),
            }
            violations = self.safety.evaluate(proposed)
            if violations:
                conclusion.safety_violations.extend(violations)
                # The safety-gate step is *intentionally* about mains
                # discharge verification — it does not actually recommend
                # probing live mains. We keep it but record the violations
                # so the host can show the safety context.
                if step.get("_stage") == "safety_gate":
                    step["_intentional_safety_context"] = True
                else:
                    blocked = any(v.level == SafetyLevel.BLOCK for v in violations)
                    if blocked:
                        step["blocked_by_safety"] = True
