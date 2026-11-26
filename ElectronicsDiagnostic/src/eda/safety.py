"""Safety rules for the diagnostic engine.

The assistant must not recommend unsafe actions. These rules are
checked before any next-test or hypothesis is emitted and are also
applied as a hard filter on LLM-suggested next steps.
"""
from __future__ import annotations

import re
from typing import List, Tuple

# Phrases that must never appear in a recommendation.
FORBIDDEN_ACTIONS = [
    (r"short[\s\w]{0,12}(battery|cell|terminal)", "shorting battery terminals"),
    (r"bypass[\s\w]{0,12}(battery|protection|ocp|ovp|scp)", "bypassing battery or protection"),
    (r"disable[\s\w]{0,12}(overcurrent|overvoltage|over-?current|over-?voltage|protection)", "disabling protection"),
    (r"inject[\s\w]{0,12}(uncontrolled|unlimited|direct)", "uncontrolled power injection"),
    (r"connect[\s\w]{0,12}(incompatible|wrong|higher)", "incompatible power supply"),
    (r"force[\s\w]{0,12}charge", "forced charging"),
    (r"screwdriver[\s\w]{0,12}discharg", "screwdriver discharge of capacitors"),
]

# Phrases that, if a recommendation contains them, require an explicit warning.
WARN_ACTIONS = [
    (r"\blive\s+(probe|measure|voltage)", "live measurement"),
    (r"\bon[\s-]+adapter\b", "measuring on adapter power"),
    (r"\bhot[\s-]+air\b", "hot-air rework"),
]


def check_action(text: str) -> Tuple[bool, List[str], List[str]]:
    """Return (allowed, forbidden_reasons, warning_reasons)."""
    low = text.lower()
    forbidden = []
    for pat, label in FORBIDDEN_ACTIONS:
        if re.search(pat, low):
            forbidden.append(label)
    warnings = []
    for pat, label in WARN_ACTIONS:
        if re.search(pat, low):
            warnings.append(label)
    allowed = not forbidden
    return allowed, forbidden, warnings


def safety_banner() -> str:
    return (
        "SAFETY: Resistance measurements must be made with power disconnected "
        "and bulk capacitors safely discharged. Live measurements require an "
        "explicit ground reference and caution near high-current paths. "
        "Never short battery terminals, bypass protection, or inject "
        "uncontrolled current."
    )


def required_caution_for(test_text: str) -> str:
    """Return the caution string that must precede a given test."""
    _, _, warnings = check_action(test_text)
    if "live measurement" in warnings or "measuring on adapter power" in warnings:
        return (
            "CAUTION: This is a live measurement. Power the board only while "
            "you measure. Keep one hand away from the board. Use the stated "
            "ground reference (e.g. a known GND pad or the DC jack negative "
            "pin). Do not let probe tips bridge adjacent pins."
        )
    return ""
