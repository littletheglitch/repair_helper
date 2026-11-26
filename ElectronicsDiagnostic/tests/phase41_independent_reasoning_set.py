"""Phase 4.1 — INDEPENDENT reasoning evaluation test set.

51 free-text responses to a FIXED exercise, designed and written
separately from the evaluator's development/tuning set. The response
strings are PURE technician answers — no parenthetical meta-comments,
because those comments would leak keywords into the evaluated text.
Categories (the spec's section 1):

  A. correct + correct reasoning         -> expect correct=True
  B. correct number + WRONG reasoning     -> expect correct=False
  C. correct number + incomplete reasoning-> expect correct=False
  D. hidden assumptions (no source)      -> expect correct=False
  E. keyword-right but logic-wrong       -> expect correct=False
  F. wrong number + good reasoning        -> expect correct=False
  G. measurement misinterpretation        -> expect correct=False
  H. wrong units                          -> expect correct=False
  I. no reasoning (bare number)           -> expect correct=False
  J. correct, varied phrasings            -> expect correct=True
  K. correct, edge phrasings              -> expect True/False (per case)

The fixed exercise: V=5 V, I=2 A, R=2.5 Ω (Ohm's law). This exercise
is NOT one of the generator's deterministic seeds; the (5,2) pair is
constructed by hand for the test set, so the evaluator was never tuned
on its exact prompt.
"""
from __future__ import annotations

FIXED_EXERCISE = {
    "id": 0,
    "exercise_key": "independent_test_fixed_ohm",
    "skill_key": "ohm_kirchhoff",
    "difficulty": 2,
    "source_kind": "engine",
    "source_ref": "electrical_math.ohm_resistance",
    "prompt": "A rail measures 5 V and the load current is 2 A. What is "
              "the effective load resistance?",
    "reference_solution": "2.5",
    "valid_range": None,
    "unit": "Ω",
    "formulas": "R = V / I",
    "assumptions": "purely resistive load; steady state",
    "solution_hidden": True,
}

# Each tuple: (category, response_text, expected_correct).
# The response_text is the PURE technician answer; meta-commentary is
# kept OUT of the string so it cannot leak keywords.
CASES = [
    # ---- A. correct + correct reasoning (expect True) ----
    ("A", "Using Ohm's law R = V/I, with V=5 V and I=2 A, so R = 5/2 = 2.5 Ω. Therefore the load resistance is 2.5 Ω.", True),
    ("A", "By Ohm's law, R = V over I. V is 5 V, I is 2 A, so R = 5/2 = 2.5 Ω. Therefore 2.5 Ω.", True),
    ("A", "R = V/I = 5/2 = 2.5 Ω. So the effective load is 2.5 Ω because Ohm's law relates V, I, R.", True),
    ("A", "Ohm: R=V/I. Substituting 5V and 2A gives 2.5 Ω. Therefore the load is 2.5 Ω.", True),
    ("A", "The load resistance is R = V/I = 5V / 2A = 2.5 Ω. Therefore 2.5 Ω.", True),
    ("A", "Since V=IR, R=V/I=5/2=2.5 Ω. Therefore the answer is 2.5 Ω.", True),

    # ---- B. correct number + WRONG reasoning (expect False) ----
    ("B", "Using R = V times I, with V=5 and I=2, R = 10. But the answer is 2.5 Ω. Therefore 2.5 Ω.", False),
    ("B", "I guessed 2.5 Ω because it feels right. Therefore 2.5 Ω.", False),
    ("B", "The resistance is 2.5 Ω. I remember this value from a similar problem. Therefore 2.5 Ω.", False),
    ("B", "5 divided by 2 is 2.5, so 2.5 Ω.", False),

    # ---- C. correct number + INCOMPLETE reasoning (expect False) ----
    ("C", "2.5", False),
    ("C", "R = 2.5 Ω.", False),
    ("C", "Answer: 2.5.", False),
    ("C", "It is 2.5 because 5 and 2.", False),

    # ---- D. hidden assumptions (no source) (expect False) ----
    ("D", "Assume the rail is exactly 5.0V. R = V/I = 5/2 = 2.5 Ω. Therefore 2.5 Ω.", False),
    ("D", "Let R = 5. R = V/I = 5/2 = 2.5 Ω. Therefore 2.5 Ω.", False),
    ("D", "Assume the load is purely resistive. R = V/I = 5/2 = 2.5 Ω. Therefore 2.5 Ω.", False),

    # ---- E. keyword-right but logic-wrong (expect False) ----
    ("E", "Using Ohm's law, the resistance is V plus I = 5 plus 2 = 7 Ω. Therefore 7 Ω.", False),
    ("E", "By Kirchhoff, R = V minus I = 5 minus 2 = 3 Ω. Therefore 3 Ω.", False),
    ("E", "Ohm's law says R = V times I, so R = 5 times 2 = 10 Ω. Therefore 10 Ω.", False),
    ("E", "The voltage divider gives Vout = Vin times R2 over R1 plus R2 = 2.5 Ω. Therefore 2.5 Ω.", False),

    # ---- F. wrong number + good reasoning (expect False) ----
    ("F", "Using Ohm's law R = V/I, with V=5 V and I=2 A, so R = 5 times 2 = 10 Ω. Therefore 10 Ω.", False),
    ("F", "R = V/I = 5/3 = 1.67 Ω. Therefore 1.67 Ω.", False),
    ("F", "Ohm's law: R = V/I = 12/2 = 6 Ω. Therefore 6 Ω.", False),

    # ---- G. measurement misinterpretation (expect False) ----
    ("G", "The 0 V reading means no voltage present, so R = V/I = 0/2 = 0 Ω. Therefore 0 Ω.", False),
    ("G", "0V means the rail is dead. R = 0/2 = 0 Ω. Therefore 0 Ω.", False),
    ("G", "The reading is 0V so there is no power. R = 0/2 = 0 Ω.", False),

    # ---- H. wrong units (expect False) ----
    ("H", "Using Ohm's law R = V/I, with V=5 V and I=2 A, so R = 5/2 = 2.5 V. Therefore 2.5 V.", False),
    ("H", "R = V/I = 5/2 = 2.5 A. Therefore 2.5 A.", False),
    ("H", "R = V/I = 5/2 = 2.5 H. Therefore 2.5 H.", False),
    ("H", "R = V/I = 5/2 = 2.5 W. Therefore 2.5 W.", False),

    # ---- I. no reasoning (bare number) (expect False) ----
    ("I", "2.5", False),
    ("I", "2.5 Ω", False),
    ("I", "answer 2.5", False),
    ("I", "2.5 ohms", False),

    # ---- J. correct, varied phrasings (expect True) ----
    ("J", "Ohm's law gives R = V/I. With 5V across and 2A through, R = 5/2 = 2.5 Ω. Therefore the load is 2.5 Ω.", True),
    ("J", "V=IR so R=V/I. The rail is 5V, current 2A, so R = 2.5 Ω. Therefore 2.5 Ω.", True),
    ("J", "Applying Ohm's law: R = V/I = 5/2 = 2.5 Ω. Therefore the effective load resistance is 2.5 Ω.", True),
    ("J", "R = V/I = 5 V / 2 A = 2.5 Ω. Therefore 2.5 Ω.", True),
    ("J", "Since R=V/I and V=5, I=2, R=2.5 Ω. Therefore 2.5 Ω.", True),

    # ---- K. correct, edge phrasings (expect True) ----
    ("K", "Ohm: R=V/I. Substituting 5V and 2A gives 2.5 Ω. Therefore 2.5 Ω.", True),
    ("K", "R = V/I = 5/2 = 2.5 Ω. Therefore 2.5 Ω.", True),
    ("K", "By the formula R = V/I, substituting 5V and 2A, R = 2.5 Ω. Therefore 2.5 Ω.", True),
    ("K", "V/I = 5/2 = 2.5 Ω. Therefore the load is 2.5 Ω.", True),
    ("K", "Assume steady state. R = V/I = 5/2 = 2.5 Ω. Therefore 2.5 Ω.", True),

    # ---- L. more wrong-reasoning traps (expect False) ----
    ("L", "The resistance is 2.5 Ω because 5 minus 2 is 3 and 3 minus 0.5 is 2.5. Therefore 2.5 Ω.", False),
    ("L", "R = 2.5 Ω. The current is 2A so the voltage must be 5V. Therefore 2.5 Ω.", False),
    ("L", "I think it is 2.5 because the rail is 5 and you divide by something. Therefore 2.5 Ω.", False),

    # ---- M. correct, alternative-law reference (Kirchhoff + Ohm) ----
    ("M", "By KVL around the loop and Ohm's law R=V/I, with V=5V and I=2A, R = 2.5 Ω. Therefore 2.5 Ω.", True),

    # ---- N. extra edge cases (mixed) ----
    ("N", "Using Ohm's law, R = V/I = 5/2 = 2.5 Ω. The current 2A is from the measurement. Therefore 2.5 Ω.", True),
    ("N", "R = V/I = 5/2 = 2.5. Units: ohms. Therefore 2.5 Ω.", True),
    ("N", "The load draws 2A at 5V so by Ohm R = V/I = 5/2 = 2.5 Ω. Therefore 2.5 Ω.", True),
    ("N", "5V over 2A equals 2.5 Ω by Ohm's law. Therefore 2.5 Ω.", True),
]

# 52 cases across categories A-M, independent of the dev set.
