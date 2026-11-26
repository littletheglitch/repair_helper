# Architecture

## Overview

The Electronics Diagnostic Expansion Pack is a modular, offline,
deterministic diagnostic system for electronics repair. It is built as
a portable Python package that a host application loads via a single
adapter (`Integration/host_adapter.py`).

## Design principles

1. **Deterministic first.** All core diagnostic reasoning is
   deterministic. The LLM is never required for diagnostic operation.
2. **No silent interpretation.** A measurement that was not taken is
   `NOT_MEASURED`, never treated as zero or normal.
3. **Safety in code, not just docs.** The SafetyPolicy blocks unsafe
   actions at the engine level; documentation alone is not enough.
4. **Provenance everywhere.** Every component record, replacement
   mapping, and knowledge item carries source attribution.
5. **No fabrication.** Specs, URLs, pinouts, and compatibility claims
   are sourced. Unverifiable data is marked `unknown`/`unverified`.

## Module map

```
DiagnosticEngines/
├── core/
│   ├── framework/          ← Core Diagnostic Framework (shared)
│   │   ├── case.py         ← Case + CaseStatus
│   │   ├── measurement.py  ← Measurement (zero != missing)
│   │   ├── evidence.py     ← Evidence + EvidenceStrength
│   │   ├── hypothesis.py   ← Hypothesis (evidence-based ranking)
│   │   ├── session.py      ← DiagnosticSession (merge point)
│   │   ├── next_test.py    ← NextTestSelector (info gain + safety)
│   │   ├── report.py       ← ReportBuilder (Markdown + dict)
│   │   └── safety.py       ← SafetyPolicy (hard guardrails)
│   ├── engine.py           ← Legacy power-rail engine (preserved)
│   ├── power_rail_bridge.py← Bridge: framework Measurements → legacy engine
│   ├── sequence_graph.py   ← Power-sequence dependency graph
│   ├── component_db.py     ← ComponentDatabase accessor
│   ├── replacement_engine.py  ← Evidence-based replacement evaluator
│   ├── identification_engine.py ← Component ID from markings
│   ├── measurement_intelligence.py ← Measurement analysis
│   ├── failure_pattern_db.py ← Repair cases + learning pipeline
│   ├── knowledge_platform.py ← Ingestion + retrieval framework
│   └── llm_orchestrator.py ← Lazy-load LLM with safety+evidence gates
├── Motherboard/            ← Laptop + desktop motherboard engine
├── PowerSupply/            ← 15 SMPS topologies + safety gate
├── CircuitAnalysis/        ← 36 circuit-family topology library
├── PowerRails/             ← Rail definitions + ALW rules
├── BuckConverter/ BoostConverter/ LDO/ VRM/ Charger/  ← Topology rules
└── (legacy rule files preserved)
```

## Data flow

```
Host application
   │
   ▼
ExpansionPackAdapter.handle(DiagnosticRequest)
   │
   ├─ motherboard  → MotherboardEngine → EngineConclusion
   ├─ power_supply → PowerSupplyEngine → EngineConclusion
   ├─ circuit      → CircuitAnalysisEngine → EngineConclusion
   ├─ identify     → ComponentIdentificationEngine → IdentificationResult
   ├─ replacement  → ReplacementEngine → ReplacementEvaluation
   ├─ measurement  → MeasurementIntelligenceEngine → MeasurementAnalysis
   └─ repair_case  → FailurePatternDB → list[RepairCase]
   │
   ▼
DiagnosticSession.merge_conclusion(conclusion)
   │
   ▼
ReportBuilder.to_markdown() / .to_dict()
```

## Execution modes

- **Diagnostic Mode** (`LLMConfig.mode='diagnostic'`): LLM never loaded.
  All engines run deterministically. This is the default.
- **Assistant Mode** (`LLMConfig.mode='assistant'`): LLM loaded lazily
  on first use. Safety + evidence gates reject unsafe or contradicting
  LLM output.

## Resource strategy

- Pure Python standard library; no third-party runtime dependencies.
- LLM loaded only on demand; never in Diagnostic Mode.
- Response cache in the LLM orchestrator avoids repeat inference.
- Conversation history is the host's responsibility; the orchestrator
  is stateless across calls except for the cache.

## Current counts

- IC records: 20
- MOSFET records: 5
- Discrete semiconductor records: 7
- Analog IC records: 12
- Replacement mappings: 9
- Rail families: 25
- Diagnostic rules: 39
- Power-supply topologies: 15
- Circuit topologies: 36
- Repair cases: 1
