"""Generic laptop-repair forum connector.

A configurable HTML connector for publicly accessible laptop-repair
forums (e.g. a tech-support board). The user configures the base URL,
board URL pattern, and the HTML selectors via the source config's
``meta`` field (kept simple for the initial implementation).

This connector is intentionally conservative: it only follows links
that match a configured thread-URL pattern and only parses posts that
look like forum posts (a <div class="post"> or <blockquote>). If the
site uses JS rendering, the fetcher returns js_required and the
connector logs unsupported and skips.
"""
from __future__ import annotations

import re
from typing import Iterator, Optional

from .base import BaseConnector, RawItem, ParsedItem
from .. import database, config
from ..schemas import SourceKind
from .. import filtering
from .eevblog import _extract_posts, _extract_thread_links, _thread_id_from_url


class LaptopForumConnector(BaseConnector):
    name = "laptopforum"
    kind = SourceKind.FORUM_HTML
    base_url = ""   # set by the user

    def discover(self) -> Iterator[RawItem]:
        if not self.base_url:
            return
        max_pages = self.max_depth()
        max_records = self.max_records()
        count = 0
        for page in range(0, max_pages):
            if count >= max_records:
                return
            board_url = self.base_url.rstrip("/") + f"/page/{page}"
            html = self.fetch_html(board_url)
            if not html:
                return
            thread_links = _extract_thread_links(html, self.base_url)
            if not thread_links:
                return
            for thread_url, title in thread_links:
                if count >= max_records:
                    return
                # No title-only pre-filter; orchestrator filters posts.
                thread_html = self.fetch_html(thread_url)
                if not thread_html:
                    continue
                for post_text, author, date in _extract_posts(thread_html):
                    if count >= max_records:
                        return
                    yield RawItem(
                        url=thread_url,
                        thread_id=_thread_id_from_url(thread_url) or thread_url,
                        post_id=f"{_thread_id_from_url(thread_url) or thread_url}_{count}",
                        raw=post_text,
                        meta={"author": author, "date": date,
                              "title": title, "kind": "post"},
                    )
                    count += 1
            database.update_crawl_state(self.name, last_page=page,
                                        status="running")
        database.update_crawl_state(self.name, status="done")

    def parse(self, raw: RawItem) -> Optional[ParsedItem]:
        if not raw.raw or not isinstance(raw.raw, str):
            return None
        text = raw.raw.strip()
        if len(text) < 30:
            return None
        return ParsedItem(
            url=raw.url, thread_id=raw.thread_id, post_id=raw.post_id,
            title=raw.meta.get("title", ""),
            author=raw.meta.get("author", ""),
            published_at=raw.meta.get("date", ""),
            text=text, meta={"kind": "post"},
        )
