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

from .base import (BaseConnector, RawItem, ParsedItem, stable_post_id,
                    legacy_start_page, legacy_checkpoint)
from .. import database, config
from ..schemas import SourceKind
from .. import filtering
from .eevblog import _extract_posts, _extract_thread_links, _thread_id_from_url


class LaptopForumConnector(BaseConnector):
    name = "laptopforum"
    kind = SourceKind.FORUM_HTML
    base_url = ""   # set by the user

    def discover(self) -> Iterator[RawItem]:
        self.base_url = str(self.source_config.get("base_url") or self.base_url).strip()
        if not self.base_url:
            database.update_crawl_state(self.name, status="failed",
                                        error="Laptop forum base_url is not configured")
            return
        max_pages = self.max_depth()
        max_records = self.max_records()
        count = 0
        start_page = legacy_start_page(self.name, "laptopforum", 0)
        for page in range(start_page, start_page + max_pages):
            if count >= max_records:
                return
            board_url = self.base_url.rstrip("/") + f"/page/{page}"
            html = self.fetch_html(board_url)
            if not html:
                database.update_crawl_state(self.name, status="failed",
                                            error=self.fetch_failure("Laptop forum board fetch failed", board_url))
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
                    database.update_crawl_state(self.name, status="failed",
                                                error=self.fetch_failure("Laptop forum thread fetch failed", thread_url))
                    return
                for post_text, author, date in _extract_posts(thread_html):
                    if count >= max_records:
                        return
                    tid = _thread_id_from_url(thread_url) or thread_url
                    pid = stable_post_id(tid, author, date, post_text)
                    yield RawItem(
                        url=f"{thread_url}#kc-post-{pid}",
                        thread_id=tid,
                        post_id=pid,
                        raw=post_text,
                        meta={"author": author, "date": date,
                              "title": title, "kind": "post"},
                    )
                    count += 1
            legacy_checkpoint(self.name, "laptopforum", page, page + 1)
        database.update_crawl_state(self.name, status="paused",
                                    error="configured board pagination depth reached; resume to continue")

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
