"""Structured resume representation and the extraction orchestrator.

The pipeline is:

    PDF parser -> ResumeProfile (structured, section-bounded) ->
    intelligence services (ATS / JD / AI) -> UI

Pages never re-implement extraction logic; they consume a ResumeProfile.
"""

import re
from dataclasses import dataclass, field

from modules.parser import extract_text_from_pdf
from modules.candidate_details import extract_candidate_details, extract_education
from modules.sections import section_contents
from modules.skills import extract_skills

# Skill-talk filler that commonly rides inside EDUCATION blocks ("...strong
# foundation in C, C++, Java") and must not be kept as an education entry.
_EDUCATION_FILLER_RE = re.compile(
    r"\b(?:foundation in|knowledge of|understanding of|familiarity with|"
    r"hands-on|proficient in|working with)\b",
    re.IGNORECASE,
)


@dataclass
class ResumeProfile:
    """Structured, validated view of one parsed resume."""

    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    education: list = field(default_factory=list)
    experience: list = field(default_factory=list)
    skills: list = field(default_factory=list)
    projects: list = field(default_factory=list)
    certifications: list = field(default_factory=list)
    achievements: list = field(default_factory=list)
    raw_text: str = ""

    def to_details_dict(self):
        """Legacy flat dict shape expected by the Resume Results page/report."""
        return {
            "name": self.name or None,
            "email": self.email or "Not Found",
            "phone": self.phone or "Not Found",
            "location": self.location or "Not Found",
            "linkedin": self.linkedin or "Not Found",
            "github": self.github or "Not Found",
            "education": "\n".join(self.education) if self.education else "Not Found",
        }


def _section_lines(text, canonical, cap_lines=80):
    """Cleaned non-empty lines from every ``canonical`` section (bounded)."""
    out = []
    for block in section_contents(text, canonical):
        for line in block.splitlines():
            stripped = line.strip()
            if stripped and stripped not in out:
                out.append(stripped)
            if len(out) >= cap_lines:
                return out
    return out


def _education_lines(text):
    """Education section lines after skill-talk filler is filtered out."""
    out = []
    for block in section_contents(text, "education"):
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if _EDUCATION_FILLER_RE.search(stripped):
                continue
            if stripped not in out:
                out.append(stripped)
            if len(out) >= 10:
                return out
    return out


def build_profile_from_text(text):
    """Fill a ResumeProfile from already-extracted and normalized resume text."""
    details = extract_candidate_details(text)

    profile = ResumeProfile(
        name=details.get("name") or "",
        email=details.get("email") or "",
        phone=details.get("phone") or "",
        location=details.get("location") or "",
        linkedin=details.get("linkedin") or "",
        github=details.get("github") or "",
        raw_text=text,
    )
    profile.education = _education_lines(text)
    if not profile.education:
        edu = extract_education(text)
        if edu != "Not Found":
            profile.education = [edu]
    profile.experience = _section_lines(text, "experience")
    profile.projects = _section_lines(text, "projects")
    profile.certifications = _section_lines(text, "certifications")
    profile.achievements = _section_lines(text, "achievements")
    profile.skills = extract_skills(text)
    return profile


def build_resume_profile(uploaded_file):
    """Parse an uploaded resume PDF into a structured ResumeProfile.

    Raises ValueError when the PDF yields no readable text so callers can
    surface a clean error instead of silently scoring an empty resume.
    """
    text = extract_text_from_pdf(uploaded_file)
    if not text.strip():
        raise ValueError(
            "The PDF did not contain readable text (scanned or empty file)."
        )
    return build_profile_from_text(text)