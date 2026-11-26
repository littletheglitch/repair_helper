"""Built-in electronics knowledge base.

This module loads structured technical facts from JSON files in the
``Knowledge/`` directory into the SQLite ``knowledge_facts`` table. The
content is curated general electronics and laptop-motherboard-power
knowledge. It deliberately avoids fabricated component-specific numbers;
every numeric value is either a generic law (e.g. Ohm's law) or is
qualified with "typical" and provenance to a public domain reference.

Provenance values used here:
- "general-electrical-law"   : textbook physics/electronics, no copyright issue.
- "manufacturer-app-note"    : summarizes an application note's qualitative
                               behavior without reproducing tables.
- "repair-forum-summary"     : aggregated technician-reported experience,
                               rewritten as a concise verified/unverified
                               summary, not a copy of any single post.
- "verified-reference"       : a named technical reference.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Iterable, List

from . import config
from . import database


def knowledge_dir() -> Path:
    return config.app_dir("Knowledge")


def builtin_files() -> List[Path]:
    d = knowledge_dir()
    if not d.exists():
        return []
    return sorted(d.glob("*.json"))


def load_builtin(force: bool = False) -> int:
    """Load every JSON knowledge file into the database.

    Returns the number of facts inserted. By default it skips files that
    were already loaded (matched by file stem in a registry row), so the
    knowledge base is idempotent. Pass ``force=True`` to reload everything.
    """
    import time
    conn = database.connect()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS knowledge_sources (
            file_stem TEXT PRIMARY KEY,
            fact_count INTEGER NOT NULL,
            loaded_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    inserted = 0
    for fp in builtin_files():
        stem = fp.stem
        if not force:
            row = conn.execute(
                "SELECT 1 FROM knowledge_sources WHERE file_stem=?", (stem,)
            ).fetchone()
            if row:
                continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        n = _ingest(data)
        conn.execute(
            "INSERT OR REPLACE INTO knowledge_sources(file_stem, fact_count, loaded_at)"
            " VALUES(?,?,?)",
            (stem, n, database.now_iso()),
        )
        conn.commit()
        inserted += n
    return inserted


def _ingest(data: Dict) -> int:
    """Insert facts from a parsed knowledge JSON document."""
    source = data.get("source", {})
    file_provenance = data.get("provenance", "verified-reference")
    file_tier = data.get("tier") or database._PROVENANCE_TO_TIER.get(
        file_provenance, "verified-reference")
    retrieved = source.get("retrieved_at", "")
    n = 0
    for section in data.get("sections", []):
        category = section.get("category", "uncategorized")
        topic = section.get("topic", "")
        for fact in section.get("facts", []):
            fact_prov = fact.get("provenance", file_provenance)
            # Per-fact tier takes precedence; otherwise derive from the
            # fact's own provenance (which may differ from the file's).
            fact_tier = fact.get("tier") or database._PROVENANCE_TO_TIER.get(
                fact_prov, file_tier)
            database.import_fact(
                category=category,
                topic=topic,
                fact=fact.get("text", ""),
                provenance=fact_prov,
                confidence=fact.get("confidence", "verified"),
                source_url=fact.get("source_url", source.get("url", "")),
                source_title=fact.get("source_title", source.get("title", "")),
                source_date=fact.get("source_date", source.get("date", "")),
                retrieved_at=retrieved,
                tags=",".join(fact.get("tags", [])),
                tier=fact_tier,
            )
            n += 1
    return n


def search_facts(query: str, limit: int = 20, tier: str = "") -> List[Dict]:
    """Keyword search over the knowledge base using FTS5.

    If ``tier`` is set, only facts of that provenance tier are returned
    (so the reasoning engine can prefer manufacturer-spec facts over
    community observations when needed).
    """
    has_tier = _tier_column_present()
    select_cols = ("kf.id, kf.category, kf.topic, kf.fact, kf.provenance, "
                    "kf.confidence, kf.source_url, kf.source_title"
                    + (", kf.tier" if has_tier else ""))
    try:
        sql = (f"SELECT {select_cols} FROM knowledge_fts k "
               "JOIN knowledge_facts kf ON kf.id = k.rowid "
               "WHERE k.fact MATCH ?")
        params: List = [query]
        if tier and has_tier:
            sql += " AND kf.tier = ?"
            params.append(tier)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        rows = database.query_all(sql, params)
        if rows:
            return rows
    except Exception:
        pass
    # Fallback: simple LIKE search.
    like = f"%{query}%"
    select_cols2 = ("id, category, topic, fact, provenance, confidence, "
                     "source_url, source_title"
                     + (", tier" if has_tier else ""))
    sql2 = (f"SELECT {select_cols2} FROM knowledge_facts "
            "WHERE (fact LIKE ? OR topic LIKE ?)")
    params2: List = [like, like]
    if tier and has_tier:
        sql2 += " AND tier = ?"
        params2.append(tier)
    sql2 += " LIMIT ?"
    params2.append(limit)
    return database.query_all(sql2, params2)


def _tier_column_present() -> bool:
    conn = database.connect()
    return any(c["name"] == "tier" for c in conn.execute(
        "PRAGMA table_info(knowledge_facts)").fetchall())


def facts_by_tier(tier: str, limit: int = 100) -> List[Dict]:
    """Return all facts of a given tier (no keyword filter)."""
    if not _tier_column_present():
        return []
    return database.query_all(
        "SELECT id, category, topic, fact, provenance, confidence, tier "
        "FROM knowledge_facts WHERE tier=? LIMIT ?",
        (tier, limit),
    )


def tier_counts() -> Dict[str, int]:
    """Return {tier: count} for the whole knowledge base."""
    if not _tier_column_present():
        return {}
    rows = database.query_all(
        "SELECT tier, COUNT(*) AS n FROM knowledge_facts GROUP BY tier"
    )
    return {r["tier"]: int(r["n"]) for r in rows}


def categories() -> List[str]:
    rows = database.query_all(
        "SELECT DISTINCT category FROM knowledge_facts ORDER BY category"
    )
    return [r["category"] for r in rows]
