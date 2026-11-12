import streamlit as st

from services.file_security_service import (
    calculate_file_hash_file,
    log_rejected_upload,
    validate_resume_file,
)
from services.auth_service import current_user
from ui.theme import apply_site_css
from ui.navigation import render_navbar, render_footer
from ui.components import page_header

st.set_page_config(
    page_title="TalentIQ · Resume Analyzer",
    page_icon=":material/description:",
    layout="wide",
)

apply_site_css()
render_navbar(active="Resume Analyzer")

# Center the header, labels, and explanation so the whole page reads as one
# centered column (matching the Recruiter Dashboard layout structure).
st.markdown(
    """
    <style>
      .tq-page-title, .tq-page-subtitle,
      [data-testid="stWidgetLabel"], .stTextArea label,
      [data-testid="stCaptionContainer"] { text-align: center; }
      [data-testid="stCaptionContainer"] p,
      [data-testid="stWidgetLabel"] > span { margin: 0 auto; }
    </style>
    """,
    unsafe_allow_html=True,
)

left, center, right = st.columns([1, 2.4, 1])

with center:
    page_header(
        "Resume Analyzer",
        "Upload a resume to get an ATS score, AI insights, skill analysis, and interview questions.",
    )

    st.space("medium")

    uploaded_file = st.file_uploader(
        "Upload Resume (PDF)",
        type=["pdf"],
        label_visibility="collapsed",
        help="Single resume in PDF format",
    )

    st.space("medium")

    job_description = st.text_area(
        "Job Description (optional)",
        height=220,
        placeholder="Paste the job description to enable JD matching...",
    )

    st.space("medium")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Back to Dashboard", icon=":material/arrow_back:", width="stretch"):
            st.switch_page("app.py")
    with c2:
        if st.button(
            "Analyze Resume",
            type="primary",
            icon=":material/send:",
            width="stretch",
        ):
            if uploaded_file is None:
                st.warning("Upload a resume to continue.", icon=":material/warning:")
                st.stop()

            ok, reason = validate_resume_file(uploaded_file)
            if not ok:
                actor = (current_user() or {}).get("id")
                try:
                    resource_id = calculate_file_hash_file(uploaded_file)[:12]
                except Exception:
                    resource_id = None
                log_rejected_upload(actor, reason, resource_id=resource_id)
                st.warning(reason, icon=":material/warning:")
                st.stop()

            st.session_state.resume_file = uploaded_file
            st.session_state.job_description = job_description  # Optional

            st.switch_page("pages/2_Resume_Results.py")

    st.space("small")
    st.caption("Results are calculated instantly with ATS scoring, JD matching and Groq-powered AI insights.")

render_footer()