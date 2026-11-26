"""Evidence-based diagnostic reasoning engine.

The engine is deliberately a deterministic rule-based system that
mirrors the way an experienced technician thinks. The local LLM is
used only to paraphrase and explain; it cannot invent measurements,
pin numbers or component values that the rule engine has not derived
from evidence.

For every ``analyze`` call, the engine:

1. Reads the session's current measurements, symptoms, and topology.
2. Ranks fault hypotheses against the evidence using a transparent
   scoring function.
3. Lists the evidence supporting and contradicting each hypothesis.
4. Lists missing evidence that, if obtained, would move the ranking.
5. Recommends the single most informative next measurement or
   inspection, with where to measure, the reference point, and the
   rationale. Expected values are given only when supported by a
   reliable rule, otherwise stated as "unknown without datasheet".
6. Requests a document when it is genuinely needed.

The engine is the source of truth: it never declares a component
defective on a single measurement, and it never asks for every
document up front.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import database
from . import measurements as M
from . import retrieval
from . import safety
from . import document_requests


# ---- Data structures ----------------------------------------------------

@dataclass
class Hypothesis:
    fault: str
    rationale: str = ""
    supporting: List[str] = field(default_factory=list)
    contradicting: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class NextTest:
    test: str
    where_to_measure: str
    reference_point: str
    expected: str
    rationale: str
    priority: int
    caution: str = ""
    needs_document: Optional[document_requests.DocumentRequest] = None


@dataclass
class Analysis:
    hypotheses: List[Hypothesis] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    next_test: Optional[NextTest] = None
    safety_banner: str = ""
    context_warnings: List[str] = field(default_factory=list)
    confirmed_diagnosis: Optional[str] = None
    llm_used: bool = False

    def to_text(self) -> str:
        out = []
        if self.safety_banner:
            out.append(self.safety_banner)
            out.append("")
        if self.context_warnings:
            out.append("Context gaps:")
            for w in self.context_warnings:
                out.append(f"  - {w}")
            out.append("")
        out.append("Ranked hypotheses:")
        if not self.hypotheses:
            out.append("  (none yet; not enough evidence)")
        else:
            for i, h in enumerate(self.hypotheses, 1):
                out.append(
                    f"  {i}. {h.fault}  [score {h.score:.2f}]"
                )
                out.append(f"     Why plausible: {h.rationale}")
                if h.supporting:
                    out.append("     Supporting:")
                    for s in h.supporting:
                        out.append(f"       + {s}")
                if h.contradicting:
                    out.append("     Contradicting:")
                    for c in h.contradicting:
                        out.append(f"       - {c}")
                if h.missing:
                    out.append("     Missing:")
                    for m in h.missing:
                        out.append(f"       ? {m}")
        if self.missing_evidence:
            out.append("")
            out.append("Missing evidence (any of these would change the ranking):")
            for m in self.missing_evidence:
                out.append(f"  - {m}")
        if self.next_test:
            out.append("")
            out.append("Next most informative test:")
            nt = self.next_test
            out.append(f"  Test: {nt.test}")
            out.append(f"  Where: {nt.where_to_measure}")
            out.append(f"  Reference: {nt.reference_point}")
            out.append(f"  Expected: {nt.expected}")
            out.append(f"  Why: {nt.rationale}")
            if nt.caution:
                out.append(f"  {nt.caution}")
            if nt.needs_document:
                out.append("")
                out.append(nt.needs_document.render_prompt())
        if self.confirmed_diagnosis:
            out.append("")
            out.append("CONFIRMED diagnosis: " + self.confirmed_diagnosis)
        return "\n".join(out)


# ---- Rule helpers -------------------------------------------------------

# Normalize rail names. Case-insensitive; strip non-alphanumerics.
def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


# Rail-name categories. These are topology rules: rail names like
# +3VALW / +5VALW / VIN indicate the always-on stage; names like +3VS / +5VS
# are run rails derived from the always-on rails.
ALWAYS_ON_NAMES = {"3valw", "5valw", "3valw", "3valw", "5valw", "3v_alw", "5v_alw",
                   "3p3valw", "valw", "3vsus", "5vsus"}
# Common adapter/input rail names.
INPUT_NAMES = {"vin", "dcin", "ac", "acoff", "acok", "pac", "psid", "acin",
               "vin1", "vdc", "19v"}
RUN_RAIL_NAMES = {"3vs", "5vs", "1p05v", "1p5v", "1p2v", "1p8v", "vcore",
                  "vccsa", "cpu", "1p05", "1p35", "ram", "ddr", "vtt"}


def _value_and_unit(m: M.Measurement) -> Tuple[Optional[float], str]:
    return m.value, m.unit or ""


def _is_zero_volt(m: M.Measurement) -> bool:
    if m.value is None:
        return False
    if m.unit and m.unit.lower() in ("mv",):
        return abs(m.value) < 1.0
    return abs(m.value) < 0.05


def _is_present_volt(m: M.Measurement, lo: float = 0.5, hi: float = 25.0) -> bool:
    if m.value is None:
        return False
    v = m.value if (m.unit or "").lower() != "mv" else m.value / 1000.0
    return lo <= v <= hi


def _v_in_volts(m: M.Measurement) -> Optional[float]:
    if m.value is None:
        return None
    u = (m.unit or "").lower()
    if u == "mv":
        return m.value / 1000.0
    return m.value


def _looks_like(name: str, category: set) -> bool:
    n = _norm(name)
    for c in category:
        if c in n:
            return True
    return False


# ---- The rule engine ----------------------------------------------------

def analyze(session_id: int, symptoms: List[str],
            measurements: List[M.Measurement]) -> Analysis:
    """Run the evidence-based analysis for one session."""
    analysis = Analysis()
    analysis.safety_banner = safety.safety_banner()

    # Context warnings from the measurement manager.
    analysis.context_warnings = M.identify_missing_context(session_id)

    # Pull relevant knowledge facts that could constrain interpretation.
    # We search the knowledge base for the most informative symptom keyword.
    kb_hits: List[Dict] = []
    if symptoms:
        kw = symptoms[0]
        kb_hits = retrieval.search_knowledge(kw, limit=10)

    hyps = _build_hypotheses(symptoms, measurements, kb_hits)
    analysis.hypotheses = sorted(hyps, key=lambda h: -h.score)

    analysis.missing_evidence = _collect_missing(analysis.hypotheses)
    nt = _choose_next_test(symptoms, measurements, analysis.hypotheses)
    analysis.next_test = nt

    # Persist the analysis into the session tables.
    _persist(session_id, analysis)

    # A confirmed diagnosis requires multiple independent supporting
    # measurements and zero high-score contradicting hypotheses.
    confirmed = _confirmable(analysis.hypotheses, measurements)
    analysis.confirmed_diagnosis = confirmed
    return analysis


def _build_hypotheses(symptoms: List[str],
                       measurements: List[M.Measurement],
                       kb_hits: List[Dict]) -> List[Hypothesis]:
    hyps: List[Hypothesis] = []
    s_text = " ".join(symptoms).lower()
    no_power = any(k in s_text for k in ("no power", "no power.", "dead", "no lights", "won't power"))
    no_post = any(k in s_text for k in ("no post", "no display", "no boot", "won't boot", "no logo"))
    no_charge = any(k in s_text for k in ("no charge", "not charging", "battery not", "won't charge"))

    # Build name->measurement index (latest value).
    by_name: Dict[str, M.Measurement] = {}
    for m in measurements:
        by_name[_norm(m.name)] = m

    vin = _find(measurements, INPUT_NAMES)
    valw3 = _find_by_substr(measurements, "3valw")
    valw5 = _find_by_substr(measurements, "5valw")

    # Rule 1: Adapter/input path fault.
    h = Hypothesis(fault="Adapter input path open (input MOSFET / fuse / jack)")
    h.rationale = (
        "The adapter voltage may be present at the jack but blocked before "
        "it reaches the charger IC and always-on rails."
    )
    if vin is not None:
        v = _v_in_volts(vin)
        if v is not None and v >= 17.0:
            h.supporting.append(f"VIN measured {v:.2f} V at {vin.point or 'unspecified point'}; adapter present.")
            # If VIN is present but always-on rails are dead, the input path
            # or the always-on converter is suspect.
            if valw3 is not None and _is_zero_volt(valw3):
                h.supporting.append("VIN present but +3VALW is 0 V; downstream always-on converter has no input or is disabled.")
        elif v is not None and v < 1.0:
            h.contradicting.append("VIN is near 0 V; if measured at the board input, the adapter or jack is suspect, not the downstream MOSFET.")
    else:
        h.missing.append("VIN (adapter voltage at the board input, after the jack).")
    h.score = _score(h, base=0.4 if no_power else 0.2)
    hyps.append(h)

    # Rule 2: Always-on converter / standby rail failure.
    h = Hypothesis(fault="Always-on (standby) converter failure (+3VALW and/or +5VALW)")
    h.rationale = (
        "If the always-on rails are missing the EC has no power, the power "
        "button does nothing, and the board presents as 'dead'."
    )
    if valw3 is not None and _is_zero_volt(valw3):
        h.supporting.append("+3VALW is 0 V while VIN is reported present; the standby converter is not producing its output.")
    if valw5 is not None and _is_zero_volt(valw5):
        h.supporting.append("+5VALW is 0 V; if +3VALW is also dead the shared always-on controller or its input is the likely cause.")
    if valw3 is not None and _is_present_volt(valw3, lo=3.0, hi=3.5):
        h.contradicting.append("+3VALW is present at a normal level; standby converter is OK.")
    if valw3 is None and valw5 is None:
        h.missing.append("+3VALW and +5VALW measurements.")
    h.score = _score(h, base=0.5 if no_power else 0.3)
    hyps.append(h)

    # Rule 3: Enable/power-good gating by the EC.
    h = Hypothesis(fault="Converter held off by EC (missing EN / ACIN / power-good)")
    h.rationale = (
        "The charger IC and run-rail converters need EN and adapter-valid "
        "inputs from the EC. If ACOK/ACIN is low, the EC may refuse to "
        "enable downstream power."
    )
    acok = _find_by_substr(measurements, "acok")
    if acok is not None:
        v = _v_in_volts(acok)
        if v is not None and v < 0.5:
            h.supporting.append(f"ACOK measured {v:.2f} V; if the EC expects a logic-high adapter-valid signal here, it will not enable charging/run rails.")
        elif v is not None and v >= 2.5:
            h.contradicting.append(f"ACOK is {v:.2f} V (logic high); adapter-valid path looks asserted.")
    else:
        h.missing.append("ACOK / ACIN adapter-valid signal at the EC pin.")
    h.score = _score(h, base=0.3 if no_power else 0.2)
    hyps.append(h)

    # Rule 4: Short on a power rail.
    h = Hypothesis(fault="Short on a power rail (low resistance to ground)")
    h.rationale = (
        "A low resistance-to-ground on a power rail suggests a shorted "
        "capacitor, MOSFET or IC; OCP/SCP in the converter then keeps the "
        "rail from starting."
    )
    res = None
    for m in measurements:
        if (m.unit or "").lower() in ("ω", "ω".lower(), "ohm", "kω", "kohm", "ω"):
            res = m
            break
    if res is not None:
        u = (res.unit or "").lower()
        val = res.value
        if val is not None:
            ohm = val * 1000.0 if "k" in u else val
            if ohm < 5.0:
                h.supporting.append(f"Resistance to ground on {res.name} is {ohm:.1f} Ω; very low, consistent with a short on that rail.")
            elif ohm < 50.0:
                h.supporting.append(f"Resistance to ground on {res.name} is {ohm:.1f} Ω; lower than a healthy main rail, partial short or low-impedance load possible.")
            else:
                h.contradicting.append(f"Resistance to ground on {res.name} is {ohm:.1f} Ω; not low enough to indicate a hard short.")
    else:
        h.missing.append("Resistance-to-ground measurement on each suspect rail (taken with power off, capacitors discharged).")
    h.score = _score(h, base=0.4 if no_power else 0.2)
    hyps.append(h)

    # Rule 5: Wrong rail voltage (regulation fault).
    h = Hypothesis(fault="Rail present but out of regulation (feedback / overload / current limit)")
    h.rationale = (
        "A rail at the wrong voltage can mean wrong feedback ratio, open "
        "feedback, or the converter in current limit. The exact cause is "
        "regulator- and topology-specific."
    )
    for m in measurements:
        if _looks_like(m.name, RUN_RAIL_NAMES) or _looks_like(m.name, ALWAYS_ON_NAMES):
            v = _v_in_volts(m)
            if v is None:
                continue
            n = _norm(m.name)
            expected = None
            if "3valw" in n or "3vsus" in n or "3valw" in n:
                expected = 3.3
            elif "5valw" in n or "5vsus" in n:
                expected = 5.0
            elif "1p05" in n or "1p5" in n or "vcore" in n:
                expected = None  # CPU core voltage is part-specific
            if expected and abs(v - expected) > 0.2 and v > 0.1:
                h.supporting.append(f"{m.name} = {v:.2f} V, expected ~{expected:.2f} V; rail is out of regulation.")
    h.score = _score(h, base=0.2)
    hyps.append(h)

    # Rule 6: BIOS / firmware / SPI.
    h = Hypothesis(fault="BIOS/firmware corruption or SPI flash fault")
    h.rationale = (
        "If all rails are up and power-good is asserted but the board still "
        "does not POST, BIOS/SPI is a candidate. A real flash parser is "
        "required to confirm; a raw file alone does not establish corruption."
    )
    if no_post and valw3 is not None and _is_present_volt(valw3):
        h.supporting.append("Always-on rails present and board still does not POST; BIOS/SPI becomes a candidate.")
    h.missing.append("Confirmation that all run rails and power-good are present before suspecting BIOS.")
    h.score = _score(h, base=0.2 if no_post else 0.05)
    hyps.append(h)

    # Rule 7: Charger / battery charging path.
    h = Hypothesis(fault="Charger IC or battery-charging path fault")
    h.rationale = (
        "If the board runs but will not charge, the charger IC, its current "
        "sense, the SMBus to the pack, or the EC's charge-enable are "
        "candidates."
    )
    if no_charge:
        h.supporting.append("Symptom indicates a charging fault specifically.")
    else:
        h.missing.append("Battery/charging-specific symptom or measurements (battery voltage, SMBus, ISET/CSET).")
    h.score = _score(h, base=0.4 if no_charge else 0.1)
    hyps.append(h)

    # Add KB context to top hypothesis rationale (provenance).
    if kb_hits:
        top_refs = []
        for kb in kb_hits[:3]:
            top_refs.append(
                f"{kb['topic']} ({kb['confidence']})"
            )
        # Attach as a contextual note, not as a hypothesis by itself.
        hyps[0].rationale += " Knowledge-base context: " + "; ".join(top_refs) + "."

    # Drop hypotheses with zero support and zero missing evidence (pure noise).
    hyps = [h for h in hyps if h.supporting or h.missing]
    return hyps


def _find(measurements, names: set) -> Optional[M.Measurement]:
    for m in measurements:
        if _looks_like(m.name, names):
            return m
    return None


def _find_by_substr(measurements, substr: str) -> Optional[M.Measurement]:
    n = _norm(substr)
    for m in measurements:
        if n in _norm(m.name):
            return m
    return None


def _score(h: Hypothesis, base: float) -> float:
    s = base
    s += 0.2 * len(h.supporting)
    s -= 0.25 * len(h.contradicting)
    # Missing evidence lowers confidence but doesn't kill the hypothesis.
    s -= 0.05 * len(h.missing)
    return max(0.0, min(1.0, s))


def _collect_missing(hyps: List[Hypothesis]) -> List[str]:
    out: List[str] = []
    seen = set()
    for h in hyps:
        for m in h.missing:
            key = m.lower()
            if key not in seen:
                seen.add(key)
                out.append(f"{m}  (would affect: {h.fault})")
    return out


def _choose_next_test(symptoms: List[str],
                       measurements: List[M.Measurement],
                       hyps: List[Hypothesis]) -> Optional[NextTest]:
    """Pick the single most informative next step.

    Strategy: pick the highest-value missing measurement that would most
    differentiate the top hypotheses. If a measurement is missing from a
    high-scoring hypothesis, ask for it. If all measurements are present
    but interpretation needs a datasheet, request it.
    """
    if not hyps:
        # Nothing to reason about yet; ask for the obvious starting point.
        return NextTest(
            test="Measure adapter DC voltage at the board input (after the jack)",
            where_to_measure="The DC jack's positive pin on the motherboard side, or the input fuse / first inductor after the jack",
            reference_point="DC jack negative pin (GND)",
            expected="~19 V (typical laptop adapter); check the adapter label",
            rationale="No measurements yet; the input path is the foundation of every later check.",
            priority=10,
            caution=safety.required_caution_for(
                "live measure DC voltage at board input on adapter"
            ),
        )

    # Sort hypotheses by score; the next test should target the highest
    # hypothesis whose missing evidence is cheap to obtain.
    sorted_hyps = sorted(hyps, key=lambda h: -h.score)
    by_name = { _norm(m.name): m for m in measurements }
    vin = _find(measurements, INPUT_NAMES)
    valw3 = _find_by_substr(measurements, "3valw")
    valw5 = _find_by_substr(measurements, "5valw")
    acok = _find_by_substr(measurements, "acok")
    res = None
    for m in measurements:
        if (m.unit or "").lower() in ("ω", "ohm", "kω", "kohm"):
            res = m
            break

    # Priority order of next tests (mirrors a real diagnostic flow):
    # 1. If VIN is missing, ask for it.
    if vin is None:
        return NextTest(
            test="Measure DC adapter voltage at the board input (after the jack)",
            where_to_measure="Positive pad of the DC jack on the motherboard side, or the input fuse / first inductor",
            reference_point="DC jack negative pin (GND)",
            expected="~19 V if the adapter is good; check the printed rating",
            rationale="Without knowing the input is present at the board, every downstream rail check is ambiguous.",
            priority=10,
            caution=safety.required_caution_for(
                "live measure adapter voltage on adapter"
            ),
        )

    # 2. If VIN is present but always-on rails are not measured, ask for them.
    if valw3 is None or valw5 is None:
        return NextTest(
            test="Measure +3VALW and +5VALW (always-on / standby rails)",
            where_to_measure="The inductor outputs of the always-on buck converter(s); rail test points are usually near the EC",
            reference_point="A known GND pad (e.g. a USB shell or a ground plane test pad)",
            expected="~3.3 V and ~5.0 V respectively, whenever adapter is present",
            rationale="If the always-on rails are missing the EC has no power and the board presents as dead; if they are present the standby converter is exonerated.",
            priority=9,
            caution=safety.required_caution_for(
                "live measure always-on rails on adapter"
            ),
        )

    # 3. If any always-on rail is dead, ask for resistance to ground on it.
    dead_alw = None
    for cand, label in ((valw3, "+3VALW"), (valw5, "+5VALW")):
        if cand is not None and _is_zero_volt(cand):
            dead_alw = (cand, label)
            break
    if dead_alw and res is None:
        cand, label = dead_alw
        return NextTest(
            test=f"Measure resistance to ground on the dead {label} rail",
            where_to_measure=f"The {label} inductor output (or the rail test point)",
            reference_point="A known GND pad",
            expected="Healthy rails read in the hundreds of ohms to kilo-ohms; a reading under ~20 Ω suggests a short.",
            rationale="A shorted rail explains why the converter produces 0 V (OCP/SCP latched). A normal reading redirects suspicion to the converter or its enable.",
            priority=8,
            caution=(
                "CAUTION: Disconnect power and discharge bulk capacitors before "
                "any resistance measurement."
            ),
        )

    # 4. Adapter-valid signal to the EC, when an always-on rail is dead
    #    but its resistance is normal (so the rail is not just shorted).
    any_alw_dead = (valw3 is not None and _is_zero_volt(valw3)) or (
        valw5 is not None and _is_zero_volt(valw5)
    )
    res_normal = res is not None and res.value is not None and (
        (res.value * (1000.0 if "k" in (res.unit or "").lower() else 1.0)) >= 50.0
    )
    if acok is None and any_alw_dead and (res is None or res_normal):
        return NextTest(
            test="Measure ACOK / ACIN (adapter-valid signal at the EC)",
            where_to_measure="The ACOK pin of the charger IC and the corresponding EC input pin (pin numbers are board- and EC-specific; consult the schematic)",
            reference_point="EC ground (a known GND pad)",
            expected="Logic high (typically 3.0-3.3 V) when a valid adapter is detected; the exact threshold is charger-IC-specific",
            rationale="If ACOK is low the EC will refuse to enable charging and run rails, even with VIN present.",
            priority=7,
            caution=safety.required_caution_for(
                "live measure ACOK on adapter"
            ),
            needs_document=document_requests.DocumentRequest(
                kind="schematic",
                name_hint="this motherboard's schematic (for EC pin mapping of ACOK/ACIN and the charger IC pinout)",
                fields_needed=[
                    "EC ACOK/ACIN input pin number",
                    "Charger IC ACOK output pin number",
                    "Always-on converter EN and PGOOD pin numbers",
                ],
                rationale=(
                    "To measure ACOK and the converter EN/PGOOD signals at the "
                    "right pins I need the board schematic. Without it I can "
                    "only say 'measure the ACOK signal somewhere on the rail'."
                ),
            ),
        )

    # 5. If VIN present AND all measured always-on rails healthy -> check run rails.
    alw_healthy = True
    if valw3 is not None and not _is_present_volt(valw3, lo=3.0, hi=3.5):
        alw_healthy = False
    if valw5 is not None and not _is_present_volt(valw5, lo=4.5, hi=5.5):
        alw_healthy = False
    if (valw3 is not None or valw5 is not None) and alw_healthy:
        return NextTest(
            test="Measure the CPU core VRM output (VCORE) and a run rail (e.g. +1.05V_CPU or +1.5V)",
            where_to_measure="The VRM inductor outputs near the CPU; run-rail test points vary by board",
            reference_point="A known GND pad",
            expected="CPU core voltage is part-specific (commonly 0.6-1.2 V) and only appears after sequencing; if absent while always-on rails are up, sequencing or VRM enable is suspect.",
            rationale="With always-on rails healthy, the next branch is the run-rail sequencing; the VRM enable depends on prior power-good.",
            priority=6,
            caution=safety.required_caution_for(
                "live measure run rails on adapter"
            ),
            needs_document=document_requests.DocumentRequest(
                kind="datasheet",
                name_hint="VRM controller datasheet for the part on this board (for EN, PGOOD, feedback and OCP thresholds)",
                fields_needed=[
                    "EN pin and threshold",
                    "PGOOD pin and assertion level",
                    "Feedback divider reference voltage",
                    "OCP/SCP threshold and latch behavior",
                ],
                rationale=(
                    "To interpret VCORE and confirm the VRM is being correctly "
                    "enabled, the controller datasheet is needed. The exact "
                    "part number is on the IC package; provide the datasheet "
                    "for that part number."
                ),
                optional=True,
            ),
        )

    # 6. Fallback: ask for a symptom clarification or a resistance sweep.
    return NextTest(
        test="Resistance-to-ground sweep of the main rails (with power off)",
        where_to_measure="Each main rail's inductor output, one rail at a time",
        reference_point="A known GND pad",
        expected="Healthy rails read hundreds of ohms to kilo-ohms; flag any rail under ~20 Ω for isolation.",
        rationale="Current evidence does not isolate a single rail; a resistance sweep locates a short to ground cheaply.",
        priority=5,
        caution=(
            "CAUTION: Disconnect power and discharge bulk capacitors before "
            "any resistance measurement."
        ),
    )


def _confirmable(hyps: List[Hypothesis], measurements: List[M.Measurement]) -> Optional[str]:
    """A hypothesis is confirmable only with multiple independent
    supporting measurements and no contradicting evidence above score 0.4."""
    if not hyps:
        return None
    top = sorted(hyps, key=lambda h: -h.score)[0]
    if top.score >= 0.7 and not top.contradicting and len(top.supporting) >= 2:
        return top.fault
    return None


def _persist(session_id: int, analysis: Analysis) -> None:
    """Store hypotheses, missing evidence and next test for this session."""
    # Clear prior (each analyze re-derives).
    database.execute("DELETE FROM hypotheses WHERE session_id=?", (session_id,))
    database.execute("DELETE FROM missing_evidence WHERE session_id=?", (session_id,))
    database.execute("DELETE FROM next_tests WHERE session_id=?", (session_id,))
    for i, h in enumerate(analysis.hypotheses, 1):
        database.execute(
            """INSERT INTO hypotheses(session_id, rank, fault, rationale,
               supporting, contradicting, confidence, created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (session_id, i, h.fault, h.rationale,
             " | ".join(h.supporting), " | ".join(h.contradicting),
             h.score, database.now_iso()),
        )
    for m in analysis.missing_evidence:
        database.execute(
            "INSERT INTO missing_evidence(session_id, item, reason, created_at) VALUES(?,?,?,?)",
            (session_id, m, "auto-analysis", database.now_iso()),
        )
    if analysis.next_test:
        nt = analysis.next_test
        database.execute(
            """INSERT INTO next_tests(session_id, test, where_to_measure,
               reference_point, expected, rationale, priority, created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (session_id, nt.test, nt.where_to_measure, nt.reference_point,
             nt.expected, nt.rationale, nt.priority, database.now_iso()),
        )
    database.add_timeline(session_id, "analysis",
                           f"Analysis: {len(analysis.hypotheses)} hypotheses.")
    database.touch_session(session_id)
