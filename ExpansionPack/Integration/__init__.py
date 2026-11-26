"""Integration package — the host-application adapter shim.

Public API:
  ExpansionPackAdapter  — the host's single entry point
  DiagnosticRequest     — the request dataclass
  load_adapter          — convenience factory
"""
from .host_adapter import ExpansionPackAdapter, DiagnosticRequest, load_adapter

__all__ = ["ExpansionPackAdapter", "DiagnosticRequest", "load_adapter"]
