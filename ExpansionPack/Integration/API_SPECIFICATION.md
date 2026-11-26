# API Specification

The public API surface of the Electronics Diagnostic Expansion Pack is
intentionally tiny. Everything the host application needs is exposed
through two modules.

## `DiagnosticEngines.core` — top-level entry points

### `load_engine(pack_root: Optional[Path] = None) -> DiagnosticEngine`

Load the diagnostic engine from a pack root. If `pack_root` is `None`,
the engine locates the pack root relative to its own `__file__`.

Returns a `DiagnosticEngine` instance. Safe to call once at application
startup and reuse for every diagnosis.

### `Measurement` dataclass

A single measurement supplied to the engine.

| Field      | Type            | Meaning |
| ---------- | --------------- | ------- |
| `name`     | `str`           | Fact key, e.g. `"rail_voltage"`, `"vin"`, `"en"`. The engine derives `<name>_present` and `<name>_measured` facts from this. |
| `value`    | `Any`           | `True` / `False` for booleans, `float` for numerics, `None` for "not measured". |
| `measured` | `bool`          | `True` if the measurement was actually taken. `False` if the engineer is explicitly asserting "not measured yet" (rare). |
| `unit`     | `str`           | Optional unit string for display. |
| `notes`    | `str`           | Optional free-form note. |

### `DiagnosisResult` dataclass

The output of `DiagnosticEngine.diagnose()`.

| Field               | Type                          | Meaning |
| ------------------- | ----------------------------- | ------- |
| `rail_family`       | `str`                         | The rail family the diagnosis was run for. |
| `topology`          | `str`                         | The topology used for rule selection. |
| `controller_part`   | `Optional[str]`               | The controller part number the host supplied, if any. |
| `fired_rule_id`     | `Optional[str]`               | The rule that fired, or `None` if no rule fired. |
| `next_steps`        | `list[NextStep]`              | The next diagnostic step(s) from the fired rule. Empty if no rule fired. |
| `requires_info`     | `bool`                        | `True` if the workflow cannot proceed without more information. |
| `requires_info_note`| `str`                         | Human-readable explanation of what is missing. |
| `facts_evaluated`   | `dict[str, Optional[bool]]`   | The fact namespace the engine built from the measurements. |
| `candidate_rules`   | `list[str]`                   | The rule IDs that applied to this rail family (for transparency). |
| `notes`             | `list[str]`                   | Controller-linking notes (datasheet URL, pinout-verified flag, etc.). |

### `NextStep` dataclass

| Field                 | Type                | Meaning |
| --------------------- | ------------------- | ------- |
| `step`                | `int`               | Step number within the rule. |
| `action`              | `str`               | What the technician should do. |
| `measure`             | `Optional[dict]`    | What/where/instrument/expected/safety for the measurement. |
| `interpretations`     | `list[dict]`        | Branch list: `if_result` → `then`, optional `set_facts`, optional `next_rule`. |
| `requires_info`       | `bool`              | `True` if this step needs controller-specific data. |
| `requires_info_note`  | `str`               | What controller-specific data is needed. |

## `DiagnosticEngine` class

### `DiagnosticEngine(pack_root: Path)`

Construct directly if you want to override the auto-detected pack root.
Prefer `load_engine()` for normal use.

### `diagnose(rail_family, measurements, controller_part=None, topology=None) -> DiagnosisResult`

Run the diagnostic workflow.

- `rail_family` (str): one of the rail families defined in
  `DiagnosticEngines/PowerRails/` (e.g. `"+3VALW"`, `"VCORE"`).
- `measurements` (list[Measurement]): the measurements the technician
  has collected so far. The engine derives boolean facts from them.
- `controller_part` (Optional[str]): the part number marked on the
  controller IC. **Recommended.** The engine uses it to attach datasheet
  URL and pinout-verified status to the result. The engine never
  short-circuits the workflow based on the controller identity.
- `topology` (Optional[str]): override the rail's default topology. Rarely
  needed; useful when a board uses a non-standard topology for a rail
  family.

Returns a `DiagnosisResult`. Deterministic: same inputs always produce
the same output.

### `all_rules() -> list[dict]`

Return all loaded rule records. Useful for tests and for the host
application to render a rule browser.

### `all_rails() -> dict[str, dict]`

Return all loaded rail-family definitions.

## `DiagnosticEngines.core.component_db` — component database

### `ComponentDatabase.load(pack_root: Path) -> ComponentDatabase`

Load every IC, MOSFET and replacement-mapping record from the pack.

### `ComponentDatabase` instance attributes

| Attribute              | Type            | Meaning |
| ---------------------- | --------------- | ------- |
| `pack_root`            | `Path`          | The pack root. |
| `ic_records`           | `list[dict]`    | All IC records. |
| `mosfet_records`       | `list[dict]`    | All MOSFET records. |
| `replacement_mappings` | `list[dict]`    | All replacement mappings. |

### `ComponentDatabase` methods

| Method | Returns | Meaning |
| --- | --- | --- |
| `find_part(part_number: str)` | `Optional[dict]` | Find an IC or MOSFET record by exact part number. |
| `replacements_for(part_number: str)` | `list[dict]` | All replacement mappings whose `original_part` matches. |
| `by_category(category: str)` | `list[dict]` | All IC records in a category. |
| `by_channel(channel: str)` | `list[dict]` | All MOSFET records of a channel (`"N-channel"` or `"P-channel"`). |
| `summary()` | `dict` | Counts by category / channel for display. |

## Condition expression language (rule authors only)

Rule conditions use a tiny boolean expression language over the fact
namespace. Host applications do not need to write expressions; they only
supply measurements.

```
expr   := term (("AND" | "OR") term)*
term   := "NOT" term | factor
factor := "(" expr ")" | IDENT["=" ("True"|"False")]
```

- Fact names follow `[a-z_][a-z0-9_]*`.
- `AND`, `OR`, `NOT` are uppercase reserved words.
- A fact named `<x>_measured` defaults to `False` when absent (i.e. it
  was not measured). Any other absent fact defaults to `None` (unknown),
  which makes the whole expression evaluate to `None` and the engine
  surfaces `requires_info=True`.

## Stability promise

Within a schema version (currently `1.0.0`):

- The `load_engine`, `Measurement`, `DiagnosisResult`, `NextStep`,
  `DiagnosticEngine.diagnose`, `ComponentDatabase.load` and
  `ComponentDatabase.find_part` symbols are stable.
- New optional fields may be added to dataclasses; existing fields will
  not be removed or renamed.
- New rules and rail families may be added without breaking existing
  diagnoses (rule IDs are stable).
- A breaking change bumps the schema version and the pack version
  together, and is documented in `VERSION` and `manifest.json`.
