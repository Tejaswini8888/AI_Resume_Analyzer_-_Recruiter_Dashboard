"""TalentIQ · Sign Out (public).

Clears the authenticated-session marker and any sensitive page-scratch state so
no recruiter/candidate data survives a sign-out. After clearing, the page shows
a clean confirmation with a way back to the public home and the login page.
The navbar reflects the anonymous state (Sign In link).
"""

import streamlit as st

from services.auth_service import logout_user
from ui.navigation import render_navbar, render_footer
from ui.theme import apply_site_css

st.set_page_config(
    page_title="TalentIQ · Sign Out",
    page_icon=":material/logout:",
    layout="wide",
)

apply_site_css()
logout_user()
render_navbar()

_l, center, _r = st.columns([1, 2.4, 1])

with center:
    st.markdown(
        """
        <div class="tq-state">
          <div class="tq-state-icon tq-state-icon-success">
            <span class="material-symbols-outlined" style="font-size:2.6rem;">logout</span>
          </div>
          <h3 class="tq-state-title">You've been signed out</h3>
          <p class="tq-state-message">
            Your recruiter session has ended and protected data is no longer
            visible on this device.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button(
            "Return to Home",
            icon=":material/home:",
            width="stretch",
        ):
            st.switch_page("app.py")
    with c2:
        if st.button(
            "Sign In",
            type="primary",
            icon=":material/login:",
            width="stretch",
        ):
            st.switch_page("pages/0_Login.py")

render_footer()