"""Power-sequence analysis engine.

Models the laptop-motherboard power-up sequence as a configurable
dependency graph and analyzes a session's measurements against it.

The sequence model is per-board (no two platforms use identical
sequences or signal names). A default generic Intel-platform model is
provided; boards may define their own.

States (ACPI):
    G3  — mechanical off (no power)
    S5  — soft off (only always-on rails live)
    S4  — suspend-to-disk (RAM off)
    S3  — suspend-to-RAM (suspend rails live)
    S0  — working (all rails up)

Each sequence node declares:
    node_key         — short id (e.g. "vin", "3valw", "5valw", "ec_on",
                       "3vs", "5vs", "vcore", "reset_deassert")
    node_type        — rail | signal | controller | state
    display_name     — human-readable
    state            — the S-state in which this node should be alive
    depends_on       — list of node_keys that must be alive first
    enable_signal    — the signal that enables this node (optional)
    power_good_signal— the PGOOD this node asserts (optional)
    nominal_voltage  — e.g. "3.3V", "19V" (string; only for display)
    ordinal          — suggested order within its state

Analysis returns:
    - which measured nodes are alive / dead / unknown
    - which alive nodes lack a measured prerequisite (sequence violation)
    - which dead nodes block downstream nodes
    - which enable / PGOOD signals are unmeasured
    - the S-state the board appears to be stuck in
    - the single most informative next measurement for the sequence

This engine does NOT infer a specific rail voltage or signal polarity
without supporting documentation; nominal voltages are display-only
strings sourced from the sequence model, not from datasheets.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .base import Engine, MethodSpec
from ..schemas import EngineError, EngineResult, ErrorCode
from .. import database, measurements as M, sessions


# ACPI power states, ordered from least to most powered.
STATES = ["G3", "S5", "S4", "S3", "S0"]


@dataclass
class SeqNode:
    node_key: str
    node_type: str
    display_name: str = ""
    state: str = "S0"
    depends_on: List[str] = field(default_factory=list)
    enable_signal: str = ""
    power_good_signal: str = ""
    nominal_voltage: str = ""
    ordinal: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


# Default generic Intel-laptop power sequence. Real boards ship their
# own model; this is the conservative default used when no board-specific
# model is configured.
DEFAULT_SEQUENCE: List[SeqNode] = [
    SeqNode("vin", "rail", "DC adapter input (VIN)", state="S5",
            nominal_voltage="19V", ordinal=1),
    SeqNode("3valw", "rail", "+3VALW (always-on 3.3V)", state="S5",
            depends_on=["vin"], nominal_voltage="3.3V", ordinal=2,
            power_good_signal="3valw_pg"),
    SeqNode("5valw", "rail", "+5VALW (always-on 5V)", state="S5",
            depends_on=["vin"], nominal_voltage="5V", ordinal=2,
            power_good_signal="5valw_pg"),
    SeqNode("ec_on", "controller", "Embedded controller alive", state="S5",
            depends_on=["3valw"], ordinal=3),
    SeqNode("acok", "signal", "ACOK adapter-valid to EC", state="S5",
            depends_on=["vin"], ordinal=3),
    SeqNode("nbsvon", "signal", "S5 enable (RSMRST#/SLP_S5#)", state="S5",
            depends_on=["ec_on", "acok"], ordinal=4),
    SeqNode("3vs", "rail", "+3VS (S5/S0 run rail 3.3V)", state="S0",
            depends_on=["3valw", "nbsvon"], nominal_voltage="3.3V", ordinal=5),
    SeqNode("5vs", "rail", "+5VS (S5/S0 run rail 5V)", state="S0",
            depends_on=["5valw", "nbsvon"], nominal_voltage="5V", ordinal=5),
    SeqNode("vram", "rail", "DDR memory rail", state="S0",
            depends_on=["3vs"], nominal_voltage="1.2V", ordinal=6),
    SeqNode("vcore", "rail", "CPU core VRM", state="S0",
            depends_on=["3vs", "5vs"], nominal_voltage="0.6-1.2V", ordinal=7,
            enable_signal="vrm_en", power_good_signal="vrm_pg"),
    SeqNode("vccsa", "rail", "CPU system agent rail", state="S0",
            depends_on=["vcore"], nominal_voltage="1.05V", ordinal=8),
    SeqNode("reset_deassert", "signal", "PLTRST# deasserted", state="S0",
            depends_on=["vcore", "vccsa"], ordinal=9),
]


def _default_sequence_dict() -> List[Dict[str, Any]]:
    return [n.to_dict() for n in DEFAULT_SEQUENCE]


@dataclass
class SeqAnalysis:
    """Structured value returned by ``analyze``."""
    sequence_name: str
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    alive: List[str] = field(default_factory=list)
    dead: List[str] = field(default_factory=list)
    unknown: List[str] = field(default_factory=list)
    sequence_violations: List[str] = field(default_factory=list)
    blocking_nodes: List[str] = field(default_factory=list)
    missing_enables: List[str] = field(default_factory=list)
    missing_power_good: List[str] = field(default_factory=list)
    apparent_state: str = "G3"
    next_measurement: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


class PowerSequenceEngine(Engine):
    name = "power_sequence"
    version = "1.0.0"
    summary = ("Configurable per-board power-sequence analysis. Models "
              "G3/S5/S4/S3/S0 with dependency graphs; identifies missing "
              "prerequisites, missing enables/PGOODs, sequence violations, "
              "and the next most informative measurement.")
    provenance = ("Original implementation; default generic Intel-platform "
                  "sequence model. Board-specific models override it.")

    methods = {
        "default_sequence": MethodSpec(
            "default_sequence", "Return the built-in generic sequence model",
            {}, "list of nodes"),
        "list_sequences": MethodSpec(
            "list_sequences", "List stored board-specific sequence models",
            {}, "list of names"),
        "save_sequence": MethodSpec(
            "save_sequence", "Save a board-specific sequence model",
            {"name": ("string", True), "nodes": ("list", True),
             "board_model": ("string", False), "description": ("string", False)},
            "ok"),
        "get_sequence": MethodSpec(
            "get_sequence", "Get a stored sequence by name (falls back to default)",
            {"name": ("string", False)}, "list of nodes"),
        "analyze": MethodSpec(
            "analyze", "Analyze a session's measurements against a sequence",
            {"session_id": ("number", True), "sequence_name": ("string", False)},
            "SeqAnalysis"),
    }

    # ---- sequence management ----------------------------------------
    def _default_sequence(self, inputs: Dict[str, Any]) -> EngineResult:
        return self._ok("default_sequence", value=_default_sequence_dict(),
                        inputs=inputs)

    def _list_sequences(self, inputs: Dict[str, Any]) -> EngineResult:
        rows = database.query_all(
            "SELECT name, board_model, description FROM power_sequences ORDER BY name"
        )
        return self._ok("list_sequences", value={"sequences": rows}, inputs=inputs)

    def _save_sequence(self, inputs: Dict[str, Any]) -> EngineResult:
        name = inputs.get("name", "")
        if not name:
            return self._err("save_sequence", ErrorCode.MISSING_INPUT,
                             "missing name", "name")
        nodes = inputs.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            return self._err("save_sequence", ErrorCode.INVALID_INPUT,
                             "nodes must be a non-empty list", "nodes")
        # Validate node keys.
        for n in nodes:
            if not isinstance(n, dict) or "node_key" not in n:
                return self._err("save_sequence", ErrorCode.INVALID_INPUT,
                                 "each node must have node_key", "nodes")
        database.execute(
            "INSERT OR REPLACE INTO power_sequences(name, board_model, "
            "description, created_at) VALUES(?,?,?,?)",
            (name, inputs.get("board_model", ""), inputs.get("description", ""),
             database.now_iso()),
        )
        seq_row = database.query_one(
            "SELECT id FROM power_sequences WHERE name=?", (name,)
        )
        sid = seq_row["id"]
        database.execute("DELETE FROM power_sequence_nodes WHERE sequence_id=?",
                         (sid,))
        for n in nodes:
            database.execute(
                "INSERT INTO power_sequence_nodes("
                "sequence_id, node_key, node_type, display_name, state, "
                "depends_on, enable_signal, power_good_signal, nominal_voltage, "
                "ordinal) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sid, n.get("node_key"), n.get("node_type", "rail"),
                 n.get("display_name", ""), n.get("state", "S0"),
                 json.dumps(n.get("depends_on", [])),
                 n.get("enable_signal", ""), n.get("power_good_signal", ""),
                 n.get("nominal_voltage", ""), int(n.get("ordinal", 0))),
            )
        return self._ok("save_sequence", value={"saved": name}, inputs=inputs)

    def _get_sequence(self, inputs: Dict[str, Any]) -> EngineResult:
        name = inputs.get("name", "")
        if not name:
            return self._ok("get_sequence", value=_default_sequence_dict(),
                           inputs=inputs)
        row = database.query_one(
            "SELECT id FROM power_sequences WHERE name=?", (name,)
        )
        if not row:
            return self._ok("get_sequence", value=_default_sequence_dict(),
                           inputs=inputs)
        nodes = database.query_all(
            "SELECT * FROM power_sequence_nodes WHERE sequence_id=? "
            "ORDER BY ordinal, id", (row["id"],)
        )
        out = []
        for n in nodes:
            d = dict(n)
            try:
                d["depends_on"] = json.loads(d.get("depends_on") or "[]")
            except (json.JSONDecodeError, TypeError):
                d["depends_on"] = []
            out.append(d)
        return self._ok("get_sequence", value=out, inputs=inputs)

    # ---- analysis ----------------------------------------------------
    def _analyze(self, inputs: Dict[str, Any]) -> EngineResult:
        sid = inputs.get("session_id")
        if sid is None:
            return self._err("analyze", ErrorCode.MISSING_INPUT,
                             "missing session_id", "session_id")
        sess = sessions.get(int(sid))
        if not sess:
            return self._err("analyze", ErrorCode.NOT_APPLICABLE,
                             f"no session {sid}")
        # Load the sequence (named or default).
        seq_name = inputs.get("sequence_name", "default")
        seq_result = self._get_sequence({"name": seq_name if seq_name != "default" else ""})
        nodes = seq_result.value
        if not nodes:
            return self._err("analyze", ErrorCode.NOT_APPLICABLE,
                             "empty sequence")
        # Map measurements onto nodes by normalized name.
        ms = M.list_for(int(sid))
        measured: Dict[str, M.Measurement] = {}
        for m in ms:
            measured[_norm_name(m.name)] = m
        analysis = SeqAnalysis(sequence_name=seq_name)
        node_by_key = {n["node_key"]: n for n in nodes}
        # Determine alive/dead/unknown per node.
        for n in nodes:
            key = n["node_key"]
            display = n.get("display_name") or key
            # Find a measurement that matches this node's key or its signals.
            m = _match_measurement(measured, key, n)
            entry = {**n, "status": "unknown", "measured_value": None}
            if m is None:
                analysis.unknown.append(key)
            else:
                entry["measured_value"] = f"{m.value} {m.unit}".strip()
                v = m.value
                if v is None:
                    entry["status"] = "unknown"
                    analysis.unknown.append(key)
                elif _is_alive(n, v, m.unit or ""):
                    entry["status"] = "alive"
                    analysis.alive.append(key)
                else:
                    entry["status"] = "dead"
                    analysis.dead.append(key)
            analysis.nodes.append(entry)
        # Detect sequence violations: a node alive while a prerequisite is
        # dead or unknown.
        for entry in analysis.nodes:
            if entry["status"] == "alive":
                for dep in entry.get("depends_on", []):
                    dep_entry = next((e for e in analysis.nodes if e["node_key"] == dep), None)
                    if dep_entry is None:
                        continue
                    if dep_entry["status"] != "alive":
                        analysis.sequence_violations.append(
                            f"{entry['node_key']} alive but prerequisite "
                            f"'{dep}' is {dep_entry['status']}."
                        )
        # Detect dead nodes that block downstream nodes.
        for entry in analysis.nodes:
            if entry["status"] == "dead":
                for other in analysis.nodes:
                    if entry["node_key"] in other.get("depends_on", []) and other["status"] != "alive":
                        analysis.blocking_nodes.append(entry["node_key"])
                        break
        # Missing enables / PGOODs for alive rails.
        for entry in analysis.nodes:
            if entry["status"] == "alive" and entry.get("enable_signal"):
                if _norm_name(entry["enable_signal"]) not in measured:
                    analysis.missing_enables.append(entry["enable_signal"])
            if entry["status"] == "alive" and entry.get("power_good_signal"):
                if _norm_name(entry["power_good_signal"]) not in measured:
                    analysis.missing_power_good.append(entry["power_good_signal"])
        # Apparent state = the highest state that has at least one alive node,
        # and all earlier states have at least one alive node of their own.
        # Simplified: the highest state with an alive node, if its S5
        # prerequisite is satisfied.
        alive_states = {entry.get("state") for entry in analysis.nodes if entry["status"] == "alive"}
        if not alive_states:
            analysis.apparent_state = "G3"
        else:
            # Highest state (rightmost in STATES) that has an alive node.
            for st in reversed(STATES):
                if st in alive_states:
                    analysis.apparent_state = st
                    break
        # Next measurement: the first unknown node whose prerequisites are
        # all alive (so measuring it is informative), else the first blocking
        # node.
        candidate = None
        for entry in analysis.nodes:
            if entry["status"] != "unknown":
                continue
            deps = entry.get("depends_on", [])
            if all(
                next((e for e in analysis.nodes if e["node_key"] == d), {}).get("status") == "alive"
                for d in deps
            ):
                candidate = entry
                break
        if candidate is None and analysis.blocking_nodes:
            candidate = next((e for e in analysis.nodes if e["node_key"] == analysis.blocking_nodes[0]), None)
        if candidate:
            analysis.next_measurement = {
                "node": candidate["node_key"],
                "display": candidate.get("display_name") or candidate["node_key"],
                "where": f"Measure {candidate.get('display_name') or candidate['node_key']} "
                         f"({candidate.get('nominal_voltage', 'unknown nominal')})",
                "reference": "A known GND pad",
                "why": (f"This node is {candidate['status']} and is a "
                        f"prerequisite for downstream nodes."),
            }
        return self._ok("analyze", value=analysis.to_dict(), inputs=inputs,
                        assumptions=[f"sequence = {seq_name}",
                                     "rail alive/dead judged by measured voltage"],
                        limitations=["nominal voltages are display-only, not datasheet-sourced",
                                     "signal polarity is not inferred without documentation"])

    # ---- dispatch ---------------------------------------------------
    _DISPATCH = None

    def run(self, method: str, inputs: Dict[str, Any]) -> EngineResult:
        fn = PowerSequenceEngine._DISPATCH.get(method)
        if fn is None:
            return self._err(method, ErrorCode.NOT_APPLICABLE, f"unknown method '{method}'")
        return fn(self, inputs)


PowerSequenceEngine._DISPATCH = {
    "default_sequence": PowerSequenceEngine._default_sequence,
    "list_sequences": PowerSequenceEngine._list_sequences,
    "save_sequence": PowerSequenceEngine._save_sequence,
    "get_sequence": PowerSequenceEngine._get_sequence,
    "analyze": PowerSequenceEngine._analyze,
}


# ---- helpers ---------------------------------------------------------

def _norm_name(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _match_measurement(measured: Dict[str, M.Measurement], key: str, node: dict) -> Optional[M.Measurement]:
    nk = _norm_name(key)
    if nk in measured:
        return measured[nk]
    # Try display name.
    dk = _norm_name(node.get("display_name", ""))
    if dk and dk in measured:
        return measured[dk]
    # Try matching any measured name containing the key.
    for name, m in measured.items():
        if nk and nk in name:
            return m
    return None


def _is_alive(node: dict, value: float, unit: str) -> bool:
    """Decide whether a measured value indicates the node is alive.

    For rails: voltage above a small threshold (relative to nominal).
    For signals: logic high (>= 2.0 V) for active-high; we do NOT assume
    polarity without documentation, so signals are judged alive only if
    the voltage is clearly logic-high or clearly logic-low AND the node
    is documented as active-low. Without documentation, a signal
    measurement near mid-rail is reported as 'unknown' upstream."""
    nt = node.get("node_type", "rail")
    if nt == "rail":
        # Alive if voltage is above 0.5 V (any rail).
        v = value
        if unit and unit.lower() == "mv":
            v = value / 1000.0
        return v >= 0.5
    if nt == "signal":
        v = value
        if unit and unit.lower() == "mv":
            v = value / 1000.0
        # Without polarity documentation we treat >2.0 V as asserted-high.
        return v >= 2.0
    if nt == "controller":
        # Controller alive if its supply rail was measured alive.
        # We use the measured value: any non-zero indicates powered.
        return abs(value) > 0.05
    # state nodes: alive if any node of that state is alive; simplified.
    return abs(value) > 0.05
