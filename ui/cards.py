"""TalentIQ card components.

`metric_card`, `score_card`, and `candidate_card` compose native Streamlit
containers with a small, consistent layer of HTML for typography and icon
treatment. All data passed in is rendered as text — no unsafe user input.
"""

import streamlit as st

from ui.theme import material_icon


# ---------------------------------------------------------------------------
# Metric card
# ---------------------------------------------------------------------------
def metric_card(
    label: str,
    value,
    delta: str | None = None,
    icon: str | None = None,
    unit: str | None = None,
) -> None:
    """A compact KPI card. ``icon`` is a Material Symbols name (e.g. "speed")."""
    icon_html = material_icon(icon) if icon else ""
    val = str(value)
    unit_html = f'<span class="unit">{unit}</span>' if unit else ""
    delta_html = f'<div class="tq-metric-delta">{delta}</div>' if delta else ""

    with st.container(border=True):
        st.markdown(
            f"""
            <div class="tq-metric">
              <div class="tq-metric-head">
                <span class="tq-metric-icon">{icon_html}</span>
                <span>{label}</span>
              </div>
              <div class="tq-metric-value">{val}{unit_html}</div>
              {delta_html}
            </div>
            """,
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Score card (value + animated progress)
# ---------------------------------------------------------------------------
def score_card(
    label: str,
    value: float,
    max_value: float | int = 100,
    icon: str | None = None,
    caption: str | None = None,
    tone: str = "primary",
) -> None:
    """A KPI card with a gradient progress bar.

    ``tone`` ∈ {"primary", "success", "warning", "danger"}.
    """
    pct = max(0.0, min(100.0, (float(value) / float(max_value)) * 100))
    icon_html = material_icon(icon) if icon else ""
    caption_html = f'<div class="tq-metric-delta">{caption}</div>' if caption else ""

    with st.container(border=True):
        st.markdown(
            f"""
            <div class="tq-metric">
              <div class="tq-metric-head">
                <span class="tq-metric-icon">{icon_html}</span>
                <span>{label}</span>
              </div>
              <div class="tq-metric-value">
                {value:.0f}<span class="unit">/ {max_value:.0f}</span>
              </div>
              <div class="tq-progress tq-progress-{tone}">
                <div style="width:{pct:.1f}%"></div>
              </div>
              {caption_html}
            </div>
            """,
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Candidate card
# ---------------------------------------------------------------------------
def candidate_card(
    name: str,
    rank: int | str | None = None,
    scores: dict[str, float] | None = None,
    subtitle: str | None = None,
    tags: list[str] | None = None,
) -> None:
    """A recruiter-facing candidate summary card."""
    scores = scores or {}
    rank_html = f'<div class="tq-rank">#{rank}&nbsp;&middot;&nbsp;Ranked Candidate</div>' if rank is not None else ""
    subtitle_html = f'<div class="tq-metric-delta">{subtitle}</div>' if subtitle else ""

    chip_html = ""
    if tags:
        chips = "".join(f'<span class="tq-chip tq-chip-muted">{t}</span>' for t in tags)
        chip_html = f'<div class="tq-chip-group" style="margin-top:0.7rem;">{chips}</div>'

    score_html = ""
    if scores:
        cells = "".join(
            f"<div><div class='tq-metric-head'>{k}</div>"
            f"<div class='tq-metric-value' style='font-size:1.35rem;'>{v:.1f}</div></div>"
            for k, v in scores.items()
        )
        score_html = f'<div style="display:flex;gap:1.6rem;flex-wrap:wrap;margin-top:0.9rem;">{cells}</div>'

    with st.container(border=True):
        st.markdown(
            f"""
            <div>
              {rank_html}
              <h3 class="tq-candidate-name">{name}</h3>
              {subtitle_html}
              {chip_html}
              {score_html}
            </div>
            """,
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Insight card
# ---------------------------------------------------------------------------
def insight_card(
    title: str,
    body: str,
    icon: str | None = None,
    tone: str = "primary",
) -> None:
    """A left-accented insight block for AI outputs."""
    icon_html = material_icon(icon) if icon else ""
    st.markdown(
        f"""
        <div class="tq-insight">
          <p class="tq-insight-title"><span>{icon_html}</span> {title}</p>
          <div class="tq-insight-body">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )