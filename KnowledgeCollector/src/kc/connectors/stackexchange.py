"""Configurable Stack Exchange network connector.

Uses the sanctioned public API, not page scraping. One API request can fetch
answers for up to 100 question IDs, which avoids the old one-request-per-question
bottleneck. Per-source cursor records the tag-query segment and page so a stop
or quota pause does not restart the entire crawl.

Set STACKEXCHANGE_KEY in the environment to raise the default shared-IP quota
(300/day without a key; 10,000/day with a valid app key, subject to provider
throttles). The connector respects quota_remaining, has_more, and backoff.
"""
from __future__ import annotations

import html
import json
import os
import re
import time
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlencode, urlparse

from .base import BaseConnector, RawItem, ParsedItem
from .. import database
from ..schemas import SourceKind

API_BASE = "https://api.stackexchange.com/2.3"
DEFAULT_TAG_QUERIES = [
    "motherboard;laptop;repair;troubleshooting;power-supply",
    "mosfet;voltage-regulator;dc-dc-converter;battery-charging;multimeter",
    "display;backlight;charger;amplifier;audio",
]


class StackExchangeConnector(BaseConnector):
    name = "stackexchange_electronics"
    kind = SourceKind.STACKEXCHANGE_API
    base_url = "https://electronics.stackexchange.com"

    def __init__(self, source_config=None, fetcher=None):
        super().__init__(source_config, fetcher)
        self.site = (urlparse(self.source_config.get("base_url", self.base_url)).hostname or
                     "electronics.stackexchange.com").split(".")[0]
        try:
            self.settings = json.loads(self.source_config.get("settings_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            self.settings = {}
        configured_queries = self.settings.get("tag_queries") or DEFAULT_TAG_QUERIES
        if isinstance(configured_queries, str):
            configured_queries = [configured_queries]
        # /questions?tagged=a;b is an AND constraint. Treat catalog groups
        # as OR buckets by issuing one single-tag query per tag, then let the
        # collector deduplicate overlap between those independent result sets.
        self.tag_queries = []
        seen_tags = set()
        for group in configured_queries:
            for tag in str(group).split(";"):
                tag = tag.strip().strip("[]")
                if tag and tag not in seen_tags:
                    seen_tags.add(tag)
                    self.tag_queries.append(tag)
        self.api_key = os.environ.get("STACKEXCHANGE_KEY", "").strip()
        self.quota_remaining: Optional[int] = None
        self._stopped_for_quota = False

    def _api(self, path: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.fetcher:
            return None
        payload = dict(params)
        if self.api_key:
            payload["key"] = self.api_key
        url = f"{API_BASE}{path}?{urlencode(payload, doseq=True)}"
        data = self.fetch_json(url)
        if data is None:
            return None
        quota = data.get("quota_remaining")
        if quota is not None:
            self.quota_remaining = int(quota)
            # Leave a margin for an in-flight page/batch and avoid exhausting
            # the shared IP quota for other local Stack Exchange applications.
            if self.quota_remaining <= 1:
                self._stopped_for_quota = True
        backoff = data.get("backoff")
        if backoff:
            time.sleep(min(max(int(backoff), 1), 300))
        return data

    def _cursor(self) -> Dict[str, int]:
        raw = database.get_crawl_state(self.name).get("last_url") or ""
        try:
            d = json.loads(raw)
            if d.get("connector") == "stackexchange":
                return {"query_index": max(0, int(d.get("query_index", 0))),
                        "page": max(1, int(d.get("page", 1)))}
        except (ValueError, TypeError, json.JSONDecodeError):
            pass
        # Compatibility with v1 database: last_page represented the page of
        # the single electronics query. Re-read that page once to avoid gaps.
        state = database.get_crawl_state(self.name)
        return {"query_index": 0, "page": max(1, int(state.get("last_page", 0) or 1))}

    def discover(self) -> Iterator[RawItem]:
        state = self._cursor()
        query_index = state["query_index"]
        start_page = state["page"]
        total = 0
        seen_questions = set()
        seen_answers = set()
        max_records = self.max_records()
        # Anonymous access is hard-capped at page 25. A registered request key
        # lifts that specific cap; catalog depth is then the configured bound.
        provider_cap = 100 if self.api_key else 25
        page_cap = min(max(self.max_depth(), 1), provider_cap)
        for qi in range(query_index, len(self.tag_queries)):
            page = start_page if qi == query_index else 1
            tag_query = str(self.tag_queries[qi]).strip()
            if not tag_query:
                continue
            while page <= page_cap and total < max_records:
                if self._stopped_for_quota:
                    database.update_crawl_state(
                        self.name, status="paused",
                        error=f"Stack Exchange quota nearly exhausted (remaining={self.quota_remaining}); resume after quota reset or configure STACKEXCHANGE_KEY.")
                    return
                params = {
                    "page": page, "pagesize": 100, "order": "desc",
                    "sort": "activity", "tagged": tag_query,
                    "site": self.site, "filter": "withbody",
                }
                data = self._api("/questions", params)
                if not data:
                    detail = self.last_fetch_error or "no fetch diagnostic available"
                    database.update_crawl_state(
                        self.name, status="failed",
                        error=(f"Stack Exchange API fetch failed: site={self.site}, "
                               f"query={qi}, page={page}; {detail}"))
                    return
                questions = data.get("items", [])
                if not questions:
                    break

                # Batch-answer lookup in chunks of <=100 IDs; answers are then
                # yielded next to their question in deterministic thread order.
                answers_by_q: Dict[str, List[Dict[str, Any]]] = {}
                # Tag searches overlap heavily. Avoid refetching/re-emitting
                # the same question and answers across independent OR queries.
                qmap = {str(q.get("question_id")): q for q in questions
                        if str(q.get("question_id")) not in seen_questions}
                qids = list(qmap)
                for offset in range(0, len(qids), 100):
                    chunk = qids[offset:offset + 100]
                    ans_page = 1
                    while True:
                        if self._stopped_for_quota:
                            database.update_crawl_state(
                                self.name, status="paused",
                                error=f"Stack Exchange quota nearly exhausted (remaining={self.quota_remaining}); page is checkpointed for safe replay.")
                            return
                        ans = self._api(f"/questions/{';'.join(chunk)}/answers", {
                            "page": ans_page, "pagesize": 100, "order": "desc",
                            "sort": "activity", "site": self.site, "filter": "withbody",
                        })
                        if not ans:
                            # Keep the question records, but stop before advancing
                            # the checkpoint so answer retrieval can be retried.
                            detail = self.last_fetch_error or "no fetch diagnostic available"
                            database.update_crawl_state(
                                self.name, status="failed",
                                error=(f"Answer batch fetch failed for site={self.site}, "
                                       f"page={page}; {detail}"))
                            return
                        for a in ans.get("items", []):
                            aid = str(a.get("answer_id", ""))
                            if aid and aid in seen_answers:
                                continue
                            if aid:
                                seen_answers.add(aid)
                            answers_by_q.setdefault(str(a.get("question_id")), []).append(a)
                        if not ans.get("has_more") or ans_page >= 25:
                            break
                        ans_page += 1

                for q in questions:
                    if total >= max_records:
                        break
                    qid = str(q.get("question_id", ""))
                    if qid in seen_questions:
                        continue
                    seen_questions.add(qid)
                    qurl = q.get("link", "")
                    yield RawItem(url=qurl, thread_id=qid, post_id=qid,
                                  raw=json.dumps(q),
                                  meta={"kind": "question", "site": self.site,
                                        "page": page, "tag_query": tag_query})
                    total += 1
                    for a in answers_by_q.get(qid, []):
                        if total >= max_records:
                            break
                        aid = str(a.get("answer_id", ""))
                        aurl = qurl.rstrip("/") + f"#answer-{aid}"
                        yield RawItem(url=aurl, thread_id=qid, post_id=aid,
                                      raw=json.dumps(a),
                                      meta={"kind": "answer", "site": self.site,
                                            "page": page, "question_title": q.get("title", "")})
                        total += 1

                # Checkpoint only after the whole question page and answer
                # batches have been handled. Replaying a page is idempotent.
                next_query, next_page = qi, page + 1
                if not data.get("has_more") or page >= page_cap:
                    next_query, next_page = qi + 1, 1
                cursor = json.dumps({"connector": "stackexchange",
                                     "query_index": next_query,
                                     "page": next_page})
                database.update_crawl_state(self.name, last_page=page,
                                            last_url=cursor, status="running")
                if not data.get("has_more") or page >= page_cap:
                    break
                page += 1
            start_page = 1
            if total >= max_records:
                database.update_crawl_state(self.name, status="paused",
                                            error=f"per-source record cap reached ({max_records}); resume to continue")
                return
        database.update_crawl_state(self.name, status="done", error="")

    def parse(self, raw: RawItem) -> Optional[ParsedItem]:
        try:
            data = json.loads(raw.raw) if isinstance(raw.raw, str) else raw.raw
        except (json.JSONDecodeError, TypeError):
            return None
        kind = raw.meta.get("kind", "question")
        title = _strip_html(data.get("title", "")) if kind == "question" else raw.meta.get("question_title", "")
        body = _strip_html(data.get("body", ""))
        if not body:
            return None
        owner = data.get("owner") or {}
        return ParsedItem(
            url=raw.url, thread_id=raw.thread_id, post_id=raw.post_id,
            title=title, author=owner.get("display_name", ""),
            published_at=_ts_to_iso(data.get("creation_date")), text=body,
            meta={"kind": kind, "site": raw.meta.get("site", self.site),
                  "tags": data.get("tags", []), "score": data.get("score", 0),
                  "is_accepted": data.get("is_accepted", False),
                  "answer_count": data.get("answer_count", 0)},
        )


def _strip_html(s: str) -> str:
    if not s:
        return ""
    t = re.sub(r"<[^>]+>", " ", str(s))
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def _ts_to_iso(ts: Optional[int]) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts)))
