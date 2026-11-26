"""Source manifest management.

Records, per source: license, terms URL, access method, collection
date, permitted storage/redistribution, and required attribution.

This module reads/writes the ``source_manifest`` table. The export
includes a manifest.json listing every source's manifest entry so the
assistant (and any downstream consumer) knows the legal status of each
record.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config, database


# Default manifest entries for the built-in sources. These are the
# defaults the user can override per source. They record the legal
# status the Collector operates under; the user confirms by enabling
# the source after reviewing the source's terms.
DEFAULT_MANIFESTS = {
    "stackexchange_electronics": {
        "license": "CC-BY-SA 4.0 (Stack Exchange network content license)",
        "terms_url": "https://stackexchange.com/legal/terms-of-service",
        "access_method": "public Stack Exchange API (sanctioned)",
        "permitted_storage": "metadata + content extracts with attribution and link",
        "attribution": "Electronics Stack Exchange; link to the original question/answer",
        "notes": "Content is user-licensed CC-BY-SA; share-alike required.",
    },
    "eevblog": {
        "license": "Forum ToS (user-submitted; cite per post)",
        "terms_url": "https://www.eevblog.com/forum/",
        "access_method": "HTML (robots-respecting; no auth bypass)",
        "permitted_storage": "metadata + short extracts with link to original thread",
        "attribution": "EEVblog Forum; link to the original post",
        "notes": "Do not redistribute full thread archives; short extracts with link.",
    },
    "badcaps": {
        "license": "Forum ToS (user-submitted; cite per post)",
        "terms_url": "https://www.badcaps.net/forum/",
        "access_method": "HTML (robots-respecting; no auth bypass)",
        "permitted_storage": "metadata + short extracts with link to original thread",
        "attribution": "Badcaps Forum; link to the original post",
        "notes": "Do not redistribute full thread archives.",
    },
}


def write_default_manifests() -> None:
    """Write the default manifest entries for the built-in sources.
    Idempotent."""
    for name, m in DEFAULT_MANIFESTS.items():
        database.upsert_manifest(name, **m)


def manifest_for(source_name: str) -> Optional[Dict[str, Any]]:
    return database.get_manifest(source_name)


def list_manifests() -> List[Dict[str, Any]]:
    return database.list_manifests()


def render_manifest_text() -> str:
    rows = list_manifests()
    if not rows:
        return "No source manifests recorded yet."
    lines = ["Source manifests:"]
    for r in rows:
        lines.append(f"\n  {r['source_name']}")
        lines.append(f"    license: {r['license']}")
        lines.append(f"    terms: {r['terms_url']}")
        lines.append(f"    access: {r['access_method']}")
        lines.append(f"    permitted: {r['permitted_storage']}")
        lines.append(f"    attribution: {r['attribution']}")
        if r.get("notes"):
            lines.append(f"    notes: {r['notes']}")
        lines.append(f"    collected_at: {r['collected_at']}")
    return "\n".join(lines)


def export_manifests(out_dir: Path) -> Path:
    """Write a manifest.json of all source manifests (used by the
    knowledge export to bundle legal metadata)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "sources_manifest.json"
    payload = {
        "schema_version": 1,
        "exported_at": config.now_iso(),
        "manifests": list_manifests(),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path
