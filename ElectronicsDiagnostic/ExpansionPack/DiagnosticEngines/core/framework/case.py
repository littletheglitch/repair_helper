"""Case and session management for the Core Diagnostic Framework.

A Case is the top-level container for one repair job. A DiagnosticSession
(see session.py) is the live working state inside a case: the running
list of measurements, hypotheses, evidence, and engine conclusions.

Both are pure data classes with explicit JSON serialization so the host
application can persist them however it wants (file, SQLite, etc.).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class CaseStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    AWAITING_PARTS = "awaiting_parts"
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    ABANDONED = "abandoned"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class Case:
    """One repair job.

    The Case holds identity + symptoms + status. The live diagnostic
    state (measurements, hypotheses, evidence) lives in the
    DiagnosticSession attached to the case.

    Field semantics:
      * case_id: stable, host-assigned or generated. Never reused.
      * platform: 'laptop' | 'desktop' | 'power_supply' | 'other'.
        Selects which specialized engine the host should route to.
      * board_model / device_model / manufacturer: best-effort identity.
        Any may be None when unknown; the framework never assumes them.
      * confirmed_failure: filled only at case resolution. None until
        then. Setting it does NOT auto-close the case.
    """
    case_id: str = field(default_factory=lambda: _new_id("CASE"))
    platform: str = "laptop"
    manufacturer: Optional[str] = None
    device_model: Optional[str] = None
    board_model: Optional[str] = None
    symptoms: list[str] = field(default_factory=list)
    initial_observations: list[str] = field(default_factory=list)
    technician_notes: list[str] = field(default_factory=list)
    status: CaseStatus = CaseStatus.OPEN
    created_at_utc: str = field(default_factory=_utc_now_iso)
    updated_at_utc: str = field(default_factory=_utc_now_iso)
    confirmed_failure: Optional[str] = None
    repair_action: Optional[str] = None
    final_verification: Optional[str] = None
    technician: Optional[str] = None
    custom_fields: dict = field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at_utc = _utc_now_iso()

    def add_observation(self, text: str) -> None:
        if text and text not in self.initial_observations:
            self.initial_observations.append(text)
            self.touch()

    def add_note(self, text: str) -> None:
        if text:
            self.technician_notes.append(text)
            self.touch()

    def resolve(self, confirmed_failure: str, repair_action: str,
                final_verification: Optional[str] = None) -> None:
        if not confirmed_failure or not repair_action:
            raise ValueError("resolve requires confirmed_failure and repair_action")
        self.confirmed_failure = confirmed_failure
        self.repair_action = repair_action
        self.final_verification = final_verification
        self.status = CaseStatus.RESOLVED
        self.touch()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Case":
        d = dict(d)
        if isinstance(d.get("status"), str):
            d["status"] = CaseStatus(d["status"])
        return cls(**d)
