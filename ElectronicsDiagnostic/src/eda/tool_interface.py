"""Tool Calling Interface — the JSON protocol between the LLM and the
specialized engines.

This module sits *on top of* the existing Tool Router
(``eda.engines.base``). It adds three things the raw router does not
provide:

1. A **tool catalog** — a JSON description of every engine method the
   LLM is allowed to call (name, purpose, argument schema, return
   shape). The orchestrator injects this catalog into the LLM prompt so
   the model knows which tools exist and how to call them.
2. A **tool-call / tool-result message pair** with strict validation:
   every tool call must name a real engine method and supply the
   required inputs; every tool result carries the engine's
   ``provenance`` (where the number came from) so the LLM cannot pass an
   invented number off as an engine output.
3. **Structured errors** — engine failures come back as
   ``{"error": {"code": ..., "message": ..., "field": ...}}`` rather
   than Python tracebacks, so the LLM can explain them to the technician
   or retry with corrected inputs.

Design rules (matching the project's existing contracts):

* The LLM is *never* trusted to invent numbers, pinouts, voltages, or
  component specs. Every numeric or technical claim it cites must be
  traceable to a tool result's ``provenance`` field, or to a Knowledge
  Base fact, or to a document chunk. The orchestrator checks this.
* Tool calls and results are plain JSON (serializable, loggable,
  replayable). The same protocol works for the stub backend, a real
  llama.cpp server, and an OpenAI-compatible cloud API.
* A tool result that comes from an engine is *immutable* to the LLM;
  the LLM may paraphrase it but the ``value``/``value_si``/``unit`` and
  the ``provenance`` are preserved verbatim into the conversation
  history.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .engines import base as E
from .schemas import EngineResult, Quantity


# ---------------------------------------------------------------------------
# Tool catalog
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    """One tool the LLM may call.

    ``arg_schema`` maps arg name -> ``(type_name, required)`` exactly as
    in :class:`eda.engines.base.MethodSpec`, so the catalog is generated
    from the live registry, not hand-maintained.
    """
    name: str            # "<engine>.<method>", e.g. "electrical_math.ohm_voltage"
    purpose: str         # one-line description for the LLM
    args: Dict[str, Any] # arg_name -> {"type": ..., "required": ..., "desc": ...}
    returns: str         # short description of the return shape
    engine: str
    method: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "purpose": self.purpose,
            "args": self.args,
            "returns": self.returns,
        }


# Human-readable purpose strings per engine method. These are short
# because they go into the LLM's context window. Keep them honest: they
# describe what the engine *computes*, not what the LLM may claim.
_PURPOSE: Dict[str, str] = {
    "electrical_math.ohm_voltage": "Compute voltage V = I*R (Ohm's law).",
    "electrical_math.ohm_current": "Compute current I = V/R (Ohm's law).",
    "electrical_math.ohm_resistance": "Compute resistance R = V/I (Ohm's law).",
    "electrical_math.power_vi": "Compute power P = V*I.",
    "electrical_math.power_ir": "Compute power P = I^2*R.",
    "electrical_math.power_v2r": "Compute power P = V^2/R.",
    "electrical_math.series_resistors": "Sum resistors in series.",
    "electrical_math.parallel_resistors": "Sum resistors in parallel.",
    "electrical_math.voltage_divider": "Compute V_out = V_in * R2/(R1+R2).",
    "electrical_math.current_divider": "Compute branch current in a divider.",
    "electrical_math.kvl_loop": "Sum voltages around a loop; returns residual (should be ~0).",
    "electrical_math.kcl_node": "Sum currents at a node; returns residual (should be ~0).",
    "measurement_validation.validate": "Validate one measurement against an expected range.",
    "measurement_validation.compare": "Compare two measurements within a tolerance.",
    "measurement_validation.session_review": "Review all measurements in a session for contradictions.",
    "datasheet.lookup": "Look up stored datasheet parameters for a part number.",
    "datasheet.extract": "Extract parameters from an imported datasheet document.",
    "datasheet.list_documents_for_part": "List document ids that mention a part number.",
    "power_sequence.default_sequence": "Return the default laptop power-up sequence.",
    "power_sequence.analyze": "Analyze a session's measurements against a power sequence.",
    "power_sequence.get_sequence": "Get a named saved power sequence.",
    "hypotheses.analyze": "Run the deterministic hypothesis engine for a session.",
    "hypotheses.list": "List ranked hypotheses for a session.",
    "next_best_test.recommend": "Recommend the next most informative test for a session.",
    "topology.components_on_net": "List components on a given net in the session's topology.",
    "topology.rail_origin": "Find the origin component of a rail/net.",
    "topology.find_opens": "Find nets with no driver (possible open).",
    "topology.find_shorts": "Find nets shorted together.",
    "spice.dc_op": "Run a DC operating-point SPICE simulation.",
    "spice.available": "Report whether the SPICE backend is installed.",
}


def build_catalog(engine_names: Optional[List[str]] = None) -> List[ToolSpec]:
    """Build the tool catalog from the live registry.

    Only engines whose methods appear in ``_PURPOSE`` are exposed to the
    LLM. This is deliberate: some methods (e.g. ``hypotheses.confirm``)
    mutate the database and must not be called by the LLM autonomously.
    Engines that fail to import (e.g. test stubs left in the registry)
    are skipped rather than aborting the catalog.
    """
    specs: List[ToolSpec] = []
    for name in E.available():
        if engine_names and name not in engine_names:
            continue
        try:
            eng = E.get(name)
        except Exception:
            # Skip engines whose module cannot be imported (e.g. a test
            # stub left in the registry). Never let a bad registry entry
            # break the whole catalog.
            continue
        for method, ms in eng.methods.items():
            key = f"{name}.{method}"
            if key not in _PURPOSE:
                continue
            args: Dict[str, Any] = {}
            for arg, (type_name, required) in ms.input_schema.items():
                args[arg] = {"type": type_name, "required": required}
            specs.append(ToolSpec(
                name=key, purpose=_PURPOSE[key],
                args=args, returns=ms.output,
                engine=name, method=method,
            ))
    return specs


def catalog_as_prompt_text(specs: Optional[List[ToolSpec]] = None) -> str:
    """Render the catalog as text for the LLM system prompt."""
    if specs is None:
        specs = build_catalog()
    lines = ["You may call the following tools. Each call must be a JSON "
             "object with \"tool\" and \"args\". Only call tools listed here.",
             ""]
    for s in specs:
        arg_strs = []
        for a, meta in s.args.items():
            tag = f"{a}:{meta['type']}" + ("*" if meta["required"] else "")
            arg_strs.append(tag)
        lines.append(f"- {s.name}({', '.join(arg_strs)}) -> {s.returns}")
        lines.append(f"    {s.purpose}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool-call / tool-result messages
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """A tool call emitted by the LLM.

    ``args`` may contain ``Quantity`` objects (parsed from the LLM's
    JSON by :func:`coerce_args`) or plain scalars. ``id`` ties the call
    to its result for replay and logging.
    """
    tool: str            # "<engine>.<method>"
    args: Dict[str, Any]
    id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"tool": self.tool, "args": _jsonable(self.args), "id": self.id}


@dataclass
class ToolResult:
    """The structured result handed back to the LLM.

    ``provenance`` is mandatory for any technical value: it tells the
    LLM (and the technician) where the number came from. The orchestrator
    refuses to let the LLM assert a technical claim without a matching
    provenance in the conversation history.
    """
    tool: str
    id: str
    ok: bool
    value: Any = None                 # jsonable engine value (or None on error)
    unit: str = ""
    provenance: str = ""
    formulas: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    error: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool, "id": self.id, "ok": self.ok,
            "value": _jsonable(self.value), "unit": self.unit,
            "provenance": self.provenance,
            "formulas": self.formulas, "assumptions": self.assumptions,
            "limitations": self.limitations,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch(call: ToolCall) -> ToolResult:
    """Execute one tool call via the Tool Router; return a ToolResult.

    The LLM's raw JSON args are coerced into the types the engine
    expects (e.g. ``{"value": 19.4, "unit": "V"}`` -> ``Quantity``).
    Engine errors are preserved as structured dicts, never raised.
    """
    if "." not in call.tool:
        return _err_result(call, "invalid_input",
                           f"tool name must be '<engine>.<method>', got '{call.tool}'")
    engine_name, method = call.tool.split(".", 1)
    # Coerce args: turn {"value":..., "unit":...} dicts into Quantity.
    coerced = coerce_args(call.args, engine_name, method)
    if isinstance(coerced, dict) and coerced.get("__coerce_error"):
        code = coerced.get("__code", "invalid_input")
        return _err_result(call, code, coerced["__coerce_error"],
                           field=coerced.get("__field", ""))
    result: EngineResult = E.call(engine_name, method, coerced)
    if not result.ok:
        return ToolResult(
            tool=call.tool, id=call.id, ok=False,
            error=result.error.to_dict() if result.error else
                  {"code": "internal", "message": "unknown engine failure"},
        )
    return ToolResult(
        tool=call.tool, id=call.id, ok=True,
        value=_jsonable(result.value),
        unit=result.unit,
        provenance=result.provenance or f"engine:{engine_name}.{method}",
        formulas=list(result.formulas),
        assumptions=list(result.assumptions),
        limitations=list(result.limitations),
    )


def coerce_args(args: Dict[str, Any], engine_name: str, method: str) -> Dict[str, Any]:
    """Coerce LLM-supplied args into the types an engine method expects.

    For ``quantity`` typed args the LLM may pass either a Quantity (if
    we already parsed one), a ``{"value": v, "unit": u}`` dict, or a
    string like ``"19.4V"``. Numbers and strings are accepted for
    ``number`` args; lists/dicts pass through.

    Returns a dict ready for :func:`eda.engines.base.call`, or
    ``{"__coerce_error": msg, "__field": name}`` on failure.
    """
    try:
        engine = E.get(engine_name)
    except KeyError:
        return {"__coerce_error": f"unknown engine '{engine_name}'",
                "__code": "unavailable"}
    spec = engine.methods.get(method)
    if spec is None:
        return {"__coerce_error": f"engine '{engine_name}' has no method '{method}'",
                "__code": "not_applicable"}
    out: Dict[str, Any] = {}
    for key, (type_name, required) in spec.input_schema.items():
        if key not in args:
            if required:
                return {"__coerce_error": f"missing required arg '{key}'",
                        "__field": key,
                        "__code": "missing_input"}
            continue
        v = args[key]
        if isinstance(v, Quantity):
            out[key] = v
            continue
        if type_name == "quantity":
            try:
                out[key] = _to_quantity(v)
            except (ValueError, TypeError) as e:
                return {"__coerce_error": f"arg '{key}' must be a quantity: {e}",
                        "__field": key,
                        "__code": "invalid_input"}
        elif type_name == "number":
            if isinstance(v, bool):
                return {"__coerce_error": f"arg '{key}' must be a number, got bool",
                        "__field": key,
                        "__code": "invalid_input"}
            try:
                out[key] = float(v) if not isinstance(v, int) else v
            except (ValueError, TypeError) as e:
                return {"__coerce_error": f"arg '{key}' must be a number: {e}",
                        "__field": key,
                        "__code": "invalid_input"}
        elif type_name == "string":
            out[key] = str(v)
        elif type_name == "bool":
            out[key] = bool(v)
        elif type_name == "list":
            out[key] = v if isinstance(v, list) else [v]
        elif type_name == "dict":
            out[key] = v if isinstance(v, dict) else {}
        else:  # "any"
            out[key] = v
    return out


def _to_quantity(v: Any) -> Quantity:
    if isinstance(v, Quantity):
        return v
    if isinstance(v, dict):
        return Quantity.parse(v["value"], v.get("unit", ""))
    if isinstance(v, (int, float)):
        raise ValueError("quantity needs a unit; pass {\"value\": v, \"unit\": u}")
    if isinstance(v, str):
        return Quantity.parse(v)
    raise ValueError(f"cannot make a Quantity from {type(v).__name__}")


# ---------------------------------------------------------------------------
# Parsing LLM output into tool calls
# ---------------------------------------------------------------------------

def parse_tool_calls(text: str) -> List[ToolCall]:
    """Parse one or more tool calls out of the LLM's raw text output.

    The orchestrator asks the LLM to emit tool calls as fenced JSON:
    `````tool_call``` blocks (one JSON object per block) or a single
    JSON object with a ``"calls"`` list. Tolerant parser: it scans for
    the first balanced JSON object/block that looks like a tool call.

    Malformed JSON blocks are NOT silently dropped: they are recorded on
    ``parse_tool_calls.last_malformed`` (a module-level list) so the
    orchestrator can surface them to the model/user rather than looping
    indefinitely on unparseable output. This is a reliability fix — a
    silent drop made a broken model output indistinguishable from "no
    tool call, here is the final answer".
    """
    global last_malformed
    last_malformed = []
    calls: List[ToolCall] = []
    # Fenced ```tool_call blocks first (preferred).
    import re
    blocks = re.findall(r"```tool_call\s*(.*?)```", text, flags=re.DOTALL)
    if not blocks:
        # Fenced ```json blocks: capture the whole inner content (which
        # may contain nested braces) up to the closing fence.
        blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    for b in blocks:
        try:
            obj = json.loads(b)
        except json.JSONDecodeError as e:
            # Record the malformed block + the parser error so the
            # orchestrator can surface it. Truncate to avoid huge logs.
            last_malformed.append({
                "block": b.strip()[:120],
                "error": str(e)[:80],
            })
            continue
        _collect_calls(obj, calls)
    if not calls:
        # Last resort: try the whole text as JSON.
        try:
            obj = json.loads(text)
            _collect_calls(obj, calls)
        except json.JSONDecodeError as e:
            # Only record as malformed if the text actually looks like
            # it was *trying* to be a tool call (contains "tool" or a
            # fenced block). Prose final answers must not be flagged.
            if "tool" in text.lower()[:200] or blocks:
                last_malformed.append({
                    "block": text.strip()[:120],
                    "error": str(e)[:80],
                })
    return calls


# Module-level record of the most recent parse's malformed blocks.
# The orchestrator reads this to surface parse failures. It is reset on
# every call to parse_tool_calls.
last_malformed: List[Dict[str, str]] = []


def _collect_calls(obj: Any, out: List[ToolCall]) -> None:
    if isinstance(obj, list):
        for o in obj:
            _collect_calls(o, out)
        return
    if not isinstance(obj, dict):
        return
    if "calls" in obj and isinstance(obj["calls"], list):
        for o in obj["calls"]:
            _collect_calls(o, out)
        return
    if "tool" in obj and isinstance(obj["tool"], str):
        out.append(ToolCall(
            tool=obj["tool"],
            args=obj.get("args", {}) or obj.get("arguments", {}) or {},
            id=str(obj.get("id", "")),
        ))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jsonable(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, Quantity):
        return {"magnitude": v.magnitude, "unit": v.display_unit or "SI",
                "dimension": str(v.dimension)}
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    # dataclass with to_dict
    if hasattr(v, "to_dict") and callable(v.to_dict):
        try:
            return v.to_dict()
        except Exception:
            return str(v)
    return str(v)


def _err_result(call: ToolCall, code: str, message: str,
                field: str = "") -> ToolResult:
    return ToolResult(
        tool=call.tool, id=call.id, ok=False,
        error={"code": code, "message": message, "field": field},
    )
