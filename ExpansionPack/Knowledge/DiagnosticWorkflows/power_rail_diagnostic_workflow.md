# Power-Rail Diagnostic Workflow (master reference)

This document is the human-readable companion to the deterministic rule set
under `DiagnosticEngines/`. It describes the workflow the engine implements
and the engineering reasoning behind each step.

> **Golden rule.** Never apply power to a rail you have not first measured
> resistance-to-ground on, with all sources disconnected. A shorted rail
> will burn the next component you power it through.

## Universal first step: resistance to ground

For every reported "rail missing" symptom:

1. Disconnect adapter, main battery, and CMOS coin cell.
2. Wait at least 30 seconds for the bulk and ceramic capacitors to bleed
   down through their discharge paths.
3. Measure resistance from the rail test point to ground, DMM in resistance
   mode (200 Ω range first, then 2 kΩ).
4. Compare the reading to a known-good board whenever possible. CPU VRM
   rails have intrinsically low normal resistance (5–30 Ω); a generic
   threshold does not work.

Only after resistance is confirmed not-short do you re-apply power and
continue.

## The generic buck-converter workflow

The same logical sequence applies to buck converters, multi-phase VRMs,
and the always-on standby bucks; only the expected values and the
controller-specific pin numbers change.

```
rail missing
  → identify topology + controller part number
  → measure resistance to ground  (power disconnected)
       ├── < 10 Ω  →  SHORTED RAIL  (hunt the short, do NOT power)
       └── normal  →  continue
  → measure controller VIN  (high-side FET drain)
       ├── missing  →  diagnose upstream rail first
       └── present  →  continue
  → measure controller VCC  (logic supply, typically 5 V or 3.3 V)
       ├── missing  →  trace VCC source (usually a small LDO from +5VALW)
       └── present  →  continue
  → measure EN / VR_ON
       ├── disabled level  →  trace the EN signal from the EC / PCH
       └── enabled level   →  continue
  → probe switching node + gate drives
       ├── no switching  →  controller dead OR fault latched (OCP/OVP/OTP)
       │                    check SS / ILIM / fault-register pins before
       │                    condemning the controller
       └── switching OK  →  continue
  → measure FB / VSEN + (for VRM) VID bus
       ├── FB at VREF and rail still missing  →  output stage fault
       │                                        (open inductor, blown
       │                                        sense resistor, shorted
       │                                        output FET)
       └── FB at VREF and rail present  →  continue
  → measure PGOOD
       ├── PGOOD low while rail voltage correct
       │     →  check PGOOD pull-up source, then controller
       └── PGOOD high  →  rail is good; issue is downstream
```

## Controller-specific data

The workflow needs the controller's datasheet at three points:

1. To identify which pin is VIN, VCC, EN, FB, PGOOD.
2. To know the expected VREF value (D-CAP controllers use ~0.6–0.8 V;
   current-mode controllers use the error-amplifier output).
3. To know the active polarity of EN and the PGOOD assertion conditions.

The engine **never invents** these. When a rule reaches a step that needs
controller-specific data and the controller part number is unknown or its
datasheet is NDA-gated, the engine returns `requires_info=True` and tells
the engineer exactly what is missing.

## Topology-specific notes

### Always-on / standby rails (+3VALW, +5VALW, +VCCRTC)

These are the first rails to come up. They are powered directly from the
adapter/battery (or coin cell for VCCRTC) without EC intervention. If they
are dead, the board will not even reach S5 — no LED, no fan, no EC activity.

ALW controllers are typically dual-channel bucks (RT8205, RT8206,
TPS51224) with both +3VALW and +5VALW in one IC. A fault on one channel
often masks a fault on the other; check both.

### CPU / GPU VRM (VCORE, GPU_CORE)

Multi-phase synchronous bucks. Their enable (VR_ON) is gated by the PCH
after the upstream rails (DDR_VDD, VCCSA, +1.8 V) report PGOOD. A missing
VR_ON almost always means an earlier PGOOD is missing — do not condemn the
VRM controller until you have confirmed the upstream PGOODs.

VRM output resistance is intrinsically low. Compare against a known-good
board; do not apply a generic threshold.

### Charger input (ADAPTER_INPUT)

The adapter path is: DC jack → input fuse → input MOSFET → charger IC ACIN.
The charger IC gates the input MOSFET via ACOK; if ACOK is low the MOSFET
stays off and the board sees no adapter. ACOK can be low because:

- ACIN is below the charger's under-voltage threshold.
- The adapter-ID pin (middle pin on Dell/HP jacks) is open — the EC
  refuses to enable charging without a valid ID.
- The EC has deliberately disabled the charger (OTP, wrong adapter,
  battery-only mode).

Always measure the adapter voltage at the jack first, then walk forward.

### DDR VTT

DDR_VTT must source AND sink current and must track DDR_VDD/2. A standard
LDO cannot do this — VTT regulators are a special class (TPS51200,
RT9179). A VTT rail that is high by ~VDDQ/2 (e.g. 0.6 V on a 1.2 V DDR4
system) usually means the VTT regulator lost its reference, not a short.

## Safety

- Always current-limit a bench supply used for short-hunting. 0.5 A is
  plenty for low-voltage rails; 100 mA is plenty for VCCRTC.
- Discharge capacitors before measuring resistance.
- Use a ground spring on oscilloscope probes, not the long alligator clip.
- Beware shorting adjacent pads with a probe tip on dense QFN packages.
- Adapter input rails carry 19 V at several amps. A probe slip can burn
  a trace or weld the probe tip.

## When the engine cannot decide

The engine is deterministic, not omniscient. It will return
`requires_info=True` when:

- A rule's condition references a measurement that was not supplied.
- The rail family or topology is not covered by the rule database.
- The controller part number is unknown and the next step needs the
  datasheet.

This is by design. The engine refuses to guess; the engineer supplies the
missing measurement or the controller identity, and the workflow
continues.
