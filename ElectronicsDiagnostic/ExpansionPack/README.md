# Electronics Diagnostic Expansion Pack — v1.5.1

Offline, deterministic diagnostic engines and component intelligence for board-level electronics repair. This release is the closeout remediation of the supplied v1.5.0 source archive. The pack remains a support engine, not an autonomous repair authority: it consumes technician-supplied measurements and returns evidence-based next steps.

## Release inventory

| Data / capability | Current count or status |
|---|---:|
| IC records | 35 |
| MOSFET records | 5 |
| Discrete semiconductor records | 20 |
| Analog IC records | 16 |
| **Total component records** | **76** |
| Replacement mappings | 9 |
| Power-rail families | 25 |
| Diagnostic rule checks | 528 validation checks |
| Power-supply topologies | 15 |
| Circuit topologies | 36 |
| Laptop power-on sequence | 17 stages |
| Desktop power-on sequence | 14 required core stages + 3 optional peripheral checks |
| Independently verified repair cases | **0** |
| Excluded teaching seed examples | 1 (non-promotable; excluded from default search) |

The earlier `83 components` figure was incorrect. The actual inventory is 76 records; the manifest and maintained status documents now agree.

## Main diagnostic capabilities

- State-aware measurements distinguish NOT_MEASURED, MEASURED_ZERO, INVALID, OVERLOAD and valid numeric readings.
- Motherboard diagnosis includes power-rail reasoning and laptop/desktop power-on sequence walks with dependency-aware stage ordering.
- Desktop sequence checks ATX +5VSB, front-panel/PS_ON#, ATX main rails, PWR_OK, EPS12V, CPU VRM, memory rails, PCH/chipset, BIOS/SPI, clocks/reset, PCIe/GPU auxiliary power, POST and display. Optional USB/audio/LAN checks happen after the core sequence and cannot gate POST.
- Power-supply engine covers 15 topology models with dependency-aware stages and primary-side safety gates.
- Circuit analysis includes graph/netlist structure recognition and multi-path tracing; it is not an electrical simulator.
- Component identification, replacement evaluation, measurement intelligence, repair-case search, knowledge retrieval and LLM orchestration contract are available.
- Safety policy is enforced in code for hazardous diagnostic actions.

## Local document ingestion

`IngestionPipeline.ingest_file(path, ...)` extracts one explicitly selected local file and saves a provenance-bearing `KnowledgeItem` in `Knowledge/ExtractedKnowledge/`.

Supported formats:

- HTML / HTM: visible text, title/author metadata and relative image references (`src`, `alt`, `title`). Common navigation, scripts, forms, footer, sidebar and advertisement chrome are skipped.
- DOCX: paragraphs and table-cell text.
- CSV: header-labelled rows.
- TXT / MD / RST / LOG.
- PDF: selectable text only; PyMuPDF or pypdf must be installed.

SHA-256 duplicate suppression is enabled. Extraction records source path, URL (when supplied), method, MIME type, status and warnings. HTML image bytes are not copied into the knowledge store; references are retained while the actual image assets remain in the local HTTrack mirror. No website fetch, directory crawl, JavaScript execution, OCR or automatic image interpretation is performed. Scanned PDFs therefore remain unextracted. BIOS/firmware binaries, schematics and BoardView files are explicitly excluded.

## Master EDA host integration

The supplied master EDA project includes a Tool Router bridge named `expansion_pack`:

```text
engine expansion_pack capabilities
engine expansion_pack dispatch --engine_name power_on_sequence --platform desktop --payload_json '{"platform":"desktop","measurements":[]}'
engine expansion_pack ingest_file --path "D:\RepairArchive\thread.html" --category forum_post --source_url "https://example.invalid/thread/123"
```

The host-session bridge is read-only. It maps existing host session measurements into the pack framework without altering the host database. The host schema does not preserve all pack measurement states/conditions, so uncertain fields remain explicitly inferred or unknown.

## Tests and validation

From this folder:

```powershell
python Tests/run_validation_suite.py
```

Verified in the supplied Linux build environment:

- **360 / 360 pack tests passed**, 0 failures.
- **528 rule checks passed**.
- **41 component/database checks passed**, with 2 expected warnings for intentionally unknown/unverified mapping data.
- Legacy Measurement compatibility passed.
- Static offline check found no runtime network imports.

The master EDA project also passed **291 / 291 host tests**, including direct Tool Router bridge and local HTML ingestion tests.

## Limits that remain real

- There are no independently verified real RepairCase records yet. The one shipped Lenovo record used placeholder board identity `NM-xxxxx` and lacked auditable second-technician confirmation; it is retained only as a non-promotable seed example and excluded from default search. Do not describe it as a confirmed repair case.
- All pack tests are software/synthetic. No physical motherboard, PSU, DMM, oscilloscope or live board was tested. Hardware values, timing and real-world localization remain unverified.
- PDF OCR is not implemented; embedded figures/images in DOCX are not extracted. HTML only keeps references to locally mirrored images.
- The component database is a useful seed, not exhaustive coverage of every IC or board.
- Netlist recognition is not SPICE or full circuit simulation.
- The bridge is a compatibility layer, not a host database schema migration; state/condition fields not present in host records cannot be reconstructed with certainty.

See `CLOSEOUT_STATUS_v1.5.1.md`, `INSTALL_FA_v1.5.1.md`, `Integration/INTEGRATION_GUIDE.md`, `SAFETY_POLICY.md` and `TESTING_GUIDE.md` for details.
