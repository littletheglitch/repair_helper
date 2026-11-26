# Phase 2 Changelog

## [2.0.0] — 2026-11-23

### Architecture
- **Common schemas** (`eda/schemas.py`): `Quantity` with SI base + engineering
  prefixes + dimensional analysis (raises `DimensionalError` on mismatch);
  `ComponentRef`, `PinRef`, `NetRef` typed identifiers; `EngineResult` /
  `EngineError` envelope with formulas, assumptions, limitations, provenance.
- **Tool Router** (`eda/engines/base.py`): engine registry with versioning,
  lazy import, input-schema validation, structured-error handling. Engines
  never raise; errors are returned as `EngineResult.error`.
- **Validation framework** (`eda/validation.py`): machine-readable test
  reports (total/passed/failed/max_error/tolerance/environment/version).
  Reference answers are computed independently, never by the engine.

### Engines (P1–P7)
- **electrical_math** (P1): Ohm's law (V/I/R), power (VI/I²R/V²/R), series/
  parallel resistors, voltage/current dividers, KVL/KCL residuals. All
  inputs/outputs are SI-checked `Quantity` objects.
- **measurement_validation** (P1): range comparison, contradiction detection
  across matching operating conditions, missing-context surfacing. Never
  compares measurements from different operating conditions as equivalent.
- **Datasheet intelligence** (P2): structured parameter extraction from
  imported datasheets, with page/section/original-text/confidence/verification
  provenance. Never invents missing values; missing params are `not_found`.
- **6-tier knowledge provenance** (P2): manufacturer-spec / verified-reference
  / technician-confirmed / community / unverified / general-principle.
  `search_facts(query, tier=...)` filters by tier.
- **power_sequence** (P3): configurable per-board dependency graph, G3/S5/
  S4/S3/S0, missing-prerequisite / sequence-violation / blocking-node
  detection, apparent-state inference, next-measurement suggestion.
  Default generic Intel-platform model; boards define their own.
- **hypotheses** (P3): structured hypothesis manager with the 6 explicit
  statuses. Confirmation requires >=3 supporting, zero contradicting, zero
  required-evidence (stronger than the Phase 1 rule engine). Qualitative
  confidence basis only — no fabricated probabilities.
- **topology** (P4): circuit graph (components/pins/nets). add_node /
  add_connection / parse_netlist / components_on_net / rail_origin /
  find_opens / find_shorts. Honest about unsupported formats: requests a
  screenshot or ASCII export; never claims to parse PDF/binary BoardView.
- **next_best_test** (P5): information-gain selection across competing
  hypotheses. Returns test, where, reference, board state, instrument,
  expected (cited to a source), outcome branches, safety precautions,
  distinguishes, information_gain.
- **spice** (P7): optional ngspice integration via PySpice (lazy import).
  DC/transient/AC/sweep methods. Pure-Python linear DC fallback (MNA) when
  ngspice is absent. Simulated results are marked `simulated=True` and
  never presented as measurements.

### Benchmark + learning (P6)
- **Blind benchmark** (`eda/benchmark.py`): hides the known fault, runs the
  hypothesis engine on the case's symptoms/measurements, records
  predictions, compares with the actual fault. Dev cases separate from
  held-out; held-out never used for tuning. Metrics: top-1/top-3 accuracy,
  next-test usefulness, unsupported claims, appropriate-uncertainty rate.
- **Controlled learning** (`eda/learning.py`): technician-confirmed cases
  enter the verified KB (tier `technician-confirmed`); unverified cases are
  stored but never promoted. Versioned, traceable, reversible (revert to
  an earlier version by appending a new version row). The LLM may never
  convert its own answers into verified knowledge.

### Migrations, integrity, recovery
- **Migration layer** (`eda/migrations.py`): `PRAGMA user_version` tracking,
  idempotent migrations, `integrity_check`, `recover`, `backup`, `restore`.
  `init_db` runs `migrate()` automatically. CLI: `db integrity|backup|
  recover|version`.

### CLI
- 9 new commands added (additive; existing 16 unchanged): `engines`,
  `engine`, `validate`, `hypotheses`, `next-best-test`, `sequence`,
  `topology`, `benchmark`, `learn`, `db`.

### Tests
- 212 new tests added (P0: 46, P1: 43, P2: 26, P3: 22, P4: 15, P5: 13,
  P6: 19, P7: 12, CLI engines: 21). Total: 286, all passing on Linux
  Python 3.12, SQLite 3.53 with FTS5.

### Compatibility
- All 74 Phase 1 tests remain green.
- Existing CLI commands (`new`, `open`, `measure`, `analyze`, `next-test`,
  `add-file`, `report`, etc.) preserve their output shape.
- The Phase 1 rule engine is preserved and used as one evidence source by
  the new hypothesis manager.
- DB schema is forward-compatible: new tables added by migration; existing
  tables gain a `tier` column (default `verified-reference` for Phase 1
  facts).

### Honest limitations
- The Windows EXE was not built in this Linux sandbox (PyInstaller
  unavailable). The Python entry point tested here is the EXE's entry point.
- ngspice/PySpice are not installed in this sandbox; the SPICE engine is
  tested for graceful UNAVAILABLE reporting and the pure-Python DC fallback
  is verified against an independent divider calculation.
- OCR, binary BoardView parsing, BIOS semantic analysis, and PDF schematic
  understanding are NOT implemented; the engines say so plainly.
- Benchmark metrics are reported honestly: 100% accuracy is only claimed
  when every case's top-1 actually matched.
