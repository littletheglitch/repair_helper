#!/usr/bin/env python3
"""Phase 6 test suite — knowledge quality.

Verifies:
  - Synonym expansion ('step-down' matches 'buck')
  - Dedup (identical title+content collapsed)
  - verified_only excludes unverified claims
  - Provenance tiers (datasheet > app_note > community)
  - Entity-relationship graph (component↔topology↔failure↔procedure)
  - Source references preserved on every item

Run: python Tests/test_phase6_knowledge.py
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from DiagnosticEngines.core.knowledge_platform import (
    KnowledgeRetriever, KnowledgeItem, RetrievalResult,
    KnowledgeGraph, KnowledgeLink, build_seed_knowledge_graph,
)


class MemStore:
    """In-memory store (bypasses disk) for isolated testing."""
    items: list[KnowledgeItem] = []


def make_retriever(items: list[KnowledgeItem]) -> KnowledgeRetriever:
    store = MemStore()
    store.items = items
    return KnowledgeRetriever(store)


def KI(title, content, status="unverified", category="forum_post",
       url=""):
    return KnowledgeItem(title=title, content_text=content,
                         verification_status=status, category=category,
                         source_url=url)


class TestSynonymExpansion(unittest.TestCase):

    def test_step_down_matches_buck(self):
        """A query for 'step-down' must match items containing 'buck'
        (synonym expansion)."""
        r = make_retriever([KI("Buck basics", "a buck converter steps down")])
        results = r.search("step-down converter")
        self.assertTrue(results, "synonym 'step-down' must match 'buck'")

    def test_step_up_matches_boost(self):
        r = make_retriever([KI("Boost basics", "a boost converter steps up")])
        results = r.search("step-up")
        self.assertTrue(results, "synonym 'step-up' must match 'boost'")

    def test_synonym_does_not_invent_matches(self):
        """A query with no real overlap must still return nothing."""
        r = make_retriever([KI("Zener", "zener diode regulation")])
        results = r.search("buck converter")
        self.assertEqual(results, [],
            "no synonym between buck and zener — must not match")


class TestDedup(unittest.TestCase):

    def test_duplicates_collapsed(self):
        """Two items with identical title+content must collapse to one
        (keeping the higher-scored/verified copy)."""
        r = make_retriever([
            KI("Buck", "buck converter", status="verified", category="datasheet"),
            KI("Buck", "buck converter", status="unverified", category="forum_post"),
        ])
        results = r.search("buck")
        self.assertEqual(len(results), 1, "duplicates must collapse to 1")

    def test_different_content_not_deduped(self):
        r = make_retriever([
            KI("Buck", "buck converter basics"),
            KI("Buck", "buck converter advanced design"),  # different content
        ])
        results = r.search("buck")
        self.assertEqual(len(results), 2, "different content must not dedup")


class TestVerifiedOnly(unittest.TestCase):

    def test_verified_only_excludes_unverified(self):
        r = make_retriever([
            KI("Verified buck", "buck", status="verified", category="datasheet"),
            KI("Community buck", "buck", status="unverified", category="forum_post"),
        ])
        results = r.search("buck", verified_only=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].item.verification_status, "verified")

    def test_unverified_never_presented_as_verified(self):
        """When verified_only=True, no unverified item may appear."""
        r = make_retriever([
            KI("Community", "buck tips", status="unverified", category="forum_post"),
        ])
        results = r.search("buck", verified_only=True)
        self.assertEqual(results, [],
            "unverified item must not appear when verified_only=True")


class TestProvenanceTiers(unittest.TestCase):

    def test_datasheet_scores_higher_than_community(self):
        """A datasheet item must out-rank a community item with the same
        token overlap (manufacturer facts > community claims)."""
        r = make_retriever([
            KI("Community", "buck converter", status="unverified", category="forum_post"),
            KI("Datasheet", "buck converter", status="verified", category="datasheet"),
        ])
        results = r.search("buck converter")
        self.assertEqual(len(results), 2)
        # Datasheet (verified + manufacturer) must rank first
        self.assertEqual(results[0].item.category, "datasheet")
        self.assertGreater(results[0].score, results[1].score)


class TestSourceReferences(unittest.TestCase):

    def test_every_item_has_source_fields(self):
        """Every KnowledgeItem must carry source fields (source_url or
        source_author) — no orphan claims."""
        ki = KI("Test", "content", status="verified", category="datasheet",
                url="https://example.com/datasheet.pdf")
        self.assertTrue(ki.source_url or ki.source_author,
            "verified item must have a source reference")

    def test_unverified_items_marked(self):
        """Unverified items must have verification_status='unverified'
        so they're never confused with manufacturer facts."""
        ki = KI("Forum tip", "buck tip", status="unverified", category="forum_post")
        self.assertEqual(ki.verification_status, "unverified")


class TestEntityGraph(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.g = build_seed_knowledge_graph()

    def test_graph_has_links(self):
        self.assertGreater(len(self.g), 10, "seed graph must have links")

    def test_component_to_topology(self):
        """UC3842 must link to flyback topology."""
        nb = self.g.neighbors("component", "UC3842")
        topos = [l.object_id for l in nb if l.relation == "used_in"]
        self.assertIn("flyback", topos)

    def test_topology_to_failure(self):
        """Flyback must link to its common failure patterns."""
        nb = self.g.neighbors("topology", "flyback", relation="has_failure")
        failures = [l.object_id for l in nb]
        self.assertIn("no_output", failures)

    def test_failure_to_procedure(self):
        """A failure pattern must link to a diagnostic procedure."""
        nb = self.g.neighbors("failure_pattern", "no_output",
                              relation="diagnosed_by")
        procedures = [l.object_id for l in nb]
        self.assertTrue(procedures, "no_output must link to a procedure")
        # The procedure must have a note explaining the steps
        self.assertTrue(nb[0].note, "procedure link must have a note")

    def test_related_returns_both_directions(self):
        """related() must return links where the entity is subject OR
        object."""
        # flyback is the object of UC3842's 'used_in' link
        rel = self.g.related("topology", "flyback")
        self.assertTrue(any(l.subject_type == "component" for l in rel),
            "related() must include links where flyback is the object")

    def test_custom_link_addable(self):
        """A caller can add a custom link."""
        g = KnowledgeGraph()
        g.add(KnowledgeLink("component", "MY_PART", "used_in",
                            "topology", "buck"))
        nb = g.neighbors("component", "MY_PART")
        self.assertEqual(len(nb), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
