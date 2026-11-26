# MOSFET Replacement Classification Criteria

This document defines the engineering criteria the `ReplacementMappings`
records use to classify a candidate MOSFET as COMPATIBLE, CONDITIONAL,
NOT_RECOMMENDED, or UNKNOWN. It is the human-readable reference behind the
`validate_database.py` check that enforces "no COMPATIBLE classification
without full evidence".

## Why voltage + current ratings are not enough

A MOSFET that matches VDS and ID is not necessarily a drop-in replacement.
The following criteria all matter, and a mismatch on any one of them can
turn a "compatible" swap into a reliability or functionality bug.

### 1. Package and physical compatibility

Same pin count is not enough. SO-8, DFN-8 3×3, DFN-8 2×2, DPAK, and
PowerBlock combo packages all have different land patterns. A candidate
in a different package will not fit the board's footprint without rework,
and even when the pin count matches, the thermal pad geometry differs.

### 2. Pinout

Classic SO-8 N-channel: pins 1–3 source, pin 4 gate, pins 5–8 drain.
DFN-8 parts usually follow the same convention, but some vendors swap
positions or use the exposed pad differently. **Always confirm pin-by-pin
against both datasheets.** A swapped pinout will short the input to
ground and lift the output, destroying the FET and possibly the
controller.

### 3. Voltage rating (VDS)

The candidate VDS must be ≥ the original, with margin to the worst-case
rail voltage including transients. For an adapter-rail high-side switch,
30 V is the floor on a 19 V adapter; 40 V is safer.

### 4. Current rating (ID)

ID must be ≥ the original at the same case temperature. Datasheets quote
ID at T_C=25 °C with a footnote that says "package limited" — the real
usable current is much lower once you derate for ambient and PCB thermal
resistance.

### 5. RDS(on)

Must be ≤ the original at the **same VGS** the controller drives. This is
the most common replacement mistake: a candidate specified at VGS=10 V is
swapped into a 5 V-driven circuit and never fully enhances. Always read
the RDS(on) test condition.

- Logic-level FETs are specified at VGS=4.5 V (sometimes 2.5 V).
- Standard-level FETs are specified at VGS=10 V only.
- **A 5 V-driven gate must use a logic-level FET.**

### 6. Gate drive level

The controller's gate-drive voltage (often 5 V from +5VALW, sometimes
BOOT-derived ~5–6 V for high-side) must meet the FET's VGS(th) with
margin and must not exceed VGS_max (typically ±20 V, but some logic-level
parts are ±12 V or ±8 V).

### 7. Gate charge (Qg)

Qg determines switching losses and the gate-drive power the controller
must supply. A candidate with 2× the original Qg will run hotter and may
over-stress the controller's gate driver at high switching frequencies.

### 8. Thermal characteristics

RθJA, RθJC, and the operating temperature range must be at least as good
as the original. A candidate with higher RθJA in the same package will
run hotter and may trip thermal shutdown under the same load.

### 9. Switching characteristics (t_on, t_off, t_r, t_f)

Matters for high-frequency bucks (>1 MHz). A slow FET will have large
dead-time losses and may shoot-through if the controller's dead-time is
fixed.

## Classification matrix

| Criterion verdict → | all match | some conditional, none mismatch | any mismatch | insufficient data |
| Classification →    | COMPATIBLE | CONDITIONAL                     | NOT_RECOMMENDED | UNKNOWN |

- **COMPATIBLE** requires a `match` verdict on every required criterion
  (package, pinout, voltage_rating, current_rating, rds_on, gate_drive,
  gate_charge, thermal for MOSFETs). The validator enforces this.
- **CONDITIONAL** requires a non-empty `conditions` list explaining what
  the technician must verify on the specific board before substitution.
- **NOT_RECOMMENDED** is used whenever any criterion is a hard mismatch
  (e.g. package, pinout, control_logic, feedback_topology).
- **UNKNOWN** is used when the candidate's datasheet was not consulted.
  The validator emits a warning, never a pass.

## Controller ICs and other firmware-defined parts

For ICs (PWM controllers, charger ICs, ECs, PD controllers, PMICs) the
criteria are different: package, pinout, voltage, current, control_logic,
feedback_topology, enable_threshold, operating_frequency. But the
dominant criterion is **control_logic**: most controllers are
firmware-tuned via SMBus / I2C, and the host EC firmware depends on the
exact part. Even same-family swaps (e.g. BQ24737 → BQ24780S) break the
chemistry tables and current limit. As a rule, controller-IC swaps are
NOT_RECOMMENDED without an explicit cross-reference document from the
vendor or a board service note.

## Sources

The criteria above are standard power-electronics practice. Authoritative
references:

- TI application note SLVA861, "MOSFET selection for synchronous buck
  converters" —
  https://www.ti.com/lit/an/slva861/slva861.pdf
- Vishay AN-608, "Power MOSFET basics" —
  https://www.vishay.com/docs/71217/71217.pdf
- Alpha & Omega Semiconductor application notes —
  https://www.aosmd.com/web/library/apps_notes

(URLs are real manufacturer / application-note pages, recorded for
attribution. The pack does not reproduce their contents.)
