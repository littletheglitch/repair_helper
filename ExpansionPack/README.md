# Electronics Diagnostic Expansion Pack

A standalone, portable expansion package for an offline laptop-motherboard
repair diagnostic system. This package adds two major capabilities to a host
repair application:

1. **Component Intelligence Database** — verified records of ICs and MOSFETs
   used in laptop power circuits, with evidence-backed replacement mappings.
2. **Power Rail Diagnostic Engine** — a deterministic, measurement-driven
   diagnostic workflow for the common laptop power topologies (buck, boost,
   LDO, multi-phase CPU VRM, GPU VRM, memory power, charger input, and the
   always-on / standby rails).

The package is **independent**: it does not import, modify, or depend on any
external project. It uses only the Python standard library and requires
**no internet access at runtime**. The deterministic engine does **not**
call an LLM.

---

## Design principles

1. **Never fabricate.** Every electrical spec, every URL, every compatibility
   claim is sourced. Records that cannot be fully verified are marked
   `verification_status="partial"` or `"unknown"` with explicit notes.
2. **Never assume.** The engine never assumes a controller from a rail name;
   the caller must supply the identified part number. Expected rail voltages
   are hints, not gates.
3. **Never jump to replacing the controller.** The workflow walks
   resistance → input → controller VCC → enable → switching → feedback →
   PGOOD before condemning any part.
4. **Be able to say "I don't know."** When a rule depends on a measurement
   that was not supplied, the engine returns `requires_info=True` rather
   than guessing.
5. **Replacement evidence must be complete.** A `COMPATIBLE` mapping requires
   evidence for every relevant criterion (package, pinout, voltage, current,
   RDS_on, gate drive, gate charge, thermal). Most controller IC swaps are
   `NOT_RECOMMENDED` because of firmware dependency.
6. **Verified vs unverified is always separated.** Community-derived
   knowledge lives in `Knowledge/` with clear "unverified" labelling; the
   component database itself only carries manufacturer-sourced facts.

---

## Folder structure

```
Electronics_Diagnostic_Expansion_Pack/
├── README.md                          ← this file
├── VERSION                            ← semantic version
├── manifest.json                      ← machine-readable pack manifest
├── ComponentDatabase/
│   ├── ICs/                           ← one JSON record per part, by category
│   │   ├── pwm_controllers/
│   │   ├── buck_controllers/
│   │   ├── charger_ics/
│   │   ├── battery_management/
│   │   ├── ldo_regulators/
│   │   ├── load_switches/
│   │   ├── pmic/
│   │   ├── bios_flash/
│   │   ├── ec_controllers/
│   │   ├── usb_pd/
│   │   └── voltage_regulators/
│   ├── MOSFETs/
│   │   ├── n_channel/
│   │   └── p_channel/
│   ├── ReplacementMappings/           ← one JSON record per (original, candidate)
│   └── schemas/                       ← JSON-schema (draft-07 subset) files
│       ├── ic_component.schema.json
│       ├── mosfet_component.schema.json
│       ├── replacement_mapping.schema.json
│       ├── rail_definition.schema.json
│       └── diagnostic_rule.schema.json
├── DiagnosticEngines/
│   ├── core/                          ← the deterministic Python engine
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   └── component_db.py
│   ├── PowerRails/                    ← one JSON definition per rail family
│   ├── BuckConverter/                 ← RULE_*.json for the buck topology
│   ├── BoostConverter/
│   ├── LDO/
│   ├── VRM/
│   └── Charger/
├── Knowledge/
│   ├── TechnicalReferences/           ← markdown explainers, sourced
│   ├── ApplicationNotes/              ← curated app-note references
│   └── DiagnosticWorkflows/           ← step-by-step workflow docs
├── Tests/
│   ├── run_all_tests.py               ← full test suite (stdlib unittest)
│   ├── test_components/               ← component-level test stubs
│   ├── test_power_rails/              ← rail-level test stubs
│   └── sample_cases/                  ← JSON sample cases the engine must solve
├── Tools/
│   ├── validate_database.py           ← validate the component DB
│   ├── validate_rules.py              ← validate rules + rail definitions
│   └── export_summary.py              ← emit a summary JSON + Markdown
├── Integration/
│   ├── INTEGRATION_GUIDE.md           ← how to wire this pack into a host app
│   ├── API_SPECIFICATION.md           ← public API contract
│   └── IMPORT_MANIFEST.json           ← machine-readable import manifest
└── download/                          ← generated reports (summary.json/.md)
```

---

## Component coverage

This build ships a small but verified set of records. The goal is correctness
and useful diagnostic depth, not the number of records.

| Group                  | Records |
| ---------------------- | ------- |
| PWM controllers        | 2       |
| Buck controllers       | 2       |
| Charger ICs            | 2       |
| Battery management ICs | 1       |
| LDO regulators         | 3       |
| Load switches          | 1       |
| PMICs                  | 1       |
| BIOS / SPI flash       | 2       |
| EC controllers         | 2       |
| USB-C / PD controllers | 2       |
| Voltage regulators     | 2       |
| **IC total**           | **20**  |
| N-channel MOSFETs      | 3       |
| P-channel MOSFETs      | 2       |
| **MOSFET total**       | **5**   |
| Replacement mappings   | 9       |

Run `python Tools/export_summary.py` to regenerate the authoritative count
list at any time.

---

## Diagnostic coverage

The engine ships **33 rules** across six topologies plus the always-on
standby family, and **14 rail-family definitions**:

`+3VALW`, `+5VALW`, `+1.8V`, `+VCCRTC`, `VCORE`, `VCCSA`, `GPU_CORE`,
`VRAM`, `LCD_VDD`, `USB_VBUS`, `DDR_VDD`, `DDR_VTT`, `ADAPTER_INPUT`,
`BOOST_GENERIC`.

The engine evaluates a small boolean expression language over measured
facts. Each rule maps a measurement state to the next diagnostic step with:
what to measure, where, with which instrument, the expected result, the
safety precaution, and the interpretation branches that assert new facts
and name the next rule.

---

## Running the validators and tests

```bash
# from inside Electronics_Diagnostic_Expansion_Pack/

python Tools/validate_database.py    # exit 0 = clean
python Tools/validate_rules.py       # exit 0 = clean
python Tests/run_all_tests.py        # exit 0 = all tests pass
python Tools/export_summary.py       # writes download/summary.json + .md
```

All four are pure standard library and run offline.

---

## Integration

See `Integration/INTEGRATION_GUIDE.md` for the wiring recipe and
`Integration/API_SPECIFICATION.md` for the public API contract. In short:

```python
import sys
sys.path.insert(0, "/path/to/Electronics_Diagnostic_Expansion_Pack")
from DiagnosticEngines.core import load_engine, Measurement

eng = load_engine()  # auto-locates the pack root
result = eng.diagnose(
    rail_family="+3VALW",
    measurements=[Measurement(name="rail_voltage", value=0.0, measured=True)],
    controller_part="RT8205",   # optional but recommended
)
print(result.fired_rule_id, result.next_steps[0].action)
```

---

## Known limitations

- **EC controllers and PD controllers carry NDA-gated datasheets.** Their
  pinouts and register maps are not enumerated here; only the part family
  and high-level role are recorded. The diagnostic engine will ask for the
  controller datasheet whenever pin-level work is required.
- **The rule database does not cover every motherboard on the market.** It
  covers the topologies and rail families listed above. Boards with custom
  PMICs, exotic sequencing, or non-IMVP CPU platforms may fall outside the
  rule set; the engine will say so rather than guess.
- **Replacement mappings favour NOT_RECOMMENDED.** Controller ICs, charger
  ICs, ECs and PD controllers are firmware-defined; cross-vendor swaps are
  almost never safe. The mappings reflect this honestly.
- **No BIOS dumps, firmware images, schematics or BoardView files are
  ingested.** They are explicitly excluded per the spec.
- **Community / forum-derived knowledge is intentionally absent from the
  component database.** It belongs in `Knowledge/` and is clearly labelled
  unverified when added.

---

## Versioning

- Pack version: see `VERSION`.
- Schema version: `1.0.0` (every record carries a `schema_version` field).
- Any future breaking schema change bumps the schema version and the pack
  version together.
