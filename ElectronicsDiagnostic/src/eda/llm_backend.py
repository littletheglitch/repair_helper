"""LLM backend abstraction.

A single :class:`Backend` protocol the orchestrator talks to, with
several concrete implementations:

* :class:`StubBackend` — deterministic, no model. Used by tests and as
  the fallback when no model is configured. It emits *correct* tool
  calls for the diagnostic cases it knows and an honest "I don't have
  enough information" refusal otherwise. This is the upper-bound
  reference for the benchmark: a perfect tool-caller.
* :class:`CloudBackend` — calls an external OpenAI-compatible chat CLI
  (``z-ai chat`` in this sandbox). Used only as a *reference* to prove
  the architecture works with a real model; the target deployment does
  not use this. It is opt-in and never the default.
* :class:`OpenAIServerBackend` — talks to a *persistent* local
  llama.cpp server (``llama-server``) over HTTP. This is the real
  target backend. The server is started once and kept alive across
  requests so the model is loaded only once (fixes the "new process
  per request" problem in the original engine).
* :class:`LlamaCliBackend` — legacy one-shot ``llama-cli`` subprocess
  path, kept for compatibility with the existing tests and
  ``prepare_model.py`` flow. Not recommended for interactive use.

The orchestrator never depends on which backend is in use; it only
calls :meth:`Backend.chat` and gets back a string (the model's raw
output, which it then parses for tool calls).

Persistence (the important fix):

The original ``llm_engine._call_llama_cli`` spawned a fresh
``llama-cli`` process per request, reloading the GGUF every time. On a
6th-gen i7 CPU that is 5-30 seconds of model load per request before
any generation. :class:`ServerRuntime` starts ``llama-server`` once,
holds the process, and routes every request through HTTP to the same
loaded model. The runtime is a module-level singleton so a single
server serves the whole CLI session.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from . import config


# ---------------------------------------------------------------------------
# Message types and the Backend protocol
# ---------------------------------------------------------------------------

@dataclass
class Message:
    role: str            # "system" | "user" | "assistant" | "tool"
    content: str
    tool_call_id: str = ""   # for role=="tool": the id this answers

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


@dataclass
class BackendResponse:
    text: str
    backend: str
    model: str
    elapsed_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    note: str = ""

    @property
    def tok_per_s(self) -> float:
        if self.elapsed_ms <= 0 or self.completion_tokens <= 0:
            return 0.0
        return self.completion_tokens / (self.elapsed_ms / 1000.0)


class Backend(Protocol):
    name: str
    model: str

    def is_available(self) -> bool: ...
    def availability_report(self) -> str: ...
    def chat(self, messages: List[Message],
             max_tokens: Optional[int] = None,
             temperature: Optional[float] = None) -> Optional[BackendResponse]: ...
    def shutdown(self) -> None: ...


# ---------------------------------------------------------------------------
# Stub backend (deterministic; the benchmark upper bound)
# ---------------------------------------------------------------------------

class StubBackend:
    """Deterministic backend used by tests and as the no-model fallback.

    It does not call any model. For a small set of well-known diagnostic
    shapes it emits the *correct* sequence of tool calls and a final
    answer grounded in the tool results; for anything else it emits an
    honest refusal. This makes the orchestrator + tool interface testable
    without a real model, and gives the benchmark a "perfect tool-caller"
    reference to compare real models against.
    """
    name = "stub"
    model = "stub-1.0"

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        self.settings = settings or config.load_settings()
        self._calls_emitted = 0

    def is_available(self) -> bool:
        return True

    def availability_report(self) -> str:
        return ("Backend: stub (deterministic reference, no model loaded)\n"
                "Available: True\n"
                "Emits correct tool calls for known cases; honest refusal otherwise.")

    def chat(self, messages: List[Message],
             max_tokens: Optional[int] = None,
             temperature: Optional[float] = None) -> Optional[BackendResponse]:
        # The stub inspects the last user message (the orchestrator's
        # prompt) and decides what a good tool-calling model would do.
        user_msg = ""
        for m in reversed(messages):
            if m.role == "user":
                user_msg = m.content
                break
        t0 = time.time()
        text = self._plan(user_msg, messages)
        return BackendResponse(
            text=text, backend="stub", model=self.model,
            elapsed_ms=int((time.time() - t0) * 1000),
            completion_tokens=len(text.split()),
        )

    def _plan(self, user_msg: str, history: List[Message]) -> str:
        # If we already have tool results in history, synthesize a final
        # answer grounded in them; otherwise emit the first tool call.
        # Pull the session id out of any system/context message we can see.
        sid = 0
        for m in history:
            m_low = m.content
            if "Session id:" in m_low:
                import re as _re
                mm = _re.search(r"Session id:\s*(\d+)", m_low)
                if mm:
                    sid = int(mm.group(1))
                    break
        tool_results = [m for m in history if m.role == "tool"]
        if not tool_results:
            # Heuristic: does the symptom text mention a voltage rail?
            low = user_msg.lower()
            if "5valw" in low or "3valw" in low or "no power" in low or "vin" in low:
                # Ask for the next-best-test engine first.
                return _json_tool_call("next_best_test.recommend",
                                       {"session_id": sid},
                                       self._calls_emitted)
            if "ohm" in low or "voltage" in low or "current" in low:
                return _json_tool_call("electrical_math.ohm_voltage",
                                       {"I": {"value": 2, "unit": "A"},
                                        "R": {"value": 5, "unit": "Ω"}},
                                       self._calls_emitted)
            # Unknown case: honest refusal.
            return ("I do not have enough information to choose a tool. "
                    "Please provide the symptom and at least one measurement.")
        # We have tool results: summarize them honestly.
        lines = ["Based on the tool results:"]
        for m in tool_results:
            try:
                r = json.loads(m.content)
            except json.JSONDecodeError:
                lines.append(f"  - {m.content}")
                continue
            if r.get("ok"):
                lines.append(f"  - {r['tool']}: {json.dumps(r.get('value'))} "
                             f"(provenance: {r.get('provenance','?')})")
            else:
                lines.append(f"  - {r['tool']} failed: {r.get('error')}")
        lines.append("")
        lines.append("I will not assert any voltage, pinout, or component "
                     "spec that is not in the tool results above.")
        return "\n".join(lines)

    def shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# OpenAI-compatible persistent server backend (the real target)
# ---------------------------------------------------------------------------

class ServerRuntime:
    """Manages a persistent ``llama-server`` process.

    The server is started once (model loaded into RAM once) and kept
    alive. Every request goes over HTTP to the same loaded model, so
    the 5-30s model-load penalty is paid once per session, not per
    request. This is the architectural fix for the original
    one-process-per-request design.

    Lifecycle:
    * :meth:`start` — spawn the server, wait for ``GET /health`` to
      return ok (polls up to ``startup_timeout``).
    * :meth:`stop` — terminate the process (SIGTERM then SIGKILL).
    * The runtime is a process singleton: the same loaded model serves
      the whole CLI session.
    """
    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        self.settings = settings or config.load_settings()
        self.cfg = self.settings.get("engine", {})
        self._proc: Optional[subprocess.Popen] = None
        self._port = int(self.cfg.get("server_port", 8080))
        self._host = self.cfg.get("server_host", "127.0.0.1")
        self._cli: Optional[Path] = None
        self._model: Optional[Path] = None

    # -- discovery -------------------------------------------------------
    def server_path(self) -> Optional[Path]:
        cfg_path = self.cfg.get("server_path") or self.cfg.get("cli_path")
        if cfg_path:
            p = Path(cfg_path)
            if p.exists():
                return p
        bundled = config.app_dir("Engine")
        for name in ("llama-server.exe", "llama-server", "server.exe", "server"):
            cand = bundled / name
            if cand.exists():
                return cand
        for name in ("llama-server", "llama-server.exe", "server", "server.exe"):
            on_path = shutil.which(name)
            if on_path:
                return Path(on_path)
        return None

    def model_path(self) -> Optional[Path]:
        name = self.cfg.get("model_file")
        if not name:
            models_dir = config.app_dir(self.cfg.get("model_dir", "Models"))
            ggufs = sorted(models_dir.glob("*.gguf"))
            return ggufs[0] if ggufs else None
        p = Path(name)
        if not p.is_absolute():
            p = config.app_dir(self.cfg.get("model_dir", "Models")) / name
        return p if p.exists() else None

    # -- lifecycle -------------------------------------------------------
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        if self.is_running():
            return True
        cli = self.server_path()
        model = self.model_path()
        if not cli or not model:
            return False
        n_ctx = int(self.cfg.get("n_ctx", 2048))
        n_threads = int(self.cfg.get("n_threads", 4))
        ngpu = int(self.cfg.get("n_gpu_layers", 0))
        n_batch = int(self.cfg.get("n_batch", 512))
        cmd = [
            str(cli),
            "-m", str(model),
            "--host", self._host,
            "--port", str(self._port),
            "-c", str(n_ctx),
            "-t", str(n_threads),
            "-ngl", str(ngpu),
            "-b", str(n_batch),
            "--cont-batching",
        ]
        try:
            # Detach stdout/stderr so a server banner does not block us.
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # New process group on Unix so we can kill the tree.
                start_new_session=True,
            )
        except (FileNotFoundError, OSError):
            return False
        return self._wait_ready()

    def _wait_ready(self, timeout: Optional[int] = None) -> bool:
        import urllib.request
        timeout = timeout or int(self.cfg.get("startup_timeout_seconds", 60))
        deadline = time.time() + timeout
        url = f"http://{self._host}:{self._port}/health"
        while time.time() < deadline:
            if self._proc and self._proc.poll() is not None:
                return False  # server died
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    if r.status == 200:
                        return True
            except Exception:
                time.sleep(0.5)
        return False

    def stop(self) -> None:
        if not self._proc:
            return
        try:
            if os.name == "nt":
                # On Windows, terminate() only stops the parent
                # process; llama-server may spawn worker threads that
                # survive as orphaned handles. Use taskkill /T to kill
                # the whole process tree. Fall back to terminate() if
                # taskkill is unavailable (it is on every supported
                # Windows since XP).
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID",
                         str(self._proc.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=3, check=False)
                except (FileNotFoundError, subprocess.TimeoutExpired,
                        OSError):
                    self._proc.terminate()
            else:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                self._proc.kill()
            except (ProcessLookupError, OSError):
                pass
        self._proc = None

    def shutdown(self) -> None:
        self.stop()

    # -- inference -------------------------------------------------------
    def chat(self, messages: List[Message],
             max_tokens: Optional[int] = None,
             temperature: Optional[float] = None) -> Optional[BackendResponse]:
        if not self.start():
            return None
        import urllib.request
        url = f"http://{self._host}:{self._port}/v1/chat/completions"
        body = {
            "model": self.cfg.get("model_file", "local"),
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature if temperature is not None
                           else float(self.cfg.get("temperature", 0.2)),
            "max_tokens": max_tokens or int(self.cfg.get("max_tokens", 512)),
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        t0 = time.time()
        timeout = int(self.cfg.get("timeout_seconds", 120))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except Exception:
            return None
        elapsed = int((time.time() - t0) * 1000)
        text = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = resp.get("usage", {})
        return BackendResponse(
            text=text, backend="llama_server",
            model=str(self.model_path() or self.cfg.get("model_file", "")),
            elapsed_ms=elapsed,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    def availability_report(self) -> str:
        cli = self.server_path()
        model = self.model_path()
        return ("Backend: llama_server (persistent)\n"
                f"Server: {cli if cli else 'NOT FOUND'}\n"
                f"Model:  {model if model else 'NOT FOUND'}\n"
                f"Running: {self.is_running()}\n"
                f"Endpoint: http://{self._host}:{self._port}/v1/chat/completions\n"
                f"Available: {cli is not None and model is not None}")


# ---------------------------------------------------------------------------
# Cloud backend (reference only — NOT the target deployment)
# ---------------------------------------------------------------------------

class CloudBackend:
    """Reference backend that shells out to the ``z-ai chat`` CLI.

    This exists ONLY to prove the orchestrator + tool interface works
    with a real model in this sandbox. The target deployment uses
    :class:`ServerRuntime` (or the legacy :class:`LlamaCliBackend`),
    never this. It is opt-in: the backend is selected only when
    ``settings.engine.backend == 'cloud'``.
    """
    name = "cloud"
    model = "cloud-reference"

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        self.settings = settings or config.load_settings()
        self.cfg = self.settings.get("engine", {})

    def is_available(self) -> bool:
        return shutil.which("z-ai") is not None

    def availability_report(self) -> str:
        return ("Backend: cloud (z-ai chat — REFERENCE ONLY, not the target)\n"
                f"Available: {self.is_available()}")

    def chat(self, messages: List[Message],
             max_tokens: Optional[int] = None,
             temperature: Optional[float] = None) -> Optional[BackendResponse]:
        if not self.is_available():
            return None
        # Flatten the message list into a prompt for the CLI.
        system = next((m.content for m in messages if m.role == "system"), "")
        convo = []
        for m in messages:
            if m.role == "system":
                continue
            convo.append(f"[{m.role}]\n{m.content}")
        prompt = "\n\n".join(convo)
        t0 = time.time()
        try:
            proc = subprocess.run(
                ["z-ai", "chat", "-p", prompt] + (["-s", system] if system else []),
                capture_output=True, text=True, timeout=int(self.cfg.get("timeout_seconds", 120)),
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None
        if proc.returncode != 0:
            return BackendResponse(
                text="", backend="cloud", model=self.model,
                elapsed_ms=int((time.time() - t0) * 1000),
                note=f"z-ai chat failed: {proc.stderr[:200]}",
            )
        # The CLI prints JSON; pull the assistant content out of it.
        text = _extract_content(proc.stdout)
        return BackendResponse(
            text=text, backend="cloud", model=self.model,
            elapsed_ms=int((time.time() - t0) * 1000),
            completion_tokens=len(text.split()) if text else 0,
        )

    def shutdown(self) -> None:
        pass


def _extract_content(stdout: str) -> str:
    """Pull the assistant message content out of the z-ai CLI JSON output."""
    # The CLI prints a JSON object; find the first { and parse.
    try:
        start = stdout.index("{")
        obj = json.loads(stdout[start:])
    except (ValueError, json.JSONDecodeError):
        return stdout.strip()
    choices = obj.get("choices") or []
    if choices:
        msg = choices[0].get("message", {})
        c = msg.get("content")
        if c:
            return c
    # Some SDK versions nest content differently.
    return json.dumps(obj.get("result", obj)) if obj else stdout.strip()


# ---------------------------------------------------------------------------
# Legacy one-shot llama-cli backend (compatibility)
# ---------------------------------------------------------------------------

class LlamaCliBackend:
    """One-shot ``llama-cli`` subprocess backend.

    Kept for compatibility with the existing tests and
    ``prepare_model.py`` flow. Each :meth:`chat` call spawns a fresh
    process and reloads the model — this is the slow path the new
    architecture replaces; use :class:`ServerRuntime` instead.
    """
    name = "llama_cli"
    model = ""

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        self.settings = settings or config.load_settings()
        self.cfg = self.settings.get("engine", {})
        self.model = str(self.cfg.get("model_file", ""))

    def cli_path(self) -> Optional[Path]:
        cfg_path = self.cfg.get("cli_path")
        if cfg_path:
            p = Path(cfg_path)
            if p.exists():
                return p
        bundled = config.app_dir("Engine")
        for name in ("llama-cli.exe", "llama-cli", "main.exe", "main"):
            cand = bundled / name
            if cand.exists():
                return cand
        for name in ("llama-cli", "llama-cli.exe", "main", "main.exe"):
            on_path = shutil.which(name)
            if on_path:
                return Path(on_path)
        return None

    def model_path(self) -> Optional[Path]:
        name = self.cfg.get("model_file")
        if not name:
            models_dir = config.app_dir(self.cfg.get("model_dir", "Models"))
            ggufs = sorted(models_dir.glob("*.gguf"))
            return ggufs[0] if ggufs else None
        p = Path(name)
        if not p.is_absolute():
            p = config.app_dir(self.cfg.get("model_dir", "Models")) / name
        return p if p.exists() else None

    def is_available(self) -> bool:
        return self.model_path() is not None and self.cli_path() is not None

    def availability_report(self) -> str:
        return ("Backend: llama_cli (legacy one-shot — reloads model every request)\n"
                f"Model: {self.model_path() or 'NOT FOUND'}\n"
                f"CLI:   {self.cli_path() or 'NOT FOUND'}\n"
                f"Available: {self.is_available()}")

    def chat(self, messages: List[Message],
             max_tokens: Optional[int] = None,
             temperature: Optional[float] = None) -> Optional[BackendResponse]:
        cli = self.cli_path()
        model = self.model_path()
        if not cli or not model:
            return None
        system = next((m.content for m in messages if m.role == "system"), "")
        convo = "\n\n".join(f"[{m.role}]\n{m.content}" for m in messages if m.role != "system")
        full = f"{system}\n\n{convo}".strip() if system else convo
        mt = max_tokens or int(self.cfg.get("max_tokens", 512))
        n_ctx = int(self.cfg.get("n_ctx", 2048))
        n_threads = int(self.cfg.get("n_threads", 4))
        ngpu = int(self.cfg.get("n_gpu_layers", 0))
        temp = temperature if temperature is not None else float(self.cfg.get("temperature", 0.2))
        cmd = [str(cli), "-m", str(model), "-p", full, "-n", str(mt),
               "-c", str(n_ctx), "-t", str(n_threads), "--temp", str(temp),
               "-ngl", str(ngpu), "--no-display-prompt", "-e"]
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=int(self.cfg.get("timeout_seconds", 120)),
                                  check=False)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None
        text = proc.stdout.strip()
        return BackendResponse(
            text=text, backend="llama_cli",
            model=str(model),
            elapsed_ms=int((time.time() - t0) * 1000),
            completion_tokens=len(text.split()) if text else 0,
            note="" if text else "empty output",
        )

    def shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Disabled backend (rule-engine-only)
# ---------------------------------------------------------------------------

class DisabledBackend:
    name = "disabled"
    model = ""

    def is_available(self) -> bool:
        return False

    def availability_report(self) -> str:
        return ("Backend: disabled\n"
                "The deterministic rule engine does all reasoning. "
                "No LLM is loaded.")

    def chat(self, messages: List[Message],
             max_tokens: Optional[int] = None,
             temperature: Optional[float] = None) -> Optional[BackendResponse]:
        return None

    def shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Factory + singleton
# ---------------------------------------------------------------------------

_runtime: Optional[ServerRuntime] = None
_backend: Optional[Backend] = None


def get_backend(settings: Optional[Dict[str, Any]] = None) -> Backend:
    """Return the configured backend, constructing it on first use.

    Backends, in priority order if ``backend == 'auto'``:
    1. ``llama_server`` — if a model AND server binary are present.
    2. ``llama_cli`` — if a model AND CLI binary are present (legacy).
    3. ``stub`` — always available (deterministic reference / fallback).
    """
    global _backend, _runtime
    if _backend is not None:
        return _backend
    s = settings or config.load_settings()
    cfg = s.get("engine", {})
    choice = cfg.get("backend", "auto")
    if choice == "disabled":
        _backend = DisabledBackend()
    elif choice == "stub":
        _backend = StubBackend(s)
    elif choice == "cloud":
        _backend = CloudBackend(s)
    elif choice == "llama_server":
        _runtime = ServerRuntime(s)
        _backend = _runtime
    elif choice == "llama_cli":
        _backend = LlamaCliBackend(s)
    else:  # auto
        rt = ServerRuntime(s)
        if rt.server_path() and rt.model_path():
            _runtime = rt
            _backend = rt
        else:
            cli = LlamaCliBackend(s)
            if cli.is_available():
                _backend = cli
            else:
                _backend = StubBackend(s)
    return _backend


def set_backend(b: Backend) -> None:
    """Inject a backend (used by tests and the benchmark harness)."""
    global _backend, _runtime
    _backend = b
    _runtime = b if isinstance(b, ServerRuntime) else None


def shutdown() -> None:
    """Stop the persistent runtime, if any. Call on app exit."""
    global _backend, _runtime
    if _backend is not None:
        try:
            _backend.shutdown()
        except Exception:
            pass
    _backend = None
    _runtime = None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _json_tool_call(tool: str, args: Dict[str, Any], idx: int) -> str:
    return ("```tool_call\n" +
            json.dumps({"tool": tool, "args": args, "id": f"tc{idx}"}, ensure_ascii=False) +
            "\n```")


def _extract_int(key: str, text: str) -> Optional[int]:
    import re
    m = re.search(rf"{key}\s*[:=]\s*(\d+)", text)
    return int(m.group(1)) if m else None
