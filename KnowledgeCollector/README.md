# Knowledge Collector

A standalone data-acquisition and processing application for the
[Electronics Diagnostic Assistant](../ElectronicsDiagnostic). It
selectively collects technically relevant information from permitted
public electronics-repair forums, technical references, and manufacturer
documentation, then prepares it for offline retrieval by the assistant.

**This is a separate application. It does not replace or interfere with
the diagnostic assistant.** The assistant's source code and database
schema are unchanged; the Collector produces JSON files the assistant
already knows how to ingest.

## What it does

- Collects repair-relevant forum threads (Stack Exchange API, EEVblog,
  Badcaps, configurable laptop-repair forums) with full provenance
  (URL, author, date, source license).
- Filters out off-topic content, spam, and **BIOS dump files** (hard
  exclusion at fetch, processing, and import stages).
- Extracts structured fields (component model, device model,
  motherboard model, symptoms, measurements, repair actions, outcome)
  without fabricating missing values.
- Classifies each record into A–F:
  - **A** technician-confirmed repair outcome (success + confirming test)
  - **B** reported repair without sufficient verification
  - **C** engineering explanation
  - **D** diagnostic hypothesis
  - **E** suggested test
  - **F** incomplete or uncertain
- Deduplicates (exact hash, normalized hash, near-duplicate Jaccard,
  cross-source) — never deletes unique measurements.
- Processes documents (PDF text, OCR for scanned, HTML, metadata, SHA-256)
  with corrupt-file handling.
- Exports to JSONL / Markdown / SQLite / text / **Knowledge JSON**
  (assistant-compatible).
- Imports the Knowledge JSON into the assistant's `Knowledge/` directory
  with zero code change to the assistant.

## Quick start

```
pip install -e .
python -m kc sources              # list configured sources (all disabled)
python -m kc source add eevblog forum_html https://www.eevblog.com/forum/
python -m kc source enable eevblog  # after reviewing the forum's terms
python -m kc collect --source eevblog
python -m kc status
python -m kc search "5VALW shorted"
python -m kc export knowledge
python -m kc export jsonl --out ./exports/jsonl
```

Then import into the assistant:

```
python -m kc --import ./exports/knowledge
# or via the importer module:
python -c "from kc.importer import import_export, render_import_report; \
  print(render_import_report(import_export('data/exports/knowledge', force=True)))"
```

The assistant ingests the imported files on next startup via
`knowledge_base.load_builtin()`.

## Safety and legal

- **BIOS dumps are excluded.** The BIOS guard checks filename, magic
  bytes, and extension at three stages. No override.
- **No auth/paywall/CAPTCHA/JS-render bypass.** The fetcher respects
  robots.txt, rate-limits, and reports login walls / JS-required pages
  instead of scraping them.
- **Source manifest.** Every source has a recorded license, terms URL,
  access method, permitted storage, and attribution. See
  [docs/INTEGRATION_COMPATIBILITY.md](docs/INTEGRATION_COMPATIBILITY.md)
  for the assistant-side contract.
- **No personal drive scanning.** The Collector only fetches from
  configured source URLs.
- **Offline after collection.** Search, export, and import use no network.

## Documentation

- [docs/INTEGRATION_COMPATIBILITY.md](docs/INTEGRATION_COMPATIBILITY.md) —
  the contract with the assistant (no code change required).
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module map, data flow,
  DB schema, connector contract.
- [docs/CONNECTORS.md](docs/CONNECTORS.md) — per-connector notes.
- [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md) — test counts and the
  end-to-end round-trip result.
- [docs/LIMITATIONS.md](docs/LIMITATIONS.md) — honest limitations.

## Testing

```
python -m pytest tests/
```

167 tests pass (all fixture-based; no real network during tests).

## Performance

Targets the assistant's hardware: Intel i7 6th-gen, 8 GB RAM,
CPU-only. The Collector defaults to 1 worker, 6 req/min, 500 MB per
source. Configurable via `data/settings.json`.
