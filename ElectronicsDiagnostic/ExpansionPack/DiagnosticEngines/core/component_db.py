"""Read-only, indexed accessor for ExpansionPack component data.

The loader is tolerant of individual malformed JSON files: a bad record is
reported in ``load_errors`` without preventing the remaining catalog from
being used. Lookup and summary methods cover ICs, MOSFETs, analog ICs and
discrete semiconductors without silently double-counting records.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ComponentDatabase:
    pack_root: Path
    ic_records: list[dict] = field(default_factory=list)
    mosfet_records: list[dict] = field(default_factory=list)
    discrete_records: list[dict] = field(default_factory=list)
    analog_records: list[dict] = field(default_factory=list)
    replacement_mappings: list[dict] = field(default_factory=list)
    load_errors: list[str] = field(default_factory=list)
    _by_part_number: dict[str, list[dict]] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.pack_root = Path(self.pack_root)
        self._rebuild_index()

    @staticmethod
    def _read_tree(
        root: Path, errors: list[str], label: str, required_key: str = "part_number"
    ) -> list[dict]:
        records: list[dict] = []
        if not root.exists():
            return records
        for path in sorted(root.rglob("*.json")):
            try:
                with path.open("r", encoding="utf-8-sig") as handle:
                    record = json.load(handle)
                if not isinstance(record, dict):
                    raise ValueError("JSON root must be an object")
                identity = record.get(required_key)
                if not isinstance(identity, str) or not identity.strip():
                    raise ValueError(f"missing non-empty {required_key}")
                record.setdefault("_source_file", str(path))
                records.append(record)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                errors.append(f"{label}: {path}: {type(exc).__name__}: {exc}")
        return records

    @classmethod
    def load(cls, pack_root: Path) -> "ComponentDatabase":
        root = Path(pack_root)
        errors: list[str] = []
        db = cls(
            pack_root=root,
            ic_records=cls._read_tree(root / "ComponentDatabase" / "ICs", errors, "IC"),
            mosfet_records=cls._read_tree(root / "ComponentDatabase" / "MOSFETs", errors, "MOSFET"),
            discrete_records=cls._read_tree(
                root / "ComponentDatabase" / "DiscreteSemiconductors", errors, "discrete"
            ),
            analog_records=cls._read_tree(
                root / "ComponentDatabase" / "AnalogICs", errors, "analog IC"
            ),
            replacement_mappings=cls._read_tree(
                root / "ComponentDatabase" / "ReplacementMappings", errors,
                "replacement mapping", required_key="original_part"
            ),
            load_errors=errors,
        )
        db._rebuild_index()
        return db

    def all_records(self) -> list[dict]:
        """Return unique records across all component families.

        Duplicates are identified by part number + manufacturer + category.
        Same-number parts from different manufacturers/categories remain
        distinct, because they may not be interchangeable.
        """
        ordered = (
            self.ic_records + self.mosfet_records
            + self.discrete_records + self.analog_records
        )
        seen: set[tuple[str, str, str]] = set()
        result: list[dict] = []
        for record in ordered:
            key = (
                str(record.get("part_number", "")).strip().casefold(),
                str(record.get("manufacturer", "")).strip().casefold(),
                str(record.get("category", "")).strip().casefold(),
            )
            if not key[0] or key in seen:
                continue
            seen.add(key)
            result.append(record)
        return result

    def _rebuild_index(self) -> None:
        index: dict[str, list[dict]] = {}
        for record in self.all_records():
            key = str(record.get("part_number", "")).strip().casefold()
            index.setdefault(key, []).append(record)
        self._by_part_number = index

    def find_part(self, part_number: str) -> Optional[dict]:
        """Find the first exact part-number match, case-insensitively."""
        key = str(part_number or "").strip().casefold()
        matches = self._by_part_number.get(key, [])
        return matches[0] if matches else None

    def find_all_parts(self, part_number: str) -> list[dict]:
        """Return all distinct manufacturer/category records for a part number."""
        key = str(part_number or "").strip().casefold()
        return list(self._by_part_number.get(key, []))

    def replacements_for(self, part_number: str) -> list[dict]:
        key = str(part_number or "").strip().casefold()
        return [
            mapping for mapping in self.replacement_mappings
            if str(mapping.get("original_part", "")).strip().casefold() == key
        ]

    def by_category(self, category: str) -> list[dict]:
        key = str(category or "").strip().casefold()
        return [
            record for record in self.all_records()
            if str(record.get("category", "")).strip().casefold() == key
        ]

    def by_channel(self, channel: str) -> list[dict]:
        """Return MOSFET records for 'N-channel' or 'P-channel'."""
        key = str(channel or "").strip().casefold()
        return [
            record for record in self.mosfet_records
            if str(record.get("channel_type", "")).strip().casefold() == key
        ]

    def summary(self) -> dict:
        by_cat: dict[str, int] = {}
        for record in self.all_records():
            category = str(record.get("category") or "uncategorized")
            by_cat[category] = by_cat.get(category, 0) + 1

        duplicate_groups = sum(
            1 for values in self._by_part_number.values() if len(values) > 1
        )
        return {
            "ic_count": len(self.ic_records),
            "mosfet_count": len(self.mosfet_records),
            "discrete_count": len(self.discrete_records),
            "analog_ic_count": len(self.analog_records),
            "unique_component_count": len(self.all_records()),
            "duplicate_part_number_groups": duplicate_groups,
            "replacement_mapping_count": len(self.replacement_mappings),
            "load_error_count": len(self.load_errors),
            "load_errors": self.load_errors[:20],
            "ic_by_category": by_cat,
            "mosfet_by_channel": {
                "N-channel": len(self.by_channel("N-channel")),
                "P-channel": len(self.by_channel("P-channel")),
            },
        }
