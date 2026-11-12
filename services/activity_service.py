"""Persistence service: candidate activity timeline.

Responsibilities
----------------
- Provide the controlled activity vocabulary (``ACTIVITY_TYPES``) and helpers
  that map a status transition to a meaningful event type + human description.
- Append immutable activity rows **inside the caller's transaction** so a status
  change and its activity are atomic: either both land or neither does.
- Query a candidate's timeline (newest -> oldest) for the detail/result UI.

Important
---------
- Activities are append-only: recruiter actions never edit or delete them, so
  removing a candidate from the shortlist still leaves the historical
  "Shortlisted" event visible.
- The database is the source of truth; activities are never cached in Streamlit
  session state as authoritative data.
- No candidate identity/scores are duplicated onto activity rows — only the
  foreign key and event metadata.
"""

import logging

from core.db import session_scope
from core.models import ACTIVITY_TYPES, Candidate, CandidateActivity
from services.auth_service import can_access

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIVITY_TYPES",
    "activity_type_for_status",
    "activity_description_for_status",
    "log_activity",
    "log_status_activity",
    "get_candidate_activity",
]


def is_valid_activity_type(activity_type: str) -> bool:
    return activity_type in ACTIVITY_TYPES


def activity_type_for_status(new_status: str) -> str:
    """Map a target status to its specific activity type.

    Only the meaningful transitions get a dedicated type; anything else falls
    back to ``STATUS_CHANGED`` so no unnecessary event types are invented.
    """
    return {
        "Shortlisted": "SHORTLISTED",
        "Interview": "MOVED_TO_INTERVIEW",
        "Selected": "MARKED_SELECTED",
        "Rejected": "MARKED_REJECTED",
    }.get(new_status, "STATUS_CHANGED")


def activity_description_for_status(previous_status: str | None, new_status: str) -> str:
    """Short human description shown under the timeline entry."""
    specific = {
        "Shortlisted": "Candidate shortlisted",
        "Interview": "Candidate advanced to interview",
        "Selected": "Candidate marked selected",
        "Rejected": "Candidate marked rejected",
    }.get(new_status)
    if specific:
        return specific
    if previous_status:
        return f"Candidate moved from {previous_status} to {new_status}"
    return f"Candidate set to {new_status}"


def log_activity(
    session,
    *,
    candidate_id: int,
    activity_type: str,
    previous_status: str | None = None,
    new_status: str | None = None,
    description: str | None = None,
    actor_user_id: int | None = None,
):
    """Append one activity row on the caller's session (no commit).

    The caller owns the surrounding transaction so the activity lands in the
    same BEGIN/COMMIT as the change that caused it (or rolls back together).
    ``actor_user_id`` records which account performed the action.
    """
    session.add(
        CandidateActivity(
            candidate_id=candidate_id,
            actor_user_id=actor_user_id,
            activity_type=activity_type,
            previous_status=previous_status,
            new_status=new_status,
            description=(description or None),
        )
    )
    return None


def log_status_activity(
    session,
    *,
    candidate_id: int,
    previous_status: str | None,
    new_status: str,
    actor_user_id: int | None = None,
):
    """Record a status transition as a specific activity event."""
    return log_activity(
        session,
        candidate_id=candidate_id,
        activity_type=activity_type_for_status(new_status),
        previous_status=previous_status,
        new_status=new_status,
        description=activity_description_for_status(previous_status, new_status),
        actor_user_id=actor_user_id,
    )


def _candidate_visible(session, candidate_id: int, user: dict | None) -> bool:
    """Can ``user`` see this candidate's timeline? (Unknown -> False.)"""
    candidate = session.get(Candidate, candidate_id)
    if candidate is None:
        return False
    return can_access(user, candidate.created_by_user_id)


def get_candidate_activity(
    candidate_id: int, limit: int = 50, user: dict | None = None
):
    """One candidate's activity timeline, newest -> oldest (most recent first).

    Returns ``[]`` when the candidate is unknown or invisible to ``user``.
    Only fetched when the UI actually needs it (a selected candidate), never
    for every candidate on every rerun.
    """
    with session_scope() as session:
        if not _candidate_visible(session, candidate_id, user):
            return []
        return (
            session.query(CandidateActivity)
            .filter(CandidateActivity.candidate_id == candidate_id)
            .order_by(CandidateActivity.created_at.desc(), CandidateActivity.id.desc())
            .limit(limit)
            .all()
        )


def activity_count(candidate_id: int, user: dict | None = None) -> int:
    with session_scope() as session:
        if not _candidate_visible(session, candidate_id, user):
            return 0
        return (
            session.query(CandidateActivity)
            .filter(CandidateActivity.candidate_id == candidate_id)
            .count()
        )
