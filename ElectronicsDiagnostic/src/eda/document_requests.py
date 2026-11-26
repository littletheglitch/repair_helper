"""Document request manager.

The reasoning engine decides when a document is genuinely needed and
asks for one specific file with a clear justification. The user
explicitly submits the file (drag-drop or path). The application never
scans drives or imports automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from . import database


@dataclass
class DocumentRequest:
    """A request for a specific document, with the reason it is needed."""
    kind: str           # datasheet | schematic | boardview | bios | manual | image | pdf
    name_hint: str      # e.g. "TPS51716 datasheet" or "G50-80 schematic"
    fields_needed: List[str] = field(default_factory=list)  # e.g. ["EN pin","UVLO","PGOOD"]
    rationale: str = ""
    optional: bool = False

    def render_prompt(self) -> str:
        head = f"I need the {self.kind}: {self.name_hint}."
        why = f"\nReason: {self.rationale}" if self.rationale else ""
        fields = ""
        if self.fields_needed:
            joined = "\n".join(f"  - {f}" for f in self.fields_needed)
            fields = f"\nInformation I want to extract:\n{joined}"
        tail = (
            "\nPlease drag and drop the file into this CMD window, or paste "
            "its full path. The application will process only this file."
        )
        opt = (
            "\n(This document is helpful but optional for the next step.)"
            if self.optional else ""
        )
        return head + why + fields + tail + opt


def record_request(session_id: int, req: DocumentRequest) -> None:
    """Persist the fact that a document was requested, for the timeline."""
    database.add_timeline(
        session_id, "document-request",
        req.render_prompt().replace("\n", " "),
    )
    database.touch_session(session_id)
