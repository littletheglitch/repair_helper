"""Tests for the Stack Exchange connector — fixture-based, no network."""
import json
from pathlib import Path

import pytest

from kc import database, config, schemas
from kc.connectors import stackexchange as SE
from kc.connectors.base import RawItem, ParsedItem

FIXTURES = Path(__file__).parent / "fixtures" / "stackexchange"


class FakeFetcher:
    """Serves canned JSON from the fixtures dir based on the URL path."""
    def __init__(self):
        self.calls = []

    def fetch(self, url, rate_per_min=6, extra_headers=None):
        self.calls.append(url)
        if "/questions?" in url and "/answers" not in url:
            page = 1
            if "page=" in url:
                import re
                m = re.search(r"page=(\d+)", url)
                if m:
                    page = int(m.group(1))
            p = FIXTURES / f"questions_page{page}.json"
            if not p.exists():
                # No more pages: return empty.
                body = json.dumps({"items": [], "has_more": False}).encode()
            else:
                body = p.read_bytes()
            from kc import fetcher
            return fetcher.FetchResult(url=url, status=200, body=body,
                                       headers={"Content-Type": "application/json"},
                                       kind="ok")
        if "/answers" in url:
            import re
            m = re.search(r"/questions/(\d+)/answers", url)
            qid = m.group(1) if m else "0"
            p = FIXTURES / f"answers_{qid}.json"
            body = p.read_bytes() if p.exists() else b'{"items":[]}'
            from kc import fetcher
            return fetcher.FetchResult(url=url, status=200, body=body,
                                       headers={"Content-Type": "application/json"},
                                       kind="ok")
        from kc import fetcher
        return fetcher.FetchResult(url=url, status=404, kind="error", error="not found")


def _setup_source():
    database.upsert_source("stackexchange_electronics",
                           "stackexchange_api",
                           "https://electronics.stackexchange.com",
                           enabled=True, license="CC-BY-SA 4.0",
                           access_method="public API",
                           rate_per_min=6, max_depth=1, max_records=10)


def test_connector_name_and_kind(tmp_app):
    c = SE.StackExchangeConnector(database.get_source("stackexchange_electronics"))
    assert c.name == "stackexchange_electronics"
    assert c.kind == schemas.SourceKind.STACKEXCHANGE_API


def test_discover_yields_questions_and_answers(tmp_app):
    _setup_source()
    f = FakeFetcher()
    c = SE.StackExchangeConnector(database.get_source("stackexchange_electronics"),
                                  fetcher=f)
    items = list(c.discover())
    # 2 questions + 2 answers (question 123456) + 0 answers (123457).
    # Order: q1, a1(123456), a2(123456), q2.
    assert len(items) == 4
    assert items[0].meta["kind"] == "question"
    assert items[1].meta["kind"] == "answer"
    assert items[2].meta["kind"] == "answer"
    assert items[3].meta["kind"] == "question"


def test_parse_question_preserves_provenance(tmp_app):
    _setup_source()
    f = FakeFetcher()
    c = SE.StackExchangeConnector(database.get_source("stackexchange_electronics"),
                                  fetcher=f)
    items = list(c.discover())
    q = c.parse(items[0])
    assert q is not None
    assert q.title == "Lenovo G50-80 no power, +5VALW shorted"
    assert q.author == "repair_tech"
    assert "+5VALW" in q.text
    assert q.url == "https://electronics.stackexchange.com/questions/123456/lenovo-g50-80-no-power"
    assert q.thread_id == "123456"
    assert q.meta["kind"] == "question"
    assert "motherboard" in q.meta["tags"]


def test_parse_answer_preserves_question_link(tmp_app):
    _setup_source()
    f = FakeFetcher()
    c = SE.StackExchangeConnector(database.get_source("stackexchange_electronics"),
                                  fetcher=f)
    items = list(c.discover())
    a = c.parse(items[1])  # first answer (interleaved after q1)
    assert a is not None
    assert a.thread_id == "123456"
    assert a.post_id == "789001"
    assert a.meta["kind"] == "answer"
    assert a.meta["is_accepted"] is True
    assert "C45" in a.text


def test_parse_strips_html(tmp_app):
    _setup_source()
    f = FakeFetcher()
    c = SE.StackExchangeConnector(database.get_source("stackexchange_electronics"),
                                  fetcher=f)
    items = list(c.discover())
    q = c.parse(items[0])
    assert "<p>" not in q.text  # HTML stripped
    assert "</p>" not in q.text


def test_resume_increments_page(tmp_app):
    _setup_source()
    database.update_crawl_state("stackexchange_electronics",
                                last_page=1, status="done")
    f = FakeFetcher()
    c = SE.StackExchangeConnector(database.get_source("stackexchange_electronics"),
                                  fetcher=f)
    list(c.discover())
    state = database.get_crawl_state("stackexchange_electronics")
    assert state["last_page"] >= 1


def test_parse_rejects_empty_body(tmp_app):
    _setup_source()
    c = SE.StackExchangeConnector(database.get_source("stackexchange_electronics"))
    raw = RawItem(url="https://x", thread_id="t", post_id="p",
                  raw='{"title":"x","body":""}', meta={"kind": "question"})
    assert c.parse(raw) is None


def test_published_at_is_iso_date(tmp_app):
    _setup_source()
    f = FakeFetcher()
    c = SE.StackExchangeConnector(database.get_source("stackexchange_electronics"),
                                  fetcher=f)
    items = list(c.discover())
    q = c.parse(items[0])
    assert q.published_at == "2024-05-01"  # 1714521600 = 2024-05-01 UTC


def test_max_records_limits_discovery(tmp_app):
    _setup_source()
    database.upsert_source("stackexchange_electronics",
                           "stackexchange_api",
                           "https://electronics.stackexchange.com",
                           enabled=True, rate_per_min=6, max_depth=1,
                           max_records=2)  # only 2 records
    f = FakeFetcher()
    c = SE.StackExchangeConnector(database.get_source("stackexchange_electronics"),
                                  fetcher=f)
    items = list(c.discover())
    assert len(items) <= 2
