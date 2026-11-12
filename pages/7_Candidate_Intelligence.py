"""TalentIQ · Recruiter Workspace (unified Candidate Pool + Candidate Detail).

One coherent recruiter surface backed entirely by the persisted database:

- Candidate pool: one row per candidate (name / status / ATS / JD match /
  final score / latest resume version / last updated), filtered by free-text,
  status, skill, job description and minimum scores. Each candidate appears
  exactly once regardless of how many resume versions or analyses exist.
  The pool can be exported as CSV from the exact current result set.
- Candidate detail pane: identity, the persisted analysis snapshot, matched /
  missing skills, a compact status control with context-aware actions, resume
  version history and the auditable activity timeline.
- Every analysis is ALWAYS shown with its exact context: the selected resume
  version + job description that produced it. Selecting Version 1 + JD A can
  never show Version 2's scores, and scores from different JDs are never mixed.

The database is the source of truth. Session state holds only UI selection —
search/filter values, the selected candidate, and the selected version/JD —
all of which can be cleared by the recruiter. No fake data is ever introduced,
and existing services are reused (search, recruitment, analysis, activity,
resume versions); nothing here re-implements persistence.
"""

from html import escape

import pandas as pd

import streamlit as st

from ui.theme import apply_site_css
from ui.navigation import render_navbar, render_footer
from ui.components import page_header, section_header, skill_chips
from ui.cards import metric_card
from ui.empty_states import empty_state, error_state
from ui.timeline import render_activity_timeline, format_activity_timestamp

from services.candidate_search_service import (
    PAGE_SIZE,
    candidate_pool_csv,
    get_analysis_for_context,
    get_job_description_options,
    get_skill_options,
    get_sort_options,
    get_status_options,
    get_workspace_detail,
    score_eligible_min,
    search_candidates,
    sort_key_for_label,
)
from services.recruitment_service import update_candidate_status
from ui.auth_gate import require_authenticated

# ---------------------------------------------------------------------------
# Page shell
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="TalentIQ · Recruiter Workspace",
    page_icon=":material/workspaces:",
    layout="wide",
)

apply_site_css()
AUTH_USER = require_authenticated()
render_navbar(active="Recruiter Workspace")

_ALL_STATUS = "All"
_ANY_SKILL = "Any skill"
_ALL_JDS = "All job descriptions"
_NO_JD = "No job description"
_DEFAULT_SORT_LABEL = get_sort_options()[0]

# Session-only UI state (database remains the source of truth).
SELECTED_KEY = "ws_selected_candidate_id"
POOL_NONCE_KEY = "ws_pool_nonce"
_WS_DEFAULTS = {
    "ws_search": "",
    "ws_status": _ALL_STATUS,
    "ws_skill": _ANY_SKILL,
    "ws_jd": _ALL_JDS,
    "ws_min_ats": 0,
    "ws_min_jd": 0,
    "ws_min_final": 0,
    "ws_sort": _DEFAULT_SORT_LABEL,
}


def _status_chip_class(status: str) -> str:
    return {
        "New": "tq-chip tq-chip-muted",
        "Analyzed": "tq-chip tq-chip-muted",
        "Shortlisted": "tq-chip tq-chip-success",
        "Interview": "tq-chip",
        "Selected": "tq-chip tq-chip-success",
        "Rejected": "tq-chip tq-chip-danger",
    }.get(status, "tq-chip")


def _short_date(value) -> str:
    if value is None:
        return "—"
    try:
        local = value.astimezone()
    except Exception:
        local = value
    return local.strftime("%b %d, %Y")


def _job_label(ctx: dict) -> str:
    title = (ctx.get("title") or "").strip()
    return title or f"JD #{ctx['id']}"


def _default_jd_label(detail: dict):
    """Default to the job description of the candidate's latest analysis."""
    latest = detail.get("latest_analysis")
    if latest is not None and getattr(latest, "jd_id", None) is not None:
        for ctx in detail.get("jd_contexts") or []:
            if ctx["id"] == latest.jd_id:
                return _job_label(ctx)
    return None


# Only useful next actions exist per state; deeper moves are always available
# through the same single status system (recruitment_service).
_STATE_ACTIONS = {
    "New": [
        ("Shortlisted", "Shortlist", ":material/bookmark:"),
        ("Rejected", "Reject", ":material/block:"),
    ],
    "Analyzed": [
        ("Shortlisted", "Shortlist", ":material/bookmark:"),
        ("Rejected", "Reject", ":material/block:"),
    ],
    "Shortlisted": [
        ("Interview", "Move to Interview", ":material/record_voice_over:"),
        ("Rejected", "Reject", ":material/block:"),
    ],
    "Interview": [
        ("Selected", "Mark Selected", ":material/task_alt:"),
        ("Rejected", "Reject", ":material/block:"),
    ],
    "Selected": [
        ("Shortlisted", "Move Back to Shortlist", ":material/undo:"),
    ],
    "Rejected": [
        ("Analyzed", "Move Back to Analyzed", ":material/undo:"),
    ],
}


def _render_candidate_detail(detail: dict, candidate_id: int) -> None:
    """Full candidate detail pane (identity, analysis, skills, status, history).

    The analysis is always resolved from the EXACT (candidate, resume version,
    job description) triple the recruiter selects, so a version's scores can
    never bleed into another version's card or into a different JD.
    """
    cand = detail["candidate"]
    versions = detail.get("resume_versions") or []
    contexts = detail.get("jd_contexts") or []
    has_no_jd = detail.get("has_no_jd_analysis", False)
    latest = detail.get("latest_analysis")
    activities = detail.get("activities") or []
    current_status = cand["status"]

    version_key = f"ws_ver_{candidate_id}"
    jd_key = f"ws_jd_{candidate_id}"

    # ---------------------------------------------------------------------
    # Analysis context selectors (version + JD) — determine the analysis shown
    # ---------------------------------------------------------------------
    sel_version_id = None
    sel_version_number = None
    if versions:
        version_labels, version_lookup = [], {}
        for v in versions:
            tag = " · Current" if v.get("is_current") else ""
            label = f"Version {v['version_number']}{tag}"
            version_labels.append(label)
            version_lookup[label] = v["id"]
        if st.session_state.get(version_key) not in version_labels:
            st.session_state[version_key] = version_labels[0]
        st.selectbox(
            "Resume Version",
            version_labels,
            index=version_labels.index(st.session_state[version_key]),
            key=version_key,
        )
        sel_version_id = version_lookup[st.session_state[version_key]]
        sel_version_number = next(
            (v["version_number"] for v in versions if v["id"] == sel_version_id), None
        )

    jd_labels = [_job_label(ctx) for ctx in contexts]
    if has_no_jd:
        jd_labels.append(_NO_JD)
    jd_lookup = {label: ctx["id"] for label, ctx in zip(jd_labels, contexts)}
    jd_lookup[_NO_JD] = None

    sel_jd_id = None
    jd_caption = _NO_JD
    if jd_labels and versions:
        default_jd = _default_jd_label(detail)
        if st.session_state.get(jd_key) not in jd_labels:
            st.session_state[jd_key] = (
                default_jd if default_jd in jd_labels else jd_labels[0]
            )
        st.selectbox(
            "Job Description",
            jd_labels,
            index=jd_labels.index(st.session_state[jd_key]),
            key=jd_key,
        )
        sel_jd_id = jd_lookup[st.session_state[jd_key]]
        jd_caption = st.session_state[jd_key]

    # ---------------------------------------------------------------------
    # Resolve the exact persisted analysis for the chosen context
    # ---------------------------------------------------------------------
    if sel_version_id is not None:
        analysis = get_analysis_for_context(
            candidate_id, sel_version_id, sel_jd_id, user=AUTH_USER
        )
    else:
        analysis = latest

    if analysis is not None and analysis.jd_id is not None:
        jd_caption = next(
            (_job_label(c) for c in contexts if c["id"] == analysis.jd_id),
            f"JD #{analysis.jd_id}",
        )
    elif analysis is not None and analysis.jd_id is None:
        jd_caption = _NO_JD

    if sel_version_id is not None and sel_version_number is not None:
        ver_caption = f"Version {sel_version_number}"
    else:
        ver_caption = cand.get("resume_filename") or "original resume"

    # ---------------------------------------------------------------------
    # Analysis summary
    # ---------------------------------------------------------------------
    section_header(
        "Analysis Summary",
        description=f"Analysis for: {jd_caption} · Resume: {ver_caption}",
        icon="insights",
    )

    if analysis is None:
        st.caption("No analysis available for this candidate.")
    else:
        st.caption(f"Analyzed {format_activity_timestamp(analysis.created_at)}")
        a1, a2, a3, a4 = st.columns(4)
        with a1:
            metric_card(
                "Final Score",
                f"{analysis.final_score:.1f}" if analysis.final_score is not None else "—",
                icon="trending_up",
            )
        with a2:
            metric_card(
                "ATS Score",
                f"{analysis.ats_score:.1f}" if analysis.ats_score is not None else "—",
                icon="target",
            )
        with a3:
            metric_card(
                "JD Match",
                f"{analysis.jd_match_score:.1f}" if analysis.jd_match_score is not None else "—",
                icon="description",
            )
        with a4:
            metric_card(
                "Semantic Match",
                f"{analysis.semantic_match_score:.1f}" if analysis.semantic_match_score is not None else "—",
                icon="psychology",
            )

        st.space("small")

        # -----------------------------------------------------------------
        # Skills — from the exact analysis shown above
        # -----------------------------------------------------------------
        section_header(
            "Skills",
            description="Matched and missing skills recorded in the shown analysis.",
            icon="sell",
        )
        s1, s2, s3 = st.columns(3)
        with s1:
            st.markdown('<h4 class="tq-section-title">Matched</h4>', unsafe_allow_html=True)
            matched = analysis.matched_skills or []
            if matched:
                skill_chips(sorted(matched), tone="success")
            else:
                st.caption("None")
        with s2:
            st.markdown(
                '<h4 class="tq-section-title">Missing Required</h4>',
                unsafe_allow_html=True,
            )
            missing_req = analysis.missing_required_skills or []
            if missing_req:
                skill_chips(sorted(missing_req), tone="warning")
            else:
                st.caption("None")
        with s3:
            st.markdown(
                '<h4 class="tq-section-title">Missing Preferred</h4>',
                unsafe_allow_html=True,
            )
            missing_pref = analysis.missing_preferred_skills or []
            if missing_pref:
                skill_chips(sorted(missing_pref), tone="muted")
            else:
                st.caption("None")

    st.space("small")

    # ---------------------------------------------------------------------
    # Recruitment control (single status system; score never changes status)
    # ---------------------------------------------------------------------
    section_header(
        "Recruitment",
        description="Score-Eligible is a score property and is never applied as a status automatically.",
        icon="handyman",
    )
    with st.container(border=True):
        st.markdown(
            f'<div class="tq-metric-delta">Current Status</div>'
            f'<div style="margin:0.3rem 0 0.7rem;">'
            f'<span class="{_status_chip_class(current_status)}">{escape(current_status)}</span></div>',
            unsafe_allow_html=True,
        )
        actions = _STATE_ACTIONS.get(current_status, _STATE_ACTIONS["Analyzed"])
        act_cols = st.columns(2)
        for i, (target, label, icon) in enumerate(actions):
            with act_cols[i % 2]:
                if st.button(
                    label,
                    icon=icon,
                    key=f"ws_act_{candidate_id}_{target}",
                    width="stretch",
                ):
                    try:
                        update_candidate_status(candidate_id, target, user=AUTH_USER)
                        st.rerun()
                    except Exception:
                        st.error(
                            "Could not update this candidate's status. "
                            "The previous status was preserved.",
                            icon=":material/error:",
                        )
        st.caption("Status changes update the persisted candidate and append activity history.")

    st.space("small")

    # ---------------------------------------------------------------------
    # Resume history (list only; the version selector above drives the analysis)
    # ---------------------------------------------------------------------
    section_header(
        "Resume History",
        description="Every distinct upload gets its own numbered version; identical re-uploads reuse one.",
        icon="description",
    )
    if not versions:
        st.caption("No resume versions recorded for this candidate.")
    elif len(versions) <= 1:
        st.caption("No previous resume versions.")
    else:
        for v in versions:
            is_viewing = v["id"] == sel_version_id
            current_tag = " · Current" if v.get("is_current") else ""
            viewing_tag = " · viewing" if is_viewing else ""
            chip_cls = "tq-chip tq-chip-success" if v.get("is_current") else "tq-chip tq-chip-muted"
            st.markdown(
                f'<div class="tq-metric-delta" style="margin:0.25rem 0;">'
                f'<span class="{chip_cls}">Version {v["version_number"]}{current_tag}</span>'
                f' &nbsp; {escape(v.get("filename") or "unnamed")}'
                f' &middot; {_short_date(v.get("created_at"))}'
                f'{viewing_tag}</div>',
                unsafe_allow_html=True,
            )

    st.space("small")

    # ---------------------------------------------------------------------
    # Activity timeline (reused — never duplicated)
    # ---------------------------------------------------------------------
    section_header(
        "Activity Timeline",
        description="Auditable history for the selected candidate. Latest event first.",
        icon="history",
    )
    render_activity_timeline(
        activities,
        empty_message="No recruitment activity recorded yet.",
    )


# ---------------------------------------------------------------------------
# Clear-filters reset: applied BEFORE any widget is instantiated.
# ---------------------------------------------------------------------------
if st.session_state.pop("_ws_reset_requested", False):
    for _key, _value in _WS_DEFAULTS.items():
        if st.session_state.get(_key) != _value:
            st.session_state[_key] = _value
    st.session_state["ws_page"] = 1
    st.session_state[SELECTED_KEY] = None
    st.session_state[POOL_NONCE_KEY] = int(st.session_state.get(POOL_NONCE_KEY, 0)) + 1
    st.rerun()

# Back navigation: from Candidate Detail back to the Candidate Pool.
if st.session_state.pop("_ws_back_requested", False):
    st.session_state[SELECTED_KEY] = None
    st.session_state[POOL_NONCE_KEY] = int(st.session_state.get(POOL_NONCE_KEY, 0)) + 1
    st.rerun()

page_header(
    "Recruiter Workspace",
    "Search the persistent candidate pool, open a candidate for their current "
    "analysis, skills, status and history — all from one surface.",
)

st.space("small")

# ---------------------------------------------------------------------------
# Data feeds (option sets always derived from the database)
# ---------------------------------------------------------------------------
status_options = get_status_options()
skill_options = get_skill_options(user=AUTH_USER)
jd_options = get_job_description_options(user=AUTH_USER)

all_results = search_candidates(user=AUTH_USER)
if not all_results["has_candidates"]:
    empty_state(
        "No candidates have been analyzed yet",
        "Analyze resumes in the Recruiter Dashboard and their candidates will "
        "appear here — everything is read straight from the persisted database.",
    )
    render_footer()
    st.stop()

# ---------------------------------------------------------------------------
# Search + filter controls (kept in session state while navigating candidates)
# ---------------------------------------------------------------------------
section_header(
    "Search Candidates",
    description=(
        "Every control narrows the persisted candidate pool; scoring, ranking "
        "and recruitment statuses are never changed here."
    ),
    icon="manage_search",
)

c_text, c_status, c_skill, c_jd = st.columns([2.2, 1, 1.2, 1.4])
with c_text:
    search_text = st.text_input(
        "Search candidates by name, email, skill, or resume filename",
        key="ws_search",
        placeholder="e.g. Tejas, python, resume.pdf",
    )
with c_status:
    status_filter = st.selectbox("Status", status_options, key="ws_status")
with c_skill:
    skill_filter = st.selectbox("Skill", [_ANY_SKILL, *skill_options], key="ws_skill")
with c_jd:
    jd_labels = [_ALL_JDS] + [
        f"{jd_title} ({jd['analysis_count']})"
        for jd in jd_options
        for jd_title in [(jd["title"] or f"JD #{jd['id']}")]
    ]
    jd_labels_lookup = {
        label: jd["id"] for label, jd in zip(jd_labels[1:], jd_options)
    }
    jd_filter = st.selectbox("Job Description", jd_labels, key="ws_jd")

c_at, c_jd_sc, c_fin, c_sort = st.columns([1, 1, 1, 1.3])
with c_at:
    min_ats = st.slider("Minimum ATS Score", 0, 100, 0, key="ws_min_ats")
with c_jd_sc:
    min_jd_score = st.slider("Minimum JD Match", 0, 100, 0, key="ws_min_jd")
with c_fin:
    min_final = st.slider("Minimum Final Score", 0, 100, 0, key="ws_min_final")
with c_sort:
    sort_label = st.selectbox("Sort By", get_sort_options(), key="ws_sort")
    sort_key = sort_key_for_label(sort_label)

if st.button("Clear Filters", icon=":material/filter_alt_off:", key="ws_clear"):
    st.session_state["_ws_reset_requested"] = True
    st.rerun()

st.space("small")

# ---------------------------------------------------------------------------
# Page state: filter changes reset pagination + selection; pool always re-queried
# ---------------------------------------------------------------------------
_fingerprint = (
    (search_text or "").strip().lower(),
    status_filter,
    skill_filter,
    jd_filter,
    min_ats,
    min_jd_score,
    min_final,
    sort_label,
)
if st.session_state.get("_ws_fp") != _fingerprint:
    st.session_state["_ws_fp"] = _fingerprint
    st.session_state["ws_page"] = 1
    st.session_state[SELECTED_KEY] = None
    st.session_state[POOL_NONCE_KEY] = int(st.session_state.get(POOL_NONCE_KEY, 0)) + 1
page = int(st.session_state.get("ws_page", 1) or 1)

jd_id = jd_labels_lookup.get(jd_filter) if jd_filter != _ALL_JDS else None
skill_value = None if skill_filter == _ANY_SKILL else skill_filter

# ---------------------------------------------------------------------------
# Query the persistent database fresh on every rerun
# ---------------------------------------------------------------------------
try:
    response = search_candidates(
        query=search_text,
        status=status_filter,
        skill=skill_value,
        jd_id=jd_id,
        min_ats=min_ats,
        min_jd=min_jd_score,
        min_final=min_final,
        sort=sort_key,
        page=page,
        per_page=PAGE_SIZE,
        user=AUTH_USER,
    )
except Exception:
    error_state(
        "Search is temporarily unavailable",
        "The candidate database could not be queried right now. "
        "Please try again in a moment.",
    )
    render_footer()
    st.stop()

total = response["total"]
page = min(page, response["total_pages"] or 1)
total_pages = response["total_pages"]
results = response["results"]

if not response["has_results"]:
    empty_state(
        "No candidates match your current filters",
        "Loosen the search text, status, skill, job description or "
        "minimum-score filters to see more candidates.",
    )
    render_footer()
    st.stop()

# ---------------------------------------------------------------------------
# Workspace panes: Candidate Pool (left) + Candidate Detail (right)
# ---------------------------------------------------------------------------
pool_col, detail_col = st.columns([1.45, 1.05], gap="large")

# --- pool: result summary + CSV export -------------------------------------
with pool_col:
    summary = f"{total} candidate" + ("s" if total != 1 else "") + " in pool"
    if search_text:
        summary += f' · search «{search_text}»'
    if jd_id is not None:
        summary += " · scoped to one job description"

    head_c1, head_c2 = st.columns([3, 1.4])
    with head_c1:
        st.markdown(
            f'<div class="tq-metric-head" style="font-size:0.9rem;">'
            f'<span class="material-symbols-outlined">groups</span>'
            f"&nbsp; {escape(summary)}</div>",
            unsafe_allow_html=True,
        )
    with head_c2:
        st.download_button(
            "Export Pool (CSV)",
            candidate_pool_csv(results),
            "TalentIQ_Candidate_Pool.csv",
            "text/csv",
            icon=":material/download:",
            width="stretch",
        )

    table_df = pd.DataFrame(
        [
            {
                "Candidate": row["name"] or f"Candidate #{row['id']}",
                "Status": row["status"],
                "Score-Eligible": "Yes" if row["score_eligible"] else "No",
                "ATS": row["ats_score"],
                "JD Match": row["jd_match_score"],
                "Final Score": row["final_score"],
                "Resume Version": (
                    f"v{row['latest_version']}" if row.get("latest_version") else "—"
                ),
                "Updated": _short_date(row["updated_at"]),
            }
            for row in results
        ]
    )

    st.dataframe(
        table_df,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"ws_pool_{int(st.session_state.get(POOL_NONCE_KEY, 0))}",
        column_config={
            "Candidate": st.column_config.TextColumn("Candidate", width="medium"),
            "Status": st.column_config.TextColumn("Status", width="small"),
            "Score-Eligible": st.column_config.TextColumn(
                "Score-Eligible",
                width="small",
                help=(
                    f"Latest final score ≥ {score_eligible_min():.0f} — a score "
                    "property, never a recruitment status."
                ),
            ),
            "ATS": st.column_config.NumberColumn("ATS", width="small", format="%.1f"),
            "JD Match": st.column_config.NumberColumn("JD Match", width="small", format="%.1f"),
            "Final Score": st.column_config.NumberColumn("Final Score", width="small", format="%.1f"),
            "Resume Version": st.column_config.TextColumn("Resume Version", width="small"),
            "Updated": st.column_config.TextColumn("Updated", width="small"),
        },
    )

    nav1, nav_mid, nav2 = st.columns([1, 2, 1])
    with nav1:
        if st.button("← Previous", disabled=page <= 1, key="ws_prev"):
            st.session_state["ws_page"] = max(1, page - 1)
            st.rerun()
    with nav_mid:
        st.caption(f"Page {page} of {total_pages or 1}")
    with nav2:
        if st.button("Next →", disabled=page >= total_pages, key="ws_next"):
            st.session_state["ws_page"] = min(total_pages, page + 1)
            st.rerun()
    st.caption(f"{PAGE_SIZE} candidates per page · click a row to open its detail. "
               "Read directly from the persisted database.")

# --- selected candidate ----------------------------------------------------
selected_id = st.session_state.get(SELECTED_KEY)
with detail_col:
    if selected_id is None:
        with st.container(border=True):
            st.markdown(
                '<div class="tq-state" style="padding:2.2rem 1rem;">'
                '<div class="tq-state-icon" style="font-size:2.2rem;">'
                '<span class="material-symbols-outlined">person_search</span></div>'
                "<h3 class=\"tq-state-title\" style=\"font-size:1.05rem;\">"
                "Select a candidate from the pool</h3>"
                "<p class=\"tq-state-message\">Open a candidate to view their "
                "current analysis, skills, recruitment status, resume versions "
                "and activity timeline.</p></div>",
                unsafe_allow_html=True,
            )
        st.stop()

    try:
        detail = get_workspace_detail(selected_id, user=AUTH_USER)
    except Exception:
        st.error("Could not load this candidate's details.", icon=":material/error:")
        if st.button("← Back to Candidate Pool", key="ws_err_back", icon=":material/arrow_back:"):
            st.session_state["_ws_back_requested"] = True
            st.rerun()
        st.stop()

    if detail is None:
        st.error("Candidate details are unavailable.", icon=":material/error:")
        if st.button("← Back to Candidate Pool", key="ws_missing_back", icon=":material/arrow_back:"):
            st.session_state["_ws_back_requested"] = True
            st.rerun()
        st.stop()

    cand = detail["candidate"]
    current_status = cand["status"]

    with st.container(border=True):
        chips = [
            f'<span class="{_status_chip_class(current_status)}">{escape(current_status)}</span>'
        ]
        chips.append(
            '<span class="tq-chip tq-chip-success">Score-Eligible</span>'
            if detail.get("score_eligible")
            else '<span class="tq-chip tq-chip-muted">Below Threshold</span>'
        )
        contact_parts = [
            escape(cand.get("email") or ""),
            escape(cand.get("phone") or ""),
            escape(cand.get("location") or ""),
        ]
        contact_line = " · ".join(p for p in contact_parts if p) or "—"
        st.markdown(
            f"""
            <div>
              <h3 class="tq-candidate-name">{escape(cand["name"] or f"Candidate #{selected_id}")}</h3>
              <div style="margin:0.4rem 0 0.5rem;">{' '.join(chips)}</div>
              <div class="tq-metric-delta">
                {contact_line}<br>
                {escape(cand.get("resume_filename") or "")}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.space("small")

    if st.button("← Back to Candidate Pool", key="ws_back", icon=":material/arrow_back:"):
        st.session_state["_ws_back_requested"] = True
        st.rerun()

    st.space("small")

    _render_candidate_detail(detail, selected_id)

render_footer()