"""TalentIQ top navigation bar and footer.

A sticky branded navbar rendered as a single HTML block. It is auth-aware
(Phase 4): recruiter links (Recruiter, Recruiter Workspace, Analytics) are only
shown to signed-in users; anonymous visitors see the public journey (Resume
Analyzer, AI Resume Coach) plus a "Sign In" entry. The brand home (Dashboard)
stays public so the landing page is always reachable.
"""

import streamlit as st

from services.auth_service import current_user
from ui.theme import material_icon

# Unreleased product areas render as disabled "Coming soon" items so no broken
# links are exposed. Settings is only meaningful for signed-in users.
_PRIVATE_ITEMS = [
    ("Recruiter", "Recruiter_Dashboard", "Recruiter"),
    ("Recruiter Workspace", "Candidate_Intelligence", "Recruiter_Workspace"),
    ("Analytics", "Recruiter_Analytics", "Recruiter_Analytics"),
]


def _render_item(label: str, href: str, active_name: str | None, active) -> str:
    classes = ["tq-nav-link"]
    if active_name == active or label == active:
        classes.append("active")
    return f'<a class="{" ".join(classes)}" href="{href}">{label}</a>'


def _render_disabled(label: str) -> str:
    return (
        f'<span class="tq-nav-link disabled" title="Coming soon">{label}</span>'
    )


def _user_menu(user: dict) -> str:
    icon = material_icon("account_circle", size=18)
    initial = (user.get("name") or "User").strip()[:1].upper()
    return (
        f'<span class="tq-nav-user">{icon}'
        f"<span class=\"tq-nav-user-name\">{initial} · {user.get('role', '')}</span></span>"
    )


def render_navbar(active: str | None = None) -> None:
    """Render the TalentIQ navbar. ``active`` is the label of the current item."""
    user = current_user()
    authenticated = user is not None

    links = [
        ("Dashboard", "./", "Dashboard"),
        ("Resume Analyzer", "Resume_Analysis", "Resume Analyzer"),
    ]
    if authenticated:
        links += _PRIVATE_ITEMS
        if user.get("role") == "ADMIN":
            links.append(("Audit Logs", "Admin_Audit_Logs", "Audit Logs"))
    links.append(("AI Resume Coach", "AI_Resume_Coach", "AI Resume Coach"))

    link_html = "".join(
        _render_item(label, href, name, active) for label, href, name in links
    )
    if authenticated:
        link_html += _render_disabled("Settings")

    if authenticated:
        right_html = (
            '<div class="tq-nav-right">'
            f"{_user_menu(user)}"
            '<a class="tq-nav-link tq-nav-signout" href="Logout">Sign out</a>'
            "</div>"
        )
    else:
        right_html = (
            '<div class="tq-nav-right">'
            '<a class="tq-nav-link tq-nav-signin" href="Login">Sign In</a>'
            "</div>"
        )

    html = f"""
    <nav class="tq-nav" aria-label="Primary">
      <a class="tq-brand" href="./">Talent<span>IQ</span></a>
      <div class="tq-nav-links">{link_html}</div>
      {right_html}
    </nav>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_footer() -> None:
    """Render the page footer."""
    st.markdown(FOOTER, unsafe_allow_html=True)


def render_footer_with_user(user: dict | None = None) -> None:
    """Alias kept for callers that resolve the user for other reasons."""
    render_footer()


FOOTER = """
<div class="tq-footer">
  <a class="tq-brand" href="./">Talent<span>IQ</span></a>
  <span>AI Recruitment Intelligence Platform &middot; Built with Streamlit</span>
</div>
"""