# Knowledge Collector — Test Results

## Automated test suite

```
platform linux -- Python 3.12.16
pytest-9.0.2
collected 167 items
167 passed in 0.77s
```

All 167 tests pass. Breakdown:

| File                                | Tests | Coverage |
|-------------------------------------|-------|----------|
| test_bios_guard.py                  | 15    | BIOS extension/magic-byte/filename rejection + assert_not_bios |
| test_database.py                    | 16    | Schema, migrations, FTS5, store_record, dedup lookup, manifest, rejections |
| test_fetcher.py                     | 11    | robots.txt, rate-limit, retry/backoff, login-wall, JS-required |
| test_connectors_stackexchange.py    | 9     | API discover/parse, provenance, resume, max_records |
| test_connectors_eevblog.py          | 6     | SMF HTML board/thread parsing, author/date preservation |
| test_connectors_badcaps.py          | 5     | vBulletin HTML board/thread parsing, recipe-thread filter |
| test_filtering.py                   | 9     | Relevance whitelist, blacklist, BIOS-dump reference, length |
| test_extraction.py                  | 12    | Component/device/motherboard model, symptoms, measurements, repair, outcome |
| test_classifier.py                  | 12    | A–F classification, A→B downgrade, authority order, no-fabricate-probability |
| test_dedup.py                       | 12    | Shingles, Jaccard, exact/normalized/near/cross-source, variant preservation |
| test_documents.py                   | 14    | PDF/HTML/text/image/corrupt, BIOS rejection, SHA-256, scanned PDF detection |
| test_export.py                      | 9     | JSONL/Markdown/SQLite/text/knowledge + manifest + tier mapping |
| test_cli.py                         | 17    | All 14 CLI commands + offline operation |
| test_importer.py                    | 9     | Validate, dry-run, copy, force-clear knowledge_sources, source-ref preservation |
| test_recovery.py                    | 9     | Interrupted collection, pause, rate-limit, offline search, BIOS URL/magic rejection, cross-source dedup |
| test_end_to_end.py                  | 2     | **Full Collector → real assistant ingestion with no code change** |

## End-to-end round-trip (the spec's §15.9 requirement)

`test_end_to_end_collector_to_assistant` does the full pipeline:

1. Seeds a Collector record (an EEVblog-style repair post).
2. Exports it to Knowledge JSON (`export knowledge`).
3. Validates the export matches the assistant's schema.
4. Imports it into a sandboxed copy of the **real** Electronics
   Diagnostic Assistant (copied to a temp dir, not the live install).
5. Calls the real assistant's `knowledge_base.load_builtin(force=True)`.
6. Asserts the assistant ingested the fact (`inserted > 0`).
7. Asserts the assistant's `search_facts("5VALW shorted C45")` finds it.
8. Asserts the fact's `tier == "technician-confirmed"` (class A mapping).
9. Asserts the source URL survived the round-trip.

Result: **PASS** — the assistant ingests the Collector's output with
zero code changes.

## BIOS exclusion (hard requirement)

- `test_bios_dump_url_rejected_before_download`: a `.bin` URL is
  rejected at the filename check, logged in `rejections`, never stored.
- `test_bios_dump_rejected_via_magic_bytes`: a downloaded file with
  `\xAA\x55` magic bytes is rejected after download, logged, never stored.
- `test_process_bios_rejected` (in test_documents.py): the document
  processor rejects BIOS files; the original is NOT copied to
  `data/originals/`.

## Interrupted-collection recovery

- `test_interrupted_collection_can_resume`: a simulated network drop
  mid-collection leaves `crawl_state.status='failed'`; a resume with a
  fresh fetcher completes and collects records.
- `test_pause_during_collection`: `pause_after=N` stops the collection
  and sets `crawl_state.status='paused'`.

## Offline search

- `test_offline_search_works_after_collection`: after collection, the
  FTS5 search finds records with no network access. Source provenance
  is preserved on every hit.

## Honest notes

- The test suite uses **saved fixtures** (`tests/fixtures/`); it never
  hits real websites. This is the spec's requirement.
- The end-to-end test copies the real assistant to a temp dir so the
  live install is never modified.
- Source references (URL, author, date) survive every processing stage
  (verified in `test_export`, `test_importer`, `test_end_to_end`).
