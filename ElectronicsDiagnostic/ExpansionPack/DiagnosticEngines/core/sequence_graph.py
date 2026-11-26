"""Power-sequence dependency graph.

Builds a directed graph of rail dependencies from the rail definitions'
`upstream_rails` / `downstream_rails` fields and answers:

  * What rails must be up before rail X can come up?
  * What is the earliest rail in the sequence that is missing?
  * What PGOOD / EN signals are required for rail X and which are present?
  * Where in the sequence is the failure point?

The graph is built from rail definitions only. It does NOT assume any
voltage, pin number, or controller; it only knows rail-family names and
their declared dependencies. Platform (laptop vs desktop) is preserved
so the graph never crosses between a laptop rail and a desktop rail.

The graph is read-only; it never mutates the rail definitions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import json


@dataclass
class SequenceNode:
    rail_family: str
    platform: str
    always_on: bool
    upstream: list[str] = field(default_factory=list)
    downstream: list[str] = field(default_factory=list)


@dataclass
class SequenceFailure:
    """The earliest point in a rail's prerequisite chain that is not
    satisfied. Identifies the rail that should be debugged first."""
    target_rail: str
    earliest_missing_rail: str
    missing_chain: list[str]          # ordered list from target back to the missing rail
    reason: str                       # 'rail_not_measured' | 'rail_measured_zero' | 'rail_short'
    notes: str = ""


@dataclass
class SequenceAnalysis:
    target_rail: str
    can_come_up: bool                 # all measured prerequisites are good
    missing_prerequisites: list[str] = field(default_factory=list)  # not measured yet
    failed_prerequisites: list[str] = field(default_factory=list)  # measured bad
    shorted_prerequisites: list[str] = field(default_factory=list)
    earliest_failure: Optional[SequenceFailure] = None
    unknown_rails: list[str] = field(default_factory=list)  # referenced but not defined


class PowerSequenceGraph:
    """Directed graph of rail dependencies, built from rail definitions."""

    def __init__(self, rails: dict[str, dict]):
        self.nodes: dict[str, SequenceNode] = {}
        for fam, rail in rails.items():
            platform = rail.get("platform", "laptop")
            self.nodes[fam] = SequenceNode(
                rail_family=fam,
                platform=platform,
                always_on=rail.get("always_on", False),
                upstream=list(rail.get("upstream_rails", []) or []),
                downstream=list(rail.get("downstream_rails", []) or []),
            )

    # --- queries -------------------------------------------------------

    def platform_of(self, rail_family: str) -> Optional[str]:
        n = self.nodes.get(rail_family)
        return n.platform if n else None

    def upstream_chain(self, rail_family: str, visited: Optional[set] = None) -> list[str]:
        """Return the full set of rails that must be up before
        `rail_family` can come up, in BFS order (closest first).
        Detects and skips cycles."""
        if rail_family not in self.nodes:
            return []
        if visited is None:
            visited = set()
        out: list[str] = []
        seen: set[str] = {rail_family}
        frontier = [rail_family]
        while frontier:
            current = frontier.pop(0)
            for up in self.nodes[current].upstream:
                if up in seen or up in visited:
                    continue
                seen.add(up)
                out.append(up)
                if up in self.nodes:
                    frontier.append(up)
        return out

    def analyze(self, target_rail: str,
                rail_states: dict[str, str]) -> SequenceAnalysis:
        """Analyze whether `target_rail` can come up given the measured
        state of its prerequisites.

        `rail_states` maps rail_family -> one of:
            'good'           - measured present and at expected voltage
            'zero'           - measured present but 0 V
            'short'          - measured short to ground
            'not_measured'   - slot exists, no reading
            'unknown_rail'   - rail referenced but not in the graph

        Returns a SequenceAnalysis identifying:
          * missing prerequisites (not_measured) — the engine needs these
          * failed prerequisites (zero) — diagnose these first
          * shorted prerequisites (short) — diagnose these first, do NOT power
          * the earliest failure in the chain (closest to the target)
        """
        analysis = SequenceAnalysis(target_rail=target_rail, can_come_up=True)
        if target_rail not in self.nodes:
            analysis.can_come_up = False
            analysis.unknown_rails.append(target_rail)
            return analysis

        chain = self.upstream_chain(target_rail)
        # Walk from the target outward; the FIRST bad rail in BFS order
        # is the earliest failure (closest to the target).
        earliest: Optional[SequenceFailure] = None
        for rail in chain:
            if rail not in self.nodes:
                analysis.unknown_rails.append(rail)
                continue
            state = rail_states.get(rail, "not_measured")
            if state == "not_measured":
                analysis.missing_prerequisites.append(rail)
                analysis.can_come_up = False  # cannot decide without this
            elif state == "zero":
                analysis.failed_prerequisites.append(rail)
                analysis.can_come_up = False
                if earliest is None:
                    earliest = SequenceFailure(
                        target_rail=target_rail,
                        earliest_missing_rail=rail,
                        missing_chain=[r for r in chain if r != rail],
                        reason="rail_measured_zero",
                        notes=f"{rail} measured present but at 0 V; diagnose {rail} before {target_rail}.",
                    )
            elif state == "short":
                analysis.shorted_prerequisites.append(rail)
                analysis.can_come_up = False
                if earliest is None:
                    earliest = SequenceFailure(
                        target_rail=target_rail,
                        earliest_missing_rail=rail,
                        missing_chain=[r for r in chain if r != rail],
                        reason="rail_short",
                        notes=f"{rail} measured short to ground; do NOT apply power to {target_rail}. Diagnose the short on {rail} first.",
                    )
            elif state == "good":
                pass
            # 'unknown_rail' handled above

        analysis.earliest_failure = earliest
        return analysis

    def rails_by_platform(self, platform: str) -> list[str]:
        return [fam for fam, n in self.nodes.items() if n.platform == platform]


def load_sequence_graph(pack_root: Path) -> PowerSequenceGraph:
    """Build a sequence graph from the PowerRails directory."""
    rails_root = pack_root / "DiagnosticEngines" / "PowerRails"
    rails: dict[str, dict] = {}
    for f in sorted(rails_root.glob("*.json")):
        if f.name.startswith("RULE_"):
            continue
        with open(f, "r", encoding="utf-8") as fh:
            rail = json.load(fh)
        rails[rail["rail_family"]] = rail
    return PowerSequenceGraph(rails)
