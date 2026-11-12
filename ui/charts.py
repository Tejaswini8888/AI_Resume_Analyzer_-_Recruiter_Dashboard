"""TalentIQ chart helpers.

Thin wrappers around Plotly Express that apply the TalentIQ visual language
(transparent canvas, brand palette, theme-aware text) while keeping the
exact same inputs and data the existing pages already compute.
"""

import pandas as pd
import plotly.express as px

from ui.theme import CHART_COLORS, PRIMARY, SECONDARY, SURFACE_2, TEXT

_AXIS_LINE = "rgba(148,163,184,0.18)"


def _theme_text() -> str:
    """Chart text color. The UI is dark-first, so text is always bright."""
    return TEXT


def _base_layout(fig, height: int = 400) -> None:
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, sans-serif", "color": _theme_text(), "size": 13},
        margin={"l": 20, "r": 20, "t": 40, "b": 20},
        title={
            "font": {"size": 16, "weight": 700},
            "x": 0.0,
            "xanchor": "left",
        },
        hoverlabel={
            "bgcolor": SURFACE_2,
            "bordercolor": _AXIS_LINE,
            "font": {"color": TEXT, "size": 12},
        },
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "left",
            "x": 0,
            "font": {"size": 12},
        },
    )


def ats_donut(
    breakdown: dict[str, float],
    title: str | None = None,
    height: int = 360,
    show_legend: bool = True,
) -> "px.figure":
    """Donut of the ATS score breakdown.

    No internal ``title`` is drawn by default — callers provide their own
    ``section_header`` — which avoids the duplicated "ATS Score Breakdown"
    heading plus a legend/title overlap inside the chart.
    """
    df = pd.DataFrame({"Category": list(breakdown.keys()), "Score": list(breakdown.values())})
    fig = px.pie(
        df,
        values="Score",
        names="Category",
        hole=0.62,
        color_discrete_sequence=CHART_COLORS,
    )
    fig.update_traces(
        textinfo="label+percent",
        textfont_size=12,
        textposition="auto",
        hovertemplate="<b>%{label}</b><br>%{value} / 100 pts",
    )
    _base_layout(fig, height=height)
    if title:
        fig.update_layout(title={"text": title})
    if show_legend:
        # Clean centered legend above the chart so it never collides with a
        # heading or the donut itself.
        fig.update_layout(
            legend={
                "orientation": "h",
                "yanchor": "bottom",
                "y": 1.02,
                "xanchor": "center",
                "x": 0.5,
                "font": {"size": 12},
            }
        )
    else:
        fig.update_layout(showlegend=False)
    return fig


def score_distribution(
    df: pd.DataFrame,
    column: str,
    height: int = 300,
    nbins: int = 12,
) -> "px.figure":
    """Histogram of a candidate-pool score column.

    No internal ``title`` is drawn — callers pair this with a
    ``section_header`` or caption so headings are never duplicated.
    """
    fig = px.histogram(
        df,
        x=column,
        nbins=nbins,
        color_discrete_sequence=[PRIMARY],
    )
    fig.update_traces(
        hovertemplate="<b>%{x:.0f}</b> · %{y} candidate(s)<extra></extra>",
        marker={"line": {"width": 0}},
    )
    fig.update_layout(
        xaxis={
            "title": column,
            "gridcolor": _AXIS_LINE,
            "range": [0, 105],
        },
        yaxis={"title": "Candidates", "gridcolor": _AXIS_LINE},
        bargap=0.06,
        showlegend=False,
    )
    _base_layout(fig, height=height)
    return fig