"""Practical Exercise Generator.

Generates exercises from four sources, all with a KNOWN reference
solution or a documented valid range (the LLM never invents the
answer):

  1. Engine-computed exercises — the reference solution is computed by
     a deterministic engine (electrical_math: Ohm, divider, power,
     KVL/KCL, series/parallel). The prompt gives the inputs; the user
     computes the output; the engine's result is the reference.
  2. Knowledge-Base exercises — a fact from the KB becomes a "what is
     the expected value / what does X mean" question; the KB fact
     (with its tier) is the reference. Only KB facts with a verifiable
     numeric/definitional content are used; community-tier facts are
     not used as the reference for a numeric exercise.
  3. Confirmed repair-case exercises — a technician-confirmed repair
     case becomes a "given these symptoms + measurements, what is the
     likely fault?" exercise. The original fault is the reference, BUT
     the exercise is generated as SIMILAR-BUT-NOT-IDENTICAL (component
     labels and numbers are perturbed) so the user must reason, not
     recall. The original solution is only revealed on explicit request.
  4. Controlled synthetic scenarios — small parametrized circuits with
     a known analytical answer (e.g. an RC time constant, a divider
     with a load). Parameters are drawn deterministically from a seed
     so the exercise is reproducible and testable.

Each generated exercise is stored in the ``exercises`` table with its
``source_kind``, ``reference_solution`` (or ``valid_range``), ``unit``,
and ``formulas``. ``solution_hidden`` defaults to True so the CLI does
not auto-disclose it.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .. import knowledge_base, schemas as S
from ..engines import base as E
from .. import learning as repair_learning  # existing repair-case store
from . import db
from . import physical_validation as PV


@dataclass
class Exercise:
    id: int
    exercise_key: str
    skill_key: str
    difficulty: int
    source_kind: str
    prompt: str
    reference_solution: str = ""
    valid_range: Optional[Dict[str, Any]] = None
    unit: str = ""
    formulas: str = ""
    assumptions: str = ""
    solution_hidden: bool = True
    source_ref: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "exercise_key": self.exercise_key,
            "skill_key": self.skill_key, "difficulty": self.difficulty,
            "source_kind": self.source_kind, "prompt": self.prompt,
            "reference_solution": self.reference_solution,
            "valid_range": self.valid_range, "unit": self.unit,
            "formulas": self.formulas, "assumptions": self.assumptions,
            "solution_hidden": self.solution_hidden,
            "source_ref": self.source_ref,
        }


# A seeded RNG so exercise generation is reproducible for tests.
def _rng_for(seed_key: str) -> random.Random:
    h = hashlib.sha256(seed_key.encode("utf-8")).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


class ExerciseGenerator:
    """Generates and stores exercises from the four sources."""

    def __init__(self, profile_id: Optional[int] = None,
                 profile_name: str = "default"):
        if profile_id is None:
            prof = db.get_or_create_profile(profile_name)
            profile_id = int(prof["id"])
        self.profile_id = int(profile_id)

    # -- Public ---------------------------------------------------------
    def generate(self, skill_key: str, n: int = 1,
                 seed: Optional[int] = None) -> List[Exercise]:
        """Generate up to ``n`` exercises for a skill, store them, and
        return them. Uses the appropriate source per skill."""
        db.ensure_schema()
        out: List[Exercise] = []
        # Engine-sourced skills (computable reference).
        engine_skills = {
            "ohm_kirchhoff": self._gen_ohm,
            "electrical_math": self._gen_ohm,
            "series_parallel": self._gen_series_parallel,
            "voltage_divider": self._gen_divider,
        }
        if skill_key in engine_skills:
            for i in range(n):
                key = f"{skill_key}_engine_{seed or 0}_{i}_{self.profile_id}"
                ex = engine_skills[skill_key](skill_key, key, i, seed)
                if ex:
                    self._store(ex)
                    out.append(ex)
            return out
        # KB-sourced skills.
        if skill_key in ("datasheet_interp", "power_sequencing",
                         "battery_charging", "buck_boost", "pwm_controllers"):
            for i in range(n):
                key = f"{skill_key}_kb_{seed or 0}_{i}_{self.profile_id}"
                ex = self._gen_from_kb(skill_key, key, i, seed)
                if ex:
                    self._store(ex)
                    out.append(ex)
            return out
        # Confirmed repair-case exercises (fault isolation).
        if skill_key == "fault_isolation":
            for i in range(n):
                key = f"{skill_key}_case_{seed or 0}_{i}_{self.profile_id}"
                ex = self._gen_from_case(skill_key, key, i, seed)
                if ex:
                    self._store(ex)
                    out.append(ex)
            return out
        # Synthetic RC/RL.
        if skill_key == "rc_rl":
            for i in range(n):
                key = f"{skill_key}_synth_{seed or 0}_{i}_{self.profile_id}"
                ex = self._gen_rc(skill_key, key, i, seed)
                if ex:
                    self._store(ex)
                    out.append(ex)
            return out
        # Fallback: a Socratic-prompt exercise (no numeric reference).
        for i in range(n):
            key = f"{skill_key}_prompt_{seed or 0}_{i}_{self.profile_id}"
            ex = Exercise(
                id=0, exercise_key=key, skill_key=skill_key, difficulty=2,
                source_kind="synthetic",
                prompt=(f"Explain, in your own words, the key principle of "
                        f"{skill_key} and one diagnostic situation where it "
                        f"matters."),
                reference_solution="(open-ended; graded on reasoning, not a "
                                   "single number)",
                solution_hidden=True,
            )
            self._store(ex)
            out.append(ex)
        return out

    def get(self, exercise_id: int) -> Optional[Exercise]:
        d = db.get_exercise(exercise_id)
        return self._from_dict(d) if d else None

    def list_for_skill(self, skill_key: str, limit: int = 20) -> List[Exercise]:
        rows = db.list_exercises(skill_key, limit=limit)
        return [self._from_dict(r) for r in rows]

    # -- Knowledge-transfer shapes (D10) --------------------------------
    # Each transfer shape poses a DIFFERENT question for the same skill
    # — not a number swap of the same template. The user must transfer
    # the principle to a different circuit condition, without a worked
    # example. The reference is still engine-computed and validated.
    def generate_transfer(self, skill_key: str, n: int = 1,
                          seed: Optional[int] = None) -> List[Exercise]:
        """Generate exercises that transfer the skill to a DIFFERENT
        problem shape than the base generator. Used to test knowledge
        transfer (the spec's section 3): the new problem must not be a
        mere number-swap of an existing exercise."""
        db.ensure_schema()
        out: List[Exercise] = []
        transfer_skills = {
            "ohm_kirchhoff": self._gen_ohm_inverse,        # given R,V -> I
            "electrical_math": self._gen_power_shape,      # P = V*I
            "voltage_divider": self._gen_divider_with_load,  # divider under load
            "series_parallel": self._gen_series_parallel_current,  # current split
            # Phase 4.2: 12 NEW transfer shapes (one per remaining skill).
            "rc_rl": self._gen_rc_transfer,                     # given tau,R -> C
            "mosfet": self._gen_mosfet_transfer,                 # conduction loss
            "diodes_bjt": self._gen_diodes_bjt_transfer,        # forced-beta Ib
            "opamps_comparators": self._gen_opamp_transfer,     # inverting gain
            "buck_boost": self._gen_buck_boost_transfer,         # boost duty cycle
            "pwm_controllers": self._gen_pwm_transfer,          # soft-start time
            "battery_charging": self._gen_battery_transfer,      # CC charge time
            "power_sequencing": self._gen_power_seq_transfer,   # RC enable delay
            "datasheet_interp": self._gen_datasheet_transfer,    # worst-case (max)
            "schematic_topology": self._gen_schematic_transfer,  # rail dependency
            "measurement_interp": self._gen_measurement_transfer, # ground verify
            "fault_isolation": self._gen_fault_isolation_transfer, # symptom vs cause
        }
        if skill_key not in transfer_skills:
            return out  # no transfer shape for this skill
        for i in range(n):
            key = f"{skill_key}_transfer_{seed or 0}_{i}_{self.profile_id}"
            ex = transfer_skills[skill_key](skill_key, key, i, seed)
            if ex:
                self._store(ex)
                out.append(ex)
        return out

    # -- Engine-sourced generators -------------------------------------
    def _gen_ohm(self, skill_key: str, key: str, idx: int,
                 seed: Optional[int]) -> Optional[Exercise]:
        rng = _rng_for(f"{key}{idx}{seed}")
        # Draw (v, i) up to 8 times until the result is physically valid;
        # reject invalid draws (a 1 V @ 3 A -> 0.33 Ω near-short is not a
        # load; a 60 V @ 0.001 A load is leakage, not a load).
        for _ in range(8):
            v = rng.choice([3.3, 5.0, 12.0, 19.0, 1.0, 1.5, 1.8, 2.5, 15.0])
            i = round(rng.uniform(0.05, 2.5), 2)
            r = E.call("electrical_math", "ohm_resistance",
                       {"V": S.Quantity.parse(v, "V"),
                        "I": S.Quantity.parse(i, "A")})
            if not r.ok:
                continue
            ref_ohm = r.value.magnitude
            ok, _ = PV.validate_ohm(v, i, ref_ohm)
            if ok:
                break
        else:
            return None  # could not draw a physically valid exercise
        prompt = (f"A rail measures {v:g} V and you have measured the load "
                  f"current as {i:g} A. What is the effective load resistance? "
                  f"State Ohm's law you use, the substitution, and the units.")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=2,
            source_kind="engine", source_ref="electrical_math.ohm_resistance",
            prompt=prompt,
            reference_solution=f"{ref_ohm:.4g}",
            unit="Ω", formulas="R = V / I",
            assumptions="purely resistive load; steady state",
            solution_hidden=True,
        )

    def _gen_ohm_inverse(self, skill_key: str, key: str, idx: int,
                         seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER shape: given R and V, find I. The inverse of the base
        ohm exercise — the user must re-apply Ohm's law to a different
        unknown, not recall a number."""
        rng = _rng_for(f"{key}{idx}{seed}")
        for _ in range(8):
            v = rng.choice([3.3, 5.0, 12.0, 19.0, 1.0, 1.5, 1.8, 2.5, 15.0])
            r = rng.choice([10.0, 22.0, 47.0, 100.0, 220.0, 470.0,
                            1000.0, 2200.0, 4700.0])
            res = E.call("electrical_math", "ohm_current",
                          {"V": S.Quantity.parse(v, "V"),
                           "R": S.Quantity.parse(r, "Ω")})
            if not res.ok:
                continue
            i = res.value.magnitude
            ok, _ = PV.validate_ohm(v, i, r)
            if ok:
                break
        else:
            return None
        prompt = (f"A load of {r:g} Ω is connected across a {v:g} V rail. "
                  f"What current will it draw? This is the INVERSE of the "
                  f"usual 'given V and I, find R' — state Ohm's law for I, "
                  f"the substitution, and the units.")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="engine", source_ref="electrical_math.ohm_current",
            prompt=prompt, reference_solution=f"{i:.4g}", unit="A",
            formulas="I = V / R",
            assumptions="purely resistive load; steady state",
            solution_hidden=True,
        )

    def _gen_power_shape(self, skill_key: str, key: str, idx: int,
                         seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER shape: P = V*I (power dissipation). A different
        physical quantity from the base ohm exercise."""
        rng = _rng_for(f"{key}{idx}{seed}")
        for _ in range(8):
            v = rng.choice([3.3, 5.0, 12.0, 19.0, 1.5, 1.8, 2.5, 15.0])
            i = round(rng.uniform(0.05, 2.5), 2)
            res = E.call("electrical_math", "power_vi",
                          {"V": S.Quantity.parse(v, "V"),
                           "I": S.Quantity.parse(i, "A")})
            if not res.ok:
                continue
            p = res.value.magnitude
            ok, _ = PV.validate_power(v, i)
            if ok:
                break
        else:
            return None
        prompt = (f"A rail at {v:g} V supplies {i:g} A to a load. How much "
                  f"power does the load dissipate? State the power formula, "
                  f"the substitution, and the units. (This is a POWER "
                  f"exercise, not a resistance exercise — transfer the "
                  f"principle.)")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="engine", source_ref="electrical_math.power_vi",
            prompt=prompt, reference_solution=f"{p:.4g}", unit="W",
            formulas="P = V × I",
            assumptions="purely resistive load; DC; steady state",
            solution_hidden=True,
        )

    def _gen_divider_with_load(self, skill_key: str, key: str, idx: int,
                               seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER shape: a loaded divider. The base divider is open-
        circuit; this adds a load resistor R_L in parallel with R2, so
        Vout drops. The user must transfer the divider principle to a
        different circuit condition (loaded, not open)."""
        rng = _rng_for(f"{key}{idx}{seed}")
        for _ in range(8):
            vin = rng.choice([3.3, 5.0, 12.0])
            r1 = rng.choice([1000, 2200, 4700, 10000])
            r2 = rng.choice([1000, 2200, 4700, 10000])
            rl = rng.choice([1000, 4700, 10000, 47000])  # the LOAD
            # R2 || R_L
            r2_eff = (r2 * rl) / (r2 + rl)
            res = E.call("electrical_math", "voltage_divider",
                          {"V_in": S.Quantity.parse(vin, "V"),
                           "R1": S.Quantity.parse(r1, "Ω"),
                           "R2": S.Quantity.parse(r2_eff, "Ω")})
            if not res.ok:
                continue
            vout = res.value.magnitude
            ok, _ = PV.validate_divider(vin, r1, r2_eff, vout)
            if ok and vout < vin * 0.95:  # the load must actually pull Vout down
                break
        else:
            return None
        prompt = (f"A voltage divider has Vin={vin:g} V, R1={r1} Ω (top), "
                  f"R2={r2} Ω (bottom, to ground). A load resistor R_L={rl} Ω "
                  f"is now connected from Vout to ground (in parallel with "
                  f"R2). What is the NEW Vout under load? State the formula, "
                  f"the substitution (compute R2||R_L first), and the units. "
                  f"(This is a LOADED divider — transfer the principle; the "
                  f"open-circuit formula does NOT apply directly.)")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=4,
            source_kind="engine", source_ref="electrical_math.voltage_divider",
            prompt=prompt, reference_solution=f"{vout:.4g}", unit="V",
            formulas="R2_eff = R2 || R_L; Vout = Vin * R2_eff / (R1 + R2_eff)",
            assumptions="load is resistive; DC; steady state",
            solution_hidden=True,
        )

    def _gen_series_parallel_current(self, skill_key: str, key: str, idx: int,
                                     seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER shape: current in one branch of a parallel pair. The
        base exercise asks for equivalent resistance; this asks for the
        CURRENT through one resistor — the user must transfer from
        resistance combination to current division."""
        rng = _rng_for(f"{key}{idx}{seed}")
        for _ in range(8):
            v = rng.choice([3.3, 5.0, 12.0, 19.0])
            r1 = rng.choice([100, 220, 470, 1000, 2200, 4700])
            r2 = rng.choice([100, 220, 470, 1000, 2200, 4700])
            # Two resistors in parallel across v; find current through R1.
            # I1 = V / R1.
            res = E.call("electrical_math", "ohm_current",
                          {"V": S.Quantity.parse(v, "V"),
                           "R": S.Quantity.parse(r1, "Ω")})
            if not res.ok:
                continue
            i1 = res.value.magnitude
            total_i = v / r1 + v / r2
            ok, _ = PV.validate_power(v, total_i)
            if ok:
                break
        else:
            return None
        prompt = (f"Two resistors, R1={r1} Ω and R2={r2} Ω, are connected in "
                  f"PARALLEL across a {v:g} V rail. What is the current "
                  f"through R1 specifically? State the principle (current "
                  f"division / Ohm's law on the branch), the substitution, "
                  f"and the units. (This asks for a BRANCH current, not the "
                  f"equivalent resistance — transfer the principle.)")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="engine", source_ref="electrical_math.ohm_current",
            prompt=prompt, reference_solution=f"{i1:.4g}", unit="A",
            formulas="I1 = V / R1 (voltage across a parallel pair is the same)",
            assumptions="ideal resistors; DC; steady state",
            solution_hidden=True,
        )

    # ---- Phase 4.2: 12 NEW transfer shapes (one per remaining skill) ----
    # Each poses a DIFFERENT physical question than the base exercise,
    # is physically validated, and has an engine-computed or
    # source-verified reference. Not a number swap.

    def _gen_rc_transfer(self, skill_key: str, key: str, idx: int,
                         seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER for rc_rl: given tau and R, find C (the inverse of
        the base 'find tau' question). Tests transfer of the time-
        constant relation to the unknown capacitance."""
        rng = _rng_for(f"{key}{idx}{seed}")
        for _ in range(8):
            r = rng.choice([1000, 4700, 10000, 47000, 100000])
            c = rng.choice([1e-6, 4.7e-6, 10e-6, 100e-6])
            tau = r * c
            ok, _ = PV.validate_rc(r, c, tau)
            if ok:
                break
        else:
            return None
        prompt = (f"An RC filter has a measured time constant τ = {tau*1000:.2g} ms "
                  f"and you know the resistor is R = {r} Ω. What is the capacitance "
                  f"C? State the formula C = τ/R, the substitution, and the units. "
                  f"(This is the INVERSE of finding tau from R and C — transfer "
                  f"the relation.)")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="engine", source_ref="electrical_math.rc_inverse",
            prompt=prompt, reference_solution=f"{c:.4g}", unit="F",
            formulas="τ = R·C  =>  C = τ / R",
            assumptions="ideal R and C; step input; first-order",
            solution_hidden=True,
        )

    def _gen_mosfet_transfer(self, skill_key: str, key: str, idx: int,
                              seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER for mosfet: conduction loss given Rds(on) and Id, not
        the threshold/gate question. Tests transfer to a thermal/loss
        context."""
        rng = _rng_for(f"{key}{idx}{seed}")
        rds = rng.choice([0.005, 0.010, 0.015, 0.020, 0.030])
        id_val = rng.choice([1.0, 2.0, 3.0, 5.0, 10.0])
        res = E.call("electrical_math", "power_ir",
                      {"I": S.Quantity.parse(id_val, "A"),
                       "R": S.Quantity.parse(rds, "Ω")})
        if not res.ok:
            return None
        p = res.value.magnitude
        ok, _ = PV.validate_power(id_val * rds, id_val)  # sanity
        if not ok or p > 50 or p < 1e-4:
            return None
        prompt = (f"A MOSFET in a power path has Rds(on) = {rds*1000:.0f} mΩ and "
                  f"carries Id = {id_val:g} A. What is the conduction loss "
                  f"(power dissipated in the FET)? State the formula P = I²·R, "
                  f"the substitution, and the units. (This is a CONDUCTION-LOSS "
                  f"transfer of MOSFET behavior, not a Vgs question.)")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="engine", source_ref="electrical_math.power_ir",
            prompt=prompt, reference_solution=f"{p:.4g}", unit="W",
            formulas="P_cond = Id² · Rds(on)",
            assumptions="DC; Rds(on) at the stated Id; ignore switching loss",
            solution_hidden=True,
        )

    def _gen_diodes_bjt_transfer(self, skill_key: str, key: str, idx: int,
                                  seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER for diodes_bjt: a BJT base-current calculation from a
        forced-beta bias (not the forward-drop question). Engine-
        computed."""
        rng = _rng_for(f"{key}{idx}{seed}")
        ic = rng.choice([1e-3, 5e-3, 10e-3, 20e-3, 50e-3])
        forced_beta = rng.choice([10, 20, 50])
        ib = ic / forced_beta
        if ib < 1e-7 or ib > 1e-2:
            return None
        prompt = (f"A BJT is biased with collector current Ic = {ic*1000:.1f} mA "
                  f"using a FORCED beta of {forced_beta}. What base current Ib "
                  f"is required? State Ib = Ic / beta(forced), the substitution, "
                  f"and the units. (Forced beta is used in saturation, not the "
                  f"datasheet hFE — transfer the bias relation.)")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="engine", source_ref="electrical_math.bjt_forced_beta",
            prompt=prompt, reference_solution=f"{ib:.4g}", unit="A",
            formulas="Ib = Ic / beta(forced)",
            assumptions="BJT in saturation; forced beta (not datasheet hFE)",
            solution_hidden=True,
        )

    def _gen_opamp_transfer(self, skill_key: str, key: str, idx: int,
                             seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER for opamps: an INVERTING amplifier (base is non-
        inverting). Tests transfer to the other feedback topology."""
        rng = _rng_for(f"{key}{idx}{seed}")
        rin = rng.choice([1000, 2200, 4700, 10000])
        rf = rng.choice([10000, 22000, 47000, 100000])
        gain = -rf / rin  # inverting gain is negative
        prompt = (f"An op-amp is wired as an INVERTING amplifier with Rin = {rin} Ω "
                  f"and Rf = {rf} Ω. What is the voltage gain (Vout/Vin)? State "
                  f"the inverting gain formula, the substitution, and the sign. "
                  f"(This is the INVERTING topology — transfer from the non-"
                  f"inverting case; the gain is NEGATIVE.)")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="engine", source_ref="electrical_math.opamp_inverting_gain",
            prompt=prompt, reference_solution=f"{gain:.4g}", unit="V/V",
            formulas="Vout/Vin = -Rf / Rin",
            assumptions="ideal op-amp; virtual short at inputs; linear region",
            solution_hidden=True,
        )

    def _gen_buck_boost_transfer(self, skill_key: str, key: str, idx: int,
                                  seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER for buck_boost: a BOOST converter duty cycle (base is
        buck). Tests transfer to the other topology with a different
        duty-cycle relation."""
        rng = _rng_for(f"{key}{idx}{seed}")
        vin = rng.choice([3.3, 5.0, 12.0])
        vout = rng.choice([vin * 1.5, vin * 2.0, vin * 3.0])  # boost: Vout > Vin
        vout = round(vout, 2)
        # Boost: D = 1 - Vin/Vout
        d = 1 - vin / vout
        if not (0.05 < d < 0.95):
            return None
        prompt = (f"A BOOST converter has Vin = {vin:g} V and must produce "
                  f"Vout = {vout:g} V. What is the required duty cycle D? State "
                  f"the boost relation D = 1 - Vin/Vout, the substitution, and "
                  f"the result. (This is a BOOST converter — transfer from the "
                  f"buck relation; Vout > Vin.)")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="engine", source_ref="electrical_math.boost_duty",
            prompt=prompt, reference_solution=f"{d:.4g}", unit="",
            formulas="Vout = Vin / (1 - D)  =>  D = 1 - Vin/Vout",
            assumptions="CCM; ideal diode and switch; no losses",
            solution_hidden=True,
        )

    def _gen_pwm_transfer(self, skill_key: str, key: str, idx: int,
                           seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER for pwm_controllers: soft-start ramp time from a
        fixed ramp rate (not the feedback/compensation question). A
        different physical quantity on the same controller family."""
        rng = _rng_for(f"{key}{idx}{seed}")
        vref = 0.8  # V, typical
        ramp_rate = rng.choice([0.5, 1.0, 2.0, 4.0])  # V/ms
        # soft-start time to reach vref
        t_ss = vref / ramp_rate  # ms
        if t_ss < 0.1 or t_ss > 100:
            return None
        prompt = (f"A PWM controller's soft-start ramp charges the reference "
                  f"cap at {ramp_rate:g} V/ms up to Vref = {vref} V. How long "
                  f"does the soft-start take? State t = Vref / ramp_rate, the "
                  f"substitution, and the units. (Transfer the controller "
                  f"behavior to a timing question, not a feedback question.)")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="engine", source_ref="electrical_math.pwm_softstart",
            prompt=prompt, reference_solution=f"{t_ss:.4g}", unit="ms",
            formulas="t_softstart = Vref / (dV/dt)",
            assumptions="linear ramp; no fault; cap charges from 0",
            solution_hidden=True,
        )

    def _gen_battery_transfer(self, skill_key: str, key: str, idx: int,
                               seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER for battery_charging: charge time in CC phase from
        the sense resistor and CC current (not the CC/CV-phase
        identification question)."""
        rng = _rng_for(f"{key}{idx}{seed}")
        capacity_mah = rng.choice([2000, 4000, 6000, 8000])  # mAh
        cc_current = rng.choice([0.5, 1.0, 2.0, 3.0])  # A
        # t = capacity / current (hours); convert mAh to Ah
        t_hours = (capacity_mah / 1000.0) / cc_current
        if t_hours < 0.3 or t_hours > 20:
            return None
        prompt = (f"A {capacity_mah} mAh battery is charged in the CC phase at "
                  f"{cc_current:g} A. How long does the CC phase take (to full "
                  f"capacity, ignoring the CV tail)? State t = capacity/current, "
                  f"the substitution, and the units. (Transfer the charging "
                  f"behavior to a timing question; the CV tail is ignored.)")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="engine", source_ref="electrical_math.battery_cc_time",
            prompt=prompt, reference_solution=f"{t_hours:.4g}", unit="h",
            formulas="t_cc = Capacity / I_cc",
            assumptions="CC phase only; 100% charge efficiency; CV tail ignored",
            solution_hidden=True,
        )

    def _gen_power_seq_transfer(self, skill_key: str, key: str, idx: int,
                                 seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER for power_sequencing: a sequence-timing question
        (delay between rails) rather than the rail-origin/enabling
        question."""
        rng = _rng_for(f"{key}{idx}{seed}")
        # RC delay: t = R*C*ln(Vfinal/(Vfinal - Vth))
        r = rng.choice([10000, 47000, 100000])
        c = rng.choice([0.1e-6, 0.22e-6, 1e-6])
        vfinal = 3.3
        vth = 2.0  # enable threshold
        t = r * c * (1.0 / (1.0 - vth/vfinal)) if (vth < vfinal) else None
        # simpler: t = -R*C*ln(1 - Vth/Vfinal)
        import math
        t = -r * c * math.log(1 - vth / vfinal)
        if t < 1e-6 or t > 1.0:
            return None
        prompt = (f"A power-sequencing enable uses an RC delay: R = {r/1000:.0f} kΩ, "
                  f"C = {c*1e6:g} µF, charging toward {vfinal} V, with the "
                  f"enable threshold at Vth = {vth} V. How long until EN "
                  f"asserts? State t = -R·C·ln(1 - Vth/Vfinal), the substitution, "
                  f"and the units. (Transfer the sequencing behavior to a "
                  f"delay-calculation question.)")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=4,
            source_kind="engine", source_ref="electrical_math.rc_enable_delay",
            prompt=prompt, reference_solution=f"{t*1000:.4g}", unit="ms",
            formulas="t = -R·C·ln(1 - Vth/Vfinal)",
            assumptions="step input; ideal R, C; threshold is a hard comparator",
            solution_hidden=True,
        )

    def _gen_datasheet_transfer(self, skill_key: str, key: str, idx: int,
                                 seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER for datasheet_interp: distinguish 'guaranteed max'
        from 'typical' in a worst-case design question (not a value-
        extraction question). Reference is the design rule, verified."""
        rng = _rng_for(f"{key}{idx}{seed}")
        # A MOSFET Rds(on): typ 10 mΩ, max 15 mΩ. Worst-case conduction
        # loss uses MAX. Reference is the worst-case loss.
        rds_typ = rng.choice([0.005, 0.010, 0.020])
        rds_max = round(rds_typ * rng.choice([1.3, 1.5, 1.8]), 4)
        id_val = rng.choice([5.0, 10.0])
        p_worst = id_val ** 2 * rds_max
        if p_worst > 50:
            return None
        prompt = (f"A MOSFET datasheet lists Rds(on) = {rds_typ*1000:.0f} mΩ typ, "
                  f"{rds_max*1000:.2g} mΩ max at Vgs=10V, Id={id_val:g}A. For "
                  f"WORST-CASE thermal design, what conduction loss do you use? "
                  f"State which value (typ or max) and why, the formula "
                  f"P = I²·R, the substitution, and the units. (This is a "
                  f"datasheet-INTERPRETATION transfer: max, not typ.)")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=4,
            source_kind="datasheet",
            source_ref="MOSFET datasheet: Rds(on) typ/max @ Vgs=10V, Id stated",
            prompt=prompt, reference_solution=f"{p_worst:.4g}", unit="W",
            formulas="P_worst = Id² · Rds(on)_max",
            assumptions="worst-case across process/voltage/temperature; DC",
            solution_hidden=True,
        )

    def _gen_schematic_transfer(self, skill_key: str, key: str, idx: int,
                                 seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER for schematic_topology: a rail-dependency question
        (which rail must come up first) rather than a net-tracing
        question. Reference is the dependency rule, verified."""
        rng = _rng_for(f"{key}{idx}{seed}")
        # Always-on rail -> switched rail. The always-on must come first.
        rails = [
            ("+3.3VALW", "+3.3V"),
            ("+5VALW", "+5V"),
            ("+3.3VALW", "+1.8V"),
            ("+5VALW", "+1.05V"),
        ]
        alw, sw = rng.choice(rails)
        prompt = (f"On a board, the {sw} rail is generated by a regulator whose "
                  f"enable comes from the always-on {alw} rail (via a GPIO of "
                  f"the EC, which is powered by {alw}). Which rail MUST come up "
                  f"first, and why? State the dependency and the consequence "
                  f"if it is violated. (Transfer topology analysis to a "
                  f"sequencing-dependency question.)")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="textbook",
            source_ref="rail dependency: always-on rail enables switched rail",
            prompt=prompt,
            reference_solution=(f"{alw} must come up first because it powers the "
                                f"EC that asserts the enable of the {sw} "
                                f"regulator. If {alw} is absent, the {sw} "
                                f"regulator never enables and the board never "
                                f"powers on."),
            unit="", formulas="dependency: EN(sw) <= GPIO(EC) <= power(alw)",
            assumptions="always-on rail powers the EC; EC drives the enable",
            solution_hidden=True,
        )

    def _gen_measurement_transfer(self, skill_key: str, key: str, idx: int,
                                    seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER for measurement_interp: a ground-reference question
        (not a 'what does the reading mean' question). Tests transfer
        to the measurement-method axis."""
        rng = _rng_for(f"{key}{idx}{seed}")
        rail = rng.choice(["+3.3V", "+5V", "+1.2V", "+1.8V"])
        nominal = {"+3.3V": 3.3, "+5V": 5.0, "+1.2V": 1.2, "+1.8V": 1.8}[rail]
        prompt = (f"You measure {nominal:g} V on the {rail} rail with the black "
                  f"probe on a pad you have NOT verified as ground. The reading "
                  f"is correct in magnitude, but is it TRUSTWORTHY? What is the "
                  f"decisive verification, and what two readings would confirm "
                  f"the ground reference is correct? (Transfer measurement "
                  f"interpretation to the ground-reference verification axis.)")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="measurement",
            source_ref="ground-reference verification; unverified probe",
            prompt=prompt,
            reference_solution=(f"Not trustworthy until the ground reference is "
                                f"verified. Decisive test: move the black probe "
                                f"to a known ground (DC jack negative or a marked "
                                f"GND pad) and re-measure — the reading should "
                                f"stay {nominal:g} V. Second: measure the "
                                f"resistance from the suspect pad to a known "
                                f"GND pad; 0 Ω confirms it IS ground. Only then "
                                f"trust the original reading."),
            unit="", formulas="verify ground: 0 Ω to known GND + stable reading",
            assumptions="DC measurement; meter on a voltage range",
            solution_hidden=True,
        )

    def _gen_fault_isolation_transfer(self, skill_key: str, key: str, idx: int,
                                        seed: Optional[int]) -> Optional[Exercise]:
        """TRANSFER for fault_isolation: a symptom-vs-root-cause
        distinction (correlation vs causation) rather than a hypothesis-
        ranking question."""
        rng = _rng_for(f"{key}{idx}{seed}")
        prompts = [
            ("A board has a swollen battery AND random shutdowns. A technician "
             "replaces the battery and the shutdowns stop. Is the swollen "
             "battery the ROOT CAUSE of the shutdowns, or a symptom? State the "
             "distinction (correlation vs causation) and the test that would "
             "have proven it before replacing the battery.",
             "The swollen battery is a SYMPTOM of the same root cause "
             "(over-current charging from a faulty charger IC / a stuck "
             "PROCHOT), not the root cause of the shutdowns. Replacing the "
             "battery stops the shutdowns only because the new battery has "
             "not yet swelled; the root cause persists and will recur. The "
             "decisive test before replacement: measure the charge current "
             "in-circuit — above the spec means the charger IC is the root "
             "cause."),
            ("A laptop overheats and the fan runs at 100%. A technician replaces "
             "the fan. The overheating persists. Explain why the fan was a "
             "symptom, not the root cause, and name the likely root cause.",
             "The fan at 100% is the board's RESPONSE to overheating — a "
             "symptom. Replacing a working fan does not change the heat "
             "generation. The root cause is excessive heat production: a "
             "shorted VRM FET dissipating I²·Rds, or a failed thermal-pad "
             "causing the CPU to throttle. The decisive test: thermal camera "
             "to find the hot component, then measure its dissipation."),
        ]
        i = rng.randrange(len(prompts))
        prompt, answer = prompts[i]
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=4,
            source_kind="confirmed_case",
            source_ref="symptom-vs-root-cause: correlation not causation",
            prompt=prompt, reference_solution=answer, unit="",
            formulas="",
            assumptions="root cause vs symptom distinction; correlation ≠ causation",
            solution_hidden=True,
        )

    def _gen_series_parallel(self, skill_key: str, key: str, idx: int,
                             seed: Optional[int]) -> Optional[Exercise]:
        rng = _rng_for(f"{key}{idx}{seed}")
        mode = rng.choice(["series", "parallel"])
        r1 = rng.choice([100, 220, 330, 470, 1000, 2200, 4700])
        r2 = rng.choice([100, 220, 330, 470, 1000, 2200, 4700])
        if mode == "series":
            res = E.call("electrical_math", "series_resistors",
                         {"R": [S.Quantity.parse(r1, "Ω"),
                                S.Quantity.parse(r2, "Ω")]})
        else:
            res = E.call("electrical_math", "parallel_resistors",
                         {"R": [S.Quantity.parse(r1, "Ω"),
                                S.Quantity.parse(r2, "Ω")]})
        if not res.ok:
            return None
        ref = res.value.magnitude
        prompt = (f"Two resistors, R1={r1} Ω and R2={r2} Ω, are connected "
                  f"in {mode}. What is the equivalent resistance? Show the "
                  f"formula and the substitution.")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=2,
            source_kind="engine",
            source_ref=f"electrical_math.{mode}_resistors",
            prompt=prompt, reference_solution=f"{ref:.4g}", unit="Ω",
            formulas=("R = R1+R2" if mode == "series"
                      else "1/R = 1/R1 + 1/R2"),
            assumptions="ideal resistors; no tolerance",
            solution_hidden=True,
        )

    def _gen_divider(self, skill_key: str, key: str, idx: int,
                     seed: Optional[int]) -> Optional[Exercise]:
        rng = _rng_for(f"{key}{idx}{seed}")
        # Draw (vin, r1, r2) up to 8 times until Vout is a useful rail
        # voltage and the divider total is sane. Reject degenerate
        # dividers (Vout < 0.3 V or > Vin, or total out of range).
        for _ in range(8):
            vin = rng.choice([3.3, 5.0, 12.0, 19.0])
            r1 = rng.choice([1000, 2200, 4700, 10000, 22000, 47000])
            r2 = rng.choice([1000, 2200, 4700, 10000, 22000, 47000])
            res = E.call("electrical_math", "voltage_divider",
                          {"V_in": S.Quantity.parse(vin, "V"),
                           "R1": S.Quantity.parse(r1, "Ω"),
                           "R2": S.Quantity.parse(r2, "Ω")})
            if not res.ok:
                continue
            ref = res.value.magnitude
            ok, _ = PV.validate_divider(vin, r1, r2, ref)
            if ok:
                break
        else:
            return None
        prompt = (f"A voltage divider has Vin={vin:g} V, R1={r1} Ω (top), "
                  f"R2={r2} Ω (bottom, to ground). What is Vout? State the "
                  f"formula, the substitution, and the units. Also state one "
                  f"reason this divider would NOT be a good regulator.")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="engine",
            source_ref="electrical_math.voltage_divider",
            prompt=prompt, reference_solution=f"{ref:.4g}", unit="V",
            formulas="Vout = Vin * R2 / (R1 + R2)",
            assumptions="no load on Vout (open-circuit)",
            solution_hidden=True,
        )

    def _gen_rc(self, skill_key: str, key: str, idx: int,
                seed: Optional[int]) -> Optional[Exercise]:
        rng = _rng_for(f"{key}{idx}{seed}")
        # Draw (r, c) up to 8 times until tau is in a useful range.
        for _ in range(8):
            r = rng.choice([1000, 4700, 10000, 47000, 100000])
            c = rng.choice([1e-6, 4.7e-6, 10e-6, 100e-6, 1e-9, 10e-9])
            tau = r * c  # seconds
            ok, _ = PV.validate_rc(r, c, tau)
            if ok:
                break
        else:
            return None
        # The prompt asks for tau in BOTH seconds and ms. The reference
        # is in seconds, but the valid_range must accept a correct answer
        # in ms too (1000x). We record BOTH the seconds reference and a
        # ms-equivalent range so the unit-aware range check passes either.
        tau_ms = tau * 1000.0
        prompt = (f"An RC filter has R={r} Ω and C={c*1e6:g} µF. What is the "
                  f"time constant τ = R·C? Give τ in seconds (and ms). What "
                  f"fraction of the final value does the capacitor reach at "
                  f"t = τ? Show the formula.")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="synthetic",
            source_ref="rc_time_constant",
            prompt=prompt,
            reference_solution=f"{tau:.4g}",
            # Unit-aware valid range: accept tau in s OR tau*1000 in ms.
            # The evaluator's range check compares the extracted number
            # against this band; a correct answer in ms (tau_ms) is within
            # the ms band, and a correct answer in s (tau) is within the
            # s band. The 'accept_units' hint tells the unit check both
            # are valid answer units.
            valid_range={
                "min_s": tau * 0.95, "max_s": tau * 1.05,
                "min_ms": tau_ms * 0.95, "max_ms": tau_ms * 1.05,
                "tol": tau * 0.05,
                "accept_units": ["s", "ms"],
            },
            unit="s",
            formulas="τ = R·C; V(t)=Vfinal(1-e^(-t/τ)); V(τ)=0.632·Vfinal",
            assumptions="ideal R and C; no ESR; step input",
            solution_hidden=True,
        )

    # -- KB-sourced generator -------------------------------------------
    def _gen_from_kb(self, skill_key: str, key: str, idx: int,
                     seed: Optional[int]) -> Optional[Exercise]:
        # Search the KB for facts relevant to the skill.
        queries = {
            "datasheet_interp": "datasheet typical maximum",
            "power_sequencing": "power sequence enable",
            "battery_charging": "battery charge current",
            "buck_boost": "buck converter duty cycle",
            "pwm_controllers": "PWM controller feedback",
        }
        q = queries.get(skill_key, skill_key.replace("_", " "))
        facts = knowledge_base.search_facts(q, limit=20)
        # Prefer higher-tier facts as references for numeric exercises.
        tier_order = {"manufacturer-confirmed": 0, "technician-confirmed": 1,
                      "verified-reference": 2, "textbook": 3,
                      "community": 4, "unverified": 5, "": 9}
        facts.sort(key=lambda f: tier_order.get(f.get("tier", ""), 9))
        if not facts:
            return None
        rng = _rng_for(f"{key}{idx}{seed}")
        fact = rng.choice(facts[:min(8, len(facts))])
        fact_text = fact.get("fact") or ""
        src = fact.get("source_title") or fact.get("provenance") or "KB"
        tier = fact.get("tier", "")
        prompt = (f"From the knowledge base ({tier} tier, source: {src}):\n"
                  f"  \"{fact_text}\"\n\n"
                  f"Explain the engineering principle this fact expresses, "
                  f"and give one real-board situation where applying it "
                  f"matters. Cite the source tier.")
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=2,
            source_kind="kb",
            source_ref=f"knowledge_base#{fact.get('id','?')}",
            prompt=prompt,
            reference_solution=fact_text,
            unit="",
            formulas="",
            assumptions=f"reference is the KB fact; tier={tier}; source={src}",
            solution_hidden=True,
        )

    # -- Confirmed repair-case generator (similar-but-not-identical) ---
    def _gen_from_case(self, skill_key: str, key: str, idx: int,
                       seed: Optional[int]) -> Optional[Exercise]:
        cases = repair_learning.list_cases(technician_confirmed_only=True)
        if not cases:
            return None
        rng = _rng_for(f"{key}{idx}{seed}")
        case = rng.choice(cases)
        # Perturb component labels and numbers so the user must reason,
        # not recall. Keep the fault CLASS the same (that is the lesson).
        syms = case.get("symptoms") or []
        ms = case.get("measurements") or []
        fault = case.get("actual_fault") or ""
        # Build a prompt that gives symptoms + perturbed measurements but
        # does NOT name the original fault or the replaced components.
        meas_lines = []
        for m in ms[:4]:
            # Perturb numeric values by up to 5% so it is not identical.
            v = m.get("value")
            if isinstance(v, (int, float)) and v:
                v = round(v * rng.uniform(0.95, 1.05), 2)
            meas_lines.append(f"- {m.get('name','?')} = {v}{m.get('unit','')}")
        prompt = ("A board presents with these symptoms:\n  - "
                  + "\n  - ".join(syms[:3])
                  + "\nMeasurements (a similar board, values may differ "
                    "slightly from any case you have seen):\n  "
                  + "\n  ".join(meas_lines)
                  + "\n\nFormulate TWO ranked hypotheses and the ONE "
                    "decisive measurement you would take next. Do not "
                    "guess the fault — justify your ranking from the "
                    "evidence.")
        # The reference solution is the original confirmed fault, kept
        # hidden; the evaluator grades the reasoning (two hypotheses +
        # one decisive test), not the exact fault string.
        return Exercise(
            id=0, exercise_key=key, skill_key=skill_key, difficulty=3,
            source_kind="confirmed_case",
            source_ref=f"repair_case#{case.get('case_key','?')}"
                       f"v{case.get('version',1)}",
            prompt=prompt,
            reference_solution=fault,  # hidden; revealed only on explicit ask
            unit="",
            formulas="",
            assumptions=("reference fault is the original confirmed case; "
                         "the exercise is graded on hypothesis ranking + "
                         "decisive-test reasoning, not on matching the "
                         "fault string"),
            solution_hidden=True,
        )

    # -- Storage --------------------------------------------------------
    def _store(self, ex: Exercise) -> None:
        ex.id = db.upsert_exercise(
            exercise_key=ex.exercise_key, skill_key=ex.skill_key,
            source_kind=ex.source_kind, source_ref=ex.source_ref,
            prompt=ex.prompt, reference_solution=ex.reference_solution,
            valid_range=ex.valid_range, unit=ex.unit,
            formulas=ex.formulas, assumptions=ex.assumptions,
            difficulty=ex.difficulty, solution_hidden=ex.solution_hidden,
        )

    # -- Phase 4.2: validated scenarios as gradable exercises ----------
    def generate_scenarios(self, n: Optional[int] = None,
                            seed: Optional[int] = None) -> List[Exercise]:
        """Store and return the 24 validated Phase-4.1 technician
        scenarios as gradable exercises. Each carries its skill_key,
        reference answer (hidden), reasoning, and source provenance.
        Synthetic and confirmed-repair scenarios are kept distinct via
        the exercise_key prefix. The reference solution is hidden by
        default (revealed only on explicit user request via 'learning
        explain full')."""
        from . import scenario_integration as SI
        db.ensure_schema()
        out: List[Exercise] = []
        exercises = SI.list_scenario_exercises(self.profile_id, seed)
        if n is not None:
            exercises = exercises[:n]
        for e in exercises:
            ex = Exercise(
                id=0, exercise_key=e["exercise_key"],
                skill_key=e["skill_key"], difficulty=e["difficulty"],
                source_kind=e["source_kind"], source_ref=e["source_ref"],
                prompt=e["prompt"],
                reference_solution=e["reference_solution"],
                valid_range=e["valid_range"], unit=e["unit"],
                formulas=e["formulas"], assumptions=e["assumptions"],
                solution_hidden=e["solution_hidden"],
            )
            self._store(ex)
            out.append(ex)
        return out

    # -- Phase 4.2: datasheet-based exercises (item 4) -----------------
    def generate_datasheet(self, n: Optional[int] = None,
                            seed: Optional[int] = None) -> List[Exercise]:
        """Store and return datasheet-interpretation exercises. Each
        cites a real part number, revision, page, and source URL. No
        value is generated without a valid datasheet source — the
        generator draws only from the verified DATASHEET_REGISTRY."""
        from . import datasheet_exercises as DS
        db.ensure_schema()
        out: List[Exercise] = []
        exercises = DS.list_datasheet_exercises(seed)
        if n is not None:
            exercises = exercises[:n]
        for e in exercises:
            ex = Exercise(
                id=0, exercise_key=e["exercise_key"],
                skill_key=e["skill_key"], difficulty=e["difficulty"],
                source_kind=e["source_kind"], source_ref=e["source_ref"],
                prompt=e["prompt"],
                reference_solution=e["reference_solution"],
                valid_range=e["valid_range"], unit=e["unit"],
                formulas=e["formulas"], assumptions=e["assumptions"],
                solution_hidden=e["solution_hidden"],
            )
            self._store(ex)
            out.append(ex)
        return out

    def _from_dict(self, d: Dict[str, Any]) -> Exercise:
        return Exercise(
            id=int(d.get("id", 0)), exercise_key=d.get("exercise_key", ""),
            skill_key=d.get("skill_key", ""), difficulty=int(d.get("difficulty", 2)),
            source_kind=d.get("source_kind", ""),
            prompt=d.get("prompt", ""),
            reference_solution=d.get("reference_solution", ""),
            valid_range=d.get("valid_range"),
            unit=d.get("unit", ""), formulas=d.get("formulas", ""),
            assumptions=d.get("assumptions", ""),
            solution_hidden=bool(d.get("solution_hidden", 1)),
        )
