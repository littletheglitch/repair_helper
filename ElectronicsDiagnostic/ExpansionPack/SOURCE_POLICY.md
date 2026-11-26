# Source Policy

## What may be ingested

- **Manufacturer datasheets** (public, freely redistributable by the
  manufacturer). The pack records URLs and extracted factual technical
  information with source attribution. The pack does NOT reproduce full
  datasheet text.
- **Manufacturer application notes** (public). Same rules as datasheets.
- **Engineering standards** (ATX12V, USB, JEDEC, etc. — public). The
  pack records URLs and references.
- **Open educational material** (CC-licensed textbooks, lecture notes).
- **User-provided licensed documents** — the host application may
  ingest documents the user has a license for. The pack records the
  local source reference and provenance.

## What may NOT be ingested

- **BIOS dumps and firmware dumps** — never. Forbidden category in the
  knowledge platform; the ingestion pipeline rejects them outright.
- **Schematics and BoardView files** — case-specific, manually selected
  only. Never auto-ingested into the general knowledge base.
- **Full copyrighted books** — the pack records metadata (title,
  author, ISBN, URL) but does NOT download or redistribute content.
  The source catalog marks all commercial textbooks as
  `ingestion_status: not_ingested`.
- **Entire repair forums** — the pack records the forum URL as a
  community-unverified source but does NOT bulk-ingest forum content.
  Individual technically relevant discussions may be ingested as
  RepairCases with `source: forum` and `confidence: unresolved` until
  independently verified.

## Provenance requirements

Every component record, replacement mapping, knowledge item, and
repair case MUST carry:
- `source` or `datasheet_source` — who published it
- `source_date` or `source_publication_date` — when
- `datasheet_url` or `source_url` or `local_source_reference` — where
- `verification_status` — verified / partial / unknown / unverified

The validators (`Tools/validate_database.py`) enforce these. A record
missing source attribution fails validation.

## Confidence rules

- A component record with all electrical specs verified against the
  datasheet is `verified`.
- A record with the family and package verified but the full pinout
  not enumerated is `partial`.
- A record whose datasheet was not consulted is `unknown`.
- A replacement mapping with an un-consulted candidate datasheet is
  `UNKNOWN` — the validator emits a warning, never a pass.
- A repair case from a forum is `unresolved` until a second technician
  independently verifies it.

## Never fabricated

The pack never invents:
- Component specifications
- Datasheet URLs
- Pinouts
- Expected voltages
- Compatibility claims

When information cannot be verified, the pack says so explicitly via
`verification_status` and `requires_info`.
