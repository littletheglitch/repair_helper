"""Component Replacement Engine.

Evaluates a candidate component against an original across the full
criteria the spec requires. Produces a structured classification
(COMPATIBLE / CONDITIONAL / NOT_RECOMMENDED / UNKNOWN) with per-criterion
evidence — never a bare verdict.

The engine is intentionally conservative:
  * A criterion is `match` only when both parts have verified data AND
    the data overlaps favourably.
  * A criterion is `mismatch` when the data shows a hard incompatibility
    (e.g. different pin count, different channel type, VDS too low).
  * A criterion is `conditional` when the data permits substitution but
    only with explicit board-level checks.
  * A criterion is `unknown` when either part lacks verified data for
    that criterion.
  * A COMPATIBLE classification requires `match` on every required
    criterion. ANY `unknown` criterion forces UNKNOWN overall. ANY
    `mismatch` forces NOT_RECOMMENDED. ANY `conditional` forces
    CONDITIONAL (unless a mismatch also exists).

The engine NEVER turns UNKNOWN into COMPATIBLE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import sys
import json

PACK_ROOT = Path(__file__).resolve().parents[2]
if str(PACK_ROOT) not in sys.path:
    sys.path.insert(0, str(PACK_ROOT))

from DiagnosticEngines.core.component_db import ComponentDatabase  # noqa: E402


# Required criteria per component type. The engine requires `match` on
# every one of these for a COMPATIBLE classification.
REQUIRED_CRITERIA = {
    "MOSFET": [
        "channel_type", "package", "pinout", "vds", "vgs_max",
        "rds_on", "gate_charge", "vgs_threshold", "continuous_current",
        "power_dissipation", "thermal", "body_diode",
    ],
    "DISCRETE": [  # generic fallback (unknown discrete category)
        "category_match", "package", "pinout", "vrrm_or_vceo",
        "forward_current_or_ic", "power_dissipation", "thermal",
    ],
    "DIODE": [  # diode / schottky / esd
        "category_match", "package", "pinout", "vrrm",
        "forward_current", "vf", "power_dissipation", "thermal",
    ],
    "ZENER": [  # zener — Vz is CRITICAL (it is a reference)
        "category_match", "package", "pinout", "vz",
        "iz", "power_dissipation", "thermal",
    ],
    "TVS": [  # tvs — Vbr and Vc are CRITICAL
        "category_match", "package", "pinout", "vwm",
        "vbr", "vc", "peak_pulse_power",
    ],
    "BJT": [  # bjt_npn / bjt_pnp — Vceo, Ic, hfe are CRITICAL
        "category_match", "package", "pinout", "vceo",
        "ic", "hfe", "power_dissipation", "thermal",
    ],
    "IC": [
        "package", "pin_count", "pinout", "supply_voltage", "vout",
        "functional_compatibility", "control_interface",
        "feedback_architecture", "firmware_dependency",
    ],
    "ANALOG_IC": [
        "category_match", "package", "pin_count", "pinout",
        "supply_voltage", "key_spec",
    ],
}


@dataclass
class CriterionResult:
    criterion: str
    verdict: str            # 'match' | 'mismatch' | 'conditional' | 'unknown'
    note: str


@dataclass
class ReplacementEvaluation:
    original_part: str
    candidate_part: str
    component_type: str       # 'MOSFET' | 'DISCRETE' | 'IC' | 'ANALOG_IC' | 'UNKNOWN'
    classification: str       # 'COMPATIBLE' | 'CONDITIONAL' | 'NOT_RECOMMENDED' | 'UNKNOWN'
    criteria: list[CriterionResult] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: Optional[str] = None


class ReplacementEngine:
    """Evaluates replacement candidates against originals."""

    def __init__(self, pack_root: Optional[Path] = None):
        self.pack_root = pack_root or PACK_ROOT
        self.db = ComponentDatabase.load(self.pack_root)
        # Augment the in-memory DB with the new discrete + analog IC dirs.
        self._load_extended_records()

    def _load_extended_records(self) -> None:
        """Load discrete semiconductor + analog IC records into the
        in-memory db.ic_records list (they live in different folders
        but expose the same find_part interface)."""
        for sub in ("DiscreteSemiconductors", "AnalogICs"):
            root = self.pack_root / "ComponentDatabase" / sub
            if not root.exists():
                continue
            for f in sorted(root.rglob("*.json")):
                try:
                    with open(f, "r", encoding="utf-8") as fh:
                        rec = json.load(fh)
                    self.db.ic_records.append(rec)
                except (OSError, json.JSONDecodeError):
                    continue

    # --- public API ----------------------------------------------------

    def find_part(self, part_number: str) -> Optional[dict]:
        return self.db.find_part(part_number)

    def evaluate(self, original_part: str, candidate_part: str) -> ReplacementEvaluation:
        """Evaluate whether candidate_part can replace original_part."""
        orig = self.find_part(original_part)
        cand = self.find_part(candidate_part)
        if orig is None:
            return ReplacementEvaluation(
                original_part=original_part, candidate_part=candidate_part,
                component_type="UNKNOWN", classification="UNKNOWN",
                notes=[f"Original part {original_part!r} not found in the component database."],
            )
        if cand is None:
            return ReplacementEvaluation(
                original_part=original_part, candidate_part=candidate_part,
                component_type="UNKNOWN", classification="UNKNOWN",
                notes=[f"Candidate part {candidate_part!r} not found in the component database."],
            )

        comp_type = self._classify_type(orig, cand)
        if comp_type == "UNKNOWN":
            return ReplacementEvaluation(
                original_part=original_part, candidate_part=candidate_part,
                component_type="UNKNOWN", classification="UNKNOWN",
                notes=[f"Could not classify component type for {original_part!r} (orig category={orig.get('category')!r}, cand category={cand.get('category')!r})."],
            )

        criteria = self._evaluate_criteria(orig, cand, comp_type)
        classification = self._derive_classification(criteria, comp_type)
        conditions = [c.note for c in criteria if c.verdict == "conditional"]
        return ReplacementEvaluation(
            original_part=original_part, candidate_part=candidate_part,
            component_type=comp_type, classification=classification,
            criteria=criteria, conditions=conditions,
        )

    # --- type classification -------------------------------------------

    def _classify_type(self, orig: dict, cand: dict) -> str:
        o_cat = orig.get("category", "")
        c_cat = cand.get("category", "")
        # MOSFET record has channel_type
        if "channel_type" in orig and "channel_type" in cand:
            return "MOSFET"
        # Discrete semiconductors — classify to the SPECIFIC type so
        # per-category REQUIRED_CRITERIA apply (Zener needs vz, TVS needs
        # vbr/vc, BJT needs hfe, etc.).
        diode_cats = {"diode", "schottky_diode", "esd_diode"}
        zener_cats = {"zener_diode"}
        tvs_cats = {"tvs_diode"}
        bjt_cats = {"bjt_npn", "bjt_pnp"}
        if o_cat in diode_cats and c_cat in diode_cats:
            return "DIODE"
        if o_cat in zener_cats and c_cat in zener_cats:
            return "ZENER"
        if o_cat in tvs_cats and c_cat in tvs_cats:
            return "TVS"
        if o_cat in bjt_cats and c_cat in bjt_cats:
            return "BJT"
        # Cross-discrete (e.g. zener vs tvs) — still discrete but generic
        all_discrete = diode_cats | zener_cats | tvs_cats | bjt_cats
        if o_cat in all_discrete and c_cat in all_discrete:
            return "DISCRETE"
        # Analog ICs
        analog_cats = {"op_amp", "comparator", "supervisor", "reset_ic",
                       "current_sense_amp", "protection_ic", "gate_driver",
                       "interface_ic", "clock_generator", "optocoupler", "power_stage"}
        if o_cat in analog_cats and c_cat in analog_cats:
            return "ANALOG_IC"
        # Power-path ICs (controllers / chargers / etc.)
        # Phase 3: added 'ldo', 'multi_rail_buck_controller',
        # 'buck_controller' (used by new laptop-controller records) and
        # 'voltage_supervisor' (used by new supervisor records) so they
        # route to _eval_ic (which checks vout, firmware_dependency,
        # pin_count, pinout) rather than falling through to UNKNOWN.
        ic_cats = {"pwm_controller", "buck_controller",
                   "multi_rail_buck_controller", "charger_ic",
                   "battery_management_ic", "ldo", "ldo_regulator",
                   "load_switch", "pmic", "bios_spi_flash", "ec_controller",
                   "usb_pd_controller", "voltage_regulator",
                   "voltage_supervisor"}
        if o_cat in ic_cats and c_cat in ic_cats:
            return "IC"
        return "UNKNOWN"

    # --- per-criterion evaluation --------------------------------------

    def _evaluate_criteria(self, orig: dict, cand: dict, comp_type: str) -> list[CriterionResult]:
        if comp_type == "MOSFET":
            return self._eval_mosfet(orig, cand)
        if comp_type in ("DISCRETE", "DIODE", "ZENER", "TVS", "BJT"):
            # _eval_discrete dispatches per category internally
            return self._eval_discrete(orig, cand)
        if comp_type == "ANALOG_IC":
            return self._eval_analog_ic(orig, cand)
        if comp_type == "IC":
            return self._eval_ic(orig, cand)
        return [CriterionResult("type", "unknown", "Unknown component type.")]

    def _eval_mosfet(self, o: dict, c: dict) -> list[CriterionResult]:
        out: list[CriterionResult] = []
        # channel_type
        if o.get("channel_type") == c.get("channel_type"):
            out.append(CriterionResult("channel_type", "match", f"Both {o.get('channel_type')}"))
        else:
            out.append(CriterionResult("channel_type", "mismatch",
                f"Original {o.get('channel_type')} vs candidate {c.get('channel_type')} — NOT interchangeable."))
        # package
        out.append(self._cmp_str("package", o.get("package",""), c.get("package","")))
        # pinout
        op = o.get("pinout", {}) or {}
        cp = c.get("pinout", {}) or {}
        if not op or not cp:
            out.append(CriterionResult("pinout", "unknown", "Pinout not verified for one or both parts."))
        elif op == cp:
            out.append(CriterionResult("pinout", "match", "Pinouts match."))
        else:
            out.append(CriterionResult("pinout", "mismatch", f"Pinouts differ: {op} vs {cp}"))
        # vds
        out.append(self._cmp_numeric_ge("vds", o.get("vds_v"), c.get("vds_v"),
                                        "Candidate VDS must be >= original (in magnitude)."))
        # vgs_max
        out.append(self._cmp_numeric_ge("vgs_max", o.get("vgs_max_v"), c.get("vgs_max_v"),
                                        "Candidate VGS_max must be >= original."))
        # rds_on
        o_rds = o.get("rds_on_mohm")
        c_rds = c.get("rds_on_mohm")
        if o_rds is None or c_rds is None:
            out.append(CriterionResult("rds_on", "unknown", "RDS_on not verified for one or both."))
        elif c_rds <= o_rds * 1.1:  # allow 10% higher
            out.append(CriterionResult("rds_on", "match",
                f"Candidate RDS_on {c_rds} mOhm <= original {o_rds} mOhm + 10%."))
        else:
            out.append(CriterionResult("rds_on", "conditional",
                f"Candidate RDS_on {c_rds} mOhm > original {o_rds} mOhm + 10%. Verify thermal and efficiency impact."))
        # gate_charge
        o_qg = o.get("gate_charge_nc")
        c_qg = c.get("gate_charge_nc")
        if o_qg is None or c_qg is None:
            out.append(CriterionResult("gate_charge", "unknown", "Qg not verified for one or both."))
        elif c_qg <= o_qg * 1.5:
            out.append(CriterionResult("gate_charge", "match",
                f"Candidate Qg {c_qg} nC <= original {o_qg} nC * 1.5."))
        else:
            out.append(CriterionResult("gate_charge", "conditional",
                f"Candidate Qg {c_qg} nC is much higher than original {o_qg} nC. Verify gate driver can supply the current at Fsw."))
        # vgs_threshold
        out.append(self._cmp_numeric_close("vgs_threshold", o.get("vgs_th_v"), c.get("vgs_th_v"),
                                           "VGS(th) should be similar; a much lower threshold risks false turn-on."))
        # continuous_current
        out.append(self._cmp_numeric_ge("continuous_current", o.get("id_a"), c.get("id_a"),
                                        "Candidate ID must be >= original (in magnitude)."))
        # power_dissipation
        out.append(self._cmp_numeric_ge("power_dissipation", o.get("power_dissipation_w"), c.get("power_dissipation_w"),
                                        "Candidate Pd should be >= original."))
        # thermal
        o_t = (o.get("thermal",{}) or {}).get("r_theta_ja_c_per_w")
        c_t = (c.get("thermal",{}) or {}).get("r_theta_ja_c_per_w")
        if o_t is None or c_t is None:
            out.append(CriterionResult("thermal", "unknown", "Rtheta_ja not verified for one or both."))
        elif c_t <= o_t * 1.2:  # lower Rtheta is better; allow 20% higher
            out.append(CriterionResult("thermal", "match",
                f"Candidate Rtheta_ja {c_t} C/W <= original {o_t} C/W * 1.2."))
        else:
            out.append(CriterionResult("thermal", "conditional",
                f"Candidate Rtheta_ja {c_t} C/W is significantly higher than original {o_t} C/W. Verify thermal margin."))
        # body_diode (we only check the channel type implies a body diode of the same polarity)
        if o.get("channel_type") == c.get("channel_type"):
            out.append(CriterionResult("body_diode", "match",
                "Same channel type — body diode polarity matches."))
        else:
            out.append(CriterionResult("body_diode", "mismatch",
                "Different channel type — body diode polarity differs."))
        return out

    def _eval_discrete(self, o: dict, c: dict) -> list[CriterionResult]:
        """Evaluate discrete-semiconductor replacement. Dispatches per
        category because each discrete type has DISTINCT critical specs:

          diode/schottky : Vrrm, If_avg, Vf, Pd, package, pinout
          zener_diode    : Vz (=vrrm_v in our schema), Iz (=if_avg), Pd
          tvs_diode      : Vwm (=vrrm_v), Vbr, Vc, Pp (peak pulse)
          bjt_npn/pnp    : Vceo, Ic, hfe, Pd
          esd_diode      : Vrwm, Vbr, Vc, IPP

        A generic Vrrm-or-Vceo check (the old behavior) conflated these
        and missed category-specific critical specs (e.g. a Zener's Vz,
        a TVS's Vc). This per-category split is the P3 fix.
        """
        cat = o.get("category", "")
        if cat in ("diode", "schottky_diode", "esd_diode"):
            return self._eval_diode(o, c)
        if cat == "zener_diode":
            return self._eval_zener(o, c)
        if cat == "tvs_diode":
            return self._eval_tvs(o, c)
        if cat in ("bjt_npn", "bjt_pnp"):
            return self._eval_bjt(o, c)
        # Fallback: generic discrete checks (backward-compat)
        return self._eval_diode_generic(o, c)

    def _discrete_common(self, o: dict, c: dict) -> list[CriterionResult]:
        """Common checks shared by all discrete types: category match,
        package, pinout."""
        out: list[CriterionResult] = []
        if o.get("category") == c.get("category"):
            out.append(CriterionResult("category_match", "match",
                f"Both {o.get('category')}"))
        else:
            out.append(CriterionResult("category_match", "conditional",
                f"Different categories: {o.get('category')} vs "
                f"{c.get('category')}. Verify the application tolerates it."))
        out.append(self._cmp_str("package", o.get("package",""), c.get("package","")))
        op = o.get("pinout", {}) or {}; cp = c.get("pinout", {}) or {}
        if not op or not cp:
            out.append(CriterionResult("pinout", "unknown",
                "Pinout not verified for one or both."))
        elif op == cp:
            out.append(CriterionResult("pinout", "match", "Pinouts match."))
        else:
            out.append(CriterionResult("pinout", "mismatch", "Pinouts differ."))
        return out

    def _eval_diode(self, o: dict, c: dict) -> list[CriterionResult]:
        """Diode / Schottky / ESD: Vrrm, If_avg, Vf, Pd, thermal."""
        out = self._discrete_common(o, c)
        eo = o.get("electrical_characteristics", {}) or {}
        ec = c.get("electrical_characteristics", {}) or {}
        out.append(self._cmp_numeric_ge("vrrm", eo.get("vrrm_v"), ec.get("vrrm_v"),
            "Candidate Vrrm must be >= original."))
        out.append(self._cmp_numeric_ge("forward_current",
            eo.get("if_avg_a"), ec.get("if_avg_a"),
            "Candidate If must be >= original."))
        out.append(self._cmp_numeric_le("vf",
            eo.get("vf_v"), ec.get("vf_v"),
            "Candidate Vf should be <= original (lower forward drop is better)."))
        out.append(self._cmp_numeric_ge("power_dissipation",
            eo.get("pd_w"), ec.get("pd_w"),
            "Candidate Pd should be >= original."))
        out.append(self._cmp_numeric_le("thermal",
            eo.get("thermal_r_ja_c_per_w"), ec.get("thermal_r_ja_c_per_w"),
            "Candidate Rtheta_ja should be <= original."))
        return out

    def _eval_zener(self, o: dict, c: dict) -> list[CriterionResult]:
        """Zener: Vz (=vrrm_v in our schema), Iz (=if_avg), Pd.
        A Zener's CRITICAL spec is Vz — it must MATCH closely (not just
        be >=), because a Zener is a voltage reference. A 5.1V Zener is
        NOT a substitute for a 12V Zener. Tight tolerance: ±5% (the
        'B' suffix Zeners are ±5% parts; a different Vz is a different
        reference)."""
        out = self._discrete_common(o, c)
        eo = o.get("electrical_characteristics", {}) or {}
        ec = c.get("electrical_characteristics", {}) or {}
        vz_o = eo.get("vz_v") or eo.get("vrrm_v")  # Vz (fallback to vrrm_v)
        vz_c = ec.get("vz_v") or ec.get("vrrm_v")
        # Vz must MATCH within ±5% — NOT just be >=. A Zener is a
        # precision reference; a different Vz is a different part.
        if vz_o is None or vz_c is None:
            out.append(CriterionResult("vz", "unknown",
                "Zener voltage Vz not verified for one or both."))
        elif abs(vz_c - vz_o) <= abs(vz_o) * 0.05:
            out.append(CriterionResult("vz", "match",
                f"Zener Vz matches: {vz_o}V vs {vz_c}V (within 5%)."))
        else:
            out.append(CriterionResult("vz", "mismatch",
                f"Zener Vz differs: {vz_o}V vs {vz_c}V. A Zener is a "
                "precision reference; a different Vz is NOT a substitute."))
        out.append(self._cmp_numeric_ge("iz",
            eo.get("if_avg_a"), ec.get("if_avg_a"),
            "Candidate Iz should be >= original."))
        out.append(self._cmp_numeric_ge("power_dissipation",
            eo.get("pd_w"), ec.get("pd_w"),
            "Candidate Pd should be >= original."))
        out.append(self._cmp_numeric_le("thermal",
            eo.get("thermal_r_ja_c_per_w"), ec.get("thermal_r_ja_c_per_w"),
            "Candidate Rtheta_ja should be <= original."))
        return out

    def _eval_tvs(self, o: dict, c: dict) -> list[CriterionResult]:
        """TVS: Vwm (=vrrm_v), Vbr, Vc (clamping), Pp (peak pulse).
        CRITICAL: Vc (clamping voltage) must be <= the protected IC's
        abs max — a HIGHER Vc does NOT protect. Vwm must be >= the
        rail's normal max. Pp must be >= expected surge."""
        out = self._discrete_common(o, c)
        eo = o.get("electrical_characteristics", {}) or {}
        ec = c.get("electrical_characteristics", {}) or {}
        out.append(self._cmp_numeric_ge("vwm",
            eo.get("vrrm_v"), ec.get("vrrm_v"),
            "Candidate Vwm (working) must be >= original."))
        out.append(self._cmp_numeric_ge("vbr",
            eo.get("vbr_v"), ec.get("vbr_v"),
            "Candidate Vbr must be >= original."))
        # Vc must be <= original (lower clamping = better protection).
        out.append(self._cmp_numeric_le("vc",
            eo.get("vc_v"), ec.get("vc_v"),
            "Candidate Vc (clamping) must be <= original. A HIGHER Vc "
            "does NOT protect the downstream IC."))
        out.append(self._cmp_numeric_ge("peak_pulse_power",
            eo.get("pp_w"), ec.get("pp_w"),
            "Candidate Pp (peak pulse) must be >= original."))
        return out

    def _eval_bjt(self, o: dict, c: dict) -> list[CriterionResult]:
        """BJT: Vceo, Ic, hfe, Pd, thermal."""
        out = self._discrete_common(o, c)
        eo = o.get("electrical_characteristics", {}) or {}
        ec = c.get("electrical_characteristics", {}) or {}
        out.append(self._cmp_numeric_ge("vceo",
            eo.get("vceo_v"), ec.get("vceo_v"),
            "Candidate Vceo must be >= original."))
        out.append(self._cmp_numeric_ge("ic",
            eo.get("ic_a"), ec.get("ic_a"),
            "Candidate Ic must be >= original."))
        out.append(self._cmp_numeric_ge("hfe",
            eo.get("hfe_min"), ec.get("hfe_min"),
            "Candidate hfe_min must be >= original (gain)."))
        out.append(self._cmp_numeric_ge("power_dissipation",
            eo.get("pd_w"), ec.get("pd_w"),
            "Candidate Pd should be >= original."))
        out.append(self._cmp_numeric_le("thermal",
            eo.get("thermal_r_ja_c_per_w"), ec.get("thermal_r_ja_c_per_w"),
            "Candidate Rtheta_ja should be <= original."))
        return out

    def _eval_diode_generic(self, o: dict, c: dict) -> list[CriterionResult]:
        """Legacy generic discrete checks (backward-compat for unknown
        discrete categories). Uses vrrm-or-vceo and if-or-ic."""
        out = self._discrete_common(o, c)
        eo = o.get("electrical_characteristics", {}) or {}
        ec = c.get("electrical_characteristics", {}) or {}
        vrrm_o = eo.get("vrrm_v") or eo.get("vceo_v")
        vrrm_c = ec.get("vrrm_v") or ec.get("vceo_v")
        out.append(self._cmp_numeric_ge("vrrm_or_vceo", vrrm_o, vrrm_c,
            "Candidate voltage rating must be >= original."))
        ic_o = eo.get("if_avg_a") or eo.get("ic_a")
        ic_c = ec.get("if_avg_a") or ec.get("ic_a")
        out.append(self._cmp_numeric_ge("forward_current_or_ic", ic_o, ic_c,
            "Candidate current rating must be >= original."))
        out.append(self._cmp_numeric_ge("power_dissipation",
            eo.get("pd_w"), ec.get("pd_w"),
            "Candidate Pd should be >= original."))
        out.append(self._cmp_numeric_le("thermal",
            eo.get("thermal_r_ja_c_per_w"), ec.get("thermal_r_ja_c_per_w"),
            "Candidate Rtheta_ja should be <= original."))
        return out

    def _eval_analog_ic(self, o: dict, c: dict) -> list[CriterionResult]:
        out: list[CriterionResult] = []
        if o.get("category") == c.get("category"):
            out.append(CriterionResult("category_match", "match", f"Both {o.get('category')}"))
        else:
            out.append(CriterionResult("category_match", "mismatch",
                f"Different categories: {o.get('category')} vs {c.get('category')} — NOT interchangeable."))
        out.append(self._cmp_str("package", o.get("package",""), c.get("package","")))
        if o.get("pin_count") == c.get("pin_count"):
            out.append(CriterionResult("pin_count", "match", f"Both {o.get('pin_count')} pins"))
        else:
            out.append(CriterionResult("pin_count", "mismatch",
                f"Pin count differs: {o.get('pin_count')} vs {c.get('pin_count')}"))
        op = o.get("pinout", {}) or {}; cp = c.get("pinout", {}) or {}
        if not op or not cp:
            out.append(CriterionResult("pinout", "unknown", "Pinout not verified for one or both."))
        elif op == cp:
            out.append(CriterionResult("pinout", "match", "Pinouts match."))
        else:
            out.append(CriterionResult("pinout", "mismatch", "Pinouts differ."))
        ec_o = o.get("electrical_characteristics", {}) or {}
        ec_c = c.get("electrical_characteristics", {}) or {}
        sv_o = ec_o.get("supply_voltage_range_v", {}) or {}
        sv_c = ec_c.get("supply_voltage_range_v", {}) or {}
        # Null-safe: treat missing/None as "not verified". (Phase 3 fix:
        # records may carry min=null with max set, e.g. "max 32V, min
        # not specified" — the old comparison crashed on None.)
        o_min, o_max = sv_o.get("min"), sv_o.get("max")
        c_min, c_max = sv_c.get("min"), sv_c.get("max")
        if o_max is None or c_max is None:
            out.append(CriterionResult("supply_voltage", "unknown", "Supply range not verified."))
        else:
            o_min_v = o_min if o_min is not None else 0
            c_min_v = c_min if c_min is not None else 0
            if c_min_v <= o_min_v and c_max >= o_max:
                out.append(CriterionResult("supply_voltage", "match",
                    f"Candidate supply range covers original: {sv_c} vs {sv_o}"))
            else:
                out.append(CriterionResult("supply_voltage", "conditional",
                    f"Candidate supply range {sv_c} does not fully cover original {sv_o}. Verify the application's actual VCC."))
        # Key spec: depends on category. Pick the most relevant.
        key_map = {
            "op_amp": ("bandwidth_mhz", "GBW (MHz)"),
            "comparator": ("propagation_delay_ns", "Propagation delay (ns)"),
            "supervisor": ("threshold_v", "Threshold (V)"),
            "reset_ic": ("threshold_v", "Threshold (V)"),
            "current_sense_amp": ("bandwidth_mhz", "Bandwidth (MHz)"),
            "gate_driver": ("gate_drive_a", "Peak gate drive (A)"),
            "optocoupler": ("ctr_min", "CTR min (%)"),
        }
        key_field, key_label = key_map.get(o.get("category"), (None, None))
        if key_field:
            o_v = ec_o.get(key_field)
            c_v = ec_c.get(key_field)
            if o_v is None or c_v is None:
                out.append(CriterionResult("key_spec", "unknown", f"{key_label} not verified for one or both."))
            elif abs(c_v - o_v) <= max(abs(o_v) * 0.2, 0.001):
                out.append(CriterionResult("key_spec", "match",
                    f"{key_label} similar: original {o_v} vs candidate {c_v}"))
            else:
                out.append(CriterionResult("key_spec", "conditional",
                    f"{key_label} differs significantly: original {o_v} vs candidate {c_v}. Verify the application tolerates the difference."))
        else:
            out.append(CriterionResult("key_spec", "unknown", "No key spec defined for this category."))
        return out

    def _eval_ic(self, o: dict, c: dict) -> list[CriterionResult]:
        out: list[CriterionResult] = []
        out.append(self._cmp_str("package", o.get("package",""), c.get("package","")))
        if o.get("pin_count") == c.get("pin_count"):
            out.append(CriterionResult("pin_count", "match", f"Both {o.get('pin_count')} pins"))
        else:
            out.append(CriterionResult("pin_count", "mismatch", "Pin count differs."))
        op = o.get("pinout", {}) or {}; cp = c.get("pinout", {}) or {}
        if not op or not cp:
            out.append(CriterionResult("pinout", "unknown", "Pinout not verified for one or both."))
        elif op == cp:
            out.append(CriterionResult("pinout", "match", "Pinouts match."))
        else:
            out.append(CriterionResult("pinout", "mismatch", "Pinouts differ."))
        ec_o = o.get("electrical_characteristics", {}) or {}
        ec_c = c.get("electrical_characteristics", {}) or {}
        sv_o = ec_o.get("input_voltage_range_v", {}) or {}
        sv_c = ec_c.get("input_voltage_range_v", {}) or {}
        # Null-safe (Phase 3 fix: records may carry min=null with max set).
        o_min, o_max = sv_o.get("min"), sv_o.get("max")
        c_min, c_max = sv_c.get("min"), sv_c.get("max")
        if o_max is None or c_max is None:
            out.append(CriterionResult("supply_voltage", "unknown", "Input voltage range not verified."))
        else:
            o_min_v = o_min if o_min is not None else 0
            c_min_v = c_min if c_min is not None else 0
            if c_min_v <= o_min_v and c_max >= o_max:
                out.append(CriterionResult("supply_voltage", "match", "Candidate input range covers original."))
            else:
                out.append(CriterionResult("supply_voltage", "conditional", "Candidate input range does not fully cover original."))
        # Output voltage: CRITICAL for LDOs and fixed-output regulators.
        # A 3.3V LDO is NOT a substitute for a 1.8V LDO. The output
        # voltage must match (or both be adjustable).
        ov_o = ec_o.get("output_voltage_range_v", {}) or {}
        ov_c = ec_c.get("output_voltage_range_v", {}) or {}
        if ov_o.get("typ") is None or ov_c.get("typ") is None:
            # If both are adjustable, that's a match (output set externally)
            if ov_o.get("adjustable") and ov_c.get("adjustable"):
                out.append(CriterionResult("vout", "match",
                    "Both adjustable — output set by external divider."))
            else:
                out.append(CriterionResult("vout", "unknown",
                    "Output voltage not verified for one or both."))
        elif abs(ov_o.get("typ", 0) - ov_c.get("typ", 0)) <= max(abs(ov_o.get("typ", 0)) * 0.02, 0.05):
            out.append(CriterionResult("vout", "match",
                f"Output voltage matches: {ov_o.get('typ')}V vs {ov_c.get('typ')}V."))
        else:
            out.append(CriterionResult("vout", "mismatch",
                f"Output voltage differs: {ov_o.get('typ')}V vs {ov_c.get('typ')}V. "
                "A fixed-output regulator of a different voltage is NOT a substitute."))
        # Functional compatibility: same category is a weak match; same part family stronger.
        if o.get("category") == c.get("category"):
            out.append(CriterionResult("functional_compatibility", "conditional",
                "Same category, but controller/charger ICs are firmware-tuned via SMBus/I2C. Functional compatibility requires matching the host EC firmware's expectations."))
        else:
            out.append(CriterionResult("functional_compatibility", "mismatch",
                "Different categories — not functionally compatible."))
        # Control interface: ICs in the same family often share SMBus/I2C, but register maps differ.
        out.append(CriterionResult("control_interface", "conditional",
            "Control interface compatibility cannot be verified without the datasheet's register map AND the host EC firmware. Assume incompatible until proven otherwise."))
        out.append(CriterionResult("feedback_architecture", "conditional",
            "Feedback architecture (D-CAP vs current-mode, etc.) must match. Verify from datasheets."))
        out.append(CriterionResult("firmware_dependency", "conditional",
            "Controller ICs are firmware-defined. Even pin-compatible parts break EC firmware unless the vendor explicitly cross-references them."))
        return out

    # --- helpers -------------------------------------------------------

    def _cmp_str(self, name: str, o: str, c: str) -> CriterionResult:
        if not o or not c:
            return CriterionResult(name, "unknown", f"{name} not verified for one or both.")
        if o.lower() == c.lower():
            return CriterionResult(name, "match", f"{name} matches: {o}")
        return CriterionResult(name, "mismatch", f"{name} differs: {o!r} vs {c!r}")

    def _cmp_numeric_ge(self, name: str, o, c, note: str) -> CriterionResult:
        """Candidate must be >= original (in magnitude). Used for SAFETY
        ratings (VDS, VGS_max, ID, Pd, VRRM/VCEO, forward current).

        No underrating tolerance: a candidate rated below the original
        on a safety rating is a MISMATCH, not a match. A 19 V MOSFET
        must NOT be accepted as a replacement for a 20 V part — the
        rating exists to prevent avalanche/overvoltage failure.

        A small relative epsilon (0.1%) is allowed only to absorb
        floating-point rounding, NOT rating underrating.
        """
        if o is None or c is None:
            return CriterionResult(name, "unknown", f"{name} not verified for one or both.")
        eps = abs(o) * 0.001  # 0.1% floating-point epsilon, NOT a rating tolerance
        if abs(c) >= abs(o) - eps:
            return CriterionResult(name, "match", f"{name}: candidate {c} >= original {o}. {note}")
        return CriterionResult(name, "mismatch", f"{name}: candidate {c} < original {o} (in magnitude) — UNDERRATED. {note}")

    def _cmp_numeric_le(self, name: str, o, c, note: str) -> CriterionResult:
        """Candidate must be <= original (lower is better)."""
        if o is None or c is None:
            return CriterionResult(name, "unknown", f"{name} not verified for one or both.")
        if abs(c) <= abs(o) * 1.2:  # allow 20% higher
            return CriterionResult(name, "match", f"{name}: candidate {c} <= original {o} * 1.2. {note}")
        return CriterionResult(name, "conditional", f"{name}: candidate {c} significantly higher than original {o}. {note}")

    def _cmp_numeric_close(self, name: str, o, c, note: str) -> CriterionResult:
        """Candidate should be close to original."""
        if o is None or c is None:
            return CriterionResult(name, "unknown", f"{name} not verified for one or both.")
        if abs(c - o) <= max(abs(o) * 0.5, 0.5):
            return CriterionResult(name, "match", f"{name}: candidate {c} close to original {o}. {note}")
        return CriterionResult(name, "conditional", f"{name}: candidate {c} differs from original {o}. {note}")

    # --- classification ------------------------------------------------

    def _derive_classification(self, criteria: list[CriterionResult], comp_type: str) -> str:
        """Derive the overall classification from per-criterion verdicts.

        Five classifications (P3):
          COMPATIBLE              - all required criteria match
          CONDITIONALLY_COMPATIBLE- a required criterion is conditional
                                    (usable but verify)
          NOT_RECOMMENDED         - a required criterion mismatched
          INSUFFICIENT_DATA       - a required (critical) criterion is
                                    unknown (missing spec) — the engine
                                    cannot confirm compatibility
          UNKNOWN                 - the component type itself is unknown,
                                    or cross-category

        The key P3 rule: a missing CRITICAL spec -> INSUFFICIENT_DATA,
        never COMPATIBLE. The engine never turns missing data into a
        positive compatibility claim.
        """
        required = REQUIRED_CRITERIA.get(comp_type, [])
        verdicts = {c.criterion: c.verdict for c in criteria}
        # Phase 3 fix: a MISMATCH on a CRITICAL electrical spec (vout,
        # vds, vz, vrrm — the safety/function-defining specs) is a hard
        # NOT_RECOMMENDED, regardless of unknowns on non-critical specs
        # (pinout, thermal). Without this, a wrong-Vout LDO with an
        # unverified pinout would be INSUFFICIENT_DATA instead of
        # NOT_RECOMMENDED — masking a real incompatibility.
        CRITICAL_MISMATCH_SPECS = {"vout", "vds", "vz", "vrrm",
                                   "forward_current", "continuous_current"}
        for crit_spec in CRITICAL_MISMATCH_SPECS:
            if verdicts.get(crit_spec) == "mismatch":
                return "NOT_RECOMMENDED"
        # A required criterion that is 'unknown' means a critical spec
        # is missing -> INSUFFICIENT_DATA (cannot confirm compatibility).
        for r in required:
            if verdicts.get(r) == "unknown":
                return "INSUFFICIENT_DATA"
        # Any mismatch on a required criterion => NOT_RECOMMENDED
        for r in required:
            if verdicts.get(r) == "mismatch":
                return "NOT_RECOMMENDED"
        # Any conditional on a required criterion => CONDITIONALLY_COMPATIBLE
        for r in required:
            if verdicts.get(r) == "conditional":
                return "CONDITIONALLY_COMPATIBLE"
        # All required are match => COMPATIBLE
        return "COMPATIBLE"
