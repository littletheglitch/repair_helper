"""Collector orchestrator.

Drives a connector's discover() iterator, runs each parsed item through
the BIOS guard, filter, extractor, classifier, and dedup pipeline, and
stores the result. Handles pause/resume via crawl_state.

The orchestrator is the bridge between the connector layer (per-source
parsing) and the storage layer (records/documents/rejections).
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterator, Optional

from . import (config, database, schemas, filtering, extraction, classifier,
               dedup, bios_guard, manifest)
from .connectors import (stackexchange, eevblog, badcaps, laptopforum)
from .schemas import (Record, Rejection, RejectionReason, SourceKind,
                      CLASS_TO_ASSISTANT_TIER)


# Map source name -> connector class.
CONNECTORS = {
    "stackexchange_electronics": stackexchange.StackExchangeConnector,
    "eevblog": eevblog.EEvblogConnector,
    "badcaps": badcaps.BadcapsConnector,
    "laptopforum": laptopforum.LaptopForumConnector,
}


def collect(source_name: str, fetcher, max_records: Optional[int] = None,
            pause_after: Optional[int] = None) -> Dict[str, Any]:
    """Collect from one source. Returns a summary dict.

    The orchestrator:
    1. Loads the source config + connector.
    2. Iterates the connector's discover() (which already resumes from
       crawl_state).
    3. For each raw item, parses it, runs the BIOS guard (if it has a
       filename), the relevance filter, the extractor, the classifier,
       and the dedup. Stores the record or rejection.
    4. Updates crawl_state after each item so resume works.
    """
    src = database.get_source(source_name)
    if not src:
        return {"ok": False, "error": f"unknown source '{source_name}'"}
    if not src["enabled"]:
        return {"ok": False, "error": f"source '{source_name}' is disabled"}
    cls = CONNECTORS.get(source_name)
    if cls is None:
        return {"ok": False, "error": f"no connector registered for '{source_name}'"}

    # Write the default manifest for this source on first collect.
    if not database.get_manifest(source_name):
        manifest.write_default_manifests()

    connector = cls(dict(src), fetcher=fetcher)
    database.update_crawl_state(source_name, status="running", error="")

    cap = max_records or int(src.get("max_records", 5000))
    collected = 0
    rejected = 0
    paused = False
    try:
        for raw in connector.discover():
            if collected >= cap:
                break
            if pause_after is not None and collected >= pause_after:
                database.update_crawl_state(source_name, status="paused")
                paused = True
                break
            parsed = connector.parse(raw)
            if parsed is None:
                rejected += 1
                database.store_rejection(Rejection(
                    source_name=source_name, url=raw.url,
                    reason=RejectionReason.RELEVANCE,
                    detail="parse returned None", collected_at=config.now_iso()))
                continue
            # Relevance filter.
            fr = filtering.is_relevant(parsed.title, parsed.text)
            if not fr.relevant:
                rejected += 1
                database.store_rejection(filtering.reject_unrelevant(
                    source_name, parsed.url, fr.reason))
                continue
            # Extraction.
            sf = extraction.extract(parsed.text, parsed.title)
            # Classification.
            cls_obj = classifier.classify(parsed.text, sf)
            # Build the Record (computes content_hash + normalized_hash).
            rec = Record(
                source_name=source_name, url=parsed.url,
                thread_id=parsed.thread_id, post_id=parsed.post_id,
                title=parsed.title, author=parsed.author,
                published_at=parsed.published_at,
                collected_at=config.now_iso(), text=parsed.text,
                structured=sf, classification=cls_obj,
            )
            # Dedup: if this exact (source, url) already exists, it's a
            # re-crawl — update it in place. Count it as collected (the
            # resume successfully re-fetched it).
            existing_same_url = database.query_one(
                "SELECT id FROM records WHERE source_name=? AND url=?",
                (source_name, parsed.url))
            if existing_same_url:
                database.store_record(rec)
                collected += 1
                continue
            # Exact (cross-source) duplicate.
            exact_dup = database.find_duplicate(rec.content_hash,
                                                 rec.normalized_hash,
                                                 source_name)
            if exact_dup:
                rec.status = "duplicate"
                rec.rejection_reason = (
                    f"exact content_hash match in source "
                    f"{exact_dup['source_name']}")
                # Add cross-source ref to the original.
                database.add_cross_source_ref(exact_dup["id"], parsed.url)
                database.store_record(rec)
                rejected += 1
                continue
            # Near-duplicate (same-source first, then cross-source).
            near = database.find_near_duplicate(parsed.text, source_name)
            if near:
                rec.status = "near_duplicate"
                rec.rejection_reason = f"near-duplicate of record {near['id']}"
                # Preserve the variant text on the original.
                database.add_variant(near["id"], parsed.text)
                database.store_record(rec)
                rejected += 1
                continue
            # New record.
            rec.status = "new"
            database.store_record(rec)
            collected += 1
            database.update_crawl_state(source_name, last_url=parsed.url,
                                        items_collected=collected,
                                        items_rejected=rejected)
    except Exception as e:
        database.update_crawl_state(source_name, status="failed", error=str(e))
        return {"ok": False, "error": str(e),
                "collected": collected, "rejected": rejected}
    if not paused:
        database.update_crawl_state(source_name, status="done", error="")
    return {"ok": True, "collected": collected, "rejected": rejected,
            "paused": paused}


def collect_documents(source_name: str, fetcher, urls: list,
                      enable_ocr: bool = False) -> Dict[str, Any]:
    """Collect documents (PDF/HTML/text) from a list of URLs. Runs the
    BIOS guard before download; rejects BIOS dumps."""
    from . import documents as D
    import tempfile
    collected = 0
    rejected = 0
    for url in urls:
        # Pre-check the URL filename for BIOS.
        name = url.rsplit("/", 1)[-1].split("?")[0]
        is_bios, reason = bios_guard.looks_like_bios(name)
        if is_bios:
            database.store_rejection(bios_guard.reject_bios(
                source_name, url, name))
            rejected += 1
            continue
        with tempfile.TemporaryDirectory() as tmp:
            dest = __import__("pathlib").Path(tmp) / name
            r = fetcher.download(url, dest,
                                 rate_per_min=int(database.get_source(source_name).get("rate_per_min", 6)))
            if not r.ok:
                database.store_rejection(Rejection(
                    source_name=source_name, url=url,
                    reason=RejectionReason.ERROR, detail=r.error,
                    collected_at=config.now_iso()))
                rejected += 1
                continue
            # Post-download BIOS check (magic bytes).
            try:
                bios_guard.assert_not_bios(dest)
            except ValueError as e:
                database.store_rejection(Rejection(
                    source_name=source_name, url=url,
                    reason=RejectionReason.BIOS, detail=str(e),
                    collected_at=config.now_iso()))
                rejected += 1
                continue
            doc = D.process_file(dest, source_name, url, enable_ocr=enable_ocr)
            if doc.status == "rejected":
                database.store_rejection(Rejection(
                    source_name=source_name, url=url,
                    reason=RejectionReason.BIOS, detail=doc.meta.get("reason",""),
                    collected_at=config.now_iso()))
                rejected += 1
                continue
            database.store_document(doc)
            collected += 1
    return {"ok": True, "collected": collected, "rejected": rejected}
