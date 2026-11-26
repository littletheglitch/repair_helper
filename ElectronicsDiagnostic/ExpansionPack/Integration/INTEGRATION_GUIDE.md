# Integration Guide

This document explains how to wire the Electronics Diagnostic Expansion
Pack into a host repair application.

## Design contract

The pack is **independent**:

- It uses only the Python 3 standard library. No third-party packages.
- It does **not** require network access at runtime.
- It does **not** call an LLM. The diagnostic engine is deterministic.
- It does **not** import or modify any file outside its own folder.
- It does **not** crawl the web, fetch URLs, execute page scripts, or scan directories.
- It supports explicit local-file ingestion for HTML/HTM, DOCX, CSV, TXT/MD/RST/LOG and selectable-text PDF (PDF requires PyMuPDF or pypdf).
- Firmware/BIOS binaries, schematics and BoardView files are excluded from this ingestion path.

The host application is expected to:

1. Add the pack root to `sys.path`.
2. Import `DiagnosticEngines.core.load_engine` and
   `DiagnosticEngines.core.component_db.ComponentDatabase`.
3. Call the engine with measurements it has collected from the board.
4. Display the returned `next_steps` to the technician.

## Folder placement

The pack is portable. Copy the entire `Electronics_Diagnostic_Expansion_Pack/`
folder next to your existing project:

```
your_workspace/
├── your_existing_project/
└── Electronics_Diagnostic_Expansion_Pack/   ← copy here
```

The pack does not care where it lives; it locates its own data files
relative to its `__file__`.

## Minimal integration (5 lines)

```python
import sys
sys.path.insert(0, "/path/to/Electronics_Diagnostic_Expansion_Pack")

from DiagnosticEngines.core import load_engine, Measurement

eng = load_engine()
result = eng.diagnose(
    rail_family="+3VALW",
    measurements=[Measurement(name="rail_voltage", value=0.0, measured=True)],
    controller_part="RT8205",   # optional but recommended
)

print(result.fired_rule_id)
for step in result.next_steps:
    print(f"Step {step.step}: {step.action}")
    if step.measure:
        print(f"  Measure: {step.measure['what']}")
        print(f"  Where:   {step.measure['where']}")
        print(f"  Tool:    {step.measure['instrument']}")
        print(f"  Expect:  {step.measure['expected']}")
        print(f"  Safety:  {step.measure['safety']}")
    for interp in step.interpretations:
        print(f"  If {interp['if_result']} -> {interp['then']}")
        if interp.get("next_rule"):
            print(f"    next rule: {interp['next_rule']}")
```

## What the host application is responsible for

The pack does **not**:

- Identify the controller on the board. The host must read the board
  marking and pass `controller_part` to `diagnose()`. If the controller
  is unknown, pass `None` — the engine will ask for it.
- Collect measurements. The host asks the technician (or a connected
  instrument) and passes them as `Measurement` objects.
- Persist diagnosis history. The engine is stateless; the host may keep
  whatever session log it wants.
- Render the UI. The engine returns plain dataclasses; the host renders
  them however it wants.

## What the pack guarantees

- Deterministic output: same inputs → same `fired_rule_id`, same
  `next_steps`, same `requires_info` flag.
- No silent guessing: when a rule depends on a measurement that was not
  supplied, `requires_info=True` and the note says what is missing.
- No fabricated pin numbers or expected voltages: those come from the
  controller datasheet, which the host (or the technician) supplies.
- No fabricated compatibility: every `COMPATIBLE` replacement mapping
  carries evidence for every required criterion.

## Component-to-diagnostic linking

When the host supplies `controller_part`, the engine:

- Looks up the part in `ComponentDatabase/ICs/**` and
  `ComponentDatabase/MOSFETs/**`.
- Attaches the manufacturer and datasheet URL to the diagnosis notes.
- Reports whether the controller's pinout is verified (`pinout` non-empty)
  or partial.
- Suggests the technician consult the datasheet to map VIN/VCC/EN/FB/PGOOD
  to specific pin numbers.

The host can also call the component database directly:

```python
from DiagnosticEngines.core.component_db import ComponentDatabase

db = ComponentDatabase.load(eng.pack_root)
rec = db.find_part("AON6500")
mappings = db.replacements_for("AON6500")
print([m["classification"] for m in mappings])
```

## Versioning and upgrades

- Pack version: `VERSION` file (semantic version).
- Schema version: every record carries `schema_version`; currently
  `1.0.0`. A breaking schema change bumps both.
- The pack is **append-only** for new records. To replace a record, edit
  the file in place; the record_id stays stable.
- To integrate a new pack version, copy the new folder over the old one.
  The host application's `sys.path.insert` continues to work unchanged.

## Validation as a build step

Run these as part of your host application's test suite:

```bash
python Tools/validate_database.py    # exit 0 = component DB clean
python Tools/validate_rules.py       # exit 0 = rules + rails clean
python Tests/run_all_tests.py        # exit 0 = full test suite passes
python Tools/export_summary.py       # regenerates download/summary.{json,md}
```

All four are pure standard library and run offline.


## v1.5.1 closeout additions

### Desktop power-on sequence

The desktop profile now has 14 ordered core stages from ATX +5VSB through display. Three additional post-display checks (USB, audio and LAN) are optional and do **not** gate POST or a healthy core power-on result. This distinction prevents a peripheral fault from being mislabeled as a no-POST fault.

### Explicit local document ingestion

The pack exposes `IngestionPipeline.ingest_file(path, ...)` for one user-selected local file. It extracts visible text and stores provenance, SHA-256, extraction status, and relative image references (`src`, `alt`, `title`) from HTML. Image files themselves remain in the HTTrack mirror; the knowledge record preserves their references rather than duplicating their bytes.

Supported: `.html`, `.htm`, `.docx`, `.csv`, `.txt`, `.md`, `.rst`, `.log`, and selectable-text `.pdf` (requires PyMuPDF or pypdf). Scanned PDF OCR is not performed. The importer does not crawl directories or access the network. The caller must pass each file explicitly. Duplicate file bytes are skipped by SHA-256.

### Host EDA Tool Router integration

The integrated EDA host project includes `eda.engines.expansion_pack`. It provides:

```text
engine expansion_pack capabilities
engine expansion_pack dispatch --engine_name power_on_sequence --platform desktop --payload_json '{"platform":"desktop","measurements":[]}'
engine expansion_pack ingest_file --path "C:\RepairArchive\forum-page.html" --category forum_post --source_url "https://example.invalid/thread/123"
```

The bridge maps existing host session measurements read-only. It does not write host session records. Because the host schema does not store all framework measurement-state semantics, the bridge records that state/condition may be inferred or unknown; it does not pretend the host database contains fields it does not have.
