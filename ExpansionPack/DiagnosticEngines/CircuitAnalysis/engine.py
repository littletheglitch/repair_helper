"""Circuit Analysis Engine.

Retrieves a circuit-family topology by name and produces a framework
EngineConclusion describing what to measure, where, and what to look
for. The engine also supports a simple component-graph topology
recognition for structured inputs (component list + netlist) — see
recognize_from_component_list().

The engine does NOT do image-based recognition. The spec explicitly
allows only structured inputs (netlist, component graph, BoardView-
derived info when manually supplied) for reliable topology recognition.
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
    Hypothesis, Evidence, EvidenceType, EvidenceStrength,
)
from DiagnosticEngines.core.framework.session import EngineConclusion  # noqa: E402


@dataclass
class CircuitAnalysisInput:
    """Inputs the host passes to the circuit analysis engine."""
    circuit_family: str = ""                # one of CCT_* from the topology files; empty to use recognition
    symptom: str = ""                       # optional observed symptom
    measurements: list = field(default_factory=list)  # optional framework measurements
    # Structured-input topology recognition:
    component_list: list[dict] = field(default_factory=list)  # [{refdes, value, footprint}, ...]


class CircuitAnalysisEngine:
    """Specialized engine for analyzing common PCB circuit topologies."""

    def __init__(self, pack_root: Optional[Path] = None):
        self.pack_root = pack_root or PACK_ROOT
        self.topologies: dict[str, dict] = {}
        self._load_topologies()

    def _load_topologies(self) -> None:
        topo_dir = self.pack_root / "DiagnosticEngines" / "CircuitAnalysis" / "topologies"
        if not topo_dir.exists():
            return
        for f in sorted(topo_dir.glob("*.json")):
            with open(f, "r", encoding="utf-8") as fh:
                t = json.load(fh)
            self.topologies[t["circuit_id"]] = t

    # --- public API ----------------------------------------------------

    def supported_circuits(self) -> list[str]:
        return sorted(self.topologies.keys())

    def analyze(self, inp: CircuitAnalysisInput) -> EngineConclusion:
        """Produce a diagnostic conclusion for a circuit family."""
        conclusion = EngineConclusion(
            engine="circuit_analysis",
            rule_id=None,
            next_steps=[],
        )

        # Structured-input topology recognition: if the host supplied a
        # component list, try to identify the circuit family from it.
        if inp.component_list and not inp.circuit_family:
            recognized = self.recognize_from_component_list(inp.component_list)
            if recognized:
                conclusion.notes.append(
                    f"Topology recognition: component list matches "
                    f"{recognized!r} (confidence: heuristic, not definitive)."
                )
                inp.circuit_family = recognized
            else:
                conclusion.requires_info = True
                conclusion.requires_info_note = (
                    "Component list did not match any known circuit family. "
                    "Supply a circuit_family explicitly, or expand the component list."
                )
                return conclusion

        topo = self.topologies.get(inp.circuit_family)
        if topo is None:
            conclusion.requires_info = True
            conclusion.requires_info_note = (
                f"Unknown circuit family {inp.circuit_family!r}. "
                f"Supported: {self.supported_circuits()}"
            )
            return conclusion

        conclusion.rule_id = f"CCT_{inp.circuit_family}"
        conclusion.notes.append(f"Circuit family: {topo['name']}")
        conclusion.notes.append(f"Description: {topo['description']}")
        conclusion.notes.append(f"Input: {topo['input']}  |  Output: {topo['output']}")
        conclusion.notes.append(f"Expected behavior: {topo['expected_signal_behavior']}")

        # --- real measurement-driven reasoning ---
        # Build a measurement lookup. The host supplies measurements
        # with canonical names: 'input', 'output', 'feedback'/'fb',
        # 'control'/'en', plus any circuit-specific signals.
        from DiagnosticEngines.core.framework import Measurement as FwM, MeasurementState
        by_name: dict[str, FwM] = {}
        for mm in inp.measurements:
            if isinstance(mm, FwM):
                by_name[mm.name] = mm
            elif isinstance(mm, dict):
                by_name[mm.get("name", "")] = FwM.from_dict(mm)

        def _signal_state(name: str) -> str:
            """Return 'present', 'absent', 'missing', or 'unknown' for a
            named measurement."""
            m = by_name.get(name)
            if m is None or m.state == MeasurementState.NOT_MEASURED:
                return "missing"
            if m.state == MeasurementState.MEASURED_ZERO:
                return "absent"
            if m.state == MeasurementState.IN_RANGE:
                val = m.numeric_value
                if val is not None and val == 0.0:
                    return "absent"
                return "present"
            if m.state in (MeasurementState.ABOVE_RANGE,
                           MeasurementState.BELOW_RANGE,
                           MeasurementState.INVALID):
                return "absent"
            return "unknown"

        input_state = _signal_state("input") or _signal_state("input_voltage")
        output_state = _signal_state("output") or _signal_state("output_voltage")
        # Feedback/control signal name varies by circuit family.
        fb_name = "feedback" if "feedback" in by_name else ("fb" if "fb" in by_name else "")
        fb_state = _signal_state(fb_name) if fb_name else "missing"
        ctrl_name = ""
        for cand in ("control", "en", "enable", "gate", "base"):
            if cand in by_name:
                ctrl_name = cand
                break
        ctrl_state = _signal_state(ctrl_name) if ctrl_name else "missing"

        # Branch on the input/output combination. This is the real
        # diagnostic reasoning — not metadata lookup.
        if input_state == "missing" and output_state == "missing":
            # No measurements supplied -> guide the technician to measure
            conclusion.rule_id = f"CCT_{inp.circuit_family}_NEED_DATA"
            conclusion.requires_info = True
            conclusion.requires_info_note = (
                f"No input/output measurements supplied for {topo['name']}. "
                f"Measure the input and output to begin analysis."
            )
            conclusion.next_steps.append({
                "step": 1,
                "action": (
                    f"Measure the input and output of the {topo['name']}. "
                    f"{topo['description']}"
                ),
                "measure": {
                    "what": f"Input and output of the {topo['name']}",
                    "where": topo.get("input", "") + " and " + topo.get("output", ""),
                    "instrument": "DMM + oscilloscope",
                    "expected": topo["expected_signal_behavior"],
                    "safety": "Low-voltage unless this circuit is on a mains primary side.",
                },
                "interpretations": [
                    {"if_result": "Input present and output present and correct",
                     "then": "Circuit is functioning. The fault is elsewhere.",
                     "set_facts": ["circuit_healthy=True"]},
                    {"if_result": "Input present but output missing",
                     "then": f"Circuit is not passing signal. Likely fault locations: {topo['possible_fault_locations']}.",
                     "set_facts": ["circuit_output_missing=True"]},
                    {"if_result": "Input missing",
                     "then": "Upstream supply/signal is missing. Diagnose the upstream circuit first.",
                     "set_facts": ["circuit_input_missing=True"]},
                ],
                "requires_info": True,
            })
        elif input_state == "absent":
            # Input measured and absent -> upstream fault
            conclusion.rule_id = f"CCT_{inp.circuit_family}_INPUT_MISSING"
            conclusion.notes.append(
                f"Input measured as absent. The {topo['name']} cannot function "
                f"without its input. Diagnose the upstream supply/signal first."
            )
            conclusion.next_steps.append({
                "step": 1,
                "action": (
                    f"Input of the {topo['name']} is absent. The circuit cannot "
                    f"produce its output without input. Trace the input back to "
                    f"its source and diagnose the upstream stage."
                ),
                "measure": {
                    "what": "Upstream supply/signal source",
                    "where": "The source feeding this circuit's input",
                    "instrument": "DMM + scope",
                    "expected": "Input present at the source; trace forward to find where it drops.",
                    "safety": "Low-voltage unless upstream is mains-side.",
                },
                "interpretations": [
                    {"if_result": "Input present at source, absent at circuit input",
                     "then": "Open trace, blown input fuse, or open series component between source and circuit. Check continuity (unpowered).",
                     "set_facts": ["input_path_open=True"]},
                    {"if_result": "Input absent at source too",
                     "then": "Upstream stage is faulty. Diagnose the upstream circuit.",
                     "set_facts": ["upstream_fault=True"]},
                ],
                "requires_info": False,
            })
        elif input_state == "present" and output_state == "absent":
            # Input present, output absent -> circuit not passing signal
            conclusion.rule_id = f"CCT_{inp.circuit_family}_OUTPUT_MISSING"
            conclusion.notes.append(
                f"Input present but output absent. The {topo['name']} is not "
                f"passing signal. Fault is within this circuit."
            )
            # For circuits with a control/enable signal, check it first.
            if ctrl_name and ctrl_state == "absent":
                conclusion.notes.append(
                    f"Control signal '{ctrl_name}' is absent. The circuit cannot "
                    f"enable without it. Check the control source before condemning "
                    f"the circuit components."
                )
                conclusion.next_steps.append({
                    "step": 1,
                    "action": (
                        f"Input is present but the control signal '{ctrl_name}' is "
                        f"absent. The {topo['name']} cannot enable without its control. "
                        f"Check the control source (EC, GPIO, supervisor)."
                    ),
                    "measure": {
                        "what": f"Control signal '{ctrl_name}' source",
                        "where": f"Source of {ctrl_name}",
                        "instrument": "DMM + scope",
                        "expected": "Control signal at logic-high (or per datasheet).",
                        "safety": "Low-voltage.",
                    },
                    "interpretations": [
                        {"if_result": f"{ctrl_name} present at source, absent at circuit",
                         "then": "Open trace between source and circuit. Check continuity.",
                         "set_facts": ["control_path_open=True"]},
                        {"if_result": f"{ctrl_name} absent at source too",
                         "then": "Control source is faulty. Diagnose the source (EC/GPIO/supervisor).",
                         "set_facts": ["control_source_fault=True"]},
                    ],
                    "requires_info": False,
                })
            elif fb_name and fb_state == "absent":
                conclusion.notes.append(
                    f"Feedback signal '{fb_name}' is absent. Regulation circuits "
                    f"cannot hold output without feedback. Check the feedback divider."
                )
                conclusion.next_steps.append({
                    "step": 1,
                    "action": (
                        f"Input present but feedback '{fb_name}' is absent. The "
                        f"{topo['name']} cannot regulate without feedback. Check "
                        f"the feedback divider resistors and the FB pin decoupling."
                    ),
                    "measure": {
                        "what": f"Feedback divider resistors + FB pin",
                        "where": "FB divider and FB pin",
                        "instrument": "DMM resistance (unpowered) + DMM volts (powered)",
                        "expected": "FB pin at the reference voltage per the datasheet.",
                        "safety": "Unpowered for resistance; powered for voltage.",
                    },
                    "interpretations": [
                        {"if_result": "FB divider open",
                         "then": "Replace the open resistor with the correct value.",
                         "set_facts": ["fb_divider_open=True"]},
                        {"if_result": "FB divider good but FB pin still wrong",
                         "then": "Suspect the controller/IC's internal error amp. Replace the IC.",
                         "set_facts": ["ic_internal_fault=True"]},
                    ],
                    "requires_info": False,
                })
            else:
                # No control/feedback issue identified; fault is in the
                # circuit's main signal path.
                conclusion.next_steps.append({
                    "step": 1,
                    "action": (
                        f"Input present but output absent. The {topo['name']}'s "
                        f"main signal path is faulty. Check the likely fault "
                        f"locations: {topo['possible_fault_locations']}."
                    ),
                    "measure": {
                        "what": "Main signal-path components",
                        "where": f"Focus on: {topo['possible_fault_locations']}",
                        "instrument": "DMM + scope",
                        "expected": "Signal passes through each stage to the output.",
                        "safety": "Unpowered for resistance; powered for signal tracing.",
                    },
                    "interpretations": [
                        {"if_result": "Fault confirmed in a listed component",
                         "then": "Replace with a verified-compatible part (use the replacement engine).",
                         "set_facts": ["fault_localised=True"]},
                        {"if_result": "No fault found in listed components",
                         "then": "Re-examine the symptom; multiple faults may be present.",
                         "set_facts": ["fault_not_localised=True"]},
                    ],
                    "requires_info": False,
                })
        elif input_state == "present" and output_state == "present":
            # Both present -> circuit is healthy per measurements
            conclusion.rule_id = f"CCT_{inp.circuit_family}_HEALTHY"
            conclusion.notes.append(
                f"Input and output both present. The {topo['name']} is functioning "
                f"per the supplied measurements."
            )
            conclusion.next_steps.append({
                "step": 1,
                "action": (
                    f"Circuit is healthy per measurements. If the symptom persists, "
                    f"check for marginal behavior: ripple, load transients, or "
                    f"intermittent faults not visible on a static DMM reading."
                ),
                "measure": {
                    "what": "Output ripple + load transient response",
                    "where": "Output, under load",
                    "instrument": "Oscilloscope AC-coupled",
                    "expected": topo["expected_signal_behavior"],
                    "safety": "Low-voltage.",
                },
                "interpretations": [
                    {"if_result": "Ripple and transients normal",
                     "then": "Circuit is genuinely healthy. Fault is elsewhere.",
                     "set_facts": ["circuit_healthy=True"]},
                    {"if_result": "Ripple excessive or transients large",
                     "then": "Marginal fault: output cap degraded or compensation weak. See the circuit's failure modes.",
                     "set_facts": ["circuit_marginal=True"]},
                ],
                "requires_info": False,
            })
        else:
            # input present, output missing (not measured) -> need output
            conclusion.rule_id = f"CCT_{inp.circuit_family}_NEED_OUTPUT"
            conclusion.requires_info = True
            conclusion.requires_info_note = (
                f"Input is present but output was not measured. Measure the "
                f"output to determine whether the {topo['name']} is passing signal."
            )
            conclusion.next_steps.append({
                "step": 1,
                "action": f"Measure the output of the {topo['name']}.",
                "measure": {
                    "what": "Output signal/voltage",
                    "where": topo.get("output", ""),
                    "instrument": "DMM + scope",
                    "expected": topo["expected_signal_behavior"],
                    "safety": "Low-voltage.",
                },
                "interpretations": [],
                "requires_info": True,
            })

        # Per-failure-mode narrow-down if symptom supplied AND a fault was found
        if inp.symptom and "HEALTHY" not in conclusion.rule_id and "NEED_DATA" not in conclusion.rule_id:
            matched = [fm for fm in topo.get("common_failure_modes", [])
                       if fm.get("mode") == inp.symptom]
            if matched:
                fm = matched[0]
                conclusion.notes.append(
                    f"Symptom {inp.symptom!r} matches failure mode: {fm.get('description','')}"
                )

        # Hypothesis
        h = Hypothesis(
            statement=f"Circuit {topo['name']} analysis: {conclusion.rule_id}.",
            engine="circuit_analysis",
            rule_source=conclusion.rule_id,
        )
        h.add_evidence(Evidence(
            type=EvidenceType.BACKGROUND,
            strength=EvidenceStrength.WEAK,
            description=f"Circuit family identified as {topo['name']}",
            rule_source=conclusion.rule_id,
            source_kind="knowledge",
        ))
        conclusion.new_hypotheses.append(h)

        # Source references
        for ref in topo.get("source_references", []):
            conclusion.notes.append(f"Reference: {ref.get('title','')} ({ref.get('url','')})")

        return conclusion

    # --- structured-input topology recognition -------------------------

    def recognize_from_component_list(self, components: list[dict]) -> Optional[str]:
        """Heuristic topology recognition from a component list.

        This is INTENTIONALLY conservative. It matches on key component
        types (e.g. 'inductor + 2x MOSFET + controller' suggests a buck)
        and returns a single best-guess circuit_id, or None.

        It does NOT claim definitive recognition. The host should treat
        the result as a hint and let the technician confirm.
        """
        if not components:
            return None
        # Build a bag of component-type keywords from the component list.
        types: list[str] = []
        for c in components:
            for key in ("value", "footprint", "description", "refdes"):
                v = str(c.get(key, "")).lower()
                types.append(v)
        bag = " ".join(types)

        # Simple keyword heuristics. Order matters: more specific first.
        rules = [
            ("CCT_buck", ["inductor", "mosfet", "buck"]),
            ("CCT_boost", ["inductor", "schottky", "boost"]),
            ("CCT_buck_boost", ["buck-boost", "4-switch"]),
            ("CCT_charge_pump", ["flying", "charge pump"]),
            ("CCT_linear_regulator", ["ldo", "7805", "lm317", "linear"]),
            ("CCT_opamp", ["op-amp", "opamp", "lm358", "tl072"]),
            ("CCT_comparator", ["comparator", "lm393", "lm311"]),
            ("CCT_mosfet_switch", ["mosfet", "gate"]),
            ("CCT_bjt_switch", ["bjt", "transistor", "2n3904", "2n2222"]),
            ("CCT_relay_driver", ["relay", "flyback"]),
            ("CCT_high_side_switch", ["p-channel", "p-mosfet", "high-side"]),
            ("CCT_low_side_switch", ["n-channel", "n-mosfet", "low-side"]),
            ("CCT_tvs_esd", ["tvs", "esd"]),
            ("CCT_usb", ["usb", "d+", "d-"]),
            ("CCT_uart", ["uart", "max3232", "rs232"]),
            ("CCT_i2c", ["i2c", "sda", "scl"]),
            ("CCT_spi", ["spi", "miso", "mosi", "sclk"]),
            ("CCT_crystal", ["crystal", "xtal", "32.768"]),
            ("CCT_clock_gen", ["pll", "clock gen"]),
            ("CCT_reset", ["reset", "supervisor", "tlc7705"]),
            ("CCT_watchdog", ["watchdog", "stp4528"]),
            ("CCT_memory", ["ddr", "sdram"]),
            ("CCT_hdmi", ["hdmi", "tmds"]),
            ("CCT_displayport", ["displayport", "main link"]),
            ("CCT_lvds", ["lvds"]),
            ("CCT_edp", ["edp"]),
            ("CCT_battery_mgmt", ["gas gauge", "bms", "bq20z"]),
            ("CCT_motor_driver", ["h-bridge", "motor driver"]),
            ("CCT_current_sense", ["sense resistor", "current sense"]),
        ]
        for circuit_id, keywords in rules:
            if all(kw in bag for kw in keywords):
                return circuit_id
        return None

    # --- graph-based netlist analysis (Priority 2) ---------------------

    def analyze_netlist(self, netlist_dict: dict) -> EngineConclusion:
        """Analyze a structured netlist using graph-based reasoning.

        This is the P2 entry point. It:
          1. Builds a Netlist graph from the dict.
          2. Validates graph consistency (undeclared nets, floating
             nets, missing ground).
          3. Recognizes the circuit structure from connectivity.
          4. Returns a conclusion with the recognized structure,
             validation issues, and a recommended next test.

        Unlike recognize_from_component_list (which matches on
        component-type keywords), this method reasons over the actual
        pin/net connectivity, so two netlists with the SAME components
        but DIFFERENT connectivity produce different results.
        """
        from DiagnosticEngines.CircuitAnalysis.netlist import Netlist
        conclusion = EngineConclusion(
            engine="circuit_analysis.netlist", rule_id=None, next_steps=[])
        nl = Netlist.from_dict(netlist_dict)
        issues = nl.validate()

        # If the netlist is empty or has errors, surface NEED_DATA.
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            conclusion.requires_info = True
            conclusion.requires_info_note = (
                "Netlist validation failed: " + " | ".join(i.message for i in errors)
            )
            conclusion.rule_id = "CCT_NETLIST_INVALID"
            conclusion.notes.append(f"Validation issues: {len(issues)} "
                                    f"({len(errors)} errors).")
            return conclusion

        # Recognize structure from connectivity.
        structure = nl.recognize_structure()
        conclusion.rule_id = f"CCT_NETLIST_{structure.upper()}"
        if structure == "unknown":
            conclusion.requires_info = True
            conclusion.requires_info_note = (
                "Netlist connectivity did not match any known structure. "
                "The graph is valid but the topology is unrecognized. "
                "Supply more pin-function detail or identify the circuit "
                "family explicitly.")
        else:
            conclusion.notes.append(
                f"Structure recognized from connectivity: {structure}. "
                f"This recognition is graph-driven (pin functions + net "
                f"connectivity), not part-name-driven.")
        # Record any warnings (floating nets etc.).
        warnings = [i for i in issues if i.severity == "warning"]
        if warnings:
            conclusion.notes.append(
                "Validation warnings: " + " | ".join(i.message for i in warnings))
        # Path trace from power_in to power_out if both exist.
        from DiagnosticEngines.CircuitAnalysis.netlist import PinFunction
        in_comp = next((c for c in nl.components
                        if c.pin_by_function(PinFunction.POWER_IN)), None)
        out_comp = next((c for c in nl.components
                         if c.pin_by_function(PinFunction.POWER_OUT)), None)
        if in_comp and out_comp:
            in_pin = in_comp.pin_by_function(PinFunction.POWER_IN)
            out_pin = out_comp.pin_by_function(PinFunction.POWER_OUT)
            if in_pin and out_pin:
                path = nl.trace_path(in_pin.net, out_pin.net)
                if path:
                    hops = " -> ".join(
                        f"{s.component.refdes}({s.net_in}->{s.net_out})"
                        for s in path)
                    conclusion.notes.append(f"Power path: {hops}")
        # Recommended next test.
        conclusion.next_steps.append({
            "step": 1,
            "action": (
                f"Recognized structure: {structure}. Measure the input and "
                f"output nets of the recognized structure. If the structure "
                f"is 'unknown', supply pin-function detail (power_in, "
                f"switch_output, feedback, ground) to enable recognition."),
            "measure": {
                "what": "Input and output nets of the recognized structure",
                "where": "Per the netlist's power_in and output nets",
                "instrument": "DMM DC volts",
                "expected": "Input present, output at the regulated value.",
                "safety": "Low-voltage. Disconnect input before resistance.",
            },
            "interpretations": [],
            "requires_info": structure == "unknown",
        })
        return conclusion

