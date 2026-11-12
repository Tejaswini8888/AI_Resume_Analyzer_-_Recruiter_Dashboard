"""TalentIQ · Audit Logs (admin-only).

Central system/security audit trail: who performed which action, on what
resource, and when. This is the administrative interface for
``services.audit_service`` — it never writes audit events itself.

Access is enforced at BOTH layers:
- the page gate (``require_role("ADMIN")``) stops non-admins before any control
  renders or page-scratch queries run, and
- ``list_audit_events`` re-checks the role server-side, so a direct request can
  never bypass the UI.

The log is read in paginated, server-side queries: filters (user, action,
resource type, date range) and search are applied in SQL, never by loading the
whole table into Python.
"""

import datetime as dt

import pandas as pd
import streamlit as st

from services import audit_service
from services.auth_service import list_users
from services.audit_service import ACTION_LABELS, RESOURCE_TYPES
from ui.auth_gate import require_role
from ui.components import page_header
from ui.empty_states import empty_state
from ui.navigation import render_footer, render_navbar
from ui.theme import apply_site_css

st.set_page_config(
    page_title="TalentIQ · Audit Logs",
    page_icon=":material/verified_user:",
    layout="wide",
)

apply_site_css()
AUTH_USER = require_role("ADMIN")
render_navbar(active="Audit Logs")

page_header(
    "Audit Logs",
    "A central record of user and admin actions, resource changes and security "
    "events. Read-only, admin-only, and append-only.",
)

_page_key = "admin_audit_page"
_prev_filter_key = "admin_audit_prev_filters"


def _all_actions() -> list[str]:
    return sorted(audit_service.AUDIT_EVENTS)


# ---------------------------------------------------------------------------
# Filters (all optional)
# ---------------------------------------------------------------------------
users = list_users()
user_options = {"All users": None}
for u in users:
    label = f"{u.get('name') or u.get('email')} · {u.get('email')}"
    if not u.get("is_active"):
        label += " (inactive)"
    user_options[label] = u.get("id")

action_options = {ACTION_LABELS.get(a, a): a for a in _all_actions()}
resource_options = {label: code for code, label in RESOURCE_TYPES}

c1, c2, c3, c4 = st.columns([1.4, 1.1, 1.0, 1.2])
with c1:
    selected_user = st.selectbox(
        "User", list(user_options.keys()), key="admin_audit_user"
    )
with c2:
    selected_action = st.selectbox(
        "Action",
        ["All actions"] + sorted(action_options),
        key="admin_audit_action",
    )

date_delta = st.date_input(
    "Date range",
    value=(),
    key="admin_audit_dates",
    help="Optional UTC range; leave empty to include all dates.",
)

c3, c4 = st.columns(2)
with c3:
    selected_resource = st.selectbox(
        "Resource type",
        ["All resources"] + sorted(resource_options),
        key="admin_audit_resource",
    )
with c4:
    search = st.text_input(
        "Search",
        placeholder="User, resource id, description…",
        key="admin_audit_search",
    )

start_date = end_date = None
if isinstance(date_delta, (list, tuple)) and len(date_delta) == 2:
    start_date, end_date = date_delta
elif isinstance(date_delta, tuple) and len(date_delta) == 2:
    start_date, end_date = date_delta
elif isinstance(date_delta, dt.date):
    start_date = date_delta
    end_date = date_delta

# ---------------------------------------------------------------------------
# Pagination state + page
# ---------------------------------------------------------------------------
current_filters = (
    user_options.get(selected_user),
    action_options.get(selected_action),
    resource_options.get(selected_resource),
    start_date,
    end_date,
    search,
)

if _prev_filter_key not in st.session_state:
    st.session_state[_prev_filter_key] = current_filters
if st.session_state[_prev_filter_key] != current_filters:
    st.session_state[_page_key] = 1
    st.session_state[_prev_filter_key] = current_filters

try:
    page = int(st.session_state.get(_page_key, 1))
except (TypeError, ValueError):
    page = 1
page = max(page, 1)

# ---------------------------------------------------------------------------
# Query (server-side, paginated)
# ---------------------------------------------------------------------------
try:
    result = audit_service.list_audit_events(
        user=AUTH_USER,
        user_id=user_options.get(selected_user),
        action=action_options.get(selected_action),
        resource_type=resource_options.get(selected_resource),
        start_date=start_date,
        end_date=end_date,
        search=search,
        page=page,
        per_page=25,
    )
except PermissionError:
    st.error("You do not have permission to view the audit log.")
    st.stop()

st.session_state[_page_key] = result["page"]

rows = result["events"]
total = result["total"]
total_pages = result["total_pages"]

st.markdown(
    f"<p style='color:var(--tq-muted);font-size:0.9rem;'>"
    f"<b>{total}</b> event{'s' if total != 1 else ''} in the current view</p>",
    unsafe_allow_html=True,
)

if not rows:
    empty_state(
        "No audit events",
        "No events match the current filters. Broaden the date range or clear "
        "the search to see more.",
    )
else:
    table_rows = []
    for event in rows:
        timestamp = event["created_at"]
        if timestamp is None:
            ts_str = "—"
        else:
            ts_str = timestamp.strftime("%Y-%m-%d %H:%M")
        actor = event["user_name"] or event["user_email"] or "System"
        if event["user_email"]:
            actor += f" · {event['user_email']}"
        resource = event["resource_type"]
        if event["resource_id"] is not None:
            resource += f" #{event['resource_id']}"
        table_rows.append(
            {
                "Timestamp (UTC)": ts_str,
                "User": actor,
                "Action": event["action"],
                "Resource": resource,
                "Description": event["description"],
            }
        )

    st.dataframe(
        pd.DataFrame(table_rows),
        width="stretch",
        hide_index=True,
        height=min(25 * len(table_rows) + 40, 620),
    )

    nav_cols = st.columns([1, 2, 1])
    with nav_cols[0]:
        prev_disabled = result["page"] <= 1
        if st.button(
            "Previous",
            icon=":material/arrow_back:",
            disabled=prev_disabled,
            width="stretch",
        ):
            st.session_state[_page_key] = result["page"] - 1
            st.rerun()
    with nav_cols[1]:
        st.markdown(
            f"<p style='text-align:center;color:var(--tq-muted);'>"
            f"Page <b>{result['page']}</b> of <b>{total_pages}</b></p>",
            unsafe_allow_html=True,
        )
    with nav_cols[2]:
        next_disabled = result["page"] >= total_pages
        if st.button(
            "Next",
            icon=":material/arrow_forward:",
            disabled=next_disabled,
            width="stretch",
        ):
            st.session_state[_page_key] = result["page"] + 1
            st.rerun()

with st.expander("About the audit log"):
    st.markdown(
        """
        **What is recorded** — sign-ins (success and failure), sign-outs, account
        creation, role changes, activation/deactivation, candidate creation and
        analysis, shortlisting, recruitment status changes, resume versions and
        job descriptions. Candidate-specific *activity timelines* stay separate:
        they remain visible to recruiters per the usual ownership rules.

        **What is never recorded** — passwords, password hashes, session tokens,
        API keys, authorization headers, full resume text, or any file content.
        Attempting to write such data to the log is rejected by the service layer.

        **Retention** — events are append-only and are never automatically
        deleted or trimmed.
        """,
        unsafe_allow_html=False,
    )

render_footer()