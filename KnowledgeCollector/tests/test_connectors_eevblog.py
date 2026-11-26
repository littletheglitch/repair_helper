"""Tests for the EEVblog connector — fixture-based, no network."""
from pathlib import Path

import pytest

from kc import database, config, schemas
from kc.connectors import eevblog as EB
from kc.connectors.base import RawItem

FIXTURES = Path(__file__).parent / "fixtures" / "eevblog"


class FakeFetcher:
    """Serves canned HTML from the fixtures dir based on the URL."""
    def __init__(self):
        self.calls = []

    def fetch(self, url, rate_per_min=6, extra_headers=None):
        self.calls.append(url)
        from kc import fetcher
        # Board index page.
        if "board,40" in url or url.endswith("/repair/"):
            body = (FIXTURES / "board_page.html").read_bytes()
            return fetcher.FetchResult(url=url, status=200, body=body,
                                       headers={"Content-Type": "text/html"},
                                       kind="ok")
        # Thread page.
        if "topic,12345" in url:
            body = (FIXTURES / "thread_12345.html").read_bytes()
            return fetcher.FetchResult(url=url, status=200, body=body,
                                       headers={"Content-Type": "text/html"},
                                       kind="ok")
        if "topic,12347" in url:
            body = (FIXTURES / "thread_12347.html").read_bytes()
            return fetcher.FetchResult(url=url, status=200, body=body,
                                       headers={"Content-Type": "text/html"},
                                       kind="ok")
        # Unknown thread -> empty.
        return fetcher.FetchResult(url=url, status=404, kind="error",
                                   error="not found")


def _setup_source():
    database.upsert_source("eevblog", "forum_html",
                           "https://www.eevblog.com/forum/",
                           enabled=True, license="forum ToS",
                           access_method="HTML (robots-respecting)",
                           rate_per_min=6, max_depth=1, max_records=20)


def test_connector_name(tmp_app):
    c = EB.EEvblogConnector(database.get_source("eevblog"))
    assert c.name == "eevblog"
    assert c.kind == schemas.SourceKind.FORUM_HTML


def test_discover_yields_relevant_posts(tmp_app):
    _setup_source()
    f = FakeFetcher()
    c = EB.EEvblogConnector(database.get_source("eevblog"), fetcher=f)
    items = list(c.discover())
    # Two relevant threads; thread 12345 has 2 posts, 12347 has 1 post.
    assert len(items) >= 3
    # All items have the source title set.
    for it in items:
        assert it.meta["kind"] == "post"
        assert it.meta["title"]


def test_parse_preserves_post_text(tmp_app):
    _setup_source()
    f = FakeFetcher()
    c = EB.EEvblogConnector(database.get_source("eevblog"), fetcher=f)
    items = list(c.discover())
    parsed = [c.parse(it) for it in items]
    parsed = [p for p in parsed if p is not None]
    assert any("+5VALW" in p.text for p in parsed)
    assert any("C45" in p.text for p in parsed)


def test_parse_preserves_author_and_date(tmp_app):
    _setup_source()
    f = FakeFetcher()
    c = EB.EEvblogConnector(database.get_source("eevblog"), fetcher=f)
    items = list(c.discover())
    parsed = [c.parse(it) for it in items]
    parsed = [p for p in parsed if p is not None]
    assert any(p.author == "repair_tech" for p in parsed)
    assert any("2024-05-01" in p.published_at for p in parsed)


def test_parse_thread_id_from_url(tmp_app):
    tid = EB._thread_id_from_url("https://www.eevblog.com/forum/repair/topic,12345.0.html")
    assert tid == "12345"


def test_parse_strip_tags(tmp_app):
    assert EB._strip_tags("<p>hello <b>world</b></p>") == "hello world"
