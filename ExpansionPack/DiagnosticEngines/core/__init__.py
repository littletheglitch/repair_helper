"""Deterministic power-rail diagnostic engine.

This package is intentionally pure-stdlib and dependency-free. It loads the
rail definitions, diagnostic rules, and component database from the
Electronics_Diagnostic_Expansion_Pack folder, evaluates the user's measured
facts against the rules, and returns the next diagnostic step(s).

Design principles (from the requirements):
  * Never assume a controller based only on the rail name.
  * The next step depends on actual measurements, never on a guess.
  * Distinguish expected vs measured voltage, rail resistance, controller
    VIN/VCC, EN, switching, PGOOD, FB, and ripple.
  * Be able to say "more information required" — when a rule's condition
    references a fact that has not been measured, the engine returns
    `requires_info=True` rather than firing.
"""
from .engine import DiagnosticEngine, DiagnosisResult, Measurement, load_engine
from .component_db import ComponentDatabase

__all__ = [
    "DiagnosticEngine",
    "DiagnosisResult",
    "Measurement",
    "ComponentDatabase",
    "load_engine",
]
