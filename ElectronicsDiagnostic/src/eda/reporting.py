"""Repair report generator (Markdown + optional PDF).

The PDF path uses ReportLab and the strict font/safety rules of the
project. The Markdown path is the primary, always-available output.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config
from . import database
from . import sessions
from . import measurements as M


def render_markdown(session_id: int) -> str:
    s = sessions.get(session_id)
    if not s:
        return "# Error: session not found\n"
    lines = []
    lines.append(f"# Repair Report — {s.title}")
    lines.append("")
    lines.append(f"- **Board / model:** {s.board_model or '(not specified)'}")
    lines.append(f"- **Report date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- **Session created:** {s.created_at}")
    lines.append(f"- **Session status:** {s.status}")
    lines.append("")

    # Symptoms
    syms = sessions.symptoms(session_id)
    lines.append("## Reported symptoms")
    if syms:
        for x in syms:
            lines.append(f"- {x}")
    else:
        lines.append("- (none recorded)")
    lines.append("")

    # Measurements
    ms = M.list_for(session_id)
    lines.append("## Measurements")
    if ms:
        lines.append("| # | Name | Value | Unit | Point | Reference | Note |")
        lines.append("|---|------|-------|------|-------|-----------|------|")
        for i, m in enumerate(ms, 1):
            lines.append(
                f"| {i} | {m.name} | {m.value if m.value is not None else ''} | {m.unit} | "
                f"{m.point or ''} | {m.reference or 'GND (assumed)'} | {m.note or ''} |"
            )
    else:
        lines.append("(none recorded)")
    lines.append("")

    # Diagnostic timeline
    tl = sessions.timeline(session_id)
    lines.append("## Diagnostic timeline")
    if tl:
        for ev in tl:
            lines.append(f"- `{ev['event_at']}` **{ev['kind']}** — {ev['text']}")
    else:
        lines.append("(no events)")
    lines.append("")

    # Hypotheses
    hyps = database.query_all(
        "SELECT * FROM hypotheses WHERE session_id=? ORDER BY rank", (session_id,)
    )
    lines.append("## Hypotheses and ranking")
    if hyps:
        for h in hyps:
            lines.append(f"### {h['rank']}. {h['fault']}  (confidence {h['confidence']:.2f})")
            lines.append("")
            lines.append(f"**Why plausible:** {h['rationale']}")
            if h['supporting']:
                lines.append("")
                lines.append("**Supporting evidence:**")
                for s_ in h['supporting'].split(" | "):
                    if s_.strip():
                        lines.append(f"- {s_}")
            if h['contradicting']:
                lines.append("")
                lines.append("**Contradicting evidence:**")
                for c in h['contradicting'].split(" | "):
                    if c.strip():
                        lines.append(f"- {c}")
            lines.append("")
    else:
        lines.append("(no hypotheses yet; run `analyze`)")
    lines.append("")

    # Missing evidence
    miss = database.query_all(
        "SELECT item FROM missing_evidence WHERE session_id=? ORDER BY id",
        (session_id,),
    )
    lines.append("## Missing information")
    if miss:
        for m in miss:
            lines.append(f"- {m['item']}")
    else:
        lines.append("(no missing-evidence recorded)")
    lines.append("")

    # Next tests
    nts = database.query_all(
        "SELECT * FROM next_tests WHERE session_id=? ORDER BY priority DESC, id",
        (session_id,),
    )
    lines.append("## Recommended next tests")
    if nts:
        for nt in nts:
            lines.append(f"- **{nt['test']}**")
            lines.append(f"  - Where: {nt['where_to_measure']}")
            lines.append(f"  - Reference: {nt['reference_point']}")
            lines.append(f"  - Expected: {nt['expected']}")
            lines.append(f"  - Why: {nt['rationale']}")
    else:
        lines.append("(no next test recorded)")
    lines.append("")

    # Documents referenced
    docs = database.query_all(
        "SELECT file_name, file_type, status, sha256, size_bytes FROM documents WHERE session_id=?",
        (session_id,),
    )
    lines.append("## Documents referenced")
    if docs:
        for d in docs:
            lines.append(
                f"- `{d['file_name']}` ({d['file_type']}, {d['status']}, "
                f"sha256={d['sha256'][:16]}…, {d['size_bytes']} bytes)"
            )
    else:
        lines.append("(none)")
    lines.append("")

    # Confirmed vs probable
    lines.append("## Diagnosis")
    confirmed = _confirmed(session_id)
    if confirmed:
        lines.append(f"**Confirmed diagnosis:** {confirmed}")
    else:
        if hyps:
            top = max(hyps, key=lambda h: h['confidence'])
            lines.append(
                f"**Probable diagnosis (not confirmed):** {top['fault']} "
                f"— confidence {top['confidence']:.2f}. Additional measurements "
                f"are required to confirm."
            )
        else:
            lines.append("Not enough evidence for any diagnosis yet.")
    lines.append("")
    return "\n".join(lines)


def _confirmed(session_id: int) -> Optional[str]:
    row = database.query_one(
        "SELECT text FROM timeline WHERE session_id=? AND kind='confirmed' ORDER BY id DESC",
        (session_id,),
    )
    return row["text"] if row else None


def save_markdown(session_id: int) -> Path:
    md = render_markdown(session_id)
    s = sessions.get(session_id)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (s.title if s else "session"))
    out = config.app_dir("Reports") / f"report_{safe}_{session_id}.md"
    out.write_text(md, encoding="utf-8")
    return out


def save_pdf(session_id: int) -> Optional[Path]:
    """Render the markdown report to a PDF via ReportLab if available."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        )
        from reportlab.lib import colors
    except Exception:
        return None

    s = sessions.get(session_id)
    if not s:
        return None
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in s.title)
    out = config.app_dir("Reports") / f"report_{safe}_{session_id}.pdf"

    # Register a CJK-capable font so non-ASCII characters never crash the PDF.
    font_name = "Helvetica"
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        for cand in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ):
            try:
                pdfmetrics.registerFont(TTFont("EDABody", cand))
                font_name = "EDABody"
                break
            except Exception:
                continue
    except Exception:
        pass

    doc = SimpleDocTemplate(str(out), pagesize=A4,
                            leftMargin=18*mm, rightMargin=18*mm,
                            topMargin=18*mm, bottomMargin=18*mm)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("EDABody", parent=styles["Normal"], fontName=font_name,
                          fontSize=10, leading=14, spaceAfter=6)
    h1 = ParagraphStyle("EDAH1", parent=styles["Heading1"], fontName=font_name,
                        fontSize=16, leading=20, spaceAfter=10)
    h2 = ParagraphStyle("EDAH2", parent=styles["Heading2"], fontName=font_name,
                        fontSize=12, leading=16, spaceAfter=6)
    h3 = ParagraphStyle("EDAH3", parent=styles["Heading3"], fontName=font_name,
                        fontSize=11, leading=14, spaceAfter=4)
    md = render_markdown(session_id)
    story = []
    for line in md.splitlines():
        if not line.strip():
            story.append(Spacer(1, 4))
            continue
        if line.startswith("# "):
            story.append(Paragraph(_escape(line[2:]), h1))
        elif line.startswith("## "):
            story.append(Paragraph(_escape(line[3:]), h2))
        elif line.startswith("### "):
            story.append(Paragraph(_escape(line[4:]), h3))
        elif line.startswith("| ") and "---|" in line:
            # table row; build a Table from the next block of | rows.
            continue
        elif line.startswith("| "):
            continue  # table rows are batched below
        elif line.startswith("- "):
            story.append(Paragraph("• " + _escape(line[2:]), body))
        else:
            story.append(Paragraph(_escape(line), body))
    # Measurements table
    ms = M.list_for(session_id)
    if ms:
        story.append(Paragraph("Measurements (table)", h2))
        data = [["#", "Name", "Value", "Unit", "Point"]]
        for i, m in enumerate(ms, 1):
            data.append([
                str(i), m.name,
                str(m.value) if m.value is not None else "",
                m.unit or "", m.point or "",
            ])
        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([
            ("FONT", (0,0), (-1,-1), font_name, 8),
            ("GRID", (0,0), (-1,-1), 0.4, colors.grey),
            ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ]))
        story.append(t)
    doc.build(story)
    return out


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
