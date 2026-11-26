# Knowledge Collector — Limitations

## Implemented and tested
- 4 connectors (Stack Exchange API, EEVblog, Badcaps, generic laptop
  forum) with fixture-based tests.
- Relevance filtering (whitelist + blacklist + BIOS-dump reference
  rejection).
- Structured extraction (component/device/motherboard model, symptoms,
  measurements, repair actions, outcome) — no fabrication.
- A–F classification with per-record explanation; class A requires a
  confirming test (success phrase alone is downgraded to B).
- Deduplication (exact, normalized, near-duplicate Jaccard, cross-source)
  that preserves unique measurements as variants.
- Document processing (PDF text via PyMuPDF+pypdf, HTML, text, OCR via
  Tesseract when enabled, corrupt-file handling, SHA-256, scanned-PDF
  detection).
- 5 export formats (JSONL, Markdown, SQLite snapshot, per-record text,
  Knowledge JSON for the assistant).
- Source manifest (license, terms, access method, attribution).
- Offline importer that copies Knowledge JSON into the assistant's
  `Knowledge/` dir with zero code change.
- DB migrations, integrity check, FTS5 search.
- 167 tests, all passing.

## Honest limitations

### Connectors
- **Stack Exchange**: the connector uses the public API (sanctioned).
  The API has its own rate limits and may return fewer items than
  requested during high-traffic periods. The connector does NOT bypass
  these.
- **EEVblog / Badcaps**: HTML parsing is regex-based against the
  forum software's current markup (SMF for EEVblog, vBulletin for
  Badcaps). If a forum upgrades its markup, the connector's regexes
  may need updating. The connector logs and skips pages it can't parse
  rather than scraping unreliably.
- **Laptop forum (generic)**: reuses SMF-style parsing. Forums with
  different markup need a dedicated connector.
- **No JS rendering**: the fetcher detects JS-required pages and
  returns `js_required`; the connector skips them. There is no
  headless-browser fallback (the spec's i7-6th/8GB target makes a
  headless browser impractical).

### Document processing
- **OCR is opt-in**: Tesseract must be installed separately. Without it,
  scanned PDFs are `stored_only` with `meta.ocr_recommended=True`.
- **No schematic understanding**: OCR extracts labels from a scanned
  schematic, but the Collector NEVER claims the schematic is electrically
  understood. The diagnostic assistant's datasheet engine is the one
  that reasons about parameter rows, and it explicitly does NOT parse
  schematic symbols.
- **Corrupt PDFs**: marked `failed` with the error; the original is
  preserved for manual review. The engine never crashes.

### Classification
- **Rule-based, not ML**: the classifier is transparent
  keyword/phrase matching. It may misclassify a post that uses
  unusual phrasing. The explanation always names the matched rule so
  a human can audit it.
- **No numerical probability**: the spec forbids fabricated
  probabilities. The classifier reports a qualitative confidence basis
  ("N supporting; M contradicting; K missing") and never a number.

### Deduplication
- **Near-duplicate threshold is 0.9 Jaccard** on 3-word shingles. A
  paraphrase with substantial rewording may not be detected. The
  variant text is preserved alongside the original (never deleted).
- **Cross-source dedup** keeps the first-seen source's URL and adds
  later URLs to `also_seen_at`.

### Export / import
- **Knowledge JSON filename stability**: the assistant's
  `knowledge_sources` registry dedups by filename stem. The Collector
  uses `kc_<source>_<date>` stems; to refresh, use `--force` (clears
  the assistant's registry rows for those stems).
- **SQLite snapshot** is a copy of the live DB with a WAL checkpoint.
  It is a point-in-time snapshot, not a live view.

### Performance
- **1 worker by default**: the spec's i7-6th/8GB target is conservative.
  Configurable via `data/settings.json` (`max_workers`).
- **6 req/min default rate**: one request every 10s per host.
  Configurable per source.
- **500 MB per source** default storage cap. Configurable.

### What the Collector deliberately does NOT do
- **No BIOS dump collection** (hard exclusion at 3 stages).
- **No personal drive scanning** (only configured source URLs).
- **No auth/paywall/CAPTCHA/JS bypass**.
- **No fabrication of missing values** (empty fields stay empty).
- **No promotion of uncertain info to verified** (class B is never
  stored as `technician-confirmed`).
- **No network during offline commands** (search, export, import,
  status, stats, verify all work offline).
- **No modification of the assistant's code or schema** (the import
  is file-based, via the assistant's existing `Knowledge/*.json` path).
