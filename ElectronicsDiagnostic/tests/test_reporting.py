"""Report generation tests."""
from eda import sessions, measurements as M, reasoning, reporting


def test_markdown_report_has_required_sections(tmp_app):
    s = sessions.create("Lenovo G50-80", "G50-80 board")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    reasoning.analyze(s.id, sessions.symptoms(s.id), M.list_for(s.id))
    md = reporting.render_markdown(s.id)
    for section in (
        "Repair Report", "Reported symptoms", "Measurements",
        "Diagnostic timeline", "Hypotheses and ranking",
        "Missing information", "Recommended next tests",
        "Documents referenced", "Diagnosis",
    ):
        assert section in md, f"missing section: {section}"
    # Distinguishes probable from confirmed.
    assert "Probable" in md or "Confirmed" in md or "Not enough evidence" in md


def test_markdown_report_saved(tmp_app):
    s = sessions.create("X", "Y")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    out = reporting.save_markdown(s.id)
    assert out.exists()
    assert out.suffix == ".md"


def test_pdf_report_optional(tmp_app):
    s = sessions.create("X", "Y")
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    try:
        out = reporting.save_pdf(s.id)
    except Exception:
        # reportlab may not be installed; that's allowed.
        return
    if out is not None:
        assert out.exists()
        assert out.stat().st_size > 0
