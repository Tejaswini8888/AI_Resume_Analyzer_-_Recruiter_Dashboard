"""TalentIQ empty / error / success states.

Centered, quiet states rendered inside a bordered container so they feel
deliberate rather than like an error page.
"""

import streamlit as st


def _render(kind: str, title: str, message: str, detail: str | None = None) -> None:
    icon = {
        "empty": "inbox",
        "error": "error",
        "success": "check_circle",
    }[kind]

    detail_html = (
        f'<p class="tq-state-message" style="margin-top:0.6rem;">{detail}</p>'
        if detail
        else ""
    )

    # Success cards center their message directly under the title.
    message_align = ' style="text-align:center;"' if kind == "success" else ""

    with st.container(border=True):
        st.markdown(
            f"""
            <div class="tq-state">
              <div class="tq-state-icon tq-state-icon-{kind}">
                <span class="material-symbols-outlined">{icon}</span>
              </div>
              <h3 class="tq-state-title">{title}</h3>
              <p class="tq-state-message"{message_align}>{message}</p>
              {detail_html}
            </div>
            """,
            unsafe_allow_html=True,
        )


def empty_state(title: str, message: str) -> None:
    """Nothing to show yet (no data, no upload, no candidates)."""
    _render("empty", title, message)


def error_state(title: str, message: str, detail: str | None = None) -> None:
    """Something went wrong while processing."""
    _render("error", title, message, detail)


def success_state(title: str, message: str = "") -> None:
    """Confirmation that processing completed."""
    _render("success", title, message)