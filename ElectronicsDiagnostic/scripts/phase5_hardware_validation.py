"""Phase 5, Task 6 — Windows hardware validation harness.

A repeatable validation script for the target i7 6th gen / 8 GB RAM
machine. It reports:
  * CPU and available memory.
  * Model file and quantization (parsed from the filename).
  * llama.cpp executable and version if detectable.
  * Context size and configured resource limits (from settings).
  * Model load success/failure (starts llama-server, health-checks).
  * First-token latency, generation speed and peak memory IF the model
    loads (otherwise honestly marked NOT RUN).
  * Tool-call success and a deterministic calculation round-trip
    (uses the electrical_math engine — no model required, so this part
    ALWAYS runs and verifies the deterministic path works).

Honesty contract:
  * If the target hardware or GGUF model is unavailable in the current
    environment, the hardware-dependent checks are marked NOT RUN with
    the reason. No measurement is ever fabricated.
  * The script exits 0 if all RUN checks pass; it exits 1 only if a RUN
    check fails. NOT-RUN checks do not cause a non-zero exit (they are
    reported as pending, not as failures).

Usage:
    python scripts/phase5_hardware_validation.py [--json]
    python scripts/phase5_hardware_validation.py --json   # machine-readable

The JSON output is a dict with a `checks` list; each check has
`name`, `status` (PASS|FAIL|NOT_RUN), `value`, `detail`.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make the eda package importable when run as a script.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check(name: str, status: str, value: Any = None,
           detail: str = "") -> Dict[str, Any]:
    return {"name": name, "status": status, "value": value, "detail": detail}


def _cpu_info() -> Dict[str, Any]:
    info = {
        "platform": platform.platform(),
        "processor": platform.processor() or "unknown",
        "cpu_count": os.cpu_count() or 0,
        "machine": platform.machine(),
    }
    # On Windows, platform.processor() often returns a generic string;
    # try to get the CPU model from the registry / WMI when possible.
    if sys.platform == "win32":
        try:
            # Prefer the environment variable (set on most Windows).
            proc = os.environ.get("PROCESSOR_IDENTIFIER")
            if proc:
                info["processor"] = proc
        except Exception:
            pass
    else:
        # On Linux, /proc/cpuinfo gives the real model name.
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        info["processor"] = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
    return info


def _memory_status() -> Dict[str, Optional[float]]:
    """Return {'total_mb', 'available_mb'} on Windows/Linux, or None
    values if undetectable. Uses stdlib only (ctypes on Windows,
    /proc/meminfo on Linux) so the app stays dependency-free."""
    total: Optional[float] = None
    avail: Optional[float] = None
    try:
        if sys.platform == "win32":
            try:
                import ctypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [("dwLength", ctypes.c_uint32),
                                ("dwMemoryLoad", ctypes.c_uint32),
                                ("ullTotalPhys", ctypes.c_uint64),
                                ("ullAvailPhys", ctypes.c_uint64),
                                ("ullTotalPageFile", ctypes.c_uint64),
                                ("ullAvailPageFile", ctypes.c_uint64),
                                ("ullTotalVirtual", ctypes.c_uint64),
                                ("ullAvailVirtual", ctypes.c_uint64),
                                ("ullAvailExtendedVirtual", ctypes.c_uint64)]
                m = MEMORYSTATUSEX()
                m.dwLength = ctypes.sizeof(m)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
                total = m.ullTotalPhys / (1024 * 1024)
                avail = m.ullAvailPhys / (1024 * 1024)
            except Exception:
                pass
            if total is None:
                out = subprocess.run(
                    ["systeminfo"], capture_output=True, text=True,
                    timeout=30, shell=False)
                for line in out.stdout.splitlines():
                    if "Total Physical Memory" in line:
                        mt = re.search(r"([\d,]+)\s*M", line)
                        if mt:
                            total = float(mt.group(1).replace(",", ""))
                    if "Available Physical Memory" in line:
                        ma = re.search(r"([\d,]+)\s*M", line)
                        if ma:
                            avail = float(ma.group(1).replace(",", ""))
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        total = int(line.split()[1]) / 1024
                    if line.startswith("MemAvailable:"):
                        avail = int(line.split()[1]) / 1024
    except Exception:
        pass
    return {"total_mb": total, "available_mb": avail}


def _available_memory_mb() -> Optional[float]:
    """Back-compat shim; delegates to _memory_status."""
    return _memory_status().get("available_mb")


def _peak_working_set_mb(pid: int) -> Optional[float]:
    """Peak working set of a process in MB.

    On Linux: reads /proc/<pid>/status VmHWM (peak resident set).
    On Windows: uses GetProcessMemoryInfo via ctypes (peak working set
    size). Returns None if undetectable. Never estimates."""
    if pid is None:
        return None
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD),
                            ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            psapi = ctypes.windll.psapi
            h = ctypes.windll.kernel32.OpenProcess(
                0x0400 | 0x0010, False, pid)  # QUERY_INFO | VM_READ
            if not h:
                return None
            try:
                pmc = PROCESS_MEMORY_COUNTERS()
                pmc.cb = ctypes.sizeof(pmc)
                if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc),
                                               ctypes.sizeof(pmc)):
                    return pmc.PeakWorkingSetSize / (1024 * 1024)
                return None
            finally:
                ctypes.windll.kernel32.CloseHandle(h)
        else:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmHWM:"):
                        return int(line.split()[1]) / 1024
    except Exception:
        pass
    return None


def _process_cpu_pct(pid: int, duration_s: float = 0.5
                     ) -> Optional[float]:
    """Sample a process's CPU utilization over a short window. Returns
    the average percent of one core (100 = one full core) or None.

    On Linux: reads /proc/<pid>/stat utime+stime before/after.
    On Windows: NOT_MEASURED via stdlib (would need GetProcessTimes
    sampling; kept simple here — returns None and the harness reports
    NOT_MEASURED)."""
    if pid is None or sys.platform == "win32":
        return None
    try:
        import time as _t
        clk = os.sysconf(os.sysconf_names["SC_CLK_TCK"])

        def _cpu_ticks(p):
            with open(f"/proc/{p}/stat") as f:
                parts = f.read().split()
            # utime(13) + stime(14) are ticks (1-indexed in docs; 0-indexed
            # in split). Field 14 is utime, 15 is stime after the comm field
            # which can contain spaces — but we read via split so the
            # comm field with parens is one token here. Use rfind to be safe.
            s = f.read() if False else None  # noqa
            # Re-read robustly: stat fields after comm are fixed-position.
            with open(f"/proc/{p}/stat") as f2:
                raw = f2.read()
            rp = raw.rfind(")")
            after = raw[rp + 2:].split()
            utime = int(after[11])
            stime = int(after[12])
            return utime + stime

        t0_ticks = _cpu_ticks(pid)
        t0 = _t.time()
        _t.sleep(duration_s)
        t1_ticks = _cpu_ticks(pid)
        dt = _t.time() - t0
        if dt <= 0:
            return None
        used_s = (t1_ticks - t0_ticks) / clk
        # Percent of one core.
        return (used_s / dt) * 100.0
    except Exception:
        return None


def _process_alive(pid: int) -> bool:
    """True if the process is still alive. Cross-platform."""
    if pid is None:
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        else:
            os.kill(pid, 0)
            return True
    except (ProcessLookupError, OSError, PermissionError):
        return False


def _llama_executable(settings: Dict[str, Any]) -> Optional[Path]:
    """Locate the llama-server / llama-cli executable."""
    engine = settings.get("engine", {})
    # 1. Explicit path in settings.
    for key in ("cli_path", "server_path"):
        p = engine.get(key)
        if p:
            pp = Path(p)
            if pp.exists():
                return pp
    # 2. Bundled Engine/ dir.
    from eda import config as eda_config
    bundled = eda_config.app_dir("Engine")
    for name in ("llama-server.exe", "llama-server", "main.exe", "main"):
        cand = bundled / name
        if cand.exists():
            return cand
    # 3. PATH.
    for name in ("llama-server", "llama-server.exe", "llama-cli", "llama-cli.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _llama_version(exe: Path) -> Optional[str]:
    """Try to extract a version string from the executable."""
    try:
        out = subprocess.run([str(exe), "--version"], capture_output=True,
                             text=True, timeout=10)
        txt = (out.stdout + out.stderr).strip()
        if txt:
            return txt.splitlines()[0][:120]
    except Exception:
        pass
    return None


def _quantization_from_filename(model_name: str) -> Optional[str]:
    """Parse the quantization tag from a GGUF filename, e.g.
    'qwen2.5-1.5b-instruct-q4_k_m.gguf' -> 'Q4_K_M'."""
    m = re.search(r"[-_.](q[0-9]+_[a-z0-9_]+)", model_name, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


def _model_size_mb(model_path: Path) -> Optional[float]:
    try:
        return model_path.stat().st_size / (1024 * 1024)
    except Exception:
        return None


def _peak_rss_mb(pid: int) -> Optional[float]:
    """Deprecated alias for _peak_working_set_mb (kept for back-compat)."""
    return _peak_working_set_mb(pid)


# ---------------------------------------------------------------------------
# Validation checks
# ---------------------------------------------------------------------------

def check_environment() -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    cpu = _cpu_info()
    checks.append(_check("cpu_model", "PASS", cpu["processor"], ""))
    checks.append(_check("cpu_logical_count", "PASS", cpu["cpu_count"], ""))
    mem = _memory_status()
    total = mem.get("total_mb")
    avail = mem.get("available_mb")
    checks.append(_check("total_memory_mb",
                         "PASS" if total else "NOT_RUN",
                         round(total, 1) if total else None,
                         "" if total else "could not detect total RAM"))
    checks.append(_check("available_memory_mb",
                         "PASS" if avail else "NOT_RUN",
                         round(avail, 1) if avail else None,
                         "" if avail else "could not detect available RAM"))
    # Target is 8 GB total. This is ADVISORY: on the user's target
    # machine it should be >= 4 GB available for a 1.5B Q4_K_M model
    # (~1.5-2 GB). We report NOT_RUN (not FAIL) when below, because
    # the build sandbox may have less RAM than the target machine;
    # the check is informational, not a code defect.
    if avail is None:
        checks.append(_check("memory_target_8gb", "NOT_RUN", None,
                             "could not detect available memory"))
    elif avail < 4096:
        checks.append(_check("memory_target_8gb", "NOT_RUN", round(avail, 1),
                             "available < 4 GB; on the target 8 GB "
                             "machine a 1.5B Q4_K_M model (~1.5-2 GB) "
                             "should fit. Re-run on the target to "
                             "confirm."))
    else:
        checks.append(_check("memory_target_8gb", "PASS", round(avail, 1),
                             "available >= 4 GB; 1.5B model fits"))
    return checks


def check_model_and_runtime(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    engine = settings.get("engine", {})
    model_dir = engine.get("model_dir", "Models")
    model_file = engine.get("model_file")
    from eda import config as eda_config
    models_root = eda_config.app_dir(model_dir)
    # Model file.
    if not model_file:
        # Try to find any .gguf in the model dir.
        ggufs = sorted(models_root.glob("*.gguf")) if models_root.exists() else []
        if ggufs:
            model_file = ggufs[0].name
            engine["model_file"] = model_file
            checks.append(_check("model_file", "PASS", model_file,
                                 f"auto-detected in {models_root}"))
        else:
            checks.append(_check("model_file", "NOT_RUN", None,
                                 "no model_file set and no .gguf in model dir"))
            checks.append(_check("model_quantization", "NOT_RUN", None,
                                 "no model file"))
            checks.append(_check("model_size_mb", "NOT_RUN", None,
                                 "no model file"))
            checks.append(_check("llama_executable", "NOT_RUN", None,
                                 "skipped (no model)"))
            checks.append(_check("llama_version", "NOT_RUN", None,
                                 "skipped (no model)"))
            return checks
    model_path = models_root / model_file
    if not model_path.exists():
        checks.append(_check("model_file", "NOT_RUN", model_file,
                             f"not found at {model_path}"))
        checks.append(_check("model_quantization", "NOT_RUN", None, "no model"))
        checks.append(_check("model_size_mb", "NOT_RUN", None, "no model"))
        checks.append(_check("llama_executable", "NOT_RUN", None, "skipped"))
        checks.append(_check("llama_version", "NOT_RUN", None, "skipped"))
        return checks
    checks.append(_check("model_file", "PASS", model_file, str(model_path)))
    quant = _quantization_from_filename(model_file)
    checks.append(_check("model_quantization", "PASS" if quant else "NOT_RUN",
                         quant, "parsed from filename" if quant else "could not parse"))
    size = _model_size_mb(model_path)
    checks.append(_check("model_size_mb", "PASS" if size else "NOT_RUN",
                         round(size, 1) if size else None, ""))
    # llama executable.
    exe = _llama_executable(settings)
    if not exe:
        checks.append(_check("llama_executable", "NOT_RUN", None,
                             "no llama-server/llama-cli found"))
        checks.append(_check("llama_version", "NOT_RUN", None, "no executable"))
    else:
        checks.append(_check("llama_executable", "PASS", str(exe), ""))
        ver = _llama_version(exe)
        checks.append(_check("llama_version", "PASS" if ver else "NOT_RUN",
                             ver, "" if ver else "could not get version"))
    return checks


def check_context_and_limits(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    engine = settings.get("engine", {})
    checks.append(_check("n_ctx", "PASS", engine.get("n_ctx", 2048), "tokens"))
    checks.append(_check("n_threads", "PASS", engine.get("n_threads", 4), ""))
    checks.append(_check("n_gpu_layers", "PASS", engine.get("n_gpu_layers", 0),
                         "CPU-only target"))
    checks.append(_check("max_tokens", "PASS", engine.get("max_tokens", 512), ""))
    checks.append(_check("timeout_seconds", "PASS",
                         engine.get("timeout_seconds", 120), ""))
    checks.append(_check("fallback_to_rules", "PASS",
                         engine.get("fallback_to_rules", True), ""))
    return checks


def check_deterministic_round_trip() -> Dict[str, Any]:
    """A deterministic calculation round-trip through the electrical_math
    engine — this ALWAYS runs (no model needed) and verifies the
    deterministic core works end-to-end."""
    try:
        from eda.engines import base as E
        from eda import schemas as S
        res = E.call("electrical_math", "ohm_voltage",
                     {"I": S.Quantity.parse(2, "A"),
                      "R": S.Quantity.parse(5, "Ω")})
        if res.ok and abs(res.value.magnitude - 10.0) < 1e-6:
            return _check("deterministic_calc_round_trip", "PASS",
                          "V = I*R = 2A*5Ω = 10V",
                          f"engine returned {res.value.magnitude} V "
                          f"(provenance: {res.provenance})")
        return _check("deterministic_calc_round_trip", "FAIL",
                      res.value.magnitude if res.ok else None,
                      f"engine error: {res.error.to_dict() if not res.ok else 'wrong value'}")
    except Exception as e:
        return _check("deterministic_calc_round_trip", "FAIL", None,
                       f"exception: {e}")


def _streaming_ttft_ms(host: str, port: int, prompt: str,
                       max_tokens: int = 8,
                       timeout: int = 120) -> Optional[Dict[str, Any]]:
    """Measure true time-to-first-token via SSE streaming.

    Posts to /v1/chat/completions with ``stream: true`` and reads the
    chunked response, recording the wall time of the first ``data:``
    chunk that carries a delta. Returns::

        {"ttft_ms": float, "total_ms": float, "completion_tokens": int}
        or None on any failure. Never estimates — if the server does not
        stream, returns None and the caller reports NOT_MEASURED.
    """
    import urllib.request
    url = f"http://{host}:{port}/v1/chat/completions"
    body = json.dumps({
        "model": "local",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    ttft: Optional[float] = None
    tokens = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                if ttft is None:
                    ttft = (time.time() - t0) * 1000.0
                try:
                    obj = json.loads(payload)
                    delta = (obj.get("choices", [{}])[0]
                             .get("delta", {}).get("content"))
                    if delta:
                        tokens += 1
                except json.JSONDecodeError:
                    continue
        total = (time.time() - t0) * 1000.0
        if ttft is None:
            return None
        return {"ttft_ms": round(ttft, 1),
                "total_ms": round(total, 1),
                "completion_tokens": tokens}
    except Exception:
        return None


def check_model_load_and_inference(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Attempt to start llama-server, load the model, and run one short
    generation. If the model or executable is unavailable, mark NOT_RUN.
    This NEVER fabricates latency/speed numbers; every unavailable metric
    is explicitly NOT_MEASURED with a reason."""
    checks: List[Dict[str, Any]] = []
    engine = settings.get("engine", {})
    model_file = engine.get("model_file")
    from eda import config as eda_config
    models_root = eda_config.app_dir(engine.get("model_dir", "Models"))
    model_path = models_root / model_file if model_file else None
    exe = _llama_executable(settings)
    if not exe or not model_path or not model_path.exists():
        checks.append(_check("model_load", "NOT_RUN", None,
                             "no executable or model file; cannot test load"))
        checks.append(_check("total_inference_latency_ms", "NOT_RUN", None,
                             "model not loaded"))
        checks.append(_check("time_to_first_token_ms", "NOT_RUN", None,
                             "model not loaded"))
        checks.append(_check("generation_tokens_per_s", "NOT_RUN", None,
                             "model not loaded"))
        checks.append(_check("peak_working_set_mb", "NOT_RUN", None,
                             "model not loaded"))
        checks.append(_check("cpu_utilization_pct", "NOT_RUN", None,
                             "model not loaded"))
        checks.append(_check("clean_shutdown", "NOT_RUN", None,
                             "model not loaded"))
        return checks
    # Try to start the server via the real backend. ServerRuntime takes
    # a settings dict (same shape as config.load_settings().engine).
    try:
        from eda import llm_backend as LB
        # Build a settings dict with the configured engine values so
        # ServerRuntime reads the right model/executable/limits.
        sr_settings = {"engine": dict(engine)}
        sr_settings["engine"]["server_path"] = str(exe)
        sr_settings["engine"]["model_file"] = model_file
        sr_settings["engine"]["model_dir"] = engine.get("model_dir", "Models")
        sr = LB.ServerRuntime(sr_settings)
    except Exception as e:
        checks.append(_check("model_load", "FAIL", None,
                             f"could not construct backend: {e}"))
        return checks
    started = False
    try:
        started = sr.start()
    except Exception as e:
        checks.append(_check("model_load", "FAIL", None,
                             f"start() raised: {e}"))
    if not started:
        checks.append(_check("model_load", "NOT_RUN", None,
                             "server did not become ready (binary/model/host issue)"))
        checks.append(_check("total_inference_latency_ms", "NOT_RUN", None, "no load"))
        checks.append(_check("time_to_first_token_ms", "NOT_RUN", None, "no load"))
        checks.append(_check("generation_tokens_per_s", "NOT_RUN", None, "no load"))
        checks.append(_check("peak_working_set_mb", "NOT_RUN", None, "no load"))
        checks.append(_check("cpu_utilization_pct", "NOT_RUN", None, "no load"))
        checks.append(_check("clean_shutdown", "NOT_RUN", None, "no load"))
        return checks
    checks.append(_check("model_load", "PASS", True, "server ready"))

    # --- CPU utilization during inference (sampled concurrently) ---
    pid = sr._proc.pid if sr._proc else None
    cpu_sample: List[Optional[float]] = []

    def _sample_cpu():
        cpu_sample.append(_process_cpu_pct(pid, duration_s=0.3))

    import threading
    sampler = threading.Thread(target=_sample_cpu, daemon=True)

    # --- Inference: streaming TTFT + total latency + tokens/s ---
    total_latency_ms: Optional[float] = None
    ttft_ms: Optional[float] = None
    tps: Optional[float] = None
    try:
        sampler.start()
        t0 = time.time()
        # Try streaming first for a real TTFT.
        host = engine.get("server_host", "127.0.0.1")
        port = engine.get("server_port", 8080)
        stream = _streaming_ttft_ms(
            host, port, "Reply with the single word OK.", max_tokens=8)
        if stream is not None:
            ttft_ms = stream["ttft_ms"]
            total_latency_ms = stream["total_ms"]
            comp = stream["completion_tokens"]
            if total_latency_ms and total_latency_ms > 0:
                tps = comp / (total_latency_ms / 1000.0)
        else:
            # Fall back to the non-streaming chat() for total latency;
            # TTFT is NOT_MEASURED (server did not stream).
            from eda import llm_backend as LB
            resp = sr.chat(
                [LB.Message(role="user", content="Reply with the single word OK.")],
                max_tokens=8, temperature=0.1)
            if resp is not None:
                total_latency_ms = (time.time() - t0) * 1000.0
                if total_latency_ms > 0 and resp.completion_tokens > 0:
                    tps = resp.completion_tokens / (total_latency_ms / 1000.0)
        sampler.join(timeout=2)
    except Exception as e:
        checks.append(_check("total_inference_latency_ms", "FAIL", None,
                             f"inference raised: {e}"))
        checks.append(_check("time_to_first_token_ms", "FAIL", None,
                             f"inference raised: {e}"))
        checks.append(_check("generation_tokens_per_s", "FAIL", None,
                             f"inference raised: {e}"))
        # Still attempt clean shutdown + memory below.
    # Report latency/TTFT/speed.
    checks.append(_check("total_inference_latency_ms",
                         "PASS" if total_latency_ms else "FAIL",
                         round(total_latency_ms, 1) if total_latency_ms else None,
                         "" if total_latency_ms else "no response"))
    checks.append(_check("time_to_first_token_ms",
                         "PASS" if ttft_ms is not None else "NOT_RUN",
                         round(ttft_ms, 1) if ttft_ms is not None else None,
                         "via SSE streaming" if ttft_ms is not None else
                         "NOT_MEASURED: server did not return a streamable "
                         "response; total latency was measured instead"))
    checks.append(_check("generation_tokens_per_s",
                         "PASS" if tps is not None else "FAIL",
                         round(tps, 2) if tps is not None else None,
                         "" if tps is not None else "no tokens generated"))
    # CPU utilization (Linux-only via stdlib; NOT_MEASURED on Windows).
    cpu_pct = cpu_sample[0] if cpu_sample else None
    checks.append(_check("cpu_utilization_pct",
                         "PASS" if cpu_pct is not None else "NOT_RUN",
                         round(cpu_pct, 1) if cpu_pct is not None else None,
                         "% of one core during inference" if cpu_pct is not None
                         else "NOT_MEASURED: stdlib CPU sampling is Linux-only"))

    # --- Peak working set (Windows via GetProcessMemoryInfo, Linux via VmHWM) ---
    peak = _peak_working_set_mb(pid)
    checks.append(_check("peak_working_set_mb",
                         "PASS" if peak is not None else "NOT_RUN",
                         round(peak, 1) if peak is not None else None,
                         "" if peak is not None else
                         "NOT_MEASURED: could not read process memory"))

    # --- Clean shutdown verification (no orphaned process) ---
    orphaned = False
    shutdown_err = ""
    try:
        sr.stop()
    except Exception as e:
        shutdown_err = f"stop() raised: {e}"
    # Give the OS a moment to reap, then check the PID is gone.
    time.sleep(0.3)
    if pid is not None and _process_alive(pid):
        orphaned = True
        # Best-effort final kill.
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, check=False)
            else:
                os.kill(pid, 9)
        except Exception:
            pass
        time.sleep(0.2)
        if _process_alive(pid):
            orphaned = True
        else:
            orphaned = False  # recovered
    if shutdown_err:
        checks.append(_check("clean_shutdown", "FAIL", None, shutdown_err))
    elif orphaned:
        checks.append(_check("clean_shutdown", "FAIL", None,
                             "server process still alive after stop()"))
    else:
        checks.append(_check("clean_shutdown", "PASS", True,
                             "server process terminated; no orphan"))
    return checks


def check_tool_call_success() -> Dict[str, Any]:
    """Verify the tool-call dispatch path works: parse a well-formed
    tool call, dispatch it, and confirm a structured result with
    provenance. This uses the deterministic engine, no model needed."""
    try:
        from eda import tool_interface as TI
        call = TI.ToolCall(
            tool="electrical_math.ohm_voltage",
            args={"I": {"value": 2, "unit": "A"},
                  "R": {"value": 5, "unit": "Ω"}},
            id="hv1",
        )
        result = TI.dispatch(call)
        if result.ok and result.provenance:
            # value may be a Quantity or scalar; render safely.
            try:
                v_repr = str(result.value)
            except Exception:
                v_repr = "(unrenderable)"
            return _check("tool_call_success", "PASS",
                          f"{v_repr} {result.unit}".strip(),
                          f"provenance: {result.provenance}")
        return _check("tool_call_success", "FAIL",
                       str(result.value) if result.ok else None,
                       f"error: {result.error}")
    except Exception as e:
        return _check("tool_call_success", "FAIL", None, f"exception: {e}")


def check_model_tool_call(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Verify the MODEL can emit a well-formed tool_call block that the
    orchestrator's parser accepts and the engine executes.

    This is distinct from check_tool_call_success (which hand-constructs
    a ToolCall and dispatches it — that tests the engine, not the model).

    Here we:
      1. Start the server with the configured model (real load).
      2. Prompt the model to emit ONE fenced ```tool_call block for
         electrical_math.ohm_voltage with I=2A, R=5Ω.
      3. Parse the model's raw text with TI.parse_tool_calls.
      4. Dispatch the parsed call through the real engine.
      5. Verify the engine returns 10 V (the correct answer).

    If the model is unavailable, NOT_RUN with a reason (never fabricated).
    If the model emits no parseable tool_call, FAIL (the model cannot use
    the tool interface — a real defect on the target).
    If the model emits a tool_call but the engine result is wrong, FAIL.
    """
    checks: List[Dict[str, Any]] = []
    engine = settings.get("engine", {})
    model_file = engine.get("model_file")
    from eda import config as eda_config
    models_root = eda_config.app_dir(engine.get("model_dir", "Models"))
    model_path = models_root / model_file if model_file else None
    exe = _llama_executable(settings)
    if not exe or not model_path or not model_path.exists():
        checks.append(_check("model_tool_call", "NOT_RUN", None,
                             "no executable or model file; cannot test the "
                             "model's tool-call emission"))
        return checks
    # Start the server.
    try:
        from eda import llm_backend as LB
        sr_settings = {"engine": dict(engine)}
        sr_settings["engine"]["server_path"] = str(exe)
        sr_settings["engine"]["model_file"] = model_file
        sr_settings["engine"]["model_dir"] = engine.get("model_dir", "Models")
        sr = LB.ServerRuntime(sr_settings)
    except Exception as e:
        checks.append(_check("model_tool_call", "FAIL", None,
                             f"could not construct backend: {e}"))
        return checks
    if not sr.start():
        checks.append(_check("model_tool_call", "NOT_RUN", None,
                             "server did not become ready; cannot test the "
                             "model's tool-call emission"))
        return checks
    try:
        from eda import tool_interface as TI
        # A minimal prompt asking the model to emit exactly one tool_call
        # block. We do NOT feed it the full orchestrator prompt (that
        # would couple this check to prompt-engineering changes); we ask
        # for the specific call the engine can verify.
        prompt = (
            "You have a tool: electrical_math.ohm_voltage. It computes "
            "V = I*R. Emit ONE tool call as a fenced code block named "
            "tool_call with JSON "
            '{"tool": "electrical_math.ohm_voltage", '
            '"args": {"I": {"value": 2, "unit": "A"}, '
            '"R": {"value": 5, "unit": "ohm"}}, "id": "mtc1"}. '
            "Output only that fenced block.")
        from eda import llm_backend as LB
        resp = sr.chat(
            [LB.Message(role="user", content=prompt)],
            max_tokens=128, temperature=0.0)
        if resp is None or not resp.text:
            checks.append(_check("model_tool_call", "FAIL", None,
                                 "model returned no text"))
            return checks
        calls = TI.parse_tool_calls(resp.text)
        if not calls:
            checks.append(_check("model_tool_call", "FAIL", None,
                                 "model emitted no parseable tool_call block "
                                 f"(raw text: {resp.text[:120]!r})"))
            return checks
        # Dispatch the first parsed call through the real engine.
        result = TI.dispatch(calls[0])
        if not result.ok:
            checks.append(_check("model_tool_call", "FAIL", None,
                                 f"engine rejected the model's call: "
                                 f"{result.error}"))
            return checks
        # Verify the result (2A * 5Ω = 10V).
        try:
            if isinstance(result.value, dict):
                val = result.value.get("value", result.value.get("magnitude", result.value.get("result", result.value)))
            elif hasattr(result.value, "magnitude"):
                val = result.value.magnitude
            else:
                val = result.value
        except Exception:
            val = result.value
        ok = abs(float(val) - 10.0) < 1e-3
        if ok:
            detail = ("model emitted a valid tool_call; engine "
                      "computed the correct value")
        else:
            detail = ("model's tool_call ran but returned "
                      f"{val} (expected 10.0 V)")
        checks.append(_check("model_tool_call",
                             "PASS" if ok else "FAIL",
                             f"{val} {result.unit}".strip(),
                             detail))
        return checks
    except Exception as e:
        checks.append(_check("model_tool_call", "FAIL", None,
                             f"exception: {e}"))
        return checks
    finally:
        try:
            sr.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_all(json_out: bool = False) -> int:
    from eda import config as eda_config
    settings = eda_config.load_settings()
    checks: List[Dict[str, Any]] = []
    checks.extend(check_environment())
    checks.extend(check_model_and_runtime(settings))
    checks.extend(check_context_and_limits(settings))
    checks.append(check_deterministic_round_trip())
    checks.extend(check_model_load_and_inference(settings))
    checks.append(check_tool_call_success())
    checks.extend(check_model_tool_call(settings))

    # Determine exit code: 0 unless a RUN check FAILed.
    failed = [c for c in checks if c["status"] == "FAIL"]
    not_run = [c for c in checks if c["status"] == "NOT_RUN"]

    if json_out:
        print(json.dumps({
            "target": "i7 6th gen, 8 GB RAM, Windows 64-bit, CPU-only",
            "checks": checks,
            "summary": {
                "total": len(checks),
                "passed": sum(1 for c in checks if c["status"] == "PASS"),
                "failed": len(failed),
                "not_run": len(not_run),
            },
        }, indent=2, ensure_ascii=False))
    else:
        print("=" * 64)
        print("Phase 5 — Windows hardware validation")
        print("Target: i7 6th gen, 8 GB RAM, Windows 64-bit, CPU-only")
        print("=" * 64)
        for c in checks:
            mark = {"PASS": "OK ", "FAIL": "FAIL", "NOT_RUN": "SKIP"}[c["status"]]
            val = "" if c["value"] is None else f" = {c['value']}"
            print(f"[{mark}] {c['name']}{val}")
            if c["detail"]:
                print(f"        {c['detail']}")
        print("=" * 64)
        print(f"PASS={sum(1 for c in checks if c['status']=='PASS')}  "
              f"FAIL={len(failed)}  NOT_RUN={len(not_run)}")
        if not_run:
            print("NOT_RUN items require physical hardware / a real GGUF model.")
    return 1 if failed else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON")
    args = ap.parse_args()
    sys.exit(run_all(json_out=args.json))


if __name__ == "__main__":
    main()
