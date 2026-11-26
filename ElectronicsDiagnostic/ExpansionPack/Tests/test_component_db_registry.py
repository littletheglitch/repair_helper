"""Regression coverage for component registry indexing and duplicate handling."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from DiagnosticEngines.core.component_db import ComponentDatabase


class TestComponentDatabaseRegistry(unittest.TestCase):
    def test_component_families_are_searchable_without_duplicate_objects(self):
        db = ComponentDatabase.load(PACK)
        records = db.all_records()
        keys = [
            (
                str(item.get("part_number", "")).strip().casefold(),
                str(item.get("manufacturer", "")).strip().casefold(),
                str(item.get("category", "")).strip().casefold(),
            )
            for item in records
        ]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIsNotNone(db.find_part("lm358"))
        self.assertTrue(db.by_category("op_amp"))

    def test_bad_json_record_is_reported_without_aborting_load(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ic_dir = root / "ComponentDatabase" / "ICs"
            ic_dir.mkdir(parents=True)
            (ic_dir / "good.json").write_text(
                json.dumps({"part_number": "TEST123", "manufacturer": "Example", "category": "test"}),
                encoding="utf-8",
            )
            (ic_dir / "bad.json").write_text("{not-json", encoding="utf-8")
            db = ComponentDatabase.load(root)
            self.assertIsNotNone(db.find_part("TEST123"))
            self.assertEqual(len(db.load_errors), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
