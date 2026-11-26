# Safety Policy

Safety rules live IN the engines, not just in this document. The
`SafetyPolicy` class (`DiagnosticEngines/core/framework/safety.py`) is
consulted before any next-test recommendation and before any LLM output
is accepted. BLOCK-level violations stop the workflow.

## Hard rules (BLOCK-level — the action must NOT be performed)

### SAFETY_001 — No mains / primary-side probing
The primary side of any mains-powered supply (ATX PSU, laptop adapter,
charger with PFC, flyback/forward/push-pull/half-bridge/full-bridge/
LLC) is at LIVE MAINS potential whenever the unit is plugged in. The
engine never recommends primary-side probing. Field diagnosis of mains
PSUs ends at the secondary side; primary-side diagnosis requires an
isolation transformer, current-limited bench supply, variac, and
trained personnel.

### SAFETY_002 — No touching charged primary-side bulk capacitors
The primary bulk capacitor holds charge at mains peak after disconnect.
The engine requires discharge verification (DMM, 1000V range, below 30V
after 60s unplugged) before any work. If the bleeder is open, wait
longer or discharge through a proper bleeder resistor.

### SAFETY_003 — No bypassing protection
Bypassing OCP/OVP/fuse/thermal protection is blocked. Protection exists
to prevent fire and injury; defeating it is never an accepted
diagnostic step.

### SAFETY_004 — No uncontrolled current injection
Any current source used for short-hunting must be current-limited to a
value the trace can carry without damage. The engine recommends
0.5 A for low-voltage rails, 100 mA for VCCRTC, and never recommends
"apply full adapter" or "direct short" approaches.

### SAFETY_005 — No resistance measurement on a powered rail
Resistance mode drives a small current; on a powered rail this can
damage the DMM and gives a meaningless reading. The engine blocks any
"measure resistance" action whose context contains the word "powered"
(but not "unpowered").

## Caution rules (CAUTION-level — the technician is warned)

### SAFETY_006 — Discharge before resistance
When measuring resistance, verify all bulk and ceramic capacitors are
discharged first. Charged caps can damage the DMM and give false
readings.

## LLM safety gate

The LLM orchestrator runs the SafetyPolicy over every LLM output. If
the output recommends an action that trips a BLOCK-level rule, the
output is suppressed and `rejected_by_safety=True` is returned. The LLM
cannot override safety rules.

## Evidence gate

The LLM orchestrator also rejects output that contradicts confirmed
measurement evidence. If a confirmed fact says "rail is shorted" and
the LLM says "the rail is not shorted", the output is suppressed and
`rejected_by_evidence=True` is returned. The LLM cannot override
confirmed evidence.

## Where safety is enforced

- `SafetyPolicy.evaluate(action)` — called by NextTestSelector and by
  every engine before emitting a next step.
- `MotherboardEngine._safety_check_next_steps` — runs the policy over
  each step's action+measure.
- `PowerSupplyEngine._build_mains_workflow` — emits an explicit SAFETY
  GATE as step 1 of every mains-PSU diagnosis.
- `LLMOrchestrator.explain` — runs the policy over LLM output.
