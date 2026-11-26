"""Evidence-based hypothesis manager.

This engine maintains structured hypotheses with explicit statuses
and is the Phase 2 replacement for the in-session hypothesis store.
The existing :mod:`eda.reasoning` rule engine is preserved and used as
one evidence source; this manager adds the structured status model and
a stronger confirmation gate.

Explicit statuses (from the spec):
    confirmed          — multiple independent supporting measurements,
                         zero contradicting, no missing critical evidence
    strongly_supported — ≥2 supporting, zero contradicting
    plausible          — ≥1 supporting, not contradicted
    uncertain          — no supporting evidence, not contradicted
    contradicted       — ≥1 contradicting piece of evidence
    rejected           — explicitly rejected (e.g. by a confirmed other)

A hypothesis must NEVER become 'confirmed' merely because it is the
highest-ranked one. Confirmation requires independent corroboration.

Every hypothesis carries:
    description           — the fault hypothesis
    confidence_basis      — qualitative basis (NOT a fabricated probability)
    supporting            — list of evidence strings (with source)
    contradicting         — list of evidence strings (with source)
    required_evidence      — what would be needed to confirm
    suggested_test        — next test that would move the status
    status                — one of the six above
    rank                  — display rank

The engine persists to the ``hypotheses_v2`` table (added by migration
v1). It also writes a 'confirmed' timeline entry when a hypothesis
becomes confirmed, so the report generator's existing _confirmed()
helper picks it up.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import Engine, MethodSpec
from ..schemas import EngineError, EngineResult, ErrorCode
from .. import database, sessions, measurements as M, reasoning
from .. import retrieval, knowledge_base


STATUS_CONFIRMED = "confirmed"
STATUS_STRONGLY_SUPPORTED = "strongly_supported"
STATUS_PLAUSIBLE = "plausible"
STATUS_UNCERTAIN = "uncertain"
STATUS_CONTRADICTED = "contradicted"
STATUS_REJECTED = "rejected"

# Status ordering for display ranking.
_STATUS_RANK = {
    STATUS_CONFIRMED: 0,
    STATUS_STRONGLY_SUPPORTED: 1,
    STATUS_PLAUSIBLE: 2,
    STATUS_UNCERTAIN: 3,
    STATUS_CONTRADICTED: 4,
    STATUS_REJECTED: 5,
}


@dataclass
class Hypothesis:
    hypothesis_key: str
    description: str
    confidence_basis: str = ""
    supporting: List[str] = field(default_factory=list)
    contradicting: List[str] = field(default_factory=list)
    required_evidence: List[str] = field(default_factory=list)
    suggested_test: str = ""
    status: str = STATUS_UNCERTAIN
    rank: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class HypothesisReport:
    session_id: int
    hypotheses: List[Hypothesis] = field(default_factory=list)
    confirmed: Optional[str] = None
    missing_information: List[str] = field(default_factory=list)
    next_test: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "confirmed": self.confirmed,
            "missing_information": self.missing_information,
            "next_test": self.next_test,
        }


class HypothesisEngine(Engine):
    name = "hypotheses"
    version = "1.0.0"
    summary = ("Structured hypothesis manager with explicit statuses "
              "(confirmed/strongly_supported/plausible/uncertain/"
              "contradicted/rejected) and a confirmation gate that "
              "requires independent corroboration.")
    provenance = ("Original Phase 2 implementation. The Phase 1 rule "
                  "engine (eda.reasoning) is used as one evidence source; "
                  "this manager adds the structured status model.")

    methods = {
        "analyze": MethodSpec(
            "analyze", "Run the structured hypothesis manager for a session",
            {"session_id": ("number", True)}, "HypothesisReport"),
        "list": MethodSpec(
            "list", "List stored hypotheses for a session",
            {"session_id": ("number", True)}, "HypothesisReport"),
        "confirm": MethodSpec(
            "confirm", "Manually confirm a hypothesis (technician-confirmed)",
            {"session_id": ("number", True), "hypothesis_key": ("string", True),
             "basis": ("string", False)}, "ok"),
        "reject": MethodSpec(
            "reject", "Mark a hypothesis as rejected",
            {"session_id": ("number", True), "hypothesis_key": ("string", True),
             "basis": ("string", False)}, "ok"),
    }

    # ---- analyze ----------------------------------------------------
    def _analyze(self, inputs: Dict[str, Any]) -> EngineResult:
        sid = inputs.get("session_id")
        if sid is None:
            return self._err("analyze", ErrorCode.MISSING_INPUT,
                             "missing session_id", "session_id")
        sess = sessions.get(int(sid))
        if not sess:
            return self._err("analyze", ErrorCode.NOT_APPLICABLE,
                             f"no session {sid}")
        syms = sessions.symptoms(int(sid))
        ms = M.list_for(int(sid))
        if not syms and not ms:
            return self._err("analyze", ErrorCode.NOT_APPLICABLE,
                             "no symptoms or measurements yet")
        # Run the Phase 1 rule engine as one evidence source.
        rule_analysis = reasoning.analyze(int(sid), syms, ms)
        report = HypothesisReport(session_id=int(sid))
        # Convert each rule hypothesis into a structured one with a status
        # derived from its supporting/contradicting/missing lists.
        for i, h in enumerate(rule_analysis.hypotheses, 1):
            key = _slug(h.fault)
            status = self._derive_status(h)
            hyp = Hypothesis(
                hypothesis_key=key,
                description=h.fault,
                confidence_basis=self._confidence_basis(h),
                supporting=h.supporting,
                contradicting=h.contradicting,
                required_evidence=h.missing,
                suggested_test=rule_analysis.next_test.test if rule_analysis.next_test else "",
                status=status,
                rank=i,
            )
            report.hypotheses.append(hyp)
        # Status-based re-ranking: confirmed first, then strongly_supported,
        # etc.; within a status, keep the rule engine's score order.
        report.hypotheses.sort(
            key=lambda h: (_STATUS_RANK.get(h.status, 9), h.rank)
        )
        for i, h in enumerate(report.hypotheses, 1):
            h.rank = i
        # Confirmation gate: a hypothesis is confirmed only with ≥3 supporting,
        # zero contradicting, and zero required_evidence (no missing critical
        # evidence). This is intentionally stronger than the rule engine's gate.
        for h in report.hypotheses:
            if (len(h.supporting) >= 3 and not h.contradicting
                    and not h.required_evidence):
                h.status = STATUS_CONFIRMED
                report.confirmed = h.description
                database.add_timeline(int(sid), "confirmed", h.description)
                break
        report.missing_information = rule_analysis.missing_evidence
        if rule_analysis.next_test:
            report.next_test = {
                "test": rule_analysis.next_test.test,
                "where": rule_analysis.next_test.where_to_measure,
                "reference": rule_analysis.next_test.reference_point,
                "expected": rule_analysis.next_test.expected,
                "rationale": rule_analysis.next_test.rationale,
            }
        # Persist to hypotheses_v2.
        self._persist(int(sid), report)
        return self._ok("analyze", value=report.to_dict(), inputs=inputs,
                        assumptions=["Phase 1 rule engine is one evidence source",
                                    "no fabricated probabilities: confidence_basis is qualitative"],
                        limitations=["probabilities are qualitative, not numerical",
                                     "confirmation requires independent corroboration"])

    def _derive_status(self, h) -> str:
        if h.contradicting:
            return STATUS_CONTRADICTED
        if len(h.supporting) >= 2:
            return STATUS_STRONGLY_SUPPORTED
        if len(h.supporting) >= 1:
            return STATUS_PLAUSIBLE
        return STATUS_UNCERTAIN

    def _confidence_basis(self, h) -> str:
        parts = []
        if h.supporting:
            parts.append(f"{len(h.supporting)} supporting")
        if h.contradicting:
            parts.append(f"{len(h.contradicting)} contradicting")
        if h.missing:
            parts.append(f"{len(h.missing)} missing")
        if not parts:
            return "no evidence yet"
        return "; ".join(parts) + " (qualitative; no numerical probability)"

    def _persist(self, session_id: int, report: HypothesisReport) -> None:
        database.execute("DELETE FROM hypotheses_v2 WHERE session_id=?", (session_id,))
        now = database.now_iso()
        for h in report.hypotheses:
            database.execute(
                "INSERT INTO hypotheses_v2("
                "session_id, hypothesis_key, description, status, "
                "confidence_basis, supporting, contradicting, "
                "required_evidence, suggested_test, rank, "
                "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, h.hypothesis_key, h.description, h.status,
                 h.confidence_basis, " | ".join(h.supporting),
                 " | ".join(h.contradicting),
                 " | ".join(h.required_evidence), h.suggested_test, h.rank, now, now),
            )

    # ---- list -------------------------------------------------------
    def _list(self, inputs: Dict[str, Any]) -> EngineResult:
        sid = inputs.get("session_id")
        if sid is None:
            return self._err("list", ErrorCode.MISSING_INPUT, "missing session_id", "session_id")
        rows = database.query_all(
            "SELECT * FROM hypotheses_v2 WHERE session_id=? ORDER BY rank", (int(sid),)
        )
        hyps: List[Hypothesis] = []
        for r in rows:
            h = Hypothesis(
                hypothesis_key=r["hypothesis_key"],
                description=r["description"],
                confidence_basis=r["confidence_basis"] or "",
                supporting=[s for s in (r["supporting"] or "").split(" | ") if s],
                contradicting=[s for s in (r["contradicting"] or "").split(" | ") if s],
                required_evidence=[s for s in (r["required_evidence"] or "").split(" | ") if s],
                suggested_test=r["suggested_test"] or "",
                status=r["status"],
                rank=int(r["rank"]),
            )
            hyps.append(h)
        return self._ok("list", value={"session_id": int(sid), "hypotheses": [h.to_dict() for h in hyps]},
                        inputs=inputs)

    # ---- confirm / reject (manual) ----------------------------------
    def _confirm(self, inputs: Dict[str, Any]) -> EngineResult:
        sid = inputs.get("session_id")
        key = inputs.get("hypothesis_key", "")
        if sid is None or not key:
            return self._err("confirm", ErrorCode.MISSING_INPUT,
                             "session_id and hypothesis_key required", "hypothesis_key")
        basis = inputs.get("basis", "technician-confirmed")
        row = database.query_one(
            "SELECT id FROM hypotheses_v2 WHERE session_id=? AND hypothesis_key=?",
            (int(sid), key),
        )
        if not row:
            return self._err("confirm", ErrorCode.NOT_APPLICABLE,
                             f"no hypothesis '{key}' in session {sid}")
        database.execute(
            "UPDATE hypotheses_v2 SET status=?, updated_at=? WHERE id=?",
            (STATUS_CONFIRMED, database.now_iso(), row["id"]),
        )
        desc = database.query_one(
            "SELECT description FROM hypotheses_v2 WHERE id=?", (row["id"],)
        )["description"]
        database.add_timeline(int(sid), "confirmed", f"{desc} ({basis})")
        return self._ok("confirm", value={"confirmed": key, "basis": basis}, inputs=inputs)

    def _reject(self, inputs: Dict[str, Any]) -> EngineResult:
        sid = inputs.get("session_id")
        key = inputs.get("hypothesis_key", "")
        if sid is None or not key:
            return self._err("reject", ErrorCode.MISSING_INPUT,
                             "session_id and hypothesis_key required", "hypothesis_key")
        basis = inputs.get("basis", "technician-rejected")
        row = database.query_one(
            "SELECT id FROM hypotheses_v2 WHERE session_id=? AND hypothesis_key=?",
            (int(sid), key),
        )
        if not row:
            return self._err("reject", ErrorCode.NOT_APPLICABLE,
                             f"no hypothesis '{key}' in session {sid}")
        database.execute(
            "UPDATE hypotheses_v2 SET status=?, updated_at=? WHERE id=?",
            (STATUS_REJECTED, database.now_iso(), row["id"]),
        )
        return self._ok("reject", value={"rejected": key, "basis": basis}, inputs=inputs)

    # ---- dispatch ---------------------------------------------------
    _DISPATCH = None

    def run(self, method: str, inputs: Dict[str, Any]) -> EngineResult:
        fn = HypothesisEngine._DISPATCH.get(method)
        if fn is None:
            return self._err(method, ErrorCode.NOT_APPLICABLE, f"unknown method '{method}'")
        return fn(self, inputs)


HypothesisEngine._DISPATCH = {
    "analyze": HypothesisEngine._analyze,
    "list": HypothesisEngine._list,
    "confirm": HypothesisEngine._confirm,
    "reject": HypothesisEngine._reject,
}


def _slug(text: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:60]
