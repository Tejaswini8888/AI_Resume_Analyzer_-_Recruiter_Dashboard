"""Candidate information extraction.

All extraction is section-boundary aware: fields are pulled from the header /
CONTACT region or from the bounded EDUCATION section, never from an unbounded
whole-text scan, so "Education" cannot swallow the Skills section and email or
phone cannot be picked out of unrelated resume prose.
"""

import re

from modules.sections import section_boundaries, section_content

# ---------------------------------------------------------------------------
# Contact patterns (shared with the header-scoped search).
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
_PHONE_RE = re.compile(r"(\+?\d[\d\s\-\(\)]{8,18}\d)")
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?")
_GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_-]+/?")

_NAME_IGNORE = {
    "resume",
    "curriculum vitae",
    "cv",
    "profile",
    "summary",
}


def _header_block(text):
    """Lines before the first section boundary (name + contact region)."""
    lines = text.splitlines()
    out = []
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        if i in [b[0] for b in section_boundaries(text)]:
            break
        out.append(stripped)
    return "\n".join(out)


def _contact_scope(text):
    """Header plus (when present) the CONTACT section, priority in that order."""
    header = _header_block(text)
    contact = section_content(text, "contact")
    return header + "\n" + contact if contact else header


def extract_email(text):
    emails = _EMAIL_RE.findall(_contact_scope(text))
    if not emails:
        emails = _EMAIL_RE.findall(text)
    return emails[0] if emails else "Not Found"


def extract_phone(text):
    phones = _PHONE_RE.findall(_contact_scope(text))
    if not phones:
        phones = _PHONE_RE.findall(text)
    return phones[0] if phones else "Not Found"


def extract_linkedin(text):
    links = _LINKEDIN_RE.findall(_contact_scope(text))
    if not links:
        links = _LINKEDIN_RE.findall(text)
    return links[0] if links else "Not Found"


def extract_github(text):
    links = _GITHUB_RE.findall(_contact_scope(text))
    if not links:
        links = _GITHUB_RE.findall(text)
    return links[0] if links else "Not Found"


def extract_location(text):
    """Best-effort location scan of the header/CONTACT region only."""
    scope = _contact_scope(text)
    for raw in scope.splitlines():
        line = raw.strip()
        if not line or len(line) > 60:
            continue
        if "@" in line or "http" in line.lower() or "linkedin" in line.lower():
            continue
        if _EMAIL_RE.search(line) or _PHONE_RE.search(line):
            continue
        if "," in line and sum(ch.isalpha() for ch in line) / max(len(line), 1) >= 0.6:
            return line
    return ""


def extract_name(text):
    """First plausible person-name line in the header block."""
    header = _header_block(text)
    for line in header.splitlines()[:6]:
        line = line.strip().strip(",").strip()
        if not line:
            continue
        if line.lower() in _NAME_IGNORE:
            continue
        if "@" in line or "http" in line.lower():
            continue
        if re.search(r"\d", line) or "|" in line:
            continue
        if not (2 <= len(line.split()) <= 4):
            continue
        cleaned = re.sub(r"[\.'\- ]", "", line)
        if cleaned.isalpha():
            return line
    return None


# ---------------------------------------------------------------------------
# Education extraction (bounded by the EDUCATION section).
# ---------------------------------------------------------------------------

_DEGREE_PATTERN = re.compile(
    r"Bachelor of Technology|Bachelor of Engineering|Bachelor of Computer "
    r"Applications|Bachelor of Science|Master of Technology|Master of Computer "
    r"Applications|Master of Business Administration|Master of Science|B\.Tech|"
    r"B\.E\b|B\.Sc|M\.Tech|M\.Sc\b|\bMCA\b|\bMBA\b|Ph\.?D|Diploma|Higher "
    r"Secondary|High School|Secondary School|10\+2|Intermediate",
    re.IGNORECASE,
)

_EDUCATION_BOUNDARY = re.compile(
    r"^\s*(?:SKILLS?|TECHNICAL SKILLS?|TECHNICAL STRENGTHS?|PROJECTS?|"
    r"ACADEMIC PROJECTS?|WORK EXPERIENCE|EXPERIENCE|PROFESSIONAL EXPERIENCE|"
    r"PROFESSIONAL SUMMARY|SUMMARY|CAREER OBJECTIVE|OBJECTIVE|"
    r"CERTIFICATIONS?|CERTIFICATES?|ACHIEVEMENTS?|AWARDS?|HONORS?|LANGUAGES?|"
    r"INTERESTS?|HOBBIES?|REFERENCES?|ACTIVITIES|PERSONAL DETAILS?|CONTACT|"
    r"DECLARATION|INTERNSHIP)\s*:?",
    re.IGNORECASE,
)

_INLINE_BOUNDARY = re.compile(
    r"^\s*(skills?|technical skills?|projects?|experience|summary|objective|"
    r"certifications?|achievements?|languages?|interests?|references?|"
    r"declaration)\s*:",
    re.IGNORECASE,
)

_SKILL_CUE = re.compile(
    r"[,;](?:\s+(?:with|in|on|by|and)\s+)?"
    r"(?:including|having|knowledge of|familiar with|proficient in|"
    r"skilled in|expertise in|hands-on|foundation|object\.oriented|\boop\b|"
    r"programming in|working with|exposure to|coding)",
    re.IGNORECASE,
)

_EDU_CLEANUP = re.compile(r"[*_`#]+")


def _fallback_education_scope(text):
    """When no EDUCATION heading exists, bound the degree by the nearest
    section boundaries in the body."""
    match = _DEGREE_PATTERN.search(text)
    if not match:
        return ""
    lines = text.splitlines()
    start_line = text[: match.start()].count("\n")
    boundaries = [b[0] for b in section_boundaries(text)]
    end_line = next((b for b in boundaries if b > start_line), len(lines))
    return "\n".join(lines[start_line:end_line])


def _build_education_block(scope_text):
    """Degree line plus adjacency, cleaned so skills never ride along."""
    match = _DEGREE_PATTERN.search(scope_text)
    if not match:
        return "Not Found"

    tail = scope_text[match.end():]
    edu_line = (match.group(0) + tail.split("\n", 1)[0]).strip()
    block_lines = [edu_line]

    for line in tail.split("\n")[1:]:
        stripped = line.strip()
        if not stripped:
            break
        if _EDUCATION_BOUNDARY.match(stripped) or _INLINE_BOUNDARY.match(stripped):
            break
        block_lines.append(stripped)
        if len(block_lines) >= 3:
            break

    block = " ".join(block_lines)
    block = _EDU_CLEANUP.sub("", block).strip().strip("|").strip()

    # Raw education text may contain degrees with unhelpful trailing info after
    # an unmatched ")" — cut there so "with a strong foundation in C, C++, Java"
    # never attaches to the degree.
    depth = 0
    for index, char in enumerate(block):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                block = block[:index]
                break

    skill_cut = _SKILL_CUE.search(block)
    if skill_cut:
        block = block[: skill_cut.start()]

    block = block.rstrip(",;:| ").strip()
    return block if block else "Not Found"


def extract_education(text):
    """Education bounded by its own section (never swallows SKILLS/EXPERIENCE)."""
    scope = section_content(text, "education")
    if not scope:
        scope = _fallback_education_scope(text)
    return _build_education_block(scope)


def extract_candidate_details(text):
    """Backward-compatible dict used by the Resume Results page and report."""
    return {
        "name": extract_name(text),
        "email": extract_email(text),
        "phone": extract_phone(text),
        "location": extract_location(text),
        "linkedin": extract_linkedin(text),
        "github": extract_github(text),
        "education": extract_education(text),
    }