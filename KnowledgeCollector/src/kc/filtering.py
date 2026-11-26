"""Relevance filtering.

Decides whether a parsed item is relevant to electronics repair. Only
items that pass the filter are sent to extraction/classification/store.

The filter is keyword-based with a whitelist (relevant topics) and a
blacklist (spam/unrelated). It also rejects very short or very long
texts (likely spam or copy-paste dumps).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

from .schemas import Rejection, RejectionReason
from . import config


# Whitelist of relevant keywords. An item is relevant if its title+text
# contains at least one of these (case-insensitive).
RELEVANT_KEYWORDS = [
    # motherboard / laptop / desktop
    "motherboard", "laptop", "notebook", "desktop", "mb", "mainboard",
    # power
    "power rail", "power sequence", "power sequencing", "power-good",
    "power good", "pgood", "pwr_good", "always-on", "standby rail",
    "valw", "vs", "3v", "5v", "1.2v", "1.5v", "1.8v", "0.6v", "12v", "19v",
    "dc-dc", "buck", "boost", "buck-boost", "converter", "regulator",
    "vrm", "vcore", "vccsa", "vram", "ddr", "ram voltage",
    # components
    "mosfet", "bjt", "igbt", "diode", "zener", "tvs", "capacitor", "inductor",
    "resistor", "fuse", "polyfuse", "charger ic", "pwm", "ec", "kbc",
    "super i/o", "bios", "uefi", "spi flash", "ec/kbc",
    # measurements / testing
    "multimeter", "oscilloscope", "scope", "measure", "measured",
    "resistance", "voltage", "current", "continuity", "diode mode",
    "short", "open", "leakage",
    # repair
    "repair", "fix", "fixed", "replaced", "rework", "solder", "desolder",
    "no power", "no post", "no display", "no boot", "no charge",
    "dead board", "won't power", "won't boot",
    # tools
    "hot air", "soldering iron", "flux", "solder wick",
]

# Blacklist of spam/unrelated markers. An item containing any of these
# is rejected even if it also contains a relevant keyword.
BLACKLIST_KEYWORDS = [
    # ads / commercial spam
    "buy now", "click here", "free shipping", "discount code",
    "affiliate link", "sponsored content",
    # off-topic software
    "windows update", "driver download", "antivirus",
    # unrelated
    "recipe", "cooking", "diet", "weight loss", "crypto", "bitcoin",
    "giveaway", "contest",
]

# BIOS dump trade / sharing markers.
BIOS_DUMP_MARKERS = [
    "bios dump", "bios bin", "bios rom", "uefi dump",
    "share the dump", "upload the dump", "dump file",
]


@dataclass
class FilterResult:
    relevant: bool
    reason: str = ""
    matched_keywords: List[str] = None
    blacklist_hits: List[str] = None

    def __post_init__(self):
        self.matched_keywords = self.matched_keywords or []
        self.blacklist_hits = self.blacklist_hits or []


def is_relevant(title: str, text: str) -> FilterResult:
    """Decide if an item is relevant. Returns a FilterResult with the
    matched keywords (for explanation) and blacklist hits."""
    combined = (title + " " + text).lower()
    # Length checks.
    if len(text.strip()) < 30:
        return FilterResult(relevant=False, reason="text too short")
    if len(text) > 50000:
        return FilterResult(relevant=False, reason="text too long (likely dump)")
    # Blacklist.
    bl_hits = [k for k in BLACKLIST_KEYWORDS if k in combined]
    if bl_hits:
        return FilterResult(relevant=False, reason=f"blacklist: {', '.join(bl_hits)}",
                            blacklist_hits=bl_hits)
    # BIOS dump markers -> reject.
    bd_hits = [k for k in BIOS_DUMP_MARKERS if k in combined]
    if bd_hits:
        return FilterResult(relevant=False,
                            reason=f"bios dump reference: {', '.join(bd_hits)}",
                            blacklist_hits=bd_hits)
    # Whitelist.
    matched = [k for k in RELEVANT_KEYWORDS if k in combined]
    if not matched:
        return FilterResult(relevant=False, reason="no relevant keyword")
    return FilterResult(relevant=True, matched_keywords=matched)


def reject_unrelevant(source_name: str, url: str, reason: str) -> Rejection:
    return Rejection(source_name=source_name, url=url,
                     reason=RejectionReason.RELEVANCE, detail=reason,
                     collected_at=config.now_iso())
