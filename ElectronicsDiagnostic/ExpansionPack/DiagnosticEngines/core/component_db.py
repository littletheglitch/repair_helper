"""Read-only accessor for the component database + replacement mappings.

Loads every component record (IC + MOSFET) and replacement mapping from
the pack and offers simple lookups used by the diagnostic engine, the
export tool and the tests.

Pure standard library, no network access.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ComponentDatabase:
    pack_root: Path
    ic_records: list[dict]
    mosfet_records: list[dict]
    discrete_records: list[dict] = None
    analog_records: list[dict] = None
    replacement_mappings: list[dict] = None

    def __post_init__(self):
        if self.discrete_records is None:
            self.discrete_records = []
        if self.analog_records is None:
            self.analog_records = []
        if self.replacement_mappings is None:
            self.replacement_mappings = []

    # --- construction ----------------------------------------------------

    @classmethod
    def load(cls, pack_root: Path) -> "ComponentDatabase":
        ic_records: list[dict] = []
        mosfet_records: list[dict] = []
        discrete_records: list[dict] = []
        analog_records: list[dict] = []
        mappings: list[dict] = []

        ic_root = pack_root / "ComponentDatabase" / "ICs"
        if ic_root.exists():
            for f in sorted(ic_root.rglob("*.json")):
                with open(f, "r", encoding="utf-8") as fh:
                    ic_records.append(json.load(fh))

        mosfet_root = pack_root / "ComponentDatabase" / "MOSFETs"
        if mosfet_root.exists():
            for f in sorted(mosfet_root.rglob("*.json")):
                with open(f, "r", encoding="utf-8") as fh:
                    mosfet_records.append(json.load(fh))

        # Discrete semiconductors (diodes, BJTs, Zeners, TVS, ESD)
        discrete_root = pack_root / "ComponentDatabase" / "DiscreteSemiconductors"
        if discrete_root.exists():
            for f in sorted(discrete_root.rglob("*.json")):
                with open(f, "r", encoding="utf-8") as fh:
                    discrete_records.append(json.load(fh))

        # Analog ICs (op-amps, comparators, supervisors, etc.)
        analog_root = pack_root / "ComponentDatabase" / "AnalogICs"
        if analog_root.exists():
            for f in sorted(analog_root.rglob("*.json")):
                with open(f, "r", encoding="utf-8") as fh:
                    analog_records.append(json.load(fh))

        map_root = pack_root / "ComponentDatabase" / "ReplacementMappings"
        if map_root.exists():
            for f in sorted(map_root.glob("*.json")):
                with open(f, "r", encoding="utf-8") as fh:
                    mappings.append(json.load(fh))

        return cls(
            pack_root=pack_root,
            ic_records=ic_records,
            mosfet_records=mosfet_records,
            discrete_records=discrete_records,
            analog_records=analog_records,
            replacement_mappings=mappings,
        )

    # --- lookups ---------------------------------------------------------

    def find_part(self, part_number: str) -> Optional[dict]:
        """Find any component record by exact part number."""
        for rec in (self.ic_records + self.mosfet_records
                    + self.discrete_records + self.analog_records):
            if rec.get("part_number") == part_number:
                return rec
        return None

    def replacements_for(self, part_number: str) -> list[dict]:
        """Return all replacement mappings whose original_part matches."""
        return [
            m for m in self.replacement_mappings
            if m.get("original_part") == part_number
        ]

    def by_category(self, category: str) -> list[dict]:
        return [r for r in self.ic_records if r.get("category") == category]

    def by_channel(self, channel: str) -> list[dict]:
        """channel: 'N-channel' or 'P-channel'."""
        return [r for r in self.mosfet_records if r.get("channel_type") == channel]

    # --- summary ---------------------------------------------------------

    def summary(self) -> dict:
        by_cat: dict[str, int] = {}
        for r in self.ic_records:
            by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
        return {
            "ic_count": len(self.ic_records),
            "mosfet_count": len(self.mosfet_records),
            "discrete_count": len(self.discrete_records),
            "analog_ic_count": len(self.analog_records),
            "replacement_mapping_count": len(self.replacement_mappings),
            "ic_by_category": by_cat,
            "mosfet_by_channel": {
                "N-channel": len(self.by_channel("N-channel")),
                "P-channel": len(self.by_channel("P-channel")),
            },
        }

