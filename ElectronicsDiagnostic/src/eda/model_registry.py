"""Model Registry — manage local GGUF models and the runtime.

Responsibilities (matching the spec):

* Models live in ``Models/``, the runtime lives in ``Engine/``. The
  model is **never** bundled inside the EXE.
* Download happens only on explicit user request, with a SHA-256
  check against a published hash (when available), and the file can be
  copied to another machine (offline portability).
* Settings for ``n_ctx``, ``n_threads``, ``n_batch``, ``n_gpu_layers``
  are adjustable per model; a RAM monitor measures real resident set
  size of the running server.
* The registry knows the candidate model classes (1B-1.5B, 3B-4B,
  7B-8B) the spec asks us to consider, with honest per-class estimates
  so the user is not pushed to the biggest model.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from . import config


# ---------------------------------------------------------------------------
# Candidate model catalog
# ---------------------------------------------------------------------------

# Each class lists real, publicly available GGUF models at the spec'd
# parameter counts. Sizes/URLs are the upstream values; the user MUST
# verify the SHA-256 after download (the registry prints it).
#
# These are the classes the spec asks us to evaluate. We do NOT default
# to the largest class; the recommendation for the 8 GB / i7-6th-gen
# target is the 1B-1.5B class (see MODEL_PREPARATION.md).
MODEL_CLASSES: Dict[str, Dict[str, Any]] = {
    "1b-1.5b": {
        "label": "1B–1.5B instruct",
        "ram_target_gb": 1.5,
        "disk_gb_range": (0.7, 1.1),
        "notes": ("Best fit for 8 GB RAM / i7 6th gen CPU-only. "
                  "Q4_K_M of a 1.5B model is ~1.0 GB on disk, "
                  "~1.5–2 GB RAM at n_ctx=2048."),
        "models": [
            {
                "key": "qwen2.5-1.5b-instruct-q4_k_m",
                "filename": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
                "url": ("https://huggingface.co/bartowski/"
                        "Qwen2.5-1.5B-Instruct-GGUF/resolve/main/"
                        "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"),
                "size_gb_approx": 1.0,
                "publisher_sha256": "",  # verify on download; print it
            },
            {
                "key": "llama-3.2-1b-instruct-q4_k_m",
                "filename": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
                "url": ("https://huggingface.co/bartowski/"
                        "Llama-3.2-1B-Instruct-GGUF/resolve/main/"
                        "Llama-3.2-1B-Instruct-Q4_K_M.gguf"),
                "size_gb_approx": 0.9,
                "publisher_sha256": "",
            },
        ],
    },
    "3b-4b": {
        "label": "3B–4B instruct",
        "ram_target_gb": 3.5,
        "disk_gb_range": (1.8, 2.6),
        "notes": ("Noticeably better reasoning than 1B-1.5B, but ~3-4 GB "
                  "RAM at n_ctx=2048. On an 8 GB target this leaves "
                  "little room for the OS during a long session. "
                  "Throughput on a 6th-gen i7 CPU is ~2-4 tok/s."),
        "models": [
            {
                "key": "qwen2.5-3b-instruct-q4_k_m",
                "filename": "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
                "url": ("https://huggingface.co/bartowski/"
                        "Qwen2.5-3B-Instruct-GGUF/resolve/main/"
                        "Qwen2.5-3B-Instruct-Q4_K_M.gguf"),
                "size_gb_approx": 2.0,
                "publisher_sha256": "",
            },
            {
                "key": "phi-3.5-mini-instruct-q4_k_m",
                "filename": "Phi-3.5-mini-instruct-Q4_K_M.gguf",
                "url": ("https://huggingface.co/bartowski/"
                        "Phi-3.5-mini-instruct-GGUF/resolve/main/"
                        "Phi-3.5-mini-instruct-Q4_K_M.gguf"),
                "size_gb_approx": 2.5,
                "publisher_sha256": "",
            },
        ],
    },
    "7b-8b": {
        "label": "7B–8B instruct",
        "ram_target_gb": 6.0,
        "disk_gb_range": (4.0, 5.5),
        "notes": ("Best reasoning quality of the three classes, BUT "
                  "~5-6 GB RAM at n_ctx=2048 on CPU, which is over the "
                  "8 GB target's safe envelope once the OS and the app "
                  "are counted. Expect heavy swapping and ~0.5-1.5 tok/s "
                  "on a 6th-gen i7. NOT recommended for the target."),
        "models": [
            {
                "key": "qwen2.5-7b-instruct-q4_k_m",
                "filename": "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
                "url": ("https://huggingface.co/bartowski/"
                        "Qwen2.5-7B-Instruct-GGUF/resolve/main/"
                        "Qwen2.5-7B-Instruct-Q4_K_M.gguf"),
                "size_gb_approx": 4.7,
                "publisher_sha256": "",
            },
            {
                "key": "llama-3.1-8b-instruct-q4_k_m",
                "filename": "Llama-3.1-8B-Instruct-Q4_K_M.gguf",
                "url": ("https://huggingface.co/bartowski/"
                        "Llama-3.1-8B-Instruct-GGUF/resolve/main/"
                        "Llama-3.1-8B-Instruct-Q4_K_M.gguf"),
                "size_gb_approx": 4.9,
                "publisher_sha256": "",
            },
        ],
    },
}


@dataclass
class ModelInfo:
    key: str
    filename: str
    url: str
    size_gb_approx: float
    publisher_sha256: str
    klass: str
    path: Path
    exists: bool
    actual_size_bytes: int = 0
    actual_sha256: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "filename": self.filename,
            "class": self.klass, "url": self.url,
            "size_gb_approx": self.size_gb_approx,
            "publisher_sha256": self.publisher_sha256 or "(verify on download)",
            "path": str(self.path),
            "exists": self.exists,
            "actual_size_bytes": self.actual_size_bytes,
            "actual_sha256": self.actual_sha256,
        }


def list_models() -> List[ModelInfo]:
    """List every candidate model and whether it is present on disk."""
    models_dir = config.app_dir("Models")
    out: List[ModelInfo] = []
    for klass, info in MODEL_CLASSES.items():
        for m in info["models"]:
            p = models_dir / m["filename"]
            info_obj = ModelInfo(
                key=m["key"], filename=m["filename"], url=m["url"],
                size_gb_approx=m["size_gb_approx"],
                publisher_sha256=m.get("publisher_sha256", ""),
                klass=klass, path=p, exists=p.exists(),
            )
            if info_obj.exists:
                info_obj.actual_size_bytes = p.stat().st_size
            out.append(info_obj)
    return out


def find_model(key: str) -> Optional[ModelInfo]:
    for m in list_models():
        if m.key == key:
            return m
    return None


# ---------------------------------------------------------------------------
# SHA-256
# ---------------------------------------------------------------------------

def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def verify_sha256(path: Path, expected: str) -> Tuple[bool, str]:
    actual = sha256_file(path)
    return (actual.lower() == expected.lower(), actual)


# ---------------------------------------------------------------------------
# Download (explicit only; streams with progress; verifies hash)
# ---------------------------------------------------------------------------

def download_model(key: str, yes: bool = False,
                   timeout: int = 1800) -> Dict[str, Any]:
    """Download a model by key. Refuses without ``yes=True``.

    Returns a dict with ``ok``, ``path``, ``sha256``, ``size_bytes``,
    ``verified`` (True only if a publisher hash was known and matched),
    and ``note``. Never executes anything from the downloaded file —
    it is a single .gguf that llama.cpp loads as data.
    """
    m = find_model(key)
    if not m:
        return {"ok": False, "note": f"unknown model key '{key}'"}
    if m.exists and not yes:
        return {"ok": False, "note": f"{m.filename} already exists; "
                "pass yes=True to re-download (it will be overwritten)."}
    if not yes:
        return {"ok": False, "note": "download requires explicit yes=True"}
    # Stream download. urllib follows HF redirects.
    import urllib.request
    models_dir = config.app_dir("Models")
    dest = models_dir / m.filename
    tmp = dest.with_suffix(dest.suffix + ".part")
    h = hashlib.sha256()
    try:
        req = urllib.request.Request(
            m.url, headers={"User-Agent": "eda-model-registry/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp, \
                open(tmp, "wb") as f:
            total = int(resp.headers.get("Content-Length", 0))
            got = 0
            last = -1
            while True:
                buf = resp.read(1 << 16)
                if not buf:
                    break
                f.write(buf)
                h.update(buf)
                got += len(buf)
                if total and got * 100 // total != last and got * 100 // total % 5 == 0:
                    last = got * 100 // total
                    sys.stdout.write(f"  ...{last}% "
                                     f"({got // 1 << 20 // (1<<20)} MB)\n")
                    sys.stdout.flush()
    except Exception as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        return {"ok": False, "note": f"download failed: {e}"}
    tmp.replace(dest)
    sha = h.hexdigest()
    verified = False
    if m.publisher_sha256:
        verified = sha.lower() == m.publisher_sha256.lower()
    # Update settings to use the new model.
    s = config.load_settings()
    s.setdefault("engine", {})["model_file"] = m.filename
    config.save_settings(s)
    return {
        "ok": True, "path": str(dest), "sha256": sha,
        "size_bytes": dest.stat().st_size, "verified": verified,
        "note": "saved; settings.json updated to use this model" +
                ("; SHA-256 matches publisher hash" if verified
                 else "; no publisher hash to verify against — print and record it"),
    }


# ---------------------------------------------------------------------------
# Runtime settings (per model)
# ---------------------------------------------------------------------------

def runtime_settings(model_key: Optional[str] = None) -> Dict[str, Any]:
    """Return the runtime settings for a model, with safe defaults."""
    s = config.load_settings()
    eng = s.get("engine", {}).copy()
    defaults = {
        "n_ctx": 2048,
        "n_threads": 4,
        "n_batch": 512,
        "n_gpu_layers": 0,
        "temperature": 0.2,
        "max_tokens": 512,
        "timeout_seconds": 120,
        "startup_timeout_seconds": 60,
        "server_port": 8080,
        "server_host": "127.0.0.1",
    }
    for k, v in defaults.items():
        eng.setdefault(k, v)
    return eng


def set_runtime_settings(**overrides: Any) -> Dict[str, Any]:
    """Persist runtime settings overrides (n_ctx, n_threads, n_batch,
    n_gpu_layers, temperature, max_tokens, server_port, etc.)."""
    s = config.load_settings()
    eng = s.setdefault("engine", {})
    eng.update(overrides)
    config.save_settings(s)
    return eng


# ---------------------------------------------------------------------------
# RAM monitor
# ---------------------------------------------------------------------------

def ram_usage_mb() -> Dict[str, Any]:
    """Measure the resident memory of the running llama-server process.

    On Linux/Windows ``psutil`` is the easy path but we keep stdlib-only.
    We use ``/proc`` on Linux and fall back to the platform's task
    manager command. Returns rss_mb = 0 if it cannot be measured.
    """
    # Find the llama-server process.
    pids = _find_process("llama-server")
    if not pids:
        pids = _find_process("llama-cli")
    if not pids:
        return {"running": False, "rss_mb": 0, "pid": None, "note": "no server process found"}
    pid = pids[0]
    if os.name == "nt":
        # Windows: use wmic (deprecated but present on Win10/11) or tasklist.
        try:
            import subprocess
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5).stdout.strip()
            # "name","pid","sess","sess#","mem"
            parts = out.split(",")
            mem_str = parts[-1].strip('"').rstrip(" K").replace(",", "")
            return {"running": True, "rss_mb": int(mem_str) // 1024, "pid": pid}
        except Exception:
            return {"running": True, "rss_mb": 0, "pid": pid, "note": "could not read RAM on Windows"}
    # Linux: /proc/<pid>/status
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return {"running": True, "rss_mb": kb // 1024, "pid": pid}
    except (FileNotFoundError, ValueError, OSError):
        pass
    return {"running": True, "rss_mb": 0, "pid": pid, "note": "could not read /proc"}


def _find_process(name: str) -> List[int]:
    if os.name == "nt":
        try:
            import subprocess
            out = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5).stdout
            pids = []
            for line in out.splitlines():
                if name.lower() in line.lower():
                    parts = line.split(",")
                    pids.append(int(parts[1].strip('"')))
            return pids
        except Exception:
            return []
    try:
        pids = []
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                with open(f"/proc/{d}/comm") as f:
                    if name in f.read().strip():
                        pids.append(int(d))
            except (FileNotFoundError, OSError):
                pass
        return pids
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Portability — move models to another machine
# ---------------------------------------------------------------------------

def export_bundle(out_dir: Path, model_keys: Optional[List[str]] = None) -> Dict[str, Any]:
    """Copy selected models + Engine binaries + a manifest into out_dir.

    The result is a portable folder the user can copy to another
    machine and drop into that machine's ElectronicsDiagnostic/ tree.

    If ``model_keys`` is None, every present candidate model is bundled.
    If it is an empty list, ALL ``.gguf`` files in Models/ are bundled
    (so user-downloaded models not in the candidate catalog still travel).
    If it is a non-empty list, only the named candidate keys are bundled
    (plus any extra ``.gguf`` files present, since they may be custom).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    models_out = out_dir / "Models"
    engine_out = out_dir / "Engine"
    models_out.mkdir(exist_ok=True)
    engine_out.mkdir(exist_ok=True)
    manifest: Dict[str, Any] = {"models": [], "engine": [], "settings": {}}
    src_models_dir = config.app_dir("Models")
    # Pick the set of filenames to bundle.
    wanted_files: set = set()
    for m in list_models():
        if model_keys is not None and m.key not in model_keys:
            continue
        if m.exists:
            wanted_files.add(m.filename)
    # Always include any extra .gguf files in Models/ (custom downloads).
    if model_keys is None or model_keys == []:
        for p in src_models_dir.glob("*.gguf"):
            wanted_files.add(p.name)
    for fname in sorted(wanted_files):
        src = src_models_dir / fname
        if not src.exists():
            continue
        dest = models_out / fname
        if not dest.exists():
            dest.write_bytes(src.read_bytes())
        sha = sha256_file(dest)
        manifest["models"].append({
            "filename": fname, "sha256": sha,
            "size_bytes": dest.stat().st_size,
        })
    # Engine binaries
    engine_dir = config.app_dir("Engine")
    for p in engine_dir.iterdir():
        if p.is_file() and p.name.lower().startswith(("llama", "main", "server")):
            dest = engine_out / p.name
            if not dest.exists():
                dest.write_bytes(p.read_bytes())
            manifest["engine"].append(p.name)
    manifest["settings"] = runtime_settings()
    (out_dir / "bundle_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "out_dir": str(out_dir),
            "models": len(manifest["models"]),
            "engine_files": len(manifest["engine"])}


def import_bundle(src_dir: Path) -> Dict[str, Any]:
    """Import a portable bundle into the local Models/ and Engine/."""
    src_dir = Path(src_dir)
    manifest_path = src_dir / "bundle_manifest.json"
    if not manifest_path.exists():
        return {"ok": False, "note": "no bundle_manifest.json in source dir"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    models_dir = config.app_dir("Models")
    engine_dir = config.app_dir("Engine")
    n_models = 0
    for m in manifest.get("models", []):
        src = src_dir / "Models" / m["filename"]
        if not src.exists():
            continue
        dest = models_dir / m["filename"]
        dest.write_bytes(src.read_bytes())
        actual = sha256_file(dest)
        m["verified"] = (actual == m["sha256"])
        if m["verified"]:
            n_models += 1
    n_engine = 0
    for name in manifest.get("engine", []):
        src = src_dir / "Engine" / name
        if src.exists():
            (engine_dir / name).write_bytes(src.read_bytes())
            n_engine += 1
    if manifest.get("settings"):
        set_runtime_settings(**manifest["settings"])
    return {"ok": True, "models_imported": n_models,
            "engine_imported": n_engine}
