"""Report builder — Markdown + structured report from a DiagnosticSession.

The host application already has its own Markdown and PDF reporting
(ReportLab). This builder produces a Markdown string and a structured
dict that the host can render into PDF or its own format. It does NOT
call ReportLab directly — that keeps the framework dependency-free and
lets the host decide how to render.

The report includes: case header, symptoms, diagnostic history
(measurements, hypotheses, evidence, conclusions), confirmed fault,
repair action, source references, and safety violations.
"""
from __future__ import annotations

from typing import Optional

from .session import DiagnosticSession
from .hypothesis import HypothesisStatus
from .evidence import EvidenceType
from .safety import SafetyLevel


class ReportBuilder:
    """Build a Markdown report and a structured dict from a session."""

    def __init__(self, session: DiagnosticSession):
        self.session = session

    # --- structured dict ----------------------------------------------

    def to_dict(self) -> dict:
        s = self.session
        return {
            "case": s.case.to_dict(),
            "measurement_count": len(s.measurements),
            "hypothesis_count": len(s.hypotheses),
            "conclusion_count": len(s.conclusions),
            "confirmed_hypothesis": (
                s.confirmed_hypothesis().to_dict()
                if s.confirmed_hypothesis() else None
            ),
            "top_hypotheses": [h.to_dict() for h in s.ranked_hypotheses()[:5]],
            "safety_violations": [sv.to_dict() for sv in s.safety_violations],
            "measurements": [m.to_dict() for m in s.measurements],
            "conclusions": [
                {
                    "engine": c.engine,
                    "rule_id": c.rule_id,
                    "requires_info": c.requires_info,
                    "requires_info_note": c.requires_info_note,
                    "next_steps": c.next_steps,
                    "notes": c.notes,
                    "concluded_at_utc": c.concluded_at_utc,
                }
                for c in s.conclusions
            ],
        }

    # --- markdown -----------------------------------------------------

    def to_markdown(self) -> str:
        s = self.session
        c = s.case
        lines: list[str] = []
        lines.append(f"# Diagnostic Report — Case {c.case_id}")
        lines.append("")
        lines.append(f"**Platform:** {c.platform}  ")
        lines.append(f"**Manufacturer:** {c.manufacturer or 'unknown'}  ")
        lines.append(f"**Device model:** {c.device_model or 'unknown'}  ")
        lines.append(f"**Board model:** {c.board_model or 'unknown'}  ")
        lines.append(f"**Status:** {c.status.value}  ")
        lines.append(f"**Technician:** {c.technician or 'unspecified'}  ")
        lines.append(f"**Created:** {c.created_at_utc}  ")
        lines.append(f"**Updated:** {c.updated_at_utc}  ")
        lines.append("")

        if c.symptoms:
            lines.append("## Symptoms")
            for sym in c.symptoms:
                lines.append(f"- {sym}")
            lines.append("")

        if c.initial_observations:
            lines.append("## Initial observations")
            for obs in c.initial_observations:
                lines.append(f"- {obs}")
            lines.append("")

        if s.measurements:
            lines.append("## Measurements")
            lines.append("| Name | Kind | State | Value | Unit | Condition | Instrument | Verified |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
            for m in s.measurements:
                val = "" if m.value is None else str(m.value)
                lines.append(
                    f"| {m.name} | {m.kind.value} | {m.state.value} | {val} | "
                    f"{m.unit} | {m.condition.value} | {m.instrument} | "
                    f"{'yes' if m.verified else 'no'} |"
                )
            lines.append("")

        if s.conclusions:
            lines.append("## Diagnostic history")
            for i, con in enumerate(s.conclusions, 1):
                lines.append(f"### Step {i}: {con.engine}")
                if con.rule_id:
                    lines.append(f"- Rule fired: `{con.rule_id}`")
                if con.requires_info:
                    lines.append(f"- **Requires more info:** {con.requires_info_note}")
                for ns in con.next_steps:
                    lines.append(f"- Next: {ns.get('action', ns.get('what', ''))}")
                for note in con.notes:
                    lines.append(f"- Note: {note}")
                lines.append("")

        if s.hypotheses:
            lines.append("## Hypotheses (ranked)")
            for h in s.ranked_hypotheses():
                marker = " **[CONFIRMED]**" if h.status == HypothesisStatus.CONFIRMED else ""
                lines.append(f"### {h.statement}{marker}")
                lines.append(f"- Status: `{h.status.value}`")
                lines.append(f"- Engine: `{h.engine}`  Rule: `{h.rule_source}`")
                if h.evidence:
                    lines.append("- Evidence:")
                    for ev in h.evidence:
                        arrow = {
                            EvidenceType.SUPPORTING: "supports",
                            EvidenceType.CONTRADICTING: "contradicts",
                            EvidenceType.MISSING: "missing",
                            EvidenceType.AMBIGUOUS: "ambiguous",
                            EvidenceType.BACKGROUND: "background",
                        }[ev.type]
                        lines.append(f"  - [{ev.strength.value}] {arrow}: {ev.description} "
                                     f"_(source: {ev.rule_source})_")
                lines.append("")

        if s.safety_violations:
            lines.append("## Safety violations")
            for sv in s.safety_violations:
                level_marker = "🛑" if sv.level == SafetyLevel.BLOCK else "⚠️"
                lines.append(f"- {level_marker} **{sv.level.value}** `{sv.rule}`: {sv.message}")
            lines.append("")

        if c.confirmed_failure:
            lines.append("## Confirmed failure")
            lines.append(f"**{c.confirmed_failure}**")
            lines.append("")
            lines.append("## Repair action")
            lines.append(c.repair_action or "_not recorded_")
            if c.final_verification:
                lines.append("")
                lines.append("## Final verification")
                lines.append(c.final_verification)
            lines.append("")

        if not c.confirmed_failure:
            lines.append("## Outcome")
            lines.append("_No confirmed failure yet. Diagnostic continues._")
            lines.append("")

        return "\n".join(lines)
