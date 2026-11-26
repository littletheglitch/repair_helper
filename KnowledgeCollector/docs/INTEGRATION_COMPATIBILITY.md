# Knowledge Collector — Integration Compatibility Report

This report is the first deliverable required by the spec (§15.1): an
inspection of the existing Electronics Diagnostic Assistant's database
schema and knowledge import interface, so the Collector's output can
be consumed with **zero changes** to the assistant.

## A. Assistant knowledge ingestion paths

The assistant ingests knowledge through two supported interfaces:

1. **Knowledge JSON path** (primary, preferred for the Collector)
   - Files dropped into `<assistant_root>/Knowledge/*.json`.
   - Discovered by `knowledge_base.load_builtin()` via `builtin_files()`
     which does `sorted(d.glob("*.json"))`.
   - Each file is ingested at most once, keyed by **filename stem**, into
     a `knowledge_sources` registry table. To refresh, use a new
     filename or clear that table / call with `force=True`.
   - `init_db()` creates the base schema and runs `migrations.migrate()`
     (which adds the `tier` column and the Phase 2 tables). The
     Collector's output therefore always sees the modern schema.

2. **Document path** (secondary, for binary/PDF/text files)
   - `documents.import_file(path, session_id=None, requested_by="")`.
   - SHA-256 dedup is automatic (same SHA = no-op).
   - File-type detection by extension; `.pdf`/`.txt`/image/BoardView/BIOS.
   - Extraction via PyMuPDF/pypdf for PDF, encoding sniffing for text.
   - Chunking into `document_chunks` (1000-char chunks, 100 overlap) with
     FTS5 `chunks_fts` auto-maintained by triggers.

## B. Knowledge JSON schema the Collector must emit

```jsonc
{
  "source": {
    "title": "EEVblog Forum — laptop motherboard repair",
    "url":   "https://www.eevblog.com/forum/...",
    "date":  "2026-11-23",                  // ISO date
    "retrieved_at": "2026-11-23 20:00:00"   // lands in retrieved_at column
  },
  "provenance": "repair-forum-summary",    // file-level default
  "tier": null,                            // optional override
  "sections": [
    {
      "category": "laptop-motherboard-repair",
      "topic":    "no-power-lenovo-g50-80",
      "facts": [
        {
          "text":        "Technician reported: ...",   // required
          "confidence":  "verified",                   // default
          "provenance":  "repair-forum-summary",        // default = file
          "tier":        null,                          // optional
          "source_url":  "https://...",                // default = source.url
          "source_title":"EEVblog thread #12345",      // default = source.title
          "source_date": "2025-03-12",                  // default = source.date
          "tags":        ["lenovo", "no-power", "5valw"]
        }
      ]
    }
  ]
}
```

## C. Tier mapping (the 6 provenance tiers)

The assistant's `_PROVENANCE_TO_TIER` map (Phase 2):

| Collector provenance         | Assistant tier            |
|------------------------------|---------------------------|
| `general-electrical-law`     | `general-principle`       |
| `manufacturer-app-note`      | `manufacturer-spec`       |
| `repair-forum-summary`       | `community`               |
| `verified-reference`         | `verified-reference`      |
| (set `tier` directly)        | `technician-confirmed`    |
| (set `tier` directly)        | `unverified`              |

The Collector's classification (A–F) maps to these tiers (see §D
below), and where the map cannot produce the right tier the Collector
sets the per-fact `tier` field explicitly.

## D. Collector classification → assistant tier mapping

| Collector class | Description                              | Assistant tier            |
|-----------------|------------------------------------------|---------------------------|
| A technician-confirmed repair outcome | explicit success reported + confirming test | `technician-confirmed` (set tier) |
| B reported repair (insufficient verification) | replacement claimed, no success confirmation | `community` (provenance=`repair-forum-summary`) |
| C engineering explanation | datasheet / app-note / textbook reasoning | `verified-reference` (provenance=`verified-reference`) or `manufacturer-spec` (provenance=`manufacturer-app-note`) |
| D diagnostic hypothesis | a proposed-but-unconfirmed cause | `unverified` (set tier) |
| E suggested test | a measurement proposed to confirm/refute | `community` |
| F incomplete/uncertain | low-value or partial | `unverified` (set tier) |

This mapping is the contract: the Collector never marks a class-B
record as `technician-confirmed` and never marks a class-D record as
`verified-reference`. The spec's rule "Do not mark uncertain
information as verified" is enforced by this table.

## E. What the Collector must NOT do (assistant-side)

- Do not write SQL directly into the assistant's DB.
- Do not insert into `knowledge_fts` / `chunks_fts` (triggers maintain
  them; direct writes corrupt the index).
- Do not insert into `knowledge_sources` (the assistant manages it).
- Do not depend on the `tier` column being absent — modern installs
  always have it after `init_db()` runs migrations.
- Do not change the assistant's schema or code. The Collector produces
  files; the assistant ingests them through its existing interface.

## F. Compatibility risk assessment

| Risk | Mitigation |
|------|------------|
| Filename collision overwrites an existing Knowledge file | Collector uses a stable, namespaced stem (`kc_<source>_<date>`) and never writes into the assistant's `Knowledge/` directly; the user copies the Collector's export there, or the importer does it with a versioned stem. |
| Re-import duplicates facts | The assistant's `knowledge_sources` registry dedups by stem. The Collector's `export` command uses a new stem per run; `force` is opt-in. |
| A class-A record being mis-stored as `community` | The Collector sets `tier=technician-confirmed` explicitly (the map cannot produce it). |
| BIOS dump files entering the pipeline | The Collector has a hard BIOS guard (filename + magic-byte + extension check) at the fetch, document-processing, and import stages. BIOS dumps are rejected with a logged reason; never stored. |
| Source reference loss | Every Collector record carries `source_url`, `source_title`, `source_date`, `retrieved_at`, and a content hash; the importer preserves all of them. |
| The assistant not finding the Collector's output | The Collector writes a `manifest.json` listing every export file and its schema version; the importer reads the manifest. |

## G. Verified interface facts (from the inspection)

- `import_fact(category, topic, fact, provenance, confidence="verified",
  source_url="", source_title="", source_date="", retrieved_at="",
  tags="", tier="")` — returns lastrowid. Does NOT upsert; idempotency
  is by filename stem.
- `knowledge_facts` columns after migration: `id, category, topic,
  fact, provenance, confidence, source_url, source_title, source_date,
  retrieved_at, tags, tier`.
- `knowledge_fts` triggers `knowledge_ai` (insert), `knowledge_ad`
  (delete) auto-maintain the index. No update trigger, but
  `import_fact` only inserts.
- The assistant's `Knowledge/*.json` is the ONLY ingest path that needs
  no assistant-side code change.
- `documents.import_file()` is the secondary path; SHA-256 dedup is
  automatic.
- `migrations.SCHEMA_VERSION = 1`; `init_db()` runs `migrate()` after
  base schema creation.

This report is the contract for the Collector's export module and the
offline importer. No assistant code changes are required.
