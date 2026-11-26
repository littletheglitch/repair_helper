"""Generate a small sample datasheet PDF for manual end-to-end testing.

This script creates a tiny PDF that mentions a fictional charger IC's
EN, UVLO and PGOOD behaviour, so a user can exercise the add-file +
search + analyze workflow without a real datasheet. The text is
deliberately generic; no real manufacturer part is implied.

Usage:
    python scripts/make_sample_data.py
    -> writes sample_documents/sample_charger_ic.pdf

Then in the CLI:
    repair> new SampleBoard sample
    repair> add-file "sample_documents/sample_charger_ic.pdf"
    repair> search "EN UVLO PGOOD"
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sample_documents"
OUT.mkdir(exist_ok=True)


def make_pdf():
    try:
        import fitz
    except Exception as e:
        print("PyMuPDF not available; cannot generate sample PDF:", e)
        return False
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 72), "Sample Charger IC datasheet (fictional, for testing only)")
    p.insert_text((72, 110), "EN pin: active high; threshold 1.2 V typical.")
    p.insert_text((72, 140), "UVLO: 4.0 V typical; releases EN above this VIN.")
    p.insert_text((72, 170), "PGOOD: open-drain; asserted high when VOUT is in regulation.")
    p.insert_text((72, 200), "OCP: 4 A typical; SCP latches the converter off.")
    p.insert_text((72, 230), "Feedback reference: 0.8 V internal.")
    doc.save(str(OUT / "sample_charger_ic.pdf"))
    doc.close()
    print(f"Wrote {OUT / 'sample_charger_ic.pdf'}")
    return True


def make_text():
    (OUT / "sample_schematic_notes.txt").write_text(
        "Sample board schematic notes (fictional, for testing only).\n"
        "EC: input ACOK on EC pin 12 (active high).\n"
        "Charger IC ACOK output: charger pin 8 (open drain).\n"
        "Always-on converter EN: EC pin 33 (active high).\n"
        "+3VALW inductor: L5; test point TP3.\n"
        "+5VALW inductor: L7; test point TP5.\n"
        "VRM controller: fictional part SAMPLE-VRM-1.\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUT / 'sample_schematic_notes.txt'}")


if __name__ == "__main__":
    ok = make_pdf()
    make_text()
    sys.exit(0 if ok else 1)
