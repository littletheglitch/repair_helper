"""Document import, hashing, duplicate detection, indexing tests."""
from pathlib import Path

from eda import documents as D, sessions


def _write_text(tmp_app, name: str, text: str) -> Path:
    p = tmp_app / name
    p.write_text(text, encoding="utf-8")
    return p


def test_import_text_file(tmp_app):
    p = _write_text(tmp_app, "notes.txt", "TPS51716 EN pin is active high.")
    res = D.import_file(p, requested_by="user")
    assert res.status == "extracted"
    assert res.sha256
    assert res.size_bytes > 0
    assert res.file_type == "text"


def test_sha256_stable(tmp_app):
    p = _write_text(tmp_app, "a.txt", "hello")
    res = D.import_file(p)
    import hashlib
    assert res.sha256 == hashlib.sha256(b"hello").hexdigest()


def test_duplicate_detection(tmp_app):
    p = _write_text(tmp_app, "dup.txt", "duplicate content")
    r1 = D.import_file(p)
    r2 = D.import_file(p)
    assert r1.doc_id == r2.doc_id
    assert "Duplicate" in r2.message


def test_pdf_text_extraction(tmp_app):
    # Make a tiny PDF with PyMuPDF so we have something to extract.
    import fitz
    pdf_path = tmp_app / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "TPS51716 EN threshold is 1.2 V typical.")
    page.insert_text((72, 120), "UVLO is 4.0 V typical.")
    doc.save(str(pdf_path))
    doc.close()
    res = D.import_file(pdf_path)
    assert res.file_type == "pdf"
    assert res.status in ("extracted", "partially_extracted")
    # The chunk should be searchable.
    from eda import retrieval
    hits = retrieval.search_documents("TPS51716 EN threshold")
    assert hits, "PDF text not indexed"
    assert "TPS51716" in hits[0]["text"]


def test_boardview_binary_stored_only(tmp_app):
    # .fz is a binary BoardView extension we don't parse.
    p = tmp_app / "board.fz"
    p.write_bytes(b"\x00\x01\x02FZ-binary-payload\x00")
    res = D.import_file(p)
    assert res.file_type == "boardview"
    assert res.status == "stored_only"


def test_bios_stored_only(tmp_app):
    p = tmp_app / "bios.bin"
    p.write_bytes(b"\xff" * 4096)
    res = D.import_file(p)
    assert res.file_type == "bios"
    assert res.status == "stored_only"
    assert "No semantic firmware parser" in res.message


def test_indexing_and_search(tmp_app):
    p = _write_text(tmp_app, "datasheet.txt",
                     "The MAX8737 ACOK output goes high when adapter is valid.")
    D.import_file(p)
    from eda import retrieval
    hits = retrieval.search_documents("ACOK adapter valid")
    assert hits
    assert "MAX8737" in hits[0]["text"]


def test_remove_document(tmp_app):
    p = _write_text(tmp_app, "x.txt", "removable content here")
    res = D.import_file(p)
    assert D.remove_document(res.doc_id) is True
    assert D.get_document(res.doc_id) is None


def test_path_with_spaces(tmp_app):
    sp = tmp_app / "my folder" / "with spaces.txt"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("space test content", encoding="utf-8")
    res = D.import_file(sp)
    assert res.status == "extracted"
    assert res.doc_id > 0


def test_missing_file_failed(tmp_app):
    res = D.import_file(Path("does_not_exist.txt"))
    assert res.status == "failed"
    assert res.doc_id == 0


def test_status_documented(tmp_app):
    p = _write_text(tmp_app, "s.txt", "ok")
    res = D.import_file(p)
    assert res.status in (
        D.RECEIVED, D.EXTRACTED, D.PARTIALLY, D.STORED, D.FAILED,
    )
