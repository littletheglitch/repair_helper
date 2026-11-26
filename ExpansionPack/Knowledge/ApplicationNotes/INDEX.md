# Curated Application-Note References

This file lists manufacturer application notes that are useful background
for laptop motherboard power diagnostics. The pack does **not** reproduce
their contents; it records the public URL and a one-line summary so an
engineer can find the authoritative source.

> All URLs in this file are manufacturer-hosted. The pack verifies that
> each URL exists at build time but does not cache the document.

## Texas Instruments

| Title | URL | Why it matters |
| --- | --- | --- |
| SLVA861 — MOSFET selection for synchronous buck converters | https://www.ti.com/lit/an/slva861/slva861.pdf | Defines the RDS(on)/Qg/thermal tradeoff used in our replacement-classification criteria. |
| SLVA454 — Understanding output ripple in buck converters | https://www.ti.com/lit/an/slva454/slva454.pdf | Background for the ripple-measurement step in the buck workflow. |
| SLUA501 — Battery charger design using BQ247xx | https://www.ti.com/lit/an/slua501/slua501.pdf | Walks through the ACIN/ACOK/SRN/SRP signal flow used in the charger-input workflow. |

## Richtek

| Title | URL | Why it matters |
| --- | --- | --- |
| D-CAP2 mode explanation | https://www.richtek.com/battery-management/en/designing/rich002 | Explains why D-CAP2 controllers need no external compensation and how the FB pin is used. |
| RT8205 product page (standby controller) | https://www.richtek.com/assets/product_file/RT8205=RT8205A/DS8205A-12.pdf | Datasheet for one of the most common ALW controllers. |

## Vishay Siliconix

| Title | URL | Why it matters |
| --- | --- | --- |
| AN-608 — Power MOSFET basics | https://www.vishay.com/docs/71217/71217.pdf | Defines VGS(th), RDS(on), Qg, and the safe-operating-area concept. |
| AN-864 — Load switch design | https://www.vishay.com/docs/73391/si8644.pdf | Background for the load-switch rule set. |

## Alpha & Omega Semiconductor

| Title | URL | Why it matters |
| --- | --- | --- |
| AOS application notes index | https://www.aosmd.com/web/library/apps_notes | Source for trench-MOSFET tradeoffs and combo PowerBlock parts. |

## Macronix / Winbond (SPI flash)

| Title | URL | Why it matters |
| --- | --- | --- |
| MX25L12873F datasheet | https://www.macronix.com/Lists/Datasheet/Attachments/7404/MX25L12873F,%201.8V-3.3V,%20128Mb,%20v1.2.pdf | Reference for BIOS-flash command-set and pinout differences. |
| W25Q128JV datasheet | https://www.winbond.com/resource-files/w25q128jv%20revh%2004082019%20plus.pdf | Reference for cross-vendor BIOS-flash swap conditions. |

## onsemi

| Title | URL | Why it matters |
| --- | --- | --- |
| FUSB302B datasheet | https://www.onsemi.com/pdf/datasheet/fusb302b-d.pdf | Reference for the PD-PHY role vs full PD controller. |

## Community resources (unverified — use with caution)

These are NOT part of the component database. They are listed here for
engineer convenience and must be treated as community-derived, not as
authoritative engineering references.

- BadCaps Forum (https://www.badcaps.net/forum/) — community repair
  logs. Useful for confirming a fault pattern seen on a specific board,
  never a substitute for the datasheet.
- Vinafix (https://vinafix.com/) — community BIOS / EC firmware
  resources. Out of scope for this pack (we do not ingest firmware).

Any community-derived knowledge added to the pack in the future MUST live
under `Knowledge/` with a clear `unverified` label, never in
`ComponentDatabase/`.
