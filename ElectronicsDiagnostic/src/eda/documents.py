"""Document processor.

Format-aware import for PDFs, plain text, datasheets, schematics,
images, BoardView and BIOS/firmware files. Every import records a
SHA-256, size, type and a status (received / extracted / partially
extracted / stored only / failed). Text is chunked and indexed in
SQLite FTS5 so retrieval is on-demand and the full archive is never
held in RAM.

The processor NEVER scans the user's drives. It only opens files the
user explicitly drags in or pastes a path to.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config
from . import database


# ---- Status constants ---------------------------------------------------

RECEIVED = "received"
EXTRACTED = "extracted"
PARTIALLY = "partially_extracted"
STORED = "stored_only"
FAILED = "failed"


# ---- File-type detection ------------------------------------------------

_TEXT_EXTS = {".txt", ".md", ".csv", ".tsv", ".json", ".log", ".asc"}
_PDF_EXTS = {".pdf"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif"}
_BOARDVIEW_EXTS = {".fz", ".bvr", ".bv", ".asc", ".brd2", ".brd"}
_BIOS_EXTS = {".bin", ".rom", ".fd", ".ima", ".fw", ".spi", ".hex"}
# Some extensions are ambiguous; .asc can be text or boardview.


def detect_file_type(path: Path) -> str:
    """Return one of: pdf, text, image, boardview, bios, unknown."""
    ext = path.suffix.lower()
    if ext in _PDF_EXTS:
        return "pdf"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _BIOS_EXTS:
        return "bios"
    if ext in _BOARDVIEW_EXTS:
        return "boardview"
    if ext in _TEXT_EXTS:
        return "text"
    return "unknown"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- Chunking -----------------------------------------------------------

def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 100) -> List[Tuple[str, Optional[int]]]:
    """Split text into overlapping chunks. Each chunk = (text, page_or_None).

    For non-paginated text, page is None. Page markers in the extracted
    text are expected as the literal token ``\\x0cPAGE:n\\x0c``.
    """
    if not text:
        return []
    # Honor explicit page markers if present.
    parts = []
    cur_page: Optional[int] = 1
    buf = []
    marker = re.compile(r"\x0cPAGE:(\d+)\x0c")
    i = 0
    while i < len(text):
        m = marker.match(text, i)
        if m:
            parts.append(("".join(buf), cur_page))
            cur_page = int(m.group(1))
            buf = []
            i = m.end()
            continue
        buf.append(text[i])
        i += 1
    if buf:
        parts.append(("".join(buf), cur_page))

    if len(parts) == 1 and parts[0][1] is None:
        # No page markers; do plain sliding window.
        body = parts[0][0]
        chunks = []
        start = 0
        while start < len(body):
            end = min(start + chunk_size, len(body))
            chunks.append((body[start:end], None))
            if end == len(body):
                break
            start = end - overlap
        return chunks
    # Otherwise chunk each page separately.
    out = []
    for body, page in parts:
        if not body:
            continue
        start = 0
        while start < len(body):
            end = min(start + chunk_size, len(body))
            out.append((body[start:end], page))
            if end == len(body):
                break
            start = end - overlap
    return out


# ---- Extractors ---------------------------------------------------------

def _extract_pdf(path: Path) -> Tuple[str, bool, List[Tuple[int, str]]]:
    """Return (concatenated_text_with_page_markers, is_scanned, [(page, text)]).

    Prefers PyMuPDF; falls back to pypdf; both are pure-text extractors.
    Detects scanned PDFs by checking the text-per-page ratio.
    """
    pages: List[Tuple[int, str]] = []
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        scanned = True
        total_chars = 0
        for i, page in enumerate(doc, start=1):
            t = page.get_text("text") or ""
            pages.append((i, t))
            total_chars += len(t.strip())
            if len(t.strip()) > 20:
                scanned = False
        doc.close()
        if not pages:
            return "", True, []
        text = "\n".join(f"\x0cPAGE:{p}\x0c{t}" for p, t in pages)
        # If almost no text anywhere, treat as scanned even if a couple pages had scraps.
        if total_chars < max(5 * len(pages), 20):
            scanned = True
        return text, scanned, pages
    except Exception:
        pass

    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        scanned = True
        total_chars = 0
        for i, page in enumerate(reader.pages, start=1):
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            pages.append((i, t))
            total_chars += len(t.strip())
            if len(t.strip()) > 20:
                scanned = False
        if not pages:
            return "", True, []
        text = "\n".join(f"\x0cPAGE:{p}\x0c{t}" for p, t in pages)
        if total_chars < max(5 * len(pages), 20):
            scanned = True
        return text, scanned, pages
    except Exception as e:
        raise RuntimeError(f"PDF extraction failed: {e}")


def _extract_text(path: Path) -> str:
    encodings = ["utf-8", "latin-1", "cp1252", "ascii"]
    for enc in encodings:
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    # Last resort: bytes with errors replaced.
    return path.read_bytes().decode("utf-8", errors="replace")


def _extract_boardview(path: Path) -> Tuple[str, str]:
    """Return (text_summary, status). Only well-known ASCII formats are
    attempted; proprietary binary formats are 'stored_only'.
    """
    ext = path.suffix.lower()
    # .asc (ASCII), .brd2 (JSON-ish): attempt text extraction.
    if ext in {".asc", ".brd2", ".bvr"}:
        try:
            txt = _extract_text(path)
            return txt[:20000], EXTRACTED if len(txt) > 100 else PARTIALLY
        except Exception:
            return "", STORED
    # Binary formats we do not parse.
    return (
        f"BoardView file '{path.name}' stored. Binary format '{ext}' is not "
        f"parsed by this build; ask the user for a screenshot or an ASCII "
        f"export if component/net data is needed.",
        STORED,
    )


def _describe_bios(path: Path, sha: str, size: int) -> Tuple[str, str]:
    """BIOS/firmware: store and hash, do NOT claim semantic analysis."""
    summary = (
        f"BIOS/firmware file '{path.name}' stored. "
        f"size={size} bytes, sha256={sha}. "
        "No semantic firmware parser is implemented in this build; "
        "do not interpret this file as analyzed."
    )
    return summary, STORED


# ---- Importer -----------------------------------------------------------

@dataclass
class ImportResult:
    doc_id: int
    file_name: str
    file_type: str
    sha256: str
    size_bytes: int
    status: str
    meta: Dict
    message: str


def _stored_name(path: Path, sha: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", path.name)
    return config.app_dir("Documents") / f"{sha[:12]}_{safe}"


def import_file(path: Path, session_id: Optional[int] = None,
                requested_by: str = "") -> ImportResult:
    """Import a single explicitly-submitted file. Never scans folders."""
    path = Path(path).expanduser().resolve()
    if not path.exists():
        return ImportResult(
            doc_id=0, file_name=str(path), file_type="unknown", sha256="",
            size_bytes=0, status=FAILED, meta={}, message="File not found.",
        )
    sha = sha256_of(path)
    size = path.stat().st_size
    # Duplicate detection by SHA-256: if already in library, link to session.
    existing = database.query_one(
        "SELECT id FROM documents WHERE sha256=?", (sha,)
    )
    if existing:
        if session_id:
            database.execute(
                "UPDATE documents SET session_id=? WHERE id=?",
                (session_id, existing["id"]),
            )
        existing_status = database.query_one(
            "SELECT status FROM documents WHERE id=?", (existing["id"],)
        )["status"]
        return ImportResult(
            doc_id=existing["id"], file_name=path.name,
            file_type=detect_file_type(path), sha256=sha, size_bytes=size,
            status=existing_status,
            meta={}, message="Duplicate file (same SHA-256) already imported.",
        )

    ftype = detect_file_type(path)
    stored = _stored_name(path, sha)
    shutil.copy2(path, stored)

    meta: Dict = {"requested_by": requested_by, "original_ext": path.suffix}
    status = RECEIVED
    text = ""
    message = "Received."

    if ftype == "pdf":
        try:
            text, scanned, pages = _extract_pdf(stored)
            meta["scanned"] = scanned
            meta["pages"] = len(pages)
            status = EXTRACTED if (text.strip() and not scanned) else (
                PARTIALLY if text.strip() else STORED
            )
            if scanned:
                meta["ocr_recommended"] = True
                message = (
                    "PDF appears to be scanned or image-only. Selectable text "
                    "extraction yielded little or no content. Local OCR is "
                    "available as an optional capability; enable it in "
                    "settings.documents.ocr_enabled to extract text."
                )
                status = PARTIALLY if text.strip() else STORED
            else:
                message = f"Extracted {len(pages)} pages of selectable text."
        except Exception as e:
            status = FAILED
            message = str(e)
    elif ftype == "text":
        try:
            text = _extract_text(stored)
            status = EXTRACTED if text.strip() else STORED
            message = "Extracted plain text." if text.strip() else "Empty text file."
        except Exception as e:
            status = FAILED
            message = str(e)
    elif ftype == "boardview":
        text, status = _extract_boardview(stored)
        message = "BoardView stored; ASCII format partially parsed." if status == EXTRACTED else (
            "BoardView stored; format not parsed, see note."
        )
    elif ftype == "bios":
        text, status = _describe_bios(stored, sha, size)
        message = text
    elif ftype == "image":
        meta["ocr_recommended"] = True
        status = STORED
        message = (
            "Image stored. Local OCR is an optional capability and is not "
            "enabled by default; enable settings.documents.ocr_enabled to "
            "extract text."
        )
    else:
        # Unknown extension: try as text; if not, store only.
        try:
            text = _extract_text(stored)
            status = PARTIALLY if text.strip() else STORED
            message = "Unknown file type; attempted plain-text extraction."
        except Exception:
            status = STORED
            message = "Unknown file type; stored without extraction."

    # Insert document row.
    cur = database.execute(
        """INSERT INTO documents(
            session_id, original_path, stored_path, file_name, file_type,
            sha256, size_bytes, status, meta, imported_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            session_id, str(path), str(stored), path.name, ftype, sha, size,
            status, __import__("json").dumps(meta, ensure_ascii=False),
            database.now_iso(),
        ),
    )
    doc_id = int(cur.lastrowid)

    # Chunk and index text (only if we got any).
    if text.strip():
        max_chars = config.load_settings().get("documents", {}).get(
            "max_extract_chars", 200_000
        )
        text = text[:max_chars]
        for seq, (chunk, page) in enumerate(chunk_text(text)):
            database.execute(
                "INSERT INTO document_chunks(document_id, seq, page, text) VALUES(?,?,?,?)",
                (doc_id, seq, page, chunk),
            )

    if session_id:
        database.add_timeline(
            session_id, "document",
            f"Imported {path.name} ({ftype}, {status})",
        )
        database.touch_session(session_id)

    return ImportResult(
        doc_id=doc_id, file_name=path.name, file_type=ftype, sha256=sha,
        size_bytes=size, status=status, meta=meta, message=message,
    )


def list_documents(session_id: Optional[int] = None) -> List[Dict]:
    if session_id is not None:
        return database.query_all(
            "SELECT * FROM documents WHERE session_id=? ORDER BY id", (session_id,)
        )
    return database.query_all("SELECT * FROM documents ORDER BY id")


def remove_document(doc_id: int) -> bool:
    """Remove a document and its chunks and FTS entries."""
    row = database.query_one(
        "SELECT stored_path FROM documents WHERE id=?", (doc_id,)
    )
    if not row:
        return False
    database.execute("DELETE FROM document_chunks WHERE document_id=?", (doc_id,))
    database.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    try:
        p = Path(row["stored_path"])
        if p.exists():
            p.unlink()
    except OSError:
        pass
    return True


def get_document(doc_id: int) -> Optional[Dict]:
    return database.query_one("SELECT * FROM documents WHERE id=?", (doc_id,))
