"""Failure Pattern Database + Repair Case Learning Engine.

Stores structured repair cases and supports:
  * search by board model, component, rail, symptom, failure pattern, topology
  * a controlled learning pipeline that promotes approved cases to
    reusable knowledge — but NEVER automatically. A case must pass
    required-field validation, separate observations from conclusions,
    and be marked confirmed/probable/unresolved before promotion.

The DB is JSON-file-backed (one file per case) for portability. A
SQLite store is NOT used because the spec says "SQLite only if genuinely
necessary" — JSON files are sufficient at this scale and stay portable.

Cases are stored under Tests/sample_cases/ (the seed scenarios shipped
with the pack) and under Knowledge/RepairCases/ (cases added by the
host application). Both directories are read at construction.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
import json
import sys
import uuid
from datetime import datetime, timezone

PACK_ROOT = Path(__file__).resolve().parents[2]
if str(PACK_ROOT) not in sys.path:
    sys.path.insert(0, str(PACK_ROOT))


CONFIDENCE_CONFIRMED = "confirmed"
CONFIDENCE_PROBABLE = "probable"
CONFIDENCE_UNRESOLVED = "unresolved"

PROMOTION_STATUS_PENDING = "pending"
PROMOTION_STATUS_APPROVED = "approved"
PROMOTION_STATUS_REJECTED = "rejected"


@dataclass
class RepairCase:
    """One real repair case. Matches the spec's required fields."""
    case_id: str = field(default_factory=lambda: f"RC_{uuid.uuid4().hex[:10]}")
    device_manufacturer: str = ""
    device_model: str = ""
    board_model: str = ""
    platform: str = ""                   # 'laptop' / 'desktop' / 'power_supply'
    symptoms: list[str] = field(default_factory=list)
    initial_observations: list[str] = field(default_factory=list)
    measurements: list[dict] = field(default_factory=list)
    diagnostic_path: list[str] = field(default_factory=list)
    confirmed_faulty_component: str = ""
    root_cause: str = ""
    repair_action: str = ""
    final_verification: str = ""
    technician_notes: str = ""
    source: str = ""                     # 'technician' / 'forum' / 'service_manual'
    confidence: str = CONFIDENCE_UNRESOLVED
    date: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    # Search helpers
    rails_involved: list[str] = field(default_factory=list)
    components_involved: list[str] = field(default_factory=list)
    topology: str = ""
    failure_pattern: str = ""            # e.g. 'no_output', 'shorted_rail'
    # Promotion pipeline
    promotion_status: str = PROMOTION_STATUS_PENDING
    promotion_notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RepairCase":
        return cls(**d)


@dataclass
class ValidationResult:
    valid: bool
    missing_fields: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


class FailurePatternDB:
    """Stores and searches repair cases. JSON-file backed."""

    def __init__(self, pack_root: Optional[Path] = None):
        self.pack_root = pack_root or PACK_ROOT
        self.case_dirs = [
            self.pack_root / "Knowledge" / "RepairCases",
            self.pack_root / "Tests" / "sample_cases",
        ]
        self.cases: list[RepairCase] = []
        self._load()

    def _load(self) -> None:
        for d in self.case_dirs:
            if not d.exists():
                continue
            for f in sorted(d.glob("*.json")):
                try:
                    with open(f, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    # sample_cases files are scenario descriptions, not
                    # full RepairCase records; skip files that don't have
                    # the required RepairCase shape.
                    if "case_id" in data or "confirmed_faulty_component" in data:
                        self.cases.append(RepairCase.from_dict(data))
                except (OSError, json.JSONDecodeError, TypeError):
                    continue

    # --- queries -------------------------------------------------------

    def search(self, *,
               board_model: Optional[str] = None,
               component: Optional[str] = None,
               rail: Optional[str] = None,
               symptom: Optional[str] = None,
               failure_pattern: Optional[str] = None,
               topology: Optional[str] = None,
               platform: Optional[str] = None,
               confidence: Optional[str] = None) -> list[RepairCase]:
        out = []
        for c in self.cases:
            if board_model and board_model.lower() not in c.board_model.lower():
                continue
            if component and not any(component.lower() in comp.lower()
                                     for comp in c.components_involved):
                continue
            if rail and not any(rail.lower() in r.lower()
                                for r in c.rails_involved):
                continue
            if symptom and not any(symptom.lower() in s.lower()
                                   for s in c.symptoms):
                continue
            if failure_pattern and failure_pattern.lower() != c.failure_pattern.lower():
                continue
            if topology and topology.lower() != c.topology.lower():
                continue
            if platform and platform.lower() != c.platform.lower():
                continue
            if confidence and confidence.lower() != c.confidence.lower():
                continue
            out.append(c)
        return out

    def count(self) -> int:
        return len(self.cases)

    # --- mutation ------------------------------------------------------

    def add_case(self, case: RepairCase) -> Path:
        """Persist a new case to Knowledge/RepairCases/."""
        out_dir = self.pack_root / "Knowledge" / "RepairCases"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{case.case_id}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(case.to_dict(), f, indent=2, ensure_ascii=False)
        self.cases.append(case)
        return out_path


class RepairCaseLearner:
    """Controlled learning pipeline.

    Steps (per the spec):
      1. Collect the repair record.
      2. Check required fields.
      3. Identify missing evidence.
      4. Separate observations from conclusions.
      5. Mark the result as confirmed, probable, or unresolved.
      6. Allow technician review.
      7. Promote approved patterns into reusable knowledge.

    Promotion is NEVER automatic. A case must be validated AND have its
    promotion_status set to 'approved' by a technician before it can be
    used as a reusable pattern.
    """

    REQUIRED_FIELDS = [
        "device_manufacturer", "device_model", "board_model", "platform",
        "symptoms", "confirmed_faulty_component", "root_cause",
        "repair_action", "source", "confidence",
    ]

    def validate(self, case: RepairCase) -> ValidationResult:
        missing = []
        for f in self.REQUIRED_FIELDS:
            v = getattr(case, f, None)
            if v is None or v == "" or v == []:
                missing.append(f)
        issues = []
        # Observation vs conclusion separation: measurements + observations
        # are observations; confirmed_faulty_component + root_cause are
        # conclusions. A case where the conclusion is asserted but no
        # observations support it is flagged.
        if case.confirmed_faulty_component and not case.measurements and not case.initial_observations:
            issues.append("Confirmed fault is asserted without any supporting observations or measurements.")
        if case.confidence == CONFIDENCE_CONFIRMED and not case.final_verification:
            issues.append("Confirmed cases should include final verification (re-test after repair).")
        if case.source == "forum" and case.confidence == CONFIDENCE_CONFIRMED:
            issues.append("Forum-sourced cases should not be marked 'confirmed' without independent verification.")
        return ValidationResult(
            valid=(not missing and not issues),
            missing_fields=missing,
            issues=issues,
        )

    def can_promote(self, case: RepairCase) -> tuple[bool, str]:
        """A case can be promoted only if:
          - validation passes
          - confidence is 'confirmed' or 'probable'
          - promotion_status is 'approved' (set by a technician)
        """
        vr = self.validate(case)
        if vr.missing_fields:
            return False, f"Missing required fields: {vr.missing_fields}"
        if vr.issues:
            return False, f"Validation issues: {vr.issues}"
        if case.confidence == CONFIDENCE_UNRESOLVED:
            return False, "Case is unresolved; cannot promote."
        if case.promotion_status != PROMOTION_STATUS_APPROVED:
            return False, (f"Promotion status is '{case.promotion_status}'. "
                           f"A technician must set promotion_status='approved' before promotion.")
        return True, "OK"

    def promote(self, case: RepairCase, db: FailurePatternDB) -> tuple[bool, str]:
        """Promote an approved case into the reusable knowledge DB.

        Promotion means: the case is persisted to Knowledge/RepairCases/
        with promotion_status='approved' so future searches surface it
        as a verified pattern. The case is NOT turned into a diagnostic
        rule — that requires a separate, manual rule-authoring step.
        """
        ok, reason = self.can_promote(case)
        if not ok:
            return False, reason
        # Persist (or re-persist) with the approved status
        db.add_case(case)
        return True, f"Case {case.case_id} promoted to reusable knowledge."
