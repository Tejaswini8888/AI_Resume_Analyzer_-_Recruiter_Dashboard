import io
import re

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


# ---------------------------------------------------------------------------
# Markdown / loose-HTML -> ReportLab paragraph markup
# ReportLab's Paragraph() only understands a small safe XML subset, so raw
# Markdown (tables, **bold**, `code`) and stray <br>/<b> tags must be
# converted before being handed over, otherwise it raises a paraparser error
# and the whole report fails to build.
# ---------------------------------------------------------------------------

_BR_TAG_RE = re.compile(r"</?br\s*/?>", re.IGNORECASE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_TABLE_SEPARATOR_RE = re.compile(r"\s*\|?[\s:\-|]+\|?\s*")


def _escape_text(text):
    """Escape HTML/XML special characters so leftover tags render as text."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _to_pdf_markup(text):
    """Convert one block of text to safe ReportLab paragraph markup.

    - <br>/<br/> tags become forced line breaks (kept, since they are valid
      ReportLab markup);
    - ``**bold**`` becomes <b>bold</b>;
    - every other character is HTML-escaped, so pipes, backticks, angle
      brackets and stray markup can never break the parser;
    - newlines are preserved as <br/> so long questions/tables wrap cleanly.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _BR_TAG_RE.sub("\n", text)

    lines = []
    for line in text.split("\n"):
        line = _escape_text(line)
        line = line.replace("`", "")
        line = _BOLD_RE.sub(r"<b>\1</b>", line)
        lines.append(line)

    return "<br/>".join(lines)


def _is_table_line(line):
    return line.count("|") >= 1


def _table_flow(raw_lines, styles):
    """Build a safe ReportLab Table from Markdown table lines (or None)."""
    rows = []
    for raw in raw_lines:
        if _TABLE_SEPARATOR_RE.fullmatch(raw):
            continue
        cells = raw.strip().strip("|").split("|")
        rows.append([cell.strip() for cell in cells])

    if not rows:
        return None

    ncols = max(len(row) for row in rows)
    body = []
    for row in rows:
        row_cells = [
            Paragraph(_to_pdf_markup(cell or " "), styles["BodyText"])
            for cell in row
        ]
        row_cells += [Paragraph(" ", styles["BodyText"])] * (ncols - len(row))
        body.append(row_cells)

    table = Table(body, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D0D0D0")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ECECEC")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _markdown_flowables(markdown_text, styles):
    """Split AI output into paragraph/table flowables that never crash ReportLab."""
    text = markdown_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    flow = []
    prose = []
    i = 0
    while i < len(lines):
        if _is_table_line(lines[i]) and i + 1 < len(lines) and _is_table_line(lines[i + 1]):
            if prose:
                flow.append(
                    Paragraph(_to_pdf_markup("\n".join(prose)), styles["BodyText"])
                )
                prose = []

            run = []
            while i < len(lines) and _is_table_line(lines[i]):
                run.append(lines[i])
                i += 1

            table = _table_flow(run, styles)
            if table is not None:
                flow.append(table)
        else:
            prose.append(lines[i])
            i += 1

    if prose:
        flow.append(Paragraph(_to_pdf_markup("\n".join(prose)), styles["BodyText"]))

    return flow if flow else [Paragraph("No content provided.", styles["BodyText"])]


def generate_pdf_report(
    candidate,
    summary,
    ats_score,
    skills,
    match_score,
    missing_skills,
    ai_analysis,
    questions
):
    """Build the report fully in memory and return the raw PDF ``bytes``.

    No temporary file is ever created, so nothing can leak on the filesystem.
    Every string handed to a ``Paragraph`` is HTML-escaped first: the resume
    candidate/name/skills are user-controlled content and ReportLab treats
    ``&``/``<``/``>`` as markup, which would otherwise abort the whole build or
    render hostile text.
    """

    buffer = io.BytesIO()
    pdf = SimpleDocTemplate(buffer)

    styles = getSampleStyleSheet()

    content = []

    content.append(
        Paragraph(
            "AI Resume Analysis Report",
            styles["Title"]
        )
    )

    content.append(
        Spacer(1, 12)
    )

    content.append(
        Paragraph(
            _escape_text(f"Name: {candidate['name']}"),
            styles["Normal"]
        )
    )

    content.append(
        Paragraph(
            _escape_text(f"Email: {candidate['email']}"),
            styles["Normal"]
        )
    )

    content.append(
        Spacer(1, 12)
    )

    content.append(
        Paragraph(
            _escape_text(f"ATS Score: {ats_score}/100"),
            styles["Heading2"]
        )
    )

    content.append(
        Paragraph(
            _escape_text(f"JD Match Score: {match_score if match_score else 0}%"),
            styles["Heading2"]
        )
    )

    content.append(
        Spacer(1, 12)
    )

    content.append(
        Paragraph(
            "Resume Summary",
            styles["Heading2"]
        )
    )

    content.extend(
        _markdown_flowables(summary, styles)
    )

    content.append(
        Spacer(1, 12)
    )

    content.append(
        Paragraph(
            "Detected Skills",
            styles["Heading2"]
        )
    )

    content.append(
        Paragraph(
            _escape_text(", ".join(skills)),
            styles["BodyText"]
        )
    )

    content.append(
        Spacer(1, 12)
    )

    content.append(
        Paragraph(
            "Missing Skills",
            styles["Heading2"]
        )
    )

    content.append(
        Paragraph(
            _escape_text(", ".join(missing_skills))
            if missing_skills
            else "None",
            styles["BodyText"]
        )
    )

    content.append(
        Spacer(1, 12)
    )

    content.append(
        Paragraph(
            "AI Analysis",
            styles["Heading2"]
        )
    )

    content.extend(
        _markdown_flowables(ai_analysis, styles)
    )

    content.append(
        Spacer(1, 12)
    )

    content.append(
        Paragraph(
            "Interview Questions",
            styles["Heading2"]
        )
    )

    content.extend(
        _markdown_flowables(questions, styles)
    )

    pdf.build(content)

    return buffer.getvalue()