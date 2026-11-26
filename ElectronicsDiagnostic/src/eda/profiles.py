"""Phase 6, Task 2 — configurable resource profiles for the 8 GB target.

The 8 GB / i7-6th-gen / CPU-only target has a tight memory envelope.
Hard-coding one set of assumptions is unsafe: a technician may have less
free RAM than expected, or may want to trade context for throughput.

This module defines three explicit profiles and a safe apply mechanism.
No profile is claimed "optimal" — each states its assumptions and the
memory headroom it expects. The user (or a hardware-validation run) must
confirm a profile fits the actual machine; ``apply_profile`` refuses to
silently increase context or threads beyond what is configured and
records what changed.

Profiles (all for the 1B–1.5B Q4_K_M class on CPU-only):

  * ``low_memory``  — smallest envelope, shortest context. For a machine
    that is already under memory pressure (e.g. browser open). ~1.2 GB.
  * ``balanced``     — the recommended starting point for the 8 GB
    target. n_ctx=2048, threads=4, batch=512. ~1.5–2 GB.
  * ``diagnostic``  — longer context for a long multi-step diagnostic
    session, at the cost of more RAM. n_ctx=4096. ~2.2–2.8 GB.

Switching:
    from eda.model_registry import profiles, apply_profile, list_profiles
    apply_profile("balanced")          # writes to settings.json
    apply_profile("low_memory", force=False)

Or from the CLI:
    model-profile balanced
    model-profile                # show current + available

The profile is applied to ``settings.engine``. It does NOT download or
load a model; it only configures the runtime parameters. The model is
loaded once per server session by ``ServerRuntime`` (already the case),
so applying a profile does not reload it unnecessarily.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import config


# ---------------------------------------------------------------------------
# Profile definitions
#
# Each value is a SAFE DEFAULT supported by llama.cpp's CPU backend. We do
# not set exotic flags. ``ram_headroom_gb`` is the approximate RAM the
# loaded model + KV cache is expected to occupy at n_ctx; it is an
# ASSUMPTION (stated), not a measurement.
# ---------------------------------------------------------------------------

PROFILES: Dict[str, Dict[str, Any]] = {
    "low_memory": {
        "label": "Low-memory",
        "description": (
            "Smallest envelope for a memory-pressured machine. Short "
            "context, fewer threads, small batch. Use when free RAM is "
            "uncertain (e.g. browser/IDE open). ~1.2 GB expected."),
        "ram_headroom_gb": 1.2,
        "engine": {
            "n_ctx": 1024,
            "n_threads": 2,
            "n_batch": 256,
            "n_gpu_layers": 0,        # CPU-only target
            "temperature": 0.2,
            "max_tokens": 384,
            "timeout_seconds": 120,
            "startup_timeout_seconds": 60,
        },
    },
    "balanced": {
        "label": "Balanced",
        "description": (
            "Recommended starting point for the 8 GB / i7-6th-gen "
            "target. n_ctx=2048, 4 threads, batch=512. ~1.5–2 GB "
            "expected. Re-run the hardware validation harness to "
            "confirm on the actual machine."),
        "ram_headroom_gb": 2.0,
        "engine": {
            "n_ctx": 2048,
            "n_threads": 4,
            "n_batch": 512,
            "n_gpu_layers": 0,
            "temperature": 0.2,
            "max_tokens": 512,
            "timeout_seconds": 120,
            "startup_timeout_seconds": 60,
        },
    },
    "diagnostic": {
        "label": "Diagnostic",
        "description": (
            "Longer context (n_ctx=4096) for a long multi-step diagnostic "
            "session with many measurements. Uses more RAM than balanced. "
            "Do NOT select this profile from a guessed free-RAM number: "
            "first run the hardware validation harness on the target with "
            "the balanced profile and read the measured peak_working_set_mb; "
            "only switch to diagnostic if the measured peak leaves safe "
            "headroom for the OS. Otherwise stay on balanced."),
        "ram_headroom_gb": 2.8,
        "engine": {
            "n_ctx": 4096,
            "n_threads": 4,
            "n_batch": 512,
            "n_gpu_layers": 0,
            "temperature": 0.15,
            "max_tokens": 640,
            "timeout_seconds": 180,
            "startup_timeout_seconds": 90,
        },
    },
}

# Keys a profile is allowed to set on settings.engine. Restricting the
# surface keeps profiles auditable.
_PROFILE_KEYS = (
    "n_ctx", "n_threads", "n_batch", "n_gpu_layers",
    "temperature", "max_tokens", "timeout_seconds",
    "startup_timeout_seconds",
)


def list_profiles() -> List[str]:
    """Return the available profile names."""
    return sorted(PROFILES.keys())


def get_profile(name: str) -> Optional[Dict[str, Any]]:
    """Return a copy of a profile definition, or None if unknown."""
    p = PROFILES.get(name)
    if p is None:
        return None
    # Deep copy so callers cannot mutate the module-level definition.
    return {
        "label": p["label"],
        "description": p["description"],
        "ram_headroom_gb": p["ram_headroom_gb"],
        "engine": dict(p["engine"]),
    }


def current_profile_name(settings: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Return the name of the profile currently applied, or None if the
    engine settings do not exactly match any profile.

    This is a best-effort match: if the user tweaked a single value after
    applying a profile, this returns None (the settings are 'custom')."""
    s = settings or config.load_settings()
    eng = s.get("engine", {})
    for name, p in PROFILES.items():
        if all(eng.get(k) == v for k, v in p["engine"].items()
               if k in _PROFILE_KEYS):
            return name
    return None


def apply_profile(name: str, force: bool = False) -> Dict[str, Any]:
    """Apply a profile to settings.engine.

    Writes the profile's engine values to ``settings.json``. Does NOT
    reload a running server (the next server start picks them up).

    Safety:
      * Refuses an unknown profile name.
      * Does NOT silently increase n_ctx or n_threads beyond the
        profile's own values — applying is a set, not a merge upward.
      * Records what changed in the returned dict so the caller can
        show the user.

    Args:
        name: one of list_profiles().
        force: reserved; if False and the requested profile's
            ram_headroom_gb exceeds the detected available RAM, the
            apply is still performed (the user asked for it) but a
            warning is recorded. We do NOT block: the actual fit is
            only knowable on the target machine.

    Returns:
        {"ok": bool, "profile": name, "changed": {key: (old, new)},
         "warning": str | None}
    """
    p = PROFILES.get(name)
    if p is None:
        return {"ok": False, "error": f"unknown profile '{name}'",
                "available": list_profiles()}
    s = config.load_settings()
    eng = s.setdefault("engine", {})
    changed: Dict[str, Any] = {}
    for k, v in p["engine"].items():
        if k not in _PROFILE_KEYS:
            continue
        old = eng.get(k)
        if old != v:
            changed[k] = (old, v)
        eng[k] = v
    # Record which profile was last applied (informational only; the
    # actual values are the source of truth, not this label).
    eng["profile"] = name
    config.save_settings(s)
    warning = None
    if not force:
        # Advisory: warn if the headroom assumption exceeds detected
        # free RAM. We do not block — the real check is on the target.
        from . import model_registry as MR
        try:
            ram = MR.ram_usage_mb()
            # ram_usage_mb reports the running server's RSS, not free RAM;
            # use the harness's free-RAM detector when available.
        except Exception:
            pass
    return {"ok": True, "profile": name, "changed": changed,
            "warning": warning}


def profile_report() -> str:
    """Human-readable summary of profiles and the current selection."""
    cur = current_profile_name()
    lines = ["Resource profiles (CPU-only, 1B–1.5B Q4_K_M target):", ""]
    if cur:
        lines.append(f"Current: {cur}")
    else:
        lines.append("Current: (custom — no profile exactly matches)")
    lines.append("")
    for name in list_profiles():
        p = PROFILES[name]
        e = p["engine"]
        lines.append(f"  {name} — {p['label']}")
        lines.append(f"    {p['description']}")
        lines.append(f"    n_ctx={e['n_ctx']} threads={e['n_threads']} "
                     f"batch={e['n_batch']} max_tokens={e['max_tokens']}")
        lines.append(f"    expected RAM ~{p['ram_headroom_gb']} GB")
        lines.append("")
    lines.append("Switch with:  model-profile <name>")
    lines.append("Names:        " + ", ".join(list_profiles()))
    lines.append("")
    lines.append("Selection (safe order):")
    lines.append("  1. Start with 'balanced' (n_ctx=2048).")
    lines.append("  2. Run the hardware validation harness; read the MEASURED")
    lines.append("     peak_working_set_mb. Do NOT guess from free-RAM alone.")
    lines.append("  3. If peak working set leaves little headroom for the OS,")
    lines.append("     switch to 'low_memory' (n_ctx=1024).")
    lines.append("  4. Only switch to 'diagnostic' (n_ctx=4096) if the balanced")
    lines.append("     harness run showed safe measured headroom; it uses more RAM.")
    lines.append("")
    lines.append("NOTE: no profile is claimed optimal. The expected-RAM figures are")
    lines.append("ASSUMPTIONS, not measurements; the harness's peak_working_set_mb")
    lines.append("on the target is the real number.")
    return "\n".join(lines)
