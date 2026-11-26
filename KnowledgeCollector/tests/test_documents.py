"""Tests for the document processing module (PDF/HTML/text/OCR/corrupt/BIOS)."""
import fitz
from pathlib import Path

from kc import documents as D, bios_guard, config


def _make_pdf(path: Path, text: str = "Hello datasheet world. " * 50):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def _make_scanned_pdf(path: Path):
    """A PDF with a blank page (no extractable text)."""
    doc = fitz.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()


def test_detect_pdf(tmp_path):
    p = tmp_path / "x.pdf"
    _make_pdf(p)
    assert D.detect_file_type(p) == "pdf"


def test_detect_text(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hello")
    assert D.detect_file_type(p) == "text"


def test_detect_html(tmp_path):
    p = tmp_path / "page.html"
    p.write_text("<!DOCTYPE html><html><body><p>x</p></body></html>")
    assert D.detect_file_type(p) == "html"


def test_detect_image(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    assert D.detect_file_type(p) == "image"


def test_detect_unknown(tmp_path):
    p = tmp_path / "x.dat"
    p.write_bytes(b"\x00\x01\x02")
    assert D.detect_file_type(p) == "unknown"


def test_process_text_file(tmp_app, tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("Laptop no power issue: measured +5VALW = 0V. Replaced C45.")
    doc = D.process_file(p, "eevblog", "https://x/notes.txt")
    assert doc.status == "extracted"
    assert doc.file_type == "text"
    assert doc.sha256
    assert doc.original_path.startswith("originals/")
    # Extracted text stored on disk.
    ext = config.extracted_dir() / f"{doc.sha256[:12]}.txt"
    assert ext.exists()
    assert "+5VALW" in ext.read_text()


def test_process_pdf(tmp_app, tmp_path):
    p = tmp_path / "datasheet.pdf"
    _make_pdf(p, "TPS51716 datasheet. VIN 4.5V to 18V. UVLO 4.0V typ.")
    doc = D.process_file(p, "eevblog", "https://x/datasheet.pdf")
    assert doc.status == "extracted"
    assert doc.file_type == "pdf"
    assert doc.meta.get("pages") == 1
    assert "TPS51716" in (config.extracted_dir() / f"{doc.sha256[:12]}.txt").read_text()


def test_process_scanned_pdf_marks_ocr_recommended(tmp_app, tmp_path):
    p = tmp_path / "scanned.pdf"
    _make_scanned_pdf(p)
    doc = D.process_file(p, "eevblog", "https://x/scanned.pdf")
    assert doc.status in ("partially_extracted", "stored_only")
    assert doc.meta.get("ocr_recommended") is True
    assert doc.meta.get("scanned") is True


def test_process_html_extracts_title_and_body(tmp_app, tmp_path):
    p = tmp_path / "page.html"
    p.write_text(
        "<!DOCTYPE html><html><head><title>MOSFET failure analysis</title>"
        "<meta name='author' content='tech'></head><body>"
        "<p>The MOSFET was shorted drain to source.</p>"
        "<p>Replaced it and the board works.</p>"
        "</body></html>")
    doc = D.process_file(p, "eevblog", "https://x/page.html")
    assert doc.status == "extracted"
    assert doc.file_type == "html"
    assert doc.meta.get("title") == "MOSFET failure analysis"
    assert "MOSFET" in (config.extracted_dir() / f"{doc.sha256[:12]}.txt").read_text()


def test_process_bios_rejected(tmp_app, tmp_path):
    p = tmp_path / "firmware.bin"
    p.write_bytes(b"\xAA\x55" + b"\x00" * 200)
    doc = D.process_file(p, "eevblog", "https://x/firmware.bin")
    assert doc.status == "rejected"
    assert doc.file_type == "rejected"
    assert "BIOS dump" in doc.meta["reason"]
    # The original file must NOT be stored.
    assert not (config.originals_dir() / p.name).exists()


def test_process_corrupt_pdf_does_not_crash(tmp_app, tmp_path):
    p = tmp_path / "broken.pdf"
    p.write_bytes(b"%PDF-1.4\nthis is not really a pdf\n%%EOF\n")
    doc = D.process_file(p, "eevblog", "https://x/broken.pdf")
    # Must not raise; status is failed or stored_only (no extractable text).
    assert doc.status in ("failed", "stored_only", "partially_extracted")
    assert doc.file_type == "pdf"


def test_process_preserves_source_url_in_meta(tmp_app, tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("laptop no power issue with motherboard repair")
    doc = D.process_file(p, "eevblog", "https://example.com/thread/123#post5")
    assert doc.url == "https://example.com/thread/123#post5"
    assert doc.meta.get("source_url") == "https://example.com/thread/123#post5"


def test_process_sha256_is_deterministic(tmp_app, tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("same content")
    doc1 = D.process_file(p, "eevblog", "https://x/1")
    doc2 = D.process_file(p, "eevblog", "https://x/2")
    assert doc1.sha256 == doc2.sha256


def test_process_text_sha_in_meta(tmp_app, tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hello world motherboard")
    doc = D.process_file(p, "eevblog", "https://x")
    assert doc.meta.get("text_sha256")
