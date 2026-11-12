"""JD match scoring for TalentIQ.

Three independent, documented signals, combined into one ``JDMatchResult``:

A. ``skill_match_score``  - canonical skill coverage of the JD (required
   weighted 0.7, preferred weighted 0.3; 0-100). Exact-set membership only -
   semantic similarity NEVER clears a missing mandatory skill.
B. ``semantic_match_score`` - offline local sentence-embedding cosine between
   the meaningful resume representation and the meaningful JD representation
   (0-100). Captures contextual relevance when wording differs. Isolated in
   ``services.semantic_matcher``; 0 and ``semantic_available=False`` when the
   embedding model is unavailable.
C. ``text_similarity_score`` / legacy ``similarity_score`` - TF-IDF cosine on
   resume text vs JD text (0-100). Preserved bit-for-bit as the fallback and as
   the value the existing "JD Match" display surface continues to use.

The combined ``overall_match_score`` layering (documented weights, not tuned to
any candidate pool):

   semantic available:  0.50*skill + 0.30*semantic + 0.20*tfidf
   semantic fallback :  0.60*skill + 0.40*tfidf          (deterministic only)

``calculate_jd_match`` remains the untouched TF-IDF compatibility wrapper, kept
in this module so the see original import surface keeps working.
"""

from dataclasses import dataclass, field

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from services.skill_matcher import analyze_skill_match
from services.semantic_matcher import (
    candidate_representation,
    jd_representation,
    semantic_available,
    semantic_similarity,
)
from modules.resume_profile import build_profile_from_text

# Combined JD match layering: skill is the strongest/most reliable signal,
# semantic wording second, TF-IDF keyword overlap is the supporting signal and
# the only one kept when embeddings are unavailable.
_COMBINED_SKILL_WEIGHT = 0.50
_COMBINED_SEMANTIC_WEIGHT = 0.30
_COMBINED_TFIDF_WEIGHT = 0.20
_FALLBACK_SKILL_WEIGHT = 0.60
_FALLBACK_TFIDF_WEIGHT = 0.40


@dataclass
class JDMatchResult:
    """Structured outcome of comparing a candidate resume against a JD."""

    similarity_score: float
    text_similarity_score: float = 0.0
    skill_match_score: float = 0.0
    semantic_match_score: float = 0.0
    overall_match_score: float = 0.0
    matched_skills: list = field(default_factory=list)
    matched_required_skills: list = field(default_factory=list)
    matched_preferred_skills: list = field(default_factory=list)
    missing_required_skills: list = field(default_factory=list)
    missing_preferred_skills: list = field(default_factory=list)
    candidate_skills: list = field(default_factory=list)
    jd_skills: list = field(default_factory=list)
    semantic_available: bool = True

    @property
    def missing_skills(self):
        """All missing canonical skills (required + preferred) - UI surface."""
        return sorted(set(self.missing_required_skills) | set(self.missing_preferred_skills))


def calculate_jd_match(resume_text, jd_text):
    """TF-IDF cosine similarity between resume and JD text (0-100)."""

    documents = [
        resume_text,
        jd_text
    ]

    tfidf = TfidfVectorizer()

    matrix = tfidf.fit_transform(documents)

    similarity = cosine_similarity(
        matrix[0:1],
        matrix[1:2]
    )[0][0]

    score = round(
        similarity * 100,
        2
    )

    return score


def _combined_match(skill, semantic, tfidf, sem_ok):
    """Documented combination of the three JD signals (0-100)."""
    if sem_ok:
        return round(
            _COMBINED_SKILL_WEIGHT * skill
            + _COMBINED_SEMANTIC_WEIGHT * semantic
            + _COMBINED_TFIDF_WEIGHT * tfidf,
            2,
        )
    return round(_FALLBACK_SKILL_WEIGHT * skill + _FALLBACK_TFIDF_WEIGHT * tfidf, 2)


def analyze_job_match(resume_text, candidate_skills, jd_text, profile=None):
    """Full layered JD analysis for one candidate.

    Signals: canonical skill coverage + semantic (offline embeddings) + TF-IDF.
    Empty JD   -> zeros and empty lists (callers stay safe).
    Embeddings down -> semantic signal 0 with ``semantic_available=False`` and
    the deterministic skill + TF-IDF fallback drives ``overall_match_score``.
    """
    candidate_skills = sorted(set(candidate_skills or []))
    blank_jd = not jd_text or not jd_text.strip()

    if blank_jd:
        return JDMatchResult(
            similarity_score=0.0,
            text_similarity_score=0.0,
            skill_match_score=0.0,
            semantic_match_score=0.0,
            overall_match_score=0.0,
            matched_skills=[],
            matched_required_skills=[],
            matched_preferred_skills=[],
            missing_required_skills=[],
            missing_preferred_skills=[],
            candidate_skills=candidate_skills,
            jd_skills=[],
            semantic_available=semantic_available(),
        )

    similarity = calculate_jd_match(resume_text or "", jd_text)
    skill_match = analyze_skill_match(candidate_skills, jd_text)

    candidate_profile = profile if profile is not None else build_profile_from_text(resume_text or "")
    sem_value = 0.0
    sem_ok = semantic_available()
    if sem_ok:
        candidate_rep = candidate_representation(candidate_profile)
        jd_rep = jd_representation(jd_text)
        score = semantic_similarity(candidate_rep, jd_rep)
        sem_value = score if score is not None else 0.0
        sem_ok = score is not None

    return JDMatchResult(
        similarity_score=similarity,
        text_similarity_score=similarity,
        skill_match_score=skill_match.score,
        semantic_match_score=sem_value,
        overall_match_score=_combined_match(
            skill_match.score, sem_value, similarity, sem_ok
        ),
        matched_skills=skill_match.matched_skills,
        matched_required_skills=skill_match.matched_required_skills,
        matched_preferred_skills=skill_match.matched_preferred_skills,
        missing_required_skills=skill_match.missing_required_skills,
        missing_preferred_skills=skill_match.missing_preferred_skills,
        candidate_skills=candidate_skills,
        jd_skills=skill_match.jd_skills,
        semantic_available=sem_ok,
    )