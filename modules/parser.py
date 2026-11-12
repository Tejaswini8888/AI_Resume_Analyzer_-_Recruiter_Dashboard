"""PDF text extraction for TalentIQ.

Returns cleaned, line-preserved resume text. Never returns an "Error: ..."
preamble as if it were resume content — unreadable/corrupt PDFs yield an empty
string so the validation layer can flag them instead of scoring junk text.
"""

import re

import pdfplumber

# Collapse horizontal whitespace runs (keep single spaces).
_HSPACE_RE = re.compile(r"[ \t\xa0\u00a0\ufeff]+")
# Cap runs of blank lines at a single blank line.
_BLANK_RUN_RE = re.compile(r"\n{2,}")


def _clean_text(raw):
    """Normalize extracted text while preserving line/section structure."""
    lines = []
    for row in raw.splitlines():
        row = row.strip()
        if row:
            row = _HSPACE_RE.sub(" ", row)
        lines.append(row)
    text = "\n".join(lines)
    text = _BLANK_RUN_RE.sub("\n", text)
    return text.strip()


# Defensive parsing budgets: a hostile or degenerate PDF (thousands of pages,
# GBs of extracted text) is truncated so downstream token/score cost stays
# bounded. Pages are counted before extraction; characters before cleaning.
MAX_PAGES = 200
MAX_CHARS = 200_000


def extract_text_from_pdf(
    uploaded_file,
    *,
    max_pages: int = MAX_PAGES,
    max_chars: int = MAX_CHARS,
):
    """Read readable text out of a PDF (pdfplumber) and normalize it.

    Returns "" for empty, scanned-only, or corrupted PDFs instead of raising,
    so batch flows keep running and the validation layer decides on the result.
    Extraction stops once ``max_pages`` pages or ``max_chars`` characters have
    been reached, so no single document can exhaust memory or blow up the
    downstream LLM prompt.
    """
    raw_parts = []
    total_chars = 0
    try:
        with pdfplumber.open(uploaded_file) as pdf:
            for page_number, page in enumerate(pdf.pages):
                if page_number >= max_pages:
                    break
                page_text = page.extract_text()
                if not page_text:
                    continue
                remaining = max_chars - total_chars
                if remaining <= 0:
                    break
                if len(page_text) > remaining:
                    raw_parts.append(page_text[:remaining])
                    break
                total_chars += len(page_text)
                raw_parts.append(page_text)
    except Exception:
        return ""
    return _clean_text("\n".join(raw_parts))