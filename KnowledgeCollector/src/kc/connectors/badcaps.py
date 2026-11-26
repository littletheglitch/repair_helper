"""Badcaps Forum connector.

Badcaps (https://www.badcaps.net/forum/) is a public electronics-repair
forum. It runs on vBulletin (different markup than SMF-based EEVblog).
This connector:

- Respects robots.txt (via the fetcher).
- Walks the repair-relevant sub-forums.
- Parses vBulletin thread pages for posts.
- Does NOT bypass auth, paywalls, CAPTCHAs or JS rendering.

Fixture-tested; no real network during the test suite.
"""
from __future__ import annotations

import re
from typing import Iterator, Optional

from .base import (BaseConnector, RawItem, ParsedItem, stable_post_id,
                    legacy_start_page, legacy_checkpoint)
from .. import database, config
from ..schemas import SourceKind
from .. import filtering


class BadcapsConnector(BaseConnector):
    name = "badcaps"
    kind = SourceKind.FORUM_HTML
    base_url = "https://www.badcaps.net/forum/"

    REPAIR_BOARD_URLS = [
        "https://www.badcaps.net/forum/forumdisplay.php?f=10",  # motherboard repair
    ]

    def discover(self) -> Iterator[RawItem]:
        max_pages = self.max_depth()
        max_records = self.max_records()
        count = 0
        start_page = legacy_start_page(self.name, "badcaps", 1)
        for board_url in self.REPAIR_BOARD_URLS:
            for page in range(start_page, start_page + max_pages):
                if count >= max_records:
                    return
                url = f"{board_url}&page={page}" if "?" in board_url else f"{board_url}?page={page}"
                html = self.fetch_html(url)
                if not html:
                    database.update_crawl_state(self.name, status="failed",
                                                error=self.fetch_failure("Badcaps board fetch failed", url))
                    return
                thread_links = _extract_vb_thread_links(html)
                if not thread_links:
                    return
                for thread_url, title in thread_links:
                    if count >= max_records:
                        return
                    # No title-only pre-filter; orchestrator filters posts.
                    thread_html = self.fetch_html(thread_url)
                    if not thread_html:
                        database.update_crawl_state(self.name, status="failed",
                                                    error=self.fetch_failure("Badcaps thread fetch failed", thread_url))
                        return
                    for post_text, author, date in _extract_vb_posts(thread_html):
                        if count >= max_records:
                            return
                        tid = _vb_thread_id_from_url(thread_url) or thread_url
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
                legacy_checkpoint(self.name, "badcaps", page, page + 1)
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


# ---- vBulletin HTML parsing -----------------------------------------

_VB_THREAD_LINK_RE = re.compile(
    r'<a[^>]+href="([^"]*showthread\.php/[^"]+)"[^>]*class="title"[^>]*>([^<]+)</a>',
    re.IGNORECASE
)
_VB_POST_RE = re.compile(
    r'<blockquote[^>]*class="postcontent[^"]*"[^>]*>(.*?)</blockquote>',
    re.DOTALL | re.IGNORECASE
)
_VB_AUTHOR_RE = re.compile(
    r'<a[^>]*class="username[^"]*"[^>]*>([^<]+)</a>', re.IGNORECASE
)
_VB_DATE_RE = re.compile(
    r'(\d{2}-\d{2}-\d{4},\s*\d{2}:\d{2}\s*[AP]M)', re.IGNORECASE
)


def _extract_vb_thread_links(html: str) -> list:
    out = []
    for m in _VB_THREAD_LINK_RE.finditer(html):
        url = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if url.startswith("http"):
            out.append((url, title))
        else:
            out.append(("https://www.badcaps.net/forum/" + url, title))
    return out


def _extract_vb_posts(html: str) -> list:
    posts = []
    for m in _VB_POST_RE.finditer(html):
        body = m.group(1)
        text = re.sub(r"<[^>]+>", "", body).strip()
        author = ""
        author_m = _VB_AUTHOR_RE.search(html, m.start())
        if author_m:
            author = author_m.group(1).strip()
        date = ""
        date_m = _VB_DATE_RE.search(html, max(0, m.start() - 500))
        if date_m:
            date = date_m.group(1).strip()
        if text and len(text) > 20:
            posts.append((text, author, date))
    return posts


def _vb_thread_id_from_url(url: str) -> str:
    m = re.search(r"/(\d+)-", url) or re.search(r"t=(\d+)", url)
    return m.group(1) if m else ""
