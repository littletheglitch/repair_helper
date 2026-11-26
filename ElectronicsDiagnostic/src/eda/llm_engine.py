"""Local LLM engine wrapper.

The engine has three backends:

1. ``llama_cpp``: invoke ``llama-cli`` (or ``main``) as a subprocess with
   the configured GGUF model file. This is the genuinely-local path.
2. ``openai_api``: optional, point at a local OpenAI-compatible server
   (e.g. llama.cpp's server). Still local, no cloud.
3. ``disabled``: no LLM. The deterministic rule engine does all the
   reasoning; the LLM is only used to paraphrase and explain, never to
   invent facts.

The engine is wrapped so the rest of the app does not depend on the
LLM being present. If the model file or runtime is missing, the engine
returns None and the CLI uses the rule engine's text directly. This is
the documented behaviour and the test ``test_missing_model_handling``
asserts it.

No internet is ever used. No cloud API. No subscription.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import config


@dataclass
class LLMResponse:
    text: str
    backend: str
    model: str
    elapsed_ms: int
    note: str = ""


class LLMEngine:
    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or config.load_settings()
        self.cfg = self.settings.get("engine", {})

    # --- Availability checks --------------------------------------------

    def model_path(self) -> Optional[Path]:
        name = self.cfg.get("model_file")
        if not name:
            # Try to find any .gguf in the Models directory.
            models_dir = config.app_dir(self.cfg.get("model_dir", "Models"))
            ggufs = sorted(models_dir.glob("*.gguf"))
            return ggufs[0] if ggufs else None
        p = Path(name)
        if not p.is_absolute():
            p = config.app_dir(self.cfg.get("model_dir", "Models")) / name
        return p if p.exists() else None

    def cli_path(self) -> Optional[Path]:
        """Find the llama.cpp CLI executable."""
        cfg_path = self.cfg.get("cli_path")
        if cfg_path:
            p = Path(cfg_path)
            if p.exists():
                return p
        # Search bundled Engine/ directory, then PATH.
        bundled = config.app_dir("Engine")
        for name in ("llama-cli.exe", "llama-cli", "main.exe", "main",
                     "llama.cpp.exe", "llamafile.exe"):
            cand = bundled / name
            if cand.exists():
                return cand
        for name in ("llama-cli", "llama-cli.exe", "main", "main.exe"):
            on_path = shutil.which(name)
            if on_path:
                return Path(on_path)
        return None

    def is_available(self) -> bool:
        backend = self.cfg.get("backend", "disabled")
        if backend == "disabled":
            return False
        if backend == "openai_api":
            return bool(self.cfg.get("api_base"))
        # llama_cpp
        return self.model_path() is not None and self.cli_path() is not None

    def availability_report(self) -> str:
        m = self.model_path()
        c = self.cli_path()
        lines = []
        lines.append(f"Backend: {self.cfg.get('backend','disabled')}")
        lines.append(f"Model file: {m if m else 'NOT FOUND'}")
        lines.append(f"Runtime (llama-cli): {c if c else 'NOT FOUND'}")
        lines.append(f"Available: {self.is_available()}")
        if not self.is_available():
            lines.append(
                "Falling back to the deterministic rule engine. The rule "
                "engine produces the ranked hypotheses, missing-evidence and "
                "next-test; the LLM only paraphrases when available."
            )
        return "\n".join(lines)

    # --- Inference -------------------------------------------------------

    def generate(self, prompt: str, system: str = "",
                 max_tokens: Optional[int] = None) -> Optional[LLMResponse]:
        if not self.is_available():
            return None
        backend = self.cfg.get("backend", "disabled")
        try:
            if backend == "openai_api":
                return self._call_openai(prompt, system, max_tokens)
            return self._call_llama_cli(prompt, system, max_tokens)
        except Exception as e:
            # Any inference failure degrades to None; caller falls back to
            # the rule engine's text.
            return LLMResponse(
                text="", backend=backend, model="",
                elapsed_ms=0, note=f"inference failed: {e}",
            )

    def _call_llama_cli(self, prompt: str, system: str,
                         max_tokens: Optional[int]) -> Optional[LLMResponse]:
        import time
        cli = self.cli_path()
        model = self.model_path()
        if not cli or not model:
            return None
        mt = max_tokens or int(self.cfg.get("max_tokens", 512))
        n_ctx = int(self.cfg.get("n_ctx", 2048))
        n_threads = int(self.cfg.get("n_threads", 4))
        ngpu = int(self.cfg.get("n_gpu_layers", 0))
        temp = float(self.cfg.get("temperature", 0.2))
        full_prompt = f"{system}\n\n{prompt}".strip() if system else prompt
        cmd = [
            str(cli),
            "-m", str(model),
            "-p", full_prompt,
            "-n", str(mt),
            "-c", str(n_ctx),
            "-t", str(n_threads),
            "--temp", str(temp),
            "-ngl", str(ngpu),
            "--no-display-prompt",
            "-e",
        ]
        timeout = int(self.cfg.get("timeout_seconds", 120))
        t0 = time.time()
        try:
            # DETACHED process; we read stdout into a string.
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None
        elapsed = int((time.time() - t0) * 1000)
        # llama-cli prepends a session banner; the actual generation is after.
        text = proc.stdout.strip()
        if not text:
            return LLMResponse(
                text="", backend="llama_cpp", model=str(model),
                elapsed_ms=elapsed,
                note="empty output; CLI may have failed to load model.",
            )
        return LLMResponse(
            text=text, backend="llama_cpp", model=str(model),
            elapsed_ms=elapsed,
        )

    def _call_openai(self, prompt: str, system: str,
                     max_tokens: Optional[int]) -> Optional[LLMResponse]:
        import json
        import time
        import urllib.request
        base = self.cfg.get("api_base", "http://127.0.0.1:8080/v1")
        model = self.cfg.get("model_file", "local")
        url = base.rstrip("/") + "/chat/completions"
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system or "You are a concise electronics repair assistant."},
                {"role": "user", "content": prompt},
            ],
            "temperature": float(self.cfg.get("temperature", 0.2)),
            "max_tokens": max_tokens or int(self.cfg.get("max_tokens", 512)),
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=int(self.cfg.get("timeout_seconds", 120))) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except Exception:
            return None
        elapsed = int((time.time() - t0) * 1000)
        text = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        return LLMResponse(text=text, backend="openai_api", model=str(model), elapsed_ms=elapsed)


# Module-level singleton for convenience.
_engine: Optional[LLMEngine] = None


def get_engine() -> LLMEngine:
    global _engine
    if _engine is None:
        _engine = LLMEngine()
    return _engine


def paraphrase_analysis(analysis_text: str, symptoms: List[str]) -> Optional[str]:
    """Ask the LLM to paraphrase the rule engine's analysis as a short,
    technician-facing explanation. The LLM is told NOT to invent facts;
    it must only restate the rule engine's output. Returns None if the
    LLM is unavailable."""
    eng = get_engine()
    if not eng.is_available():
        return None
    system = (
        "You are an electronics repair assistant. You will be given a "
        "deterministic analysis that ALREADY contains the ranked "
        "hypotheses, missing evidence, and next test. Restate it in clear, "
        "concise language for a board-level technician. DO NOT add new "
        "measurements, pin numbers, voltage values, or component names "
        "that are not in the provided analysis. If something is uncertain, "
        "say so. Keep it under 200 words."
    )
    prompt = (
        f"Symptoms: {', '.join(symptoms) or 'none given'}\n\n"
        f"Analysis:\n{analysis_text}\n\n"
        "Restate this for the technician."
    )
    r = eng.generate(prompt, system=system, max_tokens=400)
    if r and r.text.strip():
        return r.text.strip()
    return None
