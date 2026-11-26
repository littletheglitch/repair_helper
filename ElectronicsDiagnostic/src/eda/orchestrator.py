"""Coordinate host EDA reasoning with Expansion Pack diagnostic tools.

The host's evidence-based reasoning remains authoritative. This module is a
single, typed routing seam from the master application into every supported
Expansion Pack engine. Host session data is mapped read-only by the registered
ExpansionPackEngine bridge.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

from . import sessions
from .engines import base as engine_router

SUPPORTED_PACK_ENGINES = frozenset({
    "power_on_sequence",
    "motherboard",
    "power_supply",
    "circuit",
    "identify",
    "replacement",
    "measurement",
    "repair_case",
    "knowledge",
    "netlist",
})


def infer_platform(title: str = "", board_model: str = "") -> Tuple[str, str]:
    """Infer a conservative platform default; callers can explicitly override it."""
    context = f"{title or ''} {board_model or ''}".casefold()
    if re.search(r"\b(desktop|tower|atx|micro[- ]?atx|mini[- ]?itx)\b", context):
        return "desktop", "inferred from session title/board model"
    return "laptop", "defaulted to laptop; pass --platform desktop for desktop boards"


def dispatch_expansion_pack(
    engine_name: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    session_id: Optional[int] = None,
    platform: Optional[str] = None,
):
    """Dispatch one supported Pack engine through the host Tool Router.

    `session_id` is optional. When supplied, the bridge reads that host repair
    session and its measurements without writing to the host database.
    """
    name = str(engine_name or "").strip()
    if name not in SUPPORTED_PACK_ENGINES:
        raise ValueError(
            f"Unsupported Expansion Pack engine {name!r}; supported: "
            f"{', '.join(sorted(SUPPORTED_PACK_ENGINES))}"
        )

    request_payload = dict(payload or {})
    if platform is not None:
        selected_platform = str(platform).strip().lower()
        if selected_platform not in {"laptop", "desktop"}:
            raise ValueError("platform must be 'laptop' or 'desktop'")
        request_payload.setdefault("platform", selected_platform)

    inputs: Dict[str, Any] = {
        "engine_name": name,
        "payload_json": json.dumps(request_payload, ensure_ascii=False),
    }
    if session_id is not None:
        sid = int(session_id)
        if sessions.get(sid) is None:
            raise ValueError(f"Repair session {sid} does not exist")
        inputs["session_id"] = sid
    if platform is not None:
        inputs["platform"] = selected_platform

    return engine_router.call("expansion_pack", "dispatch", inputs)


def run_expansion_pack_sequence(session_id: int, platform: Optional[str] = None):
    """Run the Pack's power-on-sequence engine for one host repair session.

    If the platform is omitted, infer it from session title/board model and
    return the inference note so the CLI can disclose the assumption.
    """
    sid = int(session_id)
    host_session = sessions.get(sid)
    if host_session is None:
        raise ValueError(f"Repair session {sid} does not exist")

    inference_note = ""
    if platform is None:
        platform, inference_note = infer_platform(
            getattr(host_session, "title", ""),
            getattr(host_session, "board_model", ""),
        )
    platform = str(platform).strip().lower()
    if platform not in {"laptop", "desktop"}:
        raise ValueError("platform must be 'laptop' or 'desktop'")

    result = dispatch_expansion_pack(
        "power_on_sequence", payload={}, session_id=sid, platform=platform
    )
    return result, platform, inference_note
