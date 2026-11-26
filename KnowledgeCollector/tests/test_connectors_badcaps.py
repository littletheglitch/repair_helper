"""Tests for the Badcaps connector — fixture-based, no network."""
from pathlib import Path

import pytest

from kc import database, config, schemas
from kc.connectors import badcaps as BC
from kc.connectors.base import RawItem

FIXTURES = Path(__file__).parent / "fixtures" / "badcaps"


class FakeFetcher:
    def __init__(self):
        self.calls = []

    def fetch(self, url, rate_per_min=6, extra_headers=None):
        self.calls.append(url)
        from kc import fetcher
        if "forumdisplay" in url:
            body = (FIXTURES / "board_page.html").read_bytes()
            return fetcher.FetchResult(url=url, status=200, body=body,
                                       headers={"Content-Type": "text/html"},
                                       kind="ok")
        if "showthread.php/67890" in url:
            body = (FIXTURES / "thread_67890.html").read_bytes()
            return fetcher.FetchResult(url=url, status=200, body=body,
                                       headers={"Content-Type": "text/html"},
                                       kind="ok")
        return fetcher.FetchResult(url=url, status=404, kind="error", error="not found")


def _setup_source():
    database.upsert_source("badcaps", "forum_html",
                           "https://www.badcaps.net/forum/",
                           enabled=True, license="forum ToS",
                           access_method="HTML (robots-respecting)",
                           rate_per_min=6, max_depth=1, max_records=20)


def test_connector_name(tmp_app):
    c = BC.BadcapsConnector(database.get_source("badcaps"))
    assert c.name == "badcaps"


def test_discover_yields_relevant_posts(tmp_app):
    _setup_source()
    f = FakeFetcher()
    c = BC.BadcapsConnector(database.get_source("badcaps"), fetcher=f)
    items = list(c.discover())
    # One relevant thread with one post (the recipe thread is filtered out).
    assert len(items) >= 1
    assert any("AO4407" in it.raw for it in items)


def test_parse_preserves_post_text(tmp_app):
    _setup_source()
    f = FakeFetcher()
    c = BC.BadcapsConnector(database.get_source("badcaps"), fetcher=f)
    items = list(c.discover())
    parsed = [c.parse(it) for it in items]
    parsed = [p for p in parsed if p is not None]
    assert any("MOSFET" in p.text for p in parsed)
    assert any("19V" in p.text for p in parsed)


def test_parse_thread_id_from_url():
    tid = BC._vb_thread_id_from_url("https://www.badcaps.net/forum/showthread.php/67890-lenovo-no-power")
    assert tid == "67890"


def test_recipe_thread_filtered_out(tmp_app):
    _setup_source()
    f = FakeFetcher()
    c = BC.BadcapsConnector(database.get_source("badcaps"), fetcher=f)
    items = list(c.discover())
    # The recipe thread must not appear in the items.
    for it in items:
        parsed = c.parse(it)
        assert parsed is None or "recipe" not in parsed.text.lower()
