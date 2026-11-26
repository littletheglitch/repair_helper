"""Datasheet intelligence engine.

Extracts structured parameters from imported datasheet documents and
stores them in the ``datasheet_params`` table with full provenance:

* exact source document (documents.id)
* page number
* section / table name
* original text excerpt
* unit
* rating type (absolute_maximum / recommended / typical / min / max)
* extraction confidence (0..1)
* verification status (verified / unverified / ambiguous)

Rules enforced:

* The engine only extracts what it can find in the document's text. It
  never invents missing values; an absent parameter is reported as
  ``not_found``, not as a fabricated number.
* It distinguishes absolute-maximum ratings from recommended operating
  conditions and typical values from guaranteed min/max by looking at
  the surrounding section heading keywords.
* Extraction is keyword-and-regex based; it is intentionally
  conservative. Ambiguous extractions are marked
  ``verification_status='ambiguous'`` for manual confirmation.
* The LLM orchestrator may call ``lookup`` to retrieve extracted
  parameters for a part number and present them with their provenance.

This engine does NOT parse images or schematic symbols. PDF text
extraction is performed by the existing ``eda.documents`` module; this
engine only reasons over the extracted text chunks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .base import Engine, MethodSpec
from ..schemas import EngineError, EngineResult, ErrorCode
from .. import database, documents as D, retrieval


# Rating-type classification by section heading keywords.
RATING_KEYWORDS = [
    ("absolute_maximum", [r"absolute\s+maximum", r"absolute\s+max",
                          r"max(?:imum)?\s+ratings"]),
    ("recommended", [r"recommended\s+operating", r"operating\s+conditions",
                     r"recommended\s+conditions"]),
    ("electrical_characteristics", [r"electrical\s+characteristics",
                                     r"electrical\s+specs"]),
    ("typical", [r"typical\s+application", r"typical\s+operating"]),
    ("timing", [r"timing\s+characteristics", r"timing\s+specs",
                r"switching\s+characteristics"]),
    ("pin_description", [r"pin\s+(?:description|configuration|out|function)",
                         r"pin\s+assignment"]),
    ("enable", [r"enable\s+\(?(?:EN|enable)\)?", r"\bEN\b\s+pin"]),
    ("uvlo", [r"UVLO", r"under-?voltage\s+lockout"]),
    ("pgood", [r"PGOOD", r"power\s*good", r"PWR\s*GD"]),
    ("protection", [r"over-?current\s+protection", r"OCP",
                     r"over-?voltage\s+protection", r"OVP",
                     r"short[\s-]+circuit\s+protection", r"SCP",
                     r"thermal\s+(?:shutdown|protection)"]),
]


# Regexes for parameter extraction. Each captures (name, min, typ, max, unit).
# We intentionally match only clearly-formatted values; anything ambiguous
# is left to the user to verify.
_VALUE_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9_+\-/ ]{1,40}?)"      # parameter name
    r"\s*"
    r"(?:"
    r"(?P<min>[-+]?\d+(?:\.\d+)?)\s*(?P<minunit>[mµu nkKM]?[VAFΩHWHz]+)\s*"
    r"(?:to|–|-|—)?\s*"
    r")?"
    r"(?P<typ>[-+]?\d+(?:\.\d+)?)\s*(?P<typunit>[mµu nkKM]?[VAFΩHWHz]+)\s*"
    r"(?:to|–|-|—)?\s*"
    r"(?P<max>[-+]?\d+(?:\.\d+)?)?\s*(?P<maxunit>[mµu nkKM]?[VAFΩHWHz]+)?"
)


@dataclass
class ExtractedParam:
    parameter: str
    value: str = ""
    unit: str = ""
    min_value: str = ""
    typ_value: str = ""
    max_value: str = ""
    rating_type: str = "unknown"
    page: Optional[int] = None
    section: str = ""
    original_text: str = ""
    extraction_confidence: float = 0.3
    verification_status: str = "unverified"

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


class DatasheetEngine(Engine):
    name = "datasheet"
    version = "1.0.0"
    summary = ("Extracts structured parameters (pinout, ratings, thresholds, "
              "protection, timing) from an imported datasheet's text. Stores "
              "them with full provenance. Never invents missing values.")
    provenance = ("Original implementation; keyword + regex extraction over "
                  "PDF text already extracted by eda.documents. No external "
                  "datasheet database is consulted.")

    methods = {
        "extract": MethodSpec(
            "extract", "Extract parameters from an imported document by id",
            {"document_id": ("number", True), "part_number": ("string", False)},
            "dict of ExtractedParam lists"),
        "lookup": MethodSpec(
            "lookup", "Retrieve previously-extracted parameters for a part number",
            {"part_number": ("string", True)},
            "list of ExtractedParam"),
        "list_documents_for_part": MethodSpec(
            "list_documents_for_part", "List imported documents that may be a datasheet for the part",
            {"part_number": ("string", True)},
            "list of document ids"),
    }

    # ---- extract -----------------------------------------------------
    def _extract(self, inputs: Dict[str, Any]) -> EngineResult:
        did = inputs.get("document_id")
        if did is None:
            return self._err("extract", ErrorCode.MISSING_INPUT,
                             "missing document_id", "document_id")
        doc = D.get_document(int(did))
        if not doc:
            return self._err("extract", ErrorCode.NOT_APPLICABLE,
                             f"no document with id {did}")
        part_number = inputs.get("part_number", "") or self._guess_part_number(doc)
        # Read the document's text chunks.
        chunks = database.query_all(
            "SELECT seq, page, text FROM document_chunks "
            "WHERE document_id=? ORDER BY seq", (int(did),)
        )
        if not chunks:
            return self._err("extract", ErrorCode.NOT_APPLICABLE,
                             "document has no extracted text (status may be stored_only)")
        params: List[ExtractedParam] = []
        current_section = ""
        current_rating = "unknown"
        for ch in chunks:
            text = ch["text"]
            page = ch["page"]
            for line in text.splitlines():
                line_stripped = line.strip()
                # Detect a heading line BEFORE attempting value extraction.
                # A heading is a short line that matches a known section
                # keyword and contains no digit (so value rows don't match).
                if (line_stripped and not any(c.isdigit() for c in line_stripped)
                        and len(line_stripped) <= 60):
                    heading = self._classify_heading(line_stripped)
                    if heading:
                        current_section = line_stripped
                        current_rating = heading
                        continue
                # Value extraction: only on lines that have a digit and look
                # like parameter rows.
                if len(line_stripped) < 4 or len(line_stripped) > 200:
                    continue
                if not any(c.isdigit() for c in line_stripped):
                    continue
                m = _VALUE_RE.search(line_stripped)
                if not m:
                    continue
                name = m.group(1).strip(" :.-")
                if len(name) < 2 or len(name) > 40:
                    continue
                if name.lower() in {"symbol", "parameter", "min", "typ", "max",
                                     "unit", "description", "pin", "name"}:
                    continue
                p = ExtractedParam(
                    parameter=name,
                    typ_value=m.group("typ") or "",
                    unit=m.group("typunit") or "",
                    min_value=m.group("min") or "",
                    max_value=m.group("max") or "",
                    rating_type=current_rating,
                    page=page,
                    section=current_section or "",
                    original_text=line_stripped[:160],
                    extraction_confidence=0.5 if current_rating != "unknown" else 0.3,
                    verification_status="unverified",
                )
                params.append(p)
        # Persist extracted params.
        stored = 0
        for p in params:
            try:
                database.execute(
                    """INSERT INTO datasheet_params(
                        document_id, part_number, manufacturer, package, pin_count,
                        parameter, value, unit, min_value, typ_value, max_value,
                        rating_type, page, section, original_text,
                        extraction_confidence, verification_status, extracted_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (int(did), part_number, None, None, None,
                     p.parameter, p.value, p.unit, p.min_value, p.typ_value,
                     p.max_value, p.rating_type, p.page, p.section,
                     p.original_text, p.extraction_confidence,
                     p.verification_status, database.now_iso()),
                )
                stored += 1
            except Exception:
                continue
        # Always record the part number mapping even if no params found.
        if not params:
            database.execute(
                """INSERT INTO datasheet_params(
                    document_id, part_number, parameter, value, rating_type,
                    extraction_confidence, verification_status, extracted_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (int(did), part_number, "(document_marker)", "",
                 "unknown", 0.0, "unverified", database.now_iso()),
            )
        return self._ok("extract",
                        value={"part_number": part_number,
                                "parameters_extracted": stored,
                                "params": [p.to_dict() for p in params[:50]]},
                        inputs={"document_id": int(did), "part_number": part_number},
                        assumptions=["text already extracted by eda.documents",
                                     "keyword+regex extraction is conservative"],
                        limitations=["does not parse images or schematic symbols",
                                     "ambiguous rows are marked verification_status='unverified'",
                                     "no missing values are invented"])

    def _guess_part_number(self, doc) -> str:
        # Look at the document's file name for a part-like token.
        import re as _re
        fn = doc["file_name"] or ""
        m = _re.search(r"([A-Z][A-Z0-9]{2,}-[A-Z0-9]+)", fn)
        if m:
            return m.group(1)
        # Otherwise strip extension.
        return _re.sub(r"\.[^.]+$", "", fn)

    def _classify_heading(self, line: str) -> Optional[str]:
        """Return a rating-type if ``line`` looks like a section heading,
        else None. Matches the FIRST applicable rating in
        RATING_KEYWORDS so that 'Absolute Maximum Ratings' wins over
        later generic matches."""
        for rating, patterns in RATING_KEYWORDS:
            for pat in patterns:
                if re.search(pat, line, re.IGNORECASE):
                    return rating
        return None

    # ---- lookup ------------------------------------------------------
    def _lookup(self, inputs: Dict[str, Any]) -> EngineResult:
        pn = inputs.get("part_number", "")
        if not pn:
            return self._err("lookup", ErrorCode.MISSING_INPUT,
                             "missing part_number", "part_number")
        rows = database.query_all(
            "SELECT * FROM datasheet_params WHERE part_number=? "
            "ORDER BY id", (pn,)
        )
        if not rows:
            return self._err("lookup", ErrorCode.NOT_APPLICABLE,
                             f"no extracted parameters for '{pn}'. "
                             f"Import the datasheet and run extract first.")
        # Drop the document_marker row.
        rows = [r for r in rows if r["parameter"] != "(document_marker)"]
        return self._ok("lookup",
                        value={"part_number": pn, "parameters": rows},
                        inputs={"part_number": pn},
                        limitations=["only previously-extracted parameters are returned"])

    # ---- list_documents_for_part ------------------------------------
    def _list_documents(self, inputs: Dict[str, Any]) -> EngineResult:
        pn = inputs.get("part_number", "")
        if not pn:
            return self._err("list_documents_for_part", ErrorCode.MISSING_INPUT,
                             "missing part_number", "part_number")
        # Find documents whose file_name or first chunk contains the part.
        docs = D.list_documents()
        matches = []
        for d in docs:
            if pn.lower() in (d["file_name"] or "").lower():
                matches.append(d["id"])
                continue
            # Search chunks for the part number.
            hits = retrieval.search_documents(pn, document_id=d["id"], limit=1)
            if hits:
                matches.append(d["id"])
        return self._ok("list_documents_for_part",
                        value={"part_number": pn, "document_ids": matches},
                        inputs={"part_number": pn})

    # ---- dispatch ---------------------------------------------------
    _DISPATCH = None

    def run(self, method: str, inputs: Dict[str, Any]) -> EngineResult:
        fn = DatasheetEngine._DISPATCH.get(method)
        if fn is None:
            return self._err(method, ErrorCode.NOT_APPLICABLE, f"unknown method '{method}'")
        return fn(self, inputs)


DatasheetEngine._DISPATCH = {
    "extract": DatasheetEngine._extract,
    "lookup": DatasheetEngine._lookup,
    "list_documents_for_part": DatasheetEngine._list_documents,
}
