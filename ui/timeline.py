"""TalentIQ compact vertical activity timeline.

Renders an immutable per-candidate activity list as a single, clean vertical
timeline using the existing dark-brand CSS tokens — no cards, no charts.
Timestamps are stored aware-UTC in the database and converted to the reader's
local time for display (consistent with the ``utcnow`` convention in
``core/models.py``).
"""

from html import escape

import streamlit as st

ACTIVITY_META = {
    "RESUME_ANALYZED": ("Resume Analyzed", "document_scanner", "tq-tl-info"),
    "STATUS_CHANGED": ("Status Changed", "swap_horiz", "tq-tl-neutral"),
    "SHORTLISTED": ("Shortlisted", "bookmark", "tq-tl-success"),
    "REMOVED_FROM_SHORTLIST": (
        "Removed from Shortlist",
        "bookmark_remove",
        "tq-tl-warning",
    ),
    "MOVED_TO_INTERVIEW": ("Moved to Interview", "record_voice_over", "tq-tl-primary"),
    "MARKED_SELECTED": ("Selected", "task_alt", "tq-tl-success"),
    "MARKED_REJECTED": ("Rejected", "block", "tq-tl-danger"),
}

_TIMELINE_CSS = """
<style>
  .tq-timeline { position: relative; margin: 0.4rem 0 0.2rem; padding-left: 1.5rem; }
  .tq-timeline::before {
    content: ""; position: absolute; left: 7px; top: 6px; bottom: 6px; width: 2px;
    background: linear-gradient(180deg, rgba(124,58,237,0.55), rgba(30,41,59,0.9));
  }
  .tq-tl-item { position: relative; padding: 0.55rem 0.65rem 0.55rem 0.9rem; }
  .tq-tl-dot {
    position: absolute; left: -1.42rem; top: 0.9rem; width: 12px; height: 12px;
    border-radius: 50%; background: #0D1422; border: 2px solid var(--tq-primary);
    box-shadow: 0 0 0 3px rgba(7,11,20,0.9);
  }
  .tq-tl-dot.tq-tl-dot-success { border-color: var(--tq-success); }
  .tq-tl-dot.tq-tl-dot-warning { border-color: var(--tq-warning); }
  .tq-tl-dot.tq-tl-dot-danger  { border-color: var(--tq-danger); }
  .tq-tl-dot.tq-tl-dot-neutral { border-color: var(--tq-muted); }
  .tq-tl-item:not(:last-child) { border-bottom: 1px dashed rgba(30,41,59,0.75); }
  .tq-tl-title {
    display: flex; align-items: center; gap: 0.45rem; font-weight: 600;
    font-size: 0.95rem; color: #F8FAFC; margin: 0 0 0.1rem;
  }
  .tq-tl-time { font-size: 0.8rem; color: var(--tq-muted); margin: 0; }
  .tq-tl-desc { font-size: 0.85rem; color: #94A3B8; margin: 0.15rem 0 0; }
</style>
"""


def format_activity_timestamp(value) -> str:
    """Format an aware-UTC timestamp in the reader's local timezone."""
    now_local = value
    if now_local is None:
        return ""
    if getattr(now_local, "tzinfo", None) is None:
        # Defensive: treat naive stored datetimes as UTC (the model default).
        try:
            from datetime import timezone

            now_local = now_local.replace(tzinfo=timezone.utc)
        except Exception:
            return str(value)
    try:
        now_local = now_local.astimezone()
    except Exception:
        return str(value)
    return now_local.strftime("%b %d, %Y  \u00b7  %I:%M %p")


def activity_title(activity) -> str:
    title, _icon, _tone = ACTIVITY_META.get(
        activity.activity_type, ("Status Changed", "swap_horiz", "tq-tl-neutral")
    )
    if activity.activity_type == "STATUS_CHANGED" and activity.new_status:
        return f"Status Changed  \u00b7 {activity.new_status}"
    return title


def render_activity_timeline(activities, limit: int = 20, empty_message: str = "No recorded activity for this candidate yet.") -> None:
    """Render up to ``limit`` activities (newest first) as a vertical timeline."""
    if not activities:
        st.caption(empty_message)
        return

    st.markdown(_TIMELINE_CSS, unsafe_allow_html=True)

    items = []
    for activity in list(activities)[:limit]:
        title, icon, tone = ACTIVITY_META.get(
            activity.activity_type, ("Status Changed", "swap_horiz", "tq-tl-neutral")
        )
        if activity.activity_type == "STATUS_CHANGED" and activity.new_status:
            title = f"Status Changed \u00b7 {activity.new_status}"
        dot_tone = {
            "tq-tl-success": "tq-tl-dot-success",
            "tq-tl-warning": "tq-tl-dot-warning",
            "tq-tl-danger": "tq-tl-dot-danger",
            "tq-tl-neutral": "tq-tl-dot-neutral",
        }.get(tone, "")
        desc = activity.description or ""
        items.append(
            f"""
            <div class="tq-tl-item">
              <div class="tq-tl-dot {dot_tone}"></div>
              <p class="tq-tl-title">
                <span class="material-symbols-outlined" style="font-size:1.05rem;color:{'var(--tq-primary-soft)' if tone == 'tq-tl-primary' else 'var(--tq-muted)'};">{icon}</span>
                {escape(title)}
              </p>
              <p class="tq-tl-time">{escape(format_activity_timestamp(activity.created_at))}</p>
              {'<p class="tq-tl-desc">' + escape(desc) + '</p>' if desc else ''}
            </div>
            """
        )

    st.markdown(
        f'<div class="tq-timeline">{"".join(items)}</div>',
        unsafe_allow_html=True,
    )