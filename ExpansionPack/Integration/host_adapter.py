"""Host Adapter — the single entry point a host application uses.

The host never imports individual engines. It imports this module and
calls `ExpansionPackAdapter`. The adapter:

  * Detects the pack root (auto or explicit).
  * Loads the manifest + capability list.
  * Dispatches a diagnostic request to the right engine based on the
    request's `engine` field.
  * Merges the engine's conclusion into a framework DiagnosticSession.
  * Provides graceful fallback if a specialized engine is unavailable.

Public API:
  ExpansionPackAdapter                — the adapter class
  DiagnosticRequest                   — request dataclass
  Capability                          — capability descriptor

The adapter preserves all existing host functionality — it does NOT
mutate the host's session, measurements, or reports. The host calls
into the adapter; the adapter returns structured conclusions the host
can render however it wants.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any
import json
import sys

PACK_ROOT = Path(__file__).resolve().parents[1]  # Electronics_Diagnostic_Expansion_Pack/
if str(PACK_ROOT) not in sys.path:
    sys.path.insert(0, str(PACK_ROOT))

from DiagnosticEngines.core.framework import (  # noqa: E402
    Measurement, DiagnosticSession, Case, ReportBuilder,
)
from DiagnosticEngines.core.framework.session import EngineConclusion  # noqa: E402


@dataclass
class Capability:
    """One capability the pack exposes."""
    name: str               # 'motherboard.diagnose', 'power_supply.diagnose', etc.
    engine: str             # which engine module handles it
    description: str
    input_type: str         # the input dataclass name
    output_type: str        # 'EngineConclusion'


@dataclass
class DiagnosticRequest:
    """A request the host sends to the adapter.

    `engine` selects which specialized engine to invoke:
      - 'motherboard'   -> MotherboardEngine
      - 'power_supply'  -> PowerSupplyEngine
      - 'circuit'       -> CircuitAnalysisEngine
      - 'identify'      -> ComponentIdentificationEngine
      - 'replacement'   -> ReplacementEngine
      - 'measurement'   -> MeasurementIntelligenceEngine
      - 'repair_case'   -> FailurePatternDB search

    `payload` is a dict whose shape depends on the engine; the adapter
    validates it and constructs the right input dataclass.
    """
    engine: str
    payload: dict
    session: Optional[DiagnosticSession] = None  # if supplied, merge conclusion


@dataclass
class AdapterResponse:
    """The adapter's response. Either a conclusion (engine ran) or an
    error/fallback (engine unavailable or payload invalid)."""
    ok: bool
    engine: str
    conclusion: Optional[EngineConclusion] = None
    error: Optional[str] = None
    notes: list[str] = field(default_factory=list)


class ExpansionPackAdapter:
    """The host's single entry point into the Expansion Pack."""

    def __init__(self, pack_root: Optional[Path] = None):
        self.pack_root = pack_root or PACK_ROOT
        self._manifest: Optional[dict] = None
        self._engines: dict[str, Any] = {}
        self._capabilities: list[Capability] = self._build_capabilities()

    # --- manifest + capabilities --------------------------------------

    def manifest(self) -> dict:
        """Load and return the pack manifest (cached)."""
        if self._manifest is None:
            mp = self.pack_root / "manifest.json"
            if mp.exists():
                with open(mp, "r", encoding="utf-8") as f:
                    self._manifest = json.load(f)
            else:
                self._manifest = {"error": "manifest.json not found", "pack_root": str(self.pack_root)}
        return self._manifest

    def capabilities(self) -> list[Capability]:
        return list(self._capabilities)

    def _build_capabilities(self) -> list[Capability]:
        return [
            Capability("motherboard.diagnose", "motherboard",
                       "Diagnose a laptop or desktop motherboard power rail.",
                       "MotherboardDiagnosisInput", "EngineConclusion"),
            Capability("power_supply.diagnose", "power_supply",
                       "Diagnose a standalone power supply (ATX, adapter, charger, DC-DC).",
                       "PowerSupplyDiagnosisInput", "EngineConclusion"),
            Capability("circuit.analyze", "circuit",
                       "Analyze a PCB circuit family and recommend next tests.",
                       "CircuitAnalysisInput", "EngineConclusion"),
            Capability("component.identify", "identify",
                       "Identify a component from a partial marking + context.",
                       "IdentificationInput", "IdentificationResult"),
            Capability("replacement.evaluate", "replacement",
                       "Evaluate whether a candidate part can replace an original.",
                       "(original_part, candidate_part)", "ReplacementEvaluation"),
            Capability("measurement.analyze", "measurement",
                       "Analyze a session's measurements for abnormalities and inconsistencies.",
                       "DiagnosticSession", "MeasurementAnalysis"),
            Capability("repair_case.search", "repair_case",
                       "Search the failure-pattern database for matching repair cases.",
                       "search kwargs", "list[RepairCase]"),
            Capability("knowledge.search", "knowledge",
                       "Ranked knowledge retrieval (tokenized, provenance-aware).",
                       "{query, verified_only?, category?}", "EngineConclusion"),
            Capability("netlist.analyze", "netlist",
                       "Graph-based netlist analysis (validate, recognize, trace).",
                       "{netlist: {components, nets, external_ports?}}", "EngineConclusion"),
        ]

    # --- engine lazy loading -------------------------------------------

    def _get_engine(self, name: str) -> Optional[Any]:
        """Lazy-load an engine. Returns None if unavailable (graceful
        fallback — the adapter never raises on a missing engine)."""
        if name in self._engines:
            return self._engines[name]
        try:
            if name == "motherboard":
                from DiagnosticEngines.Motherboard import MotherboardEngine
                eng = MotherboardEngine(self.pack_root)
            elif name == "power_supply":
                from DiagnosticEngines.PowerSupply import PowerSupplyEngine
                eng = PowerSupplyEngine(self.pack_root)
            elif name == "circuit":
                from DiagnosticEngines.CircuitAnalysis import CircuitAnalysisEngine
                eng = CircuitAnalysisEngine(self.pack_root)
            elif name == "identify":
                from DiagnosticEngines.core.identification_engine import ComponentIdentificationEngine
                eng = ComponentIdentificationEngine(self.pack_root)
            elif name == "replacement":
                from DiagnosticEngines.core.replacement_engine import ReplacementEngine
                eng = ReplacementEngine(self.pack_root)
            elif name == "measurement":
                from DiagnosticEngines.core.measurement_intelligence import MeasurementIntelligenceEngine
                eng = MeasurementIntelligenceEngine(self.pack_root)
            elif name == "repair_case":
                from DiagnosticEngines.core.failure_pattern_db import FailurePatternDB
                eng = FailurePatternDB(self.pack_root)
            elif name == "knowledge":
                # A6-2 fix: expose the KnowledgeRetriever through the
                # adapter. The 'engine' is a tuple of (store, retriever).
                from DiagnosticEngines.core.knowledge_platform import (
                    KnowledgeStore, KnowledgeRetriever)
                store = KnowledgeStore(self.pack_root)
                eng = ("knowledge", store, KnowledgeRetriever(store))
            elif name == "netlist":
                # A6-3 fix: expose CircuitAnalysisEngine.analyze_netlist
                # through the adapter. Reuse the circuit engine.
                from DiagnosticEngines.CircuitAnalysis import CircuitAnalysisEngine
                eng = CircuitAnalysisEngine(self.pack_root)
            else:
                return None
            self._engines[name] = eng
            return eng
        except Exception as e:  # noqa: BLE001
            # Graceful fallback: record the error, return None.
            self._engines[name] = ("__error__", str(e))
            return None

    # --- dispatch ------------------------------------------------------

    def handle(self, req: DiagnosticRequest) -> AdapterResponse:
        """Dispatch a request to the right engine. Never raises."""
        engine_name = req.engine
        engine = self._get_engine(engine_name)
        if engine is None:
            cached = self._engines.get(engine_name)
            err_msg = (f"Engine '{engine_name}' unavailable."
                       if not cached else f"Engine '{engine_name}' failed to load: {cached[1]}")
            return AdapterResponse(ok=False, engine=engine_name, error=err_msg,
                                   notes=[f"Available engines: {[c.engine for c in self._capabilities]}"])

        payload = req.payload or {}
        try:
            if engine_name == "motherboard":
                from DiagnosticEngines.Motherboard import MotherboardDiagnosisInput, PLATFORM_LAPTOP
                inp = MotherboardDiagnosisInput(
                    platform=payload.get("platform", PLATFORM_LAPTOP),
                    rail_family=payload.get("rail_family", ""),
                    measurements=[Measurement(**m) if isinstance(m, dict) else m
                                  for m in payload.get("measurements", [])],
                    controller_part=payload.get("controller_part"),
                    rail_states=payload.get("rail_states", {}),
                )
                con = engine.diagnose(inp)
            elif engine_name == "power_supply":
                from DiagnosticEngines.PowerSupply import PowerSupplyDiagnosisInput
                inp = PowerSupplyDiagnosisInput(
                    topology_id=payload.get("topology_id", ""),
                    symptom=payload.get("symptom", ""),
                    measurements=[Measurement(**m) if isinstance(m, dict) else m
                                  for m in payload.get("measurements", [])],
                )
                con = engine.diagnose(inp)
            elif engine_name == "circuit":
                from DiagnosticEngines.CircuitAnalysis import CircuitAnalysisInput
                inp = CircuitAnalysisInput(
                    circuit_family=payload.get("circuit_family", ""),
                    symptom=payload.get("symptom", ""),
                    component_list=payload.get("component_list", []),
                )
                con = engine.analyze(inp)
            elif engine_name == "identify":
                from DiagnosticEngines.core.identification_engine import IdentificationInput
                inp = IdentificationInput(
                    part_number=payload.get("part_number", ""),
                    marking=payload.get("marking", ""),
                    manufacturer=payload.get("manufacturer", ""),
                    package=payload.get("package", ""),
                    pin_count=payload.get("pin_count"),
                    circuit_context=payload.get("circuit_context", ""),
                )
                # IdentificationEngine returns IdentificationResult, not EngineConclusion.
                # Wrap it in an EngineConclusion for uniformity.
                result = engine.identify(inp)
                con = EngineConclusion(
                    engine="identify",
                    rule_id=None,
                    next_steps=[{
                        "step": 1,
                        "action": f"Identification confidence: {result.confidence}",
                        "interpretations": [
                            {"if_result": c.part_number, "then": f"confidence={c.confidence} pinout_verified={c.pinout_verified} score={c.match_score} matched_on={c.matched_on}"}
                            for c in result.candidates[:5]
                        ],
                    }],
                    requires_info=result.requires_info,
                    requires_info_note=result.requires_info_note,
                    notes=result.notes,
                )
            elif engine_name == "replacement":
                ev = engine.evaluate(payload.get("original_part", ""), payload.get("candidate_part", ""))
                con = EngineConclusion(
                    engine="replacement",
                    rule_id=None,
                    next_steps=[{
                        "step": 1,
                        "action": f"Classification: {ev.classification} (type={ev.component_type})",
                        "interpretations": [
                            {"if_result": c.criterion, "then": f"verdict={c.verdict} note={c.note}"}
                            for c in ev.criteria
                        ],
                    }],
                    # A6-1 fix: requires_info must be True for both
                    # UNKNOWN (unknown type) AND INSUFFICIENT_DATA
                    # (missing critical spec — exactly when info IS
                    # needed). The old code only checked UNKNOWN.
                    requires_info=(ev.classification in ("UNKNOWN", "INSUFFICIENT_DATA")),
                    requires_info_note="; ".join(ev.notes) if ev.notes else "",
                    notes=[f"Conditions: {ev.conditions}"] if ev.conditions else [],
                )
            elif engine_name == "measurement":
                sess = req.session
                if sess is None:
                    return AdapterResponse(ok=False, engine=engine_name,
                                           error="measurement engine requires a DiagnosticSession in the request.")
                analysis = engine.analyze(sess)
                con = EngineConclusion(
                    engine="measurement",
                    rule_id=None,
                    next_steps=[{
                        "step": 1,
                        "action": analysis.summary,
                        "interpretations": [
                            {"if_result": f"{i.severity}/{i.category}/{i.measurement_name}", "then": i.description}
                            for i in analysis.issues
                        ],
                    }],
                    notes=[f"Missing expected: {analysis.missing_expected}"] if analysis.missing_expected else [],
                )
            elif engine_name == "repair_case":
                cases = engine.search(
                    board_model=payload.get("board_model"),
                    component=payload.get("component"),
                    rail=payload.get("rail"),
                    symptom=payload.get("symptom"),
                    failure_pattern=payload.get("failure_pattern"),
                    topology=payload.get("topology"),
                    platform=payload.get("platform"),
                    confidence=payload.get("confidence"),
                )
                con = EngineConclusion(
                    engine="repair_case",
                    rule_id=None,
                    next_steps=[{
                        "step": 1,
                        "action": f"Found {len(cases)} matching repair case(s).",
                        "interpretations": [
                            {"if_result": c.case_id, "then": f"{c.device_manufacturer} {c.device_model}: {c.confirmed_faulty_component} ({c.confidence})"}
                            for c in cases[:10]
                        ],
                    }],
                )
            elif engine_name == "knowledge":
                # A6-2 fix: knowledge retrieval through the adapter.
                # engine is a tuple ("knowledge", store, retriever).
                _, _store, retriever = engine
                query = payload.get("query", "")
                verified_only = bool(payload.get("verified_only", False))
                category = payload.get("category")
                results = retriever.search(query, verified_only=verified_only,
                                           category=category)
                con = EngineConclusion(
                    engine="knowledge",
                    rule_id=None,
                    next_steps=[{
                        "step": 1,
                        "action": f"Found {len(results)} knowledge item(s) for {query!r}.",
                        "interpretations": [
                            {"if_result": r.item.item_id,
                             "then": f"[{r.score:.3f}] {r.item.title} "
                                     f"(verified={r.item.verification_status}, "
                                     f"author={r.item.source_author}, "
                                     f"matched={r.matched_terms})"}
                            for r in results[:10]
                        ],
                    }],
                    requires_info=(len(results) == 0),
                    requires_info_note=("No knowledge items matched the query."
                                        if not results else ""),
                )
            elif engine_name == "netlist":
                # A6-3 fix: graph-based netlist analysis through the adapter.
                # engine is a CircuitAnalysisEngine.
                netlist_dict = payload.get("netlist", {})
                con = engine.analyze_netlist(netlist_dict)
            else:
                return AdapterResponse(ok=False, engine=engine_name,
                                       error=f"Unknown engine '{engine_name}'.")
        except Exception as e:  # noqa: BLE001
            return AdapterResponse(ok=False, engine=engine_name,
                                   error=f"Engine '{engine_name}' raised: {type(e).__name__}: {e}")

        # Merge into the session if supplied
        if req.session is not None and con is not None:
            req.session.merge_conclusion(con)

        return AdapterResponse(ok=True, engine=engine_name, conclusion=con,
                               notes=[f"Engine '{engine_name}' produced a conclusion."])

    # --- report generation --------------------------------------------

    def render_report(self, session: DiagnosticSession) -> str:
        """Convenience: render a Markdown report from a session."""
        return ReportBuilder(session).to_markdown()


def load_adapter(pack_root: Optional[Path] = None) -> ExpansionPackAdapter:
    """Convenience factory."""
    return ExpansionPackAdapter(pack_root)
