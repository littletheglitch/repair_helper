# Updated Known Limitations (v1.4.1, post-audit)

Honest list of what is partial, missing, or requires real hardware —
updated after the independent audit. The audit found and fixed 11
defects (see SOURCE_AUDIT_REPORT.md, AUDIT_TEST_REPORT.md). The
limitations below are what REMAINS after the fixes.

## Hardware-required (cannot be validated in this sandbox)

- **All measurements are synthetic.** No real DMM, oscilloscope, or
  physical board was used. The diagnostic LOGIC is verified, but
  measurement values, voltage tolerances, and stage sequences are based
  on general electronics knowledge, not board-specific validation.
- Real hardware validation requires a physical board + instruments.

## P0 — Core semantics (1 remaining gap)

- **INVALID measurement state not representable in the power-rail
  engine.** The core engine uses the legacy `Measurement` dataclass
  (`name/value/measured` only — no `state` field). The framework
  `Measurement` has a rich `MeasurementState` enum
  (NOT_MEASURED/MEASURED_ZERO/INVALID/IN_RANGE/...), but the power-rail
  engine does not use it. Consequences:
  - A DMM "OL" (overload) reading cannot be distinguished from a 0.0V
    reading or a not-measured slot in the power-rail engine.
  - The framework `Measurement` (used by PowerSupply, Motherboard,
    MeasurementIntelligence engines) DOES support these states.
  - Fix: migrate the power-rail engine to the framework Measurement.
    Not done in this audit (would touch the rule-condition evaluator
    and all rule JSON files) — left as a documented gap.
- **`no_confirmed_short` is a working assumption, not a proof.** When
  `rail_short` is unmeasured, the chain proceeds on the assumption that
  a short is not confirmed; a CAVEAT is surfaced. Only measuring
  resistance to ground can prove absence of a short.
- **Conflict detection is per-signal-name only.** A `vin` reading that
  contradicts an `input_voltage` reading (same physical net, different
  names) is NOT cross-detected.

## P1 — Power supply (2 remaining gaps)

- **6 mains-isolated topologies share a stage SEQUENCE.** Flyback,
  forward, push-pull, half-bridge, full-bridge, and laptop_adapter have
  the same ordered stage IDs (AC front + transformer + secondary). They
  differ in stage DESCRIPTIONS, COMPONENTS, and FAIL_NOTES. After the
  A1-1 fix, the mains workflow DOES walk each topology's own
  secondary-side stages (so LLC's resonant_tank, flyback's
  rectification_secondary, etc. are now distinct in the executable
  logic). But the stage *sequence* is shared; a truly distinct
  per-topology stage graph is a future enhancement.
- **`switching` treated as primary-side for mains.** The mains
  secondary-side walk blocks `switching` (it's the primary FET's gate
  drive). A secondary-side sync-rectifier switching observation would
  also be blocked. The technician observes output shape instead. This
  is conservative (safe) but may skip a legitimate secondary switching
  signal.
- **No load-step / transient testing.** The engine reasons on static
  measurements.

## P2 — Circuit analysis (3 remaining gaps)

- **No full netlist electrical solver.** The engine recognizes
  structures from connectivity + pin functions, but does NOT simulate
  currents/voltages or verify a design's electrical correctness. It
  answers "what structure is this?" not "does this design work?"
- **Recognition covers 7 structures** (ldo, buck, boost, mosfet_switch,
  opamp, current_sense, relay_driver). Other structures (comparator,
  charger, high-side/low-side switch) return 'unknown' — they use the
  older `recognize_from_component_list` keyword heuristic, not the graph.
- **No image recognition.** Per spec, only structured netlist input is
  supported. Schematics/BoardView are NOT ingested.
- **trace_path finds ONE path.** It does not enumerate all parallel
  paths or detect parallel branches (it returns the first found). After
  the A2-3 fix it DOES traverse passive intermediates and terminates on
  loops/disconnected islands.
- **External ports must be declared.** A net is only treated as an
  external port if listed in `external_ports`. A lone power_in pin
  without that declaration is flagged floating (correct — the off-board
  source is unconfirmed). The caller must declare ports for input/output
  connectors.

## P3 — Component DB/replacement (3 remaining gaps)

- **No invented specs.** Missing specs → INSUFFICIENT_DATA. Many
  real-world cross-vendor substitutions are INSUFFICIENT_DATA, not
  COMPATIBLE — correct behavior, not a defect.
- **Controller/charger IC firmware dependency always conditional.** The
  engine cannot verify EC firmware compatibility from the part number;
  it always flags firmware_dependency as conditional. Controller-IC
  replacements are usually CONDITIONALLY_COMPATIBLE or INSUFFICIENT_DATA.
- **Component DB is small (~40 verified parts).** Real boards use
  thousands; the engine falls back to "consult the datasheet" for parts
  not in the DB.
- **Zener Vz vs VRRM.** After the A3-1 fix, the 1N5231B record has an
  explicit `vz_v`. The evaluator reads `vz_v` (fallback `vrrm_v` for
  old records). For a Zener, VRRM == Vz in practice, but the fields are
  now conceptually separate. Other Zener records (if added) should use
  `vz_v`.

## P4 — Knowledge retrieval (1 remaining gap)

- **Retrieval is lexical, not semantic.** Tokenization + entity boost
  is better than substring, but not true semantic search (no embeddings,
  no synonym expansion beyond the fixed topology-term set). "Step-down
  converter" will NOT match "buck" unless both terms appear.
- **No dedup.** The pre-existing KI store has duplicate titles; the
  retriever returns all matches. Callers dedup if needed.
- **No ingestion of BIOS/firmware/schematics/BoardView.** Per spec,
  these are forbidden categories.

## P6 — Host adapter (no remaining gaps after A6 fixes)

- All 9 engines (7 original + knowledge + netlist) dispatch correctly.
- Unsupported engines return ok=False.
- Exceptions return ok=False (not misleading successes).
- The adapter exposes `verified_only` for the knowledge engine and
  `external_ports`-aware netlist analysis.

## Forbidden by spec (never implemented)

- Ingesting BIOS dumps, firmware images, schematics, or BoardView files.
- Image-based circuit recognition.
- Modifying the original host application.
- Inventing component specs, pinouts, or replacements.
- Weakening tests to make them pass.

## Backward-compatibility (unchanged from v1.4.0)

- `KnowledgeRetriever.search` returns `RetrievalResult` (with
  `.item`/`.score`); `search_items()` preserves the old
  `list[KnowledgeItem]` interface.
- Classification names: `CONDITIONALLY_COMPATIBLE` (was CONDITIONAL);
  missing-critical-spec → `INSUFFICIENT_DATA` (was UNKNOWN).
- Netlist `external_ports` is optional (defaults to empty); old
  netlists without it still work (single-pin power_in nets flagged
  floating — correct behavior).
- Zener records: `vz_v` optional (evaluator falls back to `vrrm_v`).
