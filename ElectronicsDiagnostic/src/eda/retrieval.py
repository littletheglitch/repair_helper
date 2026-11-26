"""Retrieval / search engine.

Provides on-demand retrieval over two corpora:
- the document library (PDFs, datasheets, schematics, text imported
  by the user), via SQLite FTS5 over chunks
- the built-in knowledge base, via FTS5 over knowledge_facts

Both are keyword searches run from disk; nothing is preloaded into RAM.
Vector retrieval is intentionally not used: on an 8 GB CPU-only target a
keyword + reranking approach is faster, deterministic, and avoids a
sentence-embedding model in memory.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import database
from . import knowledge_base


def search_documents(query: str, limit: int = 15,
                     document_id: Optional[int] = None) -> List[Dict]:
    """Keyword search over imported document chunks."""
    if not query.strip():
        return []
    fts_query = _to_fts_query(query)
    params: List = [fts_query]
    sql = (
        """SELECT c.id AS chunk_id, c.document_id, c.page, c.text,
                  d.file_name, d.file_type, d.status,
                  snippet(chunks_fts, 0, '>>', '<<', '...', 12) AS snippet
           FROM chunks_fts f
           JOIN document_chunks c ON c.id = f.rowid
           JOIN documents d ON d.id = c.document_id
           WHERE chunks_fts MATCH ?"""
    )
    if document_id is not None:
        sql += " AND d.id = ?"
        params.append(document_id)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        return database.query_all(sql, params)
    except Exception:
        # LIKE fallback if FTS5 unavailable at runtime.
        like = f"%{query}%"
        sql2 = (
            """SELECT c.id AS chunk_id, c.document_id, c.page, c.text,
                      d.file_name, d.file_type, d.status, c.text AS snippet
               FROM document_chunks c JOIN documents d ON d.id = c.document_id
               WHERE c.text LIKE ?"""
        )
        params2 = [like]
        if document_id is not None:
            sql2 += " AND d.id = ?"
            params2.append(document_id)
        sql2 += " LIMIT ?"
        params2.append(limit)
        return database.query_all(sql2, params2)


def search_knowledge(query: str, limit: int = 20) -> List[Dict]:
    return knowledge_base.search_facts(query, limit=limit)


def search_all(query: str, limit: int = 15) -> Dict[str, List[Dict]]:
    """Combine knowledge-base and document results for the reasoning engine."""
    return {
        "knowledge": search_knowledge(query, limit=limit),
        "documents": search_documents(query, limit=limit),
    }


def _to_fts_query(query: str) -> str:
    """Make a forgiving FTS5 query: quote multi-word phrases, AND terms."""
    terms = []
    for tok in query.strip().split():
        tok = tok.strip('"')
        if not tok:
            continue
        terms.append(f'"{tok}"')
    if not terms:
        return query
    return " AND ".join(terms)
