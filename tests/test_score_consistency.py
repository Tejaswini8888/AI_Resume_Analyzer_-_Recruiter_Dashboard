"""Score-consistency contract (Phase 4 · Step 6 audit).

The JD match layer produces several signals. ``overall_match_score`` is the one
that is persisted, ranked, exported and shown on every recruiter surface, so it
is the only number a user should ever be shown as "the match score".

``similarity_score`` is the raw TF-IDF cosine COMPONENT and is not comparable to
the combined score. Showing it as the headline match score made a candidate see
one number on the results page while the recruiter later saw a different number
for the very same resume + job description.

These tests are static (they read page source) because Streamlit pages execute
top-level code on import and cannot be imported inside a test session.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Pages and modules that surface a JD match score to a user.
_SURFACES = (
    "pages/2_Resume_Results.py",
    "pages/4_Recruiter_Results.py",
    "pages/5_AI_Resume_Coach.py",
    "modules/resume_ranker.py",
    "modules/resume_coach.py",
)

# The single score that is defined as "the" JD match score.
CANONICAL = "overall_match_score"


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative", _SURFACES)
def test_no_surface_reads_a_similarity_component_as_the_match_score(relative):
    """No surface may take its headline match score from a TF-IDF component.

    ``semantic_match_score`` is excluded: it is a labelled breakdown metric in
    its own right, and is also persisted separately for analytics.
    """
    source = _read(relative)
    for component in ("similarity_score", "text_similarity_score"):
        assert f"jd_match.{component}" not in source, (
            f"{relative} uses the JD component jd_match.{component} as a "
            "match score; only overall_match_score is the combined score"
        )


def test_results_page_headline_score_is_the_persisted_score():
    """The page-2 headline number must be the combined score it also persists."""
    source = _read("pages/2_Resume_Results.py")
    assignments = re.findall(
        r"^\s*match_score\s*=\s*(.+?)\s*$", source, flags=re.MULTILINE
    )
    assert assignments, "pages/2_Resume_Results.py no longer computes match_score"

    # The initialiser (None) and any other value are fine; what matters is that
    # no assignment pulls from a similarity component.
    for expression in assignments:
        assert ".similarity_score" not in expression, (
            "pages/2_Resume_Results.py must display jd_match."
            f"{CANONICAL}, not a component score, or candidates and recruiters "
            f"see different numbers for the same analysis (got {expression!r})"
        )
    assert any(f"jd_match.{CANONICAL}" in expression for expression in assignments), (
        f"pages/2_Resume_Results.py must display jd_match.{CANONICAL}"
    )


def test_persisted_and_ranked_scores_use_the_canonical_field():
    """The score written to the database and used for ranking is the same one."""
    analysis = _read("services/analysis_service.py")
    assert f'getattr(jd_match, "{CANONICAL}", None)' in analysis

    ranker = _read("modules/resume_ranker.py")
    assert f"jd_match.{CANONICAL}" in ranker

    coach = _read("modules/resume_coach.py")
    assert f"jd_match.{CANONICAL}" in coach

    # The recruiter results page must consume the ranker's persisted
    # "JD Match" column rather than recomputing a score of its own.
    recruiter_results = _read("pages/4_Recruiter_Results.py")
    assert '"JD Match"' in recruiter_results
    assert "analyze_job_match" not in recruiter_results


def test_matcher_still_exposes_components_for_breakdowns():
    """Components remain available (documented layering is unchanged)."""
    source = _read("modules/jd_matcher.py")
    for component in ("similarity_score", "text_similarity_score", CANONICAL):
        assert component in source
