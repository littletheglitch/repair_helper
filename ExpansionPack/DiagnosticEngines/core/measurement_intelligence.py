"""Measurement Intelligence Engine.

Analyzes a DiagnosticSession's measurements for:
  * abnormal values (vs expected rail voltages)
  * inconsistent measurements (e.g. rail voltage present but PGOOD low)
  * missing measurements (slots the workflow expects but doesn't have)
  * invalid measurement conditions (e.g. resistance taken powered)
  * cross-rail dependencies (e.g. downstream rail up while upstream down)

Produces a structured report that the host can surface to the technician.
The engine is conservative: it never interprets a NOT_MEASURED as a
normal value, and it never infers a short from low resistance alone
without considering topology and condition.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import sys

PACK_ROOT = Path(__file__).resolve().parents[2]
if str(PACK_ROOT) not in sys.path:
    sys.path.insert(0, str(PACK_ROOT))

from DiagnosticEngines.core.framework import (  # noqa: E402
    Measurement, MeasurementState, MeasurementKind, MeasurementCondition,
    DiagnosticSession,
)
from DiagnosticEngines.core.engine import DiagnosticEngine  # noqa: E402


@dataclass
class MeasurementIssue:
    severity: str            # 'info' | 'warning' | 'error'
    category: str            # 'abnormal' | 'inconsistent' | 'missing' | 'invalid_condition' | 'dependency'
    measurement_name: str
    description: str


@dataclass
class MeasurementAnalysis:
    issues: list[MeasurementIssue] = field(default_factory=list)
    missing_expected: list[str] = field(default_factory=list)
    summary: str = ""


class MeasurementIntelligenceEngine:
    """Reasons over a session's measurements."""

    def __init__(self, pack_root: Optional[Path] = None):
        self.pack_root = pack_root or PACK_ROOT
        self.rail_engine = DiagnosticEngine(self.pack_root)

    def analyze(self, session: DiagnosticSession) -> MeasurementAnalysis:
        result = MeasurementAnalysis()
        measurements = session.measurements
        if not measurements:
            result.summary = "No measurements recorded."
            return result

        # 1. Invalid conditions: resistance taken powered, voltage taken unpowered
        for m in measurements:
            if m.kind == MeasurementKind.RESISTANCE and m.condition == MeasurementCondition.POWERED:
                result.issues.append(MeasurementIssue(
                    severity="error", category="invalid_condition",
                    measurement_name=m.name,
                    description=f"Resistance measurement '{m.name}' was taken POWERED. "
                                f"Resistance mode drives a current; on a powered rail this "
                                f"can damage the DMM and gives a meaningless reading. "
                                f"Re-take with the board UNPOWERED."
                ))
            if m.kind == MeasurementKind.VOLTAGE and m.condition == MeasurementCondition.UNPOWERED:
                result.issues.append(MeasurementIssue(
                    severity="warning", category="invalid_condition",
                    measurement_name=m.name,
                    description=f"Voltage measurement '{m.name}' was taken UNPOWERED. "
                                f"A voltage reading requires the board to be powered. "
                                f"The reading is not meaningful."
                ))

        # 2. Abnormal values: rail voltage vs expected.
        # Handle IN_RANGE (numeric comparison) AND BELOW_RANGE/ABOVE_RANGE
        # (the instrument already flagged an out-of-range reading).
        for m in measurements:
            if m.kind != MeasurementKind.VOLTAGE:
                continue
            # Try to map measurement name to a rail family.
            rail_family = self._infer_rail_family(m.name)
            if not rail_family:
                continue
            rail = self.rail_engine.all_rails().get(rail_family)
            if not rail:
                continue
            expected = rail.get("typical_voltage_v", {}) or {}
            typ = expected.get("typ")
            tol_min = expected.get("min")
            tol_max = expected.get("max")
            if typ is None:
                continue
            # BELOW_RANGE / ABOVE_RANGE: the instrument flagged it.
            if m.state == MeasurementState.BELOW_RANGE:
                result.issues.append(MeasurementIssue(
                    severity="warning", category="abnormal",
                    measurement_name=m.name,
                    description=f"Rail {rail_family} reading BELOW_RANGE on the instrument. "
                                f"The rail is absent or far below typical {typ} V."
                ))
                continue
            if m.state == MeasurementState.ABOVE_RANGE:
                result.issues.append(MeasurementIssue(
                    severity="warning", category="abnormal",
                    measurement_name=m.name,
                    description=f"Rail {rail_family} reading ABOVE_RANGE (OL) on the instrument. "
                                f"The rail may be floating (open) or far above typical {typ} V."
                ))
                continue
            if m.state != MeasurementState.IN_RANGE:
                continue
            val = m.numeric_value
            if val is None:
                continue
            # Check against tolerance
            if tol_min is not None and tol_max is not None:
                if val < tol_min:
                    result.issues.append(MeasurementIssue(
                        severity="warning", category="abnormal",
                        measurement_name=m.name,
                        description=f"Rail {rail_family} measured {val} V, below the typical "
                                    f"range [{tol_min}, {tol_max}] V. Possible overload or "
                                    f"upstream regulation issue."
                    ))
                elif val > tol_max:
                    result.issues.append(MeasurementIssue(
                        severity="warning", category="abnormal",
                        measurement_name=m.name,
                        description=f"Rail {rail_family} measured {val} V, above the typical "
                                    f"range [{tol_min}, {tol_max}] V. Possible feedback fault "
                                    f"or wrong rail identity."
                    ))
            elif abs(val - typ) > typ * 0.10:
                result.issues.append(MeasurementIssue(
                    severity="info", category="abnormal",
                    measurement_name=m.name,
                    description=f"Rail {rail_family} measured {val} V, more than 10% off "
                                f"typical {typ} V. Verify with a second measurement."
                ))

        # 3. Inconsistent measurements: rail voltage present but PGOOD low
        rail_families_present = set()
        rail_families_pgood_low = set()
        for m in measurements:
            if m.kind != MeasurementKind.VOLTAGE:
                continue
            rail_family = self._infer_rail_family(m.name)
            if rail_family and m.state == MeasurementState.IN_RANGE and m.numeric_value and m.numeric_value > 0.5:
                rail_families_present.add(rail_family)
            if m.name.endswith("pgood") or "pgood" in m.name.lower():
                if m.state in (MeasurementState.MEASURED_ZERO, MeasurementState.IN_RANGE) and m.numeric_value == 0:
                    rail_family = self._infer_rail_family(m.name.replace("pgood", "").replace("PGOOD", "").strip("_").strip("."))
                    if rail_family:
                        rail_families_pgood_low.add(rail_family)
        for rf in rail_families_pgood_low:
            if rf in rail_families_present:
                result.issues.append(MeasurementIssue(
                    severity="warning", category="inconsistent",
                    measurement_name=f"{rf}.pgood",
                    description=f"Rail {rf} is present (voltage > 0.5 V) but its PGOOD reads low. "
                                f"The controller is not declaring power-good despite the rail "
                                f"being up — check the PGOOD pull-up, the controller's PGOOD "
                                f"comparator, and any secondary monitors (OCP/OVP/OTP)."
                ))

        # 3b. Cross-rail dependency check: a downstream rail that is
        # present while its declared upstream is absent is physically
        # impossible (the downstream cannot come up without its source).
        # This usually means a measurement error (wrong test point,
        # wrong rail identified) or a boardview mismatch.
        try:
            from DiagnosticEngines.core.sequence_graph import load_sequence_graph
            seq_graph = load_sequence_graph(self.pack_root)
            for downstream in rail_families_present:
                node = seq_graph.nodes.get(downstream)
                if not node:
                    continue
                for upstream in node.upstream:
                    # Only flag if the upstream was MEASURED and is absent.
                    # An unmeasured upstream is not a contradiction.
                    upstream_meas = None
                    for m in measurements:
                        if self._infer_rail_family(m.name) == upstream and m.kind == MeasurementKind.VOLTAGE:
                            upstream_meas = m
                            break
                    if upstream_meas is None:
                        continue  # not measured — no contradiction
                    if upstream_meas.state in (MeasurementState.MEASURED_ZERO,) or \
                       (upstream_meas.state == MeasurementState.IN_RANGE and
                        upstream_meas.numeric_value is not None and
                        upstream_meas.numeric_value < 0.5):
                        result.issues.append(MeasurementIssue(
                            severity="warning", category="dependency",
                            measurement_name=downstream,
                            description=(
                                f"Rail {downstream} is present but its declared upstream "
                                f"{upstream} is absent (<0.5 V). This is physically impossible "
                                f"— {downstream} cannot come up without {upstream}. Likely "
                                f"causes: measurement at the wrong test point, wrong rail "
                                f"identified, or a boardview/schematic mismatch. Re-verify "
                                f"both measurements."
                            )
                        ))
        except Exception:  # noqa: BLE001
            # The sequence graph is optional; if it fails to load, skip
            # the cross-rail check rather than crashing the whole analysis.
            pass

        # 4. Missing measurements: surface NOT_MEASURED slots explicitly
        not_measured = [m.name for m in measurements if m.state == MeasurementState.NOT_MEASURED]
        if not_measured:
            result.missing_expected.extend(not_measured)
            result.issues.append(MeasurementIssue(
                severity="info", category="missing",
                measurement_name=",".join(not_measured[:5]),
                description=f"{len(not_measured)} measurement slot(s) are NOT_MEASURED: "
                            f"{not_measured[:5]}. These are tracked as missing, NOT as zero."
            ))

        # 5. Zero vs missing distinction check
        for m in measurements:
            if m.state == MeasurementState.MEASURED_ZERO:
                result.issues.append(MeasurementIssue(
                    severity="info", category="abnormal",
                    measurement_name=m.name,
                    description=f"Measurement '{m.name}' is genuinely 0.0 (MEASURED_ZERO), "
                                f"distinct from NOT_MEASURED. Treat as a real zero reading."
                ))

        # Summary
        n_err = sum(1 for i in result.issues if i.severity == "error")
        n_warn = sum(1 for i in result.issues if i.severity == "warning")
        result.summary = (
            f"Analyzed {len(measurements)} measurement(s). "
            f"{n_err} error(s), {n_warn} warning(s), "
            f"{len(result.missing_expected)} missing slot(s)."
        )
        return result

    def _infer_rail_family(self, name: str) -> Optional[str]:
        """Infer the rail family from a measurement name.

        A measurement named '+3VALW.vin' belongs to rail family '+3VALW'.
        The '.' separator denotes a sub-signal of a rail (e.g.
        '+3VALW.vin' is the VIN pin of the +3VALW regulator). The '_'
        separator is AMBIGUOUS — '+3VALW_AUX' could be a distinct rail
        or a sub-signal — so we do NOT split on '_' to avoid
        mis-attribution. Only '.' is treated as a sub-signal separator.

        To avoid mis-attribution we:
          1. Check if the full name is a known rail family.
          2. Otherwise, find the LONGEST known rail family that is a
             prefix of the name followed by '.'.
        """
        all_rails = self.rail_engine.all_rails()
        # 1. Exact match
        if name in all_rails:
            return name
        # 2. Longest known-rail prefix followed by '.'
        best: Optional[str] = None
        for rail_family in all_rails:
            prefix = rail_family + "."
            if name.startswith(prefix):
                if best is None or len(rail_family) > len(best):
                    best = rail_family
        return best
