"""Conservative connectors for Discourse, SMF and XenForo forums.

Discourse uses its public JSON endpoints. SMF and XenForo use a deliberately
small HTML parser for public thread links and post containers. These
connectors do not bypass login walls, CAPTCHA, robots.txt, or JavaScript
requirements; the shared Fetcher enforces robots policy and rate limits.
"""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Iterator, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
import re

from .base import (
    BaseConnector, RawItem, ParsedItem, stable_post_id,
    legacy_start_page, legacy_checkpoint,
)
from .. import database
from ..schemas import SourceKind


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._href = dict(attrs).get("href", "") or ""
            self._text = []

    def handle_data(self, data):
        if self._href:
            value = " ".join(data.split())
            if value:
                self._text.append(value)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = ""
            self._text = []


class _PostBlocks(HTMLParser):
    """Capture common SMF/XenForo article/message containers."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.posts: list[dict] = []
        self._active: Optional[dict] = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            return
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").lower().split())
        candidate = (
            tag.lower() == "article" and any("message" in c or "post" in c for c in classes)
        ) or any(c in classes for c in (
            "post", "postbody", "post_body", "message", "message-content",
            "messagebody", "postarea", "windowbg",
        ))
        if self._active is None and candidate:
            self._active = {
                "tag": tag.lower(), "depth": 1, "parts": [],
                "author": attrs.get("data-author", "") or attrs.get("data-user-name", ""),
                "date": attrs.get("data-time", "") or attrs.get("datetime", ""),
                "post_id": attrs.get("id", ""),
            }
            return
        if self._active is not None:
            self._active["depth"] += 1
            if tag.lower() == "time":
                date = attrs.get("datetime")
                if date:
                    self._active["date"] = date

    def handle_data(self, data):
        if self._active is not None:
            value = " ".join(data.split())
            if value:
                self._active["parts"].append(value)

    def handle_startendtag(self, tag, attrs):
        if tag.lower() in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag.lower() in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            return
        if self._active is None:
            return
        self._active["depth"] -= 1
        if self._active["depth"] <= 0 and tag.lower() == self._active["tag"]:
            text = re.sub(r"\s+", " ", " ".join(self._active["parts"])).strip()
            if len(text) >= 30:
                self.posts.append({
                    "text": text,
                    "author": self._active["author"],
                    "date": self._active["date"],
                    "post_id": self._active["post_id"],
                })
            self._active = None


class DiscourseConnector(BaseConnector):
    """Public Discourse JSON API connector."""
    name = "discourse"
    kind = SourceKind.FORUM_HTML

    def discover(self) -> Iterator[RawItem]:
        self.base_url = str(self.source_config.get("base_url") or "").rstrip("/")
        if not self.base_url:
            database.update_crawl_state(self.name, status="failed", error="Discourse base_url is not configured")
            return
        max_records = self.max_records()
        first_page = legacy_start_page(self.name, "discourse", 0)
        count = 0
        for page in range(first_page, first_page + self.max_depth()):
            listing_url = f"{self.base_url}/latest.json?page={page}"
            payload = self.fetch_json(listing_url)
            if payload is None:
                database.update_crawl_state(
                    self.name, status="failed",
                    error=self.fetch_failure("Discourse topic-list request failed", listing_url),
                )
                return
            topics = (payload.get("topic_list") or {}).get("topics") or []
            if not topics:
                return
            for topic in topics:
                topic_id = topic.get("id")
                if not topic_id:
                    continue
                topic_url = f"{self.base_url}/t/{topic_id}.json"
                detail = self.fetch_json(topic_url)
                if detail is None:
                    database.update_crawl_state(
                        self.name, status="failed",
                        error=self.fetch_failure("Discourse thread request failed", topic_url),
                    )
                    return
                title = str(detail.get("title") or topic.get("title") or "")
                slug = str(detail.get("slug") or topic.get("slug") or "")
                posts = (detail.get("post_stream") or {}).get("posts") or []
                for post in posts:
                    if count >= max_records:
                        database.update_crawl_state(
                            self.name, status="paused", error="configured per-source record cap reached"
                        )
                        return
                    post_id = str(post.get("id") or "")
                    if not post_id:
                        continue
                    post_number = post.get("post_number") or 1
                    post_url = f"{self.base_url}/t/{slug}/{topic_id}/{post_number}"
                    yield RawItem(
                        url=post_url,
                        thread_id=str(topic_id),
                        post_id=post_id,
                        raw=post,
                        meta={"title": title, "kind": "post"},
                    )
                    count += 1
            legacy_checkpoint(self.name, "discourse", page, page + 1)

    def parse(self, raw: RawItem) -> Optional[ParsedItem]:
        if not isinstance(raw.raw, dict):
            return None
        post = raw.raw
        html = str(post.get("cooked") or post.get("raw") or "")
        parser = _TextOnly()
        try:
            parser.feed(html)
        except Exception:
            return None
        text = parser.text()
        if len(text) < 30:
            return None
        return ParsedItem(
            url=raw.url, thread_id=raw.thread_id, post_id=raw.post_id,
            title=str(raw.meta.get("title") or ""),
            author=str(post.get("username") or post.get("name") or ""),
            published_at=str(post.get("created_at") or ""),
            text=text, meta={"kind": "post", "platform": "discourse"},
        )


class _ConfiguredHTMLConnector(BaseConnector):
    platform = "generic"
    name = "forum_html"
    kind = SourceKind.FORUM_HTML

    def _page_url(self, page: int) -> str:
        parts = urlsplit(self.base_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if self.platform == "smf":
            query["start"] = str(page * 20)
        else:
            query["page"] = str(page + 1)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def _is_thread_url(self, url: str) -> bool:
        value = url.lower()
        if self.platform == "smf":
            return "topic=" in value or "index.php/topic/" in value
        return "/threads/" in value

    def discover(self) -> Iterator[RawItem]:
        self.base_url = str(self.source_config.get("base_url") or "").strip()
        if not self.base_url:
            database.update_crawl_state(self.name, status="failed", error=f"{self.platform} base_url is not configured")
            return
        first_page = legacy_start_page(self.name, self.platform, 0)
        seen_threads: set[str] = set()
        count = 0
        for page in range(first_page, first_page + self.max_depth()):
            board_url = self._page_url(page)
            html = self.fetch_html(board_url)
            if not html:
                database.update_crawl_state(
                    self.name, status="failed",
                    error=self.fetch_failure(f"{self.platform} board fetch failed", board_url),
                )
                return
            links = _Links()
            links.feed(html)
            threads = []
            for href, title in links.links:
                url = urljoin(board_url, href)
                if not self._is_thread_url(url) or url in seen_threads:
                    continue
                if urlsplit(url).netloc != urlsplit(self.base_url).netloc:
                    continue
                seen_threads.add(url)
                threads.append((url, title))
            if not threads:
                if page == first_page:
                    database.update_crawl_state(
                        self.name, status="failed",
                        error=f"No recognizable public {self.platform} thread links found; verify the configured board URL.",
                    )
                return
            for thread_url, title in threads:
                thread_html = self.fetch_html(thread_url)
                if not thread_html:
                    database.update_crawl_state(
                        self.name, status="failed",
                        error=self.fetch_failure(f"{self.platform} thread fetch failed", thread_url),
                    )
                    return
                blocks = _PostBlocks()
                blocks.feed(thread_html)
                thread_id_match = (
                    re.search(r"topic[=/](\d+)", thread_url, re.I)
                    if self.platform == "smf"
                    else re.search(r"/threads/[^/.]+\.(\d+)", thread_url, re.I)
                )
                thread_id = thread_id_match.group(1) if thread_id_match else thread_url
                for post in blocks.posts:
                    if count >= self.max_records():
                        database.update_crawl_state(
                            self.name, status="paused", error="configured per-source record cap reached"
                        )
                        return
                    post_id = stable_post_id(
                        thread_id, post.get("author", ""), post.get("date", ""),
                        post["text"], post.get("post_id", ""),
                    )
                    yield RawItem(
                        url=f"{thread_url}#kc-post-{post_id}",
                        thread_id=str(thread_id), post_id=post_id,
                        raw=post["text"],
                        meta={"title": title, "author": post.get("author", ""),
                              "date": post.get("date", ""), "kind": "post"},
                    )
                    count += 1
            legacy_checkpoint(self.name, self.platform, page, page + 1)

    def parse(self, raw: RawItem) -> Optional[ParsedItem]:
        if not isinstance(raw.raw, str):
            return None
        text = re.sub(r"\s+", " ", raw.raw).strip()
        if len(text) < 30:
            return None
        return ParsedItem(
            url=raw.url, thread_id=raw.thread_id, post_id=raw.post_id,
            title=str(raw.meta.get("title") or ""),
            author=str(raw.meta.get("author") or ""),
            published_at=str(raw.meta.get("date") or ""),
            text=text, meta={"kind": "post", "platform": self.platform},
        )


class SMFConnector(_ConfiguredHTMLConnector):
    name = "smf"
    platform = "smf"


class XenForoConnector(_ConfiguredHTMLConnector):
    name = "xenforo"
    platform = "xenforo"
