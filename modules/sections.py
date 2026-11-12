"""Generic resume section segmentation.

Section headings are recognised from a vocabulary of aliases mapped to a
canonical section id, so the pipeline stays format-agnostic: it never assumes
a single resume template. Unknown all-caps "shouty" lines are still treated as
boundaries so one section can never bleed into the next.
"""

import re

# ---------------------------------------------------------------------------
# Canonical sections -> accepted heading spellings (normalized).
# ---------------------------------------------------------------------------
SECTION_ALIASES = {
    "contact": (
        "contact",
        "contact information",
        "contact details",
        "personal details",
        "personal information",
        "personal profile",
    ),
    "summary": (
        "summary",
        "professional summary",
        "executive summary",
        "career summary",
        "objective",
        "career objective",
        "about",
        "about me",
        "profile",
        "professional profile",
    ),
    "education": (
        "education",
        "educational details",
        "educational background",
        "academics",
        "academic details",
        "academic qualifications",
    ),
    "experience": (
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "employment history",
        "work history",
        "internship",
        "internships",
        "internship experience",
    ),
    "skills": (
        "skills",
        "skill",
        "technical skills",
        "technical skills and tools",
        "technical skills and technologies",
        "key skills",
        "core skills",
        "core competencies",
        "technical strengths",
        "technical profile",
        "areas of expertise",
        "technologies",
        "tools and technologies",
        "tools & technologies",
        "programming languages",
        "technical stack",
        "technical abilities",
    ),
    "projects": (
        "projects",
        "project",
        "academic projects",
        "academic project",
        "personal projects",
        "major projects",
        "technical projects",
    ),
    "certifications": (
        "certifications",
        "certification",
        "certificates",
        "certificate",
        "courses",
        "training",
        "trainings",
        "professional certifications",
    ),
    "achievements": (
        "achievements",
        "achievement",
        "awards",
        "honors",
        "honours",
        "accomplishments",
    ),
    "other": (
        "interests",
        "interests and activities",
        "hobbies",
        "activities",
        "references",
        "declaration",
        "soft skills",
        "languages",
        "languages known",
        "personal strengths",
        "extracurricular activities",
    ),
}

_ALIAS_TO_CANON = {}
for _canon, _aliases in SECTION_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_CANON[_alias] = _canon

# Map used to recognize unknown shouty headings as generic boundaries.
_HEADER_HONORIFICS = {
    "resume",
    "curriculum vitae",
    "curriculumvitae",
    "vitae",
    "cv",
    "profile data",
}

_MARKDOWN_RE = re.compile(r"[\*_`#~]+")
_LEADING_BULLET_RE = re.compile(r"^[\s\-•◦▪·*#]+")
_TRAILING_PUNCT_RE = re.compile(r"[:\s]+$")
_NON_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def normalize_heading(line):
    """Lowercase a heading line and strip decoration for alias matching."""
    text = _LEADING_BULLET_RE.sub("", line)
    text = _MARKDOWN_RE.sub("", text)
    text = _TRAILING_PUNCT_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def recognize_heading(line):
    """Return the canonical section id for a heading line, or None."""
    key = normalize_heading(line)
    if key in _ALIAS_TO_CANON:
        return _ALIAS_TO_CANON[key]
    compact = _NON_TOKEN_RE.sub("", key)
    if compact in _ALIAS_TO_CANON:
        return _ALIAS_TO_CANON[compact]
    return None


def _is_shouty(line, near_top):
    """Heuristic: an all-caps short line that reads like a section heading.

    ``near_top`` protects the name/header block: a short all-caps multi-word
    line in the first few lines is far more likely a person's name than a
    section heading, so it is not treated as a boundary.
    """
    if len(line) > 36:
        return False
    letters = [ch for ch in line if ch.isalpha()]
    if len(letters) < 3:
        return False
    upper = sum(1 for ch in letters if ch.isupper())
    if upper / len(letters) < 0.75:
        return False
    if re.search(r"\d", line):
        return False
    if "@" in line:
        return False
    if near_top and ":" not in line and len(line.split()) >= 2:
        return False
    if normalize_heading(line) in _HEADER_HONORIFICS:
        return False
    return True


def is_boundary_line(line, document_lines, line_index):
    """True when ``line`` starts/resumes a resume section boundary."""
    stripped = line.strip()
    if not stripped:
        return False
    if recognize_heading(stripped) is not None:
        return True
    near_top = line_index <= 2
    return _is_shouty(stripped, near_top)


def _nonempty_up_to(lines, idx):
    """Number of non-empty lines seen at or before line ``idx``."""
    return sum(1 for ln in lines[: idx + 1] if ln.strip())


def section_boundaries(text):
    """Yield (line_index, canonical_or_None, heading_line) in document order."""
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        if _nonempty_up_to(lines, i) <= 3:
            near_top = True
        else:
            near_top = False
        canon = recognize_heading(stripped)
        if canon is not None or _is_shouty(stripped, near_top):
            yield i, canon, stripped


class Section:
    """One headed block of the resume: canonical id plus its content text."""

    def __init__(self, canonical, heading_line, content):
        self.canonical = canonical
        self.heading_line = heading_line
        self.content = content

    @property
    def has_content(self):
        return bool(self.content)


def split_sections(text):
    """Return the list of Section objects in document order."""
    boundaries = list(section_boundaries(text))
    if not boundaries:
        return []

    lines = text.splitlines()
    sections = []
    for idx, (start_i, canon, heading_line) in enumerate(boundaries):
        end_i = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(lines)
        body = lines[start_i + 1:end_i] if end_i > start_i + 1 else []

        first_boundary = canon is not None
        colon = heading_line.find(":")
        inline = ""
        if first_boundary and colon != -1:
            inline = heading_line[colon + 1:].strip()

        content_lines = []
        if inline:
            content_lines.append(inline)
        for ln in body:
            stripped = ln.strip()
            if stripped:
                content_lines.append(stripped)
        # De-duplicate the rare exact repeats while preserving order.
        seen = set()
        unique = []
        for ln in content_lines:
            if ln not in seen:
                seen.add(ln)
                unique.append(ln)
        sections.append(Section(canon, heading_line, "\n".join(unique)))
    return sections


def section_contents(text, canonical):
    """Return the content of every ``canonical`` section (multi-line blocks)."""
    return [
        sec.content
        for sec in split_sections(text)
        if sec.canonical == canonical and sec.has_content
    ]


def section_content(text, canonical):
    """First occurrence of a ``canonical`` section, or '' when absent."""
    contents = section_contents(text, canonical)
    return contents[0] if contents else ""


def skill_scope_text(text):
    """Content of skill-like sections, or None when no such section exists.

    Used to restrict short / ambiguous skill matching to a skills context.
    """
    contents = section_contents(text, "skills")
    if not contents:
        return None
    return "\n".join(contents)