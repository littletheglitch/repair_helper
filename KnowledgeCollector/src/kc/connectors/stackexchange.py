"""Electronics Stack Exchange connector.

Uses the public Stack Exchange API (https://api.stackexchange.com/2.3)
which is the sanctioned access method for the network. No scraping, no
auth bypass. The API is rate-limited by Stack Exchange itself; we add
our own per-source rate limit on top.

Tags used to find repair-relevant questions:
  motherboard, laptop, power-supply, voltage, mosfet, voltage-regulator,
  pwm, dc-dc-converter, battery-charging, repair, troubleshooting.

Resume: the connector stores the last page processed in crawl_state and
resumes from page+1.

Provenance: questions and answers are CC-BY-SA 4.0 (the SE network's
default license); we record that in the source manifest.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional

from .base import BaseConnector, RawItem, ParsedItem
from .. import database, config
from ..schemas import SourceKind


API_BASE = "https://api.stackexchange.com/2.3"

# Tags likely to yield motherboard/repair-relevant content.
DEFAULT_TAGS = [
    "motherboard", "laptop", "power-supply", "voltage", "mosfet",
    "voltage-regulator", "pwm", "dc-dc-converter", "battery-charging",
    "repair", "troubleshooting", "multimeter",
]


class StackExchangeConnector(BaseConnector):
    name = "stackexchange_electronics"
    kind = SourceKind.STACKEXCHANGE_API
    base_url = "https://electronics.stackexchange.com"

    def discover(self) -> Iterator[RawItem]:
        """Iterate over questions tagged with our repair-relevant tags.
        Yields one RawItem per question (with its answers inline in meta)
        so the orchestrator can store both the question and the answers
        as separate records."""
        # Resume: re-start from the last page processed (a failure may
        # have been mid-page, so we don't skip it).
        state = database.get_crawl_state(self.name)
        last_page = int(state.get("last_page", 0) or 0)
        page = max(1, last_page)
        max_pages = self.max_depth()
        max_records = self.max_records()
        count = 0
        for p in range(page, page + max_pages):
            if count >= max_records:
                break
            url = (f"{API_BASE}/questions?page={p}&pagesize=30&order=desc&"
                   f"sort=activity&tagged={';'.join(DEFAULT_TAGS)}&"
                   f"site=electronics&filter=withbody")
            data = self.fetch_json(url)
            if not data:
                # Log and stop; resume will retry this page next run.
                database.update_crawl_state(self.name, status="failed",
                                            error="API fetch returned no data")
                return
            items = data.get("items", [])
            if not items:
                # No more pages.
                break
            for q in items:
                if count >= max_records:
                    break
                # Yield the question itself.
                yield RawItem(
                    url=q.get("link", ""),
                    thread_id=str(q.get("question_id", "")),
                    post_id=str(q.get("question_id", "")),
                    raw=json.dumps(q),
                    meta={"kind": "question", "site": "electronics",
                          "page": p},
                )
                count += 1
                # Fetch answers for this question (one API call per
                # question, rate-limited).
                answers = self._fetch_answers(q.get("question_id"))
                for a in answers:
                    if count >= max_records:
                        break
                    # Answers may share the question's link in the API; we
                    # synthesize a distinct URL by appending the answer id so
                    # the (source, url) UNIQUE constraint doesn't replace the
                    # question's record.
                    a_url = a.get("link") or q.get("link", "")
                    if a_url and "#answer-" not in a_url:
                        a_url = a_url.rstrip("/") + f"#answer-{a.get('answer_id','')}"
                    yield RawItem(
                        url=a_url,
                        thread_id=str(q.get("question_id", "")),
                        post_id=str(a.get("answer_id", "")),
                        raw=json.dumps(a),
                        meta={"kind": "answer", "site": "electronics",
                              "page": p,
                              "question_title": q.get("title", "")},
                    )
                    count += 1
            database.update_crawl_state(self.name, last_page=p,
                                        status="running")
        database.update_crawl_state(self.name, status="done")

    def _fetch_answers(self, question_id: Any) -> List[Dict[str, Any]]:
        if not question_id or not self.fetcher:
            return []
        url = (f"{API_BASE}/questions/{question_id}/answers?order=desc&"
               f"sort=activity&site=electronics&filter=withbody")
        data = self.fetch_json(url)
        return data.get("items", []) if data else []

    def parse(self, raw: RawItem) -> Optional[ParsedItem]:
        try:
            data = json.loads(raw.raw) if isinstance(raw.raw, str) else raw.raw
        except (json.JSONDecodeError, TypeError):
            return None
        kind = raw.meta.get("kind", "question")
        title = ""
        body = ""
        author = ""
        published_at = ""
        if kind == "question":
            title = _strip_html(data.get("title", ""))
            body = _strip_html(data.get("body", ""))
            owner = data.get("owner", {})
            author = owner.get("display_name", "")
            published_at = _ts_to_iso(data.get("creation_date"))
        else:  # answer
            title = raw.meta.get("question_title", "")
            body = _strip_html(data.get("body", ""))
            owner = data.get("owner", {})
            author = owner.get("display_name", "")
            published_at = _ts_to_iso(data.get("creation_date"))
        if not body:
            return None
        return ParsedItem(
            url=raw.url, thread_id=raw.thread_id, post_id=raw.post_id,
            title=title, author=author, published_at=published_at,
            text=body, meta={"kind": kind, "site": "electronics",
                              "tags": data.get("tags", []),
                              "score": data.get("score", 0),
                              "is_accepted": data.get("is_accepted", False)},
        )


def _strip_html(s: str) -> str:
    """Crude HTML→text: drop tags, unescape entities."""
    import html
    import re
    if not s:
        return ""
    t = re.sub(r"<[^>]+>", "", s)
    t = html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _ts_to_iso(ts: Optional[int]) -> str:
    if not ts:
        return ""
    import time
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts)))
