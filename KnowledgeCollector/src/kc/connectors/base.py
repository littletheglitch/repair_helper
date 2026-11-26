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


class BaseConnector:
    """Subclass and set the class attributes, then implement
    ``discover`` and ``parse``."""
    name: str = ""
    kind: SourceKind = SourceKind.FORUM_HTML
    base_url: str = ""

    def __init__(self, source_config: Optional[Dict[str, Any]] = None,
                 fetcher=None):
        self.source_config = source_config or database.get_source(self.name) or {}
        self.fetcher = fetcher

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

    def fetch_json(self, url: str) -> Optional[Dict[str, Any]]:
        if not self.fetcher:
            return None
        r = self.fetcher.fetch(url, rate_per_min=self.rate_per_min(),
                               extra_headers={"Accept": "application/json"})
        if not r.ok:
            return None
        try:
            return json.loads(r.text)
        except json.JSONDecodeError:
            return None

    def fetch_html(self, url: str) -> Optional[str]:
        if not self.fetcher:
            return None
        r = self.fetcher.fetch(url, rate_per_min=self.rate_per_min())
        if not r.ok:
            return None
        return r.text
