"""Offline text extractors for user-supplied technical documents.

Supported formats:
- HTML/HTM: stdlib HTMLParser; scripts, styles, navigation and obvious
  chrome are skipped. Text is extracted only; remote assets are never fetched.
- DOCX: stdlib zipfile + ElementTree; paragraphs and table cells are preserved.
- CSV: stdlib csv; rows are rendered as labeled text.
- TXT/MD/LOG: stdlib decoding with conservative fallbacks.
- PDF: optional PyMuPDF or pypdf; no OCR. Scanned PDFs are reported as
  partial/empty rather than silently presented as successfully extracted.

This module never walks directories and never downloads URLs. Callers must
explicitly supply one local file. BIOS/firmware dumps, schematics and
BoardView files remain outside the knowledge-ingestion path.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import mimetypes
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_CHARS = 2_000_000
FORBIDDEN_BINARY_EXTENSIONS = {
    ".rom", ".bios", ".fd", ".wph", ".cap", ".bin", ".hex", ".eep",
}


class ExtractionError(RuntimeError):
    """Raised when a supplied document cannot be safely text-extracted."""


@dataclass
class ExtractionResult:
    text: str
    file_name: str
    extension: str
    mime_type: str
    sha256: str
    method: str
    status: str = "extracted"  # extracted | partial | failed
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class _TechnicalHTMLParser(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "canvas", "iframe",
             "nav", "footer", "form", "button", "head", "template", "aside"}
    _BLOCK = {"p", "div", "section", "article", "main", "br", "hr", "li", "tr",
              "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "pre",
              "blockquote", "table", "ul", "ol"}
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
             "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []
        self.title_parts: list[str] = []
        self.in_title = False
        self.meta: dict[str, str] = {}
        self.image_references: list[dict[str, str]] = []

    @property
    def hidden(self) -> bool:
        return bool(self.stack and self.stack[-1][1])

    def handle_starttag(self, tag, attrs):
        attrs_d = {str(k).lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self.in_title = True
        inherited_hidden = self.hidden
        classes = (attrs_d.get("class", "") + " " + attrs_d.get("id", "")).lower()
        own_hidden = tag in self._SKIP or any(
            token in classes for token in (
                "sidebar", "breadcrumb", "advert", "ads-", "social-share",
                "user-card", "avatar-block", "cookie-banner", "navigation"
            )
        )
        effective_hidden = inherited_hidden or own_hidden
        if tag not in self._VOID:
            self.stack.append((tag, effective_hidden))
        if effective_hidden:
            return
        if tag == "img":
            src = (attrs_d.get("src") or attrs_d.get("data-src") or attrs_d.get("data-original") or "").strip()
            if src and not src.lower().startswith("data:"):
                self.image_references.append({
                    "src": src,
                    "alt": attrs_d.get("alt", "").strip(),
                    "title": attrs_d.get("title", "").strip(),
                })
        if tag == "meta":
            key = (attrs_d.get("name") or attrs_d.get("property") or "").lower()
            value = attrs_d.get("content", "").strip()
            if key and value and key in {"author", "article:author", "og:title", "description"}:
                self.meta[key] = value
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        # Remove the matching element and any malformed descendants above it.
        match = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                match = index
                break
        was_hidden = self.hidden
        if match is not None:
            del self.stack[match:]
        if tag == "title":
            self.in_title = False
        if not was_hidden and not self.hidden and tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title and data.strip():
            self.title_parts.append(data.strip())
        if self.hidden:
            return
        if data and data.strip():
            self.parts.append(data.strip())


def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _clean_text(text: str) -> str:
    text = html.unescape(text).replace("\x00", "")
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines()]
    compact: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if compact and not blank:
                compact.append("")
            blank = True
            continue
        compact.append(line)
        blank = False
    return "\n".join(compact).strip()


def _extract_html(raw: bytes) -> tuple[str, dict, list[str]]:
    parser = _TechnicalHTMLParser()
    try:
        parser.feed(_decode_text(raw))
        parser.close()
    except Exception as exc:
        raise ExtractionError(f"HTML parsing failed: {exc}") from exc
    text = _clean_text("\n".join(parser.parts))
    meta = dict(parser.meta)
    if parser.title_parts:
        meta["title"] = " ".join(parser.title_parts)
    if parser.image_references:
        # Keep only visible image references from the saved HTML. HTTrack's
        # mirrored assets remain in place; this records their relative links.
        meta["image_references"] = parser.image_references[:500]
        if len(parser.image_references) > 500:
            meta.setdefault("warnings", []).append("Image reference list capped at 500 entries.")
    if not text:
        return "", meta, ["No visible text was extracted from this HTML file."]
    return text, meta, []


def _extract_docx(raw: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            info = zf.getinfo("word/document.xml")
            if info.file_size > MAX_EXTRACTED_CHARS * 4:
                raise ExtractionError("DOCX main document XML exceeds the decompressed-size safety limit.")
            xml_bytes = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ExtractionError(f"Invalid or unreadable DOCX file: {exc}") from exc
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ExtractionError(f"DOCX document XML is invalid: {exc}") from exc
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    blocks: list[str] = []
    body = root.find("w:body", ns)
    if body is None:
        return ""
    for child in list(body):
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            line = "".join(t.text or "" for t in child.findall(".//w:t", ns)).strip()
            if line:
                blocks.append(line)
        elif tag == "tbl":
            for row in child.findall(".//w:tr", ns):
                cells = []
                for cell in row.findall("w:tc", ns):
                    cell_text = " ".join(
                        t.text or "" for t in cell.findall(".//w:t", ns)
                    ).strip()
                    cells.append(cell_text)
                if any(cells):
                    blocks.append(" | ".join(cells))
    return _clean_text("\n".join(blocks))


def _extract_csv(raw: bytes) -> str:
    data = _decode_text(raw)
    sample = data[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(data), dialect)
    rows = list(reader)
    if not rows:
        return ""
    header = [c.strip() for c in rows[0]]
    output: list[str] = []
    if header and any(header):
        output.append("Columns: " + " | ".join(header))
        for idx, row in enumerate(rows[1:], start=1):
            if not any(str(c).strip() for c in row):
                continue
            pairs = []
            for col_idx, value in enumerate(row):
                key = header[col_idx] if col_idx < len(header) and header[col_idx] else f"column_{col_idx + 1}"
                pairs.append(f"{key}: {str(value).strip()}")
            output.append(f"Row {idx}: " + " | ".join(pairs))
    else:
        output = [" | ".join(str(c).strip() for c in row) for row in rows if any(str(c).strip() for c in row)]
    return _clean_text("\n".join(output))


def _extract_pdf(raw: bytes) -> tuple[str, list[str], dict]:
    warnings: list[str] = []
    # PyMuPDF is preferred because it preserves text order more consistently.
    try:
        import fitz  # type: ignore
        doc = fitz.open(stream=raw, filetype="pdf")
        pages = [page.get_text("text") or "" for page in doc]
        page_count = len(doc)
        doc.close()
        text = _clean_text("\n\n".join(pages))
        if not text:
            warnings.append("PDF appears scanned/image-only or contains no selectable text; OCR was not run.")
            return "", warnings, {"pages": page_count, "scanned_or_empty": True}
        if sum(len(p.strip()) for p in pages) < max(20, 5 * page_count):
            warnings.append("PDF contains very little selectable text; OCR may be needed.")
        return text, warnings, {"pages": page_count, "scanned_or_empty": False}
    except Exception as fitz_exc:
        try:
            from pypdf import PdfReader  # type: ignore
            doc = PdfReader(io.BytesIO(raw))
            pages = []
            for page in doc.pages:
                try:
                    pages.append(page.extract_text() or "")
                except Exception:
                    pages.append("")
            text = _clean_text("\n\n".join(pages))
            if not text:
                warnings.append("PDF appears scanned/image-only or contains no selectable text; OCR was not run.")
            return text, warnings, {"pages": len(pages), "scanned_or_empty": not bool(text)}
        except Exception as pypdf_exc:
            raise ExtractionError(
                "PDF extraction requires PyMuPDF or pypdf; neither extractor succeeded. "
                f"PyMuPDF: {fitz_exc}; pypdf: {pypdf_exc}"
            ) from pypdf_exc


def extract_document(path: Path, *, max_bytes: int = MAX_DOCUMENT_BYTES,
                     max_chars: int = MAX_EXTRACTED_CHARS) -> ExtractionResult:
    """Extract searchable text from one explicitly supplied local file."""
    path = Path(path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise ExtractionError(f"File does not exist or is not a regular file: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise ExtractionError(f"File is too large ({size} bytes); limit is {max_bytes} bytes.")
    ext = path.suffix.lower()
    if ext in FORBIDDEN_BINARY_EXTENSIONS:
        raise ExtractionError(f"Binary/firmware extension {ext!r} is not accepted by the knowledge importer.")
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    meta: dict = {"size_bytes": size}
    warnings: list[str] = []
    if ext in {".html", ".htm"}:
        text, html_meta, warnings = _extract_html(raw)
        meta.update(html_meta)
        method = "stdlib_html_parser"
    elif ext == ".docx":
        text = _extract_docx(raw)
        method = "stdlib_docx_xml"
    elif ext == ".csv":
        text = _extract_csv(raw)
        method = "stdlib_csv"
    elif ext == ".pdf":
        text, warnings, pdf_meta = _extract_pdf(raw)
        meta.update(pdf_meta)
        method = "pymupdf_or_pypdf"
    elif ext in {".txt", ".md", ".rst", ".log", ".text"}:
        text = _clean_text(_decode_text(raw))
        method = "stdlib_text"
    else:
        raise ExtractionError(f"Unsupported file type {ext or '(no extension)'!r}.")
    text = text[:max_chars]
    if not text:
        status = "partial" if warnings else "failed"
        warnings.append("No searchable text was extracted.")
    elif len(text) >= max_chars:
        status = "partial"
        warnings.append(f"Extracted text was truncated at {max_chars} characters.")
    else:
        status = "partial" if warnings else "extracted"
    return ExtractionResult(
        text=text, file_name=path.name, extension=ext, mime_type=mime,
        sha256=sha, method=method, status=status,
        warnings=warnings, metadata=meta,
    )
