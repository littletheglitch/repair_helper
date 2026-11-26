"""Classification — assign each record to one of A–F.

The classifier is rule-based and transparent. Each rule has a name and
matches a set of evidence phrases in the text. Multiple rules may
match; the highest-authority class wins (A > C > E > D > B > F) and the
explanation lists every matched rule.

Importantly:
- A record is class A (technician-confirmed repair) ONLY when there is
  an explicit success phrase AND a confirming test/measure phrase. A
  bare "I replaced X" is class B (reported repair, insufficient
  verification), never A.
- A class-B record is stored as a reported outcome, NOT as laboratory-
  verified evidence. The explanation makes this clear.
- A class-D (hypothesis) is never promoted to a higher class by the
  classifier.
"""
from __future__ import annotations

from typing import List

from .schemas import Classification, RecordClass, CLASS_AUTHORITY


# Rule definitions: (rule_name, class, list of trigger phrases).
RULES = [
    # A: confirmed repair — success + confirming test
    ("confirmed_outcome", RecordClass.A_CONFIRMED_REPAIR, [
        "fixed", "working now", "back to life", "powers on", "powered on",
        "boots now", "solved", "works", "running again", "back up",
    ]),
    # The "confirming test" requirement is checked separately below.
    # C: engineering explanation
    ("engineering_explanation", RecordClass.C_ENGINEERING, [
        "datasheet", "application note", "app note", "according to",
        "because", "due to", "the regulator", "the converter",
        "the controller", "the inductor", "the capacitor stores",
        "kvl", "kcl", "ohm's law",
    ]),
    # E: suggested test
    ("suggested_test", RecordClass.E_SUGGESTED_TEST, [
        "try measuring", "try to measure", "check with", "measure with",
        "test with", "use a multimeter", "verify the", "check if",
        "i would measure", "you should measure",
    ]),
    # D: diagnostic hypothesis
    ("diagnostic_hypothesis", RecordClass.D_HYPOTHESIS, [
        "could be", "might be", "may be", "suspect", "likely",
        "probably", "i think", "i believe", "guess",
    ]),
    # B: reported repair (replacement claimed)
    ("reported_repair", RecordClass.B_REPORTED_REPAIR, [
        "replaced", "swapped", "changed", "rework", "reflow", "desolder",
        "removed", "installed a new",
    ]),
    # F: incomplete (matched by the absence of the above + short text)
]

# Phrases that count as a "confirming test" for class A.
CONFIRMING_TEST_PHRASES = [
    "measured", "measuring", "i measured", "checked", "i checked",
    "verified", "i verified", "after the fix", "confirm", "confirmed",
    "tested", "i tested",
]


def classify(text: str, structured=None) -> Classification:
    """Classify a record. ``structured`` is the StructuredFields (used to
    enrich the explanation; e.g. if structured.repair_actions is non-empty
    that's evidence for B)."""
    low = text.lower()
    matched_rules: List[str] = []
    matched_evidence: List[str] = []
    classes_seen: dict = {}  # class -> rule_name

    for rule_name, cls, phrases in RULES:
        hits = [p for p in phrases if p in low]
        if hits:
            matched_rules.append(rule_name)
            matched_evidence.append(f"{rule_name}={hits[:3]}")
            classes_seen[cls] = rule_name

    # Class A requires BOTH a success phrase AND a confirming test.
    if RecordClass.A_CONFIRMED_REPAIR in classes_seen:
        confirming = [p for p in CONFIRMING_TEST_PHRASES if p in low]
        if not confirming:
            # Downgrade A to B: success claimed but no confirming test.
            del classes_seen[RecordClass.A_CONFIRMED_REPAIR]
            if RecordClass.B_REPORTED_REPAIR not in classes_seen:
                classes_seen[RecordClass.B_REPORTED_REPAIR] = "reported_repair"
            matched_rules.append("downgraded_A_to_B:no_confirming_test")
            matched_evidence.append("downgraded_A_to_B:no_confirming_test=[]")

    # If no rule matched, it's F (incomplete/uncertain).
    if not classes_seen:
        cls = RecordClass.F_INCOMPLETE
        matched_rules.append("incomplete:no_rules_matched")
        matched_evidence.append("incomplete:no_rules_matched")
    else:
        # Pick the highest-authority class that matched.
        cls = min(classes_seen.keys(), key=lambda c: CLASS_AUTHORITY[c])

    # Enrich explanation with structured fields.
    extras = []
    if structured is not None:
        if structured.repair_actions:
            extras.append(f"repair_actions={len(structured.repair_actions)}")
        if structured.measurements:
            extras.append(f"measurements={len(structured.measurements)}")
        if structured.reported_outcome:
            extras.append(f"outcome={structured.reported_outcome}")

    explanation_parts = matched_evidence + extras
    explanation = "; ".join(explanation_parts)
    return Classification(cls=cls, explanation=explanation,
                          matched_rules=matched_rules)
