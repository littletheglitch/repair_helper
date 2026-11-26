"""Document processing: PDF text, OCR, HTML, metadata, hashing, corrupt.

This module takes a downloaded file (already on disk) and:

1. Runs the BIOS guard — BIOS dumps are rejected and never processed.
2. Detects the file type by extension + magic bytes.
3. Extracts text:
   - PDF: PyMuPDF (fitz) with pypdf fallback. Scanned-PDF detection
     (text/page < 20 chars) sets meta.ocr_recommended.
   - HTML: stdlib html.parser; preserves <title>, <meta>.
   - Text: encoding sniff (utf-8, latin-1, cp1252, ascii).
   - Image: stored_only with meta.ocr_recommended; OCR runs only when
     explicitly enabled.
4. Computes SHA-256 of the file and of the extracted text.
5. Metadata: title, author (if present), published date (if present),
   page count (PDF), language (heuristic), source URL.
6. Stores the original in `data/originals/`, extracted text in
   `data/extracted/`.
7. Handles corrupt files: a corrupt PDF is `failed` with the error; the
   original is preserved; the engine never crashes.

The module NEVER claims a scanned schematic is electrically understood
just because OCR extracted its labels. OCR output is text; the
diagnostic assistant's datasheet engine is the one that reasons about
parameter rows, and it explicitly does NOT parse schematic symbols.
"""
from __future__ import annotations

import hashlib
import html
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import config, bios_guard
from .schemas import Document


# File-type detection by extension + magic bytes.
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".json", ".log", ".asc"}
PDF_MAGIC = (b"%PDF",)
HTML_MAGIC = (b"<!DOCTYPE", b"<html", b"<HTML")
TEXT_MAGIC = (b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")  # BOMs


def detect_file_type(path: Path) -> str:
    """Return one of: pdf | html | text | image | unknown."""
    ext = path.suffix.lower()
    head = b""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        pass
    if ext == ".pdf" or head.startswith(PDF_MAGIC):
        return "pdf"
    if ext in (".htm", ".html") or any(head.startswith(m) for m in HTML_MAGIC):
        return "html"
    if ext in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif"):
        return "image"
    if ext in TEXT_EXTENSIONS:
        return "text"
    return "unknown"


def process_file(path: Path, source_name: str, url: str,
                 enable_ocr: bool = False) -> Document:
    """Process a file on disk. Returns a Document dataclass.

    The original is copied to ``data/originals/<sha[:12]>_<name>`` and
    the extracted text to ``data/extracted/<sha[:12]>.txt``. A BIOS
    dump is rejected (status='rejected', file_type='rejected'); the
    original is NOT stored.
    """
    # BIOS guard (hard exclusion).
    try:
        bios_guard.assert_not_bios(path)
    except ValueError as e:
        return Document(
            source_name=source_name, url=url, filename=path.name,
            file_type="rejected", sha256="", size_bytes=path.stat().st_size,
            status="rejected", meta={"reason": str(e)},
            collected_at=config.now_iso(),
        )

    sha = _sha256_file(path)
    size = path.stat().st_size
    ftype = detect_file_type(path)
    meta: Dict[str, Any] = {"file_type": ftype, "source_url": url}
    text = ""
    status = "stored_only"

    if ftype == "pdf":
        text, status, pdf_meta = _extract_pdf(path)
        meta.update(pdf_meta)
    elif ftype == "html":
        text, status, html_meta = _extract_html(path)
        meta.update(html_meta)
    elif ftype == "text":
        text, status = _extract_text(path)
    elif ftype == "image":
        meta["ocr_recommended"] = True
        if enable_ocr:
            text, status, ocr_meta = _ocr_image(path)
            meta.update(ocr_meta)
    else:
        # Try text extraction as a last resort.
        text, status = _extract_text(path)

    text_sha = _sha256_bytes(text.encode("utf-8")) if text else ""
    meta["text_sha256"] = text_sha

    # Store the original.
    orig_name = f"{sha[:12]}_{_sanitize(path.name)}"
    orig_path_rel = f"originals/{orig_name}"
    orig_full = config.originals_dir() / orig_name
    try:
        shutil.copy2(path, orig_full)
    except OSError as e:
        meta["copy_error"] = str(e)

    # Store the extracted text.
    ext_path_rel = ""
    if text:
        ext_name = f"{sha[:12]}.txt"
        ext_path_rel = f"extracted/{ext_name}"
        ext_full = config.extracted_dir() / ext_name
        try:
            ext_full.write_text(text, encoding="utf-8")
        except OSError as e:
            meta["text_write_error"] = str(e)

    return Document(
        source_name=source_name, url=url, filename=path.name,
        file_type=ftype, sha256=sha, size_bytes=size, status=status,
        meta=meta, collected_at=config.now_iso(),
        original_path=orig_path_rel, extracted_text_path=ext_path_rel,
    )


# ---- PDF -------------------------------------------------------------

def _extract_pdf(path: Path) -> Tuple[str, str, Dict[str, Any]]:
    """Returns (text, status, meta). status is one of:
    extracted | partially_extracted | stored_only | failed."""
    meta: Dict[str, Any] = {}
    text = ""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        meta["pages"] = doc.page_count
        pages_text = []
        for i, page in enumerate(doc):
            t = page.get_text() or ""
            pages_text.append(f"\x0cPAGE:{i+1}\x0c" + t)
        doc.close()
        text = "".join(pages_text)
        # Scanned detection: average text per page < 20 chars.
        pages = meta.get("pages", 1) or 1
        if len(text) / max(pages, 1) < 20 or len(text) < max(5 * pages, 20):
            meta["scanned"] = True
            meta["ocr_recommended"] = True
            status = "partially_extracted"
        else:
            status = "extracted"
        if not text.strip():
            status = "stored_only"
            meta["ocr_recommended"] = True
        meta["extractor"] = "pymupdf"
        return text, status, meta
    except ImportError:
        pass  # fall through to pypdf
    except Exception as e:
        meta["pymupdf_error"] = str(e)
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        meta["pages"] = len(reader.pages)
        pages_text = []
        for i, page in enumerate(reader.pages):
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            pages_text.append(f"\x0cPAGE:{i+1}\x0c" + t)
        text = "".join(pages_text)
        meta["extractor"] = "pypdf"
        if not text.strip():
            status = "stored_only"
            meta["ocr_recommended"] = True
        else:
            status = "extracted"
        return text, status, meta
    except Exception as e:
        meta["error"] = str(e)
        return "", "failed", meta


# ---- HTML ------------------------------------------------------------

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list = []
        self.title = ""
        self.in_title = False
        self.meta: Dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            d = dict(attrs)
            name = d.get("name", "").lower()
            content = d.get("content", "")
            if name and content:
                self.meta[name] = content

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        self.parts.append(data)


def _extract_html(path: Path) -> Tuple[str, str, Dict[str, Any]]:
    raw = b""
    try:
        raw = path.read_bytes()
    except OSError:
        return "", "failed", {"error": "unreadable"}
    # Try utf-8 then latin-1.
    text = ""
    for enc in ("utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    parser = _TextExtractor()
    try:
        parser.feed(text)
    except Exception:
        pass
    body = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    title = parser.title.strip()
    meta: Dict[str, Any] = {
        "title": title,
        "html_meta": parser.meta,
        "extractor": "html.parser",
    }
    if parser.meta.get("author"):
        meta["author"] = parser.meta["author"]
    if parser.meta.get("date") or parser.meta.get("publication_date"):
        meta["published_date"] = parser.meta.get("date") or parser.meta.get("publication_date")
    status = "extracted" if body else "stored_only"
    return body, status, meta


# ---- Text ------------------------------------------------------------

def _extract_text(path: Path) -> Tuple[str, str]:
    for enc in ("utf-8", "latin-1", "cp1252", "ascii"):
        try:
            text = path.read_text(encoding=enc)
            return text, "extracted" if text.strip() else "stored_only"
        except UnicodeDecodeError:
            continue
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text, "extracted" if text.strip() else "stored_only"
    except OSError:
        return "", "stored_only"


# ---- OCR (lazy) -----------------------------------------------------

def _ocr_image(path: Path) -> Tuple[str, str, Dict[str, Any]]:
    """OCR an image via Tesseract. Returns (text, status, meta)."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        return "", "stored_only", {"ocr_error": f"tesseract unavailable: {e}"}
    try:
        img = Image.open(str(path))
        text = pytesseract.image_to_string(img)
        meta = {"ocr_engine": "tesseract", "language": "eng"}
        return text, "extracted" if text.strip() else "stored_only", meta
    except Exception as e:
        return "", "stored_only", {"ocr_error": str(e)}


# ---- Hashing + sanitization -----------------------------------------

def _sha256_file(path: Path, chunk: int = 64 * 1024) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(chunk)
                if not b:
                    break
                h.update(b)
    except OSError:
        pass
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)
