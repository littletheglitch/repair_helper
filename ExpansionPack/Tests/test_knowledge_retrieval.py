#!/usr/bin/env python3
"""Adversarial tests for knowledge retrieval (P4).

REQUIRED: retrieval beyond substring matching using:
  - tokenization (split query into terms)
  - normalization (lowercase, strip punctuation)
  - relevance ranking (more term matches rank higher)
  - technical-entity recognition (part numbers, topologies)
  - provenance separation (manufacturer facts vs community claims)
  - confidence scoring

A substring search misses "buck regulator" when the query is "buck
converter" (no substring match) but the tokens overlap. A ranked
retriever returns it with a partial-match score.
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from DiagnosticEngines.core.knowledge_platform import (
    KnowledgeStore, KnowledgeItem, KnowledgeRetriever,
)


def make_item(title, content, tags=None, source_author="",
              verification_status="unverified", category="app_note"):
    return KnowledgeItem(
        title=title, content_text=content, tags=tags or [],
        source_author=source_author,
        verification_status=verification_status,
        category=category,
    )


class TestTokenizedRetrieval(unittest.TestCase):
    """Retrieval must tokenize, not just substring-match."""

    @classmethod
    def setUpClass(cls):
        cls.store = KnowledgeStore()
        cls.store.add(make_item(
            "Buck converter design",
            "A buck converter steps down voltage using an inductor and switch.",
            tags=["buck", "converter"], source_author="TI",
            verification_status="verified", category="app_note"))
        cls.store.add(make_item(
            "LDO basics",
            "A linear regulator drops voltage via a pass transistor.",
            tags=["ldo", "linear"], source_author="community",
            verification_status="unverified", category="forum_post"))
        cls.retriever = KnowledgeRetriever(cls.store)

    def test_substring_match_still_works(self):
        """A query that is an exact substring still returns the item."""
        results = self.retriever.search("buck converter")
        titles = [r.item.title for r in results]
        self.assertIn("Buck converter design", titles)

    def test_tokenized_partial_match(self):
        """A query 'buck regulator' should match 'buck converter' via
        the shared token 'buck', even though 'buck regulator' is not a
        substring of the content. Substring search would miss this."""
        results = self.retriever.search("buck regulator")
        titles = [r.item.title for r in results]
        self.assertIn(
            "Buck converter design", titles,
            "DEFECT: tokenized retrieval must match 'buck regulator' to "
            "'buck converter' via shared token 'buck'",
        )

    def test_results_are_ranked(self):
        """Results must be ranked by relevance (match score). An item
        matching more query tokens ranks higher than one matching fewer."""
        store = KnowledgeStore()
        store.add(make_item("Buck", "buck buck buck", tags=["buck"]))
        store.add(make_item("Buck and boost", "buck boost", tags=["buck","boost"]))
        retriever = KnowledgeRetriever(store)
        results = retriever.search("buck boost")
        # The item matching BOTH tokens should rank first.
        self.assertEqual(results[0].item.title, "Buck and boost",
                         "DEFECT: results not ranked by match count")


class TestProvenanceSeparation(unittest.TestCase):
    """Manufacturer facts must be distinguishable from community claims."""

    @classmethod
    def setUpClass(cls):
        cls.store = KnowledgeStore()
        cls.store.add(make_item(
            "TPS51224 datasheet fact",
            "TPS51224 is a buck controller.",
            source_author="Texas Instruments",
            verification_status="verified", category="datasheet"))
        cls.store.add(make_item(
            "Forum claim about TPS51224",
            "TPS51224 sometimes fails on overvoltage (unconfirmed).",
            source_author="anonymous forum user",
            verification_status="unverified", category="forum_post"))
        cls.retriever = KnowledgeRetriever(cls.store)

    def test_manufacturer_facts_separable(self):
        """The retriever must let the caller filter to verified
        manufacturer facts only, separating them from community claims."""
        results = self.retriever.search("TPS51224")
        # Filter to the items this test added (the store also loads
        # pre-existing KI items from disk).
        mine = [r for r in results if r.item.title in
                ("TPS51224 datasheet fact", "Forum claim about TPS51224")]
        verified = [r for r in mine if r.item.verification_status == "verified"]
        unverified = [r for r in mine if r.item.verification_status == "unverified"]
        self.assertTrue(verified, "DEFECT: no verified (manufacturer) facts returned")
        self.assertTrue(unverified, "DEFECT: no unverified (community) claims returned")
        # The verified item should be from a manufacturer
        self.assertEqual(verified[0].item.source_author, "Texas Instruments")
        self.assertEqual(unverified[0].item.source_author, "anonymous forum user")

    def test_confidence_score_reflects_provenance(self):
        """A verified manufacturer fact should have a higher confidence
        score than an unverified community claim, all else equal."""
        results = self.retriever.search("TPS51224")
        mine = [r for r in results if r.item.title in
                ("TPS51224 datasheet fact", "Forum claim about TPS51224")]
        scores = {r.item.title: r.score for r in mine}
        verified_score = scores.get("TPS51224 datasheet fact", 0)
        unverified_score = scores.get("Forum claim about TPS51224", 0)
        self.assertGreater(
            verified_score, unverified_score,
            "DEFECT: verified manufacturer fact must score higher than "
            "unverified community claim",
        )


class TestEntityRecognition(unittest.TestCase):
    """The retriever should recognize technical entities (part numbers,
    topology names) in the query and boost items mentioning them."""

    def test_part_number_entity_boost(self):
        """A query containing a part number (e.g. TPS51224) should boost
        items that mention that exact part number."""
        store = KnowledgeStore()
        store.add(make_item("Mentions TPS51224", "The TPS51224 controller..."))
        store.add(make_item("Generic buck", "A buck controller..."))
        retriever = KnowledgeRetriever(store)
        results = retriever.search("TPS51224")
        # Filter to the items this test added (the store also loads
        # pre-existing KI items from disk).
        mine = [r for r in results if r.item.title in
                ("Mentions TPS51224", "Generic buck")]
        self.assertTrue(mine, "DEFECT: test items not returned")
        self.assertEqual(
            mine[0].item.title, "Mentions TPS51224",
            "DEFECT: part-number entity must rank the matching item first",
        )


class TestNoResultsReturnsEmpty(unittest.TestCase):
    """A query matching nothing returns an empty list, not an error."""

    def test_no_match(self):
        store = KnowledgeStore()
        store.add(make_item("Buck", "buck converter"))
        retriever = KnowledgeRetriever(store)
        results = retriever.search("zzz_no_match_zzz")
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
