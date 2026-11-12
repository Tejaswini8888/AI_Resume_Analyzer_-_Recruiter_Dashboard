"""TalentIQ · Sign In / Create Account (public).

Recruiter tools are protected; this page is the public entry point for the
two recruiter roles. Authentication semantics:

- ``authenticate_user`` never distinguishes a bad password from an unknown
  email — the UI shows one generic "Invalid email or password." message.
- A deactivated account reaches the correct credentials and gets a clean,
  non-revealing message instead of pretending the password was wrong.
- Registration defaults to the RECRUITER role; an admin account is created by
  registering with ``TALENTIQ_ADMIN_EMAIL`` + ``TALENTIQ_ADMIN_PASSWORD`` from
  the environment (never hardcoded).
"""

import streamlit as st

from services import auth_service
from ui.navigation import render_navbar, render_footer
from ui.theme import apply_site_css

st.set_page_config(
    page_title="TalentIQ · Sign In",
    page_icon=":material/login:",
    layout="wide",
)

apply_site_css()
render_navbar()

_l, center, _r = st.columns([1, 2.4, 1])

with center:
    st.markdown(
        """
        <div class="tq-hero" style="padding:1.6rem 1rem 1.2rem;">
          <span class="tq-eyebrow">Recruiter Access</span>
          <h1 class="tq-hero-title" style="font-size:clamp(1.7rem, 3vw, 2.3rem);">
            Sign In to <span class="tq-gradient-text">TalentIQ</span>
          </h1>
          <p class="tq-hero-subtitle">
            Access the recruiter dashboard, candidate analytics and the
            recruiter workspace. Candidate self-analysis stays public.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    already = auth_service.current_user()
    if already is not None:
        st.success(
            f"You're signed in as {already['name']} ({already['role']}).",
            icon=":material/verified:",
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button(
                "Continue to Recruiter Dashboard",
                type="primary",
                icon=":material/group:",
                width="stretch",
            ):
                st.switch_page("pages/3_Recruiter_Dashboard.py")
        with c2:
            if st.button(
                "Sign Out",
                icon=":material/logout:",
                width="stretch",
            ):
                st.switch_page("pages/9_Logout.py")
        render_footer()
        st.stop()

    tab_login, tab_register = st.tabs(["Sign In", "Create Account"])

    with tab_login:
        with st.container(border=True):
            with st.form("sign_in_form", border=False):
                st.text_input(
                    "Email", key="login_email", autocomplete="username"
                )
                st.text_input(
                    "Password",
                    key="login_password",
                    type="password",
                    autocomplete="current-password",
                )
                submitted = st.form_submit_button(
                    "Sign In", type="primary", width="stretch"
                )
            if submitted:
                status, user = auth_service.authenticate_user(
                    st.session_state.login_email,
                    st.session_state.login_password,
                )
                if status == "ok":
                    auth_service.start_session(user)
                    st.switch_page("pages/3_Recruiter_Dashboard.py")
                elif status == "inactive":
                    st.error(
                        "Your account is inactive. Contact an administrator.",
                        icon=":material/do_not_disturb_on:",
                    )
                elif status == "error":
                    st.error(
                        "Sign-in is temporarily unavailable. Please try again.",
                        icon=":material/error:",
                    )
                else:
                    st.error(
                        "Invalid email or password.",
                        icon=":material/error:",
                    )

    with tab_register:
        with st.container(border=True):
            st.write(
                "Create an account to use the recruiter tools. New accounts "
                "are recruiters by default."
            )
            with st.form("register_form", border=False):
                st.text_input(
                    "Full Name", key="register_name", autocomplete="name"
                )
                st.text_input(
                    "Email", key="register_email", autocomplete="username"
                )
                st.text_input(
                    "Password",
                    key="register_password",
                    type="password",
                    autocomplete="new-password",
                    help="At least 8 characters.",
                )
                registered = st.form_submit_button(
                    "Create Account", type="primary", width="stretch"
                )
            if registered:
                try:
                    user = auth_service.register_user(
                        st.session_state.register_name,
                        st.session_state.register_email,
                        st.session_state.register_password,
                    )
                except ValueError as exc:
                    st.error(str(exc), icon=":material/error:")
                except Exception:
                    # Validation passes but the registration itself failed
                    # (e.g. database unavailable). Show a clean, non-revealing
                    # message instead of leaking an internal exception.
                    st.error(
                        "Could not create your account right now. "
                        "Please try again in a moment.",
                        icon=":material/error:",
                    )
                else:
                    auth_service.start_session(user)
                    st.success(
                        f"Account created — you're signed in as "
                        f"{user['name']} ({user['role']}).",
                        icon=":material/check_circle:",
                    )
                    if st.button(
                        "Continue to Recruiter Dashboard",
                        type="primary",
                        icon=":material/group:",
                        width="stretch",
                        key="post_register_go",
                    ):
                        st.switch_page("pages/3_Recruiter_Dashboard.py")

    st.space("small")
    st.caption(
        "Candidate self-analysis (Resume Analyzer, AI Resume Coach) is free "
        "and public — no account required."
    )

render_footer()