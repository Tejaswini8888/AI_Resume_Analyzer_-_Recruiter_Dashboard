"""TalentIQ loading states.

River of premium loading placeholders for slow AI / parsing work.
"""

from contextlib import contextmanager

import streamlit as st


def loading_banner(text: str) -> None:
    """A compact spinner + message banner."""
    st.markdown(
        f"""
        <div class="tq-loading">
          <span class="tq-spinner"></span>
          <span>{text}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


@contextmanager
def loading_state(text: str, height: int = 220):
    """Reserve space and show a premium spinner while a block runs.

    The banner is guaranteed to be removed when the block finishes — on both
    success and failure — so it can never linger next to a success/error state.

    Usage::

        with loading_state("Analyzing resume..."):
            result = heavy_work()
        # banner is gone; result is ready
    """
    slot = st.empty()
    with slot:
        loading_banner(text)
    try:
        with st.skeleton(height=height):
            yield
    finally:
        # Always drop the loading banner so it never coexists with a
        # completion/error banner on the page.
        slot.empty()