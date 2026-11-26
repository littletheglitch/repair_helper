from __future__ import annotations
import sys
import tempfile
import zipfile
from pathlib import Path
import unittest

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from DiagnosticEngines.core.document_extractors import extract_document, ExtractionError
from DiagnosticEngines.core.knowledge_platform import KnowledgeStore, IngestionPipeline
from DiagnosticEngines.core.failure_pattern_db import FailurePatternDB, RepairCaseLearner


class TestDocumentExtractors(unittest.TestCase):
    def test_html_extracts_visible_text_and_skips_chrome(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "thread.html"
            p.write_text(
                '<html><head><title>Board repair</title><script>BADJS</script></head>'
                '<body><nav>MENU</nav><article><h1>Board repair</h1>'
                '<p>VIN is 19 V</p><div class="sidebar">SIDE AD</div>'
                '<p>Enable is 3.3 V</p></article></body></html>', encoding="utf-8")
            result = extract_document(p)
            self.assertIn("VIN is 19 V", result.text)
            self.assertIn("Enable is 3.3 V", result.text)
            self.assertNotIn("BADJS", result.text)
            self.assertNotIn("MENU", result.text)
            self.assertNotIn("SIDE AD", result.text)
            self.assertEqual(result.metadata.get("title"), "Board repair")

    def test_docx_extracts_paragraphs_and_tables(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "case.docx"
            xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   '<w:body><w:p><w:r><w:t>Repair observation</w:t></w:r></w:p>'
                   '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>VIN</w:t></w:r></w:p></w:tc>'
                   '<w:tc><w:p><w:r><w:t>19V</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                   '</w:body></w:document>')
            with zipfile.ZipFile(p, "w") as archive:
                archive.writestr("word/document.xml", xml)
            result = extract_document(p)
            self.assertIn("Repair observation", result.text)
            self.assertIn("VIN | 19V", result.text)

    def test_csv_extracts_labeled_rows(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "measurements.csv"
            p.write_text("name,value,unit\nVIN,19.2,V\nVCORE,0.9,V\n", encoding="utf-8")
            result = extract_document(p)
            self.assertIn("name: VIN", result.text)
            self.assertIn("value: 19.2", result.text)
            self.assertIn("name: VCORE", result.text)
            self.assertIn("value: 0.9", result.text)

    def test_firmware_binary_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "dump.bin"
            p.write_bytes(b"BIOS")
            with self.assertRaises(ExtractionError):
                extract_document(p)

    def test_ingest_file_is_deduplicated_by_sha256(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "thread.html"
            p.write_text("<html><body><article><p>Measured VIN 19 V</p></article></body></html>", encoding="utf-8")
            store = KnowledgeStore(Path(td) / "pack")
            pipeline = IngestionPipeline(store)
            first, _ = pipeline.ingest_file(p, source_url="https://example.test/thread/1")
            second, log = pipeline.ingest_file(p, source_url="https://example.test/thread/1")
            self.assertIsNotNone(first)
            self.assertIs(second, first)
            self.assertEqual(store.count(), 1)
            self.assertTrue(any("duplicate" in entry for entry in log))

    def test_forbidden_category_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "document.html"
            p.write_text("<html><body><p>not ingested</p></body></html>", encoding="utf-8")
            store = KnowledgeStore(Path(td) / "pack")
            item, log = IngestionPipeline(store).ingest_file(p, category="boardview")
            self.assertIsNone(item)
            self.assertEqual(store.count(), 0)
            self.assertTrue(any("FORBIDDEN" in entry for entry in log))


class TestSeedCasePolicy(unittest.TestCase):
    def test_shipped_seed_is_not_real_or_promotable(self):
        db = FailurePatternDB(PACK)
        self.assertEqual(db.real_case_count(), 0)
        self.assertEqual(db.search(failure_pattern="shorted_rail"), [])
        examples = db.search(failure_pattern="shorted_rail", include_seed_examples=True)
        self.assertEqual(len(examples), 1)
        learner = RepairCaseLearner()
        self.assertFalse(learner.validate(examples[0]).valid)
        ok, reason = learner.can_promote(examples[0])
        self.assertFalse(ok)
        self.assertIn("Seed examples", reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
