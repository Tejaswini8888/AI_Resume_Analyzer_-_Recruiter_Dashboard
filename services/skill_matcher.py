"""Explicit, deterministic skill layer of JD matching for TalentIQ.

This service answers ONE question - "which exact canonical JD skills does the
candidate hold?" - using the centralized skill extraction from Phase 2 Step 2.
It runs entirely in canonical skill space so alias forms ("Postgres" / "PostgreSQL")
can never create a false gap, and it splits the JD into required vs preferred.

The semantic layer never feeds into these lists: a semantically-related but
different skill (JD "FastAPI" vs resume "Flask") is deliberately reported as
missing. Skill presence is decided by exact canonical set membership only.
"""

from dataclasses import dataclass

from modules.missing_skills import analyze_skill_gap, classify_jd_skills

# Weights used inside the skill coverage score (internal, explicit, documented).
REQUIRED_WEIGHT = 0.7
PREFERRED_WEIGHT = 0.3


@dataclass
class SkillMatchResult:
    """Canonical-skill comparison of one candidate vs one JD."""

    matched_required_skills: list
    missing_required_skills: list
    matched_preferred_skills: list
    missing_preferred_skills: list
    matched_skills: list
    jd_skills: list
    score: float


def _coverage_score(gap, jd_skills):
    """Weighted 0-100 coverage: required 0.7 / preferred 0.3, not double counting."""
    required_total = len(jd_skills.required)
    preferred_total = len(jd_skills.preferred)

    required_hit = required_total - len(gap.missing_required_skills)
    preferred_hit = preferred_total - len(gap.missing_preferred_skills)

    weighted_sum = 0.0
    weight_total = 0.0
    if required_total:
        weighted_sum += REQUIRED_WEIGHT * (required_hit / required_total)
        weight_total += REQUIRED_WEIGHT
    if preferred_total:
        weighted_sum += PREFERRED_WEIGHT * (preferred_hit / preferred_total)
        weight_total += PREFERRED_WEIGHT

    if not weight_total:
        return 0.0
    return round(100 * weighted_sum / weight_total, 2)


def analyze_skill_match(candidate_skills, jd_text):
    """Split JD skills (required/preferred) and score canonical coverage.

    Empty JD -> empty skill lists and score 0. Unknown JD skills are simply not
    part of the dataset and are ignored.
    """
    candidate_skills = sorted(set(candidate_skills or []))
    if not jd_text or not jd_text.strip():
        return SkillMatchResult(
            matched_required_skills=[],
            missing_required_skills=[],
            matched_preferred_skills=[],
            missing_preferred_skills=[],
            matched_skills=[],
            jd_skills=[],
            score=0.0,
        )

    jd_skills = classify_jd_skills(jd_text)
    gap = analyze_skill_gap(candidate_skills, jd_skills)

    required_tuple = tuple(jd_skills.required)
    preferred_tuple = tuple(jd_skills.preferred)
    matched_required = [
        skill for skill in required_tuple if skill in set(gap.matched_skills)
    ]
    matched_preferred = [
        skill for skill in preferred_tuple if skill in set(gap.matched_skills)
    ]

    return SkillMatchResult(
        matched_required_skills=matched_required,
        missing_required_skills=gap.missing_required_skills,
        matched_preferred_skills=matched_preferred,
        missing_preferred_skills=gap.missing_preferred_skills,
        matched_skills=gap.matched_skills,
        jd_skills=jd_skills.all(),
        score=_coverage_score(gap, jd_skills),
    )