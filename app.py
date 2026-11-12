import logging

import streamlit as st

from core.db import init_db
from core.logging_config import log_startup_config
from ui.theme import apply_site_css
from ui.navigation import render_navbar, render_footer

# Centralized, secret-safe startup logging (never prints API keys / passwords).
log_startup_config()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Startup validation.
#
# Surfaces deployment misconfiguration (missing GROQ_API_KEY, unwritable
# database directory, absent skill dataset) at boot instead of letting it
# surface later as a confusing user-facing failure. It is logged rather than
# raised so a storage problem never blocks the UI, and it never makes a network
# call. Run `python -m core.startup_check` for the full report.
# ---------------------------------------------------------------------------
try:
    from core.startup_check import run_startup_checks

    _startup = run_startup_checks()
    for _check in _startup.failures:
        logger.error(
            "Startup check failed: %s (%s). %s",
            _check.name,
            _check.detail,
            _check.hint,
        )
    for _check in _startup.warnings:
        logger.warning(
            "Startup check warning: %s (%s). %s",
            _check.name,
            _check.detail,
            _check.hint,
        )
    if not _startup.ok:
        logger.error(
            "TalentIQ started with %d configuration failure(s). "
            "Run `python -m core.startup_check` for the full report.",
            len(_startup.failures),
        )
except Exception as exc:  # pragma: no cover - never block startup
    logger.warning(
        "Startup validation could not run (%s); continuing.", type(exc).__name__
    )

# Initialize the persistent SQLite layer safely on startup (idempotent —
# creates only missing tables, never drops existing data). A storage problem
# must not block the UI, so the failure is contained here — but it is LOGGED
# (type only, never the message, which could embed a path or DSN) instead of
# silently discarded, otherwise a broken database is invisible in production.
try:
    init_db()
except Exception as exc:
    logger.error(
        "Database initialization failed (%s). Persistence features are "
        "unavailable this session; the UI will still load.",
        type(exc).__name__,
    )

st.set_page_config(
    page_title="TalentIQ · AI Recruitment Intelligence",
    page_icon=":material/workspace_premium:",
    layout="wide",
)

apply_site_css()
render_navbar(active="Dashboard")

# ---------------------------------------------------------------------------
# Hero
# ---------------------------------------------------------------------------
st.markdown(
    """
    <div class="tq-hero">
      <span class="tq-eyebrow">AI Recruitment Intelligence Platform</span>
      <h1 class="tq-hero-title">
        AI-Powered<br>
        <span class="tq-gradient-text">Recruitment Intelligence</span>
      </h1>
      <p class="tq-hero-subtitle">
        Analyze resumes, match candidates to opportunities, uncover skill gaps,
        and make hiring decisions with AI-powered insights.
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)

cta_col, cta_col2, cta_col3 = st.columns([1, 2, 1])
with cta_col2:
    c1, c2 = st.columns(2)
    with c1:
        if st.button(
            "Analyze My Resume",
            type="primary",
            icon=":material/article:",
            width="stretch",
        ):
            st.switch_page("pages/1_Resume_Analysis.py")
    with c2:
        if st.button(
            "Open Recruiter Dashboard",
            icon=":material/group:",
            width="stretch",
        ):
            st.switch_page("pages/3_Recruiter_Dashboard.py")

st.markdown(
    """
    <div class="tq-hero-metrics">
      <span>ATS <b>Scoring</b></span>
      <span>&middot;</span>
      <span>Job Description <b>Matching</b></span>
      <span>&middot;</span>
      <span>Skill &amp; Gap <b>Intelligence</b></span>
      <span>&middot;</span>
      <span>AI <b>Interview Preparation</b></span>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Product showcase
# ---------------------------------------------------------------------------
st.markdown('<div class="tq-divider">Product</div>', unsafe_allow_html=True)

st.subheader("Built for candidates and recruiters")

col1, col2 = st.columns(2)

with col1:
    with st.container(border=True):
        st.markdown(":material/analytics: **Candidate Intelligence**")
        st.write(
            "Upload a resume and get an ATS score, skill extraction, "
            "job-description matching, missing-skill detection, and AI feedback "
            "with interview questions — all in one report."
        )
        st.markdown(
            "- ATS &amp; JD match scoring\n"
            "- Skill extraction and gap analysis\n"
            "- AI resume analysis and interview prep\n"
            "- Downloadable PDF report"
        )
        if st.button(
            "Analyze My Resume",
            key="showcase_analyze",
            type="primary",
            icon=":material/article:",
            width="stretch",
        ):
            st.switch_page("pages/1_Resume_Analysis.py")

with col2:
    with st.container(border=True):
        st.markdown(":material/groups: **Recruiter Dashboard**")
        st.write(
            "Compare multiple candidates at once. Rank resumes by ATS score "
            "and job-description match, filter the shortlist, and export the "
            "rankings as CSV."
        )
        st.markdown(
            "- Multi-resume comparison and ranking\n"
            "- Candidate shortlisting filters\n"
            "- Best-candidate recommendation\n"
            "- CSV export"
        )
        if st.button(
            "Open Recruiter Dashboard",
            key="showcase_recruiter",
            icon=":material/group:",
            width="stretch",
        ):
            st.switch_page("pages/3_Recruiter_Dashboard.py")

# ---------------------------------------------------------------------------
# How it works
# ---------------------------------------------------------------------------
st.markdown('<div class="tq-divider">How it works</div>', unsafe_allow_html=True)

h1, h2, h3 = st.columns(3)
with h1:
    with st.container(border=True):
        st.markdown("**01 · Upload**")
        st.write("Drop a resume PDF and, optionally, a job description.")
with h2:
    with st.container(border=True):
        st.markdown("**02 · Analyze**")
        st.write("AI extracts skills, scores ATS fit, and evaluates match.")
with h3:
    with st.container(border=True):
        st.markdown("**03 · Decide**")
        st.write("Review insights, shortlist candidates, and export reports.")

render_footer()