"""Job-description skill intelligence and skill gap analysis for TalentIQ.

Internally conservative rule-based required/preferred split: skills are
classified line-by-line using explicit cue wording. A segment's skills default
to REQUIRED when no preferred cue is present (so unspecified requirements are
not downgraded). This is deliberately internal - the public legacy helpers
(``extract_jd_skills`` / ``find_missing_skills``) keep their flat shapes.

Gap analysis happens entirely in canonical skill space, so an alias in the JD
("Postgres") and its canonical equivalent in the resume ("PostgreSQL") are
treated as the same skill (never a false "missing").
"""

import re
from dataclasses import dataclass

from modules.skills import extract_skills

_SPECIFIC_REQUIRED_CUES = re.compile(
    r"\b(?:must have|must-have|required|mandatory|essential|prerequisite)\b",
    re.IGNORECASE,
)

_SPECIFIC_PREFERRED_CUES = re.compile(
    r"\b(?:preferred|prefer|nice to have|nice-to-have|good to have|"
    r"good-to-have|desired|desirable|is a plus|a plus|bonus)\b",
    re.IGNORECASE,
)

# JD sections wrap into rows; classify per logical line. Colon-heavy prose is
# kept whole so cues like "Preferred: React, AWS" stay with their list.
_LINE_SPLIT = re.compile(r"\r?\n+")


@dataclass
class JDSkills:
    """Canonical skills from a job description, split by requirement tone."""

    required: list
    preferred: list

    def all(self):
        """Deduplicated union of required + preferred, sorted (deterministic)."""
        return sorted(set(self.required) | set(self.preferred))


def _segments(jd_text):
    return [seg.strip() for seg in _LINE_SPLIT.split(jd_text) if seg.strip()]


def classify_jd_skills(jd_text):
    """Rule-based (internal) required/preferred split of JD skills.

    Conservative rules:
    - explicit required wording  -> required
    - explicit preferred wording -> preferred
    - no wording or mixed wording -> required (requirements are not downgraded)
    """
    required = []
    preferred = []
    seen_required = set()
    seen_preferred = set()

    for segment in _segments(jd_text):
        segment_skills = extract_skills(segment)
        if not segment_skills:
            continue

        is_preferred = bool(_SPECIFIC_PREFERRED_CUES.search(segment)) and not (
            _SPECIFIC_REQUIRED_CUES.search(segment)
        )

        bucket, seen = (preferred, seen_preferred) if is_preferred else (required, seen_required)
        for skill in segment_skills:
            if skill not in seen:
                bucket.append(skill)
                seen.add(skill)

    return JDSkills(required=required, preferred=preferred)


def extract_jd_skills(jd_text):
    """Flat canonical skill list for a JD (legacy UI surface)."""
    if not jd_text or not jd_text.strip():
        return []
    return classify_jd_skills(jd_text).all()


def find_missing_skills(resume_skills, jd_skills):
    """Canonical JD skills absent from the resume (legacy UI surface)."""
    resume_set = set(resume_skills or [])
    missing = []
    for skill in jd_skills or []:
        if skill not in resume_set:
            missing.append(skill)
    return missing


@dataclass
class SkillGap:
    """Canonical-space comparison of candidate skills vs JD skills."""

    matched_skills: list
    missing_required_skills: list
    missing_preferred_skills: list


def analyze_skill_gap(candidate_skills, jd_skills):
    """Set comparison in canonical space.

    candidate_skills / jd_skills are canonical labels. Alias forms are already
    folded upstream, so "PostgreSQL" vs "Postgres" can only compare equal.
    """
    candidate_set = set(candidate_skills or [])

    matched = sorted(candidate_set & set(jd_skills.all()))
    missing_required = [skill for skill in jd_skills.required if skill not in candidate_set]
    missing_preferred = [skill for skill in jd_skills.preferred if skill not in candidate_set]

    return SkillGap(
        matched_skills=matched,
        missing_required_skills=missing_required,
        missing_preferred_skills=missing_preferred,
    )