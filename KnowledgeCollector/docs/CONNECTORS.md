# Connectors

Each connector is a subclass of `BaseConnector` (in
`src/kc/connectors/base.py`) with a `discover()` generator and a
`parse()` method. The orchestrator runs the BIOS guard, filter,
extractor, classifier, and dedup on each parsed item.

## stackexchange_electronics

- **Kind:** `stackexchange_api` (sanctioned public API; no scraping).
- **Endpoint:** `https://api.stackexchange.com/2.3/questions?...&site=electronics`
- **License:** CC-BY-SA 4.0 (Stack Exchange network content).
- **Tags queried:** motherboard, laptop, power-supply, voltage, mosfet,
  voltage-regulator, pwm, dc-dc-converter, battery-charging, repair,
  troubleshooting, multimeter.
- **Resume:** stores `last_page` in crawl_state; on resume, re-starts
  from the last page (a failure may have been mid-page).
- **Rate limit:** the API is rate-limited by Stack Exchange; the
  Collector adds its own per-source rate on top.
- **Provenance per record:** URL (question or `#answer-<id>`), author,
  creation date, tags, score, is_accepted.

## eevblog

- **Kind:** `forum_html` (SMF-based forum; robots-respecting HTML).
- **Board:** `https://www.eevblog.com/forum/repair/`.
- **License:** forum ToS; short extracts with link to original.
- **Parsing:** SMF thread links (`topic,NNN.0.html`), `<div class="post">`
  bodies, poster usernames, post dates.
- **No JS bypass:** if a page looks JS-rendered, the fetcher returns
  `js_required` and the connector logs and skips.
- **No auth bypass:** login walls are detected and the connector skips.

## badcaps

- **Kind:** `forum_html` (vBulletin-based forum).
- **Board:** `https://www.badcaps.net/forum/forumdisplay.php?f=10`.
- **License:** forum ToS; short extracts with link.
- **Parsing:** vBulletin thread links (`showthread.php/NNN-...` with
  `class="title"`), `<blockquote class="postcontent">` bodies.

## laptopforum (generic)

- **Kind:** `forum_html` (configurable; reuses SMF-style parsing).
- **Base URL:** set by the user via `source add laptopforum forum_html <url>`.
- **Use case:** any publicly accessible laptop-repair forum with SMF-like
  markup. The user reviews the forum's terms before enabling.

## Adding a new connector

1. Subclass `BaseConnector` in `src/kc/connectors/<name>.py`.
2. Set `name`, `kind`, `base_url`.
3. Implement `discover()` (yield `RawItem`s) and `parse()` (return a
   `ParsedItem` or None).
4. Register the connector in `src/kc/collector.py` `CONNECTORS`.
5. Add a default manifest in `src/kc/manifest.py` `DEFAULT_MANIFESTS`.
6. Add a fixture in `tests/fixtures/<name>/` and a test file
   `tests/test_connectors_<name>.py` using a `FakeFetcher` that serves
   the fixture. **Never hit the real site in tests.**

## What connectors must NOT do

- Bypass authentication, paywalls, CAPTCHAs or JS rendering.
- Collect entire forums indiscriminately — the filter discards off-topic items.
- Download BIOS dumps — the BIOS guard rejects them before storage.
- Fabricate missing values — `parse()` returns None if the body is empty.
- Hit the network during tests — use the fixture-based `FakeFetcher`.
