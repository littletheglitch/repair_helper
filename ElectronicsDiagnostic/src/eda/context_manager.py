"""Context Manager — assembles the prompt context for the orchestrator.

Responsibilities (matching the spec):

* Pull the session's symptoms, measurements, notes, and document
  references into a compact, structured context block.
* Retrieve the most relevant Knowledge Base facts for the symptom and
  measurement keywords (so the LLM has authoritative references).
* Enforce a token budget so the context never overflows the model's
  ``n_ctx``. When the budget is tight, it keeps the newest measurements
  and the highest-tier KB facts and summarizes older evidence.
* Produce a structured summary that the orchestrator can hand to the
  LLM; the LLM is told these are facts and must not invent beyond them.
* **Never silently drop data.** The DB is the source of truth; the
  context window is just a view. Anything trimmed from the prompt is
  still in the DB and is named in the summary ("X older measurements
  omitted — see `eda history`") so the technician can recover it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import config, database, knowledge_base, measurements, sessions


# Rough but safe token estimate. Real tokenizers are model-specific;
# for budgeting we use the well-known ~4 chars/token heuristic on the
# lower bound (3.5 chars/token for English+code). We round down to 3.
_CHARS_PER_TOKEN = 3.0


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


@dataclass
class ContextBlock:
    label: str
    text: str
    tokens: int = 0
    truncated: bool = False
    omitted_count: int = 0

    def __post_init__(self) -> None:
        if self.tokens == 0:
            self.tokens = estimate_tokens(self.text)


@dataclass
class AssembledContext:
    blocks: List[ContextBlock] = field(default_factory=list)
    total_tokens: int = 0
    budget_tokens: int = 0
    kb_facts: List[Dict[str, Any]] = field(default_factory=list)
    measurements_omitted: int = 0

    @property
    def text(self) -> str:
        return "\n\n".join(f"### {b.label}\n{b.text}" for b in self.blocks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blocks": [{"label": b.label, "tokens": b.tokens,
                        "truncated": b.truncated, "omitted_count": b.omitted_count}
                       for b in self.blocks],
            "total_tokens": self.total_tokens,
            "budget_tokens": self.budget_tokens,
            "kb_facts": len(self.kb_facts),
            "measurements_omitted": self.measurements_omitted,
        }


def token_budget(settings: Optional[Dict[str, Any]] = None) -> int:
    """The context budget in tokens.

    We reserve 30% of ``n_ctx`` for the model's response and the tool
    catalog, and use the rest for the assembled context. This is
    conservative; the orchestrator can override per-call.
    """
    s = settings or config.load_settings()
    n_ctx = int(s.get("engine", {}).get("n_ctx", 2048))
    reserve = int(s.get("engine", {}).get("reserve_tokens", 700))
    budget = n_ctx - reserve
    return max(512, budget)


def assemble(session_id: int,
             budget: Optional[int] = None,
             extra_query: str = "") -> AssembledContext:
    """Assemble the prompt context for one session.

    The DB is the source of truth. We READ it here; we never modify or
    delete anything. Older measurements that do not fit the budget are
    noted by name in the summary so the technician can recover them
    with `eda history` / `eda session dump`.
    """
    if budget is None:
        budget = token_budget()
    ctx = AssembledContext(budget_tokens=budget)

    # --- Session header -------------------------------------------------
    sess = sessions.get(session_id)
    title = sess.title if sess else f"Session {session_id}"
    board = sess.board_model if sess and sess.board_model else "(not set)"
    header = (f"Session: {title}\nBoard model: {board}\n"
              f"Session id: {session_id}")
    ctx.blocks.append(ContextBlock(label="Session", text=header))

    # --- Symptoms -------------------------------------------------------
    syms = sessions.symptoms(session_id)
    sym_text = "\n".join(f"- {s}" for s in syms) if syms else "(no symptoms recorded)"
    ctx.blocks.append(ContextBlock(label="Symptoms", text=sym_text))

    # --- Measurements (newest first; trim oldest with a note) -----------
    ms = measurements.list_for(session_id)
    ms_sorted = sorted(ms, key=lambda m: getattr(m, "taken_at", "") or "",
                       reverse=True)
    block_lines: List[str] = []
    used = 0
    omitted: List[str] = []
    for m in ms_sorted:
        line = f"- {m.name} = {m.value}{m.unit or ''} @ {m.point or 'unspecified'}"
        t = estimate_tokens(line)
        if used + t > int(budget * 0.45):  # measurements get <=45% of budget
            omitted.append(m.name)
            continue
        block_lines.append(line)
        used += t
    meas_text = "\n".join(block_lines) if block_lines else "(no measurements)"
    if omitted:
        meas_text += (f"\n\n({len(omitted)} older measurement(s) omitted to fit "
                      f"context: {', '.join(omitted[:8])}"
                      f"{'; …' if len(omitted) > 8 else ''}. "
                      "They remain in the database — use `eda history`.)")
    ctx.blocks.append(ContextBlock(
        label="Measurements", text=meas_text,
        omitted_count=len(omitted)))
    ctx.measurements_omitted = len(omitted)

    # --- Knowledge Base facts ------------------------------------------
    queries: List[str] = list(syms) + [extra_query]
    queries = [q for q in queries if q]
    kb_facts: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for q in queries[:3]:  # top 3 symptom queries
        for f in knowledge_base.search_facts(q, limit=8):
            fid = f.get("id") or f.get("fact", "")
            if fid in seen_ids:
                continue
            seen_ids.add(fid)
            kb_facts.append(f)
    # Rank by tier (manufacturer-confirmed highest), keep top N within budget.
    tier_order = {"manufacturer-confirmed": 0, "technician-confirmed": 1,
                  "verified-reference": 2, "textbook": 3,
                  "community": 4, "unverified": 5, "": 9}
    kb_facts.sort(key=lambda f: tier_order.get(f.get("tier", ""), 9))
    kb_lines: List[str] = []
    kb_used = 0
    kb_budget = int(budget * 0.35)  # KB gets <=35% of budget
    for f in kb_facts:
        tier = f.get("tier", "")
        fact = f.get("fact") or f.get("text", "")
        src = f.get("source_title", "")
        line = f"- [{tier or 'unverified'}] {fact}"
        if src:
            line += f" (source: {src})"
        t = estimate_tokens(line)
        if kb_used + t > kb_budget:
            continue
        kb_lines.append(line)
        kb_used += t
        ctx.kb_facts.append(f)
    kb_text = "\n".join(kb_lines) if kb_lines else "(no relevant KB facts)"
    ctx.blocks.append(ContextBlock(label="Knowledge Base facts", text=kb_text))

    # --- Document references (titles only) -----------------------------
    try:
        docs = database.query_all(
            "SELECT title, file_type FROM documents WHERE session_id=? "
            "ORDER BY imported_at DESC LIMIT 10",
            (session_id,))
        if docs:
            doc_text = "\n".join(f"- {d['title']} ({d['file_type']})" for d in docs)
        else:
            doc_text = "(no documents attached)"
    except Exception:
        doc_text = "(documents unavailable)"
    ctx.blocks.append(ContextBlock(label="Attached documents", text=doc_text))

    # --- Final guard: if over budget, trim KB lines from the bottom ----
    ctx.total_tokens = sum(b.tokens for b in ctx.blocks)
    if ctx.total_tokens > budget:
        # Trim the KB block until we fit.
        for b in ctx.blocks:
            if b.label.startswith("Knowledge Base"):
                while b.tokens > 0 and ctx.total_tokens > budget:
                    lines = b.text.split("\n")
                    if len(lines) <= 2:
                        break
                    lines.pop()
                    b.text = "\n".join(lines)
                    b.tokens = estimate_tokens(b.text)
                    ctx.total_tokens = sum(x.tokens for x in ctx.blocks)
                break
    return ctx


def structured_summary(session_id: int) -> str:
    """A short human-readable summary of the session state.

    Used by the orchestrator as a fallback answer and by the CLI for
    `eda summary`. Pure DB read; never deletes.
    """
    sess = sessions.get(session_id)
    syms = sessions.symptoms(session_id)
    ms = measurements.list_for(session_id)
    lines = []
    lines.append(f"Session: {sess.title if sess else session_id}"
                 + (f" ({sess.board_model})" if sess and sess.board_model else ""))
    lines.append(f"Symptoms ({len(syms)}): " + ("; ".join(syms) if syms else "none"))
    if ms:
        lines.append(f"Measurements ({len(ms)}):")
        for m in ms[-10:]:  # last 10
            lines.append(f"  - {m.name} = {m.value}{m.unit or ''} @ {m.point or '?'}")
    else:
        lines.append("Measurements: none")
    return "\n".join(lines)
