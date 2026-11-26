"""Common schemas for the Electronics Diagnostic Assistant.

This module is the single source of truth for physical quantities,
identifiers, and structured engine results. Every computational engine
in ``eda.engines`` accepts and returns only the types defined here.

Design rules (enforced by the type system and by tests):

* SI base units are used internally. Engineering prefixes are supported
  on input and rendered on output, but arithmetic is always done in the
  SI base.
* A :class:`Quantity` carries a magnitude and a :class:`Unit` with a
  physical dimension (``Dimension``). Adding two quantities of different
  dimensions raises :class:`DimensionalError`. Multiplying quantities
  multiplies dimensions.
* Identifiers (:class:`ComponentRef`, :class:`PinRef`, :class:`NetRef`)
  are typed wrappers around strings so an engine cannot accidentally
  pass a net name where a component designator is expected.
* Every engine returns an :class:`EngineResult` envelope with a
  version, the inputs it actually used (provenance), the calculation
  method, assumptions, limitations, and either a value or a structured
  error. Engines never return bare floats.
* :class:`EngineError` is structured (code, message, field) so the LLM
  orchestrator can explain it to the technician instead of crashing.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union


# ----------------------------------------------------------------------
# Dimensions and units
# ----------------------------------------------------------------------

class DimensionalError(ValueError):
    """Raised when two quantities of incompatible dimensions are combined."""


# Backwards/typo-tolerant alias.
DimensionError = DimensionalError


class _Dim:
    """A physical dimension expressed in SI base units.

    The seven SI bases relevant here are length (m), mass (kg), time (s),
    current (A), temperature (K), and the derived luminous intensity and
    amount of substance are not used in this project. Dimensions are
    represented as a tuple of exponents in the order
    ``(kg, m, s, A, K)``. Pure numbers have the zero tuple.
    """

    __slots__ = ("kg", "m", "s", "a", "k")

    def __init__(self, kg: int = 0, m: int = 0, s: int = 0, a: int = 0, k: int = 0):
        self.kg = kg; self.m = m; self.s = s; self.a = a; self.k = k

    # Convenience constructors for the dimensions we use.
    @classmethod
    def dimensionless(cls): return cls()
    @classmethod
    def voltage(cls): return cls(kg=1, m=2, s=-3, a=-1)        # V = kg·m²·s⁻³·A⁻¹
    @classmethod
    def current(cls): return cls(a=1)                            # A
    @classmethod
    def resistance(cls): return cls(kg=1, m=2, s=-3, a=-2)       # Ω = V/A
    @classmethod
    def power(cls): return cls(kg=1, m=2, s=-3)                  # W
    @classmethod
    def capacitance(cls): return cls(kg=-1, m=-2, s=4, a=2)      # F = s⁴·A²/(kg·m²)
    @classmethod
    def inductance(cls): return cls(kg=1, m=2, s=-2, a=-2)       # H = kg·m²·s⁻²·A⁻²
    @classmethod
    def frequency(cls): return cls(s=-1)                         # Hz = s⁻¹
    @classmethod
    def time(cls): return cls(s=1)                               # s
    @classmethod
    def temperature(cls): return cls(k=1)                        # K

    def _key(self) -> Tuple[int, int, int, int, int]:
        return (self.kg, self.m, self.s, self.a, self.k)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Dim) and self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def __mul__(self, other: "_Dim") -> "_Dim":
        return _Dim(
            kg=self.kg + other.kg, m=self.m + other.m, s=self.s + other.s,
            a=self.a + other.a, k=self.k + other.k,
        )

    def __truediv__(self, other: "_Dim") -> "_Dim":
        return _Dim(
            kg=self.kg - other.kg, m=self.m - other.m, s=self.s - other.s,
            a=self.a - other.a, k=self.k - other.k,
        )

    def __pow__(self, n: int) -> "_Dim":
        return _Dim(
            kg=self.kg * n, m=self.m * n, s=self.s * n, a=self.a * n, k=self.k * n,
        )

    def __repr__(self) -> str:
        return f"Dim(kg={self.kg}, m={self.m}, s={self.s}, a={self.a}, k={self.k})"

    def name(self) -> str:
        """Human-readable name for common dimensions."""
        table = {
            _Dim.dimensionless()._key(): "dimensionless",
            _Dim.voltage()._key(): "voltage",
            _Dim.current()._key(): "current",
            _Dim.resistance()._key(): "resistance",
            _Dim.power()._key(): "power",
            _Dim.capacitance()._key(): "capacitance",
            _Dim.inductance()._key(): "inductance",
            _Dim.frequency()._key(): "frequency",
            _Dim.time()._key(): "time",
            _Dim.temperature()._key(): "temperature",
        }
        return table.get(self._key(), repr(self))


# SI prefixes and their factors (relative to the base unit).
_PREFIXES: Dict[str, float] = {
    "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
    "m": 1e-3, "": 1.0, "c": 1e-2, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12,
}


# Canonical unit table: symbol -> (Dimension, SI-base symbol, factor to SI base).
# Everything is normalized to the SI base before arithmetic.
_UNITS: Dict[str, Tuple[_Dim, str, float]] = {
    # voltage
    "V": (_Dim.voltage(), "V", 1.0),
    # current
    "A": (_Dim.current(), "A", 1.0),
    # resistance
    "Ω": (_Dim.resistance(), "Ω", 1.0),
    "ohm": (_Dim.resistance(), "Ω", 1.0),
    # power
    "W": (_Dim.power(), "W", 1.0),
    # capacitance
    "F": (_Dim.capacitance(), "F", 1.0),
    # inductance
    "H": (_Dim.inductance(), "H", 1.0),
    # frequency
    "Hz": (_Dim.frequency(), "Hz", 1.0),
    # time
    "s": (_Dim.time(), "s", 1.0),
    # temperature
    "K": (_Dim.temperature(), "K", 1.0),
    "C": (_Dim.temperature(), "K", 1.0),  # treated as delta-K for arithmetic
}


def _split_prefix(unit: str) -> Tuple[str, str]:
    """Split 'mV' -> ('m', 'V'); 'Hz' -> ('', 'Hz'); 'kΩ' -> ('k', 'Ω')."""
    if not unit:
        return "", ""
    # Special multi-char units first.
    for base in ("Hz", "ohm", "Ω"):
        if unit.endswith(base):
            return unit[: len(unit) - len(base)], base
    # Two single-char bases that need care: 'A','V','F','H','W','K','C','s'
    # Take the last character as the base.
    return unit[:-1], unit[-1]


def parse_unit(raw: str) -> Tuple[_Dim, str, float]:
    """Parse a unit string into (Dimension, si_base_symbol, factor_to_SI).

    Raises ValueError on unknown units. The factor multiplies a value
    expressed in ``raw`` to get the value in the SI base.
    """
    if raw is None:
        raise ValueError("missing unit")
    s = raw.strip()
    if not s:
        raise ValueError("empty unit")
    prefix, base = _split_prefix(s)
    if prefix not in _PREFIXES:
        raise ValueError(f"unknown prefix '{prefix}' in unit '{raw}'")
    if base not in _UNITS:
        raise ValueError(f"unknown unit base '{base}' in unit '{raw}'")
    dim, si, _ = _UNITS[base]
    return dim, si, _PREFIXES[prefix]


@dataclass(frozen=True)
class Quantity:
    """A physical quantity: magnitude + dimension. Magnitude is always in
    the SI base unit. Use :meth:`parse` to construct from a (number, unit)
    pair; use :meth:`to` to render in a prefixed unit."""
    magnitude: float
    dimension: _Dim
    # The display unit is informational only; arithmetic ignores it.
    display_unit: str = ""

    # ---- construction ------------------------------------------------
    @classmethod
    def parse(cls, value: Union[float, int, str], unit: str = "") -> "Quantity":
        """Parse ``Quantity.parse(19.4, "V")`` or ``Quantity.parse("19.4V")``.

        Negative and zero values are accepted where physically meaningful
        (e.g. negative voltage across a diode); the engine that consumes
        the Quantity is responsible for rejecting unphysical values.
        """
        if isinstance(value, str):
            m = re.match(r"^\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([A-Za-zµμΩ]+)?\s*$", value)
            if not m:
                raise ValueError(f"cannot parse quantity from '{value}'")
            magnitude = float(m.group(1))
            unit = m.group(2) or unit
        else:
            magnitude = float(value)
        if not unit:
            raise ValueError("unit required to construct a Quantity")
        dim, si, factor = parse_unit(unit)
        # Overflow/underflow guard: if magnitude is finite and factor is
        # tiny, the product might overflow to inf. Detect and raise.
        if magnitude != 0.0 and not math.isfinite(magnitude * factor):
            raise ValueError(f"magnitude {magnitude} * factor {factor} overflows")
        return cls(magnitude=magnitude * factor, dimension=dim, display_unit=unit)

    @classmethod
    def from_record(cls, value: Optional[float], unit: str) -> "Quantity":
        """Build from a DB row's (value, unit). value=None -> raises."""
        if value is None:
            raise ValueError("missing value")
        return cls.parse(value, unit)

    # ---- rendering ---------------------------------------------------
    def to(self, unit: str) -> "Quantity":
        """Return a new Quantity with ``display_unit=unit`` (magnitude
        stays in SI base). Used so the renderer can pick the prefix."""
        # Validate the unit exists and matches the dimension.
        dim, si, factor = parse_unit(unit)
        if dim != self.dimension:
            raise DimensionalError(
                f"cannot render {self.dimension.name()} as '{unit}' "
                f"(that unit has dimension {dim.name()})"
            )
        return Quantity(self.magnitude, self.dimension, unit)

    def render(self, unit: Optional[str] = None) -> str:
        """Render as ``"<value><unit>"`` with a sensible prefix."""
        target = unit or self.display_unit or _default_unit_for(self.dimension)
        dim, si, factor = parse_unit(target)
        if dim != self.dimension:
            raise DimensionalError(
                f"cannot render {self.dimension.name()} as '{target}' "
                f"(that unit has dimension {dim.name()})"
            )
        # value in the requested unit
        val = self.magnitude / factor
        # strip trailing zeros for clean output
        if val == int(val):
            s = f"{int(val)}"
        else:
            s = f"{val:.6g}"
        return f"{s}{target}"

    # ---- arithmetic --------------------------------------------------
    def _check(self, other: "Quantity", op: str) -> None:
        if self.dimension != other.dimension:
            raise DimensionalError(
                f"cannot {op} {self.dimension.name()} and {other.dimension.name()}"
            )

    def __add__(self, other: "Quantity") -> "Quantity":
        self._check(other, "add")
        return Quantity(self.magnitude + other.magnitude, self.dimension, self.display_unit or other.display_unit)

    def __sub__(self, other: "Quantity") -> "Quantity":
        self._check(other, "subtract")
        return Quantity(self.magnitude - other.magnitude, self.dimension, self.display_unit or other.display_unit)

    def __mul__(self, other: "Quantity") -> "Quantity":
        if not isinstance(other, Quantity):
            return Quantity(self.magnitude * float(other), self.dimension, self.display_unit)
        return Quantity(self.magnitude * other.magnitude, self.dimension * other.dimension)

    def __rmul__(self, other: "Quantity") -> "Quantity":
        return self.__mul__(other)

    def __truediv__(self, other: "Quantity") -> "Quantity":
        if not isinstance(other, Quantity):
            return Quantity(self.magnitude / float(other), self.dimension, self.display_unit)
        return Quantity(self.magnitude / other.magnitude, self.dimension / other.dimension)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        return self.dimension == other.dimension and math.isclose(self.magnitude, other.magnitude, rel_tol=1e-12, abs_tol=1e-15)

    def __hash__(self) -> int:
        return hash((self.magnitude, self.dimension))

    def __repr__(self) -> str:
        return f"Quantity({self.render()})"

    def is_zero(self, tol: float = 1e-9) -> bool:
        return abs(self.magnitude) <= tol


def _default_unit_for(dim: _Dim) -> str:
    table = {
        _Dim.voltage(): "V",
        _Dim.current(): "A",
        _Dim.resistance(): "Ω",
        _Dim.power(): "W",
        _Dim.capacitance(): "F",
        _Dim.inductance(): "H",
        _Dim.frequency(): "Hz",
        _Dim.time(): "s",
        _Dim.temperature(): "K",
        _Dim.dimensionless(): "",
    }
    return table.get(dim, "")


# ----------------------------------------------------------------------
# Identifiers (typed so they cannot be confused)
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class ComponentRef:
    """A component designator on a board, e.g. ``Q12``, ``R5``, ``U3``."""
    ref: str
    def __post_init__(self):
        if not re.match(r"^[A-Za-z]+\d+[A-Za-z0-9]*$", self.ref):
            raise ValueError(f"invalid component ref: '{self.ref}'")
    def __str__(self) -> str:
        return self.ref


@dataclass(frozen=True)
class PinRef:
    """A pin of a component, e.g. ``Q12.1`` or ``U3.5``."""
    component: ComponentRef
    pin: str
    def __post_init__(self):
        if not re.match(r"^[A-Za-z0-9]+$", str(self.pin)):
            raise ValueError(f"invalid pin: '{self.pin}'")
    @classmethod
    def parse(cls, s: str) -> "PinRef":
        if "." not in s:
            raise ValueError(f"pin ref must be 'REF.PIN': '{s}'")
        ref, pin = s.rsplit(".", 1)
        return cls(ComponentRef(ref), pin)
    def __str__(self) -> str:
        return f"{self.component}.{self.pin}"


@dataclass(frozen=True)
class NetRef:
    """An electrical net name, e.g. ``+3VALW``, ``GND``, ``VIN``."""
    name: str
    def __post_init__(self):
        if not self.name or "/" in self.name:
            raise ValueError(f"invalid net name: '{self.name}'")
    def __str__(self) -> str:
        return self.name


# ----------------------------------------------------------------------
# Engine result envelope
# ----------------------------------------------------------------------

class ErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    MISSING_INPUT = "missing_input"
    DIMENSIONAL = "dimensional"
    OUT_OF_RANGE = "out_of_range"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"
    INTERNAL = "internal"


@dataclass
class EngineError:
    """Structured error returned by an engine instead of raising.

    The LLM orchestrator reads ``code``/``message``/``field`` and explains
    to the technician; it never converts an error into a fake result.
    """
    code: ErrorCode
    message: str
    field: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "field": self.field}

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}" + (f" (field: {self.field})" if self.field else "")


@dataclass
class EngineResult:
    """Every engine returns this envelope. The LLM may paraphrase but
    must not alter ``value``/``value_si``/``metadata``."""
    engine: str          # e.g. "electrical_math"
    method: str          # e.g. "ohm_voltage"
    version: str = ""    # engine version
    inputs: Dict[str, Any] = field(default_factory=dict)   # what was used
    value: Optional[Any] = None                            # the result (often a Quantity)
    value_si: Optional[float] = None                       # SI base magnitude when applicable
    unit: str = ""                                         # display unit when applicable
    formulas: List[str] = field(default_factory=list)      # human-readable formulas used
    assumptions: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    provenance: str = ""                                  # where the method comes from
    error: Optional[EngineError] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Serialize Quantity values nicely.
        if isinstance(self.value, Quantity):
            d["value"] = self.value.render()
            d["value_si"] = self.value.magnitude
            d["unit"] = self.value.display_unit or _default_unit_for(self.value.dimension)
        if self.error is not None:
            d["error"] = self.error.to_dict()
        return d

    def render(self) -> str:
        """Human-readable rendering for the CLI / LLM."""
        if not self.ok:
            return f"[{self.engine} v{self.version}] ERROR: {self.error}"
        v = self.value.render() if isinstance(self.value, Quantity) else str(self.value)
        lines = [
            f"[{self.engine} v{self.version}] {self.method} -> {v}",
            f"  inputs: {self.inputs}",
        ]
        if self.formulas:
            lines.append("  formulas: " + "; ".join(self.formulas))
        if self.assumptions:
            lines.append("  assumptions: " + "; ".join(self.assumptions))
        if self.limitations:
            lines.append("  limitations: " + "; ".join(self.limitations))
        if self.provenance:
            lines.append(f"  provenance: {self.provenance}")
        return "\n".join(lines)
