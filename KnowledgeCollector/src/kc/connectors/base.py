"""Base connector contract.

Every connector subclasses :class:`BaseConnector` and implements
``discover`` (an iterator over raw items) and ``parse`` (turn one raw
item into a parsed item with title/body/meta). The orchestrator runs
the BIOS guard, filter, extractor, classifier, and dedup on each
parsed item.

A connector is configured via a :class:`SourceConfig` row in the
database. The base class loads it on construction. Connectors NEVER
bypass auth, paywalls, CAPTCHAs or JS rendering; they log "unsupported"
and skip. They NEVER collect an entire forum indiscriminately — the
filter discards off-topic items.
"""
from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from .. import database, config
from ..schemas import SourceConfig, SourceKind


@dataclass
class RawItem:
    """A raw item from a connector, before parsing."""
    url: str
    thread_id: str
    post_id: str
    raw: Any             # str (HTML/JSON) or bytes
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedItem:
    """A parsed item ready for the filter/classifier/dedup pipeline."""
    url: str
    thread_id: str
    post_id: str
    title: str
    author: str
    published_at: str
    text: str
    meta: Dict[str, Any] = field(default_factory=dict)


def stable_post_id(thread_id: str, author: str, published_at: str, text: str,
                   explicit_id: str = "") -> str:
    """Stable post key when forum HTML has no native post identifier."""
    if explicit_id is not None and str(explicit_id).strip():
        return str(explicit_id).strip()
    def norm(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    parts = [norm(thread_id), norm(author), norm(published_at), norm(text)]
    digest = hashlib.sha256("\0".join(parts).encode("utf-8", errors="replace")).hexdigest()
    return "kc_" + digest[:24]


def legacy_start_page(source_name: str, connector_id: str, first_page: int) -> int:
    """Resume legacy page-based connectors; replay an unfinished page safely."""
    state = database.get_crawl_state(source_name)
    raw = state.get("last_url") or ""
    try:
        cursor = json.loads(raw)
        if cursor.get("connector") == "legacy:" + connector_id:
            return max(first_page, int(cursor.get("next_page", first_page)))
    except (TypeError, ValueError, AttributeError):
        pass
    # Backward compatibility: old collector versions stored a post URL and
    # the last completed page number. Replaying a page is safer than skipping.
    last_page = int(state.get("last_page") or 0)
    if raw:
        # A raw URL came from older collector versions and cannot tell us
        # whether the current page had finished. Replay the first/ambiguous
        # page rather than risk skipping posts.
        if last_page <= first_page:
            return first_page
        return max(first_page, last_page + 1)
    if last_page > first_page:
        return max(first_page, last_page + 1)
    return first_page


def legacy_checkpoint(source_name: str, connector_id: str, completed_page: int,
                      next_page: int) -> None:
    database.update_crawl_state(
        source_name, last_page=completed_page,
        last_url=json.dumps({"connector": "legacy:" + connector_id,
                             "next_page": int(next_page)}, separators=(",", ":")),
        status="running")


class BaseConnector:
    """Subclass and set the class attributes, then implement
    ``discover`` and ``parse``."""
    name: str = ""
    kind: SourceKind = SourceKind.FORUM_HTML
    base_url: str = ""

    def __init__(self, source_config: Optional[Dict[str, Any]] = None,
                 fetcher=None):
        self.source_config = source_config or database.get_source(self.name) or {}
        # Catalog-driven connectors may serve many named sources. Bind the
        # configured source id so each one keeps an independent crawl cursor.
        if self.source_config.get("name"):
            self.name = str(self.source_config["name"])
        self.fetcher = fetcher
        # Preserve the last transport outcome so connectors can report why a
        # request failed (HTTP status, robots policy, login wall, timeout, etc.)
        # instead of collapsing every failure into a vague "fetch failed".
        self.last_fetch_result = None
        self.last_fetch_error = ""

    # ---- to be overridden ------------------------------------------
    def discover(self) -> Iterator[RawItem]:
        raise NotImplementedError

    def parse(self, raw: RawItem) -> Optional[ParsedItem]:
        raise NotImplementedError

    # ---- shared helpers -------------------------------------------
    def rate_per_min(self) -> int:
        return int(self.source_config.get("rate_per_min", 6))

    def max_depth(self) -> int:
        return int(self.source_config.get("max_depth", 5))

    def max_records(self) -> int:
        return int(self.source_config.get("max_records", 5000))

    def max_bytes(self) -> int:
        return int(self.source_config.get("max_bytes", 500 * 1024 * 1024))

    def _remember_fetch_result(self, url: str, result) -> None:
        """Store a concise, actionable description of the last fetch result."""
        self.last_fetch_result = result
        self.last_fetch_error = ""
        if result is None:
            self.last_fetch_error = f"no response object for {url}"
            return
        if getattr(result, "ok", False):
            return

        parts = []
        kind = getattr(result, "kind", "")
        status = getattr(result, "status", 0)
        error = str(getattr(result, "error", "") or "").strip()
        final_url = str(getattr(result, "final_url", "") or "").strip()
        if kind:
            parts.append(str(kind))
        if status:
            parts.append(f"HTTP {status}")
        if error:
            parts.append(error)
        if final_url and final_url != url:
            parts.append(f"redirected to {final_url}")
        self.last_fetch_error = "; ".join(parts) or "request failed without details"

    def fetch_failure(self, action: str, url: str) -> str:
        """Format a source-level error without hiding the underlying cause."""
        detail = self.last_fetch_error or "no diagnostic detail available"
        return f"{action}: {url}; {detail}"

    def fetch_json(self, url: str) -> Optional[Dict[str, Any]]:
        self.last_fetch_error = ""
        if not self.fetcher:
            self.last_fetch_result = None
            self.last_fetch_error = "no fetcher configured"
            return None
        try:
            r = self.fetcher.fetch(url, rate_per_min=self.rate_per_min(),
                                   extra_headers={"Accept": "application/json"})
        except Exception as exc:
            self.last_fetch_result = None
            self.last_fetch_error = f"fetcher raised {type(exc).__name__}: {exc}"
            return None
        self._remember_fetch_result(url, r)
        if not getattr(r, "ok", False):
            return None
        try:
            payload = json.loads(r.text)
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            self.last_fetch_error = f"invalid JSON response ({type(exc).__name__}: {exc})"
            return None
        if not isinstance(payload, dict):
            self.last_fetch_error = f"unexpected JSON root type: {type(payload).__name__}"
            return None
        return payload

    def fetch_html(self, url: str) -> Optional[str]:
        self.last_fetch_error = ""
        if not self.fetcher:
            self.last_fetch_result = None
            self.last_fetch_error = "no fetcher configured"
            return None
        try:
            r = self.fetcher.fetch(url, rate_per_min=self.rate_per_min())
        except Exception as exc:
            self.last_fetch_result = None
            self.last_fetch_error = f"fetcher raised {type(exc).__name__}: {exc}"
            return None
        self._remember_fetch_result(url, r)
        if not getattr(r, "ok", False):
            return None
        text = getattr(r, "text", None)
        if text is None:
            self.last_fetch_error = "successful response did not contain text"
            return None
        return text
