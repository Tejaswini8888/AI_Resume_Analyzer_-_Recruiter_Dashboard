import hashlib

import streamlit as st

from ui.theme import apply_site_css
from ui.navigation import render_navbar, render_footer
from ui.components import (
    page_header,
    section_header,
    status_badge,
    progress_bar,
    divider,
    skill_chips,
)
from ui.cards import metric_card, score_card, insight_card
from ui.charts import ats_donut
from ui.loading import loading_state
from ui.empty_states import empty_state, error_state, success_state

from modules.ai_analysis import generate_resume_summary, analyze_resume
from modules.resume_profile import build_resume_profile
from modules.ats_score import analyze_ats
from modules.jd_matcher import analyze_job_match
from modules.interview_generator import generate_interview_questions
from modules.pdf_report import generate_pdf_report

from services.candidate_service import (
    get_candidate,
    resume_hash_file,
    create_candidate,
    find_candidate_by_resume_hash,
)
from services.recruitment_service import save_job_description, mark_analyzed
from services.analysis_service import save_analysis_from_results
from services.activity_service import get_candidate_activity
from services.resume_version_service import create_or_get_resume_version
from services.auth_service import current_user
from services.file_security_service import (
    log_file_security_event,
    log_report_failure,
    sanitize_filename,
)
from services.audit_service import PDF_INVALID, RESOURCE_FILE

from ui.timeline import render_activity_timeline


st.set_page_config(
    page_title="TalentIQ · Resume Results",
    page_icon=":material/insights:",
    layout="wide",
)

apply_site_css()
render_navbar(active=None)

# ---------------------------------------------------------------------------
# Guard: this page is only reachable from the analysis flow
# ---------------------------------------------------------------------------
if "resume_file" not in st.session_state:
    empty_state(
        "No resume selected",
        "Start by uploading a resume in the Resume Analyzer.",
    )
    st.stop()

uploaded_file = st.session_state.resume_file
job_description = st.session_state.get("job_description", "")

# Hostile upload names (path separators, control characters) are reduced to a
# harmless label for display and persisted metadata — never used as a path.
safe_file_name = sanitize_filename(uploaded_file.name or "resume.pdf")

# ---------------------------------------------------------------------------
# AI analysis results are cached per-resume so Streamlit reruns (tab switches,
# widget changes) reuse the completed analysis instead of re-showing the
# loading banner or re-hitting the model. The loading banner only appears
# while a request is actively in flight.
# ---------------------------------------------------------------------------
AI_RESULTS_KEY = "resume_ai_results"


def _resume_fingerprint(resume_text: str) -> str:
    return hashlib.sha256(resume_text.encode("utf-8")).hexdigest()[:24]


def _analyze_with_ai(resume_text: str) -> dict:
    """Run (or reuse) the AI analysis for this resume."""
    fingerprint = _resume_fingerprint(resume_text)
    cached = st.session_state.get(AI_RESULTS_KEY)
    if cached and cached.get("fingerprint") == fingerprint:
        return cached

    results = {}
    with loading_state("Analyzing resume with AI..."):
        results["summary"] = generate_resume_summary(resume_text)
        results["ai_analysis"] = analyze_resume(resume_text)
        results["questions"] = generate_interview_questions(resume_text)

    st.session_state[AI_RESULTS_KEY] = {"fingerprint": fingerprint, **results}
    return st.session_state[AI_RESULTS_KEY]

# ---------------------------------------------------------------------------
# Fast, deterministic analysis (ATS, skills, JD match) — always available
# ---------------------------------------------------------------------------
try:
    profile = build_resume_profile(uploaded_file)
    resume_text = profile.raw_text
    candidate = profile.to_details_dict()
    skills = profile.skills
    ats_result = analyze_ats(profile)
    ats_score = ats_result.overall_score
    breakdown = ats_result.section_scores
except Exception:
    log_file_security_event(
        (current_user() or {}).get("id"),
        action=PDF_INVALID,
        resource_type=RESOURCE_FILE,
        resource_id=None,
        description="Resume processing failed.",
    )
    error_state(
        "Something went wrong while analyzing this resume",
        "The resume could not be parsed. Try a different PDF file.",
    )
    st.stop()

jd_match = None
match_score = None
missing_skills = []
if job_description.strip():
    jd_match = analyze_job_match(resume_text, skills, job_description, profile=profile)
    # Display the SAME layered score that is persisted below (and that every
    # recruiter surface ranks by). ``similarity_score`` is the raw TF-IDF
    # component only, so showing it here made a candidate see a different
    # number than the recruiter later saw for the same resume + JD.
    # The TF-IDF component is still available as ``text_similarity_score``.
    match_score = jd_match.overall_match_score
    missing_skills = jd_match.missing_skills

# ---------------------------------------------------------------------------
# Persistent candidate + analysis (best-effort; results never depend on it).
# One upload+JD pair is persisted once per session, so reruns do not spam the
# database with identical analysis snapshots.
# ---------------------------------------------------------------------------
PERSIST_KEY = "last_persisted_analysis"


def _clean_details(value):
    return value if value and value != "Not Found" else None


def _owner_user_id() -> int | None:
    """Ownership recorded only when a signed-in user runs self-analysis;
    anonymous self-analysis stays in the legacy/shared (NULL-owner) pool."""
    user = current_user()
    return user["id"] if user is not None else None


def _persist_analysis() -> None:
    try:
        owner_id = _owner_user_id()
        current_user_or_none = current_user()
        jd_row = (
            save_job_description(job_description, created_by_user_id=owner_id)
            if job_description.strip()
            else None
        )
        resume_hash = resume_hash_file(uploaded_file)
        fingerprint = resume_hash + ("|" + jd_row.content_hash if jd_row else "|no-jd")
        if st.session_state.get(PERSIST_KEY) == fingerprint:
            return

        candidate_row = create_candidate(
            resume_hash=resume_hash,
            name=candidate.get("name") or None,
            email=_clean_details(candidate.get("email")),
            phone=_clean_details(candidate.get("phone")),
            location=_clean_details(candidate.get("location")),
            resume_filename=safe_file_name,
            created_by_user_id=owner_id,
        )
        # Remember the persisted row so the activity timeline can bind to the
        # exact record (not a same-hash lookalike owned by someone else).
        st.session_state["_last_persisted_candidate_id"] = candidate_row.id
        mark_analyzed(candidate_row.id, user=current_user_or_none)

        resume_version = create_or_get_resume_version(
            candidate_id=candidate_row.id,
            file_hash=resume_hash,
            filename=safe_file_name,
            user=current_user_or_none,
        )

        jd_score = jd_match.overall_match_score if jd_match else 0.0
        save_analysis_from_results(
            candidate_id=candidate_row.id,
            jd_id=(jd_row.id if jd_row else None),
            resume_version_id=resume_version.id,
            user=current_user_or_none,
            ats_result=ats_result,
            jd_match=jd_match,
            final_score=round(ats_score * 0.4 + jd_score * 0.6, 2),
        )
        st.session_state[PERSIST_KEY] = fingerprint
    except Exception:
        st.caption(
            "Note: this analysis could not be saved to the local database. "
            "Your results below are still valid."
        )


_persist_analysis()

name_display = candidate.get("name") or safe_file_name

page_header(
    "Resume Analysis Results",
    f"{safe_file_name}",
)
st.caption(f"Candidate: {name_display}")

# ---------------------------------------------------------------------------
# Key metrics
# ---------------------------------------------------------------------------
m1, m2, m3, m4 = st.columns(4)
with m1:
    score_card("ATS Score", ats_score, max_value=100, icon="target", caption="Overall resume quality")
with m2:
    metric_card(
        "JD Match",
        f"{match_score:.1f}" if match_score is not None else "N/A",
        icon="link",
        unit="%",
        delta="Not provided" if match_score is None else None,
    )
with m3:
    metric_card("Detected Skills", len(skills), icon="checklist")
with m4:
    metric_card(
        "Missing Skills",
        len(missing_skills),
        icon="error",
        delta="vs job description" if match_score is not None else "No JD compared",
    )

# ---------------------------------------------------------------------------
# AI intelligence (slow — LLM)
# ---------------------------------------------------------------------------
summary = ai_analysis = questions = None
try:
    ai_results = _analyze_with_ai(resume_text)
    summary = ai_results["summary"]
    ai_analysis = ai_results["ai_analysis"]
    questions = ai_results["questions"]
except Exception:
    error_state(
        "The AI analysis could not be generated",
        "ATS scoring and skill analysis completed, but the AI service "
        "temporarily failed. Try again in a moment.",
    )
    st.stop()

success_state(
    "Analysis completed successfully.", "ATS score, skill analysis, and AI insights are ready below.",
)
st.toast("Analysis completed successfully.")

# ---------------------------------------------------------------------------
# Result tabs
# ---------------------------------------------------------------------------
overview_tab, analytics_tab, ai_tab, interview_tab, report_tab = st.tabs(
    [
        "Overview",
        "Analytics",
        "AI Insights",
        "Interview",
        "Report",
    ]
)

# =========================
# Overview
# =========================
with overview_tab:
    section_header("AI Professional Summary")
    with st.container(border=True):
        st.markdown(summary)

    divider()

    left_col, right_col = st.columns(2)

    with left_col:
        section_header("Candidate Information", icon="person")
        with st.container(border=True):
            st.write("### Personal")
            for label, value in [
                ("Name", candidate.get("name")),
                ("Email", candidate.get("email")),
                ("Phone", candidate.get("phone")),
            ]:
                st.markdown(f"**{label}:** {value or 'Not provided'}")

            st.markdown("---")

            st.write("### Professional")
            for label, value in [
                ("LinkedIn", candidate.get("linkedin")),
                ("GitHub", candidate.get("github")),
                ("Education", candidate.get("education")),
            ]:
                st.markdown(f"**{label}:** {value or 'Not provided'}")

    with right_col:
        section_header("Technical Skills", icon="construction")
        with st.container(border=True):
            if skills:
                st.caption(f"{len(skills)} skills detected from the resume")
                st.space("small")
                skill_chips(skills)
            else:
                st.write("No skills detected.")

    divider()

    section_header("ATS Score Breakdown", icon="pie_chart")
    st.plotly_chart(
        ats_donut(breakdown, height=330, show_legend=False),
        width="stretch",
        theme=None,
        key="ats_donut_overview",
    )

    divider()

    section_header("Resume vs Job Description", icon="checklist")
    if match_score is not None:
        match_col, gap_col = st.columns(2)
        with match_col:
            st.write("#### JD Match")
            st.metric("Match Score", f"{match_score:.1f}%", border=True)
            progress_bar(match_score, max_value=100, tone="primary")
        with gap_col:
            st.write("#### Missing Skills")
            if missing_skills:
                skill_chips(missing_skills, tone="warning")
            else:
                status_badge("No missing skills", tone="success")
    else:
        st.info("Upload a job description to calculate JD Match.", icon=":material/info:")

    divider()

    section_header("AI Resume Analysis", icon="auto_awesome")
    with st.container(border=True):
        st.markdown(ai_analysis)

    section_header("Interview Preparation", icon="quiz")
    with st.container(border=True):
        st.markdown(questions)

# =========================
# Analytics
# =========================
with analytics_tab:
    section_header("Assessment", icon="speed")

    verdict = "Excellent Resume" if ats_score >= 85 else ("Good Resume" if ats_score >= 70 else "Needs Improvement")
    verdict_tone = "success" if ats_score >= 85 else ("info" if ats_score >= 70 else "warning")
    status_badge(verdict, tone=verdict_tone)
    st.space("small")

    c1, c2 = st.columns(2)
    with c1:
        section_header("ATS Score Breakdown", icon="pie_chart")
        st.plotly_chart(
            ats_donut(breakdown, height=300, show_legend=True),
            width="stretch",
            theme=None,
            key="ats_donut_analytics",
        )
    with c2:
        section_header("Score Components", icon="analytics")
        for category, score in breakdown.items():
            st.markdown(f"**{category}**")
            progress_bar(score, max_value=40, tone="primary", thin=True)
        st.space("small")
        st.markdown(f"**Total ATS Score:** {ats_score} / 100")

    if match_score is not None:
        st.space("small")
        section_header("Job Description Match", icon="link")
        st.progress(match_score / 100)
        st.metric("Match Score", f"{match_score:.1f}%", border=True)

        if missing_skills:
            st.space("small")
            section_header("Missing Skills", icon="error")
            for skill in missing_skills:
                st.markdown(f"- {skill}")

# =========================
# AI Insights
# =========================
with ai_tab:
    section_header("AI Resume Analysis", icon="auto_awesome")
    with st.container(border=True):
        st.markdown(ai_analysis)

    insight_card(
        "What to focus on",
        "Use the improvement suggestions above to strengthen weak areas, "
        "then re-analyze after editing your resume.",
        icon="lightbulb",
    )

# =========================
# Interview
# =========================
with interview_tab:
    section_header("Personalized Interview Questions", icon="quiz")
    with st.container(border=True):
        st.markdown(questions)

    st.caption("Questions are generated from the skills and projects found in this resume.")

# =========================
# Report
# =========================
with report_tab:
    section_header("Export Report", icon="download")
    st.write("Download a formatted PDF report with the scores, skills, and AI analysis.")

    try:
        report_bytes = generate_pdf_report(
            candidate,
            summary,
            ats_score,
            skills,
            match_score,
            missing_skills,
            ai_analysis,
            questions,
        )
    except Exception:
        log_report_failure(_owner_user_id())
        error_state(
            "Could not create report",
            "The PDF report could not be generated. Please try again.",
        )
        st.stop()

    st.download_button(
        "Download Resume Analysis Report",
        data=report_bytes,
        file_name="TalentIQ_Resume_Analysis_Report.pdf",
        mime="application/pdf",
        icon=":material/description:",
        width="stretch",
        key="report_download",
    )

    st.caption("The report includes the ATS breakdown, JD match, detected and missing skills, AI analysis, and interview questions.")

# =========================
# Activity Timeline (compact, best-effort)
# =========================
st.space("small")
section_header(
    "Activity Timeline",
    description="Recorded history for this candidate. Latest event first.",
    icon="history",
)
try:
    _tl_user = current_user()
    _tl_candidate_id = st.session_state.get("_last_persisted_candidate_id")
    _tl_candidate = (
        get_candidate(_tl_candidate_id, user=_tl_user)
        if _tl_candidate_id is not None
        else None
    )
    if _tl_candidate is None:
        _tl_candidate = find_candidate_by_resume_hash(
            resume_hash_file(uploaded_file), user=_tl_user
        )
except Exception:
    _tl_candidate = None
if _tl_candidate is None:
    st.caption("No recorded activity for this candidate yet.")
else:
    try:
        _tl_activities = get_candidate_activity(
            _tl_candidate.id, user=current_user()
        )
    except Exception:
        _tl_activities = []
    if _tl_activities:
        render_activity_timeline(_tl_activities)
    else:
        st.caption("No recorded activity for this candidate yet.")

with st.container(horizontal=True, horizontal_alignment="right"):
    if st.button(
        "Analyze Another Resume",
        icon=":material/refresh:",
    ):
        # Clear only this analysis flow's scratch state. A full
        # ``session_state.clear()`` would also drop the auth marker and silently
        # sign out a signed-in recruiter mid-flow.
        for key in (
            "resume_file",
            "job_description",
            "resume_ai_results",
            PERSIST_KEY,
            "resume_analysis_result",
        ):
            if key in st.session_state:
                del st.session_state[key]
        st.switch_page("pages/1_Resume_Analysis.py")

render_footer()