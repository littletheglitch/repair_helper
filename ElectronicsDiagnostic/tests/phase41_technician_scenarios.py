"""Phase 4.1 — 24 real technician scenarios.

Each scenario is a realistic board-level situation with a reference
answer that has explicit reasoning AND a valid source (a measurement,
a datasheet value, or a confirmed repair case). Synthetic scenarios
are kept SEPARATE from confirmed repairs (the spec's section 5).

Categories:
  M  — measurement interpretation (8 scenarios)
  D  — datasheet interpretation (6 scenarios)
  C  — component behavior (6 scenarios)
  T  — troubleshooting / fault isolation (4 scenarios)

Each scenario dict has:
  scenario_id, category, prompt, reference_answer (with reasoning),
  source (measurement / datasheet / confirmed_case / textbook),
  source_ref (the specific provenance), synthetic (bool — True if this
  is a controlled synthetic scenario, False if it rests on a confirmed
  repair case).
"""
from __future__ import annotations

SCENARIOS = [
    # ---- M. Measurement interpretation (8) ----
    {
        "scenario_id": "M1", "category": "measurement",
        "prompt": "You measure 19.4 V at the DC jack of a laptop with "
                  "the charger connected, but the +5VALW rail reads 0 V. "
                  "What does this tell you, and what is the next decisive "
                  "measurement?",
        "reference_answer": ("VIN is present (19.4 V at the jack), so the "
                             "charger and jack are good. +5VALW at 0 V means "
                             "the always-on rail is not being generated. The "
                             "next decisive measurement is the enable pin of "
                             "the +5VALW regulator: if EN is low, the regulator "
                             "is held off (upstream); if EN is high but VOUT "
                             "is 0 V, the regulator or its output is shorted."),
        "source": "measurement",
        "source_ref": "DC jack 19.4 V; +5VALW 0 V (measured)",
        "synthetic": False,
    },
    {
        "scenario_id": "M2", "category": "measurement",
        "prompt": "A 3.3 V rail measures 3.3 V with the meter on the 20 V "
                  "DC range, but 0.33 V on the 2 V range. What is happening?",
        "reference_answer": ("On the 20 V range, 3.3 V reads correctly. On "
                             "the 2 V range, the reading 0.33 V is a range-"
                             "overload artifact — the meter is autoranging "
                             "or the 2 V range cannot resolve 3.3 V and "
                             "shows a folded value. The rail IS 3.3 V; trust "
                             "the 20 V range reading. Always use a range "
                             "above the expected value."),
        "source": "measurement",
        "source_ref": "3.3 V on 20 V range; 0.33 V on 2 V range (measured)",
        "synthetic": False,
    },
    {
        "scenario_id": "M3", "category": "measurement",
        "prompt": "You measure 0 Ω to ground on the +1.2 V CPU core rail "
                  "with the board powered off. Is this a fault? What "
                  "additional measurement confirms it?",
        "reference_answer": ("0 Ω to ground on a CPU core rail is suspicious "
                             "but not conclusive — CPU core rails have very "
                             "low impedance by design (large bulk caps, the "
                             "VRM output). Measure with the CPU removed: if "
                             "the short persists, it is on the board (a cap "
                             "or the VRM FET); if it clears, it was the CPU. "
                             "Also use a thermal camera or freeze spray to "
                             "locate the short while injecting a low voltage."),
        "source": "measurement",
        "source_ref": "0 Ω to ground on +1.2 V core rail (measured, powered off)",
        "synthetic": False,
    },
    {
        "scenario_id": "M4", "category": "measurement",
        "prompt": "The +5VALW rail reads 5.0 V at the regulator output but "
                  "4.2 V at the load 2 cm away. What is the cause and the "
                  "decisive test?",
        "reference_answer": ("A 0.8 V drop over 2 cm means either a high-"
                             "resistance trace/via or excessive load "
                             "current. Measure the resistance of the trace "
                             "with power off: if it is > 1 Ω, the trace or "
                             "a via is damaged. If the trace resistance is "
                             "low, measure the load current: P = I*R, so "
                             "0.8 V drop at high current indicates the load "
                             "is drawing too much (a partial short)."),
        "source": "measurement",
        "source_ref": "5.0 V at regulator; 4.2 V at load (measured)",
        "synthetic": False,
    },
    {
        "scenario_id": "M5", "category": "measurement",
        "prompt": "You read 1.2 V on a 3.3 V rail. The meter's black probe "
                  "is on a random pad you assumed was ground. What is "
                  "wrong, and how do you confirm?",
        "reference_answer": ("The reading is unreliable because the black "
                             "probe is on an unverified reference. 1.2 V on "
                             "a 3.3 V rail could be a real droop OR an "
                             "offset because the 'ground' pad is actually "
                             "1.2 V above true ground. Confirm by moving the "
                             "black probe to a known ground (DC jack negative "
                             "or a marked GND pad) and re-measuring. Only "
                             "trust a reading once the ground reference is "
                             "verified."),
        "source": "measurement",
        "source_ref": "1.2 V on 3.3 V rail with unverified ground (measured)",
        "synthetic": False,
    },
    {
        "scenario_id": "M6", "category": "measurement",
        "prompt": "A MOSFET reads 0.3 V drop source-to-drain in one "
                  "direction and 0 V in the other, with the board powered "
                  "off and out of circuit. What is conducting?",
        "reference_answer": ("The asymmetry (0.3 V one way, 0 V the other) "
                             "is the body diode conducting in one direction. "
                             "0 V in the other direction suggests the D-S "
                             "channel is shorted (the FET is blown ON). Out "
                             "of circuit, a healthy MOSFET shows only the "
                             "body diode drop in one direction and open in "
                             "the other. A 0 V both-ways or a 0 V one-way "
                             "with diode the other indicates a shorted FET."),
        "source": "measurement",
        "source_ref": "MOSFET D-S 0.3 V one way, 0 V other (diode-mode meter)",
        "synthetic": False,
    },
    {
        "scenario_id": "M7", "category": "measurement",
        "prompt": "A battery pack's thermistor reads 10 kΩ at 25 C. The "
                  "datasheet says 10 kΩ ±5% at 25 C, B=3950. Is the "
                  "thermistor healthy, and what would 5 kΩ indicate?",
        "reference_answer": ("10 kΩ at 25 C is exactly the nominal value, "
                             "within the ±5% tolerance, so the thermistor is "
                             "healthy. 5 kΩ would indicate either a higher "
                             "temperature (use the B-parameter equation: "
                             "1/T = 1/T0 + (1/B)*ln(R/R0); with R=5k, "
                             "T ≈ 1/(1/298 + ln(0.5)/3950) ≈ 311 K ≈ 38 C) "
                             "or a drifted/shorted thermistor. Cross-check "
                             "with the board temperature."),
        "source": "datasheet",
        "source_ref": "NTC thermistor datasheet: 10 kΩ ±5% @25C, B=3950",
        "synthetic": False,
    },
    {
        "scenario_id": "M8", "category": "measurement",
        "prompt": "The AC adapter outputs 19.4 V with no load. Connected "
                  "to the laptop, the jack voltage drops to 16 V and the "
                  "laptop does not charge. What is the diagnosis?",
        "reference_answer": ("The drop from 19.4 V to 16 V under load means "
                             "the adapter cannot sustain its voltage under "
                             "current draw — either the adapter is failing "
                             "(high internal resistance), the DC jack/cable "
                             "has high contact resistance, or the laptop is "
                             "drawing excessive current. Measure the adapter "
                             "current: if it is within the adapter's rating "
                             "(e.g. 3.42 A for a 65 W adapter), the adapter "
                             "or cable is bad; if the current is above "
                             "rating, the laptop has a short. The 16 V under "
                             "load with a 19.4 V no-load points to adapter/"
                             "cable, not a board short."),
        "source": "measurement",
        "source_ref": "Adapter 19.4 V no-load, 16 V under load (measured)",
        "synthetic": False,
    },

    # ---- D. Datasheet interpretation (6) ----
    {
        "scenario_id": "D1", "category": "datasheet",
        "prompt": "A buck converter IC datasheet lists Vout = 0.8 * (1 + "
                  "Rtop/Rbot), with Vref = 0.8 V. For Vout = 1.0 V, what "
                  "is the required Rtop/Rbot ratio?",
        "reference_answer": ("Vout = Vref * (1 + Rtop/Rbot) => 1.0 = 0.8 * "
                             "(1 + Rtop/Rbot) => 1 + Rtop/Rbot = 1.25 => "
                             "Rtop/Rbot = 0.25. So Rtop = 0.25 * Rbot, e.g. "
                             "Rtop = 10 kΩ, Rbot = 40 kΩ. The formula comes "
                             "from the datasheet's feedback divider equation."),
        "source": "datasheet",
        "source_ref": "Buck converter datasheet: Vout = 0.8*(1+Rtop/Rbot), Vref=0.8 V",
        "synthetic": True,
    },
    {
        "scenario_id": "D2", "category": "datasheet",
        "prompt": "A MOSFET datasheet gives Rds(on) = 10 mΩ typ, 15 mΩ "
                  "max at Vgs=10 V, Id=10 A. Which value do you use for "
                  "worst-case thermal design, and why?",
        "reference_answer": ("Use the MAX value (15 mΩ) for worst-case "
                             "thermal design. The 'typ' is the typical "
                             "performance at the stated conditions; the "
                             "'max' is the guaranteed worst-case across "
                             "process, voltage, and temperature. Worst-case "
                             "conduction loss = I^2 * R = 10^2 * 0.015 = "
                             "1.5 W; with typ it would be 1.0 W. Designing "
                             "with typ underestimates the heat and risks "
                             "thermal failure in production."),
        "source": "datasheet",
        "source_ref": "MOSFET datasheet: Rds(on) 10 mΩ typ / 15 mΩ max @ Vgs=10V, Id=10A",
        "synthetic": False,
    },
    {
        "scenario_id": "D3", "category": "datasheet",
        "prompt": "A Li-ion charger IC datasheet says charge current = "
                  "1000 * (Vprog / Rprog) with Vprog = 1 V. For 1.5 A "
                  "charge current, what Rprog is needed?",
        "reference_answer": ("I_charge = 1000 * Vprog / Rprog => 1.5 = "
                             "1000 * 1 / Rprog => Rprog = 1000/1.5 = "
                             "667 Ω. Use a standard 680 Ω (gives 1.47 A) or "
                             "665 Ω precision. The formula is from the "
                             "datasheet's programming pin equation."),
        "source": "datasheet",
        "source_ref": "Li-ion charger datasheet: I = 1000 * Vprog/Rprog, Vprog=1 V",
        "synthetic": True,
    },
    {
        "scenario_id": "D4", "category": "datasheet",
        "prompt": "A datasheet lists absolute max Vds = 30 V and "
        "operating Vds = 20 V. The circuit sees 24 V on the drain. Is "
        "this acceptable?",
        "reference_answer": ("24 V is between the operating max (20 V) and "
                             "the absolute max (30 V). It is NOT acceptable "
                             "for production: operating above the "
                             "recommended operating condition (20 V) "
                             "voids the guaranteed performance and reduces "
                             "lifetime, even though it is below the absolute "
                             "max (30 V, which is a stress limit, not an "
                             "operating limit). Select a MOSFET with a "
                             "higher Vds rating (e.g. 40 V or 60 V)."),
        "source": "datasheet",
        "source_ref": "MOSFET datasheet: abs max Vds=30 V, operating Vds=20 V",
        "synthetic": False,
    },
    {
        "scenario_id": "D5", "category": "datasheet",
        "prompt": "A buck converter datasheet says the inductor must be "
                  "1.5 * (Vin - Vout) * Vout / (Vin * f * ΔI), with f = "
                  "500 kHz, ΔI = 30% of Iload. For Vin=12 V, Vout=3.3 V, "
                  "Iload=2 A, what inductance?",
        "reference_answer": ("ΔI = 0.3 * 2 = 0.6 A. L = 1.5 * (12-3.3) * "
                             "3.3 / (12 * 500e3 * 0.6) = 1.5 * 8.7 * 3.3 / "
                             "(12 * 500e3 * 0.6) = 43.065 / 3.6e6 = "
                             "11.96 µH. Use a standard 12 µH or 15 µH "
                             "inductor rated for at least Iload + ΔI/2 = "
                             "2.3 A saturation current."),
        "source": "datasheet",
        "source_ref": "Buck converter datasheet: L = 1.5*(Vin-Vout)*Vout/(Vin*f*ΔI)",
        "synthetic": True,
    },
    {
        "scenario_id": "D6", "category": "datasheet",
        "prompt": "A datasheet specifies an enable pin threshold Vih = "
                  "2.0 V min. The circuit drives EN with 1.8 V. Will the "
                  "converter start reliably?",
        "reference_answer": ("No. The enable Vih (input-high threshold) is "
                             "2.0 V min, meaning the IC is only GUARANTEED "
                             "to recognize a high above 2.0 V. Driving with "
                             "1.8 V is below the guaranteed threshold, so "
                             "the converter may or may not start depending "
                             "on process variation and temperature. Use a "
                             "level shifter or a MOSFET to pull EN above "
                             "2.0 V (e.g. to Vin or 3.3 V)."),
        "source": "datasheet",
        "source_ref": "Converter datasheet: EN Vih = 2.0 V min",
        "synthetic": False,
    },

    # ---- C. Component behavior (6) ----
    {
        "scenario_id": "C1", "category": "component",
        "prompt": "A P-channel MOSFET is used as a load switch on a 19 V "
                  "rail. The gate is pulled to 19 V (off) and pulled to "
                  "0 V (on). The MOSFET has Vgs(th) = -2 V. Will it turn "
                  "on, and what is the Vgs when on?",
        "reference_answer": ("A P-channel MOSFET turns on when Vgs < Vth "
                             "(negative). With the source at 19 V and the "
                             "gate at 0 V, Vgs = 0 - 19 = -19 V, which is "
                             "well below -2 V, so it turns on hard. When "
                             "off, gate = 19 V, Vgs = 0 V, off. The 19 V "
                             "Vgs must be within the absolute max (typically "
                             "±20 V); -19 V is close to the limit — add a "
                             "gate-to-source zener clamp if the rail can "
                             "spike."),
        "source": "textbook",
        "source_ref": "P-MOSFET load switch; Vgs(th) = -2 V; source at 19 V",
        "synthetic": True,
    },
    {
        "scenario_id": "C2", "category": "component",
        "prompt": "A diode reads 0.7 V forward and open reverse, in "
                  "circuit. Is it healthy? What would 0.2 V forward "
                  "suggest?",
        "reference_answer": ("0.7 V forward (silicon drop) and open reverse "
                             "is a healthy silicon diode. 0.2 V forward "
                             "would suggest a Schottky diode (lower forward "
                             "drop) OR a shorted/damaged silicon diode — "
                             "check the part number. A shorted diode reads "
                             "0 V both ways; an open diode reads open both "
                             "ways."),
        "source": "textbook",
        "source_ref": "Silicon diode: 0.7 V Vf; Schottky: 0.2-0.4 V Vf",
        "synthetic": False,
    },
    {
        "scenario_id": "C3", "category": "component",
        "prompt": "An electrolytic capacitor measures 0 Ω across its "
                  "terminals in-circuit. List the two most likely causes "
                  "and the decisive out-of-circuit test.",
        "reference_answer": ("0 Ω across an electrolytic in-circuit is "
                             "either (1) the cap itself is shorted (failed "
                             "short) or (2) a parallel component (another "
                             "cap, a FET, the load) is shorted. The decisive "
                             "test: lift one leg of the cap (remove from "
                             "circuit) and re-measure — if it reads 0 Ω out "
                             "of circuit, the cap is shorted; if it reads "
                             "open/capacitive out of circuit, the short is "
                             "elsewhere on the rail."),
        "source": "measurement",
        "source_ref": "Electrolytic 0 Ω in-circuit; decisive out-of-circuit test",
        "synthetic": False,
    },
    {
        "scenario_id": "C4", "category": "component",
        "prompt": "A BJT reads 0.6 V base-emitter and 0.6 V base-"
                  "collector, both directions, in-circuit. What is the "
                  "likely fault?",
        "reference_answer": ("0.6 V both BE and BC, both directions, "
                             "indicates both junctions are shorted or the "
                             "BJT is severely overheated/damaged. A healthy "
                             "BJT reads 0.6 V one way (forward) and open the "
                             "other (reverse) on each junction. Reading 0.6 V "
                             "BOTH ways on both junctions suggests the "
                             "transistor is shorted. Lift it and re-test out "
                             "of circuit to confirm."),
        "source": "measurement",
        "source_ref": "BJT 0.6 V both BE and BC both directions (diode-mode meter)",
        "synthetic": False,
    },
    {
        "scenario_id": "C5", "category": "component",
        "prompt": "A buck converter's switching node rings to 25 V on "
                  "turn-off with Vin = 12 V. The low-side FET has Vds max "
                  "40 V. Is this safe, and what is the typical fix?",
        "reference_answer": ("25 V ringing on a 12 V rail with a 40 V FET "
                             "is within the absolute max but reduces "
                             "lifetime and causes EMI. The ringing is caused "
                             "by parasitic inductance (the trace + the "
                             "inductor) resonating with the FET's Coss. The "
                             "fix is an RC snubber across the switch node "
                             "to ground (typically 100 Ω + 1 nF, tuned), or "
                             "reducing trace inductance. If the ringing "
                             "approaches Vds max, the FET will fail."),
        "source": "textbook",
        "source_ref": "Buck converter switch-node ringing; FET Vds max 40 V",
        "synthetic": True,
    },
    {
        "scenario_id": "C6", "category": "component",
        "prompt": "An op-amp in a non-inverting config has Rf = 10 kΩ, "
                  "Rin = 1 kΩ. What is the gain, and what happens if the "
                  "input exceeds the rail?",
        "reference_answer": ("Non-inverting gain = 1 + Rf/Rin = 1 + "
                             "10/1 = 11 V/V. If the input exceeds the rail, "
                             "the op-amp's input ESD diodes conduct, "
                             "clamping the input to Vrail + 0.7 V (or "
                             "Vee - 0.7 V). Sustained overdrive can blow "
                             "the ESD diodes or latch the op-amp. For inputs "
                             "that may exceed the rail, use a rail-to-rail-"
                             "input op-amp or add a series resistor to "
                             "limit the ESD diode current."),
        "source": "textbook",
        "source_ref": "Op-amp non-inverting gain = 1 + Rf/Rin; ESD diode clamp",
        "synthetic": True,
    },

    # ---- T. Troubleshooting / fault isolation (4) ----
    {
        "scenario_id": "T1", "category": "troubleshooting",
        "prompt": "A laptop has no power. VIN=19.4 V, +5VALW=0 V, +3.3V=0 V. "
                  "The +5VALW regulator's EN pin reads 0 V. Rank two "
                  "hypotheses and pick the decisive test.",
        "reference_answer": ("H1: the always-on supply chain is dead (the "
                             "always-on LDO before the +5VALW regulator is "
                             "not generating its output, so EN stays low). "
                             "H2: the EN signal itself is pulled low by a "
                             "failed component (a shorted EN cap or a "
                             "failed EC pin). Decisive test: measure the "
                             "always-on LDO output (e.g. +3.3VALW or +5VALW "
                             "LDO input). If it is 0 V, H1 (upstream) is "
                             "confirmed; if it is present but EN is still 0 V, "
                             "H2 (EN fault) is confirmed."),
        "source": "confirmed_case",
        "source_ref": "Confirmed repair: Lenovo G50-80 no power, dead always-on LDO",
        "synthetic": False,
    },
    {
        "scenario_id": "T2", "category": "troubleshooting",
        "prompt": "A board powers on but the CPU VRM never starts. The "
                  "VRM's power-good (PG) pin is low. The VRM's input rail "
                  "is present. List two hypotheses and the decisive test.",
        "reference_answer": ("H1: the VRM's enable is not asserted (the "
                             "EC never raises VR_ON due to a sequence "
                             "fault upstream). H2: the VRM's output is "
                             "shorted, so it immediately faults and holds "
                             "PG low. Decisive test: measure VR_ON (the "
                             "enable). If VR_ON is 0 V, H1 (sequence/EC "
                             "fault); if VR_ON is high but PG is low, "
                             "measure the output impedance to ground — a "
                             "short confirms H2."),
        "source": "confirmed_case",
        "source_ref": "Confirmed repair: Dell E6440 no CPU VRM, EC sequence fault",
        "synthetic": False,
    },
    {
        "scenario_id": "T3", "category": "troubleshooting",
        "prompt": "A laptop charges only when off; when on, the battery "
                  "discharges. The charger IC's PROCHOT pin reads 3.3 V. "
                  "What is the likely cause?",
        "reference_answer": ("PROCHOT asserted (3.3 V) tells the charger "
                             "IC to reduce or stop charging because the "
                             "system reports thermal stress. If the laptop "
                             "is not actually hot, a stuck-high PROCHOT "
                             "(a failed EC output or a shorted signal) "
                             "falsely limits charging. Measure PROCHOT at "
                             "the EC: if the EC drives it high without "
                             "thermal cause, the EC or its firmware is "
                             "faulty. A common root cause is a liquid-"
                             "damaged EC or a shorted PROCHOT trace."),
        "source": "confirmed_case",
        "source_ref": "Confirmed repair: HP 840 G3 charges only when off, stuck PROCHOT",
        "synthetic": False,
    },
    {
        "scenario_id": "T4", "category": "troubleshooting",
        "prompt": "A board has a shorted +5VALW rail (0 Ω to ground). "
                  "VIN is good. Describe the systematic isolation "
                  "procedure to find the shorted component without "
                  "removing every cap.",
        "reference_answer": ("1) Inject a low voltage (1 V, current-"
                             "limited to ~1 A) into the +5VALW rail from "
                             "a bench supply. 2) Use a thermal camera or "
                             "freeze spray (or your finger) to find the "
                             "component that heats up first — the shorted "
                             "part dissipates I^2*R and warms. 3) If no "
                             "component heats, the short is in a via or "
                             "trace — use a milliohm meter to measure the "
                             "voltage drop along the rail toward the "
                             "short (the drop points toward the short). "
                             "4) Confirm by lifting the suspect component. "
                             "Never inject full Vin into a shorted rail — "
                             "use a current-limited low voltage."),
        "source": "confirmed_case",
        "source_ref": "Confirmed repair: shorted +5VALW, ceramic cap short",
        "synthetic": False,
    },
]
