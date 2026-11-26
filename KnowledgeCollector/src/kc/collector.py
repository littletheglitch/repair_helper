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
from .connectors import (stackexchange, eevblog, badcaps, laptopforum, forums)
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
        kind = src.get("kind")
        settings = {}
        try:
            import json
            settings = json.loads(src.get("settings_json") or "{}")
        except (TypeError, ValueError):
            settings = {}
        if kind == "stackexchange_api":
            cls = stackexchange.StackExchangeConnector
        elif kind == "discourse_api":
            cls = forums.DiscourseConnector
        elif kind == "forum_html":
            platform = settings.get("platform")
            cls = {"smf": forums.SMFConnector,
                   "xenforo": forums.XenForoConnector}.get(platform)
    if cls is None:
        return {"ok": False, "error": f"no connector registered for '{source_name}'"}

    # Write the default manifest for this source on first collect.
    if not database.get_manifest(source_name):
        manifest.write_default_manifests()

    connector = cls(dict(src), fetcher=fetcher)
    initial_state = database.get_crawl_state(source_name)
    base_collected = int(initial_state.get("items_collected", 0))
    base_rejected = int(initial_state.get("items_rejected", 0))
    database.update_crawl_state(source_name, status="running", error="")

    cap = max_records or int(src.get("max_records", 5000))
    collected = 0
    rejected = 0
    paused = False
    limit_reached = False

    def persist_counts() -> None:
        database.update_crawl_state(
            source_name,
            items_collected=base_collected + collected,
            items_rejected=base_rejected + rejected)

    try:
        for raw in connector.discover():
            if collected >= cap:
                limit_reached = True
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
                persist_counts()
                continue
            # Relevance filter.
            fr = filtering.is_relevant(parsed.title, parsed.text)
            if not fr.relevant:
                rejected += 1
                database.store_rejection(filtering.reject_unrelevant(
                    source_name, parsed.url, fr.reason))
                persist_counts()
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
            if parsed.thread_id and parsed.post_id:
                existing_same_url = database.query_one(
                    "SELECT id FROM records WHERE source_name=? AND thread_id=? AND post_id=? LIMIT 1",
                    (source_name, parsed.thread_id, parsed.post_id))
            else:
                existing_same_url = database.query_one(
                    "SELECT id FROM records WHERE source_name=? AND url=? LIMIT 1",
                    (source_name, parsed.url))
            if existing_same_url:
                database.store_record(rec)
                collected += 1
                persist_counts()
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
                persist_counts()
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
                persist_counts()
                continue
            # New record.
            rec.status = "new"
            database.store_record(rec)
            collected += 1
            # last_url is a connector-owned resume cursor (JSON for paged/API
            # connectors). Do not replace it with the most recently seen post URL.
            database.update_crawl_state(source_name,
                                        items_collected=base_collected + collected,
                                        items_rejected=base_rejected + rejected)
    except Exception as e:
        database.update_crawl_state(source_name, status="failed", error=str(e),
                                    items_collected=base_collected + collected,
                                    items_rejected=base_rejected + rejected)
        return {"ok": False, "error": str(e),
                "collected": collected, "rejected": rejected}
    # Never overwrite a connector's explicit quota pause or error with "done".
    current = database.get_crawl_state(source_name)
    if paused or limit_reached:
        database.update_crawl_state(source_name, status="paused",
                                    error=("per-run record cap reached; resume to continue" if limit_reached else ""),
                                    items_collected=base_collected + collected,
                                    items_rejected=base_rejected + rejected)
    elif current.get("status") == "running":
        database.update_crawl_state(source_name, status="done", error="",
                                    items_collected=base_collected + collected,
                                    items_rejected=base_rejected + rejected)
    else:
        database.update_crawl_state(source_name,
                                    items_collected=base_collected + collected,
                                    items_rejected=base_rejected + rejected)
    final_state = database.get_crawl_state(source_name)
    final_status = final_state.get("status")
    final_error = final_state.get("error", "")
    # A connector can terminate its generator after recording a source-level
    # failure. Returning ok=True here made manual collection report success
    # even though the source state was failed.
    return {"ok": final_status != "failed",
            "collected": collected, "rejected": rejected,
            "paused": paused or limit_reached or final_status == "paused",
            "state": final_status, "error": final_error}


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
