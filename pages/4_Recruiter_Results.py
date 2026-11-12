from html import escape

import pandas as pd

import streamlit as st

from ui.theme import apply_site_css
from ui.navigation import render_navbar, render_footer
from ui.components import page_header, section_header
from ui.cards import metric_card, score_card
from ui.charts import score_distribution
from ui.loading import loading_state
from ui.empty_states import empty_state, error_state
from ui.timeline import render_activity_timeline
from ui.auth_gate import require_authenticated

from modules.resume_ranker import rank_resumes

from core.models import RECRUITMENT_STATUSES
from services.candidate_service import resume_hash_file, create_candidate, find_candidate_by_resume_hash
from services.recruitment_service import (
    save_job_description,
    mark_analyzed,
    shortlist_candidate,
    remove_from_shortlist,
    update_candidate_status,
    get_pipeline_counts,
)
from services.analysis_service import save_analysis_from_results
from services.activity_service import get_candidate_activity
from services.resume_version_service import create_or_get_resume_version
from services.candidate_search_service import score_eligible_min
from services.file_security_service import csv_neutralize, sanitize_filename

# ---------------------------------------------------------------------------
# Status thresholds (transparent, display-only — the Final Score itself is
# computed by the unchanged ranking engine below).
#
# SCORE_ELIGIBLE_MIN drives the "Score-Eligible Candidates" KPI only and is
# sourced from centralized configuration (RECRUITER_SCORE_THRESHOLD), read at
# render time via ``score_eligible_min()``. Manual shortlisting (SHORTLIST_KEY)
# is recruiter-driven and independent: reaching the threshold NEVER
# automatically shortlists a candidate.
# ---------------------------------------------------------------------------
STATUS_STRONG_MIN = 80
STATUS_POTENTIAL_MIN = 60
SHORTLIST_KEY = "recruiter_shortlist"


def score_status(final_score: float) -> str:
    if final_score >= STATUS_STRONG_MIN:
        return "Strong Match"
    if final_score >= STATUS_POTENTIAL_MIN:
        return "Potential Match"
    return "Needs Review"


def status_chip_class(status: str) -> str:
    if status == "Strong Match":
        return "tq-chip tq-chip-success"
    if status == "Needs Review":
        return "tq-chip tq-chip-warning"
    return "tq-chip"


st.set_page_config(
    page_title="TalentIQ · Recruiter Results",
    page_icon=":material/analytics:",
    layout="wide",
)

apply_site_css()
AUTH_USER = require_authenticated()
render_navbar(active=None)

# ---------------------------------------------------------------------------
# Guard: this page is only reachable from the recruiter flow
# ---------------------------------------------------------------------------
if "uploaded_files" not in st.session_state:
    empty_state(
        "No candidates yet",
        "Start by uploading resumes and a job description in the Recruiter Dashboard.",
    )
    st.stop()

uploaded_files = st.session_state.uploaded_files
job_description = st.session_state.recruiter_jd

# ---------------------------------------------------------------------------
# Persistent candidate + analysis persistence.
#
# Every analyzed resume creates/reuses a Candidate (keyed by a SHA-256 of the
# file bytes) and stores one analysis snapshot. This is best-effort: ranking
# output never depends on it, and repeated reruns are deduplicated with a
# session guard so identical analyses are not re-inserted on every widget
# interaction.
# ---------------------------------------------------------------------------
PERSISTED_ANALYSES_KEY = "persisted_recruiter_analyses"


def _persist_candidate(file, profile, ats_result, jd_match, final_score):
    try:
        if profile is None or not getattr(profile, "raw_text", "").strip():
            return
        safe_name = sanitize_filename(getattr(file, "name", None) or "resume.pdf")
        resume_hash = resume_hash_file(file)
        jd_row = (
            save_job_description(
                job_description, created_by_user_id=AUTH_USER["id"]
            )
            if job_description.strip()
            else None
        )
        fingerprint = resume_hash + ("|" + jd_row.content_hash if jd_row else "|no-jd")
        if st.session_state.get(PERSISTED_ANALYSES_KEY, {}).get(fingerprint):
            return

        candidate_row = create_candidate(
            resume_hash=resume_hash,
            name=profile.name or safe_name,
            email=profile.email or None,
            phone=profile.phone or None,
            location=profile.location or None,
            resume_filename=safe_name,
            created_by_user_id=AUTH_USER["id"],
        )
        mark_analyzed(candidate_row.id, user=AUTH_USER)
        # Each distinct resume file becomes its own numbered version; identical
        # re-uploads reuse the existing version. Status is never changed here.
        resume_version = create_or_get_resume_version(
            candidate_id=candidate_row.id,
            file_hash=resume_hash,
            filename=safe_name,
            user=AUTH_USER,
        )
        save_analysis_from_results(
            candidate_id=candidate_row.id,
            jd_id=jd_row.id if jd_row else None,
            resume_version_id=resume_version.id,
            user=AUTH_USER,
            ats_result=ats_result,
            jd_match=jd_match,
            final_score=final_score,
        )
        st.session_state.setdefault(PERSISTED_ANALYSES_KEY, {})[fingerprint] = True
    except Exception:
        # Persistence is best-effort; ranking output stays valid either way.
        return


def _candidate_lookup():
    """Map displayed candidate name -> persisted candidate row (owned pool)."""
    mapping = {}
    for uploaded in uploaded_files:
        fname = getattr(uploaded, "name", None)
        if not fname:
            continue
        try:
            candidate = find_candidate_by_resume_hash(
                resume_hash_file(uploaded), user=AUTH_USER
            )
        except Exception:
            candidate = None
        if candidate is not None:
            mapping[fname] = candidate
    return mapping


NOT_PERSISTED = "—"

page_header(
    "Candidate Intelligence",
    "Ranked by a weighted blend of ATS score and job-description match.",
)

st.space("small")

# ---------------------------------------------------------------------------
# Ranking computation (unchanged logic, premium loading/error handling)
# ---------------------------------------------------------------------------
try:
    with loading_state("Analyzing candidates..."):
        rankings = rank_resumes(uploaded_files, job_description, on_candidate=_persist_candidate)
except Exception:
    error_state(
        "Something went wrong while ranking candidates",
        "One or more resumes could not be parsed. Check the files and try again.",
    )
    st.stop()

ranking_df = pd.DataFrame(rankings)
if ranking_df.empty:
    empty_state(
        "No candidates analyzed",
        "None of the uploaded resumes could be parsed. "
        "Start over with readable PDF files.",
    )
    st.stop()
ranking_df = ranking_df.sort_values(by="Final Score", ascending=False).reset_index(drop=True)
ranking_df["Status"] = ranking_df["Final Score"].map(score_status)
candidate_lookup = _candidate_lookup()
ranking_df["Recruitment Status"] = ranking_df["Name"].map(
    lambda name: (
        candidate_lookup[name].status if name in candidate_lookup else NOT_PERSISTED
    )
)

# ---------------------------------------------------------------------------
# Summary metrics (pool-level snapshot, unchanged scores)
# ---------------------------------------------------------------------------
section_header(
    "Recruiter Summary",
    description="Pool-level snapshot computed by the existing ATS + JD-match engine.",
    icon="overview",
)

k1, k2, k3, k4 = st.columns(4)
with k1:
    metric_card("Candidates Analyzed", len(ranking_df), icon="group")
_eligible_min = score_eligible_min()
with k2:
    metric_card(
        "Score-Eligible Candidates",
        f"{int((ranking_df['Final Score'] >= _eligible_min).sum())} of {len(ranking_df)}",
        delta=f"Final score ≥ {_eligible_min:.0f}",
        icon="checklist",
    )
    st.caption("Candidates meeting the configured score threshold")
with k3:
    score_card(
        "Average Final Score",
        ranking_df["Final Score"].mean(),
        max_value=100,
        icon="trending_up",
        caption="across the whole pool",
        tone="primary",
    )
with k4:
    metric_card(
        "Average ATS",
        f"{ranking_df['ATS Score'].mean():.1f}",
        icon="target",
        delta=f"{ranking_df['JD Match'].mean():.1f}% avg JD match",
    )

st.space("small")

# ---------------------------------------------------------------------------
# Recruitment pipeline (real DB counts per status — never mocked)
# ---------------------------------------------------------------------------
section_header(
    "Recruitment Pipeline",
    description="Live recruiter decisions across all persisted candidates, by status.",
    icon="database",
)

pipeline_icons = {
    "New": "person_add",
    "Analyzed": "fact_check",
    "Shortlisted": "bookmark",
    "Interview": "record_voice_over",
    "Selected": "task_alt",
    "Rejected": "block",
}
pipeline_unavailable = False
try:
    pipeline_counts = get_pipeline_counts(user=AUTH_USER)
except Exception:
    pipeline_unavailable = True
    pipeline_counts = {status: 0 for status in RECRUITMENT_STATUSES}
    pipeline_counts["All"] = 0

pcols = st.columns(len(RECRUITMENT_STATUSES))
for col, status in zip(pcols, RECRUITMENT_STATUSES):
    with col:
        metric_card(
            status,
            pipeline_counts[status],
            icon=pipeline_icons.get(status),
        )
if pipeline_unavailable:
    st.caption("Recruitment pipeline is temporarily unavailable.")
else:
    st.caption("Source of truth: persisted recruitment status in the local candidate database.")

st.space("small")

# ---------------------------------------------------------------------------
# Recruiter filters (narrow the displayed list only)
# ---------------------------------------------------------------------------
section_header(
    "Filter Candidates",
    description="These controls only narrow the displayed candidate list — scoring and ranking stay unchanged.",
    icon="tune",
)

f1, f2, f3, f4 = st.columns(4)
with f1:
    min_ats = st.slider("Min ATS Score", 0, 100, 0)
with f2:
    min_jd = st.slider("Min JD Match %", 0, 100, 0)
with f3:
    min_final = st.slider("Min Final Score", 0, 100, 0)
with f4:
    status_filter = st.selectbox(
        "Score Status",
        ["All", "Strong Match", "Potential Match", "Needs Review"],
    )

rec_status_filter = st.selectbox(
    "Recruitment Status",
    ["All", *RECRUITMENT_STATUSES],
    placeholder="Filter by status",
)

displayed = ranking_df[
    (ranking_df["ATS Score"] >= min_ats)
    & (ranking_df["JD Match"] >= min_jd)
    & (ranking_df["Final Score"] >= min_final)
].copy()
if status_filter != "All":
    displayed = displayed[displayed["Status"] == status_filter]
if rec_status_filter != "All":
    displayed = displayed[displayed["Recruitment Status"] == rec_status_filter]

if displayed.empty:
    empty_state(
        "No candidates match the selected filters",
        "Loosen the minimum scores or pick a broader status to see candidates.",
    )
    st.stop()

# ---------------------------------------------------------------------------
# Candidate ranking table
# ---------------------------------------------------------------------------
st.space("small")

section_header(
    "Candidate Ranking",
    description="Status thresholds — Strong Match ≥ 80 · Potential Match 60–79 · Needs Review < 60.",
    icon="leaderboard",
)

st.dataframe(
    displayed.style.background_gradient(
        subset=["Final Score"],
        cmap="Greens",
    ),
    width="stretch",
    hide_index=True,
    column_config={
        "Name": st.column_config.TextColumn("Candidate", width="medium"),
        "ATS Score": st.column_config.NumberColumn("ATS Score", format="%.1f"),
        "JD Match": st.column_config.NumberColumn("JD Match %", format="%.1f"),
        "Final Score": st.column_config.ProgressColumn("Final Score", format="%.1f", min_value=0, max_value=100),
        "Status": st.column_config.TextColumn("Status"),
        "Recruitment Status": st.column_config.TextColumn("Recruitment Status"),
    },
)

# ---------------------------------------------------------------------------
# Recruiter shortlist actions (persistent DB status with a session-only
# fallback for rows that could not be mapped to a persisted candidate, e.g.
# mocked/malformed uploads. Real recruiter flows always use the database.)
# ---------------------------------------------------------------------------
st.space("small")

fallback_shortlist = st.session_state.setdefault(SHORTLIST_KEY, {})


def _is_shortlisted(name: str) -> bool:
    candidate = candidate_lookup.get(name)
    if candidate is not None:
        return candidate.status == "Shortlisted"
    return bool(fallback_shortlist.get(name))


displayed_names = list(displayed["Name"])
shortlisted_names = [name for name in displayed_names if _is_shortlisted(name)]

section_header(
    "Recruiter Shortlist",
    description="Candidates manually saved by the recruiter for further review. "
    "Shortlisting does not change their AI scores.",
    icon="bookmark",
)

if shortlisted_names and st.button(
    "Clear Shortlist",
    icon=":material/delete:",
    key="clear_shortlist",
):
    for name in displayed_names:
        candidate = candidate_lookup.get(name)
        if candidate is not None and candidate.status == "Shortlisted":
            try:
                remove_from_shortlist(candidate.id, user=AUTH_USER)
            except Exception:
                st.error(
                    "Could not update the shortlist. "
                    "The previous status was preserved."
                )
                break
        else:
            fallback_shortlist.pop(name, None)
    st.rerun()

saved_count = len(shortlisted_names)
st.caption(f"{saved_count} candidate{'s' if saved_count != 1 else ''} manually shortlisted")

cols = st.columns(4)
for i, (_, row) in enumerate(displayed.iterrows()):
    name = row["Name"]
    saved = _is_shortlisted(name)
    shortlisted_chip = (
        ' <span class="tq-chip tq-chip-success">Shortlisted</span>' if saved else ""
    )
    with cols[i % 4]:
        with st.container(border=True):
            recruit_status = row["Recruitment Status"]
            st.markdown(
                f"""
                <div>
                  <div class="tq-candidate-name" style="font-size:0.95rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{escape(name)}</div>
                  <div style="margin:0.35rem 0 0.15rem;"><span class="{status_chip_class(row['Status'])}">{row['Status']}</span>{shortlisted_chip}
                    <span class="tq-chip">{escape(recruit_status)}</span></div>
                  <div class="tq-metric-delta" style="font-size:0.78rem;">
                    ATS {row['ATS Score']:.0f} &middot; JD {row['JD Match']:.0f}% &middot; Final {row['Final Score']:.0f}
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        if st.button(
            "Remove from Shortlist" if saved else "Shortlist",
            key=f"shortlist_{i}_{name}",
            width="stretch",
        ):
            candidate = candidate_lookup.get(name)
            if candidate is not None:
                try:
                    if saved:
                        remove_from_shortlist(candidate.id, user=AUTH_USER)
                    else:
                        shortlist_candidate(candidate.id, user=AUTH_USER)
                except Exception:
                    st.error(
                        "Could not update this candidate's status. "
                        "The previous status was preserved."
                    )
            else:
                fallback_shortlist[name] = not saved
            st.rerun()

        candidate_row = candidate_lookup.get(name)
        if candidate_row is not None:
            status_options = [
                s for s in RECRUITMENT_STATUSES if s != candidate_row.status
            ]
            sc1, sc2 = st.columns([3, 1])
            with sc1:
                chosen = st.selectbox(
                    "Change status to",
                    status_options,
                    index=None,
                    key=f"recruit_status_{i}_{name}",
                    label_visibility="collapsed",
                    placeholder="Change status to\u2026",
                )
            with sc2:
                if st.button(
                    "Update Status",
                    key=f"recruit_update_{i}_{name}",
                    width="stretch",
                ):
                    if chosen:
                        try:
                            update_candidate_status(candidate_row.id, chosen, user=AUTH_USER)
                        except Exception:
                            st.error(
                                "Could not update this candidate's status. "
                                "The previous status was preserved."
                            )
                        st.session_state.pop(f"recruit_status_{i}_{name}", None)
                        st.rerun()

# ---------------------------------------------------------------------------
# Activity Timeline (loads ONLY the selected candidate's history — never one
# query per row, never on every table cell)
# ---------------------------------------------------------------------------
st.space("small")

section_header(
    "Activity Timeline",
    description="Auditable history for the selected candidate. Latest event first.",
    icon="history",
)

timeline_name = st.selectbox(
    "Candidate",
    displayed_names,
    index=0,
    key="timeline_candidate",
)
timeline_row = candidate_lookup.get(timeline_name)
if timeline_row is None:
    st.caption("No persisted activity timeline for this candidate.")
else:
    try:
        timeline_activities = get_candidate_activity(timeline_row.id, user=AUTH_USER)
    except Exception:
        timeline_activities = []
    if not timeline_activities:
        st.caption("No recorded activity for this candidate yet.")
    else:
        render_activity_timeline(timeline_activities)

# ---------------------------------------------------------------------------
# Best candidate + recommendation (compact, 2-column layout)
# ---------------------------------------------------------------------------
st.space("small")

winner = displayed.iloc[0]

section_header("Best Candidate", icon="workspace_premium")

b1, b2 = st.columns(2)
with b1:
    with st.container(border=True):
        st.markdown(
            f"""
            <div>
              <div class="tq-rank">#1 &middot; Top Match</div>
              <h3 class="tq-candidate-name" style="margin-top:0.35rem;">{escape(winner["Name"])}</h3>
              <div style="margin:0.4rem 0 0;">
                <span class="{status_chip_class(winner["Status"])}">{winner["Status"]}</span>
              </div>
              <div style="display:flex;gap:1.5rem;flex-wrap:wrap;margin-top:0.75rem;">
                <div>
                  <div class="tq-metric-head">ATS Score</div>
                  <div class="tq-metric-value" style="font-size:1.3rem;">{winner["ATS Score"]:.1f}</div>
                </div>
                <div>
                  <div class="tq-metric-head">JD Match</div>
                  <div class="tq-metric-value" style="font-size:1.3rem;">{winner["JD Match"]:.1f}%</div>
                </div>
                <div>
                  <div class="tq-metric-head">Final Score</div>
                  <div class="tq-metric-value" style="font-size:1.3rem;">{winner["Final Score"]:.1f}</div>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
with b2:
    with st.container(border=True):
        st.markdown("**Recommendation**")
        st.write(
            "Shortlist this candidate first based on the combined ATS and "
            "job-description match."
        )
        st.markdown(
            "<div class='tq-metric-delta'>final score thresholds apply</div>",
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------------
# Remaining useful information (single distribution chart)
# ---------------------------------------------------------------------------
st.space("small")

section_header("Candidate Analytics", icon="query_stats")

st.plotly_chart(
    score_distribution(displayed, "Final Score", height=300),
    key="r4_distribution",
    width="stretch",
    theme=None,
)
st.caption("Distribution of final scores across the displayed candidates")

st.space("small")

# ---------------------------------------------------------------------------
# Export + start over
# ---------------------------------------------------------------------------
# Export a COPY of the displayed frame: string cells are run through
# ``csv_neutralize`` so spreadsheet formula injection payloads never execute,
# and the live ``displayed`` frame (used by charts, shortlist rows, timeline)
# is left untouched.
_export_frame = displayed.copy()
for _col in _export_frame.select_dtypes(include=["object"]).columns:
    _export_frame[_col] = _export_frame[_col].map(csv_neutralize)
csv = _export_frame.to_csv(index=False)

f1, f2 = st.columns(2)
with f1:
    st.download_button(
        "Download Candidate Rankings (CSV)",
        csv,
        "TalentIQ_Candidate_Rankings.csv",
        "text/csv",
        icon=":material/download:",
        width="stretch",
    )
with f2:
    if st.button(
        "Compare New Candidates",
        icon=":material/refresh:",
        width="stretch",
    ):
        st.session_state.pop("uploaded_files", None)
        st.session_state.pop("recruiter_jd", None)
        st.switch_page("pages/3_Recruiter_Dashboard.py")

render_footer()