"""Measurement model for the Core Diagnostic Framework.

Critical semantics enforced here:

  * `MeasurementState` distinguishes NOT_MEASURED from MEASURED_ZERO.
    A rail that was measured as 0.0 V is NOT the same as a rail that
    was never measured. The framework and every engine must preserve
    this distinction.

  * `MeasurementCondition` records the powered/unpowered state, the
    reference point, and the instrument. Two resistance readings on
    the same rail are NOT comparable if one was taken powered and the
    other unpowered.

  * `MeasurementKind` enumerates the supported measurement types so
    the engines can dispatch on kind without parsing strings.

A Measurement is immutable in spirit; engines read it, they do not
mutate it. New measurements are appended to the session.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any


class MeasurementKind(str, Enum):
    VOLTAGE = "voltage"
    RESISTANCE = "resistance"
    CONTINUITY = "continuity"
    DIODE = "diode"
    CURRENT = "current"
    FREQUENCY = "frequency"
    TEMPERATURE = "temperature"
    WAVEFORM = "waveform"
    LOGIC_LEVEL = "logic_level"
    CUSTOM = "custom"


class MeasurementState(str, Enum):
    """The state of a measurement slot, not the value.

    This is the single most important enum in the framework. Engines
    MUST consult it before interpreting a value.
    """
    NOT_MEASURED = "not_measured"      # the slot exists but no reading taken
    MEASURED_ZERO = "measured_zero"   # the reading is genuinely 0.0
    BELOW_RANGE = "below_range"        # value below instrument range
    ABOVE_RANGE = "above_range"        # value above instrument range (OL)
    IN_RANGE = "in_range"              # value is a valid numeric reading
    INVALID = "invalid"                # measurement taken but unusable
    NOT_APPLICABLE = "not_applicable"  # e.g. resistance on a powered rail
    UNKNOWN = "unknown"                # state cannot be determined


class MeasurementCondition(str, Enum):
    """The powered condition under which a measurement was taken.

    Resistance readings are only meaningful UNPOWERED. Voltage readings
    are only meaningful POWERED. The framework refuses to compare two
    measurements whose conditions are incompatible.
    """
    POWERED = "powered"
    UNPOWERED = "unpowered"
    PARTIALLY_POWERED = "partially_powered"  # some rails up, others down
    UNKNOWN = "unknown"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Measurement:
    """A single measurement.

    `value` is a float for numeric kinds, a string for waveform/logic
    descriptions, None when state is NOT_MEASURED.

    `state` MUST be set consistently with `value`:
      * state=MEASURED_ZERO  -> value == 0.0 (or "0")
      * state=NOT_MEASURED   -> value is None
      * state=IN_RANGE       -> value is a valid number/string
      * state=BELOW_RANGE / ABOVE_RANGE -> value is None or the clamped edge
      * state=INVALID / NOT_APPLICABLE -> value is None
    """
    name: str
    kind: MeasurementKind
    state: MeasurementState
    value: Optional[Any] = None
    unit: str = ""
    location: str = ""              # where on the board (test point name)
    reference_point: str = ""       # what the negative probe was on
    condition: MeasurementCondition = MeasurementCondition.UNKNOWN
    instrument: str = ""            # e.g. "DMM", "oscilloscope 100MHz 10x"
    timestamp_utc: str = field(default_factory=_utc_now_iso)
    technician: Optional[str] = None
    verified: bool = False          # independently re-measured by a 2nd tech
    uncertainty: Optional[str] = None  # e.g. "+/- 2% + 2 digits"
    notes: str = ""
    measurement_id: str = field(default_factory=lambda: f"M_{uuid.uuid4().hex[:10]}")

    def __post_init__(self):
        # Consistency check between state and value
        if self.state == MeasurementState.MEASURED_ZERO and self.value not in (0, 0.0, "0"):
            # Tolerate None being normalized to 0.0
            if self.value is None:
                self.value = 0.0
            else:
                raise ValueError(
                    f"Measurement {self.name!r}: state=MEASURED_ZERO but value={self.value!r}"
                )
        if self.state == MeasurementState.NOT_MEASURED and self.value is not None:
            raise ValueError(
                f"Measurement {self.name!r}: state=NOT_MEASURED but value is not None"
            )
        if isinstance(self.kind, str):
            self.kind = MeasurementKind(self.kind)
        if isinstance(self.state, str):
            self.state = MeasurementState(self.state)
        if isinstance(self.condition, str):
            self.condition = MeasurementCondition(self.condition)

    # --- helpers -------------------------------------------------------

    @property
    def is_measured(self) -> bool:
        """True if a real reading was taken (any state except NOT_MEASURED
        and NOT_APPLICABLE)."""
        return self.state not in (
            MeasurementState.NOT_MEASURED,
            MeasurementState.NOT_APPLICABLE,
        )

    @property
    def numeric_value(self) -> Optional[float]:
        """Return the value as a float, or None when not numeric."""
        if self.value is None:
            return None
        if isinstance(self.value, (int, float)):
            return float(self.value)
        try:
            return float(self.value)
        except (TypeError, ValueError):
            return None

    def comparable_to(self, other: "Measurement") -> bool:
        """Two measurements can only be compared when their kind and
        condition are compatible. This is the explicit guard against
        the classic mistake of comparing a powered voltage reading to
        an unpowered resistance reading."""
        if not isinstance(other, Measurement):
            return False
        if self.kind != other.kind:
            return False
        # Resistance must be unpowered on both sides to compare.
        if self.kind == MeasurementKind.RESISTANCE:
            return self.condition == MeasurementCondition.UNPOWERED and \
                   other.condition == MeasurementCondition.UNPOWERED
        # Voltage must be powered on both sides.
        if self.kind == MeasurementKind.VOLTAGE:
            return self.condition == MeasurementCondition.POWERED and \
                   other.condition == MeasurementCondition.POWERED
        # Other kinds: require same condition.
        return self.condition == other.condition

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["state"] = self.state.value
        d["condition"] = self.condition.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Measurement":
        d = dict(d)
        if isinstance(d.get("kind"), str):
            d["kind"] = MeasurementKind(d["kind"])
        if isinstance(d.get("state"), str):
            d["state"] = MeasurementState(d["state"])
        if isinstance(d.get("condition"), str):
            d["condition"] = MeasurementCondition(d["condition"])
        return cls(**d)

    @classmethod
    def not_measured(cls, name: str, kind: MeasurementKind, **kw) -> "Measurement":
        """Construct a NOT_MEASURED slot. Used when an engine wants to
        represent a measurement it expects but does not have."""
        return cls(name=name, kind=kind, state=MeasurementState.NOT_MEASURED, **kw)

    @classmethod
    def measured_zero(cls, name: str, kind: MeasurementKind, **kw) -> "Measurement":
        return cls(name=name, kind=kind, state=MeasurementState.MEASURED_ZERO,
                   value=0.0, **kw)
