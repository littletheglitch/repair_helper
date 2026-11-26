"""Tests for the datasheet intelligence engine."""
from pathlib import Path

from eda.engines import base as E
from eda.engines import datasheet as DS
from eda import schemas as S, database, documents as D


def _make_sample_datasheet(tmp_app, part_number: str = "TPS51716") -> Path:
    """Create a tiny datasheet-like PDF with section headings and value rows."""
    import fitz
    p = tmp_app / f"{part_number}_datasheet.pdf"
    doc = fitz.open()
    page = doc.new_page()
    lines = [
        f"{part_number} Datasheet (sample, for testing)",
        "Manufacturer: Texas Instruments (fictional sample)",
        "",
        "Absolute Maximum Ratings",
        "VIN voltage        -0.3 to 20 V",
        "EN voltage         -0.3 to 6 V",
        "Junction temperature  -40 to 150 C",
        "",
        "Recommended Operating Conditions",
        "VIN voltage        4.5 to 18 V",
        "Output voltage     0.6 to 5 V",
        "",
        "Electrical Characteristics",
        "UVLO threshold typ 4.0 V",
        "EN threshold typ 1.2 V",
        "PGOOD assertion 3.0 V typ",
        "Switching frequency 500 kHz typ",
        "",
        "Pin Description",
        "EN    Enable input, active high",
        "VIN   Input supply",
        "PGOOD Power-good output, open drain",
        "FB    Feedback input",
    ]
    page.insert_text((72, 72), "\n".join(lines))
    doc.save(str(p))
    doc.close()
    return p


def test_extract_finds_part_number_from_filename(tmp_app):
    p = _make_sample_datasheet(tmp_app, "TPS51716")
    res = D.import_file(p)
    assert res.status == "extracted"
    r = E.call("datasheet", "extract", {"document_id": res.doc_id})
    assert r.ok
    assert "TPS51716" in r.value["part_number"]


def test_extract_stores_params_with_provenance(tmp_app):
    p = _make_sample_datasheet(tmp_app)
    res = D.import_file(p)
    r = E.call("datasheet", "extract", {"document_id": res.doc_id})
    assert r.ok
    assert r.value["parameters_extracted"] > 0
    # Stored rows in datasheet_params carry provenance fields.
    rows = database.query_all(
        "SELECT * FROM datasheet_params WHERE document_id=?", (res.doc_id,)
    )
    assert rows
    sample = rows[0]
    assert "part_number" in sample
    assert "page" in sample
    assert "rating_type" in sample
    assert "extraction_confidence" in sample
    assert "verification_status" in sample


def test_extract_classifies_rating_types(tmp_app):
    p = _make_sample_datasheet(tmp_app)
    res = D.import_file(p)
    E.call("datasheet", "extract", {"document_id": res.doc_id})
    rows = database.query_all(
        "SELECT rating_type FROM datasheet_params WHERE document_id=?",
        (res.doc_id,),
    )
    types = {r["rating_type"] for r in rows}
    # We expect at least one of these to be classified from the headings.
    assert types & {"absolute_maximum", "recommended",
                    "electrical_characteristics", "pin_description"}


def test_lookup_returns_previously_extracted(tmp_app):
    p = _make_sample_datasheet(tmp_app, "ABC123")
    res = D.import_file(p)
    E.call("datasheet", "extract", {"document_id": res.doc_id, "part_number": "ABC123"})
    r = E.call("datasheet", "lookup", {"part_number": "ABC123"})
    assert r.ok
    assert r.value["parameters"]


def test_lookup_missing_part_number_errors(tmp_app):
    r = E.call("datasheet", "lookup", {"part_number": "DOES_NOT_EXIST_999"})
    assert not r.ok
    assert r.error.code == S.ErrorCode.NOT_APPLICABLE


def test_extract_missing_document_errors(tmp_app):
    r = E.call("datasheet", "extract", {"document_id": 999999})
    assert not r.ok
    assert r.error.code == S.ErrorCode.NOT_APPLICABLE


def test_extract_never_invents_values(tmp_app):
    """The engine must NOT invent missing values. A document with no
    extractable rows must return parameters_extracted=0, not a fabricated set."""
    import fitz
    p = tmp_app / "empty.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "This document has no numeric parameter rows.")
    doc.save(str(p)); doc.close()
    res = D.import_file(p)
    r = E.call("datasheet", "extract", {"document_id": res.doc_id})
    assert r.ok
    assert r.value["parameters_extracted"] == 0


def test_list_documents_for_part(tmp_app):
    p = _make_sample_datasheet(tmp_app, "PARTXYZ")
    res = D.import_file(p)
    r = E.call("datasheet", "list_documents_for_part", {"part_number": "PARTXYZ"})
    assert r.ok
    assert res.doc_id in r.value["document_ids"]


def test_result_envelope_includes_assumptions_and_limitations(tmp_app):
    p = _make_sample_datasheet(tmp_app)
    res = D.import_file(p)
    r = E.call("datasheet", "extract", {"document_id": res.doc_id})
    assert r.ok
    assert r.assumptions
    assert r.limitations
    assert any("no missing values are invented" in l for l in r.limitations)
