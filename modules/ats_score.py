"""Explainable ATS Compatibility Score for TalentIQ.

The analyzer is deterministic and operates on the structured ResumeProfile from
Phase 2 Step 1 (plus the centralized skill intelligence from Step 2). It is
fully independent from the UI: pages/ranking call ``analyze_ats`` and consume
an ATSScoreResult.

Design principles
-----------------
- Components that can be reliably detected are scored; nothing is rewarded for
  existing "just because" (no page counts, no arbitrary word/char counts, no
  fake formatting credit).
- Skills only count once per canonical skill (aliases fold upstream), so
  "Postgres" and "PostgreSQL" can never double count.
- The result is 0-100. The section scores always sum to the overall score.
- This is an *ATS Compatibility Score*, a heuristic readability/completeness
  estimate - it does not represent any real commercial ATS vendor score.

Weight budget (max total 100):
    Contact 15 | Summary 10 | Skills 25 | Education 10
    Experience 15 | Projects 10 | Certifications 5 | Structure 10
"""

from dataclasses import dataclass

from modules.skills import skill_categories
from modules.sections import split_sections

_NOT_FOUND = ("Not Found", "", None)

_MAIN_SECTIONS = [
    "summary",
    "skills",
    "education",
    "experience",
    "projects",
    "certifications",
]

_SECTION_LABELS = {
    "summary": "Professional summary",
    "skills": "Technical skills",
    "education": "Education",
    "experience": "Professional experience",
    "projects": "Projects",
    "certifications": "Certifications",
}

_CONTACT_WEIGHTS = {"name": 5, "email": 5, "phone": 3, "location": 2}
_SKILL_WEIGHT = 2
_SKILL_CAP = 25


@dataclass
class ATSScoreResult:
    """Structured, explainable ATS Compatibility Score for one resume."""

    overall_score: int
    section_scores: dict
    strengths: list
    issues: list
    detected_sections: list
    missing_sections: list
    keyword_coverage: dict


def _present(value):
    return value not in _NOT_FOUND


def _detected_section_ids(text):
    """Canonical section ids (document order) that actually carry content."""
    seen = []
    for section in split_sections(text):
        if section.canonical and section.has_content and section.canonical not in seen:
            seen.append(section.canonical)
    return seen


def _contact_values(profile):
    return {
        "name": profile.name if profile is not None else "",
        "email": profile.email if profile is not None else "",
        "phone": profile.phone if profile is not None else "",
        "location": profile.location if profile is not None else "",
    }


def _score_components(text, skills, profile):
    """Independent, deterministic per-component scores -> (dict, metadata)."""
    detected = _detected_section_ids(text)

    # A. Contact information (from structured extraction, not text guesses).
    contact = {"name": "", "email": "", "phone": "", "location": ""}
    if profile is not None:
        contact = _contact_values(profile)
    contact_score = sum(
        weight for field, weight in _CONTACT_WEIGHTS.items() if _present(contact[field])
    )

    # B. Professional summary (bounded by an actual SUMMARY section).
    summary_score = 10 if "summary" in detected else 0

    # C. Skills - canonical recognized skills, deduplicated upstream.
    skills = list(skills or [])
    skills_score = min(len(skills) * _SKILL_WEIGHT, _SKILL_CAP)

    # D. Education (a real, non-empty education section).
    education_score = 10 if ("education" in detected or profile and profile.education) else 0

    # E. Experience (content-rich work experience section).
    experience_lines = []
    if profile is not None:
        experience_lines = [ln for ln in (profile.experience or []) if ln.strip()]
    if len(experience_lines) >= 3:
        experience_score = 15
    elif len(experience_lines) >= 1:
        experience_score = 10
    else:
        experience_score = 0

    # F. Projects (bounded projects section).
    has_projects = "projects" in detected or bool(profile is not None and profile.projects)
    project_score = 10 if has_projects else 0

    # G. Certifications (bounded certifications section).
    has_certs = "certifications" in detected or bool(profile is not None and profile.certifications)
    certification_score = 5 if has_certs else 0

    # H. Structure / readability (organized sections + sane document shape).
    has_text = bool(text.strip())
    section_count_score = min(len(detected), 5) if has_text else 0
    chars = len(text)
    length_score = 3 if (has_text and 200 <= chars <= 30000) else 0
    longest_line = max((len(ln) for ln in text.splitlines() if ln.strip()), default=0)
    wrapping_score = 2 if (has_text and longest_line <= 160) else 0
    structure_score = section_count_score + length_score + wrapping_score

    section_scores = {
        "Contact": contact_score,
        "Summary": summary_score,
        "Skills": skills_score,
        "Education": education_score,
        "Experience": experience_score,
        "Projects": project_score,
        "Certifications": certification_score,
        "Structure": structure_score,
    }
    return section_scores, detected


def _keyword_coverage(skills):
    """Useful canonical coverage info (no naive substring matching)."""
    categories = skill_categories()
    by_category = {}
    for skill in skills:
        by_category[categories.get(skill, "Other")] = by_category.get(categories.get(skill, "Other"), 0) + 1
    return {
        "recognized_skills": len(skills),
        "unique_canonical_skills": len(set(skills)),
        "skill_categories": by_category,
    }


def _explanations(section_scores, detected, skills, contact, is_empty):
    """Deterministic strengths/issues derived only from actually seen data."""
    strengths = []
    issues = []

    if is_empty:
        issues.append("Resume is empty or could not be read.")

    if _present(contact.get("name")):
        strengths.append("Candidate name detected.")
    else:
        issues.append("Candidate name was not detected.")
    if _present(contact.get("email")):
        strengths.append("Contact information present (email detected).")
    else:
        issues.append("Email address was not detected.")
    if _present(contact.get("phone")):
        strengths.append("Contact information present (phone detected).")
    else:
        issues.append("Phone number was not detected.")

    if "summary" in detected:
        strengths.append("Professional summary section detected.")
    if "skills" in detected:
        strengths.append("Technical skills section detected.")
    if skills:
        strengths.append(f"Recognized technical skills were found ({len(skills)}).")
    else:
        issues.append("No recognized technical skills were found.")
    if "education" in detected:
        strengths.append("Education section detected.")
    if "experience" in detected:
        strengths.append("Work experience section detected.")
    if "projects" in detected:
        strengths.append("Projects section detected.")
    if "certifications" in detected:
        strengths.append("Certifications section detected.")

    missing = [s for s in _MAIN_SECTIONS if s not in detected]
    for section in missing:
        issues.append(f"{_SECTION_LABELS[section]} section was not detected.")

    if len(detected) >= 3:
        strengths.append("Resume uses a structured multi-section layout.")

    return strengths, issues, missing


def analyze_ats(profile=None, *, resume_text="", skills=None):
    """Compute the explainable ATS Compatibility Score for a ResumeProfile.

    ``profile`` is the structured input from Phase 2 Step 1. The keyword
    arguments are only used by the legacy ``calculate_ats_score`` wrapper,
    which has no profile (structured contact data is then unavailable).
    """
    if profile is not None:
        text = profile.raw_text or ""
        skill_list = list(skills) if skills is not None else list(profile.skills or [])
    else:
        text = resume_text or ""
        skill_list = list(skills or [])

    section_scores, detected = _score_components(text, skill_list, profile)
    contact = _contact_values(profile)
    strengths, issues, missing = _explanations(
        section_scores, detected, skill_list, contact, is_empty=not text.strip()
    )

    overall = sum(section_scores.values())
    overall = max(0, min(overall, 100))

    return ATSScoreResult(
        overall_score=overall,
        section_scores=section_scores,
        strengths=strengths,
        issues=issues,
        detected_sections=detected,
        missing_sections=missing,
        keyword_coverage=_keyword_coverage(skill_list),
    )


def calculate_ats_score(resume_text, detected_skills):
    """Legacy compatibility wrapper -> ``(score, breakdown_dict)``.

    Kept for callers that pass raw text + skills only. Prefer ``analyze_ats``
    with a ResumeProfile for the structured contact/section signals.
    """
    result = analyze_ats(resume_text=resume_text, skills=detected_skills)
    return result.overall_score, result.section_scores