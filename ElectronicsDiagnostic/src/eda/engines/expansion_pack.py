"""Read-only bridge from the master EDA Tool Router into Expansion Pack.

The master application remains owner of its SQLite sessions, measurements,
CLI and reports. This bridge maps a host session into the pack's in-memory
framework types, invokes one pack capability, and returns a serializable
result. It never writes pack data or mutates the host database.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .base import Engine, MethodSpec

PACK_ROOT = Path(__file__).resolve().parents[3] / "ExpansionPack"


class ExpansionPackEngine(Engine):
    name = "expansion_pack"
    version = "1.5.1"
    summary = "Dispatches diagnostic requests to the offline Electronics Diagnostic Expansion Pack."
    provenance = "Electronics Diagnostic Expansion Pack v1.5.1; host session data mapped read-only."
    methods = {
        "capabilities": MethodSpec(
            name="capabilities",
            summary="List Expansion Pack capabilities.",
            input_schema={},
            output="list of capability descriptors",
        ),
        "dispatch": MethodSpec(
            name="dispatch",
            summary="Call one Expansion Pack engine. Optionally attach a read-only host session.",
            input_schema={
                "engine_name": ("string", True),
                "payload_json": ("string", False),
                "session_id": ("number", False),
                "platform": ("string", False),
            },
            output="structured Expansion Pack adapter response",
        ),
        "ingest_file": MethodSpec(
            name="ingest_file",
            summary="Extract one explicitly selected local HTML/DOCX/CSV/TXT/MD/PDF file into the pack knowledge store.",
            input_schema={
                "path": ("string", True),
                "category": ("string", False),
                "source_title": ("string", False),
                "source_author": ("string", False),
                "source_url": ("string", False),
                "technical_classification": ("string", False),
                "tags_json": ("string", False),
            },
            output="extraction result, provenance and deduplication status",
        ),
    }

    def __init__(self):
        self._adapter = None

    def _get_adapter(self):
        if not PACK_ROOT.is_dir():
            raise FileNotFoundError(f"Expansion Pack folder not found: {PACK_ROOT}")
        if self._adapter is None:
            import sys
            pack_path = str(PACK_ROOT)
            if pack_path not in sys.path:
                sys.path.insert(0, pack_path)
            from Integration.host_adapter import ExpansionPackAdapter
            self._adapter = ExpansionPackAdapter(PACK_ROOT)
        return self._adapter

    @staticmethod
    def _json_value(value: Any):
        if is_dataclass(value):
            return {k: ExpansionPackEngine._json_value(v) for k, v in asdict(value).items()}
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {str(k): ExpansionPackEngine._json_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [ExpansionPackEngine._json_value(v) for v in value]
        if isinstance(value, Path):
            return str(value)
        return value

    @staticmethod
    def _host_measurement_to_pack(row):
        from DiagnosticEngines.core.framework import (
            Measurement, MeasurementKind, MeasurementState, MeasurementCondition,
        )
        unit = (row.unit or "").strip()
        unit_low = unit.lower()
        if unit_low in {"v", "mv"}:
            kind = MeasurementKind.VOLTAGE
        elif unit_low in {"ohm", "ohms", "ω", "kohm", "kω"}:
            kind = MeasurementKind.RESISTANCE
        elif unit_low in {"a", "ma", "ua", "µa"}:
            kind = MeasurementKind.CURRENT
        elif unit_low in {"hz", "khz", "mhz"}:
            kind = MeasurementKind.FREQUENCY
        else:
            kind = MeasurementKind.CUSTOM

        if row.value is None:
            state, value = MeasurementState.NOT_MEASURED, None
        elif float(row.value) == 0.0:
            state, value = MeasurementState.MEASURED_ZERO, 0.0
        else:
            state, value = MeasurementState.IN_RANGE, float(row.value)

        context = " ".join((row.board_state or "", row.adapter_state or "", row.battery_state or "")).lower()
        if any(token in context for token in ("unpowered", "power off", "off", "disconnected")):
            condition = MeasurementCondition.UNPOWERED
        elif any(token in context for token in ("powered", "power on", "on", "connected")):
            condition = MeasurementCondition.POWERED
        else:
            condition = MeasurementCondition.UNKNOWN

        return Measurement(
            name=row.name, kind=kind, state=state, value=value, unit=unit,
            location=row.point or "", reference_point=row.reference or "",
            condition=condition, instrument="", notes=(row.note or "") +
            " [Imported from host EDA session; host schema has no explicit MeasurementState field.]",
        )

    def _build_host_session(self, session_id: str, platform: str):
        from eda import sessions, measurements
        from eda.sessions import symptoms as list_symptoms
        from DiagnosticEngines.core.framework import Case, DiagnosticSession

        try:
            sid = int(session_id)
        except (TypeError, ValueError):
            raise ValueError("session_id must be an integer ID from the host EDA database")
        host_session = sessions.get(sid)
        if host_session is None:
            raise ValueError(f"Host repair session {sid} does not exist")
        pack_case = Case(
            case_id=f"EDA_SESSION_{sid}", platform=platform or "laptop",
            device_model=host_session.title or None,
            board_model=host_session.board_model or None,
            symptoms=list_symptoms(sid),
            technician_notes=[host_session.notes] if host_session.notes else [],
        )
        pack_session = DiagnosticSession(case=pack_case)
        for row in measurements.list_for(sid):
            pack_session.add_measurement(self._host_measurement_to_pack(row))
        return pack_session

    def run(self, method: str, inputs: dict):
        if method == "ingest_file":
            try:
                source_path = Path(inputs["path"]).expanduser().resolve()
                if not source_path.exists() or not source_path.is_file():
                    raise ValueError(f"path must identify one existing local file: {source_path}")
                tags_raw = inputs.get("tags_json", "")
                tags = json.loads(tags_raw) if tags_raw else []
                if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
                    raise ValueError("tags_json must be a JSON array of strings")
                adapter = self._get_adapter()
                from DiagnosticEngines.core.knowledge_platform import KnowledgeStore, IngestionPipeline
                store = KnowledgeStore(adapter.pack_root)
                pipeline = IngestionPipeline(store)
                item, stage_log = pipeline.ingest_file(
                    source_path,
                    source_title=inputs.get("source_title", ""),
                    source_author=inputs.get("source_author", ""),
                    source_url=inputs.get("source_url", ""),
                    category=inputs.get("category", "forum_post"),
                    technical_classification=inputs.get("technical_classification", ""),
                    tags=tags,
                )
                if item is None:
                    return self._ok(method, value={
                        "ingested": False,
                        "source_path": str(source_path),
                        "stage_log": stage_log,
                        "reason": "Extraction or validation declined this document; see stage_log.",
                    }, inputs=inputs,
                    limitations=["No knowledge record was written for this document."])
                value = {
                    "ingested": True,
                    "item_id": item.item_id,
                    "title": item.title,
                    "source_path": item.local_source_reference,
                    "source_url": item.source_url,
                    "sha256": item.source_sha256,
                    "mime_type": item.source_mime_type,
                    "extraction_method": item.extraction_method,
                    "extraction_status": item.extraction_status,
                    "verification_status": item.verification_status,
                    "category": item.category,
                    "characters": len(item.content_text),
                    "image_references": item.source_image_references,
                    "warnings": item.extraction_warnings,
                    "stage_log": stage_log,
                }
                return self._ok(method, value=value, inputs=inputs,
                                metadata={"pack_version": adapter.manifest().get("pack_version"),
                                          "deduplication": "SHA-256"},
                                limitations=["One explicitly supplied local file only; no directory crawl or network access.",
                                             "PDF OCR is not performed; image-only PDFs need a separate licensed/manual OCR workflow."])
            except (ValueError, TypeError, json.JSONDecodeError, ImportError, FileNotFoundError) as exc:
                from ..schemas import ErrorCode
                return self._err(method, ErrorCode.INVALID_INPUT, f"{type(exc).__name__}: {exc}", inputs=inputs)
            except Exception as exc:
                from ..schemas import ErrorCode
                return self._err(method, ErrorCode.INTERNAL, f"{type(exc).__name__}: {exc}", inputs=inputs)

        if method == "capabilities":
            try:
                caps = [self._json_value(c) for c in self._get_adapter().capabilities()]
                return self._ok(method, value=caps, inputs=inputs,
                                metadata={"pack_version": self._get_adapter().manifest().get("pack_version"),
                                          "pack_root": str(PACK_ROOT)})
            except Exception as exc:
                from ..schemas import ErrorCode
                return self._err(method, ErrorCode.UNAVAILABLE, str(exc), inputs=inputs)

        if method != "dispatch":
            from ..schemas import ErrorCode
            return self._err(method, ErrorCode.NOT_APPLICABLE, f"Unsupported method: {method}", inputs=inputs)

        try:
            engine_name = inputs["engine_name"].strip()
            if not engine_name:
                raise ValueError("engine_name cannot be empty")
            raw_payload = inputs.get("payload_json", "")
            payload = json.loads(raw_payload) if raw_payload else {}
            if not isinstance(payload, dict):
                raise ValueError("payload_json must contain a JSON object")
            platform = inputs.get("platform") or payload.get("platform") or "laptop"
            payload.setdefault("platform", platform)
            pack_session = None
            if inputs.get("session_id"):
                pack_session = self._build_host_session(inputs["session_id"], platform)
                payload.setdefault("observed_symptoms", list(pack_session.case.symptoms))
                if engine_name in {"power_on_sequence", "motherboard", "power_supply"} and not payload.get("measurements"):
                    payload["measurements"] = [m.to_dict() for m in pack_session.measurements]

            from Integration.host_adapter import DiagnosticRequest
            response = self._get_adapter().handle(
                DiagnosticRequest(engine=engine_name, payload=payload, session=pack_session)
            )
            result = {
                "ok": bool(response.ok),
                "engine": response.engine,
                "error": response.error,
                "notes": list(response.notes),
                "conclusion": self._json_value(response.conclusion),
                "source_host_session_id": inputs.get("session_id") or None,
                "session_mapping": {
                    "read_only": True,
                    "host_measurement_state": "inferred from stored numeric value; host schema does not preserve NOT_MEASURED vs MEASURED_ZERO explicitly",
                    "measurement_condition": "UNKNOWN unless the existing host context fields explicitly indicate powered/unpowered",
                } if pack_session is not None else None,
            }
            if not response.ok:
                from ..schemas import ErrorCode
                return self._err(method, ErrorCode.NOT_APPLICABLE, response.error or "Pack dispatch failed",
                                 inputs=inputs)
            return self._ok(method, value=result, inputs=inputs,
                            limitations=["No hardware measurements were performed by this bridge.",
                                         "Host DB remains authoritative; the pack session is in-memory and read-only."])
        except (ValueError, TypeError, json.JSONDecodeError, ImportError, FileNotFoundError) as exc:
            from ..schemas import ErrorCode
            return self._err(method, ErrorCode.INVALID_INPUT, f"{type(exc).__name__}: {exc}", inputs=inputs)
        except Exception as exc:  # never leak tracebacks through the engine interface
            from ..schemas import ErrorCode
            return self._err(method, ErrorCode.INTERNAL, f"{type(exc).__name__}: {exc}", inputs=inputs)
