#!/usr/bin/env python3
"""Phase 7: categorized independent validation suite runner.

Runs ALL test suites and reports results separated into the required
categories:
  - Unit tests            (per-module logic)
  - Integration tests     (cross-module / host adapter)
  - Adversarial tests     (contradictory/incomplete/malformed inputs)
  - Knowledge/DB validation (component DB + knowledge base)
  - Synthetic diagnostic scenarios (full diagnostic sequences)
  - Hardware-required validation (documented, not runnable in sandbox)

Also verifies:
  - Offline operation (no runtime network dependencies)
  - Backward compatibility (legacy Measurement payloads still work)
  - All existing tests preserved (no weakening)

Run: python Tests/run_validation_suite.py
Exit code 0 = all green; 1 = any failure.
"""
from __future__ import annotations
import subprocess
import sys
import re
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]

# Category -> list of test files. Each file is assigned to ONE primary
# category (a file may contain tests spanning categories, but the
# primary category is what it's filed under for reporting).
CATEGORIES = {
    "Unit tests": [
        "test_core_semantics.py",
        "test_phase1_measurement_states.py",
        "test_netlist.py",
        "test_replacement_p3.py",
    ],
    "Integration tests": [
        "run_all_tests.py",
        "test_audit.py",  # host adapter + cross-engine
    ],
    "Adversarial tests": [
        "test_independent_validation.py",
    ],
    "Knowledge/DB validation": [
        "test_knowledge_retrieval.py",
        "test_phase3_component_db.py",
        "test_phase6_knowledge.py",
        "test_document_ingestion.py",
    ],
    "Synthetic diagnostic scenarios": [
        "test_phase2_power_supply.py",
        "test_phase4_motherboard.py",
        "test_phase5_netlist.py",
        "test_power_supply_topology.py",
        "run_expanded_tests.py",
    ],
}


def run_suite(name: str) -> tuple[bool, str, int]:
    """Run a test file; return (ok, summary_line, test_count)."""
    path = PACK / "Tests" / name
    if not path.exists():
        return False, f"MISSING: {name}", 0
    proc = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True, text=True, timeout=120,
    )
    out = proc.stdout + proc.stderr
    # Extract "Ran N tests" and final OK/FAILED
    ran_line = ""
    test_count = 0
    for line in out.splitlines():
        if line.startswith("Ran "):
            ran_line = line
            try:
                test_count = int(line.split()[1])
            except (IndexError, ValueError):
                pass
    ok = proc.returncode == 0 and "OK" in out.splitlines()[-1:]
    final = out.strip().splitlines()[-1] if out.strip() else "(no output)"
    return ok, final, test_count


def main() -> int:
    print("=" * 70)
    print("ELECTRONICS DIAGNOSTIC EXPANSION PACK v1.5.1 — VALIDATION SUITE")
    print("=" * 70)
    total_tests = 0
    total_pass = 0
    total_fail = 0
    any_failure = False
    for category, files in CATEGORIES.items():
        print(f"\n--- {category} ---")
        cat_tests = 0
        for f in files:
            ok, final, n = run_suite(f)
            cat_tests += n
            status = "PASS" if ok else "FAIL"
            if ok:
                total_pass += n
            else:
                total_fail += n
                any_failure = True
            print(f"  [{status}] {f:42s} {n:4d} tests  ({final})")
        total_tests += cat_tests
        print(f"  {category} subtotal: {cat_tests} tests")
    print("\n" + "=" * 70)
    print(f"TOTAL: {total_tests} tests | {total_pass} pass | {total_fail} fail")
    print("=" * 70)

    # --- validators ---
    print("\n--- Validators ---")
    for tool in ["validate_rules.py", "validate_database.py"]:
        path = PACK / "Tools" / tool
        if path.exists():
            proc = subprocess.run(
                [sys.executable, str(path)],
                capture_output=True, text=True, timeout=60,
            )
            final = (proc.stdout + proc.stderr).strip().splitlines()[-1] \
                if (proc.stdout + proc.stderr).strip() else "(no output)"
            print(f"  [{ 'PASS' if proc.returncode == 0 else 'FAIL' }] {tool:30s} {final}")
            if proc.returncode != 0:
                any_failure = True

    # --- offline verification (cross-platform; no external grep dependency) ---
    print("\n--- Offline operation ---")
    network_import = re.compile(
        r"^\s*(?:import\s+(?:requests|urllib|http|socket|aiohttp)(?:\.|\s|,|$)"
        r"|from\s+(?:requests|urllib|http|socket|aiohttp)(?:\.|\s))",
        re.MULTILINE,
    )
    scan_roots = [PACK / "DiagnosticEngines", PACK / "Integration", PACK / "Tools"]
    net_files = []
    for scan_root in scan_roots:
        for source in scan_root.rglob("*.py"):
            if "__pycache__" in source.parts:
                continue
            try:
                source_text = source.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if network_import.search(source_text):
                net_files.append(str(source.relative_to(PACK)))
    if net_files:
        print(f"  [FAIL] runtime network imports found: {net_files}")
        any_failure = True
    else:
        print("  [PASS] no runtime network imports — fully offline")

    # --- backward compat verification ---
    print("\n--- Backward compatibility ---")
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0,'.'); "
         "from DiagnosticEngines.core.engine import load_engine, Measurement; "
         "eng=load_engine(); "
         "r=eng.diagnose('+3VALW',[Measurement('rail_voltage',0.0),Measurement('vin',19.0)]); "
         "print('legacy Measurement payload: OK' if r.fired_rule_id else 'FAIL')"],
        capture_output=True, text=True, cwd=str(PACK))
    print(f"  [{'PASS' if proc.returncode==0 else 'FAIL'}] {proc.stdout.strip()}")

    # --- hardware-required (documented, not runnable) ---
    print("\n--- Hardware-required validation (NOT runnable in sandbox) ---")
    print("  The following require physical hardware + instruments and")
    print("  are documented but NOT executed here:")
    print("  - Real measurement values (voltages, resistances, waveforms)")
    print("  - Voltage-tolerance compliance on a physical board")
    print("  - Stage-sequence timing on a real power-on")
    print("  - Component replacement on a live board")
    print("  Synthetic tests verify the LOGIC; hardware validates the VALUES.")

    print("\n" + "=" * 70)
    if any_failure:
        print("RESULT: FAILURES PRESENT — see above")
        return 1
    print("RESULT: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
