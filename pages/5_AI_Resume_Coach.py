"""AI Resume Coach — personalized, JD-grounded resume improvement advice.

Runs the AI coach once per (resume, JD) pair (session-state cache keyed by a
fingerprint), shows a loading state only while the request is in flight, and
falls back to deterministic, evidence-based recommendations on any failure so
the page is never blank. Only verified signals (ATS, JD match, matched/missing
skills, ATS issues) feed the fallback — no fabricated facts.
"""

import hashlib

import streamlit as st

from ui.theme import apply_site_css
from ui.navigation import render_navbar, render_footer
from ui.components import page_header, section_header, status_badge, divider, skill_chips
from ui.cards import score_card
from ui.loading import loading_state
from ui.empty_states import empty_state, error_state

from modules.sections import section_content
from modules.resume_profile import build_resume_profile
from modules.ats_score import analyze_ats
from modules.jd_matcher import analyze_job_match
from modules.resume_coach import generate_resume_coach


st.set_page_config(
    page_title="TalentIQ · AI Resume Coach",
    page_icon=":material/fact_check:",
    layout="wide",
)

apply_site_css()
render_navbar(active="AI Resume Coach")

COACH_CACHE_KEY = "resume_coach_result"

_PRIORITY_TONE = {
    "Critical": "danger",
    "Important": "warning",
    "Improvement": "info",
}


def _detect_role(jd_text: str) -> str:
    """Deterministic, best-effort target role from the first JD lines."""
    for line in (jd_text or "").splitlines():
        line = line.strip().strip("#")
        if not line:
            continue
        lowered = line.lower()
        for prefix in ("job title:", "role:", "position:", "title:", "about the role"):
            if lowered.startswith(prefix):
                return line.split(":", 1)[1].strip()
        if len(line) <= 6:
            continue
        if any(prefix in lowered for prefix in (" job", " engineer", " developer", " analyst", " scientist", " intern", " intern,")):
            return line
    return "Not detected"


def _coach_fingerprint(resume_text: str, jd_text: str) -> str:
    return hashlib.sha256(
        (resume_text + "\x00" + (jd_text or "")).encode("utf-8")
    ).hexdigest()[:24]


def _generate(resume_text: str, profile, ats_result, jd_match, jd_text: str):
    fingerprint = _coach_fingerprint(resume_text, jd_text)
    cached = st.session_state.get(COACH_CACHE_KEY)
    if cached and cached.get("fingerprint") == fingerprint:
        return cached["result"]

    result = None
    with loading_state("Generating personalized recommendations..."):
        result = generate_resume_coach(profile, resume_text, ats_result, jd_match, jd_text)

    if result is None:
        error_state(
            "Could not generate recommendations",
            "Something went wrong while preparing your coaching.",
        )
        st.stop()

    st.session_state[COACH_CACHE_KEY] = {"fingerprint": fingerprint, "result": result}
    return result


def _render_bullets(items):
    if not items:
        st.markdown("No specific items available.")
        return
    for item in items:
        st.markdown(f"- {item}")


# ---------------------------------------------------------------------------
# Guard: only reachable from the analysis flow
# ---------------------------------------------------------------------------
if "resume_file" not in st.session_state:
    empty_state(
        "No resume selected",
        "Start by uploading a resume in the Resume Analyzer, then open AI Resume Coach.",
    )
    st.stop()

uploaded_file = st.session_state.resume_file
job_description = st.session_state.get("job_description") or ""

# ---------------------------------------------------------------------------
# Fast, deterministic analysis (always available before any AI call)
# ---------------------------------------------------------------------------
try:
    profile = build_resume_profile(uploaded_file)
    resume_text = profile.raw_text
    ats_result = analyze_ats(profile)
except Exception:
    error_state(
        "Something went wrong while analyzing this resume",
        "The resume could not be parsed. Try a different PDF file.",
    )
    st.stop()

jd_match = None
if job_description.strip():
    jd_match = analyze_job_match(resume_text, profile.skills, job_description, profile=profile)

# ---------------------------------------------------------------------------
# AI coach (cached per resume + JD) with deterministic fallback
# ---------------------------------------------------------------------------
result = _generate(resume_text, profile, ats_result, jd_match, job_description)

page_header(
    "AI Resume Coach",
    "Personalized recommendations based on your resume and target role.",
)

if not result.ai_generated:
    status_badge("AI unavailable — showing evidence-based recommendations", "warning")
    st.caption(result.note or "Recommendations below are derived directly from your ATS analysis, JD match, and skill signals.")

if not job_description.strip():
    status_badge("No job description selected", "info")
    st.caption("Recommendations are based on general resume quality. Add a Job Description in the Resume Analyzer for JD-specific coaching.")

divider()

# ---------------------------------------------------------------------------
# Resume & Job Context
# ---------------------------------------------------------------------------
section_header("Resume & Job Context", icon="description")
c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
with c1:
    st.markdown("**Resume**")
    st.caption(uploaded_file.name)
with c2:
    st.markdown("**Target Role**")
    st.caption(_detect_role(job_description))
with c3:
    m = ats_result.overall_score
    score_card("ATS Compatibility", m, max_value=100, icon="target", caption="Resume quality")
with c4:
    m = jd_match.overall_match_score if jd_match is not None else None
    score_card(
        "JD Match",
        m if m is not None else 0,
        max_value=100,
        icon="iframe",
        caption="Job description fit" if jd_match is not None else "No JD selected",
    )

if result.overall_assessment:
    divider()
    st.markdown("**Overall assessment**")
    st.write(result.overall_assessment)

# ---------------------------------------------------------------------------
# Top Improvements
# ---------------------------------------------------------------------------
divider("Top Improvements")
improvements = result.priority_improvements
if not improvements:
    st.markdown("No specific improvements detected.")
else:
    for item in improvements:
        tone = _PRIORITY_TONE.get((item.priority or "").capitalize(), "info")
        st.markdown(f"**{item.priority or 'Improvement'}** — {item.issue}")
        st.write(item.recommendation)
        st.caption(item.reason)
        st.markdown("")

# ---------------------------------------------------------------------------
# Resume Summary
# ---------------------------------------------------------------------------
divider("Resume Summary")
summary_result = result.summary_improvement or {}
current_text = section_content(resume_text, "summary").strip()
c1, c2 = st.columns(2)
with c1:
    st.markdown("**Current**")
    st.write(current_text if current_text else "_(No summary detected)_")
with c2:
    st.markdown("**Suggested Rewrite**")
    suggested = (summary_result.get("suggested_rewrite") or "").strip()
    st.write(suggested if suggested else "_(Not available with evidence-based mode.)_")
if (summary_result.get("current_issue") or "").strip():
    st.caption(f"Why: {summary_result['current_issue']}")

# ---------------------------------------------------------------------------
# Skill Gap Guidance
# ---------------------------------------------------------------------------
divider("Skill Gap Guidance")
c1, c2 = st.columns(2)
with c1:
    st.markdown("**Missing Required Skills**")
    missing_req = list(getattr(jd_match, "missing_required_skills", None) or [])
    if missing_req:
        skill_chips(missing_req, tone="danger")
    else:
        st.markdown("_None — you cover all required skills._")
with c2:
    st.markdown("**Missing Preferred Skills**")
    missing_pref = list(getattr(jd_match, "missing_preferred_skills", None) or [])
    if missing_pref:
        skill_chips(missing_pref, tone="warning")
    else:
        st.markdown("_None — you cover all preferred skills._")

if result.skill_gap_guidance:
    divider()
    _render_bullets(result.skill_gap_guidance)

# ---------------------------------------------------------------------------
# Experience & Projects
# ---------------------------------------------------------------------------
divider("Experience & Projects")
_render_bullets(result.experience_improvements)

# ---------------------------------------------------------------------------
# Keyword & ATS Recommendations
# ---------------------------------------------------------------------------
divider("Keyword & ATS Recommendations")
_render_bullets(result.keyword_recommendations)
if result.formatting_recommendations:
    divider()
    _render_bullets(result.formatting_recommendations)

# ---------------------------------------------------------------------------
# Interview Focus Areas
# ---------------------------------------------------------------------------
divider("Interview Focus Areas")
_render_bullets(result.interview_focus_areas)

# ---------------------------------------------------------------------------
# Retry when AI was unavailable
# ---------------------------------------------------------------------------
if not result.ai_generated:
    if st.button("Retry AI recommendations"):
        st.session_state.pop(COACH_CACHE_KEY, None)
        st.rerun()

render_footer()