"""Phase 6, Task 7 — Windows deployment readiness.

A repeatable pre-flight check for the target Windows machine. It reports,
without starting the model:

  * Python version + required + optional dependency availability.
  * llama.cpp executable discovery (settings, bundled Engine/, PATH).
  * Model file path + size + quantization + whether it exists.
  * Configuration validation (n_ctx, threads, profile sanity; the model
    file referenced in settings actually exists; paths are real).
  * Clear, actionable startup errors (NOT a stack trace) when something
    is missing — so a technician on the target machine can fix it.
  * A logging setup that writes to Logs/ (offline; no cloud).

This module NEVER downloads anything and NEVER assumes internet. It is
safe to import even when optional deps are absent (it reports them as
MISSING, not crashes).

Usage (CLI / script):
    python -m eda.deployment_readiness            # human report
    python -m eda.deployment_readiness --json      # machine-readable
    python -m eda.deployment_readiness --strict    # exit 1 on any FAIL
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config


# ---------------------------------------------------------------------------
# Required vs optional deps
# ---------------------------------------------------------------------------
REQUIRED_DEPS = {
    # name: (import_module, why)
    "pypdf": ("pypdf", "PDF extraction (schematics, datasheets)"),
    "PyMuPDF": ("fitz", "PDF text + image extraction"),
}
OPTIONAL_DEPS = {
    "reportlab": ("reportlab", "PDF report generation (optional)"),
    "pytesseract": ("pytesseract", "OCR of scanned docs (optional)"),
    "Pillow": ("PIL", "image handling for OCR (optional)"),
    "psutil": ("psutil", "process/memory introspection (optional)"),
}


def _check_dep(name: str, import_module: str, why: str,
               required: bool) -> Dict[str, Any]:
    try:
        m = importlib.import_module(import_module)
        ver = getattr(m, "__version__", "?")
        return {"name": name, "status": "OK", "version": ver,
                "required": required, "why": why}
    except ImportError:
        return {"name": name,
                "status": "MISSING" if required else "OPTIONAL_MISSING",
                "required": required, "why": why,
                "fix": (f"pip install {name}" if required
                        else f"pip install {name}  (optional)")}


def check_dependencies() -> List[Dict[str, Any]]:
    out = []
    for name, (mod, why) in REQUIRED_DEPS.items():
        out.append(_check_dep(name, mod, why, required=True))
    for name, (mod, why) in OPTIONAL_DEPS.items():
        out.append(_check_dep(name, mod, why, required=False))
    return out


# ---------------------------------------------------------------------------
# llama.cpp discovery
# ---------------------------------------------------------------------------

def discover_llama_cpp(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Find the llama-server / llama-cli executable. Returns a report."""
    engine = settings.get("engine", {})
    candidates: List[str] = []
    # 1. Explicit settings path.
    for key in ("server_path", "cli_path"):
        p = engine.get(key)
        if p:
            candidates.append(f"settings.{key}={p}")
            pp = Path(p)
            if pp.exists():
                return {"status": "OK", "path": str(pp),
                        "source": f"settings.{key}"}
    # 2. Bundled Engine/ dir.
    bundled = config.app_dir("Engine")
    candidates.append(f"bundled {bundled}")
    for name in ("llama-server.exe", "llama-server", "main.exe", "main"):
        cand = bundled / name
        if cand.exists():
            return {"status": "OK", "path": str(cand),
                    "source": "bundled Engine/"}
    # 3. PATH.
    for name in ("llama-server", "llama-server.exe", "llama-cli",
                 "llama-cli.exe"):
        found = shutil.which(name)
        if found:
            return {"status": "OK", "path": found, "source": "PATH"}
    return {"status": "MISSING", "path": None,
            "source": None,
            "fix": ("Place llama-server.exe in the Engine/ folder next to "
                    "the app, or set engine.server_path in "
                    "Config/settings.json, or add it to PATH."),
            "candidates_checked": candidates}


def llama_cpp_version(exe: Path) -> Optional[str]:
    """Try to read the llama.cpp version string."""
    import subprocess
    try:
        out = subprocess.run([str(exe), "--version"], capture_output=True,
                             text=True, timeout=10)
        txt = (out.stdout + out.stderr).strip()
        if txt:
            return txt.splitlines()[0][:120]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Model file validation
# ---------------------------------------------------------------------------

def _quant_from_name(name: str) -> Optional[str]:
    m = re.search(r"[-_.](q[0-9]+_[a-z0-9_]+)", name, re.IGNORECASE)
    return m.group(1).upper() if m else None


def validate_model(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the configured model file exists and report its size +
    quantization. Never downloads."""
    engine = settings.get("engine", {})
    model_dir = engine.get("model_dir", "Models")
    models_root = config.app_dir(model_dir)
    model_file = engine.get("model_file")
    # Auto-detect a single .gguf if none configured.
    if not model_file and models_root.exists():
        ggufs = sorted(models_root.glob("*.gguf"))
        if ggufs:
            model_file = ggufs[0].name
    if not model_file:
        return {"status": "MISSING", "path": None,
                "fix": (f"Place a 1.5B Q4_K_M GGUF in {models_root} and "
                        "set engine.model_file in Config/settings.json, or "
                        "use `eda model-profile balanced` after placing it."),
                "model_dir": str(models_root)}
    model_path = models_root / model_file
    if not model_path.exists():
        return {"status": "MISSING", "configured": model_file,
                "expected_path": str(model_path),
                "fix": (f"model_file '{model_file}' not found in "
                        f"{models_root}. Place it there or update "
                        "engine.model_file.")}
    size_mb = model_path.stat().st_size / (1024 * 1024)
    return {"status": "OK", "path": str(model_path),
            "file": model_file, "size_mb": round(size_mb, 1),
            "quantization": _quant_from_name(model_file)}


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------

def validate_config(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Sanity-check the engine config. Reports issues, never silently
    increases anything."""
    engine = settings.get("engine", {})
    issues: List[str] = []
    n_ctx = int(engine.get("n_ctx", 2048))
    n_threads = int(engine.get("n_threads", 4))
    n_gpu = int(engine.get("n_gpu_layers", 0))
    if n_ctx < 512:
        issues.append(f"n_ctx={n_ctx} is very small; >=512 recommended")
    if n_ctx > 8192:
        issues.append(f"n_ctx={n_ctx} is large for an 8 GB target; "
                      "expect high memory use")
    if n_threads < 1:
        issues.append(f"n_threads={n_threads} must be >=1")
    cpu_count = os.cpu_count() or 1
    if n_threads > cpu_count:
        issues.append(f"n_threads={n_threads} > cpu_count={cpu_count}; "
                      "will oversubscribe the CPU")
    if n_gpu != 0:
        issues.append(f"n_gpu_layers={n_gpu} but the target is CPU-only; "
                      "set to 0")
    # Profile consistency (informational).
    from . import profiles as PF
    cur_profile = PF.current_profile_name(settings)
    return {"status": "OK" if not issues else "WARN",
            "issues": issues,
            "current_profile": cur_profile,
            "n_ctx": n_ctx, "n_threads": n_threads,
            "n_gpu_layers": n_gpu,
            "cpu_count": cpu_count}


# ---------------------------------------------------------------------------
# Logging setup (offline; writes to Logs/)
# ---------------------------------------------------------------------------

def configure_logging(level: str = "INFO") -> Dict[str, Any]:
    """Configure logging to Logs/eda.log (and stderr). Offline; no cloud
    handlers. Idempotent."""
    log_dir = config.app_dir("Logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "eda.log"
    root = logging.getLogger()
    # Avoid duplicate handlers on re-call.
    if not any(getattr(h, "_eda_file", False) for h in root.handlers):
        fh = logging.FileHandler(str(log_path), encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        fh._eda_file = True  # type: ignore[attr-defined]
        root.addHandler(fh)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    return {"log_path": str(log_path), "level": level}


# ---------------------------------------------------------------------------
# Full readiness report
# ---------------------------------------------------------------------------

def readiness_report() -> Dict[str, Any]:
    """Run all pre-flight checks. Returns a structured report. Does NOT
    start the model. Does NOT access the internet."""
    settings = config.load_settings()
    deps = check_dependencies()
    llama = discover_llama_cpp(settings)
    if llama["status"] == "OK":
        llama["version"] = llama_cpp_version(Path(llama["path"]))
    model = validate_model(settings)
    cfg = validate_config(settings)
    logging_info = configure_logging()
    python = {"version": platform.python_version(),
              "platform": platform.platform(),
              "machine": platform.machine()}
    failed = []
    if any(d["status"] == "MISSING" for d in deps):
        failed.append("missing required dependency")
    if llama["status"] != "OK":
        failed.append("llama.cpp executable missing")
    if model["status"] != "OK":
        failed.append("model file missing")
    return {
        "python": python,
        "dependencies": deps,
        "llama_cpp": llama,
        "model": model,
        "config": cfg,
        "logging": logging_info,
        "overall": "READY" if not failed else "NOT_READY",
        "failures": failed,
    }


def _format_human(report: Dict[str, Any]) -> str:
    lines = ["=" * 60,
             "EDA deployment readiness (offline pre-flight)",
             "=" * 60]
    lines.append(f"Overall: {report['overall']}")
    if report["failures"]:
        lines.append("Failures: " + ", ".join(report["failures"]))
    lines.append("")
    lines.append(f"Python {report['python']['version']} "
                 f"on {report['python']['platform']}")
    lines.append("")
    lines.append("Dependencies:")
    for d in report["dependencies"]:
        mark = {"OK": "OK ", "MISSING": "MISS",
                "OPTIONAL_MISSING": "opt "}[d["status"]]
        req = "REQ" if d["required"] else "opt"
        lines.append(f"  [{mark}] {req} {d['name']}: {d['status']}  ({d['why']})")
        if "fix" in d:
            lines.append(f"        fix: {d['fix']}")
    lines.append("")
    lines.append("llama.cpp:")
    ll = report["llama_cpp"]
    lines.append(f"  status: {ll['status']}")
    if ll["status"] == "OK":
        lines.append(f"  path: {ll['path']}  (via {ll['source']})")
        if ll.get("version"):
            lines.append(f"  version: {ll['version']}")
    else:
        lines.append(f"  fix: {ll.get('fix','')}")
    lines.append("")
    lines.append("Model:")
    md = report["model"]
    lines.append(f"  status: {md['status']}")
    if md["status"] == "OK":
        lines.append(f"  file: {md['file']}  ({md['size_mb']} MB, "
                     f"{md['quantization']})")
        lines.append(f"  path: {md['path']}")
    else:
        lines.append(f"  fix: {md.get('fix','')}")
    lines.append("")
    lines.append("Config:")
    cf = report["config"]
    lines.append(f"  profile: {cf['current_profile']}")
    lines.append(f"  n_ctx={cf['n_ctx']} n_threads={cf['n_threads']} "
                 f"n_gpu={cf['n_gpu_layers']} (cpu_count={cf['cpu_count']})")
    if cf["issues"]:
        for i in cf["issues"]:
            lines.append(f"  WARN: {i}")
    lines.append("")
    lines.append(f"Logging to: {report['logging']['log_path']}")
    lines.append("")
    lines.append("Repeatable launch:")
    lines.append("  1. eda model-profile balanced   # set resource profile")
    lines.append("  2. python scripts/phase5_hardware_validation.py")
    lines.append("  3. eda                          # start the REPL")
    lines.append("Model files stay in Models/ (not bundled in the exe).")
    return "\n".join(lines)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if overall is NOT_READY")
    args = ap.parse_args()
    report = readiness_report()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(_format_human(report))
    if args.strict and report["overall"] != "READY":
        sys.exit(1)


if __name__ == "__main__":
    main()
