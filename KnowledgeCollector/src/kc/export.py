"""Export the collected knowledge in multiple formats.

Formats:
  jsonl       — one record per line, full structured fields + text.
  markdown    — human-readable, one section per record, with source URL.
  sqlite      — a copy of the Collector's DB (snapshot).
  text        — per-record .txt files with a source-reference header.
  knowledge   — the assistant-compatible Knowledge JSON files (one per
                source). This is the format the diagnostic assistant
                ingests via knowledge_base.load_builtin() with NO code
                changes.

The Knowledge JSON format matches the integration compatibility report
exactly: each file is keyed by a stable stem (``kc_<source>_<date>``)
so the assistant's knowledge_sources registry dedups re-imports.

A ``manifest.json`` is always written alongside the exports, listing
every file and the schema version, so the importer can verify the
export before importing.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config, database, schemas
from .schemas import RecordClass, CLASS_TO_ASSISTANT_TIER


SCHEMA_VERSION = 1


def export(format_name: str, out_dir: Optional[Path] = None,
           source_name: str = "") -> Dict[str, Any]:
    """Run an export. Returns a summary dict. ``out_dir`` defaults to
    config.export_dir() / <format>."""
    if format_name not in ("jsonl", "markdown", "sqlite", "text", "knowledge"):
        return {"ok": False, "error": f"unknown format '{format_name}'"}
    out = Path(out_dir) if out_dir else config.export_dir() / format_name
    out.mkdir(parents=True, exist_ok=True)
    if format_name == "jsonl":
        return _export_jsonl(out, source_name)
    if format_name == "markdown":
        return _export_markdown(out, source_name)
    if format_name == "sqlite":
        return _export_sqlite(out)
    if format_name == "text":
        return _export_text(out, source_name)
    if format_name == "knowledge":
        return _export_knowledge(out, source_name)


# ----------------------------------------------------------------------
# JSONL
# ----------------------------------------------------------------------

def _export_jsonl(out: Path, source_name: str) -> Dict[str, Any]:
    rows = _load_records(source_name)
    path = out / "records.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    database.log_export("jsonl", 1, len(rows), str(out))
    _write_manifest(out, {"jsonl": [str(path)]}, len(rows))
    return {"ok": True, "format": "jsonl", "file": str(path),
            "records": len(rows)}


# ----------------------------------------------------------------------
# Markdown
# ----------------------------------------------------------------------

def _export_markdown(out: Path, source_name: str) -> Dict[str, Any]:
    rows = _load_records(source_name)
    path = out / "records.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Collected repair knowledge\n\n")
        for r in rows:
            f.write(f"## {r.get('title') or r.get('url')}\n\n")
            f.write(f"- Source: {r['source_name']}\n")
            f.write(f"- URL: {r['url']}\n")
            f.write(f"- Collected: {r['collected_at']}\n")
            cls = _parse_class(r.get("classification", ""))
            if cls:
                f.write(f"- Classification: {cls}\n")
            f.write(f"- Content hash: {r['content_hash']}\n")
            f.write(f"\n{r['text']}\n\n---\n\n")
    database.log_export("markdown", 1, len(rows), str(out))
    _write_manifest(out, {"markdown": [str(path)]}, len(rows))
    return {"ok": True, "format": "markdown", "file": str(path),
            "records": len(rows)}


# ----------------------------------------------------------------------
# SQLite snapshot
# ----------------------------------------------------------------------

def _export_sqlite(out: Path) -> Dict[str, Any]:
    """Create a consistent standalone SQLite snapshot using SQLite backup API.

    The live collector connection remains open and its WAL/SHM sidecars are
    not copied separately. This avoids a torn snapshot and avoids clearing
    the module's active connection cache.
    """
    src = config.db_path()
    dst = out / "kc_snapshot.sqlite3"
    out.mkdir(parents=True, exist_ok=True)
    temp = dst.with_name(f".{dst.name}.tmp")
    try:
        temp.unlink(missing_ok=True)
        source_conn = database.connect()
        snapshot_conn = sqlite3.connect(str(temp))
        try:
            source_conn.backup(snapshot_conn)
            check = snapshot_conn.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                raise sqlite3.DatabaseError(
                    f"snapshot integrity check failed: {check[0] if check else 'no result'}"
                )
        finally:
            snapshot_conn.close()
        os.replace(temp, dst)
    except Exception:
        temp.unlink(missing_ok=True)
        raise

    database.log_export("sqlite", 1, -1, str(out))
    _write_manifest(out, {"sqlite": [str(dst)]}, -1)
    return {"ok": True, "format": "sqlite", "file": str(dst)}


# ----------------------------------------------------------------------
# Extracted text (per-record .txt with source-reference header)
# ----------------------------------------------------------------------

def _export_text(out: Path, source_name: str) -> Dict[str, Any]:
    rows = _load_records(source_name)
    files = []
    for r in rows:
        raw_name = str(r.get("url") or r["id"])
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in raw_name)
        safe = safe.strip(" .")[:80] or "record"
        path = out / f"{r['id']}_{safe}.txt"
        header = (
            f"# Source: {r['source_name']}\n"
            f"# URL: {r['url']}\n"
            f"# Collected: {r['collected_at']}\n"
            f"# Title: {r.get('title', '')}\n"
            f"# Author: {r.get('author', '')}\n"
            f"# Content hash: {r['content_hash']}\n"
            f"# Classification: {_parse_class(r.get('classification',''))}\n"
            f"\n"
        )
        path.write_text(header + r["text"], encoding="utf-8")
        files.append(str(path))
    database.log_export("text", len(files), len(rows), str(out))
    _write_manifest(out, {"text": files}, len(rows))
    return {"ok": True, "format": "text", "files": len(files),
            "records": len(rows)}


# ----------------------------------------------------------------------
# Knowledge JSON (assistant-compatible)
# ----------------------------------------------------------------------

def _export_knowledge(out: Path, source_name: str) -> Dict[str, Any]:
    """Write one Knowledge JSON file per source, matching the assistant's
    schema exactly. Each fact carries provenance + tier derived from the
    Collector's classification."""
    # Group records by source.
    if source_name:
        sources = [source_name]
    else:
        sources = [r["source_name"] for r in database.query_all(
            "SELECT DISTINCT source_name FROM records ORDER BY source_name")]
    files = []
    total = 0
    today = config.now_iso().split()[0]  # YYYY-MM-DD
    for src in sources:
        rows = _load_records(src)
        if not rows:
            continue
        sections: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            cls_obj = _parse_classification(r.get("classification", ""))
            tier = _class_to_tier(cls_obj)
            provenance = _tier_to_provenance(tier)
            # Each record becomes one fact. category groups by topic-ish.
            category = "repair-forum" if src.startswith("eevblog") or \
                       src.startswith("badcaps") or src.startswith("laptop") \
                       else "stackexchange"
            topic = (r.get("title") or r["thread_id"] or "uncategorized")[:60]
            topic_key = topic.lower().replace(" ", "-").replace("/", "-")[:60]
            section_key = f"{category}|{topic_key}"
            if section_key not in sections:
                sections[section_key] = {
                    "category": category,
                    "topic": topic_key,
                    "facts": [],
                }
            fact_text = _fact_text(r, cls_obj)
            sections[section_key]["facts"].append({
                "text": fact_text,
                "confidence": "verified" if tier == "technician-confirmed"
                              else ("unverified" if tier == "unverified"
                                    else "verified"),
                "provenance": provenance,
                "tier": tier,
                "source_url": r["url"],
                "source_title": (r.get("title") or f"{src} thread {r['thread_id']}")[:200],
                "source_date": (r.get("published_at") or r["collected_at"] or "")[:10],
                "tags": _tags_for(r, cls_obj),
            })
            total += 1
        # Build the file.
        file_stem = f"kc_{src}_{today}"
        path = out / f"{file_stem}.json"
        payload = {
            "source": {
                "title": f"Knowledge Collector export: {src}",
                "url": "",  # per-fact URLs are the authoritative source
                "date": today,
                "retrieved_at": config.now_iso(),
            },
            "provenance": "repair-forum-summary",
            "sections": list(sections.values()),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        files.append(str(path))
    database.log_export("knowledge", len(files), total, str(out))
    _write_manifest(out, {"knowledge": files}, total)
    return {"ok": True, "format": "knowledge", "files": len(files),
            "records": total}


def _fact_text(r: Dict[str, Any], cls_obj: Optional[Dict[str, Any]]) -> str:
    """Compose the fact text from the record. Preserves source + text +
    classification explanation so the assistant can cite it."""
    parts = [f"[{r['source_name']}] "]
    if r.get("title"):
        parts.append(r["title"] + ": ")
    parts.append(r["text"])
    if cls_obj:
        parts.append(f" (Collector classification: {cls_obj.get('cls','?')}; "
                     f"{cls_obj.get('explanation','')})")
    # Structured fields if present.
    try:
        struct = json.loads(r.get("structured", "{}") or "{}")
    except json.JSONDecodeError:
        struct = {}
    if struct:
        bits = []
        for k in ("component_model", "device_model", "motherboard_model",
                  "reported_symptoms", "measurements", "repair_actions"):
            v = struct.get(k, [])
            if v:
                bits.append(f"{k}={v}")
        if bits:
            parts.append(" [structured: " + "; ".join(bits) + "]")
    return "".join(parts)[:4000]   # cap at 4 KB per fact


def _tags_for(r: Dict[str, Any], cls_obj) -> List[str]:
    tags = [r["source_name"]]
    if cls_obj:
        tags.append(cls_obj.get("cls", "").split("_")[0])
    try:
        struct = json.loads(r.get("structured", "{}") or "{}")
        for k in ("component_model", "device_model", "motherboard_model"):
            for v in struct.get(k, [])[:3]:
                tags.append(v)
    except json.JSONDecodeError:
        pass
    return tags


def _class_to_tier(cls_obj: Optional[Dict[str, Any]]) -> str:
    if not cls_obj:
        return "community"
    cls = cls_obj.get("cls", "")
    for rc in RecordClass:
        if rc.value == cls:
            return CLASS_TO_ASSISTANT_TIER[rc]
    return "community"


def _tier_to_provenance(tier: str) -> str:
    # Reverse-map to the assistant's provenance strings so the existing
    # _PROVENANCE_TO_TIER map produces the right tier if the user does NOT
    # set tier explicitly. We DO set tier explicitly anyway.
    m = {
        "technician-confirmed": "repair-forum-summary",
        "community": "repair-forum-summary",
        "verified-reference": "verified-reference",
        "manufacturer-spec": "manufacturer-app-note",
        "unverified": "repair-forum-summary",
        "general-principle": "general-electrical-law",
    }
    return m.get(tier, "repair-forum-summary")


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------

def _write_manifest(out: Path, files: Dict[str, List[str]],
                    record_count: int) -> None:
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": config.now_iso(),
        "record_count": record_count,
        "files": files,
        "compatibility": "Electronics Diagnostic Assistant Knowledge/*.json schema",
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _load_records(source_name: str = "") -> List[Dict[str, Any]]:
    if source_name:
        return database.query_all(
            "SELECT * FROM records WHERE source_name=? AND status != 'rejected' "
            "ORDER BY id", (source_name,))
    return database.query_all(
        "SELECT * FROM records WHERE status != 'rejected' ORDER BY id")


def _parse_class(s: str) -> str:
    """Extract the class string from the classification JSON column."""
    if not s:
        return ""
    try:
        obj = json.loads(s)
        return obj.get("cls", "")
    except json.JSONDecodeError:
        return ""


def _parse_classification(s: str) -> Optional[Dict[str, Any]]:
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None
