"""TalentIQ atomic UI components.

Small building blocks: page/section headers, badges, skill chips, dividers,
and progress bars. Cards and metric tiles live in ``ui.cards``.
"""

from html import escape

import streamlit as st

from ui.theme import material_icon

TONE_CLASSES = {"success", "warning", "danger", "info", "neutral"}


# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
def page_header(title: str, subtitle: str | None = None) -> None:
    """Large page title with optional subtitle."""
    sub = f'<p class="tq-page-subtitle">{escape(subtitle)}</p>' if subtitle else ""
    st.markdown(f'<h1 class="tq-page-title">{escape(title)}</h1>{sub}', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section header
# ---------------------------------------------------------------------------
def section_header(
    title: str,
    description: str | None = None,
    icon: str | None = None,
) -> None:
    """Section heading with optional description and Material icon."""
    icon_html = material_icon(icon) if icon else ""
    desc = f'<p class="tq-section-desc">{escape(description)}</p>' if description else ""
    st.markdown(
        f'<h3 class="tq-section-title">{icon_html}&nbsp; {escape(title)}</h3>{desc}',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Status badge
# ---------------------------------------------------------------------------
def status_badge(label: str, tone: str = "neutral") -> None:
    """A pill badge. ``tone`` ∈ {"success", "warning", "danger", "info", "neutral"}."""
    tone = tone if tone in TONE_CLASSES else "neutral"
    st.markdown(f'<span class="tq-badge tq-badge-{tone}">{escape(label)}</span>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Skill chips
# ---------------------------------------------------------------------------
def skill_chip(label: str, tone: str = "default") -> None:
    """A single skill chip (rendered standalone)."""
    tone = tone if tone in ("success", "warning", "danger", "muted") else ""
    cls = f"tq-chip tq-chip-{tone}" if tone else "tq-chip"
    st.markdown(f'<span class="{cls}">{escape(label)}</span>', unsafe_allow_html=True)


def skill_chips(skills: list[str], tone: str = "default") -> None:
    """Render a list of skills as a wrapping chip row."""
    if not skills:
        return
    tone = tone if tone in ("success", "warning", "danger", "muted") else ""
    cls = f"tq-chip tq-chip-{tone}" if tone else "tq-chip"
    chips = "".join(f'<span class="{cls}">{escape(s)}</span>' for s in skills)
    st.markdown(f'<div class="tq-chip-group">{chips}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Divider
# ---------------------------------------------------------------------------
def divider(label: str | None = None) -> None:
    """A quiet horizontal rule, optionally labeled (small uppercase text)."""
    if label:
        st.markdown(f'<div class="tq-divider">{escape(label)}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="tq-divider"></div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------
def progress_bar(
    value: float,
    max_value: float | int = 100,
    tone: str = "primary",
    thin: bool = False,
) -> None:
    """Gradient progress bar. ``tone`` ∈ {"primary", "success", "warning", "danger"}."""
    pct = max(0.0, min(100.0, (float(value) / float(max_value)) * 100))
    tone_cls = f"tq-progress-{tone}" if tone in TONE_CLASSES else ""
    thin_cls = "tq-progress-thin" if thin else ""
    st.markdown(
        f'<div class="tq-progress {tone_cls} {thin_cls}">'
        f'<div style="width:{pct:.1f}%"></div></div>',
        unsafe_allow_html=True,
    )