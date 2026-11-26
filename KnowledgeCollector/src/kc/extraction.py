"""Structured field extraction from repair-discussion text.

Extracts:
- component_model (e.g. TPS51716, ISL6248, AO4407)
- device_model (e.g. Lenovo G50-80, MacBook Pro A1278)
- motherboard_model (e.g. NM-A111, DAOZ9AMB6E0)
- reported_symptoms (e.g. "no power", "no POST")
- measurements (raw strings like "+5VALW = 0V", "8 ohm to ground")
- proposed_tests (e.g. "measure resistance on +5VALW")
- repair_actions (e.g. "replaced C45")
- reported_outcome ("fixed"/"not fixed"/"")
- image_refs (image URLs/filenames)

The extractor NEVER fabricates. If a field can't be found, it's an
empty list. Every extracted value is a verbatim substring of the text
so it can be traced back to the source.
"""
from __future__ import annotations

import re
from typing import List

from .schemas import StructuredFields


# Component model number patterns. Component ICs are usually 3-7 uppercase
# letters + digits + optional suffix.
_COMPONENT_RE = re.compile(
    r"\b([A-Z]{2,7}\d{2,5}[A-Z0-9]*)\b"
)
# Known manufacturer prefixes to reduce false positives.
_KNOWN_PREFIXES = {
    "TPS", "ISL", "RT", "AO", "IRF", "STP", "FD", "SI", "FDN", "BSS",
    "IRL", "IRLZ", "2N", "BC", "BD", "BAV", "1N", "BAT", "LM", "LTC",
    "MAX", "LT", "AD", "OP", "TLC", "CD", "74", "40", "MMBT", "MMBTA",
    "PAM", "SY", "HX", "RT", "MP", "SC", "TDA", "TIP",
}

# Device model: "Lenovo G50-80", "ThinkPad T420", "MacBook Pro A1278",
# "Dell Latitude E6420", "HP EliteBook 840 G3", "Asus X540".
_DEVICE_RE = re.compile(
    r"\b((?:Lenovo|ThinkPad|MacBook|Dell|Latitude|HP|EliteBook|Asus|Acer|"
    r"Samsung|Toshiba|Sony|Vaio|MSI|Gigabyte|Apple)\s+[A-Z0-9][A-Z0-9\- ]{0,30})\b",
    re.IGNORECASE
)
# Motherboard model: NM-A111, DAOZ9AMB6E0, W7721, 54520-1, 48.4F701.021.
_BOARD_RE = re.compile(
    r"\b(NM-[A-Z0-9]{3,6}|[A-Z]{2,4}[0-9]{2,5}[A-Z0-9]{2,8}|"
    r"\d{2,4}\.\d{2,4}[A-Z0-9.]*|\d{5}-\d{1,3}|W\d{4,5})\b"
)

# Symptom phrases.
_SYMPTOMS = [
    "no power", "no post", "no display", "no boot", "no charge",
    "won't power", "won't boot", "won't charge", "dead board",
    "no lights", "no fan", "random shutdown", "shuts down",
    "overheating", "short circuit", "blown fuse", "no video",
]

# Measurement patterns: "<rail> = <value><unit>", "<value> <unit> to ground".
_MEASUREMENT_RE = re.compile(
    r"((?:\+?[\d.]+V(?:ALW|VS)?|[A-Z]+\w*|VIN|VCORE|VCC|VDD|VPP|3V|5V|12V|19V)"
    r"\s*=?\s*[-+]?\d+(?:\.\d+)?\s*(?:m?V|m?A|m?ohm|kohm|ohm|Ω|Hz|kHz|MHz|W)\b"
    r"(?:\s*(?:to ground|to gnd|measured))?)",
    re.IGNORECASE,
)
# Bare measurement: "<number> <unit> to ground".
_RES_TO_GROUND_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:m?ohm|kohm|ohm|Ω)\s*to ground)",
    re.IGNORECASE,
)

# Proposed tests.
_TEST_PATTERNS = [
    "try measuring", "try to measure", "check with", "measure with",
    "test with", "use a multimeter", "check the", "measure the",
    "verify the", "check if",
]

# Repair actions.
_REPAIR_RE = re.compile(
    r"\b(replaced?\s+(?:the\s+)?[A-Z0-9][\w\s,/.-]{1,60}"
    r"|swapped?\s+[A-Z0-9][\w\s,/.-]{1,60}"
    r"|changed?\s+(?:the\s+)?[A-Z0-9][\w\s,/.-]{1,60}"
    r"|rework(?:ed)?\s+[A-Z0-9][\w\s,/.-]{1,60}"
    r"|desolder(?:ed)?\s+[A-Z0-9][\w\s,/.-]{1,60}"
    r"|reflow(?:ed)?\s+[A-Z0-9][\w\s,/.-]{1,60})",
    re.IGNORECASE,
)

# Outcome phrases.
_OUTCOME_FIXED = [
    "fixed", "working now", "back to life", "powers on", "powered on",
    "boots now", "fixed it", "solved", "works", "running again",
]
_OUTCOME_FAILED = [
    "still dead", "still no power", "didn't fix", "did not fix",
    "no change", "still broken",
]

# Image refs.
_IMG_RE = re.compile(r"(https?://\S+\.(?:png|jpg|jpeg|gif|bmp)|"
                     r"/(?:media|uploads?|attachments?)/\S+|"
                     r"!\[[^\]]*\]\([^)]+\))", re.IGNORECASE)


def extract(text: str, title: str = "") -> StructuredFields:
    """Extract structured fields from a post's text + title."""
    combined = f"{title}\n{text}" if title else text
    sf = StructuredFields()
    # Components.
    seen = set()
    for m in _COMPONENT_RE.finditer(combined):
        token = m.group(1)
        prefix = "".join(c for c in token if c.isalpha())[:5]
        if prefix in _KNOWN_PREFIXES and token not in seen:
            sf.component_model.append(token)
            seen.add(token)
    # Device model.
    for m in _DEVICE_RE.finditer(combined):
        name = m.group(1).strip()
        if name not in sf.device_model:
            sf.device_model.append(name)
    # Motherboard model.
    for m in _BOARD_RE.finditer(combined):
        bm = m.group(1)
        if bm not in sf.motherboard_model:
            sf.motherboard_model.append(bm)
    # Symptoms.
    low = combined.lower()
    for s in _SYMPTOMS:
        if s in low and s not in sf.reported_symptoms:
            sf.reported_symptoms.append(s)
    # Measurements.
    seen_meas = set()
    for m in _MEASUREMENT_RE.finditer(combined):
        s = m.group(1).strip()
        if s and s not in seen_meas:
            sf.measurements.append(s)
            seen_meas.add(s)
    for m in _RES_TO_GROUND_RE.finditer(combined):
        s = m.group(1).strip()
        if s and s not in seen_meas:
            sf.measurements.append(s)
            seen_meas.add(s)
    # Proposed tests.
    low = combined.lower()
    for p in _TEST_PATTERNS:
        idx = low.find(p)
        while idx >= 0:
            # Grab the sentence containing this pattern.
            end = combined.find(".", idx)
            if end < 0:
                end = min(idx + 80, len(combined))
            sent = combined[idx:end].strip()
            if sent and sent not in sf.proposed_tests:
                sf.proposed_tests.append(sent[:120])
            idx = low.find(p, idx + 1)
    # Repair actions.
    for m in _REPAIR_RE.finditer(combined):
        action = m.group(1).strip()
        if action and action not in sf.repair_actions:
            sf.repair_actions.append(action[:120])
    # Outcome.
    if any(p in low for p in _OUTCOME_FIXED):
        sf.reported_outcome = "fixed"
    elif any(p in low for p in _OUTCOME_FAILED):
        sf.reported_outcome = "not fixed"
    # Image refs.
    for m in _IMG_RE.finditer(combined):
        ref = m.group(1)
        if ref not in sf.image_refs:
            sf.image_refs.append(ref)
    return sf
