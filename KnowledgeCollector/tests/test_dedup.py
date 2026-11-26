"""Tests for the deduplication module."""
from kc import dedup as D
from kc import schemas, database, config


def test_shingles_basic():
    s = D.shingles("the quick brown fox", 3)
    assert "the quick brown" in s
    assert "quick brown fox" in s


def test_shingles_short_text():
    s = D.shingles("hi", 3)
    assert s == {"hi"}


def test_jaccard_identical():
    assert D.jaccard_3shingle("the quick brown fox", "the quick brown fox") == 1.0


def test_jaccard_disjoint():
    assert D.jaccard_3shingle("alpha beta gamma", "one two three") == 0.0


def test_jaccard_partial_overlap():
    j = D.jaccard_3shingle("the quick brown fox jumps",
                          "the quick brown fox runs")
    assert 0.4 <= j <= 1.0


def test_near_duplicate_threshold():
    # Same text, minor word change.
    a = "I replaced C45 and the board powered on after the fix."
    b = "I replaced C45 and the board powered on after the repair."
    assert D.near_duplicate(a, b, threshold=0.5)


def test_near_duplicate_distinct():
    assert not D.near_duplicate("alpha beta gamma delta",
                                 "one two three four", threshold=0.5)


def test_dedup_decision_new():
    status, reason = D.dedup_decision("some text", "hash1", "nhash1")
    assert status == "new"
    assert reason == ""


def test_dedup_decision_exact():
    existing = {"source_name": "eevblog", "text": "some text"}
    status, reason = D.dedup_decision("some text", "h", "nh",
                                       existing_exact=existing)
    assert status == "duplicate"
    assert "eevblog" in reason


def test_dedup_decision_near():
    existing = {"source_name": "eevblog",
                "text": "I replaced C45 and the board powered on after the fix."}
    new_text = "I replaced C45 and the board powered on after the repair."
    status, reason = D.dedup_decision(new_text, "h", "nh",
                                       existing_near=existing, threshold=0.5)
    assert status == "near_duplicate"
    assert "jaccard" in reason


def test_cross_source_duplicate_preserves_original(tmp_app):
    """The dedup must never delete the original; cross-source dups add
    to the also_seen_at list."""
    rec1 = schemas.Record(
        source_name="eevblog", url="https://a/t1", thread_id="t1",
        post_id="p1", title="", author="", published_at="",
        collected_at=config.now_iso(), text="identical text")
    database.store_record(rec1)
    rec2 = schemas.Record(
        source_name="stackexchange", url="https://b/t2", thread_id="t2",
        post_id="p2", title="", author="", published_at="",
        collected_at=config.now_iso(), text="identical text")
    # The DB layer should detect the cross-source duplicate.
    dup = database.find_duplicate(rec2.content_hash, rec2.normalized_hash,
                                  "stackexchange")
    assert dup is not None
    assert dup["source_name"] == "eevblog"


def test_unique_measurements_preserved_in_near_dup(tmp_app):
    """A near-duplicate with a UNIQUE measurement must not be deleted;
    it's preserved as a variant."""
    a_text = "I replaced C45 and measured 480 ohm. Board fixed."
    b_text = "I replaced C45 and measured 120 ohm. Board fixed."  # different measurement
    rec1 = schemas.Record(
        source_name="eevblog", url="https://a/t1", thread_id="t1",
        post_id="p1", title="", author="", published_at="",
        collected_at=config.now_iso(), text=a_text)
    rid = database.store_record(rec1)
    # Add the near-dup as a variant.
    database.add_variant(rid, b_text)
    row = database.query_one("SELECT variants FROM records WHERE id=?", (rid,))
    import json
    variants = json.loads(row["variants"] or "[]")
    assert b_text in variants
