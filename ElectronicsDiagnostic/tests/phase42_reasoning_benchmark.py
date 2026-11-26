"""Phase 4.2 — INDEPENDENT reasoning benchmark.

>= 50 reasoning tests, independent of the dev set. Covers multi-step
reasoning, correlation vs causation, datasheet value-class, hidden
assumptions, measurement misinterpretation, wrong units, no-reasoning.
"""
from __future__ import annotations


MULTI_STEP_EX = {
    "id": 0, "exercise_key": "ind42_multistep",
    "skill_key": "fault_isolation", "difficulty": 4,
    "source_kind": "confirmed_case",
    "source_ref": "ind42 multi-step",
    "prompt": ("A board has a shorted +5VALW rail. Describe TWO ranked "
               "hypotheses for the fault location and the ONE decisive "
               "measurement you would take next. Number your steps."),
    "reference_solution": ("H1: a shorted bulk cap on +5VALW; H2: a shorted "
                          "load IC. Decisive test: inject 1 V current-limited "
                          "and locate the hot component."),
    "valid_range": None, "unit": "", "formulas": "",
    "assumptions": "two ranked hypotheses; multi-step",
    "solution_hidden": True,
}

CC_EX = {
    "id": 0, "exercise_key": "ind42_cc",
    "skill_key": "fault_isolation", "difficulty": 4,
    "source_kind": "confirmed_case",
    "source_ref": "ind42 correlation-causation",
    "prompt": ("A laptop overheats and the fan runs at 100%. A technician "
               "replaces the fan but the overheating persists. Explain why "
               "the fan was a symptom, not the root cause, and name the "
               "likely root cause."),
    "reference_solution": ("The fan is a symptom; the root cause is "
                          "excessive heat production."),
    "valid_range": None, "unit": "", "formulas": "",
    "assumptions": "root cause vs symptom; correlation ≠ causation",
    "solution_hidden": True,
}

DS_ABSMAX_EX = {
    "id": 0, "exercise_key": "ind42_ds_absmax",
    "skill_key": "datasheet_interp", "difficulty": 3,
    "source_kind": "datasheet", "source_ref": "ind42 abs-max",
    "prompt": ("From the AO3400A datasheet, the Absolute Maximum Vds is "
               "30 V. A circuit operates at 29 V. Is this safe?"),
    "reference_solution": "No. 30 V is the absolute max (stress limit).",
    "valid_range": None, "unit": "", "formulas": "",
    "assumptions": "datasheet value class: abs_max",
    "solution_hidden": True,
}

DS_TYP_EX = {
    "id": 0, "exercise_key": "ind42_ds_typ",
    "skill_key": "datasheet_interp", "difficulty": 3,
    "source_kind": "datasheet", "source_ref": "ind42 typical",
    "prompt": ("From the TP4056 datasheet, the typical charge current is "
               "1 A. Is this safe for worst-case design?"),
    "reference_solution": "No. Typical is not guaranteed; use the guaranteed min/max.",
    "valid_range": None, "unit": "", "formulas": "",
    "assumptions": "datasheet value class: typical",
    "solution_hidden": True,
}

DS_GUAR_EX = {
    "id": 0, "exercise_key": "ind42_ds_guar",
    "skill_key": "datasheet_interp", "difficulty": 4,
    "source_kind": "datasheet", "source_ref": "ind42 guaranteed",
    "prompt": ("From the AO3400A datasheet, the GUARANTEED Rds(on) max is "
               "40 mΩ. For worst-case thermal design, which value?"),
    "reference_solution": "Use the guaranteed max (40 mΩ).",
    "valid_range": None, "unit": "", "formulas": "",
    "assumptions": "datasheet value class: guaranteed",
    "solution_hidden": True,
}

OHM_EX = {
    "id": 0, "exercise_key": "ind42_ohm",
    "skill_key": "ohm_kirchhoff", "difficulty": 2,
    "source_kind": "engine", "source_ref": "ind42 ohm",
    "prompt": "A rail measures 5 V and the load current is 2 A. What is R?",
    "reference_solution": "2.5", "valid_range": None, "unit": "Ω",
    "formulas": "R = V / I", "assumptions": "", "solution_hidden": True,
}

MM_EX = {
    "id": 0, "exercise_key": "ind42_mm",
    "skill_key": "measurement_interp", "difficulty": 3,
    "source_kind": "measurement", "source_ref": "ind42 mm",
    "prompt": "You read 0 V on the +3.3V rail. What does it mean?",
    "reference_solution": "needs ground ref",
    "valid_range": None, "unit": "", "formulas": "",
    "assumptions": "", "solution_hidden": True,
}

MOSFET_EX = {
    "id": 0, "exercise_key": "ind42_mosfet",
    "skill_key": "mosfet", "difficulty": 3,
    "source_kind": "engine", "source_ref": "ind42 mosfet",
    "prompt": "Rds=0.02Ω, Id=10A. What is conduction loss?",
    "reference_solution": "2", "valid_range": None, "unit": "W",
    "formulas": "P = I²·R", "assumptions": "", "solution_hidden": True,
}

BOOST_EX = {
    "id": 0, "exercise_key": "ind42_boost",
    "skill_key": "buck_boost", "difficulty": 3,
    "source_kind": "engine", "source_ref": "ind42 boost",
    "prompt": "Boost: Vin=3.3, Vout=4.95. What is D?",
    "reference_solution": "0.3333", "valid_range": None, "unit": "",
    "formulas": "D = 1 - Vin/Vout", "assumptions": "", "solution_hidden": True,
}


# (case_id, exercise, response, expected_correct). Each is a simple 4-tuple.
REASONING_CASES = [
    # Multi-step (13)
    ("MS1", MULTI_STEP_EX, "H1: the +5VALW bulk cap is shorted. H2: a load IC on +5VALW is shorted. The decisive measurement: inject 1 V current-limited and locate the hot component. Therefore the cap is most likely.", True),
    ("MS2", MULTI_STEP_EX, "First, H1 is a shorted cap; second, H2 is a shorted load. The decisive test is current-limited injection + thermal. Therefore cap is likely.", True),
    ("MS3", MULTI_STEP_EX, "It is a shorted cap. Therefore replace the cap.", False),
    ("MS4", MULTI_STEP_EX, "Replace the cap. Therefore it works.", False),
    ("MS5", MULTI_STEP_EX, "Step 1: H1 = shorted cap. Step 2: H2 = shorted IC. Step 3: decisive test = thermal. Therefore cap likely.", True),
    ("MS6", MULTI_STEP_EX, "Step 1: H1 = shorted cap on +5VALW. Step 2: H2 = shorted load IC. The decisive test is current-limited injection + thermal imaging. Therefore H1 is most likely.", True),
    ("MS7", MULTI_STEP_EX, "The cap is bad. Therefore replace it.", False),
    ("MS8", MULTI_STEP_EX, "H1: shorted cap; H2: shorted load. Therefore inject current and find the hot part.", True),
    ("MS9", MULTI_STEP_EX, "Replace the cap and it works. Therefore done.", False),
    ("MS10", MULTI_STEP_EX, "First, the cap is likely shorted. Second, a load IC could be shorted. Therefore use a thermal camera to decide.", True),
    ("MS11", MULTI_STEP_EX, "Just replace the cap. Therefore done.", False),
    ("MS12", MULTI_STEP_EX, "H1 is a shorted cap; H2 is a shorted IC. The decisive measurement is a thermal scan after current injection. Therefore H1.", True),
    ("MS13", MULTI_STEP_EX, "It is a short. Therefore replace the cap.", False),

    # Correlation/causation (13)
    ("CC1", CC_EX, "The fan is a SYMPTOM (the board's response to heat), not the root cause. The root cause is excessive heat production (a shorted VRM FET or failed thermal pad). Therefore the fan replacement did not fix it.", True),
    ("CC2", CC_EX, "The fan at 100% is a symptom, not the root cause. The root cause is a shorted VRM FET dissipating excess power. Therefore replacing the fan was wrong.", True),
    ("CC3", CC_EX, "The fan is the root cause. Therefore replace it.", False),
    ("CC4", CC_EX, "It is a heat issue. Therefore replace the fan.", False),
    ("CC5", CC_EX, "The swollen battery is a symptom; the actual fault is the stuck PROCHOT. Therefore PROCHOT is the root cause.", True),
    ("CC6", CC_EX, "The fan is a symptom of overheating, not the cause. The actual root cause is a shorted VRM FET. Therefore the fan replacement was wrong.", True),
    ("CC7", CC_EX, "Replace the fan to fix overheating. Therefore fan.", False),
    ("CC8", CC_EX, "The fan at 100% is a symptom; the root cause is excess heat from a failed thermal pad. Therefore fix the pad.", True),
    ("CC9", CC_EX, "The fan is the root cause. Therefore replace it.", False),
    ("CC10", CC_EX, "The fan is a symptom, not the root cause. The root cause is a shorted FET dissipating I²·Rds. Therefore fix the FET.", True),
    ("CC11", CC_EX, "The fan is bad. Therefore replace it.", False),
    ("CC12", CC_EX, "Symptom: fan at 100%. Root cause: shorted VRM. Therefore the fan was a symptom, not the cause.", True),
    ("CC13", CC_EX, "Replace the fan. Therefore it works.", False),

    # Datasheet value-class (23)
    ("DS1", DS_ABSMAX_EX, "No. 30 V is the ABSOLUTE MAXIMUM, a stress limit, not an operating point. Operating at 29 V is at the limit and not guaranteed. Therefore it is not safe.", True),
    ("DS2", DS_ABSMAX_EX, "Yes it is safe. The 30V is fine. Therefore it works.", False),
    ("DS3", DS_ABSMAX_EX, "No, it is not safe. The absolute maximum is a stress limit. Therefore design within recommended operating conditions.", True),
    ("DS4", DS_TYP_EX, "No. The typical 1 A is representative, not guaranteed. For worst-case, use the guaranteed min/max. Therefore do not design to the typical.", True),
    ("DS5", DS_TYP_EX, "Yes, use the typical. Therefore 1 A is fine.", False),
    ("DS6", DS_TYP_EX, "No. Typical is not guaranteed; the worst case is not bounded by the typical. Therefore use the guaranteed min/max.", True),
    ("DS7", DS_GUAR_EX, "Use the GUARANTEED MAX (40 mΩ), not the typical, for worst-case design. The max is the worst-case across process/voltage/temperature. Therefore use 40 mΩ.", True),
    ("DS8", DS_GUAR_EX, "Use the typical. Therefore the typical is fine.", False),
    ("DS9", DS_GUAR_EX, "For worst-case, use the max (40 mΩ). Therefore the max is the worst-case.", True),
    ("DS10", DS_ABSMAX_EX, "No. The absolute max is a stress limit. Therefore not safe.", True),
    ("DS11", DS_ABSMAX_EX, "Yes acceptable. Therefore safe.", False),
    ("DS12", DS_TYP_EX, "No, the typical is not guaranteed. Therefore use the guaranteed.", True),
    ("DS13", DS_GUAR_EX, "Use the max (40 mΩ). The guaranteed max is the worst case. Therefore use 40 mΩ.", True),
    ("DS14", DS_GUAR_EX, "It is fine. Therefore use typical.", False),
    ("DS15", DS_ABSMAX_EX, "No. Absolute max is a stress limit, not an operating point. Therefore 29V is not safe.", True),
    ("DS16", DS_ABSMAX_EX, "No. Absolute max is a stress limit. Therefore 29 V is not safe for continuous operation.", True),
    ("DS17", DS_TYP_EX, "No, the typical is not guaranteed. Therefore use the guaranteed min/max for worst-case.", True),
    ("DS18", DS_GUAR_EX, "Use the guaranteed max (40 mΩ). The max is the worst case. Therefore 40 mΩ.", True),
    ("DS19", DS_ABSMAX_EX, "Yes, 29V is fine. Therefore safe.", False),
    ("DS20", DS_TYP_EX, "Yes, use the typical 1 A. Therefore 1 A.", False),
    ("DS21", DS_GUAR_EX, "Use the typical for design. Therefore typical.", False),
    ("DS22", DS_ABSMAX_EX, "No, it is a stress limit. Therefore not safe.", True),
    ("DS23", DS_TYP_EX, "No, typical is not guaranteed. Therefore use max.", True),

    # Hidden assumptions (3)
    ("HA1", OHM_EX, "Assume the rail is exactly 5.0V. R = V/I = 5/2 = 2.5 Ω. Therefore 2.5 Ω.", False),
    ("HA2", OHM_EX, "Let R = 5. R = V/I = 5/2 = 2.5 Ω. Therefore 2.5 Ω.", False),
    ("HA3", OHM_EX, "Using Ohm's law R = V/I, with V=5 V and I=2 A, so R = 5/2 = 2.5 Ω. Therefore 2.5 Ω.", True),

    # Measurement misinterpretation (2)
    ("MM1", MM_EX, "The 0 V reading means no voltage present, so the rail is dead. Therefore no power.", False),
    ("MM2", MM_EX, "First, verify the ground reference. A 0 V reading means no voltage relative to ground only if ground is verified. Therefore verify ground first.", True),

    # Wrong units (1)
    ("WU1", MOSFET_EX, "Using P = Id²·Rds, with Id=10A and Rds=0.02Ω, so P = 100·0.02 = 2 V. Therefore 2 V.", False),

    # No reasoning (1)
    ("NR1", BOOST_EX, "0.3333", False),
]
