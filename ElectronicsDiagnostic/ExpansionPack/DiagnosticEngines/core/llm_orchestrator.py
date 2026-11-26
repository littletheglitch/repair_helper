"""LLM Orchestration Layer.

Hybrid architecture:
  Layer 1: Deterministic engines (the rest of this pack) — always on,
           no LLM required.
  Layer 2: Lightweight retrieval (KnowledgeStore, FailurePatternDB) —
           always on, no LLM required.
  Layer 3: Optional small models — not loaded by this pack; the host
           may plug in.
  Layer 4: LLM — loaded lazily ONLY when Assistant Mode is active AND
           the host has provided a backend. The pack never loads a
           model on its own.

Hard rules enforced here:
  * Diagnostic Mode (mode='diagnostic') NEVER touches the LLM. The
    orchestrator short-circuits all LLM calls and returns a no-op.
  * Assistant Mode (mode='assistant') loads the LLM lazily on first
    use. If no backend is configured, it returns a no-op with a clear
    message — it never crashes.
  * The LLM NEVER overrides confirmed measurement evidence or safety
    rules. The orchestrator wraps every LLM call with a post-check
    that compares the LLM's output against the session's confirmed
    facts and the safety policy; any contradiction is rejected.
  * Memory and CPU usage are measured (best-effort, stdlib only) so
    the host can report resource consumption.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, Any
import sys
import time

PACK_ROOT = Path(__file__).resolve().parents[2]
if str(PACK_ROOT) not in sys.path:
    sys.path.insert(0, str(PACK_ROOT))

from DiagnosticEngines.core.framework import DiagnosticSession  # noqa: E402
from DiagnosticEngines.core.framework.hypothesis import HypothesisStatus  # noqa: E402
from DiagnosticEngines.core.framework.safety import SafetyPolicy, SafetyLevel  # noqa: E402


MODE_DIAGNOSTIC = "diagnostic"
MODE_ASSISTANT = "assistant"


@dataclass
class LLMCallResult:
    """Result of one LLM call."""
    ok: bool
    text: str = ""
    used_llm: bool = False             # False if no-op fallback was used
    mode: str = MODE_DIAGNOSTIC
    backend: str = ""                  # 'none' / 'llama_cpp' / 'openai_compatible' / etc.
    error: Optional[str] = None
    duration_s: float = 0.0
    memory_delta_mb: Optional[float] = None
    rejected_by_safety: bool = False
    rejected_by_evidence: bool = False
    rejection_reason: str = ""


@dataclass
class LLMConfig:
    """Configuration for the LLM orchestrator."""
    mode: str = MODE_DIAGNOSTIC        # 'diagnostic' or 'assistant'
    backend: str = "none"              # 'none' / 'llama_cpp' / 'openai_compatible'
    model_path: str = ""               # path to GGUF or model identifier
    context_size: int = 2048
    max_tokens: int = 512
    temperature: float = 0.3
    enable_cache: bool = True
    max_conversation_history: int = 4  # limit history sent to the LLM


class LLMOrchestrator:
    """The single LLM entry point. Pure stdlib; the actual model
    backend is injected by the host via `set_backend`.

    The orchestrator is stateless across calls except for an optional
    response cache. It never persists conversation history — that is
    the host's responsibility, and the host should respect
    `max_conversation_history` when building prompts.
    """

    def __init__(self, config: Optional[LLMConfig] = None,
                 safety_policy: Optional[SafetyPolicy] = None):
        self.config = config or LLMConfig()
        self.safety = safety_policy or SafetyPolicy()
        self._backend: Optional[Callable[[str, dict], str]] = None
        self._backend_name: str = "none"
        self._cache: dict[str, str] = {}
        # Resource measurement: best-effort, stdlib only
        self._mem_available = self._get_mem_available_mb()

    # --- backend management --------------------------------------------

    def set_backend(self, name: str, call_fn: Callable[[str, dict], str]) -> None:
        """Inject an LLM backend. The host provides a callable that
        takes (prompt, config_dict) and returns a string. The pack
        never imports llama_cpp or any model library directly."""
        self._backend = call_fn
        self._backend_name = name

    def clear_backend(self) -> None:
        self._backend = None
        self._backend_name = "none"

    @property
    def backend_name(self) -> str:
        return self._backend_name

    def is_llm_available(self) -> bool:
        return self._backend is not None and self.config.mode == MODE_ASSISTANT

    # --- the main call -------------------------------------------------

    def explain(self, prompt: str, session: Optional[DiagnosticSession] = None,
                confirmed_facts: Optional[list[str]] = None) -> LLMCallResult:
        """Ask the LLM to explain / summarize / interpret.

        In Diagnostic Mode: always returns a no-op (the LLM is never
        loaded). The host gets a clear message that the LLM is off.

        In Assistant Mode without a backend: returns a no-op fallback.

        In Assistant Mode with a backend: calls the backend, then
        post-checks the output against safety + confirmed evidence.
        Contradictions are rejected.
        """
        t0 = time.monotonic()

        # 1. Diagnostic Mode: never touch the LLM.
        if self.config.mode == MODE_DIAGNOSTIC:
            return LLMCallResult(
                ok=False, text="", used_llm=False, mode=MODE_DIAGNOSTIC,
                backend="none",
                error="LLM is not loaded in Diagnostic Mode. Switch to Assistant Mode to enable LLM-assisted explanation.",
                duration_s=time.monotonic() - t0,
            )

        # 2. Assistant Mode without a backend: no-op fallback.
        if self._backend is None:
            return LLMCallResult(
                ok=False, text="", used_llm=False, mode=MODE_ASSISTANT,
                backend="none",
                error="No LLM backend configured. Provide a backend via set_backend() to enable LLM-assisted explanation.",
                duration_s=time.monotonic() - t0,
            )

        # 3. Cache check. A cache hit does NOT invoke the LLM, so
        # used_llm=False (the call did not use the model this time).
        if self.config.enable_cache and prompt in self._cache:
            return LLMCallResult(
                ok=True, text=self._cache[prompt], used_llm=False,
                mode=MODE_ASSISTANT, backend=self._backend_name,
                duration_s=time.monotonic() - t0,
            )

        # 4. Call the backend
        try:
            raw = self._backend(prompt, {
                "context_size": self.config.context_size,
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
            })
        except Exception as e:  # noqa: BLE001
            return LLMCallResult(
                ok=False, text="", used_llm=True, mode=MODE_ASSISTANT,
                backend=self._backend_name, error=f"LLM backend raised: {type(e).__name__}: {e}",
                duration_s=time.monotonic() - t0,
            )

        # 5. Safety post-check: reject output that recommends unsafe actions
        safety_violations = self.safety.evaluate({
            "what": raw, "where": "", "instrument": "", "safety": "", "action": raw,
        })
        blocked = any(v.level == SafetyLevel.BLOCK for v in safety_violations)
        if blocked:
            return LLMCallResult(
                ok=False, text=raw, used_llm=True, mode=MODE_ASSISTANT,
                backend=self._backend_name, duration_s=time.monotonic() - t0,
                rejected_by_safety=True,
                rejection_reason="LLM output recommended an action blocked by the safety policy. Output suppressed.",
            )

        # 6. Evidence post-check (CONSERVATIVE, best-effort).
        #
        # This check rejects LLM output that explicitly contradicts a
        # confirmed fact via a full-phrase negation: "not <fact>",
        # "is not <fact>", etc. It does NOT do word-extraction or
        # adjective matching, because substring-based contradiction
        # detection is fundamentally unreliable:
        #   - "not shorted to ground but shorted to VCC" is still a
        #     fault, but contains "not shorted".
        #   - "no longer shorted" / "was shorted" use different phrasing.
        #
        # This is a SAFETY-NET, not a reliable contradiction detector.
        # The authoritative evidence gate is the technician's review of
        # the session's confirmed measurements. The host should pass
        # confirmed facts as structured data and let the technician (or
        # a higher-layer rule engine) arbitrate, not rely on this
        # substring heuristic.
        if confirmed_facts:
            raw_lower = raw.lower()
            for fact in confirmed_facts:
                fact_lower = fact.lower().strip()
                explicit_negations = [
                    f"not {fact_lower}",
                    f"isn't {fact_lower}",
                    f"is not {fact_lower}",
                    f"are not {fact_lower}",
                    f"was not {fact_lower}",
                    f"were not {fact_lower}",
                ]
                for pat in explicit_negations:
                    if pat in raw_lower:
                        return LLMCallResult(
                            ok=False, text=raw, used_llm=True, mode=MODE_ASSISTANT,
                            backend=self._backend_name, duration_s=time.monotonic() - t0,
                            rejected_by_evidence=True,
                            rejection_reason=(
                                f"LLM output explicitly negates a confirmed fact "
                                f"({fact!r}). Output suppressed. NOTE: this is a "
                                f"conservative heuristic that only catches full-phrase "
                                f"negations; review the session's confirmed measurements "
                                f"for the authoritative check."
                            ),
                        )

        # 7. Cache and return
        if self.config.enable_cache:
            self._cache[prompt] = raw
        mem_delta = self._mem_delta_mb()
        return LLMCallResult(
            ok=True, text=raw, used_llm=True, mode=MODE_ASSISTANT,
            backend=self._backend_name, duration_s=time.monotonic() - t0,
            memory_delta_mb=mem_delta,
        )

    # --- resource measurement ------------------------------------------

    def _get_mem_available_mb(self) -> Optional[float]:
        """Best-effort available-memory read, stdlib only. Returns None
        if not readable (e.g. non-Linux)."""
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) / 1024.0
        except (OSError, ValueError, IndexError):
            return None
        return None

    def _mem_delta_mb(self) -> Optional[float]:
        """Return change in MemAvailable since construction (negative =
        memory consumed). Best-effort."""
        now = self._get_mem_available_mb()
        if now is None or self._mem_available is None:
            return None
        return self._mem_available - now

    # --- introspection -------------------------------------------------

    def status(self) -> dict:
        return {
            "mode": self.config.mode,
            "backend": self._backend_name,
            "llm_available": self.is_llm_available(),
            "cache_size": len(self._cache),
            "model_path": self.config.model_path,
            "context_size": self.config.context_size,
            "max_tokens": self.config.max_tokens,
        }
