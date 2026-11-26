"""EEVblog Forum connector.

EEVblog (https://www.eevblog.com/forum/) is a public electronics forum
with repair-relevant content. This connector:

- Respects robots.txt (via the fetcher).
- Walks the thread list pages (SMF board index -> topic pages).
- For each thread that passes the relevance filter, fetches the first
  page of posts and parses them.
- Does NOT bypass auth, paywalls, CAPTCHAs or JS rendering. If a page
  looks JS-required or login-walled, the connector skips it and logs.

The connector is fixture-tested; it never hits the real site during
the test suite.
"""
from __future__ import annotations

import re
from typing import Iterator, Optional

from .base import BaseConnector, RawItem, ParsedItem
from .. import database, config
from ..schemas import SourceKind
from .. import filtering


class EEvblogConnector(BaseConnector):
    name = "eevblog"
    kind = SourceKind.FORUM_HTML
    base_url = "https://www.eevblog.com/forum/"

    # SMF board URLs for repair-relevant boards.
    REPAIR_BOARD_URLS = [
        "https://www.eevblog.com/forum/repair/",
    ]

    def discover(self) -> Iterator[RawItem]:
        max_pages = self.max_depth()
        max_records = self.max_records()
        count = 0
        for board_url in self.REPAIR_BOARD_URLS:
            for page in range(0, max_pages):
                if count >= max_records:
                    return
                url = f"{board_url}index.php/board,40.{page*30}.html"
                html = self.fetch_html(url)
                if not html:
                    database.update_crawl_state(self.name, status="failed",
                                                error="board fetch failed")
                    return
                thread_links = _extract_thread_links(html, board_url)
                if not thread_links:
                    return  # no more threads
                for thread_url, title in thread_links:
                    if count >= max_records:
                        return
                    # We do NOT pre-filter by title alone here; the
                    # orchestrator filters each parsed post with its
                    # full text. Title-only filtering would reject
                    # threads whose title is short but whose posts are
                    # highly relevant.
                    thread_html = self.fetch_html(thread_url)
                    if not thread_html:
                        continue
                    for post_text, author, date in _extract_posts(thread_html):
                        if count >= max_records:
                            return
                        yield RawItem(
                            url=thread_url,
                            thread_id=_thread_id_from_url(thread_url),
                            post_id=f"{_thread_id_from_url(thread_url)}_{count}",
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


# ---- SMF HTML parsing (used by EEVblog; SMF is the forum software) ----

_POST_BLOCK_RE = re.compile(
    r'<div class="post">(.*?)</div>', re.DOTALL | re.IGNORECASE
)
_AUTHOR_RE = re.compile(
    r'<a[^>]*class="[^"]*poster[^"]*"[^>]*>([^<]+)</a>', re.IGNORECASE
)
_DATE_RE = re.compile(
    r'on:\s*([^<]+)<', re.IGNORECASE
)
_THREAD_LINK_RE = re.compile(
    r'<a[^>]+href="([^"]*topic,\d+\.\d+\.html)"[^>]*>([^<]+)</a>',
    re.IGNORECASE
)


def _extract_thread_links(html: str, base: str) -> list:
    """Return [(url, title), ...] of thread links in a board index page."""
    out = []
    for m in _THREAD_LINK_RE.finditer(html):
        url = m.group(1)
        title = _strip_tags(m.group(2)).strip()
        if url.startswith("http"):
            out.append((url, title))
        else:
            out.append((base + url, title))
    return out


def _extract_posts(html: str) -> list:
    """Return [(text, author, date), ...] of posts in a thread page."""
    posts = []
    for m in _POST_BLOCK_RE.finditer(html):
        body = m.group(1)
        text = _strip_tags(body)
        author = ""
        author_m = _AUTHOR_RE.search(html, m.start())
        if author_m:
            author = author_m.group(1).strip()
        date = ""
        date_m = _DATE_RE.search(html, m.start())
        if date_m:
            date = date_m.group(1).strip()
        if text and len(text) > 20:
            posts.append((text, author, date))
    return posts


def _thread_id_from_url(url: str) -> str:
    m = re.search(r"topic,(\d+)\.", url)
    return m.group(1) if m else ""


def _strip_tags(s: str) -> str:
    import html as _html
    t = re.sub(r"<[^>]+>", "", s)
    return _html.unescape(t).strip()
