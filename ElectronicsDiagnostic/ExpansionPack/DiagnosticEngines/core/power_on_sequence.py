"""Motherboard power-on sequence engine.

Models the laptop/desktop power-on sequence as an ordered list of
diagnostic states. Given the measured state of each stage, the engine
identifies the EARLIEST stage that has not completed and returns a
structured conclusion with the fault-state classification and the
next test.

This is REAL sequence reasoning, not metadata lookup. The engine walks
the stages in order, checks each against the supplied measurements, and
stops at the first stage that cannot be confirmed. It never claims a
fault beyond what the measurements support.

The 8 fault-state distinctions (per spec):
  1. no_power        — no adapter/battery input at all
  2. no_standby      — input present but standby rails dead
  3. no_power_on     — standby up but board won't respond to power button
  4. no_post         — power rails up but CPU not executing POST
  5. no_display      — POST passes but no display output
  6. shutdown        — board powers up then turns off
  7. restart_loop    — board powers up, restarts repeatedly
  8. charging_fault  — battery won't charge (laptop only)

Stages (laptop):
  adapter_input -> standby_rails -> ec_alive -> power_button ->
  main_enables -> cpu_vrm -> memory -> clock_reset -> post -> display

Stages (desktop):
  atx_5vsb -> front_panel -> ps_on -> main_rails -> cpu_vrm ->
  memory -> pch -> clock_reset -> post -> display
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .framework import (
    Measurement, MeasurementState, MeasurementKind, MeasurementCondition,
    Hypothesis, Evidence, EvidenceType, EvidenceStrength,
)
from .framework.session import EngineConclusion


# --- fault-state classifications ----------------------------------------

FAULT_NO_POWER = "no_power"
FAULT_NO_STANDBY = "no_standby"
FAULT_NO_POWER_ON = "no_power_on"
FAULT_NO_POST = "no_post"
FAULT_NO_DISPLAY = "no_display"
FAULT_SHUTDOWN = "shutdown"
FAULT_RESTART_LOOP = "restart_loop"
FAULT_CHARGING = "charging_fault"
FAULT_HEALTHY = "healthy"
FAULT_INSUFFICIENT_DATA = "insufficient_data"


@dataclass
class SequenceStage:
    """One stage in the power-on sequence."""
    stage_id: str
    name: str
    description: str
    # The measurement name(s) that confirm this stage completed.
    # Each is (measurement_name, expected_state, expected_value_min, expected_value_max)
    confirm_measurements: list[tuple[str, str, Optional[float], Optional[float]]]
    # The fault-state if this stage cannot be confirmed
    fault_if_failed: str
    # Phase 4: explicit dependencies. A stage is only diagnosed if its
    # dependencies are confirmed-healthy. If a dependency is unknown/
    # failed, the walk stops there (do not diagnose downstream stages).
    depends_on: list[str] = field(default_factory=list)
    # Phase 4: alternative hypotheses when this stage fails (avoids the
    # "one missing output => IC is dead" fallacy). Each is a string
    # describing a possible root cause to investigate.
    alternative_hypotheses: list[str] = field(default_factory=list)
    # Phase 4: the next measurement that maximizes information value for
    # this stage (what to measure NEXT to disambiguate). "info_value" is
    # a short note explaining why this measurement is high-value.
    next_measurement: str = ""
    next_measurement_info_value: str = ""
    optional: bool = False  # supplemental checks never block the core boot sequence when unmeasured


@dataclass
class PowerOnSequenceInput:
    """Inputs for the power-on sequence diagnosis."""
    platform: str                        # 'laptop' or 'desktop'
    measurements: list[Measurement] = field(default_factory=list)
    observed_symptoms: list[str] = field(default_factory=list)
    # ^ free-text symptoms: 'shutdown_after_2s', 'restart_loop', 'no_display', etc.


# --- stage definitions --------------------------------------------------

# Each stage's confirm_measurements uses (name, state, min, max).
# state is a MeasurementState value string. min/max are voltage bounds
# (None = don't check magnitude, only presence).

_LAPTOP_STAGES = [
    SequenceStage(
        stage_id="adapter_input", name="Adapter/battery input",
        description="The adapter must supply voltage at the DC jack, and/or the battery must supply voltage. Without either, the board has no power source.",
        confirm_measurements=[
            ("adapter_input_voltage", "in_range", 17.0, 21.0),
        ],
        fault_if_failed=FAULT_NO_POWER,
        depends_on=[],
        alternative_hypotheses=[
            "Adapter itself dead (test with a known-good adapter).",
            "DC jack broken / cold solder joint (measure at the jack pin, not just the barrel).",
            "Input fuse open (measure voltage on both sides of the input fuse).",
            "Battery depleted AND adapter not supplying (battery alone cannot start without some adapter handshake on most laptops).",
        ],
        next_measurement="Measure adapter voltage at the DC jack pin (not the barrel tip).",
        next_measurement_info_value="Distinguishes adapter-dead from jack/fuse open — the highest-value first measurement.",
    ),
    SequenceStage(
        stage_id="input_protection", name="Input protection (fuse, TVS, reverse-polarity FET)",
        description="Past the DC jack: input fuse, TVS diode, and reverse-polarity protection FET. A blown fuse or shorted TVS blocks power to the charger IC.",
        confirm_measurements=[
            ("post_fuse_voltage", "in_range", 17.0, 21.0),
        ],
        fault_if_failed=FAULT_NO_POWER,
        depends_on=["adapter_input"],
        alternative_hypotheses=[
            "Input fuse blown (often indicates a downstream short — do NOT just replace the fuse).",
            "TVS diode shorted (measure resistance to ground at the input rail).",
            "Reverse-polarity FET open (gate-drive missing or FET dead).",
        ],
        next_measurement="Measure resistance to ground at the post-fuse rail (unpowered).",
        next_measurement_info_value="A low resistance explains the blown fuse and points to the shorted component — must be found before replacing the fuse.",
    ),
    SequenceStage(
        stage_id="charger_ic", name="Charger IC (adapter -> battery buck)",
        description="The charger IC (e.g. BQ247xx, ISL6251) regulates adapter-to-battery charging and passes adapter power to the system. It needs VCC, ACIN, and SMBus from the EC.",
        confirm_measurements=[
            ("charger_vcc", "in_range", 4.5, 5.5),
            ("charger_acin", "in_range", 17.0, 21.0),
        ],
        fault_if_failed=FAULT_NO_POWER,
        depends_on=["input_protection"],
        alternative_hypotheses=[
            "Charger IC VCC missing (check the VCC feed — often a small LDO off the input).",
            "Charger IC disabled by EC via SMBus (EC firmware may hold it off — check EC SMBus traffic).",
            "Charger IC dead (confirm VCC+ACIN present AND enable asserted before condemning the IC).",
            "Back-to-back adapter-select FETs open (gate drive from charger IC missing).",
        ],
        next_measurement="Measure charger IC VCC and ACIN pins.",
        next_measurement_info_value="Confirms whether the IC has power+input before condemning it — a single missing output does NOT prove the IC is dead.",
    ),
    SequenceStage(
        stage_id="battery_path", name="Battery path (BAT1+/BATDRV)",
        description="The battery connects via BAT1+ through the charger IC's battery-drive FETs. A battery can power the board standalone if the adapter is absent.",
        confirm_measurements=[
            ("battery_voltage", "in_range", 7.5, 12.6),
        ],
        fault_if_failed=FAULT_NO_POWER,
        depends_on=["charger_ic"],
        alternative_hypotheses=[
            "Battery depleted or dead (measure cell voltage at the battery connector).",
            "Battery thermistor open (EC disables charging/discharging on a bad thermistor).",
            "Battery-drive FETs open (gate drive from charger IC missing).",
        ],
        next_measurement="Measure battery voltage at the BAT1+ connector.",
        next_measurement_info_value="Determines if the battery can substitute for the adapter — critical when adapter input is suspect.",
    ),
    SequenceStage(
        stage_id="standby_rails", name="Standby rails (+3VALW, +5VALW)",
        description="Always-on standby rails derived from the adapter (via the charger IC). The EC, super-IO, and RTC run off these. Without standby, the board cannot respond to the power button.",
        confirm_measurements=[
            ("plus_3valw_voltage", "in_range", 3.2, 3.4),
            ("plus_5valw_voltage", "in_range", 4.75, 5.25),
        ],
        fault_if_failed=FAULT_NO_STANDBY,
        depends_on=["charger_ic"],
        alternative_hypotheses=[
            "Standby buck controller dead (confirm VIN+VCC+EN before condemning).",
            "Standby rail shorted (measure resistance to ground — a short pulls the rail down).",
            "Standby controller disabled (EN pin low from EC — check EC firmware state).",
            "Inductor open in the standby buck (switching present but output absent).",
        ],
        next_measurement="Measure resistance to ground on +3VALW and +5VALW (unpowered).",
        next_measurement_info_value="A short explains the absent rail and must be found before applying power; a normal resistance points to the controller.",
    ),
    SequenceStage(
        stage_id="rtc", name="RTC (real-time clock + CMOS)",
        description="The RTC keeps time and holds CMOS settings. It runs off +VCCRTC (a coin cell or a standby-derived rail). A dead RTC can cause CMOS-reset loops but usually does NOT block power-on.",
        confirm_measurements=[
            ("vccrtc_voltage", "in_range", 2.8, 3.3),
        ],
        fault_if_failed=FAULT_NO_STANDBY,
        depends_on=["standby_rails"],
        alternative_hypotheses=[
            "CMOS battery dead (laptops often have no coin cell — RTC runs off +VCCRTC LDO).",
            "+VCCRTC LDO dead (fed from standby — check standby first).",
            "RTC crystal dead (32.768 kHz absent — EC may not sequence correctly).",
        ],
        next_measurement="Measure +VCCRTC and the 32.768 kHz RTC crystal.",
        next_measurement_info_value="RTC rarely blocks power-on, but a dead clock can stall the EC — worth checking when EC behavior is erratic.",
    ),
    SequenceStage(
        stage_id="ec_alive", name="EC / super-IO alive",
        description="The embedded controller (laptop) runs off standby. It monitors the power button and sequences the main rails. EC liveness is confirmed by a heartbeat signal (e.g. EC_CLK, 32.768 kHz RTC) or an LED response.",
        confirm_measurements=[
            ("ec_heartbeat", "in_range", None, None),
        ],
        fault_if_failed=FAULT_NO_STANDBY,
        depends_on=["standby_rails", "rtc"],
        alternative_hypotheses=[
            "EC firmware corrupted (EC has VCC but no heartbeat — reflash or replace EC).",
            "EC crystal dead (32.768 kHz absent — EC cannot execute).",
            "EC reset stuck low (RSMRST# never de-asserts — check the reset circuit).",
            "EC power rail missing (EC VCC is a derivative of standby — verify).",
        ],
        next_measurement="Measure EC VCC, reset pin, and the 32.768 kHz crystal.",
        next_measurement_info_value="Separates EC-hardware-dead from EC-firmware-corrupted from reset-stuck — three very different faults with the same symptom.",
    ),
    SequenceStage(
        stage_id="power_button", name="Power button response",
        description="Pressing the power button signals the EC. The EC then asserts the main-rail enable signals. Confirmed by observing the EC respond (e.g. an LED blink, or the enable signals going high).",
        confirm_measurements=[
            ("power_button_pressed", "in_range", None, None),
            ("ec_responded_to_button", "in_range", None, None),
        ],
        fault_if_failed=FAULT_NO_POWER_ON,
        depends_on=["ec_alive"],
        alternative_hypotheses=[
            "Power button switch dead (measure continuity when pressed).",
            "Power button connector loose.",
            "EC GPIO input dead (button works but EC does not see it — firmware or pin fault).",
        ],
        next_measurement="Measure power-button continuity and the EC GPIO pin state when pressed.",
        next_measurement_info_value="Distinguishes a mechanical button fault from an EC input fault.",
    ),
    SequenceStage(
        stage_id="main_enables", name="Main-rail enable signals (S5->S0 transition)",
        description="The EC asserts enable signals (SYSON, RUNPWROK, etc.) to turn on the main rails (+3V, +5V, +1.8V). This is the S5->S3->S0 transition. Confirmed by the enable signals being logic-high.",
        confirm_measurements=[
            ("main_enables_high", "in_range", None, None),
        ],
        fault_if_failed=FAULT_NO_POWER_ON,
        depends_on=["power_button"],
        alternative_hypotheses=[
            "EC firmware not asserting enables (firmware state machine stuck in S5).",
            "Sleep signal stuck (SLP_S5#/SLP_S4#/SLP_S3# from PCH never de-asserts).",
            "EC GPIO output dead (enable pin never goes high despite EC running).",
        ],
        next_measurement="Measure SLP_S5#/SLP_S4#/SLP_S3# (sleep signals from PCH) and the EC enable outputs.",
        next_measurement_info_value="Sleep signals are the PCH's authorization to power on — if stuck, the EC correctly holds enables low (not an EC fault).",
    ),
    SequenceStage(
        stage_id="memory", name="Memory rail (DDR VDD/VTT)",
        description="The memory VDD rail must be up (1.2V DDR4, 1.1V DDR5). VTT (termination, half VDD) should also be present but is a secondary check — VDD is the primary confirm. Without VDD, memory training fails and POST cannot complete.",
        confirm_measurements=[
            ("ddr_vdd_voltage", "in_range", 1.0, 1.5),
        ],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["main_enables"],
        alternative_hypotheses=[
            "Memory buck controller dead (confirm VIN+EN+VCC).",
            "VTT LDO dead (VTT is half VDD — a dead VTT LDO blocks training even with good VDD).",
            "Memory rail shorted (measure resistance).",
            "DIMM socket fault (reseat/swap DIMM before condemning the controller).",
        ],
        next_measurement="Measure DDR VDD and VTT (VTT must be half VDD); reseat the DIMM.",
        next_measurement_info_value="VTT is often overlooked — a missing VTT with good VDD still blocks training; reseating the DIMM is a 10-second test that rules out socket faults.",
    ),
    SequenceStage(
        stage_id="cpu_vrm", name="CPU VRM (VCORE)",
        description="The CPU core voltage regulator must come up. VCORE is set by the CPU VID pins. Without VCORE the CPU cannot execute. This is a multi-phase VRM on modern boards.",
        confirm_measurements=[
            ("vcore_voltage", "in_range", 0.6, 1.5),
        ],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["main_enables", "memory"],
        alternative_hypotheses=[
            "VRM controller disabled (VR_ON from PCH not asserted — check PCH sleep signals).",
            "VRM phase failure (one phase's FET dead — measure each phase inductor).",
            "VCORE shorted (measure resistance to ground — VCORE reads low ohms by design, but <1 ohm is a fault).",
            "VID pins floating (CPU not seated — reseat CPU).",
        ],
        next_measurement="Measure VR_ON signal and each VRM phase inductor's switching node.",
        next_measurement_info_value="VR_ON distinguishes controller-disabled from controller-dead; per-phase inductor check finds a single failed phase without condemning the whole VRM.",
    ),
    SequenceStage(
        stage_id="vrm_gpu", name="GPU VRM (VGPU) [if discrete GPU]",
        description="Discrete-GPU laptops have a separate GPU VRM. The GPU core voltage must come up before the GPU can initialize. On iGPU-only laptops this stage is skipped.",
        confirm_measurements=[
            ("vgpu_voltage", "in_range", 0.6, 1.5),
        ],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["main_enables"],
        alternative_hypotheses=[
            "GPU VRM controller disabled (GPU_EN from EC/PCH not asserted).",
            "GPU VRM phase failure.",
            "GPU core shorted (measure VGPU resistance — <1 ohm indicates a shorted GPU).",
        ],
        next_measurement="Measure GPU_EN and VGPU resistance to ground.",
        next_measurement_info_value="A shorted GPU is a common board-killer; low VGPU resistance predicts it before applying full power.",
    ),
    SequenceStage(
        stage_id="clock_reset", name="Clock + reset (PLTRST#/RSMRST#)",
        description="The CPU reset signal (PLTRST#) must de-assert after all rails are good, and the reference clock must be running. Confirmed by reset being de-asserted (high) and clock present.",
        confirm_measurements=[
            ("reset_deasserted", "in_range", None, None),
            ("cpu_clock_present", "in_range", None, None),
        ],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["cpu_vrm", "memory"],
        alternative_hypotheses=[
            "PCH not releasing PLTRST# (a rail PGOOD is missing — check all PGOOD signals).",
            "Clock generator dead (measure the reference clock).",
            "Reset circuit stuck (capacitor leak on the reset line).",
        ],
        next_measurement="Measure all rail PGOOD signals and PLTRST#.",
        next_measurement_info_value="PLTRST# only de-asserts when ALL PGOODs are high — a single missing PGOOD explains the stuck reset without condemning the PCH.",
    ),
    SequenceStage(
        stage_id="bios_spi", name="BIOS/SPI flash communication",
        description="The CPU reads the BIOS from the SPI flash. If the SPI bus is dead (CS# never asserts, or MISO stuck), the CPU cannot execute POST. Confirmed by SPI CS# toggling and MISO activity.",
        confirm_measurements=[
            ("spi_cs_active", "in_range", None, None),
            ("spi_miso_active", "in_range", None, None),
        ],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["clock_reset"],
        alternative_hypotheses=[
            "SPI flash chip dead (CS# toggles but MISO stuck — reflash or replace).",
            "SPI trace fault (cold solder under the flash chip — reflow).",
            "BIOS image corrupted (reflash with a known-good image).",
            "PCH SPI controller dead (rare — confirm flash is good first).",
        ],
        next_measurement="Probe SPI CS# and MISO with a logic analyzer / scope.",
        next_measurement_info_value="CS# toggling with dead MISO = flash chip fault; no CS# = PCH/controller fault — pinpoints the fault to chip vs board.",
    ),
    SequenceStage(
        stage_id="ram_init", name="RAM initialization (memory training)",
        description="The BIOS trains the memory (read/write leveling, timing). A training failure produces a memory-related POST code (often 53/55 on ASUS, 3-beep on AMI). Confirmed by POST codes progressing PAST the memory range.",
        confirm_measurements=[
            ("post_code_past_memory", "in_range", None, None),
        ],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["memory", "bios_spi"],
        alternative_hypotheses=[
            "Bad DIMM (swap with a known-good DIMM).",
            "Bad DIMM slot (try the other slot).",
            "Memory controller (CPU IMC) fault (try a different CPU).",
            "VTT rail out of spec (VTT must be half VDD within 2%).",
        ],
        next_measurement="Read POST codes; swap DIMMs and try each slot.",
        next_measurement_info_value="POST codes localize the fault; DIMM/swap tests rule out the DIMM and slot before condemning the CPU IMC.",
    ),
    SequenceStage(
        stage_id="post", name="POST execution",
        description="The CPU executes the BIOS POST. Confirmed by POST codes progressing past the memory-training range, or by the BIOS POST LED turning off.",
        confirm_measurements=[
            ("post_progressing", "in_range", None, None),
        ],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["ram_init", "bios_spi"],
        alternative_hypotheses=[
            "BIOS image corrupted (reflash).",
            "CPU fault (rare — confirm POST codes stop at a CPU-related code).",
            "PCH fault (confirm peripheral POST codes).",
        ],
        next_measurement="Read POST codes through a full restart cycle.",
        next_measurement_info_value="A repeating POST code pinpoints the failing subsystem; a code that progresses then stops indicates a different fault than one that never moves.",
    ),
    SequenceStage(
        stage_id="display", name="Display output",
        description="The display must receive a signal. On laptops, the embedded display (eDP/LVDS) backlight + data must be active. Confirmed by backlight on and display data present.",
        confirm_measurements=[
            ("display_backlight_on", "in_range", None, None),
            ("display_data_present", "in_range", None, None),
        ],
        fault_if_failed=FAULT_NO_DISPLAY,
        depends_on=["post"],
        alternative_hypotheses=[
            "Backlight enable signal missing (EC not asserting BKLT_EN).",
            "Backlight fuse blown (measure continuity).",
            "eDP/LVDS cable loose.",
            "GPU display output dead (confirm POST completed first).",
            "Panel dead (test with an external monitor via HDMI/DP).",
        ],
        next_measurement="Test with an external monitor; measure backlight fuse and BKLT_EN.",
        next_measurement_info_value="External monitor rules out panel/cable; backlight fuse+EN separates power-path from signal-path faults.",
    ),
]

_DESKTOP_STAGES = [
    SequenceStage(
        stage_id="atx_5vsb", name="ATX +5VSB standby",
        description="The ATX PSU supplies +5VSB whenever AC is present. It powers standby logic such as the Super-I/O/RTC path. Verify at the 24-pin connector before diagnosing the board's power-on logic.",
        confirm_measurements=[("atx_5vsb_voltage", "in_range", 4.75, 5.25)],
        fault_if_failed=FAULT_NO_STANDBY,
        depends_on=[],
        alternative_hypotheses=["PSU standby output is absent or out of tolerance.", "Open/poor contact on the ATX 24-pin connector or standby distribution path."],
        next_measurement="Measure +5VSB at the ATX 24-pin connector relative to COM.",
        next_measurement_info_value="Separates PSU standby failure from motherboard power-on logic faults.",
    ),
    SequenceStage(
        stage_id="front_panel", name="Front-panel button / Super-I/O response",
        description="The front-panel power switch input must reach the Super-I/O / embedded controller. A switch closure alone does not prove that the controller has detected it.",
        confirm_measurements=[("power_button_pressed", "in_range", None, None), ("sio_responded_to_button", "in_range", None, None)],
        fault_if_failed=FAULT_NO_POWER_ON,
        depends_on=["atx_5vsb"],
        alternative_hypotheses=["Power-switch or front-panel header fault.", "Super-I/O standby supply/reset/clock missing.", "Super-I/O input path or firmware state fault."],
        next_measurement="Check front-panel switch continuity, then observe the corresponding Super-I/O input transition.",
        next_measurement_info_value="Distinguishes a switch/header issue from a controller-side issue.",
    ),
    SequenceStage(
        stage_id="ps_on", name="PS_ON# assertion",
        description="The motherboard asserts PS_ON# low to request the ATX PSU main outputs. Confirm the logic transition independently of the main-rail voltages.",
        confirm_measurements=[("ps_on_asserted", "in_range", None, None)],
        fault_if_failed=FAULT_NO_POWER_ON,
        depends_on=["front_panel"],
        alternative_hypotheses=["Super-I/O does not assert PS_ON#.", "PS_ON# trace, connector contact, or pull-up/pull-down network is damaged."],
        next_measurement="Observe PS_ON# at the ATX 24-pin connector during the power-button request.",
        next_measurement_info_value="Separates motherboard command generation from PSU response.",
    ),
    SequenceStage(
        stage_id="main_rails", name="ATX main rails (+3.3 V / +5 V / +12 V)",
        description="After PS_ON# is asserted, the PSU should provide the main ATX outputs. Measure each rail at the connector; do not infer one rail from another.",
        confirm_measurements=[("atx_3v3_voltage", "in_range", 3.14, 3.47), ("atx_5v_voltage", "in_range", 4.75, 5.25), ("atx_12v_voltage", "in_range", 11.4, 12.6)],
        fault_if_failed=FAULT_NO_POWER_ON,
        depends_on=["ps_on"],
        alternative_hypotheses=["PSU main output protection or failure.", "Short/overload on a main rail.", "Poor ATX connector contact or damaged distribution path."],
        next_measurement="Measure +3.3 V, +5 V and +12 V at the ATX connector under the start attempt.",
        next_measurement_info_value="Shows whether the PSU actually starts and which output is missing or collapsing.",
    ),
    SequenceStage(
        stage_id="pwr_ok", name="PWR_OK / power-good",
        description="The PSU asserts PWR_OK only after its main outputs are within regulation. A missing or unstable PWR_OK can prevent the board from progressing even when static rail readings look plausible.",
        confirm_measurements=[("pwr_ok_asserted", "in_range", None, None)],
        fault_if_failed=FAULT_NO_POWER_ON,
        depends_on=["main_rails"],
        alternative_hypotheses=["PSU does not assert PWR_OK.", "One main output is unstable or outside tolerance.", "PWR_OK signal path or motherboard input is damaged."],
        next_measurement="Observe PWR_OK at the ATX connector during startup, including whether it drops out.",
        next_measurement_info_value="Separates main-rail presence from the PSU's valid-power handshake.",
    ),
    SequenceStage(
        stage_id="eps12v", name="EPS12V CPU input",
        description="The 4/8-pin EPS connector supplies the CPU VRM input. Confirm the input at the motherboard connector and at the VRM input stage before diagnosing VCORE generation.",
        confirm_measurements=[("eps12v_voltage", "in_range", 11.4, 12.6)],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["pwr_ok"],
        alternative_hypotheses=["EPS cable absent or poorly seated.", "PSU +12 V is present at ATX but missing at EPS connector.", "Open fuse/connector/PCB path between EPS input and CPU VRM."],
        next_measurement="Measure EPS12V at the 4/8-pin CPU power connector and at the CPU VRM input.",
        next_measurement_info_value="Prevents condemning the CPU VRM when its input supply is absent.",
    ),
    SequenceStage(
        stage_id="cpu_vrm", name="CPU VRM (VCORE)",
        description="The CPU VRM must generate the processor core voltage from EPS12V. The expected value is platform/CPU dependent; this broad range is only a presence check, not a VID specification.",
        confirm_measurements=[("vcore_voltage", "in_range", 0.6, 1.5)],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["eps12v"],
        alternative_hypotheses=["CPU VRM controller lacks VCC/EN/VR_READY.", "Output rail short or excessive load.", "Power stage, current-sense, feedback, or CPU socket fault."],
        next_measurement="Check CPU VRM controller VCC, EN, switching activity and VCORE resistance with power disconnected before further powered tests.",
        next_measurement_info_value="Separates missing control/input from a loaded or failed output stage.",
    ),
    SequenceStage(
        stage_id="memory", name="RAM VRM / DDR supply",
        description="The memory power domain must be present. DDR VDD/VDDQ, VPP and termination rails vary by DDR generation and board design; do not apply a single fixed target to every platform.",
        confirm_measurements=[("ddr_vdd_voltage", "in_range", 1.0, 1.5)],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["pwr_ok"],
        alternative_hypotheses=["Memory regulator input/enable missing.", "DDR rail short or regulator protection.", "DIMM/slot/termination or memory-PHY issue."],
        next_measurement="Identify the board's DDR generation and measure its documented VDD/VDDQ rail; then check VTT/VPP where applicable.",
        next_measurement_info_value="Avoids treating DDR3/DDR4/DDR5 rail targets as interchangeable.",
    ),
    SequenceStage(
        stage_id="pch", name="PCH / chipset standby and main power",
        description="The PCH/chipset power domains, reference clocks and reset sequencing must be valid for platform initialization. Rail names and nominal values are chipset-specific.",
        confirm_measurements=[("pch_power_good", "in_range", None, None)],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["pwr_ok"],
        alternative_hypotheses=["PCH rail or regulator enable missing.", "PCH reset held active.", "Reference clock or sequencing dependency missing."],
        next_measurement="Verify the board-specific PCH rails, PCH_PWROK and reset sequencing against the board documentation.",
        next_measurement_info_value="Separates platform-controller power sequencing from CPU VRM and memory faults.",
    ),
    SequenceStage(
        stage_id="bios_spi", name="BIOS / SPI firmware interface",
        description="The SPI flash and its power/CS#/CLK/DO/DI path must allow firmware reads. A valid SPI transaction is evidence of bus activity, not proof that the firmware image is correct.",
        confirm_measurements=[("bios_spi_activity", "in_range", None, None)],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["pch"],
        alternative_hypotheses=["SPI flash supply missing.", "CS#/CLK/data path or series component fault.", "Firmware corruption, wrong image, or platform descriptor/configuration issue."],
        next_measurement="Observe SPI CS#/CLK/data activity during reset/startup and verify flash supply at the device.",
        next_measurement_info_value="Distinguishes no firmware-bus activity from later CPU/POST failures; bus activity alone does not validate firmware contents.",
    ),
    SequenceStage(
        stage_id="clock_reset", name="Reference clocks and reset release",
        description="Platform reference clocks must be present and required reset signals (for example PLTRST#) must de-assert in the correct order.",
        confirm_measurements=[("reset_deasserted", "in_range", None, None), ("cpu_clock_present", "in_range", None, None)],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["pch", "bios_spi"],
        alternative_hypotheses=["Clock generator not enabled or missing reference clock.", "PCH/CPU reset held active.", "Missing power-good dependency keeps reset asserted."],
        next_measurement="Check the relevant reference clock and reset signals with an appropriate instrument and board-specific test points.",
        next_measurement_info_value="A missing clock or asserted reset can stop execution before POST even when rails are present.",
    ),
    SequenceStage(
        stage_id="pcie_gpu_aux", name="PCIe and GPU auxiliary power",
        description="For a discrete GPU, verify PCIe slot power and the required 6/8/12/16-pin auxiliary inputs. This stage is not applicable to systems using only integrated graphics; omit its measurements in that case.",
        confirm_measurements=[("pcie_12v_voltage", "in_range", 11.4, 12.6)],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["pwr_ok"],
        alternative_hypotheses=["PCIe slot 12 V distribution or fuse fault.", "GPU auxiliary cable/connector fault.", "GPU input protection or board-level power fault."],
        next_measurement="Verify PCIe slot +12 V and each required GPU auxiliary input at its connector.",
        next_measurement_info_value="Separates host POST sequencing from a missing discrete-GPU power path; skip for iGPU-only systems.",
    ),
    SequenceStage(
        stage_id="post", name="POST execution",
        description="The CPU executes platform firmware and POST. Confirm through progressing POST codes, debug LEDs or another board-supported diagnostic output.",
        confirm_measurements=[("post_progressing", "in_range", None, None)],
        fault_if_failed=FAULT_NO_POST,
        depends_on=["cpu_vrm", "memory", "bios_spi", "clock_reset"],
        alternative_hypotheses=["CPU/socket/BIOS compatibility issue.", "Memory training failure.", "Firmware execution blocked by platform initialization or peripheral fault."],
        next_measurement="Capture POST-code progression or board debug indicators from power-on until the sequence stops.",
        next_measurement_info_value="Localizes the earliest visible firmware/initialization checkpoint.",
    ),
    SequenceStage(
        stage_id="display", name="Display output",
        description="Video output must be present from the active graphics device. For discrete GPUs, confirm PCIe and auxiliary power first; for iGPU systems use the motherboard display output.",
        confirm_measurements=[("display_data_present", "in_range", None, None)],
        fault_if_failed=FAULT_NO_DISPLAY,
        depends_on=["post"],
        alternative_hypotheses=["Wrong active display adapter/output.", "GPU/display engine fault.", "Cable/monitor/input selection issue."],
        next_measurement="Test a known-good display, cable and alternate output; check GPU initialization indicators.",
        next_measurement_info_value="Separates a board that completes POST from a display-path failure.",
    ),
    SequenceStage(
        stage_id="peripheral_usb", name="Optional USB VBUS check",
        description="Supplemental interface check only; USB VBUS is not a prerequisite for CPU POST or video output.",
        confirm_measurements=[("usb_vbus_voltage", "in_range", 4.5, 5.5)],
        fault_if_failed="peripheral_io_fault", depends_on=["display"], optional=True,
        alternative_hypotheses=["USB power switch/port protection fault.", "Port fuse or connector path open.", "USB controller enable/reset issue."],
        next_measurement="Measure VBUS at a known-good USB port and then at the output of its power switch.",
        next_measurement_info_value="Localizes a USB-only power fault without misclassifying a system that has already POSTed.",
    ),
    SequenceStage(
        stage_id="peripheral_audio", name="Optional audio codec power check",
        description="Supplemental interface check only; absence of audio codec power does not imply failure of the core boot sequence.",
        confirm_measurements=[("audio_codec_rail_present", "in_range", None, None)],
        fault_if_failed="peripheral_io_fault", depends_on=["display"], optional=True,
        alternative_hypotheses=["Codec supply missing.", "Codec reset/clock held inactive.", "Codec or surrounding passive-network fault."],
        next_measurement="Check the board-specific audio codec supply, reset and reference clock.",
        next_measurement_info_value="Separates an audio subsystem fault from a motherboard POST failure.",
    ),
    SequenceStage(
        stage_id="peripheral_lan", name="Optional LAN PHY power check",
        description="Supplemental interface check only; LAN PHY power/clock/reset is not a prerequisite for CPU POST or display.",
        confirm_measurements=[("lan_phy_rail_present", "in_range", None, None)],
        fault_if_failed="peripheral_io_fault", depends_on=["display"], optional=True,
        alternative_hypotheses=["LAN PHY supply missing.", "PHY reset or reference clock absent.", "Magnetics/RJ45 or interface-path fault."],
        next_measurement="Check the PHY supply, reset and reference clock at the LAN controller.",
        next_measurement_info_value="Keeps Ethernet-specific faults separate from the core power-on sequence.",
    ),
]



class PowerOnSequenceEngine:
    """Walks the power-on sequence and identifies the earliest failed stage."""

    def __init__(self):
        self.laptop_stages = list(_LAPTOP_STAGES)
        self.desktop_stages = list(_DESKTOP_STAGES)

    def stages_for(self, platform: str) -> list[SequenceStage]:
        if platform == "laptop":
            return self.laptop_stages
        if platform == "desktop":
            return self.desktop_stages
        return []

    def diagnose(self, inp: PowerOnSequenceInput) -> EngineConclusion:
        """Walk the sequence and identify the earliest failed stage."""
        conclusion = EngineConclusion(
            engine=f"motherboard.power_on_sequence.{inp.platform}",
            rule_id=None,
            next_steps=[],
        )

        stages = self.stages_for(inp.platform)
        if not stages:
            conclusion.requires_info = True
            conclusion.requires_info_note = (
                f"Unknown platform {inp.platform!r}. Supported: 'laptop', 'desktop'."
            )
            return conclusion

        # Build a lookup of measurements by name.
        by_name: dict[str, Measurement] = {}
        for m in inp.measurements:
            by_name[m.name] = m

        # Walk the stages in order. The first stage whose confirm
        # measurements are NOT all satisfied is the fault point.
        # A MEASURED failure takes priority over observed symptoms —
        # a measured fault is more specific than a symptom. Symptoms
        # are used when no stage has a measured failure.
        missing_measurements: list[str] = []
        # Phase 4: precompute which stages have at least one supplied
        # confirm measurement. If a stage has NONE supplied AND a later
        # stage DOES, skip the unmeasured stage (don't block) — the
        # technician may have started mid-sequence, or the stage is N/A
        # (e.g. vrm_gpu on an iGPU-only laptop). This preserves backward
        # compat with measurement sets that predate the new early stages.
        supplied_names = set(by_name.keys())
        stages_with_supply = set()
        for st in stages:
            if any(name in supplied_names
                   for name, _, _, _ in st.confirm_measurements):
                stages_with_supply.add(st.stage_id)
        # Index of the last stage that has any supplied measurement.
        supplied_indices = [i for i, st in enumerate(stages)
                            if st.stage_id in stages_with_supply and not st.optional]
        last_supplied_idx = supplied_indices[-1] if supplied_indices else -1

        optional_failures: list[SequenceStage] = []
        optional_incomplete: list[str] = []
        for idx, stage in enumerate(stages):
            stage_confirmed, stage_missing = self._stage_confirmed(stage, by_name)
            missing_measurements.extend(stage_missing)
            if stage.optional:
                stage_supplied = stage.stage_id in stages_with_supply
                if not stage_supplied:
                    continue
                if stage_missing:
                    optional_incomplete.extend(stage_missing)
                    continue
                if not stage_confirmed:
                    optional_failures.append(stage)
                continue
            if not stage_confirmed:
                # If the stage has missing measurements (not measured at all),
                # we cannot conclude it failed.
                if stage_missing:
                    # Phase 4: if NONE of this stage's confirm measurements
                    # were supplied AND a later stage HAS supplied
                    # measurements, skip this stage (don't block).
                    stage_supplied = stage.stage_id in stages_with_supply
                    if not stage_supplied and idx < last_supplied_idx:
                        continue
                    # Otherwise surface symptoms or insufficient-data.
                    symptoms_lower_check = " ".join(inp.observed_symptoms).lower()
                    has_symptom = any(k in symptoms_lower_check for k in
                                      ("shutdown", "shuts down", "restart", "boot loop",
                                       "charg")) and \
                        (inp.platform == "laptop" or "charg" not in symptoms_lower_check)
                    if has_symptom:
                        return self._symptom_conclusion(inp, stages)
                    return self._build_insufficient_data_conclusion(
                        inp, stage, missing_measurements,
                    )
                # The stage was measured and failed.
                return self._build_failed_stage_conclusion(inp, stage)

        # Supplemental USB/audio/LAN checks run after the core boot path.
        # A failure here is reported as an interface fault, never as no-POST.
        if optional_failures:
            result = self._build_failed_stage_conclusion(inp, optional_failures[0])
            result.notes.insert(0, "Core power-on stages (through display) were confirmed; the reported fault is in an optional peripheral interface check.")
            if len(optional_failures) > 1:
                result.notes.append("Additional failed optional checks: " + ", ".join(s.stage_id for s in optional_failures[1:]))
            if optional_incomplete:
                result.notes.append("Optional checks not fully measured: " + ", ".join(optional_incomplete))
            return result

        # No measured stage failure and no NEED_DATA block. Check for
        # explicit symptoms (shutdown, restart_loop, charging_fault).
        symptoms_lower = " ".join(inp.observed_symptoms).lower()
        if symptoms_lower:
            return self._symptom_conclusion(inp, stages)

        # No symptoms and no measured failure -> all stages confirmed
        # healthy. Delegate to the healthy conclusion builder.
        return self._build_healthy_conclusion(inp, stages)

    def _symptom_conclusion(self, inp: PowerOnSequenceInput,
                           stages: list[SequenceStage]) -> EngineConclusion:
        """Dispatch to the right symptom-driven conclusion based on
        observed symptoms. Used when no measured stage failure exists."""
        symptoms_lower = " ".join(inp.observed_symptoms).lower()
        if "shutdown" in symptoms_lower or "shuts down" in symptoms_lower:
            return self._build_symptom_conclusion(
                inp, FAULT_SHUTDOWN, stages,
                "Board powers up then shuts down. This is typically a protection trip (OCP/OVP/OTP) "
                "or a PGOOD timeout. Measure the rails during the brief on-window: which rail collapses "
                "first? A rail that comes up then drops indicates its load is triggering protection. "
                "A rail that never reaches PGOOD indicates a feedback or sequencing fault.",
            )
        if "restart" in symptoms_lower or "restart loop" in symptoms_lower or "boot loop" in symptoms_lower:
            return self._build_symptom_conclusion(
                inp, FAULT_RESTART_LOOP, stages,
                "Board restarts repeatedly. Common causes: CPU VRM cannot hold VCORE under load "
                "(bad output cap), memory training failure (bad DIMM/slot/VTT), or BIOS corruption. "
                "Capture POST codes during each restart cycle — a code that repeats points to the "
                "failing subsystem.",
            )
        if "charg" in symptoms_lower and inp.platform == "laptop":
            return self._build_symptom_conclusion(
                inp, FAULT_CHARGING, stages,
                "Battery won't charge. Check: adapter sense pin (center pin) continuity, charger IC "
                "EN, battery FET, and the gas gauge communication. A laptop that runs off adapter but "
                "won't charge the battery usually has a charger IC or sense-pin fault, not a battery fault.",
            )
        # No recognised symptom — fall through to healthy
        return self._build_healthy_conclusion(inp, stages)

    def _build_healthy_conclusion(self, inp: PowerOnSequenceInput,
                                  stages: list[SequenceStage]) -> EngineConclusion:
        conclusion = EngineConclusion(
            engine=f"motherboard.power_on_sequence.{inp.platform}",
            rule_id="POWER_ON_SEQUENCE_HEALTHY",
            next_steps=[],
        )
        required_stages = [stage for stage in stages if not stage.optional]
        conclusion.notes.append(
            f"All {len(required_stages)} required core power-on stages confirmed by the supplied measurements. "
            "The board reaches the display stage. Optional USB/audio/LAN checks are separate and do not gate POST. "
            "If the symptom persists, investigate the relevant peripheral or software/storage path."
        )
        h = Hypothesis(
            statement="Board power-on sequence is healthy per supplied measurements.",
            engine=conclusion.engine,
            rule_source="POWER_ON_SEQUENCE",
        )
        h.add_evidence(Evidence(
            type=EvidenceType.SUPPORTING,
            strength=EvidenceStrength.STRONG,
            description=f"All {len(required_stages)} required core sequence stages confirmed",
            rule_source="POWER_ON_SEQUENCE",
            source_kind="measurement",
        ))
        conclusion.new_hypotheses.append(h)
        return conclusion

    # --- stage checking ------------------------------------------------

    def _stage_confirmed(self, stage: SequenceStage,
                         by_name: dict[str, Measurement]) -> tuple[bool, list[str]]:
        """Return (confirmed, missing_measurements).

        confirmed=True only when ALL confirm_measurements are present
        and satisfy their expected state+value. A measurement that is
        NOT_MEASURED counts as missing (not as failed). A measurement
        that is MEASURED_ZERO or out of range counts as failed
        (confirmed=False, not missing).

        For logic-level signals (v_min/v_max both None), a MEASURED_ZERO
        reading means the signal is absent — that is a FAILED stage,
        not a confirmed one. Only an IN_RANGE non-zero reading confirms
        a logic signal.
        """
        missing: list[str] = []
        for name, expected_state, v_min, v_max in stage.confirm_measurements:
            m = by_name.get(name)
            if m is None or m.state == MeasurementState.NOT_MEASURED:
                missing.append(name)
                continue
            # Check state
            if m.state.value != expected_state and expected_state != "in_range":
                return False, missing  # measured but wrong state = failed
            if expected_state == "in_range":
                # For voltage signals (v_min/v_max set): MEASURED_ZERO is
                # a failed rail (absent), IN_RANGE is potentially confirmed.
                # For logic signals (v_min/v_max None): MEASURED_ZERO is
                # a failed/absent signal; only IN_RANGE confirms.
                if v_min is not None and v_max is not None:
                    if m.state not in (MeasurementState.IN_RANGE,):
                        # MEASURED_ZERO on a voltage rail = failed
                        return False, missing
                else:
                    # Logic signal: only IN_RANGE confirms
                    if m.state != MeasurementState.IN_RANGE:
                        return False, missing
            # Check value bounds (only for IN_RANGE with numeric value)
            if v_min is not None and v_max is not None:
                val = m.numeric_value
                if val is None:
                    # Non-numeric value where we expected a voltage — treat as missing
                    missing.append(name)
                    continue
                if val == 0.0:
                    # Zero is failed (rail absent), not confirmed
                    return False, missing
                if not (v_min <= val <= v_max):
                    return False, missing
            else:
                # Logic signal: a zero numeric value means absent/failed
                val = m.numeric_value
                if val is not None and val == 0.0:
                    return False, missing
        # If any missing, not confirmed yet
        if missing:
            return False, missing
        return True, []

    # --- conclusion builders -------------------------------------------

    def _build_failed_stage_conclusion(self, inp: PowerOnSequenceInput,
                                       stage: SequenceStage) -> EngineConclusion:
        conclusion = EngineConclusion(
            engine=f"motherboard.power_on_sequence.{inp.platform}",
            rule_id=f"POWER_ON_SEQUENCE_FAIL_{stage.stage_id}",
            next_steps=[],
        )
        conclusion.notes.append(
            f"Sequence failed at stage '{stage.name}' ({stage.stage_id}). "
            f"Fault classification: {stage.fault_if_failed}."
        )
        conclusion.notes.append(f"Stage description: {stage.description}")

        # Phase 4: dependency caveat. If this stage's dependencies are
        # not all confirmed-healthy, the localization is provisional —
        # the real fault may be upstream. Do NOT present this stage as
        # confirmed-defective when an upstream stage is unverified.
        dep_caveat = ""
        if stage.depends_on:
            by_name = {m.name: m for m in inp.measurements}
            unverified_deps = []
            for dep_id in stage.depends_on:
                dep_stage = next((s for s in self.stages_for(inp.platform)
                                  if s.stage_id == dep_id), None)
                if dep_stage is None:
                    continue
                confirmed, _ = self._stage_confirmed(dep_stage, by_name)
                if not confirmed:
                    unverified_deps.append(dep_id)
            if unverified_deps:
                dep_caveat = (
                    f"DEPENDENCY CAVEAT: upstream stage(s) "
                    f"{unverified_deps} are not confirmed-healthy. The "
                    f"localization to '{stage.stage_id}' is PROVISIONAL — "
                    f"verify the upstream stages first; the fault may be "
                    f"upstream, not here."
                )
                conclusion.notes.append(dep_caveat)

        # Phase 4: alternative hypotheses. A single missing output does
        # NOT prove the stage's IC is dead. Surface the alternatives so
        # the technician investigates before condemning a component.
        if stage.alternative_hypotheses:
            conclusion.notes.append(
                "ALTERNATIVE HYPOTHESES (do not condemn the IC from one "
                "missing output — investigate these first): "
                + " | ".join(stage.alternative_hypotheses)
            )

        # Build the next-step: the stage's high-value next measurement
        # (info_value explains WHY this measurement is selected).
        next_action = (
            f"Power-on sequence stopped at '{stage.name}'. {stage.description} "
            f"Re-verify the stage's confirm measurements, then diagnose the "
            f"subsystem that feeds this stage."
        )
        if stage.next_measurement:
            next_action += (
                f" RECOMMENDED NEXT MEASUREMENT: {stage.next_measurement}"
            )
            if stage.next_measurement_info_value:
                next_action += (
                    f" (Why: {stage.next_measurement_info_value})"
                )
        if dep_caveat:
            next_action += (
                " NOTE: verify the upstream dependency stages before "
                "concluding this stage is defective."
            )
        next_step = {
            "step": 1,
            "action": next_action,
            "measure": {
                "what": f"Confirm measurements for stage '{stage.name}'",
                "where": ", ".join(name for name, _, _, _ in stage.confirm_measurements),
                "instrument": "DMM + oscilloscope",
                "expected": "All confirm measurements at expected state and value.",
                "safety": "Powered measurement for voltage stages; unpowered for resistance follow-up.",
            },
            "interpretations": [
                {"if_result": f"{stage.stage_id} now confirmed",
                 "then": "Stage was a measurement artifact. Continue to the next stage.",
                 "set_facts": []},
                {"if_result": f"{stage.stage_id} still failed",
                 "then": f"Fault is in the {stage.name} subsystem or its upstream feed. "
                         f"Use the power-rail engine on the relevant rail family for detailed diagnosis.",
                 "set_facts": [f"{stage.stage_id}_failed=True"]},
            ],
            "requires_info": False,
        }
        conclusion.next_steps.append(next_step)

        # Phase 4: evidence-ranked hypotheses. The PRIMARY hypothesis is
        # the stage failure (STRONG); each alternative is a WEAK
        # supporting hypothesis. This gives the technician a ranked list
        # rather than a single "IC is dead" conclusion, preventing the
        # one-missing-output-=>-IC-dead fallacy.
        h = Hypothesis(
            statement=(
                f"Root cause is in the '{stage.name}' stage "
                f"(fault class: {stage.fault_if_failed})."
            ),
            engine=conclusion.engine,
            rule_source=conclusion.rule_id,
        )
        h.add_evidence(Evidence(
            type=EvidenceType.SUPPORTING,
            strength=EvidenceStrength.STRONG,
            description=f"Stage {stage.stage_id} confirm measurements failed",
            rule_source=conclusion.rule_id,
            source_kind="measurement",
        ))
        conclusion.new_hypotheses.append(h)
        for alt in stage.alternative_hypotheses:
            ha = Hypothesis(
                statement=f"Alternative: {alt}",
                engine=conclusion.engine,
                rule_source=conclusion.rule_id,
            )
            ha.add_evidence(Evidence(
                type=EvidenceType.SUPPORTING,
                strength=EvidenceStrength.WEAK,
                description=f"Possible root cause for {stage.stage_id} failure",
                rule_source=conclusion.rule_id,
                source_kind="reasoning",
            ))
            conclusion.new_hypotheses.append(ha)
        return conclusion

    def _build_insufficient_data_conclusion(self, inp: PowerOnSequenceInput,
                                            stage: SequenceStage,
                                            missing: list[str]) -> EngineConclusion:
        conclusion = EngineConclusion(
            engine=f"motherboard.power_on_sequence.{inp.platform}",
            rule_id=f"POWER_ON_SEQUENCE_NEED_DATA_{stage.stage_id}",
            next_steps=[],
            requires_info=True,
            requires_info_note=(
                f"Cannot confirm stage '{stage.name}' — the following measurements "
                f"were not supplied: {missing}. Measure them to advance the sequence."
            ),
        )
        conclusion.notes.append(
            f"Sequence walk paused at '{stage.name}'. Missing: {missing}."
        )
        next_step = {
            "step": 1,
            "action": (
                f"Measure the missing confirm signals for stage '{stage.name}' to "
                f"advance the power-on sequence walk."
            ),
            "measure": {
                "what": ", ".join(missing),
                "where": "Per the stage description",
                "instrument": "DMM + oscilloscope",
                "expected": "Per the stage's expected state and value range.",
                "safety": "Powered for voltage/logic; unpowered for resistance.",
            },
            "interpretations": [],
            "requires_info": True,
            "requires_info_note": f"Supply: {missing}",
        }
        conclusion.next_steps.append(next_step)
        return conclusion

    def _build_symptom_conclusion(self, inp: PowerOnSequenceInput,
                                  fault: str, stages: list[SequenceStage],
                                  guidance: str) -> EngineConclusion:
        conclusion = EngineConclusion(
            engine=f"motherboard.power_on_sequence.{inp.platform}",
            rule_id=f"POWER_ON_SEQUENCE_SYMPTOM_{fault.upper()}",
            next_steps=[],
        )
        conclusion.notes.append(f"Symptom-driven fault classification: {fault}.")
        conclusion.notes.append(guidance)
        next_step = {
            "step": 1,
            "action": guidance,
            "measure": {
                "what": f"Diagnose {fault}",
                "where": "Per the guidance",
                "instrument": "DMM + oscilloscope + POST code reader",
                "expected": "Identify the failing subsystem.",
                "safety": "Powered measurement; use current-limited supply for short hunting.",
            },
            "interpretations": [],
            "requires_info": False,
        }
        conclusion.next_steps.append(next_step)
        h = Hypothesis(
            statement=f"Observed symptom indicates {fault}.",
            engine=conclusion.engine,
            rule_source=conclusion.rule_id,
        )
        h.add_evidence(Evidence(
            type=EvidenceType.BACKGROUND,
            strength=EvidenceStrength.MODERATE,
            description=f"Observed symptom: {fault}",
            rule_source=conclusion.rule_id,
            source_kind="observation",
        ))
        conclusion.new_hypotheses.append(h)
        return conclusion
