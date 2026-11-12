"""Access gate for TalentIQ's recruiter (protected) pages.

Every protected page calls :func:`require_authenticated` as its FIRST action,
before ``render_navbar`` — so an anonymous visitor never sees the recruiter
navigation or any recruiter data, and a deactivated account is stopped on its
next interaction (``current_user`` re-validates identity + ``is_active`` against
the database on every call).

The gate renders a clean, branded panel instead of leaking internals: no
stack traces, no database details, only "you need to sign in" guidance.
"""

import streamlit as st

from services.auth_service import current_user
from ui.theme import apply_site_css, material_icon


def _render_access_panel(
    title: str,
    message: str,
    cta_label: str | None = None,
    target: str | None = None,
) -> None:
    apply_site_css()
    st.markdown(
        f"""
        <div class="tq-state">
          <div class="tq-state-icon">{material_icon("lock")}</div>
          <h3 class="tq-state-title">{title}</h3>
          <p class="tq-state-message">{message}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if cta_label and target:
        with st.container(horizontal=True, horizontal_alignment="center"):
            if st.button(cta_label, type="primary", icon=":material/login:"):
                st.switch_page(target)


def require_authenticated() -> dict:
    """Return the authenticated user dict or stop the page with a sign-in panel."""
    user = current_user()
    if user is None:
        _render_access_panel(
            title="Account required",
            message=(
                "Recruiter tools are for signed-in users. Sign in (or create an "
                "account) to view candidates, analytics and the recruiter workspace."
            ),
            cta_label="Sign In",
            target="pages/0_Login.py",
        )
        st.stop()
    return user


def require_role(role: str) -> dict:
    """Return the user dict, requiring the given role or stopping the page."""
    user = require_authenticated()
    if user.get("role") != role:
        _render_access_panel(
            title="Access denied",
            message=f"This area is available to {role.lower()} accounts only.",
            cta_label="Back to Recruiter Dashboard",
            target="pages/3_Recruiter_Dashboard.py",
        )
        st.stop()
    return user