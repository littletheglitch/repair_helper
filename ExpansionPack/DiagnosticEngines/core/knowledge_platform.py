"""Knowledge Platform — document processing and retrieval framework.

Defines the ingestion pipeline stages, the KnowledgeItem schema, and a
retrieval interface. The pack does NOT ingest copyrighted content; it
records source metadata (see source_catalog.json) and lets the host
application ingest user-provided licensed documents.

Pipeline stages (per the spec):
  1. Source acquisition
  2. Metadata extraction
  3. Text extraction
  4. Table extraction
  5. Image and figure extraction
  6. Technical classification
  7. Knowledge extraction
  8. Provenance tracking
  9. Validation
 10. Indexing
 11. Retrieval

This module provides:
  * KnowledgeItem           — schema for one extracted knowledge item
  * IngestionPipeline       — runs the stages (text + metadata only;
                              table/image extraction is stubbed and
                              flagged as 'not_implemented')
  * KnowledgeStore          — in-memory + JSON-file-backed store
  * KnowledgeRetriever      — keyword + tag retrieval

BIOS dumps, schematics, and BoardView files are explicitly excluded.
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


# Forbidden ingestion categories per the spec.
FORBIDDEN_CATEGORIES = {
    "bios_dump", "firmware_dump", "schematic", "boardview"
}


@dataclass
class KnowledgeItem:
    """One extracted knowledge item with full provenance."""
    item_id: str = field(default_factory=lambda: f"KI_{uuid.uuid4().hex[:10]}")
    title: str = ""
    source_title: str = ""
    source_author: str = ""            # author or manufacturer
    source_edition: str = ""
    source_publication_date: str = ""
    source_page_or_section: str = ""
    source_url: str = ""
    local_source_reference: str = ""  # path if locally stored
    extraction_status: str = "pending"  # pending | extracted | failed
    verification_status: str = "unverified"  # unverified | verified | partial
    category: str = ""                 # e.g. 'datasheet', 'app_note', 'textbook', 'standard'
    technical_classification: str = "" # e.g. 'buck_converter', 'mosfet_replacement'
    content_text: str = ""
    tags: list[str] = field(default_factory=list)
    extracted_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeItem":
        return cls(**d)


class IngestionPipeline:
    """Runs the ingestion pipeline stages on a source document.

    This implementation handles text + metadata extraction only. Table
    and image extraction are stubbed (status='not_implemented') because
    they require external libraries (pdfplumber, camelot, etc.) that
    would break the pure-stdlib constraint. The host can plug in its
    own extractors.
    """

    def __init__(self, store: "KnowledgeStore"):
        self.store = store

    def ingest_text(self, *,
                    source_title: str, source_author: str,
                    source_url: str, content_text: str,
                    category: str, technical_classification: str = "",
                    source_page_or_section: str = "",
                    source_edition: str = "",
                    source_publication_date: str = "",
                    local_source_reference: str = "",
                    tags: Optional[list[str]] = None,
                    verification_status: str = "unverified") -> tuple[Optional[KnowledgeItem], list[str]]:
        """Ingest a text-only document. Returns (item, stage_log).

        Forbidden categories (bios_dump, schematic, boardview, firmware_dump)
        are rejected outright.
        """
        log: list[str] = []
        # 1. Source acquisition
        log.append("stage_1_source_acquisition: OK (caller-supplied text)")
        # Category gate
        if category in FORBIDDEN_CATEGORIES:
            log.append(f"GATE: category '{category}' is FORBIDDEN. Ingestion rejected.")
            return None, log
        # 2. Metadata extraction
        log.append("stage_2_metadata_extraction: OK")
        # 3. Text extraction (already text)
        if not content_text or not content_text.strip():
            log.append("stage_3_text_extraction: FAILED (empty content)")
            return None, log
        log.append("stage_3_text_extraction: OK")
        # 4. Table extraction — stubbed
        log.append("stage_4_table_extraction: not_implemented (host must plug in)")
        # 5. Image/figure extraction — stubbed
        log.append("stage_5_image_extraction: not_implemented (host must plug in)")
        # 6. Technical classification
        log.append(f"stage_6_technical_classification: {technical_classification or 'none'}")
        # 7. Knowledge extraction — for text-only, the content_text IS the knowledge
        log.append("stage_7_knowledge_extraction: OK (text content stored verbatim)")
        # 8. Provenance tracking — recorded in the KnowledgeItem fields
        log.append("stage_8_provenance_tracking: OK")
        # 9. Validation — minimal: title + source required
        if not source_title or not source_author:
            log.append("stage_9_validation: FAILED (title and author required)")
            return None, log
        log.append("stage_9_validation: OK")
        # 10. Indexing — store
        item = KnowledgeItem(
            title=source_title, source_title=source_title,
            source_author=source_author, source_edition=source_edition,
            source_publication_date=source_publication_date,
            source_page_or_section=source_page_or_section,
            source_url=source_url,
            local_source_reference=local_source_reference,
            extraction_status="extracted",
            verification_status=verification_status,
            category=category,
            technical_classification=technical_classification,
            content_text=content_text,
            tags=tags or [],
        )
        self.store.add(item)
        log.append(f"stage_10_indexing: OK (item_id={item.item_id})")
        log.append("stage_11_retrieval: available via KnowledgeRetriever")
        return item, log


class KnowledgeStore:
    """In-memory + JSON-file-backed store of KnowledgeItems."""

    def __init__(self, pack_root: Optional[Path] = None):
        self.pack_root = pack_root or PACK_ROOT
        self.store_dir = self.pack_root / "Knowledge" / "ExtractedKnowledge"
        self.items: list[KnowledgeItem] = []
        self._load()

    def _load(self) -> None:
        if not self.store_dir.exists():
            return
        for f in sorted(self.store_dir.glob("*.json")):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self.items.append(KnowledgeItem.from_dict(data))
            except (OSError, json.JSONDecodeError, TypeError):
                continue

    def add(self, item: KnowledgeItem) -> Path:
        self.items.append(item)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        out = self.store_dir / f"{item.item_id}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(item.to_dict(), f, indent=2, ensure_ascii=False)
        return out

    def count(self) -> int:
        return len(self.items)


class RetrievalResult:
    """A retrieved knowledge item with a relevance score and provenance
    confidence. Used by the ranked retriever (P4)."""

    def __init__(self, item: KnowledgeItem, score: float,
                 matched_terms: list[str] = None):
        self.item = item
        self.score = score
        self.matched_terms = matched_terms or []

    def __repr__(self):
        return f"RetrievalResult({self.item.title!r}, score={self.score:.3f})"


# Technical-entity regexes (part numbers, topology names). Used by the
# retriever to boost items mentioning the same entity as the query.
# A part number: uppercase letters + digits + hyphens, >= 3 chars, with
# at least one digit (e.g. TPS51224, 1N5231B, BQ24780S).
_PART_RE = __import__("re").compile(r"\b[A-Z][A-Z0-9\-]*\d[A-Z0-9\-]*\b")
_TOPOLOGY_TERMS = {
    "buck", "boost", "buck-boost", "flyback", "forward", "push-pull",
    "half-bridge", "full-bridge", "llc", "pfc", "ldo", "linear",
    "charger", "atx", "mosfet", "bjt", "zener", "tvs", "op-amp", "opamp",
    "comparator", "optocoupler", "transformer", "inductor",
}

# Phase 6: synonym expansion. Maps a query term to its synonyms so a
# search for "step-down converter" matches items containing "buck".
# This is a fixed, curated map of common electronics terminology —
# NOT learned or dynamic. Adding a term here lets the retriever match
# items that use the synonym without requiring both terms to appear.
_SYNONYMS: dict[str, set[str]] = {
    "buck": {"step-down", "stepdown", "buck-converter", "step-down-converter"},
    "boost": {"step-up", "stepup", "boost-converter", "step-up-converter"},
    "flyback": {"flyback-converter", "ringing-choke"},
    "forward": {"forward-converter"},
    "mosfet": {"fet", "field-effect-transistor", "power-fet"},
    "bjt": {"transistor", "bipolar-transistor"},
    "zener": {"zener-diode", "breakdown-diode"},
    "tvs": {"transient-voltage-suppressor", "suppressor-diode"},
    "op-amp": {"opamp", "operational-amplifier", "op-amplifier"},
    "opamp": {"op-amp", "operational-amplifier"},
    "ldo": {"low-dropout", "low-dropout-regulator", "linear-regulator"},
    "linear": {"linear-regulator", "ldo"},
    "llc": {"llc-resonant", "resonant-converter"},
    "pfc": {"power-factor-correction", "power-factor-corrector"},
    "charger": {"battery-charger", "charge-controller"},
    "atx": {"atx-psu", "atx-power-supply"},
    "oscilloscope": {"scope", "o-scope"},
    "dmm": {"multimeter", "digital-multimeter"},
    "short": {"short-circuit", "low-resistance"},
    "ripple": {"ac-ripple", "noise", "output-ripple"},
    "feedback": {"fb", "feedback-loop", "compensation"},
}


def _expand_synonyms(tokens: set[str]) -> set[str]:
    """Expand a token set with synonyms (BIDIRECTIONAL). Returns the
    union of the original tokens and their synonyms. Bidirectional:
    if 'boost' maps to 'step-up', then a query for 'step-up' also
    expands to 'boost' (and vice versa)."""
    expanded = set(tokens)
    for tok in list(tokens):
        if tok in _SYNONYMS:
            expanded |= _SYNONYMS[tok]
    # Reverse: if a token IS a synonym value, add the key.
    for key, syns in _SYNONYMS.items():
        if tokens & syns:
            expanded.add(key)
    return expanded


def _tokenize(text: str) -> list[str]:
    """Tokenize: lowercase, split on non-alphanumeric (keep hyphens
    inside tokens like 'buck-boost'). Returns a list of tokens."""
    text = text.lower()
    # Replace non-alphanumeric (except hyphen) with space
    cleaned = __import__("re").sub(r"[^a-z0-9\-]+", " ", text)
    return [t for t in cleaned.split() if t]


def _entities(text: str) -> set[str]:
    """Extract technical entities: part numbers (uppercase) and known
    topology terms. Returns a set of lowercased entity strings."""
    ents = set()
    for m in _PART_RE.findall(text):
        ents.add(m.lower())
    for tok in _tokenize(text):
        if tok in _TOPOLOGY_TERMS:
            ents.add(tok)
    return ents


class KnowledgeRetriever:
    """Tokenized, ranked, provenance-aware retrieval over a KnowledgeStore.

    P4 improvements over the old substring search:
      - Tokenization: the query is split into terms; an item matches if
        it shares tokens (partial match), not only on full-substring.
      - Normalization: lowercase + punctuation stripping.
      - Relevance ranking: items matching more query tokens score
        higher. Exact-entity matches (part numbers, topologies) boost.
      - Provenance separation: each result carries the item's
        verification_status and source_author so the caller can filter
        manufacturer facts from community claims.
      - Confidence scoring: verified manufacturer facts score higher
        than unverified community claims, all else equal.

    The retriever is pure-stdlib and deterministic. No network access.
    """

    def __init__(self, store: KnowledgeStore):
        self.store = store

    def search(self, query: str, tags: Optional[list[str]] = None,
               category: Optional[str] = None,
               verified_only: bool = False) -> list[RetrievalResult]:
        """Ranked retrieval. Returns RetrievalResult list sorted by
        descending score. Empty list if no matches.

        verified_only: if True, return only items with
        verification_status == 'verified' (manufacturer facts).

        Phase 6: synonym expansion (a query for 'step-down' matches
        items containing 'buck') and dedup (items with identical
        title+content_text are collapsed, keeping the highest-scored
        / most-verified copy).
        """
        # Phase 6: expand query tokens with synonyms.
        q_tokens = _expand_synonyms(set(_tokenize(query)))
        q_entities = _entities(query)
        results: list[RetrievalResult] = []
        for item in self.store.items:
            if category and item.category != category:
                continue
            if tags and not all(t in item.tags for t in tags):
                continue
            if verified_only and item.verification_status != "verified":
                continue
            # Build the item's text + token + entity sets.
            item_text = " ".join([item.title, item.content_text,
                                  " ".join(item.tags)])
            item_tokens = set(_tokenize(item_text))
            item_entities = _entities(item_text)
            # Token overlap (the core ranked-retrieval signal).
            matched = q_tokens & item_tokens
            if not matched and not (q_entities & item_entities):
                continue  # no overlap at all
            # Score: fraction of query tokens matched, plus entity boost.
            token_score = len(matched) / max(len(q_tokens), 1)
            entity_overlap = q_entities & item_entities
            entity_boost = 0.3 * len(entity_overlap) / max(len(q_entities or {""}), 1)
            # Provenance confidence: verified manufacturer facts score
            # higher than unverified community claims.
            provenance_mult = 1.0
            if item.verification_status == "verified":
                provenance_mult = 1.25
            elif item.verification_status == "unverified":
                provenance_mult = 0.80
            elif item.verification_status == "partial":
                provenance_mult = 1.0
            # Datasheet/app-note (manufacturer) categories get a small
            # boost over forum/community categories.
            if item.category in ("datasheet", "app_note", "textbook", "standard"):
                provenance_mult *= 1.05
            elif item.category in ("forum_post", "blog", "community"):
                provenance_mult *= 0.95
            score = (token_score + entity_boost) * provenance_mult
            results.append(RetrievalResult(
                item=item, score=score,
                matched_terms=sorted(matched | entity_overlap)))
        # Phase 6: dedup. Collapse items with identical title+content_text
        # (case-insensitive), keeping the highest-scored copy. This
        # prevents duplicate KI records from dominating results.
        seen: dict[tuple[str, str], RetrievalResult] = {}
        for r in results:
            key = (r.item.title.strip().lower(),
                   r.item.content_text.strip().lower()[:200])
            if key not in seen or r.score > seen[key].score:
                seen[key] = r
        results = list(seen.values())
        # Sort by descending score; tie-break by title for determinism.
        results.sort(key=lambda r: (-r.score, r.item.title))
        return results

    # Convenience: return raw items (backward-compat with old callers
    # that expected list[KnowledgeItem]).
    def search_items(self, query: str, tags: Optional[list[str]] = None,
                     category: Optional[str] = None) -> list[KnowledgeItem]:
        return [r.item for r in self.search(query, tags, category)]


# =========================================================================
# Phase 6: entity-relationship knowledge graph
# =========================================================================

@dataclass
class KnowledgeLink:
    """A directed relationship between two knowledge entities.

    Entities are typed (component, topology, signal, rail,
    failure_pattern, procedure). A link says 'subject relates to object
    via relation'. This lets the retriever walk from, e.g., a component
    to its common failure patterns and the diagnostic procedure for
    those failures.
    """
    subject_type: str   # component | topology | signal | rail | failure_pattern | procedure
    subject_id: str     # e.g. 'UC3842', 'flyback', 'no_output', '+3VALW'
    relation: str       # e.g. 'used_in', 'causes', 'diagnosed_by', 'feeds', 'enables'
    object_type: str
    object_id: str
    note: str = ""


class KnowledgeGraph:
    """A lightweight entity-relationship store linking components,
    topologies, signals, rails, failure patterns, and diagnostic
    procedures. This is NOT a full ontology — it's a curated set of
    links the retriever can walk to find related knowledge.

    Example: a search for 'flyback no_output' can walk
    flyback --has_failure--> no_output --diagnosed_by--> check_feedback
    to surface the diagnostic procedure, even if the procedure's text
    doesn't contain 'flyback'.
    """

    def __init__(self) -> None:
        self._links: list[KnowledgeLink] = []
        # Index: (subject_type, subject_id) -> list[KnowledgeLink]
        self._by_subject: dict[tuple[str, str], list[KnowledgeLink]] = {}

    def add(self, link: KnowledgeLink) -> None:
        self._links.append(link)
        key = (link.subject_type, link.subject_id)
        self._by_subject.setdefault(key, []).append(link)

    def neighbors(self, entity_type: str, entity_id: str,
                  relation: Optional[str] = None) -> list[KnowledgeLink]:
        """Return links FROM this entity, optionally filtered by relation."""
        out = self._by_subject.get((entity_type, entity_id), [])
        if relation:
            return [l for l in out if l.relation == relation]
        return list(out)

    def related(self, entity_type: str, entity_id: str) -> list[KnowledgeLink]:
        """Return ALL links involving this entity (as subject OR object)."""
        out = list(self._by_subject.get((entity_type, entity_id), []))
        for link in self._links:
            if link.object_type == entity_type and link.object_id == entity_id:
                out.append(link)
        return out

    def __len__(self) -> int:
        return len(self._links)


# Phase 6: a curated seed of entity relationships covering common
# components, topologies, signals, rails, failure patterns, and
# procedures. These are well-established electronics relationships —
# NOT invented facts.
def build_seed_knowledge_graph() -> KnowledgeGraph:
    """Build a seed KnowledgeGraph with curated relationships."""
    g = KnowledgeGraph()
    # Components <-> topologies
    for pn, topo in [("UC3842", "flyback"), ("UC3843", "flyback"),
                     ("UC3844", "push-pull"), ("UC3845", "push-pull"),
                     ("SG3525", "push-pull"), ("TL494", "half-bridge"),
                     ("OB2269", "flyback")]:
        g.add(KnowledgeLink("component", pn, "used_in", "topology", topo))
    # Topologies <-> common failure patterns
    for topo, fp in [("flyback", "no_output"), ("flyback", "overheating"),
                     ("buck", "no_output"), ("buck", "wrong_voltage"),
                     ("llc", "no_output"), ("atx", "no_output"),
                     ("boost", "overvoltage")]:
        g.add(KnowledgeLink("topology", topo, "has_failure",
                            "failure_pattern", fp))
    # Failure patterns <-> diagnostic procedures
    g.add(KnowledgeLink("failure_pattern", "no_output", "diagnosed_by",
                        "procedure", "check_input_then_output",
                        "Measure input first, then output; localize the dead stage."))
    g.add(KnowledgeLink("failure_pattern", "overheating", "diagnosed_by",
                        "procedure", "thermal_scan",
                        "Scan with a thermal camera; a hot component points to the fault."))
    g.add(KnowledgeLink("failure_pattern", "wrong_voltage", "diagnosed_by",
                        "procedure", "check_feedback",
                        "Measure the feedback divider and the reference voltage."))
    # Rails <-> topologies (which topology produces which rail)
    g.add(KnowledgeLink("rail", "+3VALW", "produced_by", "topology", "buck"))
    g.add(KnowledgeLink("rail", "+5VALW", "produced_by", "topology", "buck"))
    g.add(KnowledgeLink("rail", "VCORE", "produced_by", "topology", "buck"))
    # Signals <-> rails (which signals gate which rails)
    g.add(KnowledgeLink("signal", "EN", "enables", "rail", "VCORE"))
    g.add(KnowledgeLink("signal", "PS_ON", "enables", "rail", "+12V"))
    return g

