"""Phase 6b — runnable blind-validation example (self-test).

This is the EXACT workflow documented in DEPLOYMENT_PROCEDURE.md. We run
it here to prove it executes against the real API (no placeholders).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eda import config, database, sessions, measurements
from eda import validation_framework as VF
from eda import held_out_evaluation as HE

# --- 1. Create the repair session with real evidence ----------------------
database.init_db()
s = sessions.create("Lenovo G50-80 workshop 001", "Lenovo G50-80")
sessions.add_symptom(s.id, "no power, no LEDs when adapter inserted")
# Evidence that does NOT by itself establish a short: VIN present, +5VALW low.
measurements.add(s.id, {"name": "VIN", "value": 19.4, "unit": "V",
                        "point": "DC jack", "reference": "GND"})
measurements.add(s.id, {"name": "+5VALW", "value": 0.0, "unit": "V",
                        "point": "always-on rail", "reference": "GND"})

# --- 2. Create a real-unresolved validation case linked to the session ----
r = VF.create_case("workshop_2026_001", "real_unresolved",
                   session_id=s.id, board_model="Lenovo G50-80",
                   technician="Ali")
assert r["ok"], r
case_id = r["case_id"]

# --- 3. The assistant records its BLIND prediction (root cause hidden) ---
# The blind view hides the confirmed root cause:
bv = VF.get_case_for_prediction(case_id)
assert "confirmed_root_cause" not in bv["view"], \
    "blind view must not expose the answer"

# The assistant's hypotheses, with supporting/contradicting evidence and
# explicitly named missing information. These are the assistant's
# honest hypotheses given the current (insufficient) evidence — NOT a
# confirmed diagnosis.
hypotheses = [
    {
        "hypothesis": "+5VALW regulator not enabled (enable signal absent)",
        "score": 0.45,
        "supporting": ["+5VALW=0V", "VIN=19.4V (adapter ok)"],
        "contradicting": [],
        "missing": ["+5VALW enable signal voltage",
                   "resistance_to_ground on +5VALW"],
    },
    {
        "hypothesis": "+5VALW short to ground (low-impedance fault)",
        "score": 0.35,
        "supporting": ["+5VALW=0V"],
        "contradicting": ["VIN present (no over-current shutdown observed)"],
        "missing": ["resistance_to_ground on +5VALW",
                   "current draw on VIN"],
    },
    {
        "hypothesis": "open input path between jack and +5VALW stage",
        "score": 0.20,
        "supporting": [],
        "contradicting": ["VIN=19.4V at jack (input path to jack ok)"],
        "missing": ["voltage at +5VALW stage input"],
    },
]
# Recommended next test: a resistance-to-ground measurement that
# DISTINGUISHES the 'short' hypothesis from the 'not enabled' hypothesis.
next_tests = [
    {
        "test": "resistance_to_ground on +5VALW (board unpowered, "
                "adapter disconnected)",
        "rationale": "A low resistance (<~50 ohm) supports a short to "
                     "ground; a high resistance rules it out and points "
                     "to the enable/path hypotheses.",
        "distinguishes": ["+5VALW short to ground",
                         "+5VALW regulator not enabled"],
    },
]
rec = VF.record_prediction(case_id, hypotheses, next_tests)
assert rec["ok"] and rec["frozen"], rec

# --- 4. The technician performs the recommended test and records it -----
observed = VF.record_result(case_id,
    "resistance_to_ground on +5VALW", "12 ohm", "low impedance measured")
assert observed["ok"], observed

# --- 5. Only NOW (prediction frozen) does the technician reveal ----------
# The 12 ohm reading supports the short hypothesis. The technician
# confirms the real root cause after additional bench work.
rev = VF.reveal_and_confirm(case_id,
    confirmed_root_cause="shorted +5VALW bulk decoupling capacitor",
    outcome="fixed", technician="Ali")
assert rev["ok"], rev

# --- 6. Prediction is immutable after reveal (blind-evaluation integrity)
over = VF.record_prediction(case_id, [{"hypothesis": "changed", "score": 0.9}], [])
assert not over["ok"] and "frozen" in over["error"].lower(), over

# --- 7. Promote ONLY this confirmed, fixed case to the verified KB -------
prom = VF.promote_to_repair_kb(case_id)
assert prom["ok"], prom

# --- 8. Synthetic / unresolved cases cannot be promoted -------------------
syn = VF.create_case("synthetic_001", "synthetic", session_id=s.id)
VF.freeze_prediction(syn["case_id"])
bad = VF.promote_to_repair_kb(syn["case_id"])
assert not bad["ok"], "synthetic must not promote"
unr = VF.create_case("unresolved_002", "real_unresolved", session_id=s.id)
VF.freeze_prediction(unr["case_id"])
VF.reveal_and_confirm(unr["case_id"], "unknown", "no-fix", "Ali")
bad2 = VF.promote_to_repair_kb(unr["case_id"])
assert not bad2["ok"], "unresolved (non-fixed) must not promote"

# --- 9. Held-out: register + leakage check + blind run -------------------
# (Register the SAME case as held-out for illustration; a real held-out
# case would have fresh evidence not used during development.)
he = HE.register_held_out(case_id, dataset="held_out",
                          source="workshop 2026-01, technician Ali",
                          created_by="Ali")
assert he["ok"], he
# No dev fixture with the same evidence exists yet -> no leakage.
leak = HE.detect_leakage()
assert not leak["leakage"], leak

# A predictor that mimics the assistant: it sees only the blind view.
def predictor(blind_view):
    # blind_view has NO confirmed_root_cause (blind).
    assert "confirmed_root_cause" not in blind_view
    return {"hypotheses": hypotheses, "next_tests": next_tests,
            "answer_text": "+5VALW short suspected; confirm with R-to-GND"}

res = HE.run_evaluation([case_id], predictor)
# Re-run must not overwrite the already-frozen prediction.
res2 = HE.run_evaluation([case_id], predictor)
assert res["aggregate"]["totals"]["cases_attempted"] == 1

print("BLIND VALIDATION EXAMPLE: OK")
print("  case:", case_id, "| promoted:", prom["ok"])
print("  leakage:", leak["leakage"])
print("  aggregate:", res["aggregate"]["metrics"])
