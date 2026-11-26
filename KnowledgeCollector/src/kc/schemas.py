"""Typed schemas for the Knowledge Collector.

These dataclasses flow between the fetcher, connectors, filter,
extractor, classifier, dedup, store, and exporter. Keeping them typed
prevents the "pass a net name where a component designator belongs"
class of bug.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


# ----------------------------------------------------------------------
# Source configuration
# ----------------------------------------------------------------------

class SourceKind(str, Enum):
    STACKEXCHANGE_API = "stackexchange_api"
    FORUM_HTML = "forum_html"
    DOCUMENT_INDEX = "document_index"


@dataclass
class SourceConfig:
    name: str            # short, stable id ("eevblog", "stackexchange_electronics")
    kind: SourceKind
    base_url: str
    enabled: bool = False   # disabled by default; the user reviews terms first
    license: str = ""        # "CC-BY-SA 4.0" / "forum ToS" / "public domain"
    terms_url: str = ""
    attribution: str = ""    # required attribution text
    access_method: str = "" # "public API" / "HTML (robots-respecting)" / "manual export"
    max_depth: int = 5
    rate_per_min: int = 6
    max_records: int = 5000
    max_bytes: int = 500 * 1024 * 1024
    added_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


# ----------------------------------------------------------------------
# Classification
# ----------------------------------------------------------------------

class RecordClass(str, Enum):
    A_CONFIRMED_REPAIR = "A_technician_confirmed_repair"
    B_REPORTED_REPAIR = "B_reported_repair_insufficient_verification"
    C_ENGINEERING = "C_engineering_explanation"
    D_HYPOTHESIS = "D_diagnostic_hypothesis"
    E_SUGGESTED_TEST = "E_suggested_test"
    F_INCOMPLETE = "F_incomplete_or_uncertain"


# The authority order for tie-breaking (A is highest).
CLASS_AUTHORITY = {
    RecordClass.A_CONFIRMED_REPAIR: 0,
    RecordClass.C_ENGINEERING: 1,
    RecordClass.E_SUGGESTED_TEST: 2,
    RecordClass.D_HYPOTHESIS: 3,
    RecordClass.B_REPORTED_REPAIR: 4,
    RecordClass.F_INCOMPLETE: 5,
}


# Map Collector class -> assistant tier (the integration contract).
CLASS_TO_ASSISTANT_TIER: Dict[RecordClass, str] = {
    RecordClass.A_CONFIRMED_REPAIR: "technician-confirmed",
    RecordClass.B_REPORTED_REPAIR: "community",
    RecordClass.C_ENGINEERING: "verified-reference",
    RecordClass.D_HYPOTHESIS: "unverified",
    RecordClass.E_SUGGESTED_TEST: "community",
    RecordClass.F_INCOMPLETE: "unverified",
}


@dataclass
class Classification:
    cls: RecordClass
    explanation: str   # names the rule and the matched evidence
    matched_rules: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"cls": self.cls.value, "explanation": self.explanation,
                "matched_rules": self.matched_rules}


# ----------------------------------------------------------------------
# Record (one forum post / article / answer)
# ----------------------------------------------------------------------

@dataclass
class StructuredFields:
    """Extracted from the text; never fabricated."""
    component_model: List[str] = field(default_factory=list)   # e.g. ["TPS51716"]
    device_model: List[str] = field(default_factory=list)      # e.g. ["Lenovo G50-80"]
    motherboard_model: List[str] = field(default_factory=list)
    reported_symptoms: List[str] = field(default_factory=list)
    measurements: List[str] = field(default_factory=list)      # raw strings
    proposed_tests: List[str] = field(default_factory=list)
    repair_actions: List[str] = field(default_factory=list)
    reported_outcome: str = ""                                  # "fixed"/"not fixed"/""
    image_refs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Record:
    source_name: str
    url: str
    thread_id: str
    post_id: str
    title: str
    author: str
    published_at: str
    collected_at: str
    text: str                          # the relevant post text (preserved)
    structured: StructuredFields = field(default_factory=StructuredFields)
    classification: Optional[Classification] = None
    content_hash: str = ""             # SHA-256 of text
    normalized_hash: str = ""           # SHA-256 of normalized text
    also_seen_at: List[str] = field(default_factory=list)   # cross-source dup URLs
    variants: List[str] = field(default_factory=list)      # near-dup text variants
    status: str = "new"                # new | duplicate | near_duplicate | rejected
    rejection_reason: str = ""

    def __post_init__(self):
        if not self.content_hash:
            self.content_hash = sha256(self.text.encode("utf-8"))
        if not self.normalized_hash:
            self.normalized_hash = sha256(normalize_text(self.text).encode("utf-8"))

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# ----------------------------------------------------------------------
# Document (a collected PDF/HTML/text file)
# ----------------------------------------------------------------------

@dataclass
class Document:
    source_name: str
    url: str
    filename: str
    file_type: str        # "pdf" | "html" | "text" | "image" | "unknown" | "rejected"
    sha256: str
    size_bytes: int
    status: str           # extracted | partially_extracted | stored_only | failed | rejected
    meta: Dict[str, Any] = field(default_factory=dict)
    collected_at: str = ""
    original_path: str = ""     # relative to data/originals/
    extracted_text_path: str = ""  # relative to data/extracted/

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ----------------------------------------------------------------------
# Rejection (why an item was dropped)
# ----------------------------------------------------------------------

class RejectionReason(str, Enum):
    RELEVANCE = "relevance"
    SPAM = "spam"
    BIOS = "bios_dump"
    DUPLICATE = "duplicate"
    NEAR_DUPLICATE = "near_duplicate"
    ERROR = "error"
    LOGIN_WALL = "login_wall"
    UNSUPPORTED = "unsupported"


@dataclass
class Rejection:
    source_name: str
    url: str
    reason: RejectionReason
    detail: str = ""
    collected_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"source_name": self.source_name, "url": self.url,
                "reason": self.reason.value, "detail": self.detail,
                "collected_at": self.collected_at}


# ----------------------------------------------------------------------
# Crawl state (for resume)
# ----------------------------------------------------------------------

@dataclass
class CrawlState:
    source_name: str
    last_url: str = ""
    last_page: int = 0
    last_run_at: str = ""
    status: str = "idle"   # idle | running | paused | done | failed
    error: str = ""
    items_collected: int = 0
    items_rejected: int = 0


# ----------------------------------------------------------------------
# Hashing helpers
# ----------------------------------------------------------------------

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_text(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation. Used for
    normalized-hash dedup."""
    t = text.lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t
