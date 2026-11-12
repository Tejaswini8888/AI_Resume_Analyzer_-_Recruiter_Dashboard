"""TalentIQ · Recruiter Workspace (input-first).

Recruiters stage resume PDFs and paste a job description, then trigger the
analysis. All expensive ranking work happens downstream on the results page —
this page only manages the input surface until "Analyze Candidates" is clicked.
"""

from html import escape

import streamlit as st

from services.file_security_service import (
    MAX_UPLOAD_BYTES,
    calculate_file_hash_file,
    log_rejected_upload,
    validate_resume_file,
)
from ui.theme import apply_site_css
from ui.navigation import render_navbar, render_footer
from ui.components import page_header, section_header
from ui.cards import metric_card
from ui.auth_gate import require_authenticated

# ---------------------------------------------------------------------------
# Session keys for the input workspace
# ---------------------------------------------------------------------------
UPLOADER_KEY = "recruiter_resume_uploader"
FILE_STORE_KEY = "recruiter_file_store"
SNAPSHOT_KEY = "recruiter_widget_names"
CLEARED_FLAG_KEY = "recruiter_widget_cleared"
ANALYZING_FLAG_KEY = "recruiter_analyzing"
JD_WIDGET_KEY = "recruiter_jd_text"

MAX_FILE_BYTES = MAX_UPLOAD_BYTES


def _format_bytes(n: int) -> str:
    """Human-readable file size."""
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 ** 2):.1f} MB"


# ---------------------------------------------------------------------------
# Page shell
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="TalentIQ · Recruiter Workspace",
    page_icon=":material/group:",
    layout="wide",
)

apply_site_css()
AUTH_USER = require_authenticated()
render_navbar(active="Recruiter")

analyzing = st.session_state.pop(ANALYZING_FLAG_KEY, False)

# Center the header, labels, and captions so the whole page reads as one
# centered column (matching the Resume Analyzer page).
st.markdown(
    """
    <style>
      .tq-page-title, .tq-page-subtitle,
      [data-testid="stWidgetLabel"], .stTextArea label,
      [data-testid="stCaptionContainer"] { text-align: center; }
      [data-testid="stCaptionContainer"] p,
      [data-testid="stWidgetLabel"] > span { margin: 0 auto; }
      .tq-section-title, .tq-section-desc { text-align: center; }
      .tq-staged-head {
        display: flex;
        align-items: center;
        gap: 0.45rem;
        color: var(--tq-muted);
        font-size: 0.82rem;
        font-weight: 600;
        letter-spacing: 0.02em;
        text-transform: uppercase;
        margin: 0.4rem 0 0.35rem;
      }
      .tq-file-row {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        color: var(--tq-text, #F8FAFC);
        font-size: 0.9rem;
        padding: 0.12rem 0;
        min-width: 0;
      }
      .tq-file-row .material-symbols-outlined { color: var(--tq-secondary); }
      .tq-file-size {
        margin-left: auto;
        color: var(--tq-muted);
        font-size: 0.8rem;
        white-space: nowrap;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

_l, center, _r = st.columns([1, 2.4, 1])

with center:
    page_header(
        "Recruiter Workspace",
        "Upload resumes and a job description to identify and shortlist the strongest candidates.",
    )

    st.space("medium")

    # ------------------------------------------------------------------
    # Resume Upload card
    # ------------------------------------------------------------------
    with st.container(border=True):
        section_header(
            "Resume Upload",
            description="Add candidate resumes as PDF files.",
            icon="upload_file",
        )

        uploaded = st.file_uploader(
            "Upload Multiple Resumes (PDF)",
            type=["pdf"],
            accept_multiple_files=True,
            key=UPLOADER_KEY,
            label_visibility="collapsed",
            help="Multiple resumes supported · PDF format · max 10MB per file",
        )

        # Keep a session-backed file store in sync with the uploader widget so
        # files survive page navigation and can be removed individually.
        store = st.session_state.setdefault(FILE_STORE_KEY, {})
        prev_names = st.session_state.get(SNAPSHOT_KEY, [])
        skip_prune = st.session_state.pop(CLEARED_FLAG_KEY, False)

        if uploaded:
            cur_names = [f.name for f in uploaded]
            if not skip_prune and prev_names:
                for name in set(prev_names) - set(cur_names):
                    store.pop(name, None)
            for f in uploaded:
                if f.name not in store:
                    ok, reason = validate_resume_file(f, max_bytes=MAX_FILE_BYTES)
                    if not ok:
                        try:
                            resource_id = calculate_file_hash_file(f)[:12]
                        except Exception:
                            resource_id = None
                        log_rejected_upload(
                            AUTH_USER["id"], reason, resource_id=resource_id
                        )
                        st.warning(
                            f"`{escape(f.name)}` — {reason}", icon=":material/warning:"
                        )
                        continue
                    store[f.name] = f
            st.session_state[SNAPSHOT_KEY] = cur_names
        else:
            if not skip_prune and prev_names:
                for name in prev_names:
                    store.pop(name, None)
                st.session_state[SNAPSHOT_KEY] = []

        if store:
            st.markdown(
                f'<div class="tq-staged-head">'
                f'<span class="material-symbols-outlined">description</span>'
                f"Staged Resumes ({len(store)})</div>",
                unsafe_allow_html=True,
            )
            for i, (name, fobj) in enumerate(store.items()):
                col_name, col_remove = st.columns([4, 1])
                with col_name:
                    size = _format_bytes(getattr(fobj, "size", 0) or 0)
                    st.markdown(
                        f'<div class="tq-file-row">'
                        f'<span class="material-symbols-outlined">description</span>'
                        f"{escape(name)}<span class=\"tq-file-size\">{size}</span></div>",
                        unsafe_allow_html=True,
                    )
                with col_remove:
                    if st.button(
                        "Remove",
                        key=f"remove_file_{i}",
                        icon=":material/close:",
                        width="stretch",
                    ):
                        st.session_state[FILE_STORE_KEY].pop(name, None)
                        remaining = [f for f in uploaded if f.name != name] if uploaded else []
                        st.session_state[UPLOADER_KEY] = remaining or None
                        st.session_state[CLEARED_FLAG_KEY] = True
                        st.rerun()

        st.caption(f"{len(store)} resume(s) staged for analysis.")

    st.space("medium")

    # ------------------------------------------------------------------
    # Job Description card
    # ------------------------------------------------------------------
    with st.container(border=True):
        section_header(
            "Job Description",
            description="Paste the role you are hiring for.",
            icon="description",
        )

        if JD_WIDGET_KEY not in st.session_state and st.session_state.get("recruiter_jd"):
            st.session_state[JD_WIDGET_KEY] = st.session_state["recruiter_jd"]

        recruiter_jd = st.text_area(
            "Job Description",
            height=200,
            key=JD_WIDGET_KEY,
            label_visibility="collapsed",
            placeholder=(
                "Paste the job description here. TalentIQ will compare every "
                "candidate against it using ATS signals, skills, experience, "
                "and JD matching..."
            ),
        )

        jd_text = (recruiter_jd or "").strip()
        chars = len(jd_text)
        words = len(jd_text.split())
        if words:
            st.caption(f"{words} words · {chars} characters")
        else:
            st.caption("Start typing to see the word and character count.")

    st.space("medium")

    # ------------------------------------------------------------------
    # Candidate / JD Summary
    # ------------------------------------------------------------------
    section_header(
        "Candidate / JD Summary",
        description="Preview what is ready before you run the analysis.",
        icon="summarize",
    )

    ready_candidates = bool(store)
    ready_jd = bool(words)

    k1, k2, k3 = st.columns(3)
    with k1:
        metric_card(
            "Candidates",
            len(store),
            delta="staged for analysis" if ready_candidates else "add resumes to begin",
            icon="group",
        )
    with k2:
        metric_card(
            "Job Description",
            f"{words}" if ready_jd else "—",
            unit="words" if ready_jd else None,
            delta=f"{chars} characters" if ready_jd else "needs a job description",
            icon="description",
        )
    with k3:
        if ready_candidates and ready_jd:
            status = "Ready to Analyze"
            status_delta = "run the analysis to rank candidates"
            status_icon = "check_circle"
        else:
            status = "Needs Input"
            status_delta = "upload resumes and a job description"
            status_icon = "hourglass_empty"
        metric_card("Status", status, delta=status_delta, icon=status_icon)

    st.space("small")

    # ------------------------------------------------------------------
    # Analyze CTA
    # ------------------------------------------------------------------
    if st.button(
        "Analyze Candidates",
        type="primary",
        icon=":material/query_stats:",
        width="stretch",
        disabled=analyzing,
    ):
        if not ready_candidates:
            st.warning("Upload resumes first.", icon=":material/warning:")
            st.stop()

        if not ready_jd:
            st.warning("Paste the job description.", icon=":material/warning:")
            st.stop()

        st.session_state.uploaded_files = list(store.values())
        st.session_state.recruiter_jd = jd_text
        st.session_state[ANALYZING_FLAG_KEY] = True

        st.switch_page("pages/4_Recruiter_Results.py")

    st.space("small")
    st.caption(
        "TalentIQ evaluates resumes against the job description using ATS signals, "
        "skills, experience, and JD matching."
    )

    with st.container(horizontal=True, horizontal_alignment="center"):
        if st.button("Back to Dashboard", icon=":material/arrow_back:"):
            st.switch_page("app.py")

render_footer()