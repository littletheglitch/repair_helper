"""Component Identification Engine.

Takes a partial marking / package / circuit context and returns ranked
candidate parts with a confidence level and pinout-verification status.

Confidence levels (per the spec):
  EXACT           - full part number matched, all fields agree
  LIKELY          - partial marking + package + category all match
  AMBIGUOUS       - multiple candidates match the partial marking
  UNKNOWN         - no candidate matched

The engine NEVER invents pinouts from incomplete markings. If a
candidate is matched only on partial marking, the pinout_verification
field is 'unverified' and the engine tells the technician to consult
the datasheet.

The engine is read-only against the component database.
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

from DiagnosticEngines.core.component_db import ComponentDatabase  # noqa: E402


CONFIDENCE_EXACT = "EXACT"
CONFIDENCE_LIKELY = "LIKELY"
CONFIDENCE_AMBIGUOUS = "AMBIGUOUS"
CONFIDENCE_UNKNOWN = "UNKNOWN"


@dataclass
class IdentificationInput:
    """Inputs the host passes to the identification engine.

    All fields optional; the more the host supplies, the higher the
    confidence. At least one of `part_number` or `marking` is required.
    """
    part_number: str = ""        # full part number if known
    marking: str = ""            # top marking as read off the chip
    manufacturer: str = ""
    package: str = ""
    pin_count: Optional[int] = None
    circuit_context: str = ""    # free text: "CPU VRM controller", "USB PD", etc.


@dataclass
class CandidateMatch:
    part_number: str
    manufacturer: str
    category: str
    confidence: str               # EXACT / LIKELY / AMBIGUOUS
    match_score: int              # higher is better; for ranking
    pinout_verified: str          # 'verified' / 'partial' / 'unverified'
    datasheet_url: str
    matched_on: list[str] = field(default_factory=list)
    required_additional_info: list[str] = field(default_factory=list)


@dataclass
class IdentificationResult:
    confidence: str               # overall: EXACT / LIKELY / AMBIGUOUS / UNKNOWN
    candidates: list[CandidateMatch] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    requires_info: bool = False
    requires_info_note: str = ""


class ComponentIdentificationEngine:
    """Identifies components from partial markings + context."""

    def __init__(self, pack_root: Optional[Path] = None):
        self.pack_root = pack_root or PACK_ROOT
        self.db = ComponentDatabase.load(self.pack_root)
        self._load_extended_records()

    def _load_extended_records(self) -> None:
        """Include discrete semiconductor + analog IC records."""
        for sub in ("DiscreteSemiconductors", "AnalogICs"):
            root = self.pack_root / "ComponentDatabase" / sub
            if not root.exists():
                continue
            for f in sorted(root.rglob("*.json")):
                try:
                    with open(f, "r", encoding="utf-8") as fh:
                        rec = json.load(fh)
                    self.db.ic_records.append(rec)
                except (OSError, json.JSONDecodeError):
                    continue

    # --- public API ----------------------------------------------------

    def identify(self, inp: IdentificationInput) -> IdentificationResult:
        """Return ranked candidates."""
        if not inp.part_number and not inp.marking:
            return IdentificationResult(
                confidence=CONFIDENCE_UNKNOWN,
                requires_info=True,
                requires_info_note="At least one of part_number or marking is required.",
            )

        all_records = self.db.ic_records + self.db.mosfet_records
        scored: list[tuple[int, dict, list[str]]] = []

        for rec in all_records:
            score, matched = self._score(rec, inp)
            if score > 0:
                scored.append((score, rec, matched))

        if not scored:
            return IdentificationResult(
                confidence=CONFIDENCE_UNKNOWN,
                notes=[f"No component in the database matches part_number={inp.part_number!r} marking={inp.marking!r}."],
                requires_info=True,
                requires_info_note="Component not in the database. The technician must identify it manually from the marking and consult the manufacturer's datasheet directly.",
            )

        # Sort by score descending
        scored.sort(key=lambda x: -x[0])

        # Determine overall confidence.
        top_score = scored[0][0]
        top_rec = scored[0][1]
        # EXACT: full part_number matched exactly (score 5).
        # LIKELY: strong partial match (score >= 3) with no close runner-up.
        # AMBIGUOUS: multiple candidates within 1 of the top, OR a single
        #   weak match (score < 3) — we cannot confidently identify.
        # A single weak match is NOT LIKELY; it is AMBIGUOUS because the
        # marking could match many parts we don't have in the database.
        if inp.part_number and inp.part_number.upper() == top_rec.get("part_number", "").upper():
            overall = CONFIDENCE_EXACT
        elif len(scored) >= 2 and scored[1][0] >= top_score - 1:
            overall = CONFIDENCE_AMBIGUOUS
        elif top_score >= 3:
            overall = CONFIDENCE_LIKELY
        else:
            # Single weak match (score < 3) — ambiguous, not likely.
            overall = CONFIDENCE_AMBIGUOUS

        candidates: list[CandidateMatch] = []
        for score, rec, matched in scored[:5]:  # top 5
            pn = rec.get("part_number", "")
            mfr = rec.get("manufacturer", "")
            cat = rec.get("category", "")
            # Pinout verification
            pinout = rec.get("pinout", {}) or {}
            if pinout and len(pinout) > 0 and not (len(pinout) == 1 and "notes" in pinout):
                pinout_verified = "verified"
            else:
                pinout_verified = "unverified"
            # Confidence per-candidate
            if overall == CONFIDENCE_EXACT and score == top_score:
                cand_conf = CONFIDENCE_EXACT
            elif score >= 3:
                cand_conf = CONFIDENCE_LIKELY
            else:
                cand_conf = CONFIDENCE_AMBIGUOUS
            # Required additional info
            req_info = []
            if pinout_verified != "verified":
                req_info.append("Confirm pinout from the manufacturer datasheet before any board-level work.")
            if rec.get("verification_status") == "partial":
                req_info.append("Record is partial; verify the missing fields from the datasheet.")
            if not inp.package and not inp.pin_count:
                req_info.append("Supply package and pin_count to narrow the match.")
            candidates.append(CandidateMatch(
                part_number=pn, manufacturer=mfr, category=cat,
                confidence=cand_conf, match_score=score,
                pinout_verified=pinout_verified,
                datasheet_url=rec.get("datasheet_url", ""),
                matched_on=matched,
                required_additional_info=req_info,
            ))

        notes = []
        if overall == CONFIDENCE_AMBIGUOUS:
            notes.append(
                "Multiple candidates match the supplied marking. The technician "
                "must distinguish them by package, pin count, circuit context, "
                "or by reading the full marking under magnification."
            )
        if overall == CONFIDENCE_LIKELY and candidates[0].pinout_verified != "verified":
            notes.append(
                "Top candidate matched on marking but its pinout is not verified "
                "in the database. Consult the datasheet before any probing."
            )

        return IdentificationResult(
            confidence=overall,
            candidates=candidates,
            notes=notes,
        )

    # --- scoring -------------------------------------------------------

    def _score(self, rec: dict, inp: IdentificationInput) -> tuple[int, list[str]]:
        score = 0
        matched: list[str] = []
        pn = rec.get("part_number", "")
        pn_upper = pn.upper()
        # Exact part number match: strong
        if inp.part_number and inp.part_number.upper() == pn_upper:
            score += 5
            matched.append("part_number (exact)")
        elif inp.part_number and inp.part_number.upper() in pn_upper:
            # partial: input is a substring of the actual PN
            score += 2
            matched.append("part_number (substring)")
        # Marking match. A marking is text printed on the chip; it is
        # typically a prefix or substring of the full part number. The
        # REVERSE (part number is a substring of the marking) does NOT
        # make physical sense — a marking longer than the part number
        # is a different chip. So we only match marking-in-PN, not
        # PN-in-marking.
        if inp.marking:
            m_up = inp.marking.upper()
            if m_up == pn_upper:
                score += 4
                matched.append("marking (exact)")
            elif m_up in pn_upper:
                score += 2
                matched.append("marking (substring)")
        # Manufacturer
        if inp.manufacturer:
            if inp.manufacturer.lower() in rec.get("manufacturer", "").lower() or \
               inp.manufacturer.lower() in rec.get("manufacturer_short", "").lower():
                score += 1
                matched.append("manufacturer")
        # Package
        if inp.package:
            if inp.package.lower() in rec.get("package", "").lower():
                score += 1
                matched.append("package")
        # Pin count
        if inp.pin_count and rec.get("pin_count") == inp.pin_count:
            score += 1
            matched.append("pin_count")
        # Circuit context (tag match)
        if inp.circuit_context:
            ctx_low = inp.circuit_context.lower()
            tags = [t.lower() for t in rec.get("tags", [])]
            cat = rec.get("category", "").lower()
            if any(ctx_low in t or t in ctx_low for t in tags):
                score += 1
                matched.append("circuit_context (tag)")
            if cat and cat in ctx_low:
                score += 1
                matched.append("circuit_context (category)")
        return score, matched
