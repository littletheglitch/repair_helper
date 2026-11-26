# Source Audit Report — Electronics Diagnostic Expansion Pack v1.4.0

Independent technical code audit. The audit suite (`Tests/test_audit.py`,
32 tests) probed the EXISTING v1.4.0 source without modifying it. This
report documents every defect found, with exact source location,
reproduction, actual vs expected behavior, and severity. Fixes follow in
a separate phase; this report records the pre-fix state.

## Audit method

- Read the actual source (engine.py, PowerSupply/engine.py,
  CircuitAnalysis/netlist.py, replacement_engine.py, component_db.py,
  knowledge_platform.py, host_adapter.py) — did not trust v1.4.0 docs.
- Wrote `Tests/test_audit.py` probing each audit area against the
  EXISTING code. Defect-confirming tests assert the buggy behavior (so
  they PASS when the defect is present); correctness tests assert the
  right behavior (so they FAIL when the defect is present).
- Ran the suite: 29 PASS, 3 hard FAIL. Interpreted each result against
  the source to distinguish "defect present" from "correct behavior".

## Defect summary

| ID | Area | Severity | Status |
|---|---|---|---|
| A0-2 | Core semantics | Low | Conflict clears `_measured` but not `_present` |
| A0-4 | Core semantics | Medium | Simultaneous faults suppressed by priority |
| A1-1 | Power supply | High | All mains topologies share one workflow |
| A1-2 | Power supply | High | LLC resonant_tank never walked |
| A2-1 | Netlist graph | High | No external-port concept; ports flagged floating |
| A2-2 | Netlist graph | Medium | Multi-pin floating nets undetected |
| A2-3 | Netlist graph | High | trace_path fails on passive intermediates |
| A3-1 | Component DB | Medium | Zener Vz stored in vrrm_v field |
| A6-1 | Host adapter | High | INSUFFICIENT_DATA doesn't set requires_info |
| A6-2 | Host adapter | Medium | No 'knowledge' engine |
| A6-3 | Host adapter | Medium | No 'netlist' engine |

## Confirmed NOT defects (audit verified correct)

- A0-1: legacy `Measurement` has no `state` field — by-design limitation
  (the framework Measurement has it; the power-rail engine uses the
  legacy one). Documented in KNOWN_LIMITATIONS, not a defect.
- A0-3: 3rd conflicting reading — the conflict IS recorded on the 2nd
  reading; the 3rd matching the 1st is acceptable (the disagreement was
  already flagged). Correct.
- A1-4: low-voltage topologies DO walk their own stages; buck vs linear
  localize differently. Correct.
- A2-4/A2-5: disconnected subcircuits and loops in trace_path terminate
  correctly (visited-set guards). Correct.
- A2-6: same components / different connectivity → different structure
  (buck vs boost). Correct.
- A3-2: TVS VWM/VBR/VC are separate fields with distinct values. Correct.
- A3-3: BJT hfe checked; lower hfe = mismatch. Correct.
- A3-4: missing critical spec → not COMPATIBLE. Correct.
- A3-5: invalid part → UNKNOWN (not a misleading success). Correct.
- A3-6: LDO Vout mismatch = mismatch. Correct.
- A4-1..A4-4: knowledge ranking, verified_only exclusion, backward-compat
  all correct. (Duplicates ARE returned — A4-1 noted as a minor
  limitation, not a defect: dedup is the caller's responsibility.)
- A6-4: malformed payload → ok=False (exception caught). Correct.
- A7: unrecognized netlist → 'unknown'; empty netlist → NEED_DATA. Correct.

---

## Defect details

### A0-2 — Conflict clears `_measured` but not `_present` (Low)

**Source**: `DiagnosticEngines/core/engine.py`,
`_facts_from_measurements`, lines 388-399.

**Repro**: two `rail_voltage` measurements (3.3V and 0.0V).
```
facts = DiagnosticEngine._facts_from_measurements(
    [M("rail_voltage", 3.3), M("rail_voltage", 0.0)], rail)
```

**Actual**: `rail_voltage=None`, `rail_voltage_measured=None`,
`rail_voltage_present=True`, conflicts recorded.

**Expected**: `rail_voltage_present` should also be None — a conflict
invalidates the present/absent determination, not just the value.

**Why it matters**: a rule checking `rail_voltage_present` sees True
despite the conflict, potentially firing on contradictory data.

---

### A0-4 — Simultaneous faults suppressed by priority (Medium)

**Source**: `DiagnosticEngines/core/engine.py`, `diagnose`, rule
evaluation loop (priority sort, first-match-wins).

**Repro**: `rail_short=True` (confirmed short) AND `vin=0.0` (vin
absent).
```
r = eng.diagnose("+3VALW", [
    M("rail_voltage", 0.0), M("rail_short", True),
    M("vin", 0.0, measured=True)])
```

**Actual**: `fired_rule_id=RULE_ALW_001_RESISTANCE_FIRST`. The confirmed
short and the vin-absent fault are BOTH present, but only one rule
fires and the vin fault is never mentioned in notes.

**Expected**: when two independent faults are both measured-true, the
conclusion should surface BOTH (or at least note the secondary fault),
not silently drop the lower-priority one.

**Why it matters**: a real board can have a short AND a dead input
stage. Suppressing one hides a real fault from the technician.

---

### A1-1 — All mains topologies share one workflow (High)

**Source**: `DiagnosticEngines/PowerSupply/engine.py`, `diagnose`
lines 118-122 dispatches mains → `_build_mains_workflow`, which is a
fixed 3-step sequence (safety gate → secondary output check → standby
check) identical for ALL 8 mains topologies.

**Repro**: diagnose `no_output` on flyback, forward, push_pull,
half_bridge, full_bridge, llc, pfc, atx. Compare `next_steps` actions.

**Actual**: all 8 produce byte-identical next-step action strings.

**Expected**: each topology's stage model (the structurally-distinct
JSON regenerated in v1.4.0) should drive the workflow. The
resonant_tank, transformer, PFC, standby_supply stages are dead data
for mains topologies.

**Why it matters**: the v1.4.0 claim "the workflow walks each
topology's own stages" is false for mains. The 8 mains topologies are
indistinguishable in their executable logic.

---

### A1-2 — LLC resonant_tank never walked (High, consequence of A1-1)

**Source**: consequence of A1-1. LLC is mains → `_build_mains_workflow`
→ no stage walk → `resonant_freq` measurement ignored.

**Repro**: LLC `no_output` with `resonant_freq` measured absent.
```
r = eng.diagnose(PowerSupplyDiagnosisInput(
    topology_id="PSUP_llc", symptom="no_output",
    measurements=[V("input_voltage",380), ZM("output_voltage"),
                  P("switching"), A("resonant_freq")]))
```

**Actual**: `resonant_freq` never appears in any next-step action. The
LLC's distinctive resonant_tank stage is not exercised.

**Expected**: the resonant_tank is a SECONDARY-observable (output
shape/ripple reveals detuning); the engine should consume it without
violating the mains safety gate (which blocks PRIMARY-side probing only).

---

### A2-1 — No external-port concept; ports flagged floating (High)

**Source**: `DiagnosticEngines/CircuitAnalysis/netlist.py`, `validate`,
lines 190-199. Any net with exactly one connection is flagged
`floating`.

**Repro**: a netlist with a single-pin input jack.
```
nl = Netlist.from_dict({"components": [
    {"refdes": "J1", "part": "input_jack", "pins": {
        "P": pin("VIN_EXT", "power_in", "in")}}],
    "nets": ["VIN_EXT"]})
issues = nl.validate()  # flags VIN_EXT as floating
```

**Actual**: `VIN_EXT` is flagged floating.

**Expected**: a single-connection net whose pin is a power_in / input
may be an EXTERNAL PORT (the source is off-board). The engine has no
way to mark a net as an external port, so legitimate ports are
false-positives.

**Why it matters**: real board netlists have input/output connectors;
flagging them floating produces noise that hides real floating nets.

---

### A2-2 — Multi-pin floating nets undetected (Medium)

**Source**: `netlist.py`, `validate`, lines 190-199. Only single-pin
nets are checked.

**Repro**: a net with two passive pins but no driver/source/reference.
```
R1.1 -- FLOAT_NET -- R2.1   (both passive, no source)
```

**Actual**: `FLOAT_NET` (2 connections) is NOT flagged.

**Expected**: a net with only passive pins and no power_in/power_out/
ground/source is floating (no defined potential). The check should
detect "no driver" not just "one pin".

---

### A2-3 — trace_path fails on passive intermediates (High)

**Source**: `netlist.py`, `trace_path`, `_walk` lines 235-248. The
walk only follows pins with direction OUT or BIDIRECTIONAL as
intermediate hops. A passive component (resistor/inductor) has no OUT
pin, so it cannot be an intermediate hop.

**Repro**: SW → L1 (passive) → MID → R1 (passive) → VOUT.
```
path = nl.trace_path("SW", "VOUT")  # returns None
```

**Actual**: `None` — no path found through two passive components.

**Expected**: a path through passive components (resistors, inductors,
caps as coupling) should be traversable. Passive pins are bidirectional
in signal terms.

**Note**: the 2-hop case (SW → L1 → VOUT) appears to work, but only
because `_walk` checks `if p2.net == end_net` BEFORE the direction
check — a direct bridge to the end net bypasses the direction filter.
Multi-hop paths through passive intermediates still fail.

---

### A3-1 — Zener Vz stored in vrrm_v field (Medium)

**Source**: `ComponentDatabase/DiscreteSemiconductors/zener_diode/
1N5231B.json`, `electrical_characteristics.vrrm_v = 5.1`. The
replacement engine's `_eval_zener` reads Vz from `vrrm_v`.

**Repro**: inspect the 1N5231B record.
```
ec = zener["electrical_characteristics"]
ec["vrrm_v"] == 5.1   # this is actually Vz, not VRRM
```

**Actual**: Vz (zener voltage) is stored in the `vrrm_v` field.

**Expected**: Vz and VRRM are semantically distinct. VRRM = maximum
reverse voltage (a stress rating); Vz = the zener/regulation voltage
(a functional spec). A real Zener datasheet lists both. Storing Vz in
`vrrm_v` means a TVS-style VRRM check would misread a Zener, and the
schema conflates two different concepts.

**Why it matters**: schema confusion propagates. The `_eval_zener`
works around it by treating `vrrm_v` as Vz, but this is fragile and
misleading to anyone reading the record.

---

### A6-1 — INSUFFICIENT_DATA doesn't set requires_info (High)

**Source**: `Integration/host_adapter.py`, line 255:
`requires_info=(ev.classification == "UNKNOWN")`.

**Repro**: a controller-IC replacement (TPS51621 → TPS51622) yields
INSUFFICIENT_DATA.
```
r = ad.handle(DiagnosticRequest(engine="replacement",
    payload={"original_part":"TPS51621","candidate_part":"TPS51622"}))
r.conclusion.requires_info  # False
```

**Actual**: `requires_info=False` for INSUFFICIENT_DATA.

**Expected**: INSUFFICIENT_DATA means critical specs are missing —
exactly when the caller needs more info. `requires_info` should be True
for both UNKNOWN and INSUFFICIENT_DATA.

**Why it matters**: the host sees "no info needed" and may trust a
classification that is actually incomplete.

---

### A6-2 — No 'knowledge' engine in host adapter (Medium)

**Source**: `Integration/host_adapter.py`, capabilities list
(lines 110-128) and the `handle` dispatch (no `knowledge` branch).

**Repro**: `ad.handle(DiagnosticRequest(engine="knowledge", ...))`
returns `ok=False`.

**Actual**: the KnowledgeRetriever (P4 work) is unreachable through the
host adapter.

**Expected**: a `knowledge` engine should expose `search(query,
verified_only)`.

---

### A6-3 — No 'netlist' engine in host adapter (Medium)

**Source**: `Integration/host_adapter.py`, no `netlist` branch.

**Repro**: `ad.handle(DiagnosticRequest(engine="netlist", ...))`
returns `ok=False`.

**Actual**: `CircuitAnalysisEngine.analyze_netlist()` (P2 work) is
unreachable through the host adapter.

**Expected**: a `netlist` engine should expose `analyze_netlist(netlist)`.

---

## Severity rationale

- **High** (A1-1, A1-2, A2-1, A2-3, A6-1): these break a claimed
  capability or produce misleading output. A1-1/A1-2 mean the
  "structurally distinct mains workflows" claim is false. A2-1/A2-3
  break the netlist graph's core usefulness. A6-1 misleads the host.
- **Medium** (A0-4, A2-2, A3-1, A6-2, A6-3): real defects with bounded
  impact or workarounds.
- **Low** (A0-2): inconsistency in fact clearing; bounded to conflict
  edge case.

## Audit coverage

- **Source-inspected**: engine.py, PowerSupply/engine.py,
  CircuitAnalysis/netlist.py + engine.py, replacement_engine.py,
  component_db.py, knowledge_platform.py, host_adapter.py, the 1N5231B
  / SMBJ33CA / 2N3904 / TLV70033 records.
- **Synthetic-tested**: all 32 audit tests use synthetic inputs.
- **Unverified**: real hardware behavior (impossible in sandbox).
- **Not audited in depth**: the 15 power-supply topology JSON stage
  descriptions (A1-1 confirms they're dead data for mains, so their
  content is moot until A1-1 is fixed).
