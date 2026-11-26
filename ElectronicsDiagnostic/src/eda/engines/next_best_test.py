"""Next-best-test engine.

Selects the next measurement that most reduces uncertainty across the
competing hypotheses. This is the Phase 2 replacement for the rule
engine's fixed-priority next-test ladder.

Information-gain heuristic (transparent, no fabricated probabilities):

Each hypothesis has a set of "decisive tests" — measurements whose
result would move the hypothesis to 'contradicted' or to 'confirmed'.
The engine scores each candidate test by how many competing
hypotheses it would distinguish (i.e. how much the hypothesis-status
distribution would change). The single highest-scoring test is
recommended.

For each recommended test the engine returns:
- exact measurement point
- reference ground
- required board state
- adapter/battery conditions
- measurement instrument
- expected result ONLY when supported by evidence (datasheet / verified
  range / topology)
- what different possible results would imply (outcome branches)
- safety precautions (delegated to eda.safety)

The engine never suggests a measurement that cannot safely or
meaningfully distinguish the current hypotheses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import Engine, MethodSpec
from ..schemas import EngineError, EngineResult, ErrorCode
from .. import database, sessions, measurements as M
from .. import reasoning, safety


@dataclass
class TestOutcome:
    condition: str   # e.g. "if resistance < 20 Ω"
    implication: str # e.g. "-> shorted rail; supports 'short' hypothesis"

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class TestRecommendation:
    test: str
    where: str
    reference: str
    board_state: str = ""
    adapter_battery: str = ""
    instrument: str = ""
    expected: str = ""
    expected_supported_by: str = ""
    outcomes: List[TestOutcome] = field(default_factory=list)
    rationale: str = ""
    safety_precautions: str = ""
    distinguishes: List[str] = field(default_factory=list)
    information_gain: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.__dict__,
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


# A small catalog of candidate tests, keyed by a short id. Each entry
# declares which hypothesis-key the test is decisive for, the outcome
# branches, and the safety classification.
_CANDIDATES = [
    # (test_id, display, where, reference, board_state, adapter/battery,
    #  instrument, expected, expected_supported_by, outcomes, decisive_for)
    ("vin", "Measure DC adapter voltage at board input (after jack)",
     "Positive pad of the DC jack on the motherboard side, or the input fuse / first inductor",
     "DC jack negative pin (GND)", "live", "adapter connected",
     "DMM, DC volts", "~19 V (check adapter label)", "adapter label",
     [("if VIN ~19 V", "adapter present; input path downstream is suspect"),
      ("if VIN < 1 V", "adapter or jack is the problem, not the downstream MOSFET")],
     ["input_path_open", "always_on_failure"]),
    ("3valw", "Measure +3VALW (always-on 3.3V rail)",
     "The +3VALW inductor output (or the rail test point near the EC)",
     "A known GND pad (e.g. USB shell or ground plane test pad)",
     "live", "adapter connected", "DMM, DC volts",
     "~3.3 V whenever adapter present", "verified generic always-on rail",
     [("if +3VALW ~3.3 V", "standby converter OK; look downstream"),
      ("if +3VALW = 0 V", "standby converter dead or its enable missing")],
     ["always_on_failure", "ec_held_off"]),
    ("5valw", "Measure +5VALW (always-on 5V rail)",
     "The +5VALW inductor output",
     "A known GND pad", "live", "adapter connected", "DMM, DC volts",
     "~5.0 V whenever adapter present", "verified generic always-on rail",
     [("if +5VALW ~5 V", "5VALW converter OK"),
      ("if +5VALW = 0 V", "5VALW converter dead or enable missing")],
     ["always_on_failure"]),
    ("res_5valw", "Measure resistance to ground on +5VALW (power off)",
     "The +5VALW inductor output",
     "A known GND pad", "power off, caps discharged", "adapter disconnected",
     "DMM, ohms", "hundreds of Ω to kΩ; <20 Ω suggests a short",
     "verified generic rail health rule",
     [("if resistance < 20 Ω", "short on +5VALW -> supports 'short' hypothesis"),
      ("if resistance >= 100 Ω", "rail not shorted -> suspect enable or converter")],
     ["short_on_rail", "always_on_failure"]),
    ("acok", "Measure ACOK / ACIN (adapter-valid signal at the EC)",
     "The ACOK pin of the charger IC and the corresponding EC input pin",
     "EC ground", "live", "adapter connected", "DMM, DC volts (or scope)",
     "logic high (~3.0-3.3 V) when valid adapter; threshold is IC-specific",
     "charger-IC datasheet (request when absent)",
     [("if ACOK high", "adapter-valid path OK; EC should enable downstream"),
      ("if ACOK low", "EC will refuse to enable run rails")],
     ["ec_held_off"]),
    ("vcore", "Measure CPU core VRM output (VCORE)",
     "The VRM inductor outputs near the CPU",
     "A known GND pad", "live", "adapter connected", "DMM, DC volts (or scope)",
     "0.6-1.2 V part-specific; requires VRM controller datasheet",
     "VRM controller datasheet (request when absent)",
     [("if VCORE present", "VRM up; check downstream run rails and reset"),
      ("if VCORE = 0 V", "VRM not enabled; check EN, PGOOD and sequencing")],
     ["bios_firmware", "vrm_failure"]),
]


class NextBestTestEngine(Engine):
    name = "next_best_test"
    version = "1.0.0"
    summary = ("Selects the next measurement that most reduces uncertainty "
              "across competing hypotheses (information-gain heuristic). "
              "Expected values are given only when supported by evidence. "
              "Each test lists what different results would imply.")
    provenance = ("Original Phase 2 implementation. Heuristic is transparent "
                  "and deterministic; no fabricated probabilities.")

    methods = {
        "recommend": MethodSpec(
            "recommend", "Recommend the next most informative test for a session",
            {"session_id": ("number", True)}, "TestRecommendation"),
        "list_candidates": MethodSpec(
            "list_candidates", "List all candidate tests with their decisive hypotheses",
            {}, "list"),
    }

    # ---- recommend --------------------------------------------------
    def _recommend(self, inputs: Dict[str, Any]) -> EngineResult:
        sid = inputs.get("session_id")
        if sid is None:
            return self._err("recommend", ErrorCode.MISSING_INPUT,
                             "missing session_id", "session_id")
        sess = sessions.get(int(sid))
        if not sess:
            return self._err("recommend", ErrorCode.NOT_APPLICABLE,
                             f"no session {sid}")
        syms = sessions.symptoms(int(sid))
        ms = M.list_for(int(sid))
        if not syms and not ms:
            return self._err("recommend", ErrorCode.NOT_APPLICABLE,
                             "no symptoms or measurements yet")
        # Run the hypothesis manager to get the current status set.
        from . import hypotheses as H
        hyp_result = E_call("hypotheses", "analyze", {"session_id": int(sid)})
        hyps = hyp_result.value.get("hypotheses", []) if hyp_result.ok else []
        if not hyps:
            return self._err("recommend", ErrorCode.NOT_APPLICABLE,
                             "no hypotheses to distinguish")
        # Which hypotheses are NOT yet confirmed/contradicted/rejected?
        active = [h for h in hyps if h["status"] in (
            H.STATUS_PLAUSIBLE, H.STATUS_STRONGLY_SUPPORTED, H.STATUS_UNCERTAIN,
        )]
        if not active:
            return self._err("recommend", ErrorCode.NOT_APPLICABLE,
                             "all hypotheses are confirmed/contradicted/rejected; "
                             "no informative test remains")
        active_keys = list(h["hypothesis_key"] for h in active)
        # Which measurements are already taken?
        taken = {_norm_name(m.name) for m in ms}
        # Score each candidate by how many active hypotheses it distinguishes.
        scored = []
        for cand in _CANDIDATES:
            test_id = cand[0]
            decisive_for = cand[10]
            # Skip tests already taken (match by normalized test id vs names).
            if _norm_name(test_id) in taken:
                continue
            # A candidate "distinguishes" an active hypothesis if the
            # hypothesis description contains the candidate's decisive-for
            # tag as a word-overlap (the hypothesis engine generates long
            # slugs, so exact-key matching would miss). We require ALL of
            # the tag's significant words to appear in the hypothesis key.
            distinguishes = []
            for h in active:
                ak_low = h["hypothesis_key"].lower().replace("_", " ")
                ak_words = {w for w in ak_low.split() if len(w) > 2}
                for tag in decisive_for:
                    tag_words = {w for w in tag.lower().split("_") if len(w) > 2}
                    if not tag_words:
                        continue
                    # Allow one missing word (e.g. 'rail' vs 'power rail').
                    missing = tag_words - ak_words
                    if len(missing) <= 1:
                        distinguishes.append(h["hypothesis_key"])
                        break
            if not distinguishes:
                continue
            gain = len(distinguishes) * (1 + 0.1 * len(cand[9]))
            scored.append((gain, cand, distinguishes))
        if not scored:
            return self._err("recommend", ErrorCode.NOT_APPLICABLE,
                             "no candidate test distinguishes the active hypotheses")
        scored.sort(key=lambda x: -x[0])
        gain, cand, distinguishes = scored[0]
        rec = TestRecommendation(
            test=cand[1], where=cand[2], reference=cand[3],
            board_state=cand[4], adapter_battery=cand[5],
            instrument=cand[6], expected=cand[7],
            expected_supported_by=cand[8],
            outcomes=[TestOutcome(o[0], o[1]) for o in cand[9]],
            rationale=(
                f"This test is decisive for {len(distinguishes)} of "
                f"{len(active)} active hypotheses: "
                f"{', '.join(sorted(distinguishes))}."
            ),
            safety_precautions=safety.required_caution_for(cand[4] + " " + cand[1]) or _default_safety(cand[4]),
            distinguishes=sorted(distinguishes),
            information_gain=round(gain, 3),
        )
        # Persist the chosen test to next_tests for the report.
        database.execute("DELETE FROM next_tests WHERE session_id=?", (int(sid),))
        database.execute(
            "INSERT INTO next_tests(session_id, test, where_to_measure, "
            "reference_point, expected, rationale, priority, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (int(sid), rec.test, rec.where, rec.reference, rec.expected,
             rec.rationale, int(gain * 10), database.now_iso()),
        )
        database.add_timeline(int(sid), "next_test", rec.test)
        return self._ok("recommend", value=rec.to_dict(), inputs=inputs,
                        assumptions=["information gain = (#active hypotheses distinguished) * (1 + 0.1 * #outcome branches)",
                                    "expected value is given only when supported by a named source"],
                        limitations=["heuristic is transparent, not a calibrated probability",
                                    "candidate catalog is curated; board-specific tests require topology + datasheet"])

    # ---- list_candidates --------------------------------------------
    def _list_candidates(self, inputs: Dict[str, Any]) -> EngineResult:
        out = []
        for cand in _CANDIDATES:
            out.append({
                "test_id": cand[0], "test": cand[1],
                "decisive_for": cand[10],
                "outcomes": [{"condition": o[0], "implication": o[1]} for o in cand[9]],
            })
        return self._ok("list_candidates", value={"candidates": out}, inputs=inputs)

    # ---- dispatch ---------------------------------------------------
    _DISPATCH = None

    def run(self, method: str, inputs: Dict[str, Any]) -> EngineResult:
        fn = NextBestTestEngine._DISPATCH.get(method)
        if fn is None:
            return self._err(method, ErrorCode.NOT_APPLICABLE, f"unknown method '{method}'")
        return fn(self, inputs)


NextBestTestEngine._DISPATCH = {
    "recommend": NextBestTestEngine._recommend,
    "list_candidates": NextBestTestEngine._list_candidates,
}


def E_call(name, method, inputs):
    """Local import to avoid a circular import at module load."""
    from . import base as _b
    return _b.call(name, method, inputs)


def _norm_name(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _default_safety(board_state: str) -> str:
    if "live" in board_state.lower():
        return (
            "CAUTION: This is a live measurement. Power the board only while "
            "you measure. Use the stated ground reference. Keep one hand away."
        )
    if "power off" in board_state.lower() or "caps discharged" in board_state.lower():
        return (
            "CAUTION: Disconnect power and discharge bulk capacitors before "
            "any resistance measurement."
        )
    return ""
