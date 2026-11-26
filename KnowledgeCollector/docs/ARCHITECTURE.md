# Knowledge Collector — Architecture

## 1. Module map

```
src/kc/
  config.py            paths, settings, source config (no network at runtime)
  database.py          SQLite schema + FTS5 + helpers + migrations
  schemas.py           dataclasses: SourceConfig, Record, Document, Classification
  fetcher.py           robots.txt, rate limit, retry/backoff, resume, logging
  bios_guard.py        filename + magic-byte + extension checks; BIOS dumps rejected
  filtering.py         relevance filter (whitelist topics, blacklist spam)
  extraction.py        structured field extraction (component, model, symptom, ...)
  classifier.py        A–F classification with per-record explanation
  dedup.py             exact + normalized + near-duplicate + cross-source
  documents.py         PDF text, OCR, HTML, metadata, hashing, corrupt handling
  export.py            JSONL / Markdown / SQLite / text + Knowledge JSON for assistant
  manifest.py          source manifest (license, access, date, attribution)
  collector.py         orchestrator: drives connectors, dedup, classify, store
  cli.py               the CLI (sources/add/disable/collect/resume/pause/...)
  importer.py          offline import of Collector output into the assistant
  connectors/
    base.py            BaseConnector: source id, validate, paginate, resume
    stackexchange.py   Electronics Stack Exchange via the public API
    eevblog.py         EEVblog Forum (HTML, robots-respecting, JS-aware)
    badcaps.py         Badcaps Forum (HTML)
    laptopforum.py     generic laptop-repair forum (configurable)
```

## 2. Data flow

```
                 ┌─────────────────────────────────────────────────┐
                 │  CLI: collect / resume / pause                   │
                 └────────────────────┬────────────────────────────┘
                                      ▼
   ┌─────────────────────┐    ┌──────────────────────┐
   │  Source config      │───▶│  Collector           │
   │  (DB: sources)      │    │  (orchestrator)      │
   └─────────────────────┘    └──────────┬───────────┘
                                          │
                          ┌───────────────┴────────────────┐
                          ▼                                 ▼
                 ┌──────────────────┐              ┌─────────────────┐
                 │  Fetcher        │◀──────────── │  Connector      │
                 │  robots+rate+    │              │  (per source)   │
                 │  retry+resume    │              └────────┬────────┘
                 └────────┬─────────┘                       │
                          │ raw items                        ▼
                          ▼                          ┌──────────────┐
                 ┌──────────────────┐                 │ BIOS guard    │
                 │  Filtering       │                 │ (reject BIOS) │
                 │  (relevance)     │                 └──────────────┘
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │  Extraction      │  structured fields + original text
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │  Classifier      │  A–F + explanation
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │  Dedup           │  exact / normalized / near / cross-source
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │  DB + FTS5       │  records, documents, dedup_index, sources
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │  Export          │  JSONL / MD / SQLite / text / Knowledge JSON
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │  Manifest        │  source license, access, attribution
                 └──────────────────┘
```

## 3. Database schema (Collector's own SQLite)

The Collector uses its OWN database (separate from the assistant's).
Tables (created with migrations, versioned via `PRAGMA user_version`):

- `sources` — id, name, kind, base_url, enabled, license, terms_url,
  attribution, access_method, max_depth, rate_per_min, added_at.
- `records` — id, source_id, url, thread_id, post_id, title, author,
  published_at, collected_at, content_hash, normalized_hash, text,
  structured (JSON), classification, class_explanation, tier, status.
- `documents` — id, source_id, url, filename, file_type, sha256,
  size_bytes, status, meta (JSON), collected_at, original_path,
  extracted_text_path.
- `dedup_index` — content_hash, normalized_hash, record_id, source_id.
- `crawl_state` — source_id, last_url, last_page, last_run_at, status
  (running/paused/done/failed), error.
- `source_manifest` — source_id, license, terms_url, access_method,
  collected_at, permitted_storage, attribution, notes.
- `export_runs` — id, run_at, format, file_count, record_count,
  schema_version, output_dir.
- `rejections` — id, source_id, url, reason (relevance/spam/bios/dup/
  error), collected_at.

FTS5: `records_fts(text, title, classification UNINDEXED, source_id
UNINDEXED, content='records', content_rowid='id')` with insert/delete
triggers (no update — records are immutable once stored).

## 4. Connector contract

Every connector subclasses `BaseConnector`:

```python
class BaseConnector:
    name: str
    kind: str            # "stackexchange_api" | "forum_html" | ...
    base_url: str
    def discover(self, fetcher, config) -> Iterator[RawItem]: ...
    def parse(self, raw) -> ParsedItem: ...
    def supports_pagination(self) -> bool: ...
    def resume_key(self, raw) -> str: ...   # for resume-after-interrupt
```

A connector returns `RawItem` (URL, html/json, meta). The orchestrator
runs BIOS guard → filter → extraction → classifier → dedup → store.

Unsupported sources are **disabled by default**; enabling a source
requires the user to confirm they have reviewed its robots.txt and
terms. The connector never bypasses auth, paywalls, CAPTCHAs or JS
rendering; it logs "unsupported" and skips.

## 5. BIOS guard (hard exclusion)

Three-stage check at fetch, document-processing, and import:

- **Filename**: extension in `{.bin, .rom, .fd, .ima, .fw, .spi, .hex}`
  OR filename contains `bios`, `dump`, `rom`.
- **Magic bytes**: first 8 bytes match known BIOS signatures (AMI,
  Award, EFI capsule headers, or `5A A5`/`AA 55` flash patterns).
- **Content sniff**: very large file (>16 MB) + no PDF/text magic +
  extension ambiguous → flag for manual review.

Any match is rejected with a logged reason in `rejections`; the file
is never stored.

## 6. Classification rules (A–F, with explanation)

The classifier is rule-based and transparent. Each rule has a name and
a numeric weight; the highest-weight matching rule wins, and the
explanation names the rule and the evidence that triggered it.

| Rule | Class | Trigger |
|------|-------|---------|
| `confirmed_outcome` | A | text contains a success phrase ("fixed", "working", "back to life") AND a confirming test ("measured", "checked", "verified") |
| `reported_repair` | B | text mentions component replacement ("replaced", "swapped", "changed") but NO success phrase |
| `engineering_explanation` | C | text cites a datasheet/app-note/theorem or explains a mechanism ("because", "due to", "the regulator") |
| `diagnostic_hypothesis` | D | text proposes a cause ("could be", "might be", "suspect") without confirmation |
| `suggested_test` | E | text proposes a measurement ("try measuring", "check with multimeter") |
| `incomplete` | F | short text, no component, no measurement, or spammy |

The explanation is stored as `"rule=confirmed_outcome; matched='fixed','measured'"`.

A record can match multiple rules; the classifier picks the highest-
authority class (A > C > E > D > B > F) and lists all matched rules in
the explanation.

## 7. Deduplication

- **Exact**: SHA-256 of the original text. Same hash → same record;
  cross-source duplicates keep the first source's URL and add the
  later source's URL to a `also_seen_at` list.
- **Normalized**: lowercase + strip whitespace + remove punctuation;
  SHA-256 of that. Catches whitespace/punctuation variants.
- **Near-duplicate**: Jaccard similarity ≥ 0.9 on 3-shingle sets. Used
  for paraphrases; the record keeps both texts in a `variants` list
  (the dedup never deletes unique measurements).
- **Thread/post identity**: `(source_id, thread_id, post_id)` unique;
  re-crawling a thread updates in place, preserving the original
  `collected_at`.

## 8. Document processing

- **PDF text**: PyMuPDF (`fitz`) with pypdf fallback. Page markers
  preserved. Scanned-PDF detection (text/page < 20 chars) → mark
  `ocr_recommended`.
- **OCR**: Tesseract via `pytesseract` when available; lazy import. If
  absent, the document is `stored_only` with `meta.ocr_recommended`
  and a clear log message. The engine NEVER claims a scanned schematic
  is electrically understood just because OCR extracted labels.
- **HTML**: `html.parser` (stdlib) → text extraction; metadata from
  `<title>`, `<meta>`. Preserves source URL and collection date.
- **Metadata**: title, author (if present), published date (if
  present), page count (PDF), language (via a small heuristic).
- **Hashing**: SHA-256 of the file bytes; SHA-256 of the extracted
  text (for cross-source dedup of content).
- **Corrupt handling**: a corrupt PDF is `failed` with the error
  message; the original file is still stored for manual review. The
  Collector never crashes on a corrupt file.
- **Originals vs extracted**: original files go to
  `data/originals/<sha[:12]>_<name>`; extracted text to
  `data/extracted/<sha[:12]>.txt`; the DB row references both.

## 9. Export formats

- **JSONL**: one record per line, full structured fields + text +
  classification + provenance.
- **Markdown**: human-readable, one section per record, with the
  source URL and classification at the top.
- **SQLite**: a copy of the Collector's DB (read-only snapshot).
- **Extracted text**: per-record `.txt` files with a source-reference
  header.
- **Knowledge JSON (for the assistant)**: the format from the
  integration report — one file per source, matching the assistant's
  schema exactly, ready to drop into `<assistant>/Knowledge/`.

## 10. Performance and reliability

- **Bounded memory**: connectors stream items (generators); only the
  current item + the dedup index (bounded by `max_records_per_source`)
  are in RAM. Documents are processed one at a time.
- **Resume**: `crawl_state` records `(source_id, last_url, last_page)`
  after each item; `resume` continues from there. Pause writes
  `status='paused'`; resume flips it back.
- **Rate limiting**: per-source `rate_per_min`; the fetcher sleeps to
  honor it. Default 6 requests/min (10s gap).
- **Retry/backoff**: exponential (1s, 2s, 4s, 8s, max 60s) with up to 3
  retries on 5xx/429; 4xx (except 429) is not retried (respects the
  site's "no").
- **Offline after collection**: search and export never touch the
  network. The assistant consumes the exported JSON with no network.
- **Configurable concurrency**: `max_workers` (default 1, the spec's
  i7-6th/8GB target is conservative).
- **Storage limits**: `max_bytes_per_source` (default 500 MB); the
  Collector stops a source when the limit is hit and logs it.

## 11. CLI commands

```
sources                          List configured sources
source add <name> <kind> <url>   Add a source (disabled by default)
source disable <name>            Disable a source
source enable <name>             Enable a source (after terms review)
collect [--source NAME] [--max N]  Collect from enabled sources
resume [--source NAME]           Resume interrupted collection
pause [--source NAME]            Pause a running collection
status                           Show pages discovered/collected/rejected/etc.
search <query>                   Offline full-text search
documents [--source NAME]        List collected documents
export <format> [--out DIR]      Export: jsonl|markdown|sqlite|text|knowledge
verify                           Verify the DB integrity + manifest
stats                            Show collection statistics
help                             This message
exit                             Quit (REPL only)
```

## 12. Testing strategy

Every connector is tested against **saved fixtures** (fetched HTML/JSON
committed to `tests/fixtures/`); the test suite never hits real
websites. The fetcher is mocked via an injectable transport so the
connectors exercise their parsing logic deterministically.

Test files (planned):
- `test_config.py`, `test_database.py`, `test_migrations.py`
- `test_bios_guard.py`, `test_fetcher.py` (robots/rate/retry/resume)
- `test_filtering.py`, `test_extraction.py`, `test_classifier.py`
- `test_dedup.py`, `test_documents.py` (PDF/OCR/HTML/corrupt)
- `test_export.py` (all 5 formats), `test_manifest.py`
- `test_connectors_stackexchange.py` (fixture-based)
- `test_connectors_eevblog.py`, `test_connectors_badcaps.py`
- `test_connectors_laptopforum.py`
- `test_cli.py`, `test_importer.py` (round-trip into the assistant)
- `test_recovery.py` (interrupted collection), `test_offline_search.py`
