"""Engine base class and registry (the Tool Router).

Every computational engine subclasses :class:`Engine` and registers
itself with :func:`register`. The CLI/LLM calls :func:`call` by name;
the registry enforces versioning, input validation, and structured
error handling.

Design rules:

* An engine never raises; it returns an :class:`EngineResult` with an
  ``error`` field on failure. This is so the LLM orchestrator receives
  structured errors it can explain, instead of Python tracebacks.
* An engine declares its :attr:`name`, :attr:`version`, :attr:`methods`,
  and a JSON-ish :attr:`input_schema` describing the keys/types it
  expects. The base class validates inputs against the schema before
  dispatching.
* Engines are lazily registered; importing the engines package has no
  side effects. ``get``/``call`` trigger the lazy import of the engine
  module the first time it is referenced.
* No engine touches the network. Engines may read the local SQLite DB
  or local KB on demand, but they never load the whole archive.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..schemas import EngineError, EngineResult, ErrorCode, Quantity


@dataclass
class MethodSpec:
    """Declared method of an engine."""
    name: str
    summary: str
    input_schema: Dict[str, Tuple[str, bool]]  # key -> (type_name, required)
    output: str  # short description of the return shape


class Engine:
    """Subclass and set the class attributes, then implement ``run``."""
    name: str = ""
    version: str = "0.1.0"
    summary: str = ""
    methods: Dict[str, MethodSpec] = {}
    provenance: str = ""

    def run(self, method: str, inputs: Dict[str, Any]) -> EngineResult:
        raise NotImplementedError

    # ---- helpers for subclasses --------------------------------------
    def _ok(self, method: str, *, value: Any, value_si: Optional[float] = None,
            unit: str = "", inputs: Dict[str, Any],
            formulas: Optional[List[str]] = None,
            assumptions: Optional[List[str]] = None,
            limitations: Optional[List[str]] = None,
            metadata: Optional[Dict[str, Any]] = None) -> EngineResult:
        return EngineResult(
            engine=type(self).name, method=method, inputs=inputs,
            value=value, value_si=value_si, unit=unit,
            formulas=formulas or [], assumptions=assumptions or [],
            limitations=limitations or [], provenance=type(self).provenance,
            metadata=metadata or {},
        )

    def _err(self, method: str, code: ErrorCode, message: str, field: str = "",
             inputs: Optional[Dict[str, Any]] = None) -> EngineResult:
        return EngineResult(
            engine=type(self).name, method=method,
            inputs=inputs or {},
            error=EngineError(code=code, message=message, field=field),
        )


# ----------------------------------------------------------------------
# Registry / Tool Router
# ----------------------------------------------------------------------

# Map engine name -> (module_path, class_name). Engines are imported lazily.
_REGISTRY: Dict[str, Tuple[str, str]] = {}
_LOADED: Dict[str, Engine] = {}


def register(name: str, module: str, class_name: str) -> None:
    _REGISTRY[name] = (module, class_name)


def get(name: str) -> Engine:
    """Return the engine instance for ``name``, importing it on demand."""
    if name in _LOADED:
        return _LOADED[name]
    if name not in _REGISTRY:
        raise KeyError(f"unknown engine '{name}'. Registered: {list(_REGISTRY)}")
    module_path, class_name = _REGISTRY[name]
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    inst = cls()
    _LOADED[name] = inst
    return inst


def available() -> List[str]:
    return sorted(_REGISTRY.keys())


def call(name: str, method: str, inputs: Dict[str, Any]) -> EngineResult:
    """Validate inputs against the engine's declared schema, then call.

    Returns an :class:`EngineResult` envelope (never raises). On schema
    failure the envelope carries an ``invalid_input`` / ``missing_input``
    error.
    """
    try:
        engine = get(name)
    except KeyError as e:
        return EngineResult(
            engine=name, method=method, inputs=inputs,
            error=EngineError(ErrorCode.UNAVAILABLE, str(e)),
        )
    except Exception as e:
        return EngineResult(
            engine=name, method=method, inputs=inputs,
            error=EngineError(ErrorCode.INTERNAL, f"engine import failed: {e}"),
        )
    spec = engine.methods.get(method)
    if spec is None:
        return EngineResult(
            engine=name, method=method, inputs=inputs,
            error=EngineError(
                ErrorCode.NOT_APPLICABLE,
                f"engine '{name}' has no method '{method}'. "
                f"Methods: {list(engine.methods)}",
            ),
        )
    # Validate inputs against the schema.
    for key, (type_name, required) in spec.input_schema.items():
        if required and key not in inputs:
            return EngineResult(
                engine=name, method=method, inputs=inputs,
                error=EngineError(ErrorCode.MISSING_INPUT,
                                  f"missing required input '{key}'", key),
            )
        if key in inputs and not _type_ok(inputs[key], type_name):
            return EngineResult(
                engine=name, method=method, inputs=inputs,
                error=EngineError(ErrorCode.INVALID_INPUT,
                                  f"input '{key}' must be {type_name}, "
                                  f"got {type(inputs[key]).__name__}", key),
            )
    try:
        return engine.run(method, inputs)
    except Exception as e:
        return EngineResult(
            engine=name, method=method, inputs=inputs,
            error=EngineError(ErrorCode.INTERNAL, f"engine raised: {e}"),
        )


def _type_ok(value: Any, type_name: str) -> bool:
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "bool":
        return isinstance(value, bool)
    if type_name == "quantity":
        return isinstance(value, Quantity)
    if type_name == "list":
        return isinstance(value, list)
    if type_name == "dict":
        return isinstance(value, dict)
    if type_name == "any":
        return True
    return True


def reset_for_tests() -> None:
    """Clear the loaded engine cache (used by tests for isolation)."""
    _LOADED.clear()


# Built-in engine registrations. New engines register here as they are
# added. SPICE is registered too but its module reports UNAVAILABLE if
# ngspice is not installed.
register("electrical_math", "eda.engines.electrical_math", "ElectricalMathEngine")
register("measurement_validation", "eda.engines.measurement_validation", "MeasurementValidationEngine")
register("datasheet", "eda.engines.datasheet", "DatasheetEngine")
register("power_sequence", "eda.engines.power_sequence", "PowerSequenceEngine")
register("hypotheses", "eda.engines.hypotheses", "HypothesisEngine")
register("topology", "eda.engines.topology", "TopologyEngine")
register("next_best_test", "eda.engines.next_best_test", "NextBestTestEngine")
register("spice", "eda.engines.spice", "SpiceEngine")
