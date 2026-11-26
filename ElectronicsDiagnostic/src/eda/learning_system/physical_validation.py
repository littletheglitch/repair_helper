"""Physical validation of generated exercises.

Every generated exercise must be physically sensible before it is
stored. This module centralizes the checks so every generator uses the
same rules, and so the checks are testable in isolation.

Checks (each returns (ok, reason)):
  * validate_ohm(v, i, r): a resistive load at (v, i) — the resulting R
    must be a plausible load (not a dead short), and the power
    P = V*I must be within a sane range for a board-level load.
  * validate_divider(vin, r1, r2): a voltage divider — R1+R2 must draw a
    sane quiescent current (not a dead short, not a giant resistor), and
    Vout must be a useful rail voltage (above a diode drop, below Vin).
  * validate_rc(r, c): an RC filter — tau must be in a useful range
    (not sub-ns, not hours), and R and C must be standard values.
  * validate_power(v, i, r): the power dissipated must be within a sane
    board-level limit (not MW, not fW).

The spec rule: "تمرین نامعتبر رد شود" (invalid exercises are rejected).
When a generator draws parameters that fail validation, it returns None
and the caller skips that exercise (no invalid exercise is stored).
"""
from __future__ import annotations

from typing import Tuple

# Board-level sanity bounds. These are deliberately generous so that
# real board values pass, but clearly-absurd values (a 100 A rail, a
# 0.001 Ω load, a 1 MΩ divider) are rejected.
MIN_VOLTAGE = 0.8       # V — below a diode drop is not a useful rail
MAX_VOLTAGE = 60.0     # V — above 48V PoE / 24V rails is uncommon on laptops
MIN_CURRENT = 0.001    # A — 1 mA; below that is leakage, not a load
MAX_CURRENT = 10.0     # A — a single rail pulling 10 A is the ceiling
MIN_POWER = 1e-4       # W — 100 µW; below that is quiescent, not a load
MAX_POWER = 50.0       # W — a single component dissipating >50 W is extreme
MIN_RESISTANCE = 0.1   # Ω — below that is a short, not a load
MAX_RESISTANCE = 10e6  # Ω — above 10 MΩ is leakage-dominated
MIN_TAU = 1e-9         # s — 1 ns; below that is parasitic, not a filter
MAX_TAU = 3600.0       # s — 1 hour; above that is not a useful RC
MIN_DIVIDER_TOTAL = 1e3    # Ω — a divider under 1 kΩ wastes current
MAX_DIVIDER_TOTAL = 1e7    # Ω — a divider over 10 MΩ is noise-dominated


def validate_ohm(v: float, i: float, r: float) -> Tuple[bool, str]:
    """Validate a resistive load exercise (V, I → R)."""
    if not (MIN_VOLTAGE <= v <= MAX_VOLTAGE):
        return False, f"voltage {v} V out of board range {MIN_VOLTAGE}-{MAX_VOLTAGE} V"
    if not (MIN_CURRENT <= i <= MAX_CURRENT):
        return False, f"current {i} A out of board range {MIN_CURRENT}-{MAX_CURRENT} A"
    p = v * i
    if not (MIN_POWER <= p <= MAX_POWER):
        return False, (f"power {p:.3g} W out of board range "
                      f"{MIN_POWER}-{MAX_POWER} W")
    if not (MIN_RESISTANCE <= r <= MAX_RESISTANCE):
        return False, (f"resistance {r:.3g} Ω out of range "
                      f"{MIN_RESISTANCE}-{MAX_RESISTANCE} Ω (likely a short)")
    return True, ""


def validate_divider(vin: float, r1: float, r2: float, vout: float) -> Tuple[bool, str]:
    """Validate a voltage divider exercise."""
    if not (MIN_VOLTAGE <= vin <= MAX_VOLTAGE):
        return False, f"Vin {vin} V out of board range"
    total = r1 + r2
    if not (MIN_DIVIDER_TOTAL <= total <= MAX_DIVIDER_TOTAL):
        return False, (f"R1+R2 = {total:.3g} Ω out of divider range "
                      f"{MIN_DIVIDER_TOTAL}-{MAX_DIVIDER_TOTAL} Ω")
    # Vout must be a useful rail voltage: above a diode drop, below Vin.
    if not (0.3 <= vout <= vin):
        return False, (f"Vout {vout:.3g} V is not a useful rail "
                      f"(must be 0.3 V..{vin} V)")
    # Reject degenerate R1=0 (Vout = Vin, not a divider) or R2=0 (Vout=0).
    if r1 <= 0 or r2 <= 0:
        return False, "R1 and R2 must both be positive for a real divider"
    # Quiescent current at this divider must be sane.
    iq = vin / total
    if not (1e-6 <= iq <= 1e-2):
        return False, (f"quiescent current {iq:.3g} A out of divider range "
                      f"(1 µA..10 mA)")
    return True, ""


def validate_rc(r: float, c: float, tau: float) -> Tuple[bool, str]:
    """Validate an RC time-constant exercise."""
    if not (1e3 <= r <= 1e8):
        return False, f"R {r:.3g} Ω out of RC range (1 kΩ..100 MΩ)"
    if not (1e-12 <= c <= 1e-2):
        return False, f"C {c:.3g} F out of RC range (1 pF..10 mF)"
    if not (MIN_TAU <= tau <= MAX_TAU):
        return False, (f"tau {tau:.3g} s out of useful RC range "
                      f"{MIN_TAU}-{MAX_TAU} s")
    return True, ""


def validate_power(v: float, i: float) -> Tuple[bool, str]:
    """Validate a power-dissipation exercise (P = V*I)."""
    p = v * i
    if not (MIN_POWER <= p <= MAX_POWER):
        return False, (f"power {p:.3g} W out of board range "
                      f"{MIN_POWER}-{MAX_POWER} W")
    return True, ""
