"""Socratic Reasoning Engine.

Asks ONE focused question at a time, waits for the user's reasoning,
evaluates the reasoning (not just the final answer), and either asks
the next focused question or guides the user toward the principle they
missed. The point is to make the technician reason before the answer
is revealed — never to dump a lecture.

The engine is deterministic and template-driven. Each "Socratic path"
is a small ordered list of focused questions tied to a skill and a
target concept. The path is chosen from the user's current mastery
gap (via the adaptive engine) so the questions target the missing
principle, not a random topic.

A Socratic turn is one question + (after the user answers) an
evaluation of the reasoning. The engine never asks multiple questions
at once; the CLI exposes one turn at a time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import db, skill_assessment as SA
from .reasoning_evaluator import ReasoningEvaluator


# ---------------------------------------------------------------------------
# Socratic paths — ordered, focused questions per skill/concept.
# Each question has a "focus" (the principle it probes) and a
# "good_reasoning" hint used by the evaluator to recognize a correct
# reasoning chain (the user's exact words need not match; we look for
# the principle keyword and a reasoning connective).
# ---------------------------------------------------------------------------

PATHS: Dict[str, List[Dict[str, Any]]] = {
    "ohm_kirchhoff": [
        {"focus": "identify_unknown",
         "question": "Before computing anything: in this circuit, what is the "
                     "unknown you are solving for, and what law relates it to "
                     "the values you already have?",
         "good_reasoning": ["unknown", "ohm", "kirchhoff", "kvl", "kcl"],
         "followup_ok": "Good — you named the unknown and named the law. "
                        "Next: which two known quantities does that law need?"},
        {"focus": "state_law",
         "question": "State the form of the law you will use, with units. "
                     "(For example: V = I × R, with V in volts, I in amps, "
                     "R in ohms.)",
         "good_reasoning": ["=", "volt", "amp", "ohm", "v", "a"],
         "followup_ok": "Good — the law and the units are consistent. "
                        "Next: plug in the numbers, keeping the units."},
        {"focus": "compute",
         "question": "Now compute the value. Show the substitution and the "
                     "units in the result.",
         "good_reasoning": ["=", "v", "a", "ohm"],
         "followup_ok": "Good — substitution and units are present. "
                        "Last: does the result make physical sense?"},
        {"focus": "sanity_check",
         "question": "Sanity check: is the magnitude reasonable for this "
                     "rail/component? If not, what would that imply?",
         "good_reasoning": ["reasonable", "expected", "sense", "too", "high", "low"],
         "followup_ok": "Good — you checked the result against the "
                        "physical expectation."},
    ],
    "voltage_divider": [
        {"focus": "topology",
         "question": "Look at the divider: which resistor is on top (to Vin) "
                     "and which is on the bottom (to ground)? Where is Vout "
                     "taken?",
         "good_reasoning": ["top", "bottom", "ground", "vout", "r1", "r2"],
         "followup_ok": "Good — the topology is right. Next: what is the "
                        "formula for Vout?"},
        {"focus": "formula",
         "question": "Write the divider formula with R1, R2, and Vin. Which "
                     "resistor is in the numerator?",
         "good_reasoning": ["r2", "r1+r2", "vin", "vout"],
         "followup_ok": "Good — Vout = Vin * R2/(R1+R2). Next: what happens "
                        "if you load Vout with a low impedance?"},
        {"focus": "loading",
         "question": "If you draw real current from Vout, does the divider "
                     "still give the formula's value? Why or why not?",
         "good_reasoning": ["load", "current", "drop", "regulator", "not"],
         "followup_ok": "Good — a divider is not a regulator. Last: when "
                        "would you use a divider vs. a real regulator?"},
    ],
    "mosfet": [
        {"focus": "gate_drive",
         "question": "For this MOSFET to turn ON, what must Vgs be relative "
                     "to the threshold? Which pin do you measure Vgs between?",
         "good_reasoning": ["vgs", "threshold", "gate", "source"],
         "followup_ok": "Good — Vgs must exceed Vth, measured gate-to-source. "
                        "Next: what is Rds(on) and why does it matter here?"},
        {"focus": "rdson",
         "question": "If the MOSFET is ON, what determines the voltage drop "
                     "across it? What is that drop called?",
         "good_reasoning": ["rds", "rds(on)", "drop", "on"],
         "followup_ok": "Good — Rds(on) × Id is the drop. Last: what does a "
                        "0 V Vgs with a body diode tell you?"},
        {"focus": "body_diode",
         "question": "If you measure 0.3 V across source-drain in one "
                     "direction and 0 V in the other, what is conducting?",
         "good_reasoning": ["body", "diode", "direction"],
         "followup_ok": "Good — the body diode conducts in one direction; "
                        "that is a key diagnostic clue."},
    ],
    "measurement_interp": [
        {"focus": "ground_reference",
         "question": "Before you trust this voltage reading, where is your "
                     "DMM black probe? Why does that matter?",
         "good_reasoning": ["ground", "reference", "black", "gnd"],
         "followup_ok": "Good — the ground reference is stated. Next: is the "
                        "reading in the meter's right range?"},
        {"focus": "range",
         "question": "Is the meter on a range that can actually resolve "
                     "this value? What would a wrong range look like?",
         "good_reasoning": ["range", "resolution", "overload", "ol"],
         "followup_ok": "Good — range and resolution are correct. Last: "
                        "could two readings on the same net contradict?"},
        {"focus": "contradiction",
         "question": "If two measurements of the same rail disagree, what "
                     "are the three most likely explanations?",
         "good_reasoning": ["ground", "reference", "ac", "dc", "load", "point"],
         "followup_ok": "Good — ground reference, AC vs DC, and load/no-load "
                        "are the usual culprits."},
    ],
    "fault_isolation": [
        {"focus": "hypothesis_first",
         "question": "Before you measure: state TWO hypotheses that would "
                     "explain the symptom, and what each would predict.",
         "good_reasoning": ["hypothesis", "predict", "if", "then"],
         "followup_ok": "Good — two hypotheses with predictions. Next: "
                        "which ONE measurement would distinguish them?"},
        {"focus": "decisive_test",
         "question": "Which single measurement is decisive — i.e., its "
                     "outcome rules in one hypothesis and rules out the "
                     "other? Why that one?",
         "good_reasoning": ["decisive", "distinguish", "rule", "out", "in"],
         "followup_ok": "Good — a decisive test beats ten non-decisive "
                        "ones. Last: what is the root cause vs. the symptom?"},
        {"focus": "root_vs_symptom",
         "question": "If the rail is down, is 'rail down' the root cause or "
                     "a symptom? How do you tell?",
         "good_reasoning": ["root", "symptom", "cause", "upstream", "downstream"],
         "followup_ok": "Good — 'rail down' is a symptom; the root cause is "
                        "upstream (converter, enable, input)."},
    ],
}


@dataclass
class SocraticTurn:
    skill_key: str
    step: int
    focus: str
    question: str
    is_final: bool = False
    feedback: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


class SocraticEngine:
    """Holds one in-memory Socratic path per (profile, skill) and walks
    it one question at a time. The path state is not persisted by
    default (a Socratic session is short-lived); callers may persist
    the step in the learning session if they want resume."""

    def __init__(self, profile_id: Optional[int] = None,
                 profile_name: str = "default"):
        if profile_id is None:
            prof = db.get_or_create_profile(profile_name)
            profile_id = int(prof["id"])
        self.profile_id = int(profile_id)
        self._step: Dict[str, int] = {}   # skill_key -> current step
        self.evaluator = ReasoningEvaluator(profile_id=profile_id)

    def start(self, skill_key: str) -> Optional[SocraticTurn]:
        """Begin (or restart) a Socratic path for a skill."""
        if skill_key not in PATHS:
            return None
        self._step[skill_key] = 0
        return self._turn(skill_key, 0)

    def current(self, skill_key: str) -> Optional[SocraticTurn]:
        step = self._step.get(skill_key, 0)
        return self._turn(skill_key, step)

    def respond(self, skill_key: str, user_text: str) -> SocraticTurn:
        """Evaluate the user's reasoning for the current question, then
        advance (or hold) and return the next turn (which may be a
        follow-up question or a closing note)."""
        path = PATHS.get(skill_key, [])
        step = self._step.get(skill_key, 0)
        if not path or step >= len(path):
            return SocraticTurn(skill_key, step, "done",
                                "No active Socratic path. Use 'learning start "
                                "<skill>' to begin one.", is_final=True)
        current_q = path[step]
        # Evaluate the reasoning for THIS question (lightweight: check
        # that the user mentioned the principle + a reasoning connective).
        ok, note = self._evaluate_reasoning(current_q, user_text)
        if ok:
            next_step = step + 1
            self._step[skill_key] = next_step
            if next_step >= len(path):
                return SocraticTurn(
                    skill_key, next_step - 1, current_q["focus"], "",
                    is_final=True,
                    feedback=note + " " + current_q.get("followup_ok", "") +
                    " That completes this Socratic path.")
            nxt = path[next_step]
            return SocraticTurn(
                skill_key, next_step, nxt["focus"], nxt["question"],
                feedback=note + " " + current_q.get("followup_ok", ""))
        # Hold the step; re-ask with a targeted nudge.
        nudge = self._nudge(current_q, user_text)
        return SocraticTurn(
            skill_key, step, current_q["focus"], nudge,
            feedback=note)

    # -- internals ------------------------------------------------------
    def _turn(self, skill_key: str, step: int) -> Optional[SocraticTurn]:
        path = PATHS.get(skill_key, [])
        if not path or step >= len(path):
            return None
        q = path[step]
        return SocraticTurn(
            skill_key, step, q["focus"], q["question"],
            is_final=(step + 1 >= len(path)))

    def _evaluate_reasoning(self, q: Dict[str, Any],
                            user_text: str) -> tuple:
        low = (user_text or "").lower()
        if not low.strip():
            return False, "No reasoning given. Try to state the principle " \
                          "before the number."
        good = q.get("good_reasoning", [])
        hits = sum(1 for g in good if g.lower() in low)
        has_connective = any(c in low for c in
                             ("=", "so", "therefore", "because", "since",
                              "thus", "hence", "->", "=>"))
        if hits >= 1 and has_connective:
            return True, "Reasoning recognized."
        if hits >= 1:
            return True, "Right principle; next time state the reasoning " \
                         "chain explicitly (so/because/therefore)."
        return False, ("The reasoning does not yet reference the principle "
                       "this question targets. Re-read the question and "
                       "state the law/definition it asks for, then your "
                       "answer.")

    def _nudge(self, q: Dict[str, Any], user_text: str) -> str:
        focus = q.get("focus", "")
        return (f"Not quite. Re-consider the focus: '{focus}'. "
                f"State the principle first, then your answer. "
                f"(Hint-level only; the full explanation is available "
                f"with 'learning explain'.)")

    def available_skills(self) -> List[str]:
        return list(PATHS.keys())
