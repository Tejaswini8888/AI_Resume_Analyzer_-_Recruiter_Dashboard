"""TalentIQ · Recruiter Analytics.

Dedicated analytics experience built EXCLUSIVELY from persisted database
records (candidates, analyses, activity). No session-state numbers, no mocked
data, no re-implementation of the per-candidate ranking charts that were
removed from Recruiter Results — every metric is computed by
``services.analytics_service`` from the database and rendered as a clean,
recruiter-oriented page: KPI row, pipeline overview, score insights, skill
intelligence, and a recruitment activity summary.
"""

from html import escape

import streamlit as st

from ui.theme import apply_site_css
from ui.navigation import render_navbar, render_footer
from ui.components import page_header, section_header
from ui.cards import metric_card
from ui.empty_states import empty_state
from ui.timeline import format_activity_timestamp
from ui.auth_gate import require_authenticated

from services.analytics_service import (
    TIME_RANGES,
    get_analytics_bundle,
    get_job_description_options,
)

st.set_page_config(
    page_title="TalentIQ · Recruiter Analytics",
    page_icon=":material/query_stats:",
    layout="wide",
)

apply_site_css()
AUTH_USER = require_authenticated()
render_navbar(active="Analytics")

# ---------------------------------------------------------------------------
# Filters (persisted-data based; both are optional)
# ---------------------------------------------------------------------------
_PIPELINE_CSS = """
<style>
  .tq-an-pipeline { display: flex; align-items: stretch; gap: 0.25rem; flex-wrap: wrap; }
  .tq-an-step {
    display: flex; flex-direction: column; align-items: center; gap: 0.15rem;
    min-width: 104px; border: 1px solid var(--tq-border); border-radius: var(--tq-radius);
    background: var(--tq-surface); padding: 0.6rem 0.7rem;
  }
  .tq-an-step .tq-an-count { font-size: 1.35rem; font-weight: 700; color: var(--tq-primary-soft); line-height: 1.1; }
  .tq-an-step .tq-an-name { font-size: 0.78rem; color: var(--tq-muted); letter-spacing: 0.03em; }
  .tq-an-arrow { display: flex; align-items: center; color: var(--tq-muted); font-size: 1.05rem; }
  .tq-an-step.tq-an-rejected .tq-an-count { color: var(--tq-danger); }
  .tq-an-track { margin-top: 0.1rem; height: 3px; border-radius: 999px;
    background: linear-gradient(90deg, var(--tq-primary-soft), var(--tq-secondary)); width: 100%; }
  .tq-an-skill-row {
    display: flex; align-items: center; justify-content: space-between; gap: 0.6rem;
    padding: 0.34rem 0; border-bottom: 1px dashed rgba(30, 41, 59, 0.75);
  }
  .tq-an-skill-row:last-child { border-bottom: 0; }
  .tq-an-skill-count { color: var(--tq-muted); font-size: 0.82rem; white-space: nowrap; }
  .tq-an-activity-item {
    display: flex; align-items: center; gap: 0.6rem; padding: 0.4rem 0;
    border-bottom: 1px dashed rgba(30, 41, 59, 0.75); font-size: 0.9rem;
  }
  .tq-an-activity-item:last-child { border-bottom: 0; }
  .tq-an-activity-name { font-weight: 600; color: #F8FAFC; }
  .tq-an-activity-time { margin-left: auto; color: var(--tq-muted); font-size: 0.8rem; white-space: nowrap; }
</style>
"""

try:
    jd_options = get_job_description_options(user=AUTH_USER)
except Exception:
    # UI must survive a transient DB failure: fall back to an unscoped view
    # and surface the degraded state below the filters.
    jd_options = []

_ALL_LABEL = "All roles"
jd_choices = [(_ALL_LABEL, None)] + [
    (
        jd["title"]
        or (jd["preview"][:42] + "…" if len(jd["preview"]) > 42 else jd["preview"]),
        jd["id"],
    )
    for jd in jd_options
]

time_choices = list(TIME_RANGES.keys())

filter_col, filter_col2, _spacer = st.columns([2, 2, 6])
with filter_col:
    jd_choice = st.selectbox(
        "Job Description",
        jd_choices,
        index=0,
        format_func=lambda opt: opt[0],
        key="analytics_jd",
        help="Scope analytics to one persisted job description pool.",
    )
with filter_col2:
    time_choice = st.selectbox(
        "Time Range",
        time_choices,
        index=0,
        key="analytics_time",
        help="Uses persisted analysis / activity timestamps.",
    )

jd_id = jd_choice[1]
days = TIME_RANGES[time_choice]

try:
    bundle = get_analytics_bundle(jd_id=jd_id, days=days, user=AUTH_USER)
except Exception:
    st.error(
        "Analytics are temporarily unavailable. Please try again in a moment.",
        icon=":material/error:",
    )
    render_footer()
    st.stop()
pipeline = bundle["pipeline"]
scores = bundle["scores"]
skills = bundle["skills"]
activity = bundle["activity"]

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
page_header(
    "Recruiter Analytics",
    "Recruitment intelligence from your candidate pipeline.",
)
st.caption(
    "All metrics are computed from the persisted database — not the current session."
)

# ---------------------------------------------------------------------------
# Empty states (0 candidates / 0 analyses / empty scoped pool)
# ---------------------------------------------------------------------------
if not bundle["has_data"]:
    empty_state(
        "No candidate analytics available yet.",
        "Analyze resumes to start building your hiring intelligence.",
    )
    render_footer()
    st.stop()

scoped_empty = pipeline["pool_total"] == 0
if scoped_empty:
    empty_state(
        "No analytics for this job description yet.",
        "Candidates are not analyzed against this role yet — analyze resumes with "
        "this job description to populate these insights.",
    )
    render_footer()
    st.stop()

st.space("small")

# ---------------------------------------------------------------------------
# Recruitment Overview (KPI row)
# ---------------------------------------------------------------------------
section_header(
    "Recruitment Overview",
    description="Candidates analyzed and Current recruitment status counts.",
    icon="overview",
)

overview_metrics = [
    ("Candidates Analyzed", pipeline["analyzed"], "group", None),
    ("Shortlisted", pipeline["status_counts"].get("Shortlisted", 0), "bookmark", None),
    ("Interview", pipeline["status_counts"].get("Interview", 0), "record_voice_over", None),
    ("Selected", pipeline["status_counts"].get("Selected", 0), "task_alt", None),
    ("Rejected", pipeline["status_counts"].get("Rejected", 0), "block", None),
]
visible = [
    (m[0], m[1], m[2])
    for m in overview_metrics
    if m[0] == "Candidates Analyzed" or m[1] > 0
]
cols = st.columns(len(visible))
for col, (label, value, icon) in zip(cols, visible):
    with col:
        metric_card(label, value, icon=icon)

st.space("small")

# ---------------------------------------------------------------------------
# Pipeline Overview (New -> Analyzed -> Shortlisted -> Interview -> Selected,
# with Rejected shown separately)
# ---------------------------------------------------------------------------
section_header(
    "Pipeline Overview",
    description="Actual persisted status counts. Rejected is shown separately.",
    icon="account_tree",
)
st.markdown(_PIPELINE_CSS, unsafe_allow_html=True)
parts = []
for i, name in enumerate(["New", "Analyzed", "Shortlisted", "Interview", "Selected"]):
    if i:
        parts.append('<span class="tq-an-arrow">→</span>')
    count = pipeline["status_counts"].get(name, 0)
    parts.append(
        f'<div class="tq-an-step"><span class="tq-an-count">{count}</span>'
        f'<span class="tq-an-name">{escape(name)}</span></div>'
    )
rejected_count = pipeline["status_counts"].get("Rejected", 0)
parts.append(f'<div class="tq-an-step tq-an-rejected"><span class="tq-an-count">{rejected_count}</span>'
             f'<span class="tq-an-name">Rejected</span></div>')
st.markdown(
    f'<div class="tq-an-pipeline">{"".join(parts)}</div>',
    unsafe_allow_html=True,
)
st.caption(
    f"{pipeline['pool_total']} candidate(s) in this pool · Candidates Analyzed "
    f"{pipeline['analyzed']}"
)

st.space("medium")

# ---------------------------------------------------------------------------
# Candidate Score Insights
# ---------------------------------------------------------------------------
section_header(
    "Candidate Score Insights",
    description="Averages over each candidate's latest analysis snapshot.",
    icon="speed",
)
if not scores["has_score_data"] or (
    scores["avg_ats"] is None and scores["avg_jd_match"] is None and scores["avg_final"] is None
):
    st.caption("No score data available yet.")
else:
    cards = st.columns(3)
    for col, label, value, unit in zip(
        cards,
        ("Average ATS Compatibility", "Average JD Match", "Average Final Score"),
        (scores["avg_ats"], scores["avg_jd_match"], scores["avg_final"]),
        ("%", "%", "%"),
    ):
        with col:
            metric_card(label, f"{value:.1f}" if value is not None else "—", unit=unit)
    if scores["highest_final"] is not None:
        st.caption(
            f"Final scores across {scores['candidate_count']} candidate(s): "
            f"highest {scores['highest_final']:.1f}% · lowest {scores['lowest_final']:.1f}%."
        )

st.space("medium")

# ---------------------------------------------------------------------------
# Skill Intelligence
# ---------------------------------------------------------------------------
section_header(
    "Skill Intelligence",
    description="Canonical skills found in candidate analyses and the gaps reported against the job.",
    icon="psychology",
)

if not skills["has_skill_data"]:
    st.caption("No skill data available yet.")
else:
    g1, g2 = st.columns(2)
    with g1:
        st.markdown('<h4 class="tq-section-title">Top Candidate Skills</h4>', unsafe_allow_html=True)
        if skills["top_skills"]:
            for label, count in skills["top_skills"]:
                st.markdown(
                    f'<div class="tq-an-skill-row"><span class="tq-chip">{escape(str(label))}</span>'
                    f'<span class="tq-an-skill-count">{count} candidate(s)</span></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("No matched skills recorded yet.")
    with g2:
        st.markdown('<h4 class="tq-section-title">Most Common Skill Gaps</h4>', unsafe_allow_html=True)
        if skills["top_gaps"]:
            for label, count in skills["top_gaps"]:
                st.markdown(
                    f'<div class="tq-an-skill-row"><span class="tq-chip tq-chip-warning">{escape(str(label))}</span>'
                    f'<span class="tq-an-skill-count">{count} candidate(s)</span></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("No skill gaps recorded yet.")

st.space("medium")

# ---------------------------------------------------------------------------
# Recruitment Activity
# ---------------------------------------------------------------------------
section_header(
    "Recruitment Activity",
    description="Summary of recorded activity in the selected time range.",
    icon="history",
)

if not activity["has_activity"]:
    st.caption("No recruitment activity recorded yet.")
else:
    c = activity["counts"]
    st.caption(
        f"{c['analyzed']} analyzed · {c['shortlisted']} shortlisted · "
        f"{c['removed_from_shortlist']} removed from shortlist · "
        f"{c['moved_to_interview']} moved to interview · "
        f"{c['marked_selected']} selected · {c['marked_rejected']} rejected · "
        f"{c['status_changed']} other status changes"
    )
    with st.container(border=True):
        for item in activity["recent"][:10]:
            st.markdown(
                f'<div class="tq-an-activity-item">'
                f'<span class="material-symbols-outlined" style="color:var(--tq-primary-soft);font-size:1.05rem;">history</span>'
                f'<span class="tq-an-activity-name">{escape(item["candidate_name"])}</span>'
                f'<span class="tq-chip">{escape(item["label"])}</span>'
                f'<span class="tq-an-activity-time">{escape(format_activity_timestamp(item["created_at"]))}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

render_footer()