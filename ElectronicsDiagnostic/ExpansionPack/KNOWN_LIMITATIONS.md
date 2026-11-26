# Known Limitations (v1.5.0)

Honest list of what is partial, missing, or requires real hardware —
after the v1.5.0 engineering phase. No module is marked Complete merely
because it has tests or records.

## Hardware-required (cannot be validated in this sandbox)

- **All measurements are synthetic.** No real DMM, oscilloscope, or
  physical board was used. The diagnostic LOGIC is verified by 350
  synthetic tests, but measurement values, voltage tolerances, and
  stage-sequence timing are based on general electronics knowledge, not
  board-specific validation.
- Real hardware validation requires a physical board + instruments:
  - Real measurement values (voltages, resistances, waveforms)
  - Voltage-tolerance compliance on a physical rail
  - Stage-sequence timing on a real power-on
  - Component replacement on a live board

## Phase 1 — Power-rail Measurement

- **Framework Measurement adoption is partial.** The power-rail engine
  now accepts the framework Measurement (state-aware), but the
  Motherboard engine's `MotherboardDiagnosisInput.measurements` still
  uses the legacy Measurement in its public type signature (it
  normalizes internally). A future migration could make the public API
  framework-only.
- **Conflict detection is per-signal-name only.** A `vin` reading that
  contradicts an `input_voltage` reading (same physical net, different
  names) is NOT cross-detected.
- **No unit-conversion enforcement.** A voltage reading with `unit="A"`
  (wrong unit) is processed without complaint (the unit field is
  informational; the engine does not refuse mismatched units).

## Phase 2 — Power-supply engine

- **6 mains-isolated topologies share a stage SEQUENCE.** Flyback,
  forward, push-pull, half-bridge, full-bridge, and laptop_adapter have
  the same ordered stage IDs. They differ in stage descriptions,
  components, fail_notes, AND (after v1.5.0) the secondary-side walk
  localizes to topology-specific stages. But the stage *sequence* is
  shared; a truly distinct per-topology stage graph is a future
  enhancement.
- **`switching` treated as primary-side for mains.** The mains
  secondary-side walk blocks `switching` (the primary FET's gate drive).
  A secondary-side sync-rectifier switching observation would also be
  blocked. Conservative (safe) but may skip a legitimate secondary
  switching signal.
- **No load-step / transient testing.** The engine reasons on static
  measurements.

## Phase 3 — Component database

- **Pin-by-pin pinout data is partial.** Most IC records list
  `pin_functions` (the function names) but the `pinout` dict (pin-
  number → signal) is empty for many. The replacement engine correctly
  returns INSUFFICIENT_DATA (not COMPATIBLE) when pinout is unverified
  — this is safe, but it limits pin-compatibility confirmation.
- **DB is a seed (76 component records), not exhaustive.** Real boards use
  thousands of parts; the engine falls back to "consult the datasheet"
  for parts not in the DB.
- **Controller/charger IC firmware dependency always conditional.** The
  engine cannot verify EC firmware compatibility from the part number;
  controller-IC replacements are usually CONDITIONALLY_COMPATIBLE or
  INSUFFICIENT_DATA.
- **Thermal data is partial.** Some MOSFET/discrete records have
  Rtheta_ja but not Rtheta_jc; the evaluator marks thermal 'unknown'
  when Rtheta_ja is missing (forcing INSUFFICIENT_DATA). This is safe
  but conservative.

## Phase 4 — Motherboard power-sequence

- **Desktop stages not expanded.** The laptop stages were deepened to
  17; the desktop stages remain at the v1.4.1 count (the same
  dependency/alternative-hypothesis machinery applies, but fewer stages
  were added).
- **No POST-code dictionary.** The bios_spi/ram_init/post stages
  mention POST codes generically but the engine does not decode
  specific POST codes to subsystems (board-specific).
- **EC firmware symptoms are descriptive.** The ec_alive stage lists
  "EC firmware corrupted" as an alternative hypothesis, but the engine
  cannot detect firmware corruption from measurements — it surfaces the
  hypothesis for the technician to investigate.

## Phase 5 — Netlist recognition

- **No full electrical solver.** The engine recognizes structures from
  connectivity + pin functions, but does NOT simulate currents/voltages
  or verify a design's electrical correctness. It answers "what
  structure is this?" not "does this design work?" The
  uncertainty_report explicitly states "NOT simulated".
- **Recognition covers 13 structures.** Other structures (active-clamp
  forward, LLC primary, two-switch forward, active ORing, hot-swap)
  return 'unknown'.
- **No image recognition.** Per spec, only structured netlist input is
  supported. Schematics/BoardView are NOT ingested.
- **trace_all_paths caps at 20 paths.** Avoids exponential explosion on
  dense graphs; very dense netlists may not enumerate all paths.
- **External ports must be declared.** A net is only treated as an
  external port if listed in `external_ports`.

## Phase 6 — Knowledge

- **Retrieval is lexical, not semantic.** Tokenization + entity boost +
  synonym expansion is better than substring, but not true semantic
  search (no embeddings). Synonyms cover 20 common pairs; rare terms
  not in the map won't expand.
- **KI corpus is not exhaustively curated.** The pre-existing KI
  records + 22-link seed graph are a starting point; a large curated
  corpus is a future effort.
- **No ingestion of BIOS/firmware/schematics/BoardView.** Per spec,
  these are forbidden categories.
- **Dedup is title+content based.** Two items with different titles but
  identical technical content are NOT deduped (the dedup key is
  title+content_text[:200]).

## Phase 7 — Validation

- **Hardware-required validation is documented, not executed.** The
  validation suite lists what needs physical hardware but cannot run it.
- **Test coverage is not combinatorial.** Not every
  measurement/topology/component combination is tested — infeasible. The
  tests cover main paths + the edge cases the phases identified.

## Forbidden by spec (never implemented)

- Ingesting BIOS dumps, firmware images, schematics, or BoardView files.
- Image-based circuit recognition.
- Modifying the original host application.
- Inventing component specs, pinouts, or replacements.
- Weakening tests to make them pass.

## Backward compatibility (preserved)

- Legacy `Measurement(name, value, measured)` payloads still work
  (normalizer maps to framework states).
- `KnowledgeRetriever.search` returns `RetrievalResult`; `search_items()`
  preserves the old `list[KnowledgeItem]` interface.
- Netlist `external_ports` is optional (defaults to empty).
- Zener `vz_v` optional (evaluator falls back to `vrrm_v`).
- Old motherboard measurement sets (pre-v1.5 stage names) still work
  (skip logic).


## v1.5.1 closeout addendum (2026-11-26)

- Desktop power-on sequence has 14 required core stages; USB/audio/LAN are optional checks after display and do not gate POST.
- Explicit offline file ingestion now supports HTML/HTM, DOCX, CSV, TXT/MD/RST/LOG and selectable-text PDF with PyMuPDF or pypdf. HTML image references are preserved; image bytes remain in the source mirror. No OCR, directory crawl or network fetching.
- The shipped Lenovo record is only a seed example and cannot be promoted; independently verified repair-case count is 0.
- The integrated host bridge is read-only for host sessions; unknown measurement-state semantics are not fabricated.
- Software tests are not hardware validation; physical board testing remains outside this closeout.
