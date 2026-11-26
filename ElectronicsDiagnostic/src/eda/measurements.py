"""Measurement manager.

A measurement is a single electrical reading taken on the board. The
manager parses free-text input like ``measure VIN = 19.4V`` and stores
structured fields without forcing the technician to fill every one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import database


# A measurement line may be any of:
#   measure VIN = 19.4V
#   measure +3VALW = 3.31V
#   measure resistance_to_ground = 12.4ohm
#   measure ACOK = 0V @ EC pin 12
#   measure VIN=19.4V point=input jack ref=GND
_MEASURE_RE = re.compile(
    r"""
    ^\s*measure\s+
    (?P<name>[A-Za-z0-9_+\-/.]+)          # rail or signal name (dots ok: +3.3V)
    \s*=\s*
    (?P<value>[-+]?\d+(?:\.\d+)?)         # numeric value
    \s*
    (?P<unit>[A-Za-zµuΩohm]+)?            # optional unit
    (?:\s+@\s*(?P<point>[^\n|]+))?         # optional @ point (greedy, stop at |)
    (?:\s*\|\s*ref\s*=\s*(?P<ref>[^\n|]+))?  # optional | ref= ground reference
    (?:\s*\|\s*state\s*=\s*(?P<state>[^\n|]+))?  # optional | state=
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Tolerated units and normalisation map.
_UNIT_ALIASES = {
    "v": "V", "volts": "V", "volt": "V",
    "mv": "mV",
    "a": "A", "amps": "A", "amp": "A",
    "ma": "mA", "ua": "µA", "µa": "µA",
    "ohm": "Ω", "ohms": "Ω", "r": "Ω",
    "kohm": "kΩ", "kohms": "kΩ",
    "ω": "Ω",
    "hz": "Hz", "khz": "kHz", "mhz": "MHz",
}


def parse_unit(raw: str) -> str:
    s = raw.strip().lower()
    return _UNIT_ALIASES.get(s, raw.strip())


@dataclass
class Measurement:
    id: int
    session_id: int
    name: str
    value: Optional[float]
    unit: str
    raw_text: str
    point: str
    reference: str
    board_state: str
    adapter_state: str
    battery_state: str
    note: str
    taken_at: str


def parse_line(line: str) -> Optional[dict]:
    """Parse a single ``measure ...`` command into a structured dict."""
    m = _MEASURE_RE.match(line)
    if not m:
        return None
    name = m.group("name")
    val = float(m.group("value"))
    unit = parse_unit(m.group("unit") or "")
    point = (m.group("point") or "").strip()
    ref = (m.group("ref") or "").strip()
    state = (m.group("state") or "").strip()
    return {
        "name": name,
        "value": val,
        "unit": unit or "",
        "raw_text": line.strip(),
        "point": point,
        "reference": ref,
        "board_state": state,
        "adapter_state": "",
        "battery_state": "",
        "note": "",
    }


def add(session_id: int, m: dict) -> int:
    """Persist a parsed measurement. Never overwrites previous entries."""
    cur = database.execute(
        """INSERT INTO measurements(
            session_id, name, value, unit, raw_text, point, reference,
            board_state, adapter_state, battery_state, note, taken_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            session_id, m["name"], m.get("value"), m.get("unit", ""),
            m.get("raw_text", ""), m.get("point", ""), m.get("reference", ""),
            m.get("board_state", ""), m.get("adapter_state", ""),
            m.get("battery_state", ""), m.get("note", ""), database.now_iso(),
        ),
    )
    mid = int(cur.lastrowid)
    database.add_timeline(
        session_id, "measurement",
        f"{m['name']} = {m.get('value')} {m.get('unit','')}".strip(),
    )
    database.touch_session(session_id)
    return mid


def list_for(session_id: int) -> List[Measurement]:
    rows = database.query_all(
        "SELECT * FROM measurements WHERE session_id=? ORDER BY id", (session_id,)
    )
    return [Measurement(**r) for r in rows]


def get(session_id: int, name: str) -> Optional[Measurement]:
    """Return the most recent measurement for a rail/signal name."""
    rows = database.query_all(
        "SELECT * FROM measurements WHERE session_id=? AND name=? ORDER BY id DESC",
        (session_id, name),
    )
    return Measurement(**rows[0]) if rows else None


def all_named(session_id: int) -> dict:
    """Return a {name: value+unit} dict of the *latest* reading of each name."""
    out = {}
    for mm in list_for(session_id):
        out[mm.name] = (mm.value, mm.unit, mm.point)
    return out


def identify_missing_context(session_id: int) -> List[str]:
    """Flag contextual gaps that affect measurement interpretation."""
    gaps = []
    ms = list_for(session_id)
    if not ms:
        return []
    has_v = any(m.unit in ("V", "mV") for m in ms)
    has_r = any(m.unit in ("Ω", "kΩ", "ohm") for m in ms)
    if has_v:
        # Any voltage measurement should ideally note adapter/battery state.
        any_adapter = any(m.adapter_state for m in ms)
        any_ref = any(m.reference for m in ms)
        if not any_adapter:
            gaps.append(
                "Live voltage measurements present but adapter/battery state "
                "is not recorded; the interpretation depends on whether the "
                "board is being probed on adapter, battery, or both."
            )
        if not any_ref:
            gaps.append(
                "Some voltage measurements do not specify the reference "
                "point; assume ground unless stated, but confirm to compare "
                "with datasheet 'with respect to GND' values."
            )
    if has_r:
        any_boardstate = any(m.board_state for m in ms)
        if not any_boardstate:
            gaps.append(
                "Resistance measurements should be taken with power "
                "disconnected and bulk capacitors discharged; board state "
                "(powered/unpowered) is not recorded."
            )
    return gaps
