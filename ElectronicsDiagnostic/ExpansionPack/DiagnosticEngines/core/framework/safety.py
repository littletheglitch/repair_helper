"""Safety policy — the framework's hard guardrails.

Safety rules live IN the engines, not just in documentation. The
SafetyPolicy is consulted by every NextTestSelector and every engine
before recommending a measurement. A measurement that violates the
policy is blocked and a SafetyViolation is recorded.

The policy is deliberately conservative. Mains-side probing, bypassing
protection, uncontrolled current injection, and touching charged
primary-side capacitors are all blocked at the framework level.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class SafetyLevel(str, Enum):
    INFO = "info"
    CAUTION = "caution"
    WARNING = "warning"
    BLOCK = "block"          # the action must NOT be performed


@dataclass
class SafetyViolation:
    """A recorded safety issue. BLOCK-level violations stop the workflow."""
    violation_id: str = field(default_factory=lambda: f"SV_{uuid.uuid4().hex[:10]}")
    level: SafetyLevel = SafetyLevel.WARNING
    rule: str = ""                  # which safety rule fired
    message: str = ""
    context: str = ""               # what action triggered it
    created_at_utc: str = field(default_factory=lambda: "")

    def __post_init__(self):
        if isinstance(self.level, str):
            self.level = SafetyLevel(self.level)
        from datetime import datetime, timezone
        if not self.created_at_utc:
            self.created_at_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not self.rule:
            raise ValueError("SafetyViolation requires a rule id")
        if not self.message:
            raise ValueError("SafetyViolation requires a message")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["level"] = self.level.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SafetyViolation":
        d = dict(d)
        if isinstance(d.get("level"), str):
            d["level"] = SafetyLevel(d["level"])
        return cls(**d)


# Hard-coded safety rules. Each rule is a (id, predicate, level, message)
# tuple. The predicate takes a proposed action dict and returns True if
# the rule applies.

def _mentions(action: dict, *keywords) -> bool:
    """Word-boundary match against the action's text fields. Avoids
    false positives like 'unpowered' matching 'powered'."""
    import re
    text = " ".join(str(action.get(k, "")) for k in ("what", "where", "instrument", "action", "safety")).lower()
    for kw in keywords:
        kw_low = kw.lower()
        # Use word boundaries so 'powered' does not match inside 'unpowered'.
        # But allow multi-word keywords to match as substrings.
        if " " in kw_low:
            if kw_low in text:
                return True
        else:
            if re.search(r"\b" + re.escape(kw_low) + r"\b", text):
                return True
    return False


def _is_mains(action: dict) -> bool:
    return _mentions(action, "mains", "primary side", "line voltage", "ac input", "220v", "110v", "live")


def _is_charged_cap(action: dict) -> bool:
    # Match phrases that indicate a charged primary-side bulk capacitor.
    return _mentions(action, "primary cap", "bulk cap primary", "charged capacitor",
                    "main filter cap", "primary bulk cap")


def _is_bypass(action: dict) -> bool:
    return _mentions(action, "bypass", "remove protection", "disable ocp", "disable ovp", "short the fuse")


def _is_uncontrolled_injection(action: dict) -> bool:
    return _mentions(action, "inject current", "current injection unlimited", "apply full adapter", "direct short")


SAFETY_RULES = [
    ("SAFETY_001_NO_MAINS_PROBE",
     _is_mains,
     SafetyLevel.BLOCK,
     "Mains / primary-side probing is blocked by the safety policy. "
     "Primary-side measurements require an isolated current-limited "
     "setup, proper PPE, and a trained technician. The diagnostic "
     "engine will not recommend primary-side probing."),

    ("SAFETY_002_NO_CHARGED_CAP_TOUCH",
     _is_charged_cap,
     SafetyLevel.BLOCK,
     "Touching charged primary-side bulk capacitors is blocked. "
     "Discharge through a proper bleeder resistor before any work."),

    ("SAFETY_003_NO_PROTECTION_BYPASS",
     _is_bypass,
     SafetyLevel.BLOCK,
     "Bypassing protection circuits (OCP/OVP/fuse) is blocked. "
     "Protection exists to prevent fire and injury; defeating it "
     "is not an accepted diagnostic step."),

    ("SAFETY_004_NO_UNCONTROLLED_INJECTION",
     _is_uncontrolled_injection,
     SafetyLevel.BLOCK,
     "Uncontrolled current injection is blocked. Any current source "
     "used for short-hunting must be current-limited to a value the "
     "trace can carry without damage."),

    ("SAFETY_005_POWER_BEFORE_RESISTANCE",
     lambda a: _mentions(a, "measure resistance") and _mentions(a, "powered"),
     SafetyLevel.BLOCK,
     "Measuring resistance on a powered rail is blocked. Resistance "
     "mode drives a small current; on a powered rail this can damage "
     "the DMM and give a meaningless reading. Disconnect power first."),

    ("SAFETY_006_DISCHARGE_BEFORE_RESISTANCE",
     lambda a: _mentions(a, "measure resistance") and not _mentions(a, "discharg"),
     SafetyLevel.CAUTION,
     "Verify all bulk and ceramic capacitors are discharged before "
     "measuring resistance. Charged caps can damage the DMM and give "
     "false readings."),
]


class SafetyPolicy:
    """Consulted before any next-test recommendation."""

    def evaluate(self, proposed_action: dict) -> list[SafetyViolation]:
        """Return all violations the proposed action triggers. A
        BLOCK-level violation means the action must NOT be performed."""
        out: list[SafetyViolation] = []
        for rule_id, predicate, level, message in SAFETY_RULES:
            try:
                if predicate(proposed_action or {}):
                    out.append(SafetyViolation(
                        level=level, rule=rule_id, message=message,
                        context=str(proposed_action.get("what", ""))[:200],
                    ))
            except Exception:  # noqa: BLE001
                # A broken predicate must never block diagnosis silently;
                # record a CAUTION and continue.
                out.append(SafetyViolation(
                    level=SafetyLevel.CAUTION,
                    rule="SAFETY_000_EVALUATOR_ERROR",
                    message=f"Safety rule {rule_id} raised an exception.",
                    context=str(proposed_action)[:200],
                ))
        return out

    def is_blocked(self, proposed_action: dict) -> bool:
        return any(v.level == SafetyLevel.BLOCK
                   for v in self.evaluate(proposed_action))

    def all_rules(self) -> list[dict]:
        return [{"rule_id": rid, "level": lvl.value, "message": msg}
                for rid, _, lvl, msg in SAFETY_RULES]
