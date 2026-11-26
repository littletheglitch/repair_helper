"""Engineering Reasoning Evaluator.

Grades a user's response to an exercise on multiple axes and gives
specific, technical feedback. The central rule (from the spec):

  "Do not reward an answer that happens to be correct for incorrect
   reasoning."

So a response is only "correct" if BOTH the final answer is within the
valid range AND the reasoning checks out. A right number with wrong
physics is flagged as incorrect, with an explanation of why.

Axes evaluated:
  1. Physical principle — is the law/formula the user invoked the right
     one for this problem? (e.g. using Ohm's law where Kirchhoff is needed.)
  2. Formula and units — are the units consistent? Did the user carry
     units through? (Uses the existing ``Quantity`` type and the
     electrical_math engine to verify the arithmetic.)
  3. Logical consistency — does the conclusion follow from the steps?
  4. Evidence — does the user cite the measurement/engine result that
     supports the conclusion (provenance)?
  5. Missing information — did the user identify a needed-but-absent
     value rather than guessing it?
  6. Unsupported assumptions — did the user assume a value without a
     source (e.g. "the rail is 3.3V" without a measurement or datasheet)?
  7. Measurement misinterpretation — did the user read a measurement
     correctly (ground reference, range, sign, contradiction)?

The evaluator is deterministic. It parses the user's free-text answer
for the final numeric answer and for the reasoning, checks the numeric
answer against the exercise's reference solution / valid range (using
the engines when the reference solution itself was engine-computed),
and applies the rule-based reasoning checks. It never invents a
reference value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .. import schemas as S
from ..engines import base as E
from . import db


# Numeric answer extraction.
# 1. An EXPLICIT "answer/result/final is X" or "=> X" (highest confidence —
#    the user marked the conclusion).
_EXPLICIT_ANSWER_RE = re.compile(
    r"(?:answer\s*[:is]\s*|result\s*[:is]\s*|final(?:ly)?\s*[:is]\s*"
    r"|therefore\s*[:is]?\s*|=>\s*|→\s*)"
    r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([A-Za-zµμΩ/%°]+)?",
    re.IGNORECASE)
# 2. A "= X unit" anywhere (the user's substitution conclusion). We take
#    the LAST one because the conclusion comes after the reasoning.
_EQ_ANSWER_RE = re.compile(
    r"=\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([A-Za-zµμΩ/%°]+)?")
# 3. Bare trailing number + unit (no "answer" keyword, end of text).
_TRAILING_RE = re.compile(
    r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([A-Za-zµμΩ/%]+)\s*\.?\s*$")
# A "label: number" pattern like "exercise 5:" or "step 3:" that should
# NOT be mistaken for the final answer when we fall back to the last
# bare number.
_LABEL_NUM_RE = re.compile(
    r"\b(exercise|step|q|question|page|item|part)\s*#?\s*\d+", re.IGNORECASE)


# Canonical unit dimensions for the unit check (D4). Maps a user-written
# unit string to a (base_dimension, prefix_multiplier) pair so that "kΩ"
# and "Ω" are recognized as the same dimension (resistance) and the
# magnitude is left to the range check.
_UNIT_BASE = {
    # voltage
    "v": ("voltage", 1), "volt": ("voltage", 1), "volts": ("voltage", 1),
    "mv": ("voltage", 1e-3), "kv": ("voltage", 1e3),
    # current
    "a": ("current", 1), "amp": ("current", 1), "amps": ("current", 1),
    "ma": ("current", 1e-3), "ua": ("current", 1e-6), "µa": ("current", 1e-6),
    "ka": ("current", 1e3),
    # resistance
    "ω": ("resistance", 1), "ohm": ("resistance", 1), "ohms": ("resistance", 1),
    "kω": ("resistance", 1e3), "kohm": ("resistance", 1e3), "kohms": ("resistance", 1e3),
    "mω": ("resistance", 1e-3), "mohm": ("resistance", 1e-3),
    "mω": ("resistance", 1e6), "megohm": ("resistance", 1e6),
    # power
    "w": ("power", 1), "watt": ("power", 1), "watts": ("power", 1),
    "mw": ("power", 1e-3), "kw": ("power", 1e3),
    # time
    "s": ("time", 1), "sec": ("time", 1), "ms": ("time", 1e-3),
    "us": ("time", 1e-6), "µs": ("time", 1e-6),
    # capacitance
    "f": ("capacitance", 1), "uf": ("capacitance", 1e-6),
    "µf": ("capacitance", 1e-6), "nf": ("capacitance", 1e-9), "pf": ("capacitance", 1e-12),
    # frequency
    "hz": ("frequency", 1), "khz": ("frequency", 1e3),
    "mhz": ("frequency", 1e6), "ghz": ("frequency", 1e9),
}


def _unit_to_base(unit: str) -> tuple:
    """Return (base_dimension, multiplier) for a unit string, lowercased
    and stripped of trailing dots. Unknown -> ('', 1)."""
    if not unit:
        return "", 1
    key = unit.strip().rstrip(".").lower()
    # Handle "MΩ" (mega) which lowercases to "mω" (milli) — disambiguate
    # by the original case: uppercase M prefix means mega.
    if unit.startswith("M") and key in ("mω", "mhv", "mv"):
        # "MΩ" -> mega-ohm; "MHz" handled below.
        if key == "mω":
            return "resistance", 1e6
    return _UNIT_BASE.get(key, ("", 1))


@dataclass
class Evaluation:
    correct: bool                  # final answer in range AND reasoning ok
    answer_in_range: bool          # final answer in range (ignoring reasoning)
    answer_value: Optional[float]  # the numeric answer we extracted
    answer_unit: str
    reference_value: Optional[float]
    valid_range: Optional[Dict[str, float]]
    reasoning_score: float         # 0..1
    issues: List[str] = field(default_factory=list)
    feedback: str = ""
    evaluator: str = "rule-based"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "correct": self.correct,
            "answer_in_range": self.answer_in_range,
            "answer_value": self.answer_value,
            "answer_unit": self.answer_unit,
            "reference_value": self.reference_value,
            "valid_range": self.valid_range,
            "reasoning_score": round(self.reasoning_score, 3),
            "issues": self.issues,
            "feedback": self.feedback,
            "evaluator": self.evaluator,
        }


class ReasoningEvaluator:
    """Rule-based, deterministic reasoning evaluator."""

    def __init__(self, profile_id: Optional[int] = None,
                 profile_name: str = "default"):
        if profile_id is None:
            prof = db.get_or_create_profile(profile_name)
            profile_id = int(prof["id"])
        self.profile_id = int(profile_id)
        self._last_answer_unit: str = ""  # set by _extract_answer each call

    # -- Public ---------------------------------------------------------
    def evaluate(self, exercise: Dict[str, Any],
                 response_text: str,
                 hints_used: int = 0) -> Evaluation:
        """Evaluate a user's free-text response to an exercise.

        ``exercise`` is a row from ``exercises`` (see db.get_exercise).
        """
        answer_value, answer_unit = self._extract_answer(response_text)
        ref_value, ref_unit, valid_range = self._reference(exercise)
        # A qualitative/reasoning exercise (the reference is a prose
        # answer, not a number) is graded ONLY on reasoning. In that
        # case answer_in_range defaults to True (the reasoning checks
        # are the gate), and no numeric check applies.
        ref_str = exercise.get("reference_solution") or ""
        is_numeric_exercise = bool(
            re.match(r"^\s*[-+]?\d", ref_str)
            and valid_range is None or
            re.match(r"^\s*[-+]?\d", ref_str) and
            isinstance(valid_range, dict)
        )
        if not re.match(r"^\s*[-+]?\d", ref_str) and not valid_range:
            # Qualitative: no number expected. answer_in_range = True.
            answer_in_range = True
        else:
            # 1) Numeric check (against the engine-computed reference
            # or range).
            answer_in_range = self._check_range(answer_value, ref_value,
                                                valid_range, ref_unit,
                                                exercise)
        # 2) Reasoning checks.
        issues: List[str] = []
        self._check_physical_principle(exercise, response_text, issues)
        self._check_units(exercise, response_text, answer_unit, issues)
        self._check_logic(response_text, issues)
        self._check_evidence_provenance(exercise, response_text, issues)
        self._check_missing_info(exercise, response_text, issues)
        self._check_unsupported_assumptions(exercise, response_text, issues)
        self._check_measurement_misinterpretation(response_text, issues)
        self._check_multi_step_reasoning(exercise, response_text, issues)
        self._check_correlation_causation(exercise, response_text, issues)
        self._check_datasheet_value_class(exercise, response_text, issues)
        # Reasoning score: start at 1.0, subtract per issue, floor 0.
        reasoning_score = max(0.0, 1.0 - 0.15 * len(issues))
        # The spec's central rule: correct requires BOTH.
        correct = bool(answer_in_range and reasoning_score >= 0.6 and not
                       any("incorrect principle" in i or
                           "wrong units" in i or
                           "misinterpret" in i or
                           "unsupported assumption" in i or
                           "missing information not identified" in i or
                           "incomplete multi-step reasoning" in i or
                           "correlation-vs-causation" in i or
                           "datasheet value-class error" in i or
                           "no reasoning chain" in i or
                           "no evidence cited" in i
                           for i in issues))
        feedback = self._compose_feedback(
            answer_value, answer_unit, ref_value, valid_range,
            answer_in_range, reasoning_score, issues, correct)
        return Evaluation(
            correct=correct, answer_in_range=answer_in_range,
            answer_value=answer_value, answer_unit=answer_unit or ref_unit,
            reference_value=ref_value, valid_range=valid_range,
            reasoning_score=reasoning_score, issues=issues,
            feedback=feedback,
        )

    def evaluate_and_record(self, exercise: Dict[str, Any],
                            response_text: str, hints_used: int = 0,
                            level_used: str = "guided") -> Dict[str, Any]:
        """Evaluate, persist the response + feedback, and record the
        right kind of mastery evidence. Returns a dict with the
        evaluation + the response/feedback ids."""
        ev = self.evaluate(exercise, response_text, hints_used)
        solved_independently = bool(ev.correct and hints_used == 0)
        response_id = db.record_response(
            exercise["id"], self.profile_id, response_text,
            answer_value=str(ev.answer_value) if ev.answer_value is not None else "",
            level_used=level_used, hints_used=hints_used,
            solved_independently=solved_independently)
        feedback_id = db.record_feedback(
            response_id, ev.correct, ev.reasoning_score, ev.feedback,
            ev.issues, ev.evaluator)
        # Record mastery evidence.
        skill_key = exercise.get("skill_key", "")
        if solved_independently:
            # ALWAYS record an independent solve for a clean solve.
            db.record_evidence(self.profile_id, skill_key,
                               "solved_independently",
                               exercise_id=exercise["id"])
            # ADDITIONALLY, if the user has already independently solved
            # >=3 OTHER distinct exercises on this skill, this is a
            # new-context demonstration (the strongest evidence). The
            # >=3 threshold means new-context evidence only appears
            # AFTER the user has reached practitioner, so it represents
            # consolidation well beyond a first attempt, not a fluke.
            other_solved = {r["exercise_id"] for r in db.list_responses(self.profile_id)
                            if r.get("solved_independently")
                            and r.get("exercise_id") != exercise["id"]}
            if len(other_solved) >= 3:
                db.record_evidence(self.profile_id, skill_key,
                                   "demonstrated_in_new_context",
                                   exercise_id=exercise["id"])
        elif ev.correct and hints_used > 0:
            db.record_evidence(self.profile_id, skill_key,
                               "solved_with_hints", exercise_id=exercise["id"])
        else:
            db.record_evidence(self.profile_id, skill_key, "attempted",
                               exercise_id=exercise["id"])
        return {
            "evaluation": ev.to_dict(),
            "response_id": response_id,
            "feedback_id": feedback_id,
            "solved_independently": solved_independently,
        }

    # -- Numeric checks -------------------------------------------------
    def _extract_answer(self, text: str) -> tuple:
        self._last_answer_unit = ""
        # Priority 1: an EXPLICIT "answer/result/final/therefore is X" —
        # the user marked the conclusion, so this is the answer.
        m = _EXPLICIT_ANSWER_RE.search(text)
        if m:
            self._last_answer_unit = m.group(2) or ""
            return float(m.group(1)), m.group(2) or ""
        # Priority 2: the LAST "= X unit" in the text. The conclusion
        # typically comes after the substitution, so the last "=" is the
        # result of the final computation (not a given value quoted
        # mid-reasoning).
        matches = list(_EQ_ANSWER_RE.finditer(text))
        if matches:
            m = matches[-1]
            self._last_answer_unit = m.group(2) or ""
            return float(m.group(1)), m.group(2) or ""
        # Priority 3: a trailing "number unit" at the very end.
        m = _TRAILING_RE.search(text)
        if m:
            self._last_answer_unit = m.group(2) or ""
            return float(m.group(1)), m.group(2) or ""
        # Priority 4 (lowest): the last bare number — but ONLY if it is
        # not a label like "exercise 5" or "step 3". This guard prevents
        # picking an index/page number as the answer.
        nums = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
        if nums:
            for n in reversed(nums):
                idx = text.rfind(n)
                while idx != -1:
                    prefix = text[max(0, idx - 12):idx].lower()
                    if not _LABEL_NUM_RE.search(prefix + n):
                        return float(n), ""
                    idx = text.rfind(n, 0, idx)
            return None, ""
        return None, ""

    def _reference(self, exercise: Dict[str, Any]) -> tuple:
        ref = exercise.get("reference_solution") or ""
        valid_range = exercise.get("valid_range")
        unit = exercise.get("unit", "")
        ref_value: Optional[float] = None
        # reference_solution may be "10.0" or "10 V" or a JSON envelope.
        try:
            ref_value = float(ref)
        except (TypeError, ValueError):
            m = re.search(r"[-+]?\d+(?:\.\d+)?", ref or "")
            if m:
                ref_value = float(m.group(0))
        return ref_value, unit, valid_range

    def _check_range(self, answer_value: Optional[float],
                     ref_value: Optional[float],
                     valid_range: Optional[Dict[str, float]],
                     ref_unit: str,
                     exercise: Dict[str, Any]) -> bool:
        if answer_value is None:
            return False
        # Unit-aware RC range: the valid_range may carry per-unit bands
        # (min_s/max_s for seconds, min_ms/max_ms for ms) plus an
        # accept_units list. We pick the band matching the user's unit.
        if valid_range and "accept_units" in valid_range:
            # Decide which band to use based on the extracted answer unit.
            au = (self._last_answer_unit or "").lower()
            if au in ("ms", "millisecond", "milliseconds") and "min_ms" in valid_range:
                lo, hi = float(valid_range["min_ms"]), float(valid_range["max_ms"])
                return lo <= answer_value <= hi
            if "min_s" in valid_range:
                lo, hi = float(valid_range["min_s"]), float(valid_range["max_s"])
                return lo <= answer_value <= hi
            # Fall through to the generic single-band path.
        # If the exercise has an explicit single-band valid_range, use it.
        if valid_range and ("min" in valid_range or "low" in valid_range):
            lo = float(valid_range.get("min", valid_range.get("low", 0)))
            hi = float(valid_range.get("max", valid_range.get("high", 0)))
            tol = float(valid_range.get("tol", 0))
            if lo <= answer_value <= hi:
                return True
            if tol and ref_value is not None and \
                    abs(answer_value - ref_value) <= tol:
                return True
            return False
        # Else compare to ref_value with a 5% relative tolerance (or
        # 0.1 absolute if ref is ~0). This tolerance is generous on
        # purpose: the reasoning check is the strict gate.
        if ref_value is None:
            return False
        if abs(ref_value) < 1e-9:
            return abs(answer_value) < 0.1
        return abs(answer_value - ref_value) / max(abs(ref_value), 1e-9) <= 0.05

    # -- Reasoning checks (heuristic, deterministic) --------------------
    def _check_physical_principle(self, exercise: Dict[str, Any],
                                  text: str, issues: List[str]) -> None:
        sk = exercise.get("skill_key", "")
        low = text.lower()
        # Per-skill principle patterns. Each skill accepts only the
        # principles that are actually relevant to it, so "divider" does
        # NOT satisfy an Ohm exercise and "ohm" does NOT satisfy a
        # divider exercise. This is the fix for the independent-set
        # false positives/negatives.
        if sk == "ohm_kirchhoff" or sk == "electrical_math":
            # Accept any form of Ohm's law: the phrase "ohm's law" /
            # "ohm law" / "by ohm", or any of the algebraic forms
            # R=V/I, V=IR, I=V/R (with optional spaces, *, ·, ×, /). The
            # bare unit word "ohm" (as in "2.5 ohm") does NOT count — that
            # is the unit, not the law.
            # For electrical_math POWER exercises (P = V*I), also accept
            # the power formula.
            has_ohm_law = bool(re.search(
                r"ohm'?s?\s*law|by\s+ohm|ohm'?s?\s+relat", low))
            has_formula = bool(re.search(
                r"v\s*=\s*i\s*[\*\·\×]?\s*r|i\s*=\s*v\s*/\s*r"
                r"|r\s*=\s*v\s*/\s*i|v\s*/\s*i\s*=", low))
            has_power = bool(re.search(
                r"p\s*=\s*v\s*[\*\·\×]\s*i|p\s*=\s*i\s*[\*\·\×]\s*v"
                r"|p\s*=\s*i\s*\*\*?\s*2\s*[\*\·\×]?\s*r|"
                r"p\s*=\s*v\s*\*\*?\s*2\s*/\s*r", low))
            has_kirchhoff = "kirchhoff" in low or "kvl" in low or "kcl" in low
            if not (has_ohm_law or has_formula or has_power or has_kirchhoff):
                issues.append("incorrect principle: the answer does not "
                              "reference Ohm's law (R=V/I, V=IR, or I=V/R), "
                              "the power formula (P=V*I), or Kirchhoff's "
                              "laws for this problem.")
        elif sk == "series_parallel":
            if not re.search(r"\b(series|parallel)\b|1\s*/\s*r|r1\s*\+\s*r2"
                             r"|\bohm\b|i\s*=\s*v\s*/\s*r|v\s*/\s*r", low):
                issues.append("incorrect principle: a series/parallel "
                              "answer should reference series or parallel "
                              "combination (or Ohm's law I=V/R on the "
                              "branch).")
        elif sk == "voltage_divider":
            if not re.search(r"divider|vout\s*=\s*vin\s*\*|r2\s*/\s*\(r1"
                             r"|r1\s*\+\s*r2|\bohm\b", low):
                issues.append("incorrect principle: a voltage-divider "
                              "answer should reference the divider formula "
                              "Vout = Vin * R2/(R1+R2).")
        if sk == "mosfet" and "vgs" not in low and "gate" not in low \
                and "rds" not in low and "threshold" not in low:
            issues.append("incorrect principle: a MOSFET answer should "
                          "discuss Vgs / threshold / Rds(on), not just a "
                          "bare number.")
        if sk == "measurement_interp" and "ground" not in low and "reference" not in low:
            issues.append("incorrect principle: a measurement-interpretation "
                          "answer should state the ground reference.")

    def _check_units(self, exercise: Dict[str, Any], text: str,
                     answer_unit: str, issues: List[str]) -> None:
        ref_unit = (exercise.get("unit") or "").strip()
        if not ref_unit or not answer_unit:
            return
        # If the exercise explicitly accepts multiple units (e.g. an RC
        # exercise accepts both "s" and "ms"), accept any of them.
        vr = exercise.get("valid_range") or {}
        accept = vr.get("accept_units") if isinstance(vr, dict) else None
        if accept and answer_unit.lower() in [u.lower() for u in accept]:
            return
        # Normalize the unit to a canonical (base, prefix) pair so that
        # "kΩ" vs "Ω" and "mV" vs "V" are compared correctly. The MAGNITUDE
        # consistency is handled by the range check; here we flag a unit
        # that is dimensionally incompatible (e.g. ohms vs volts vs
        # henries).
        ref_base, _ = _unit_to_base(ref_unit)
        ans_base, _ = _unit_to_base(answer_unit)
        if not ref_base:
            return  # no canonical reference to compare against
        if not ans_base:
            # The user wrote a unit we don't recognize (e.g. "H" for an
            # ohm exercise). That is a unit mismatch.
            issues.append(f"wrong units: answer given in '{answer_unit}' "
                          f"which is not a recognized {ref_base} unit; "
                          f"the problem expects '{ref_unit}'.")
            return
        if ref_base != ans_base:
            # Both known but different dimensions (e.g. volts vs amps).
            issues.append(f"wrong units: answer given in '{answer_unit}' "
                          f"({ans_base}) but the problem expects '{ref_unit}' "
                          f"({ref_base}).")

    def _check_logic(self, text: str, issues: List[str]) -> None:
        # Look for a reasoning connective (so/therefore/because/...) that
        # signals an actual reasoning chain. A numeric answer with NO
        # connective is a bare-result answer and should be flagged
        # regardless of length — the spec says we evaluate reasoning,
        # not just the final number.
        low = text.lower()
        has_connective = bool(re.search(
            r"\b(so|therefore|because|since|thus|hence|consequently|"
            r"which means|meaning|implies)\b|\b=>\b|->|→", low))
        has_steps = bool(re.search(
            r"\b(substitut|plug in|step \d|first|then|next|finally)\b", low))
        if not has_connective and not has_steps:
            issues.append("no reasoning chain: the answer states a result "
                          "with no 'so/therefore/because' connective or "
                          "step-by-step structure; show the reasoning, not "
                          "just the result.")

    def _check_evidence_provenance(self, exercise: Dict[str, Any],
                                   text: str, issues: List[str]) -> None:
        # For exercises that come from a measurement/engine source AND
        # expect a NUMERIC answer (the reference is a number), the user
        # should cite the values they substituted. "Substituting 5V and
        # 2A" or "V=5, I=2" counts as citing the evidence; a bare "R =
        # 2.5" with no values referenced does not.
        # For qualitative/reasoning exercises (reference is a prose
        # answer, not a number), this check is skipped — the reasoning
        # connectives and the multi-step/correlation checks cover it.
        ref = exercise.get("reference_solution") or ""
        ref_is_numeric = bool(re.match(r"^\s*[-+]?\d", ref))
        if not ref_is_numeric:
            return
        src = (exercise.get("source_kind") or "")
        low = text.lower()
        if src in ("engine", "confirmed_case"):
            cites = bool(re.search(
                r"\b(measur|engine|tool|from|result|substitut|plug|given"
                r"|with|across|through)\b", low))
            has_value_unit = bool(re.search(
                r"\d+(?:\.\d+)?\s*(?:v|a|ω|ohm|volt|amp)", low))
            if not cites and not has_value_unit:
                issues.append("no evidence cited: the conclusion should "
                              "reference the measurement or the substituted "
                              "values it rests on.")

    def _check_missing_info(self, exercise: Dict[str, Any],
                            text: str, issues: List[str]) -> None:
        # Positive check: did the user IDENTIFY a missing value? We only
        # add a positive note here (not an issue) — but if the exercise
        # explicitly requires acknowledging a gap and the user didn't,
        # flag it.
        if "requires_missing" in (exercise.get("assumptions") or ""):
            low = text.lower()
            if not re.search(r"\b(missing|need|absent|not (?:given|measured)|"
                             r"unknown)\b", low):
                issues.append("missing information not identified: this "
                              "problem has a deliberately absent value; "
                              "state that you need it rather than guessing.")

    def _check_unsupported_assumptions(self, exercise: Dict[str, Any],
                                       text: str,
                                       issues: List[str]) -> None:
        # Flag an un-sourced value assertion. Technicians state
        # assumptions in many phrasings: "assume X", "X is Y", "the rail
        # is Z", "let X = Y". We flag any of these that asserts a numeric
        # or component value WITHOUT a source keyword (from/per/
        # datasheet/measurement/given) AND that is not one of the values
        # already GIVEN in the exercise prompt (restating a given is
        # citing evidence, not assuming).
        low = text.lower()
        prompt = (exercise.get("prompt") or "").lower()
        # Numbers stated in the prompt (the givens). We extract them so we
        # can skip "the rail is 5" when 5 is a given value.
        given_nums = set()
        for m in re.finditer(r"[-+]?\d+(?:\.\d+)?", prompt):
            given_nums.add(float(m.group(0)))
        all_nums = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
        last_num = float(all_nums[-1]) if all_nums else None
        rail_is_pat = (r"\bthe\s+(?:rail|voltage|current|resistance|value|load)\s+"
                       r"is\s+([-+]?\d+(?:\.\d+)?)")
        source_words = ("from", "per", "datasheet", "measurement", "given",
                         "measured", "specified", "rated", "across",
                         "through", "supplies")
        flagged = False
        for m in re.finditer(r"assume\s+([^.]{3,80})", low):
            clause = m.group(0).strip()
            if any(w in clause for w in ("ideal", "steady", "linear",
                                          "constant", "lossless",
                                          "negligible")):
                continue
            if not any(w in clause for w in source_words):
                issues.append(f"unsupported assumption: '{clause}' "
                              "states a value without a source; cite a "
                              "measurement or datasheet value.")
                flagged = True
                break
        if not flagged:
            for m in re.finditer(rail_is_pat, low):
                asserted = float(m.group(1))
                # Skip if this asserted value IS the user's conclusion
                # (the last number in the text) — that is the answer.
                if last_num is not None and abs(asserted - last_num) < 1e-6:
                    continue
                # Skip if this value was GIVEN in the exercise prompt —
                # restating a given is citing evidence, not assuming.
                if any(abs(asserted - g) < 1e-6 for g in given_nums):
                    continue
                clause = m.group(0).strip()
                if any(w in clause for w in source_words):
                    continue
                issues.append(f"unsupported assumption: '{clause}' "
                              "states a value without a source; cite a "
                              "measurement or datasheet value.")
                flagged = True
                break
        if not flagged:
            for m in re.finditer(r"\blet\s+([a-z0-9_]+)\s*=\s*([-+]?\d+(?:\.\d+)?)", low):
                var_name = m.group(1)
                let_num = float(m.group(2))
                # Skip if the let-value is a given AND the variable is a
                # given quantity (V or I, not R which is the unknown).
                # "Let R = 5" is assuming the answer, not citing a given.
                if var_name in ("v", "i", "vin", "iin") and \
                        any(abs(let_num - g) < 1e-6 for g in given_nums):
                    continue
                if var_name in ("r", "req", "resistance"):
                    # Letting the unknown equal a value is an assumption.
                    pass
                clause = m.group(0).strip()
                if any(w in clause for w in source_words):
                    continue
                issues.append(f"unsupported assumption: '{clause}' "
                              "states a value without a source; cite a "
                              "measurement or datasheet value.")
                break

    def _check_measurement_misinterpretation(self, text: str,
                                             issues: List[str]) -> None:
        low = text.lower()
        # A 0V (or near-0V) reading interpreted as "no voltage / dead /
        # absent / no power" WITHOUT a stated ground reference is a
        # classic measurement misinterpretation: 0V on a floating net
        # means nothing; 0V relative to the right ground does.
        has_zero = bool(re.search(r"\b0(\.0+)?\s*v\b|\bzero\s*volt", low))
        interpreted_absent = any(p in low for p in
                                  ("no voltage", "no power", "dead",
                                   "absent", "nothing", "0 v means",
                                   "0v means", "0v = no", "no rail"))
        stated_ground = any(p in low for p in
                             ("ground", "gnd", "reference", "common",
                              "black probe", "negative probe", "relative to"))
        if has_zero and interpreted_absent and not stated_ground:
            issues.append("measurement misinterpretation: a 0 V reading "
                          "needs a stated ground reference before it can "
                          "be interpreted as 'no voltage / dead / absent'. "
                          "A floating net reads 0 V too.")

    def _check_multi_step_reasoning(self, exercise: Dict[str, Any],
                                     text: str, issues: List[str]) -> None:
        """Phase 4.2: for exercises flagged as multi-step (the prompt or
        assumptions contain 'two hypotheses', 'two ranked', 'step 1',
        'two reasons', or ask for multiple quantities), the user must
        show multiple distinct steps. A single-step answer to a multi-
        step problem is incomplete."""
        assumptions = (exercise.get("assumptions") or "").lower()
        prompt = (exercise.get("prompt") or "").lower()
        low = text.lower()
        # Detect multi-step exercises.
        multi_step_markers = [
            "two ranked", "two hypotheses", "two reasons", "rank two",
            "step 1", "two measurements", "multiple steps",
            "list the two", "name two", "two most likely",
        ]
        is_multi = any(m in prompt or m in assumptions
                        for m in multi_step_markers)
        if not is_multi:
            return
        # Count distinct reasoning steps in the answer. We look for
        # enumerated markers (1., 2., first, second, then, next, H1,
        # H2, hypothesis 1, hypothesis 2) OR multiple "therefore/so"
        # connectives (each conclusion is a step).
        step_markers = re.findall(
            r"\b1\.|\b2\.|\bfirst\b|\bsecond\b|\bh1\b|\bh2\b"
            r"|hypothesis 1|hypothesis 2|\bthen\b|\bnext\b", low)
        connectives = re.findall(
            r"\b(therefore|so|thus|hence)\b", low)
        # A multi-step answer needs at least 2 distinct steps.
        distinct_steps = max(len(set(step_markers)), len(connectives))
        if distinct_steps < 2:
            issues.append("incomplete multi-step reasoning: this problem "
                          "asks for multiple steps/hypotheses; the answer "
                          "shows fewer than 2 distinct reasoning steps. "
                          "Number your steps or use 'first/second'/'H1/H2'.")

    def _check_datasheet_value_class(self, exercise: Dict[str, Any],
                                      text: str, issues: List[str]) -> None:
        """Phase 4.2: for datasheet exercises, the answer must correctly
        identify the value class (abs max / recommended / typical /
        guaranteed) the exercise asks about. A 'yes it is safe' to an
        abs-max question, or 'use typical' to a worst-case question, is
        a value-class error."""
        sk = exercise.get("skill_key", "")
        if sk != "datasheet_interp":
            return
        assumptions = (exercise.get("assumptions") or "").lower()
        low = text.lower()
        # Extract the value class the exercise targets.
        cls = None
        for c in ("abs_max", "absolute maximum", "recommended",
                  "typical", "guaranteed"):
            if c in assumptions or c.replace("_", " ") in assumptions:
                cls = c
                break
        if cls is None:
            return
        # For abs_max and recommended exercises, the correct answer is
        # "NOT safe / not guaranteed" — flag a bare "yes it is safe".
        if cls in ("abs_max", "absolute maximum", "recommended"):
            says_safe = bool(re.search(
                r"\b(yes|it is safe|that'?s fine|acceptable)\b", low))
            says_unsafe = bool(re.search(
                r"\b(no|not safe|not guaranteed|not acceptable|stress limit"
                r"|outside)\b", low))
            if says_safe and not says_unsafe:
                issues.append("datasheet value-class error: this is an "
                              "absolute-maximum / recommended-operating "
                              "question; operating at the limit is NOT "
                              "safe for continuous operation. State that it "
                              "is not safe and why (stress limit vs "
                              "operating point).")
        # For typical exercises, the correct answer is "do NOT use
        # typical for worst-case" — flag "use typical".
        if cls == "typical":
            if re.search(r"\buse (the )?typical\b|\btypical is fine\b", low) \
                    and "not" not in low:
                issues.append("datasheet value-class error: a TYPICAL value "
                              "is NOT guaranteed; you cannot use it for "
                              "worst-case design. State that typical is "
                              "representative only and use the guaranteed "
                              "min/max.")
        # For guaranteed exercises, the correct answer uses MAX for
        # worst-case — flag "use typical" / "use min" for worst-case.
        if cls == "guaranteed":
            if re.search(r"\buse (the )?typical\b", low) and "not" not in low:
                issues.append("datasheet value-class error: for worst-case "
                              "design, use the GUARANTEED MAX, not the "
                              "typical. The typical is representative, not "
                              "guaranteed.")

    def _check_correlation_causation(self, exercise: Dict[str, Any],
                                      text: str, issues: List[str]) -> None:
        """Phase 4.2: for exercises that distinguish correlation from
        causation (fault_isolation transfer, symptom-vs-root-cause),
        the user must explicitly identify the distinction. Treating a
        symptom as the root cause is the classic error.

        Distinguishing means naming BOTH 'symptom' and 'root cause' (or
        a symptom/cause pair), OR explicitly negating ('X is a symptom,
        not the root cause'). Merely writing 'root cause' is NOT enough
        — the wrong answer 'the fan is the root cause' also contains it.
        """
        assumptions = (exercise.get("assumptions") or "").lower()
        prompt = (exercise.get("prompt") or "").lower()
        low = text.lower()
        # Detect correlation/causation exercises.
        cc_markers = ["correlation", "causation", "symptom", "root cause",
                       "root-cause", "swollen battery", "overheats and the fan"]
        is_cc = any(m in assumptions or m in prompt for m in cc_markers)
        if not is_cc:
            return
        # The answer must explicitly DISTINGUISH symptom from cause.
        # 'symptom' AND 'root cause'/'cause' both present, OR a 'not the
        # cause'/'a symptom, not' negation.
        has_symptom = "symptom" in low
        has_cause = "root cause" in low or "root-cause" in low or " the cause" in low
        has_negation = bool(re.search(
            r"\bnot\b.*\b(cause|root)\b|\bsymptom\b.*\bnot\b|"
            r"\bnot\b.*\bsymptom\b|\brather\b", low))
        distinguishes = (has_symptom and has_cause) or has_negation
        # Detect the classic error: asserting a known symptom IS the
        # root cause (without any symptom-word in the answer).
        asserts_symptom_as_cause = bool(re.search(
            r"\b(the battery|the fan|the (?:swollen|bloated))\b.*\b"
            r"(is|was)\b.*\b(root )?cause\b", low))
        if asserts_symptom_as_cause and not has_symptom:
            issues.append("correlation-vs-causation error: you treated a "
                          "symptom (swollen battery / fan at 100%) as the "
                          "root cause. State the distinction explicitly: "
                          "name it as a SYMPTOM and identify the actual "
                          "root cause that produced it.")
        elif not distinguishes:
            issues.append("correlation-vs-causation: this problem requires "
                          "distinguishing a symptom from the root cause. "
                          "State which is which explicitly (use both "
                          "'symptom' and 'root cause' in your answer).")

    # -- Feedback composition -------------------------------------------
    def _compose_feedback(self, answer_value, answer_unit, ref_value,
                          valid_range, answer_in_range, reasoning_score,
                          issues, correct) -> str:
        parts: List[str] = []
        if answer_value is None:
            parts.append("No numeric answer was found in your response.")
        else:
            if valid_range:
                lo = valid_range.get("min", valid_range.get("low"))
                hi = valid_range.get("max", valid_range.get("high"))
                parts.append(f"Your answer: {answer_value} {answer_unit}; "
                             f"valid range is {lo}–{hi}.")
            elif ref_value is not None:
                parts.append(f"Your answer: {answer_value} {answer_unit}; "
                             f"reference: {ref_value} "
                             f"({'within 5%' if answer_in_range else 'outside 5%'}).")
            if not answer_in_range:
                parts.append("The final value is outside the accepted range.")
        if issues:
            parts.append("Reasoning issues:")
            for i in issues:
                parts.append(f"  - {i}")
        else:
            parts.append("Reasoning: no issues detected by the rule checks.")
        if correct:
            parts.append("Result: correct (answer in range AND reasoning "
                         "passes the checks).")
        elif answer_in_range and not correct:
            parts.append("Result: the number is right but the reasoning does "
                         "not pass — this is NOT counted as solved. "
                         "(A correct answer for incorrect reasoning is not "
                         "rewarded.)")
        else:
            parts.append("Result: not yet correct. Review the issues above "
                         "and try again, or request a hint.")
        parts.append(f"Reasoning score: {reasoning_score:.2f} / 1.00.")
        return "\n".join(parts)
