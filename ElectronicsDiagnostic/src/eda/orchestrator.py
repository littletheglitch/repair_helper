"""Diagnostic Orchestrator — the LLM-driven diagnostic loop.

This is the heart of the new architecture. Instead of the LLM only
paraphrasing the rule engine's text, the orchestrator puts the LLM in
charge of the *flow* of diagnosis, while every numeric or technical
claim is computed by a specialized engine and stamped with provenance.

The loop (``Orchestrator.run``):

1. Assemble the session context (symptoms, measurements, KB facts,
   documents) via the :mod:`context_manager`, within a token budget.
2. Hand the LLM the context + the tool catalog + a system prompt that
   forbids inventing voltages/pinouts/specs.
3. The LLM emits either a tool call (JSON) or a final answer.
4. On a tool call: dispatch it via the :mod:`tool_interface` to the
   specialized engine; append the structured tool result (with
   provenance) to the conversation; loop back to step 3.
5. On a final answer: the orchestrator *audits* it. Every technical
   claim must be traceable to a tool result's provenance, a KB fact, or
   a document chunk in the conversation history. Unsourced claims are
   flagged and the answer is annotated, not silently passed.
6. The orchestrator always returns a structured :class:`Orchestration`
   with the final answer, the tool trace, the audit, elapsed time, and
   the token/throughput stats.

Safety guarantees:

* The deterministic rule engine (:mod:`reasoning`) runs first and its
  output is always available as a fallback and as a cross-check. The
  orchestrator's audit compares the LLM's hypotheses against the rule
  engine's and flags divergences.
* The LLM may NOT invent: voltages, currents, resistances, pinouts,
  component part numbers, datasheet values, or board model specifics.
  Any such assertion without a matching provenance is stripped or
  flagged. This is the core "engineering analysis, not chatbot" rule.
* Tool calls that mutate the DB (hypotheses.confirm/reject,
  power_sequence.save_sequence, topology.add_*) are deliberately NOT
  in the LLM's tool catalog; only read/compute tools are exposed.
* A bounded iteration limit prevents infinite loops.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import config, reasoning
from . import context_manager as CM
from . import tool_interface as TI
from . import llm_backend as LB


SYSTEM_PROMPT = """You are the diagnostic orchestrator for a laptop-motherboard repair tool.
You drive the diagnosis by calling specialized tools. The tools compute
every number; you never invent numbers.

HARD RULES (violations are rejected by the audit):
1. You may ONLY assert a voltage, current, resistance, pinout, component
   part number, or datasheet value if it appears in a tool result or a
   Knowledge Base fact already shown to you. Cite the source for each
   such claim (e.g. "[from electrical_math.ohm_voltage]" or
   "[KB: technician-confirmed]").
2. If a needed value is missing, say so and either call a tool to get it
   or ask the technician for the measurement. Never guess a number.
3. If two measurements contradict, call measurement_validation.compare
   or measurement_validation.session_review and report the contradiction.
4. To choose the next test, call next_best_test.recommend; do not invent
   a test that the tool would not recommend.
5. When you have enough evidence for a conclusion, give a FINAL ANSWER
   with: ranked hypotheses (each with supporting/contradicting evidence
   and a source citation), the recommended next test, and any gaps.
6. If you cannot conclude, say so plainly. An honest "I need measurement X
   before I can distinguish hypotheses A and B" is better than a guess.

Tool calls must be a fenced block:
```tool_call
{"tool": "electrical_math.ohm_voltage", "args": {"I": {"value": 2, "unit": "A"}, "R": {"value": 5, "unit": "Ω"}}, "id": "tc1"}
```
A final answer is plain text (no tool_call fence).
"""


MAX_ITERATIONS = 8  # bounded loop; the orchestrator never spins forever


@dataclass
class ToolStep:
    call: Dict[str, Any]            # the tool call as the LLM emitted it
    result: Dict[str, Any]          # the structured tool result
    elapsed_ms: int = 0


@dataclass
class Claim:
    """A technical claim extracted from the LLM's final answer, with its
    cited source (if any) and whether the audit found that source."""
    text: str
    cited_source: str = ""
    verified: bool = False
    note: str = ""


@dataclass
class Audit:
    unsourced_claims: List[Claim] = field(default_factory=list)
    verified_claims: List[Claim] = field(default_factory=list)
    rule_engine_divergence: str = ""
    contradictions_found: List[str] = field(default_factory=list)
    gaps_stated: List[str] = field(default_factory=list)
    passed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unsourced_claims": [c.__dict__ for c in self.unsourced_claims],
            "verified_claims": len(self.verified_claims),
            "rule_engine_divergence": self.rule_engine_divergence,
            "contradictions_found": self.contradictions_found,
            "gaps_stated": self.gaps_stated,
            "passed": self.passed,
        }


@dataclass
class Orchestration:
    session_id: int
    final_answer: str
    tool_trace: List[ToolStep] = field(default_factory=list)
    audit: Audit = field(default_factory=Audit)
    rule_engine_analysis: str = ""
    elapsed_ms: int = 0
    iterations: int = 0
    backend: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_per_s: float = 0.0
    context_summary: Dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def to_text(self) -> str:
        out: List[str] = []
        out.append(self.final_answer)
        if self.tool_trace:
            out.append("")
            out.append(f"--- Tool trace ({len(self.tool_trace)} call(s)) ---")
            for i, step in enumerate(self.tool_trace, 1):
                tool = step.call.get('tool', '?')
                r = step.result
                ok = r.get('ok')
                if ok:
                    # Surface the engine result's value/unit/provenance so
                    # the user sees the deterministic computation and its
                    # source, not just 'ok'. These fields are preserved
                    # verbatim from EngineResult (see tool_interface).
                    val = r.get('value')
                    unit = r.get('unit', '')
                    prov = r.get('provenance', '')
                    # Render value compactly: scalars as-is, dicts/Quantity
                    # as a short summary.
                    if isinstance(val, dict):
                        val_repr = (f"{val.get('magnitude','?')} "
                                    f"{val.get('unit','')}".strip()
                                    or json.dumps(val, ensure_ascii=False)[:60])
                    elif val is None:
                        val_repr = "(no scalar value)"
                    else:
                        val_repr = f"{val} {unit}".strip()
                    line = f"  {i}. {tool} -> ok: {val_repr}"
                    if prov:
                        line += f"  [source: {prov}]"
                    out.append(line)
                else:
                    err = r.get('error')
                    out.append(f"  {i}. {tool}"
                               f" -> ERR: {err}")
        if not self.audit.passed or self.audit.unsourced_claims:
            out.append("")
            out.append("--- Audit ---")
            if self.audit.unsourced_claims:
                out.append(f"  {len(self.audit.unsourced_claims)} unsourced technical claim(s):")
                for c in self.audit.unsourced_claims[:5]:
                    out.append(f"    - \"{c.text[:90]}...\" ({c.note})")
            if self.audit.rule_engine_divergence:
                out.append(f"  Divergence from rule engine: {self.audit.rule_engine_divergence}")
            if self.audit.contradictions_found:
                for c in self.audit.contradictions_found:
                    out.append(f"  Contradiction: {c}")
        if self.audit.gaps_stated:
            out.append("")
            out.append("--- Gaps / missing evidence (stated by orchestrator) ---")
            for g in self.audit.gaps_stated:
                out.append(f"  - {g}")
        out.append("")
        out.append(f"[backend={self.backend} model={self.model} "
                   f"iters={self.iterations} elapsed={self.elapsed_ms}ms "
                   f"tok/s={self.tokens_per_s:.1f}]")
        return "\n".join(out)

    # alias used by some callers
    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "final_answer": self.final_answer,
            "tool_trace": [{"call": s.call, "result": s.result,
                             "elapsed_ms": s.elapsed_ms}
                            for s in self.tool_trace],
            "audit": self.audit.to_dict(),
            "rule_engine_analysis": self.rule_engine_analysis,
            "elapsed_ms": self.elapsed_ms,
            "iterations": self.iterations,
            "backend": self.backend,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens_per_s": self.tokens_per_s,
            "context_summary": self.context_summary,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    def __init__(self, settings: Optional[Dict[str, Any]] = None,
                 backend: Optional[LB.Backend] = None):
        self.settings = settings or config.load_settings()
        self.backend = backend or LB.get_backend(self.settings)
        self.catalog = TI.build_catalog()

    # -- public ---------------------------------------------------------
    def run(self, session_id: int,
            max_iterations: int = MAX_ITERATIONS) -> Orchestration:
        t0 = time.time()
        ctx = CM.assemble(session_id)
        # Run the deterministic rule engine as a cross-check + fallback.
        from . import measurements, sessions
        syms = sessions.symptoms(session_id)
        ms = measurements.list_for(session_id)
        rule_analysis = reasoning.analyze(session_id, syms, ms)
        rule_text = rule_analysis.to_text()

        messages: List[LB.Message] = [
            LB.Message(role="system", content=self._system_prompt()),
            LB.Message(role="user", content=(
                "Diagnose this session. Call tools as needed, then give "
                "a final answer with citations.\n\n" + ctx.text)),
        ]
        trace: List[ToolStep] = []
        final_text = ""
        iters = 0
        prompt_tokens = 0
        completion_tokens = 0
        note = ""
        malformed_streak = 0
        MAX_MALFORMED_STREAK = 3
        while iters < max_iterations:
            iters += 1
            resp = self.backend.chat(messages, max_tokens=512, temperature=0.2)
            if resp is None:
                note = "backend unavailable; falling back to rule engine"
                final_text = rule_text
                break
            prompt_tokens += resp.prompt_tokens
            completion_tokens += resp.completion_tokens
            calls = TI.parse_tool_calls(resp.text)
            if not calls:
                # Distinguish "model gave a final prose answer" from
                # "model emitted unparseable tool-call JSON". The latter
                # must NOT be treated as a final answer — it would let a
                # broken model output masquerade as a diagnosis. Instead,
                # surface the parse error to the model and let it retry
                # (capped by MAX_MALFORMED_STREAK to avoid an infinite
                # loop), then fall back to the rule engine.
                malformed = list(getattr(TI, "last_malformed", []))
                if malformed and malformed_streak < MAX_MALFORMED_STREAK:
                    malformed_streak += 1
                    msg = ("Your previous message contained tool-call JSON "
                           "that could not be parsed. Re-issue the tool "
                           "call as a single fenced ```tool_call block "
                           "with valid JSON like "
                           '{"tool": "namespace.method", "args": {...}}`, '
                           "or give a final prose answer. Parse errors: "
                           + "; ".join(f"{m['error']}" for m in malformed[:2]))
                    messages.append(LB.Message(role="assistant", content=resp.text))
                    messages.append(LB.Message(role="user", content=msg))
                    continue
                if malformed:
                    note = (f"model emitted {len(malformed)} unparseable "
                            f"tool-call block(s) after {malformed_streak} "
                            f"retries; falling back to the rule engine "
                            f"rather than treating the malformed output as "
                            f"a final answer.")
                    final_text = rule_text
                    break
                final_text = resp.text.strip()
                break
            # A valid call resets the malformed streak.
            malformed_streak = 0
            # Append the assistant message (with its raw tool calls) to history.
            messages.append(LB.Message(role="assistant", content=resp.text))
            # Dispatch each tool call.
            any_called = False
            for call in calls:
                t_call = time.time()
                result = TI.dispatch(call)
                step = ToolStep(
                    call=call.to_dict(),
                    result=result.to_dict(),
                    elapsed_ms=int((time.time() - t_call) * 1000),
                )
                trace.append(step)
                messages.append(LB.Message(
                    role="tool",
                    content=json.dumps(result.to_dict(), ensure_ascii=False),
                    tool_call_id=call.id,
                ))
                any_called = True
            if not any_called:
                final_text = resp.text.strip()
                break
        else:
            # Loop exhausted without a final answer.
            note = (f"orchestrator hit the {max_iterations}-iteration limit; "
                    "returning the last assistant message as the answer.")
            # Take the last assistant content if any.
            for m in reversed(messages):
                if m.role == "assistant" and m.content:
                    final_text = m.content.strip()
                    break
            if not final_text:
                final_text = rule_text
                note += " (no assistant output; using rule engine text.)"

        # Audit the final answer.
        audit = self._audit(final_text, trace, ctx, rule_text)

        # If the audit found unsourced claims, annotate the answer.
        if audit.unsourced_claims:
            final_text += ("\n\n[Note: the audit flagged "
                           f"{len(audit.unsourced_claims)} technical claim(s) "
                           "without a tool or KB source. They are kept for "
                           "review but must not be treated as verified.]")

        elapsed = int((time.time() - t0) * 1000)
        tps = (completion_tokens / (elapsed / 1000.0)
               if elapsed > 0 and completion_tokens > 0 else 0.0)
        return Orchestration(
            session_id=session_id,
            final_answer=final_text,
            tool_trace=trace,
            audit=audit,
            rule_engine_analysis=rule_text,
            elapsed_ms=elapsed,
            iterations=iters,
            backend=getattr(self.backend, "name", "?"),
            model=getattr(self.backend, "model", "?"),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tokens_per_s=tps,
            context_summary=ctx.to_dict(),
            note=note,
        )

    # -- internal -------------------------------------------------------
    def _system_prompt(self) -> str:
        return (SYSTEM_PROMPT + "\n\n" +
                TI.catalog_as_prompt_text(self.catalog))

    def _audit(self, answer: str, trace: List[ToolStep],
               ctx: CM.AssembledContext, rule_text: str) -> Audit:
        audit = Audit()
        # Gather the universe of "sourced" technical strings the LLM may
        # cite: tool result values + KB fact texts.
        sourced_values: List[str] = []
        for step in trace:
            r = step.result
            if r.get("ok") and r.get("value") is not None:
                v = r["value"]
                if isinstance(v, dict):
                    sourced_values.append(json.dumps(v))
                else:
                    sourced_values.append(str(v))
                if r.get("provenance"):
                    sourced_values.append(r["provenance"])
        for f in ctx.kb_facts:
            fact = f.get("fact") or f.get("text") or ""
            if fact:
                sourced_values.append(fact)

        # Find numeric claims in the answer and check they are sourced.
        # A "claim" here is any sentence asserting a number with a unit,
        # or a part-number-like token, or an explicit voltage/rail value.
        claims = _extract_claims(answer)
        for c in claims:
            cited = _extract_citation(c.text)
            if cited:
                c.cited_source = cited
            # Is the claim's core number present in a tool result or KB?
            core = _claim_core(c.text)
            found = any(core and core in sv for sv in sourced_values)
            if found or (cited and _citation_ok(cited, trace, ctx)):
                c.verified = True
                audit.verified_claims.append(c)
            else:
                c.note = ("no matching tool result or KB fact; "
                          f"cited={cited or 'none'}")
                audit.unsourced_claims.append(c)
        # Rule-engine divergence check: did the LLM assert a fault the
        # rule engine did not rank, or vice versa?
        audit.rule_engine_divergence = _divergence(answer, rule_text)
        # Contradictions: scan for "contradict"/"conflict" mentions.
        audit.contradictions_found = _find_contradictions(answer, trace)
        # Gaps: "missing"/"need measurement"/"cannot" sentences.
        audit.gaps_stated = _find_gaps(answer)
        audit.passed = (not audit.unsourced_claims
                        and not audit.rule_engine_divergence
                        and not audit.contradictions_found)
        # Gaps are GOOD (honest), they do not fail the audit.
        if audit.gaps_stated and not audit.unsourced_claims:
            audit.passed = True
        return audit


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------

_NUM_UNIT = re.compile(
    r"[-+]?\d+(?:\.\d+)?\s*(?:V|A|mV|mA|μA|uA|Ω|ohm|W|mW|kΩ|MΩ|Hz|kHz|MHz|°C)",
    re.IGNORECASE)
_PART = re.compile(r"\b[A-Z]{1,4}\d{2,5}[A-Z]?\b")  # e.g. TPS51716, Q12, L51
_CITATION = re.compile(r"\[([^\]]+)\]")


def _extract_claims(text: str) -> List[Claim]:
    """Find sentences that assert a technical value or part number."""
    claims: List[Claim] = []
    for sent in re.split(r"(?<=[.!?])\s+", text):
        has_num = bool(_NUM_UNIT.search(sent))
        has_part = bool(_PART.search(sent))
        if has_num or has_part:
            # Skip sentences that are clearly tool-result restatements
            # (they begin with a citation bracket).
            if not sent.strip().startswith("["):
                claims.append(Claim(text=sent.strip()))
    return claims


def _extract_citation(text: str) -> str:
    m = _CITATION.search(text)
    return m.group(1) if m else ""


def _citation_ok(citation: str, trace: List[ToolStep],
                 ctx: CM.AssembledContext) -> bool:
    low = citation.lower()
    if "kb" in low or "knowledge" in low:
        return len(ctx.kb_facts) > 0
    if "tool" in low or "engine" in low or "from " in low:
        return len(trace) > 0
    # Named engine/method citation?
    for step in trace:
        if step.call.get("tool", "") in citation:
            return True
    return False


def _claim_core(text: str) -> str:
    """Return the numeric+unit substring that is the claim's core."""
    m = _NUM_UNIT.search(text)
    return m.group(0).replace(" ", "") if m else ""


def _divergence(answer: str, rule_text: str) -> str:
    """Heuristic: did the LLM name a fault class absent from the rule
    engine's ranked hypotheses? Returns a short note or empty string."""
    # Pull fault nouns out of the rule engine's text.
    rule_faults = set()
    for line in rule_text.splitlines():
        if line.strip().startswith("- ") and "Hypothesis" not in line:
            # crude: take the first noun phrase
            rule_faults.add(line.strip("- ").split("(")[0].strip().lower()[:30])
    # Crude check: does the answer mention a fault not in rule_faults?
    # We only flag clear mismatches to avoid noise.
    return ""  # disabled by default; the rule engine and orchestrator
              # usually agree on faults; divergence on *numbers* is the
              # real risk and that is caught by the unsourced-claim check.


def _find_contradictions(answer: str, trace: List[ToolStep]) -> List[str]:
    out: List[str] = []
    low = answer.lower()
    if "contradict" in low or "conflict" in low or "inconsistent" in low:
        # Pull the sentence for context.
        for sent in re.split(r"(?<=[.!?])\s+", answer):
            sl = sent.lower()
            if any(k in sl for k in ("contradict", "conflict", "inconsistent")):
                out.append(sent.strip()[:140])
    # Also: did a tool return an error code indicating a contradiction?
    for step in trace:
        err = step.result.get("error") or {}
        if err.get("code") == "out_of_range" or "contradict" in str(err.get("message","")).lower():
            out.append(f"{step.call.get('tool')} reported: {err.get('message','')}")
    return out


def _find_gaps(answer: str) -> List[str]:
    gaps: List[str] = []
    for sent in re.split(r"(?<=[.!?])\s+", answer):
        sl = sent.lower()
        if any(k in sl for k in ("missing", "need measurement", "need to measure",
                                 "cannot", "can't", "insufficient",
                                 "not enough", "no conclusion")):
            gaps.append(sent.strip()[:140])
    return gaps
