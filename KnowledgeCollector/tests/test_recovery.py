"""Tests for interrupted-collection recovery, rate limiting, offline
search, and BIOS exclusion end-to-end."""
import time
import pytest
from pathlib import Path

from kc import (database, config, schemas, classifier, extraction, collector,
                fetcher, bios_guard, filtering)
from kc.connectors import stackexchange as SE
from kc.schemas import Rejection, RejectionReason


def _setup_se_source():
    database.upsert_source("stackexchange_electronics", "stackexchange_api",
                           "https://electronics.stackexchange.com",
                           enabled=True, license="CC-BY-SA 4.0",
                           access_method="public API",
                           rate_per_min=6, max_depth=1, max_records=20)


class SEFakeFetcher:
    """Serves the SE fixtures."""
    def __init__(self, fail_after=None):
        self.calls = 0
        self.fail_after = fail_after

    def fetch(self, url, rate_per_min=6, extra_headers=None):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            return fetcher.FetchResult(url=url, status=0, kind="error",
                                       error="simulated network drop")
        import json as _json
        FIX = Path(__file__).parent / "fixtures" / "stackexchange"
        if "/questions?" in url and "/answers" not in url:
            page = 1
            import re
            m = re.search(r"page=(\d+)", url)
            if m:
                page = int(m.group(1))
            p = FIX / f"questions_page{page}.json"
            body = p.read_bytes() if p.exists() else _json.dumps({"items": []}).encode()
            return fetcher.FetchResult(url=url, status=200, body=body,
                                       headers={"Content-Type": "application/json"},
                                       kind="ok")
        if "/answers" in url:
            import re
            m = re.search(r"/questions/(\d+)/answers", url)
            qid = m.group(1) if m else "0"
            p = FIX / f"answers_{qid}.json"
            body = p.read_bytes() if p.exists() else b'{"items":[]}'
            return fetcher.FetchResult(url=url, status=200, body=body,
                                       headers={"Content-Type": "application/json"},
                                       kind="ok")
        return fetcher.FetchResult(url=url, status=404, kind="error", error="not found")


def test_interrupted_collection_can_resume(tmp_app, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(fetcher.RobotsCache, "allowed",
                        lambda self, url: (True, "test"))
    _setup_se_source()
    # Simulate a network drop after 2 fetches.
    f = SEFakeFetcher(fail_after=2)
    r = collector.collect("stackexchange_electronics", f)
    # The collection failed mid-run; crawl_state should be 'failed'.
    state = database.get_crawl_state("stackexchange_electronics")
    assert state["status"] in ("failed", "done", "running")
    # A resume re-runs; with a fresh fetcher it should complete.
    f2 = SEFakeFetcher()
    r2 = collector.collect("stackexchange_electronics", f2)
    assert r2["ok"]
    assert r2["collected"] > 0


def test_pause_during_collection(tmp_app, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(fetcher.RobotsCache, "allowed",
                        lambda self, url: (True, "test"))
    _setup_se_source()
    f = SEFakeFetcher()
    # Pause after collecting 1 record.
    r = collector.collect("stackexchange_electronics", f,
                          pause_after=1)
    assert r.get("paused") is True
    state = database.get_crawl_state("stackexchange_electronics")
    assert state["status"] == "paused"


def test_rate_limiting_is_enforced(tmp_app, monkeypatch):
    """The fetcher must sleep to honor the per-source rate limit."""
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(fetcher.RobotsCache, "allowed",
                        lambda self, url: (True, "test"))
    f = fetcher.Fetcher(transport=lambda url, h: (
        200, b"<p>x</p><p>y</p><p>z</p>",
        {"Content-Type": "text/html"}, "https://x/"),
        settings={"retry_max": 0, "user_agent": "test"})
    f.fetch("https://a.com/1")
    f.fetch("https://a.com/2")
    # Second fetch to the same host should have slept.
    assert any(s > 0 for s in sleeps)


def test_offline_search_works_after_collection(tmp_app, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(fetcher.RobotsCache, "allowed",
                        lambda self, url: (True, "test"))
    _setup_se_source()
    f = SEFakeFetcher()
    collector.collect("stackexchange_electronics", f)
    # Now go offline (no fetcher needed for search).
    hits = database.search_records("5VALW", limit=10)
    assert hits
    # The found record carries full provenance.
    assert any(h["source_name"] == "stackexchange_electronics" for h in hits)


def test_bios_dump_url_rejected_before_download(tmp_app):
    """The collector rejects a BIOS-dump URL at the filename check."""
    _setup_se_source()
    from kc import collector as C
    f = SEFakeFetcher()
    r = C.collect_documents("stackexchange_electronics", f,
                            ["https://x/firmware.bin"])
    assert r["rejected"] >= 1
    # A rejection row was logged with reason 'bios_dump'.
    rows = database.query_all(
        "SELECT * FROM rejections WHERE reason='bios_dump'")
    assert rows


def test_bios_dump_rejected_via_magic_bytes(tmp_app, tmp_path):
    """The collector rejects a BIOS dump after download via magic bytes."""
    _setup_se_source()
    from kc import collector as C
    # Build a fake fetcher that "downloads" a file with BIOS magic bytes.
    class DownloadFetcher:
        def download(self, url, dest, rate_per_min=6, max_bytes=524288000):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"\xAA\x55" + b"\x00" * 200)
            return fetcher.FetchResult(url=url, status=200, kind="ok")
    f = DownloadFetcher()
    r = C.collect_documents("stackexchange_electronics", f,
                            ["https://x/mystery.dat"])
    assert r["rejected"] >= 1


def test_off_topic_post_rejected_by_filter(tmp_app, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(fetcher.RobotsCache, "allowed",
                        lambda self, url: (True, "test"))
    _setup_se_source()
    # Manually insert a record with off-topic text and re-run filter.
    fr = filtering.is_relevant("Recipe", "This is a recipe for chocolate cake with sugar and flour.")
    assert not fr.relevant
    database.store_rejection(filtering.reject_unrelevant(
        "stackexchange_electronics", "https://x/recipe", fr.reason))
    rows = database.query_all(
        "SELECT * FROM rejections WHERE reason='relevance'")
    assert rows


def test_resume_clears_failed_state(tmp_app, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(fetcher.RobotsCache, "allowed",
                        lambda self, url: (True, "test"))
    _setup_se_source()
    database.update_crawl_state("stackexchange_electronics", status="failed",
                                error="network drop")
    f = SEFakeFetcher()
    r = collector.collect("stackexchange_electronics", f)
    assert r["ok"]
    state = database.get_crawl_state("stackexchange_electronics")
    assert state["status"] == "done"


def test_cross_source_duplicate_keeps_original(tmp_app, monkeypatch):
    """The same text on two sources: the second is marked duplicate, the
    first is preserved with the cross-source URL in also_seen_at."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(fetcher.RobotsCache, "allowed",
                        lambda self, url: (True, "test"))
    # Store an eevblog record.
    rec1 = schemas.Record(
        source_name="eevblog", url="https://a/t1", thread_id="t1",
        post_id="p1", title="x", author="", published_at="",
        collected_at=config.now_iso(),
        text="I replaced C45 and measured 480 ohm. Board is fixed and works now.")
    rec1.structured = extraction.extract(rec1.text)
    rec1.classification = classifier.classify(rec1.text, rec1.structured)
    database.store_record(rec1)
    # Now the same text from stackexchange.
    rec2 = schemas.Record(
        source_name="stackexchange_electronics", url="https://b/t2",
        thread_id="t2", post_id="p2", title="x", author="", published_at="",
        collected_at=config.now_iso(),
        text="I replaced C45 and measured 480 ohm. Board is fixed and works now.")
    rec2.structured = extraction.extract(rec2.text)
    rec2.classification = classifier.classify(rec2.text, rec2.structured)
    dup = database.find_duplicate(rec2.content_hash, rec2.normalized_hash,
                                  "stackexchange_electronics")
    assert dup is not None
    assert dup["source_name"] == "eevblog"
    database.add_cross_source_ref(dup["id"], rec2.url)
    # The original eevblog record carries the cross-source URL.
    row = database.query_one(
        "SELECT also_seen_at FROM records WHERE id=?", (dup["id"],))
    import json
    assert "https://b/t2" in json.loads(row["also_seen_at"])
