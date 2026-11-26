"""Motherboard Diagnostic Engine.

Wraps the power-rail engine + power-sequence graph and adds
platform-aware dispatch (laptop vs desktop). The engine produces
framework `EngineConclusion` objects so the host can merge results
into a DiagnosticSession uniformly.

The engine deliberately does NOT re-implement measurement handling,
evidence, hypotheses, or reporting — those live in the Core Diagnostic
Framework. This engine only:
  1. Validates the platform (laptop/desktop) and routes accordingly.
  2. Runs the power-rail engine on the requested rail.
  3. Runs the power-sequence graph to identify the earliest failed stage.
  4. Returns a structured conclusion with hypotheses + evidence + next steps.

Safety: the engine consults the framework SafetyPolicy before emitting
any next step that involves probing, and blocks mains-side / powered
resistance measurements at the source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import json
import sys

# Allow running both from inside the pack and from a host that has the
# pack on sys.path.
PACK_ROOT = Path(__file__).resolve().parents[2]
if str(PACK_ROOT) not in sys.path:
    sys.path.insert(0, str(PACK_ROOT))

from DiagnosticEngines.core.engine import DiagnosticEngine as _LegacyEngine  # noqa: E402
from DiagnosticEngines.core.power_rail_bridge import diagnose_with_framework_measurements  # noqa: E402
from DiagnosticEngines.core.sequence_graph import load_sequence_graph, PowerSequenceGraph, SequenceAnalysis  # noqa: E402
from DiagnosticEngines.core.power_on_sequence import PowerOnSequenceEngine, PowerOnSequenceInput  # noqa: E402
from DiagnosticEngines.core.framework import (  # noqa: E402
    Measurement, MeasurementState, MeasurementKind, MeasurementCondition,
    Hypothesis, Evidence, EvidenceType, EvidenceStrength,
    SafetyPolicy, SafetyLevel,
)
from DiagnosticEngines.core.framework.session import EngineConclusion  # noqa: E402


PLATFORM_LAPTOP = "laptop"
PLATFORM_DESKTOP = "desktop"


@dataclass
class MotherboardDiagnosisInput:
    """Inputs the host passes to the motherboard engine."""
    platform: str                            # 'laptop' or 'desktop'
    rail_family: str                         # target rail family
    measurements: list[Measurement]          # framework measurements
    controller_part: Optional[str] = None
    rail_states: dict[str, str] = field(default_factory=dict)
    # ^ map of rail_family -> 'good'/'zero'/'short'/'not_measured' for the sequence graph


class MotherboardEngine:
    """Specialized engine for laptop and desktop motherboards.

    The engine is read-only with respect to the pack's data files. It
    loads the legacy power-rail engine + sequence graph once at
    construction and reuses them for every diagnosis.
    """

    def __init__(self, pack_root: Optional[Path] = None):
        self.pack_root = pack_root or PACK_ROOT
        self.legacy_engine = _LegacyEngine(self.pack_root)
        self.sequence_graph: PowerSequenceGraph = load_sequence_graph(self.pack_root)
        self.safety = SafetyPolicy()
        self.power_on_sequence = PowerOnSequenceEngine()

    # --- public API ----------------------------------------------------

    def supported_rails(self, platform: Optional[str] = None) -> list[str]:
        """List rail families the engine knows about, optionally
        filtered by platform."""
        if platform is None:
            return sorted(self.sequence_graph.nodes.keys())
        return sorted(self.sequence_graph.rails_by_platform(platform))

    def diagnose_power_on_sequence(self, inp: PowerOnSequenceInput) -> EngineConclusion:
        """Diagnose the board's power-on sequence (adapter -> standby ->
        EC -> button -> enables -> POST -> display). Identifies the
        earliest failed stage and the fault-state classification.

        This is the high-level board-level diagnostic. For single-rail
        deep-dive, use diagnose()."""
        return self.power_on_sequence.diagnose(inp)

    def diagnose(self, inp: MotherboardDiagnosisInput) -> EngineConclusion:
        """Run the motherboard diagnostic workflow.

        Returns a framework EngineConclusion. The conclusion may carry:
          * the power-rail engine's next steps (if a rule fired)
          * a sequence-graph analysis identifying the earliest failed stage
          * a hypothesis capturing the current suspicion
          * safety violations if any next step is blocked
        """
        # 1. Platform validation
        if inp.platform not in (PLATFORM_LAPTOP, PLATFORM_DESKTOP):
            return EngineConclusion(
                engine="motherboard",
                rule_id=None,
                next_steps=[],
                requires_info=True,
                requires_info_note=(
                    f"Unknown platform {inp.platform!r}. "
                    f"Supported: 'laptop', 'desktop'."
                ),
            )

        # 2. Rail validation + platform match
        rail = self.legacy_engine.all_rails().get(inp.rail_family)
        if rail is None:
            return EngineConclusion(
                engine="motherboard",
                rule_id=None,
                next_steps=[],
                requires_info=True,
                requires_info_note=(
                    f"Rail family {inp.rail_family!r} not found. "
                    f"Available for platform {inp.platform!r}: "
                    f"{self.supported_rails(inp.platform)[:10]}..."
                ),
            )
        rail_platform = rail.get("platform", "laptop")
        if rail_platform not in (inp.platform, "universal"):
            return EngineConclusion(
                engine="motherboard",
                rule_id=None,
                next_steps=[],
                requires_info=True,
                requires_info_note=(
                    f"Rail {inp.rail_family!r} is a {rail_platform!r} rail; "
                    f"cannot diagnose it on platform {inp.platform!r}."
                ),
            )

        # 3. Run the power-rail engine via the bridge
        conclusion = diagnose_with_framework_measurements(
            self.legacy_engine,
            rail_family=inp.rail_family,
            measurements=inp.measurements,
            controller_part=inp.controller_part,
            topology=rail.get("typical_topology"),
        )
        conclusion.engine = f"motherboard.{inp.platform}"

        # 4. Run the sequence-graph analysis in parallel
        seq_analysis = self.sequence_graph.analyze(
            target_rail=inp.rail_family,
            rail_states=inp.rail_states or {},
        )
        self._merge_sequence_into_conclusion(conclusion, seq_analysis, inp)

        # 5. Safety-check any next steps and surface violations
        self._safety_check_next_steps(conclusion)

        return conclusion

    # --- internals -----------------------------------------------------

    def _merge_sequence_into_conclusion(self, conclusion: EngineConclusion,
                                         seq: SequenceAnalysis,
                                         inp: MotherboardDiagnosisInput) -> None:
        """Add sequence-graph findings to the conclusion as notes +
        a hypothesis when an earliest failure was identified."""
        if seq.unknown_rails:
            conclusion.notes.append(
                f"Sequence graph references unknown rail(s): {seq.unknown_rails}. "
                f"Sequence analysis may be incomplete."
            )
        if seq.missing_prerequisites:
            conclusion.notes.append(
                f"Sequence: prerequisites not yet measured: "
                f"{seq.missing_prerequisites}. Cannot decide whether "
                f"{inp.rail_family} can come up until these are measured."
            )
        if seq.shorted_prerequisites:
            conclusion.notes.append(
                f"Sequence: SHORTED prerequisite rail(s): "
                f"{seq.shorted_prerequisites}. Do NOT apply power to "
                f"{inp.rail_family}; diagnose the short first."
            )
            # Add a hypothesis that the shorted upstream rail is the root cause.
            for shorted in seq.shorted_prerequisites:
                h = Hypothesis(
                    statement=(
                        f"Root cause is a short on upstream rail "
                        f"{shorted!r} blocking {inp.rail_family!r}."
                    ),
                    engine=conclusion.engine,
                    rule_source="SEQUENCE_GRAPH",
                )
                h.add_evidence(Evidence(
                    type=EvidenceType.SUPPORTING,
                    strength=EvidenceStrength.STRONG,
                    description=f"{shorted} measured short to ground",
                    rule_source="SEQUENCE_GRAPH",
                    source_kind="measurement",
                    hypothesis_id=h.hypothesis_id,
                ))
                conclusion.new_hypotheses.append(h)
        if seq.failed_prerequisites:
            conclusion.notes.append(
                f"Sequence: FAILED (measured zero) prerequisite rail(s): "
                f"{seq.failed_prerequisites}. Diagnose these first; "
                f"{inp.rail_family} cannot come up without them."
            )
        if seq.earliest_failure:
            ef = seq.earliest_failure
            conclusion.notes.append(
                f"Sequence earliest failure: {ef.earliest_missing_rail} "
                f"({ef.reason}). {ef.notes}"
            )

    def _safety_check_next_steps(self, conclusion: EngineConclusion) -> None:
        """Run the SafetyPolicy over each next step's action+measure.
        Blocked steps are kept (so the host can show why) but flagged."""
        for step in conclusion.next_steps:
            action_text = step.get("action", "")
            measure = step.get("measure") or {}
            proposed = {
                "what": measure.get("what", action_text),
                "where": measure.get("where", ""),
                "instrument": measure.get("instrument", ""),
                "safety": measure.get("safety", ""),
                "action": action_text,
            }
            violations = self.safety.evaluate(proposed)
            if violations:
                conclusion.safety_violations.extend(violations)
                blocked = any(v.level == SafetyLevel.BLOCK for v in violations)
                if blocked:
                    step["blocked_by_safety"] = True
                    step["safety_violations"] = [v.to_dict() for v in violations
                                                 if v.level == SafetyLevel.BLOCK]
