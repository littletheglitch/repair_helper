"""Motherboard Diagnostic Engine — laptop + desktop platforms.

Public API:
  MotherboardEngine           — the engine class
  MotherboardDiagnosisInput   — the input dataclass
  PLATFORM_LAPTOP, PLATFORM_DESKTOP
"""
from .engine import MotherboardEngine, MotherboardDiagnosisInput, PLATFORM_LAPTOP, PLATFORM_DESKTOP

__all__ = ["MotherboardEngine", "MotherboardDiagnosisInput",
           "PLATFORM_LAPTOP", "PLATFORM_DESKTOP"]
