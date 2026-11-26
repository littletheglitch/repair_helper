"""Phase 6, Task 4 — diagnostic quality metrics.

Transparent, per-case metrics for real-world evaluation of the
assistant's diagnostic reasoning. This module does NOT reduce quality to
one misleading accuracy percentage. It shows denominators and separates:

  * cases attempted
  * cases with sufficient evidence
  * cases unresolved
  * technician-confirmed cases
  * cases independently reviewed

Per-case metrics (each boolean or count, with a short justification):

  * hypothesis_relevance      — does the top hypothesis cite real evidence
                                 (a measurement or tool result)?
  * unsupported_claims        — count of unsourced technical claims (from
                                 the orchestrator audit if provided).
  * missing_info_identified   — did the assistant name missing information?
  * next_test_quality          — is the recommended next test decisive for
                                 >= 1 active hypothesis?
  * next_test_distinguishes    — does it distinguish competing hypotheses
                                 (>= 2)?
  * hypothesis_updated         — when new evidence was added between two
                                 prediction rounds, did the top hypothesis
                                 or its score change?
  * premature_confirmation     — did the assistant claim a confirmed repair
                                 before the technician confirmed it?
  * agreement_with_confirmed   — for C cases: did the top hypothesis
                                 overlap the confirmed root cause?

Each metric is computed transparently (word-overlap / presence checks),
never as a fabricated probability. Aggregations report the denominator
explicitly (e.g. "3/5 confirmed cases agreed") and never claim
statistical significance from a small dataset.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from . import validation_framework as VF
from . import database, sessions, measurements as M


# ---------------------------------------------------------------------------
# Per-case metrics
# ---------------------------------------------------------------------------

def _has_evidence_citation(hypothesis: Dict[str, Any]) -> bool:
    """A hypothesis is 'evidence-relevant' if it cites a supporting
    measurement/tool result (not just a bare assertion)."""
    sup = hypothesis.get("supporting") or []
    if isinstance(sup, str):
        sup = [sup]
    return any(s for s in sup if s)


def metric_hypothesis_relevance(predictions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Does the TOP hypothesis cite real evidence?"""
    if not predictions:
        return {"value": None, "reason": "no prediction recorded"}
    top = max(predictions, key=lambda p: p.get("score", 0.0))
    rel = _has_evidence_citation(top)
    return {"value": rel,
            "top_hypothesis": top.get("hypothesis", ""),
            "evidence_cited": top.get("supporting", [])}


def metric_unsupported_claims(audit: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Count of unsourced technical claims from the orchestrator audit."""
    if not audit:
        return {"value": None, "reason": "no audit provided"}
    claims = audit.get("unsourced_claims") or []
    return {"value": len(claims), "claims": claims[:5]}


def metric_missing_info_identified(predictions: List[Dict[str, Any]],
                                   audit: Optional[Dict[str, Any]] = None
                                   ) -> Dict[str, Any]:
    """Did the assistant name missing information? Either a hypothesis
    lists a 'missing' field, or the audit lists gaps."""
    named = False
    sources = []
    for p in predictions:
        miss = p.get("missing") or []
        if isinstance(miss, str):
            miss = [miss]
        if miss:
            named = True
            sources.append("hypothesis:" + (p.get("hypothesis", "")[:30]))
    if audit and audit.get("gaps_stated"):
        named = True
        sources.append("audit:gaps_stated")
    return {"value": named, "sources": sources}


def metric_next_test_quality(next_tests: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Is the top recommended next test decisive for >= 1 hypothesis and
    does it distinguish >= 2 (competing) hypotheses?"""
    if not next_tests:
        return {"value": None, "reason": "no next test recorded"}
    t = next_tests[0]
    distinguishes = t.get("distinguishes") or []
    if isinstance(distinguishes, str):
        distinguishes = [distinguishes]
    return {
        "value": len(distinguishes) >= 1,
        "test": t.get("test", ""),
        "distinguishes_count": len(distinguishes),
        "distinguishes_competing": len(distinguishes) >= 2,
    }


def metric_hypothesis_updated(prediction_round_1: List[Dict[str, Any]],
                              prediction_round_2: List[Dict[str, Any]]
                              ) -> Dict[str, Any]:
    """When new evidence was added between two rounds, did the top
    hypothesis or its score change? (Requires two recorded rounds;
    returns None if only one round is available.)"""
    if not prediction_round_1 or not prediction_round_2:
        return {"value": None,
                "reason": "two prediction rounds required to assess update"}
    top1 = max(prediction_round_1, key=lambda p: p.get("score", 0.0))
    top2 = max(prediction_round_2, key=lambda p: p.get("score", 0.0))
    changed = (top1.get("hypothesis", "") != top2.get("hypothesis", "")
               or abs(top1.get("score", 0.0) - top2.get("score", 0.0)) > 1e-9)
    return {"value": changed,
            "round1_top": top1.get("hypothesis", ""),
            "round2_top": top2.get("hypothesis", "")}


def metric_premature_confirmation(answer_text: str,
                                  case: Optional[VF.ValidationCase] = None
                                  ) -> Dict[str, Any]:
    """Did the assistant claim a confirmed repair BEFORE the technician
    confirmed it? For a case that is NOT technician_confirmed, any
    'confirmed repair' / 'fault confirmed' phrasing is premature."""
    low = (answer_text or "").lower()
    markers = ("repair confirmed", "fault confirmed",
               "root cause confirmed", "confirmed the repair")
    flagged = [m for m in markers if m in low]
    if case is not None and case.case_type == "technician_confirmed":
        # The technician DID confirm; not premature.
        return {"value": False, "reason": "case is technician_confirmed"}
    return {"value": bool(flagged), "markers": flagged}


def metric_agreement_with_confirmed(case: VF.ValidationCase) -> Dict[str, Any]:
    """For C cases: does the top hypothesis overlap the confirmed root
    cause? Returns None for non-confirmed cases."""
    if case.case_type != "technician_confirmed" or not case.confirmed_root_cause:
        return {"value": None, "reason": "not a confirmed case"}
    comp = VF.compare_with_confirmed(case.id)
    if not comp.get("comparison"):
        return {"value": None, "reason": "no comparison available"}
    c = comp["comparison"]
    return {"value": c["overlap_ratio"] > 0,
            "overlap_ratio": c["overlap_ratio"],
            "technician_should_review": True}


# ---------------------------------------------------------------------------
# Per-case report
# ---------------------------------------------------------------------------

def evaluate_case(case_id: int,
                  audit: Optional[Dict[str, Any]] = None,
                  answer_text: str = "",
                  prediction_round_2: Optional[List[Dict[str, Any]]] = None
                  ) -> Dict[str, Any]:
    """Compute the full per-case metric report.

    Args:
        case_id: the validation case id.
        audit: the orchestrator's audit dict (unsourced_claims, gaps_stated).
            Optional; if absent, the unsupported-claims and missing-info
            metrics rely only on the recorded prediction.
        answer_text: the assistant's final answer text (for premature-
            confirmation detection).
        prediction_round_2: an optional second prediction round (for the
            hypothesis-updated metric). If absent, that metric is None.
    """
    case = VF.get_case(case_id)
    if not case:
        return {"ok": False, "error": f"no validation case {case_id}"}
    preds = VF.get_predictions(case_id)
    ntests = VF.get_next_tests(case_id)
    report = {
        "case_id": case_id,
        "case_key": case.case_key,
        "case_type": case.case_type,
        "metrics": {
            "hypothesis_relevance": metric_hypothesis_relevance(preds),
            "unsupported_claims": metric_unsupported_claims(audit),
            "missing_info_identified":
                metric_missing_info_identified(preds, audit),
            "next_test_quality": metric_next_test_quality(ntests),
            "hypothesis_updated": metric_hypothesis_updated(
                preds, prediction_round_2 or []),
            "premature_confirmation":
                metric_premature_confirmation(answer_text, case),
            "agreement_with_confirmed": metric_agreement_with_confirmed(case),
        },
    }
    return {"ok": True, "report": report}


# ---------------------------------------------------------------------------
# Aggregation with explicit denominators (no single accuracy %)
# ---------------------------------------------------------------------------

def aggregate(case_ids: List[int],
              audits: Optional[Dict[int, Dict[str, Any]]] = None,
              answers: Optional[Dict[int, str]] = None
              ) -> Dict[str, Any]:
    """Aggregate metrics across cases with EXPLICIT denominators.

    Returns counts for each metric (e.g. "hypothesis_relevant: 3/5")
    AND the case-type breakdown so a small dataset is never inflated.

    Never claims statistical significance; the report includes the
    total N and a note that small N is not significant.
    """
    audits = audits or {}
    answers = answers or {}
    by_type = {"synthetic": 0, "real_unresolved": 0,
               "technician_confirmed": 0}
    attempted = 0
    sufficient_evidence = 0
    unresolved = 0
    confirmed = 0
    independently_reviewed = 0
    # metric tallies
    hyp_rel = 0
    hyp_rel_den = 0
    missing_info = 0
    missing_info_den = 0
    next_test_ok = 0
    next_test_den = 0
    next_test_distinguishes = 0
    next_test_dist_den = 0
    premature = 0
    premature_den = 0
    agreed = 0
    agreed_den = 0
    per_case = []
    for cid in case_ids:
        r = evaluate_case(cid, audits.get(cid), answers.get(cid, ""))
        if not r.get("ok"):
            continue
        attempted += 1
        rep = r["report"]
        case = VF.get_case(cid)
        by_type[case.case_type] = by_type.get(case.case_type, 0) + 1
        if case.case_type == "technician_confirmed":
            confirmed += 1
        elif case.case_type == "real_unresolved":
            unresolved += 1
        m = rep["metrics"]
        # sufficient evidence = the case has >= 1 measurement.
        ms = M.list_for(case.session_id) if case.session_id else []
        if ms:
            sufficient_evidence += 1
        # hypothesis relevance
        if m["hypothesis_relevance"]["value"] is not None:
            hyp_rel_den += 1
            if m["hypothesis_relevance"]["value"]:
                hyp_rel += 1
        # missing info
        if m["missing_info_identified"]["value"] is not None:
            missing_info_den += 1
            if m["missing_info_identified"]["value"]:
                missing_info += 1
        # next test
        nt = m["next_test_quality"]
        if nt["value"] is not None:
            next_test_den += 1
            if nt["value"]:
                next_test_ok += 1
            if nt.get("distinguishes_competing"):
                next_test_distinguishes += 1
            next_test_dist_den += 1
        # premature
        pm = m["premature_confirmation"]
        if pm["value"] is not None:
            premature_den += 1
            if pm["value"]:
                premature += 1
        # agreement (only confirmed)
        ag = m["agreement_with_confirmed"]
        if ag["value"] is not None:
            agreed_den += 1
            if ag["value"]:
                agreed += 1
        per_case.append(rep)
    return {
        "totals": {
            "cases_attempted": attempted,
            "cases_with_sufficient_evidence": sufficient_evidence,
            "cases_unresolved": unresolved,
            "technician_confirmed_cases": confirmed,
            "independently_reviewed": independently_reviewed,
            "by_type": by_type,
        },
        "metrics": {
            "hypothesis_relevant": f"{hyp_rel}/{hyp_rel_den}",
            "missing_info_identified": f"{missing_info}/{missing_info_den}",
            "next_test_decisive": f"{next_test_ok}/{next_test_den}",
            "next_test_distinguishes_competing":
                f"{next_test_distinguishes}/{next_test_dist_den}",
            "premature_confirmation": f"{premature}/{premature_den}",
            "agreement_with_confirmed": f"{agreed}/{agreed_den}",
        },
        "per_case": per_case,
        "note": ("These are transparent counts with explicit denominators; "
                 f"with N={attempted} no statistical significance is "
                 "claimed. Review the per-case reports."),
    }
