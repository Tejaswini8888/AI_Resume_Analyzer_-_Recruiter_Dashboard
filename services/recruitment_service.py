"""Persistence service: recruitment workflow (status, shortlist, JD).

Responsibilities
----------------
- Manage the controlled recruitment status vocabulary on ``Candidate``.
- Persist manual shortlist actions: ``Shortlisted`` on, back to ``Analyzed``
  when removed. Candidates are never deleted, and their scores are never
  touched here.
- Save/deduplicate job descriptions by content hash.

Important
---------
- A score alone NEVER changes a candidate's status. "Score-Eligible" is a
  computed property of the analysis, not a status.
- ``mark_analyzed()`` is the only automatic transition and it only promotes
  ``New -> Analyzed`` after a real analysis; manual statuses are preserved.
"""

import hashlib
import logging

from sqlalchemy import func

from core.db import session_scope
from core.models import (
    RECRUITMENT_STATUSES,
    Candidate,
    CandidateStatusHistory,
    JobDescription,
)
from services import activity_service, audit_service
from services.auth_service import can_access, owner_scope

logger = logging.getLogger(__name__)


def is_valid_status(status: str) -> bool:
    return status in RECRUITMENT_STATUSES


def _require_candidate(session, candidate_id: int, user: dict | None) -> Candidate:
    """Fetch a candidate and enforce visibility; raises if unknown/inaccessible."""
    candidate = session.get(Candidate, candidate_id)
    if candidate is None:
        raise LookupError(f"No candidate with id {candidate_id}")
    if not can_access(user, candidate.created_by_user_id):
        raise PermissionError("You do not have access to this candidate.")
    return candidate


def _actor_user_id(user: dict | None) -> int | None:
    """Account id recorded as the actor (None for system/legacy actions)."""
    return user.get("id") if user is not None else None


def _status_audit_action(status: str) -> str:
    """Map a recruitment status to its canonical audit event code."""
    return {
        "Shortlisted": audit_service.CANDIDATE_SHORTLISTED,
        "Rejected": audit_service.CANDIDATE_REJECTED,
        "Selected": audit_service.CANDIDATE_SELECTED,
    }.get(status, audit_service.CANDIDATE_STATUS_CHANGED)


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------
def _record_transition(session, candidate: Candidate, previous_status, status: str, user: dict | None = None):
    """Append an audit row for an actual status change."""
    session.add(
        CandidateStatusHistory(
            candidate_id=candidate.id,
            actor_user_id=_actor_user_id(user),
            previous_status=previous_status,
            status=status,
        )
    )


def update_candidate_status(candidate_id: int, status: str, user: dict | None = None):
    """Set a candidate's recruitment status (controlled transitions).

    Only the six controlled statuses are allowed; the workflow is deliberately
    NOT blocked from moving a candidate backward (e.g. ``Interview`` ->
    ``Shortlisted``) because recruiters change their minds. A no-op (setting
    the status it already has) records nothing. Scores are never touched here.
    """
    if not is_valid_status(status):
        raise ValueError(
            f"Unknown recruitment status {status!r}. "
            f"Valid values: {', '.join(RECRUITMENT_STATUSES)}"
        )
    with session_scope() as session:
        candidate = _require_candidate(session, candidate_id, user)
        if candidate.status != status:
            previous_status = candidate.status
            _record_transition(session, candidate, previous_status, status, user)
            candidate.status = status
            # Same transaction as the status change: the activity AND the
            # system/security audit log commit (or roll back) together.
            activity_service.log_status_activity(
                session,
                candidate_id=candidate_id,
                previous_status=previous_status,
                new_status=status,
                actor_user_id=_actor_user_id(user),
            )
            audit_service.record_audit_event(
                _actor_user_id(user),
                _status_audit_action(status),
                audit_service.RESOURCE_CANDIDATE,
                candidate_id,
                f"Candidate moved to {status}.",
                session=session,
            )
        return candidate


def update_status(candidate_id: int, status: str, user: dict | None = None):
    """Backwards-compatible alias for ``update_candidate_status``."""
    return update_candidate_status(candidate_id, status, user=user)


def mark_analyzed(candidate_id: int, user: dict | None = None):
    """Promote ``New -> Analyzed`` only. Manual statuses are never overwritten."""
    with session_scope() as session:
        candidate = _require_candidate(session, candidate_id, user)
        if candidate.status == "New":
            _record_transition(session, candidate, "New", "Analyzed", user)
            candidate.status = "Analyzed"
        return candidate


def shortlist_candidate(candidate_id: int, user: dict | None = None):
    """Manually shortlist a candidate (status -> ``Shortlisted``)."""
    return update_candidate_status(candidate_id, "Shortlisted", user=user)


def remove_from_shortlist(candidate_id: int, user: dict | None = None):
    """Un-shortlist a candidate; status goes back to ``Analyzed``.

    The candidate, their analyses and all scores are untouched. Clean rule:
    ``Shortlisted -> Analyzed``. Statuses other than ``Shortlisted`` (e.g.
    Interview/Selected) are left alone so a recruiter's deeper decision is
    never silently rolled back.
    """
    with session_scope() as session:
        candidate = _require_candidate(session, candidate_id, user)
        if candidate.status == "Shortlisted":
            _record_transition(session, candidate, "Shortlisted", "Analyzed", user)
            candidate.status = "Analyzed"
            # Separate recruiter event; the historical "Shortlisted" activity
            # stays untouched (immutable timeline).
            activity_service.log_activity(
                session,
                candidate_id=candidate_id,
                activity_type="REMOVED_FROM_SHORTLIST",
                previous_status="Shortlisted",
                new_status="Analyzed",
                description="Candidate moved back to Analyzed",
                actor_user_id=_actor_user_id(user),
            )
            audit_service.record_audit_event(
                _actor_user_id(user),
                audit_service.CANDIDATE_STATUS_CHANGED,
                audit_service.RESOURCE_CANDIDATE,
                candidate_id,
                "Candidate removed from shortlist.",
                session=session,
            )
        return candidate


def get_candidate_status(candidate_id: int, user: dict | None = None) -> str | None:
    with session_scope() as session:
        try:
            candidate = _require_candidate(session, candidate_id, user)
        except (LookupError, PermissionError):
            return None
        return candidate.status


def get_candidates_by_status(
    status: str, limit: int | None = None, user: dict | None = None
):
    """Candidates currently in the given controlled status and visible to user."""
    if not is_valid_status(status):
        raise ValueError(
            f"Unknown recruitment status {status!r}. "
            f"Valid values: {', '.join(RECRUITMENT_STATUSES)}"
        )
    with session_scope() as session:
        query = session.query(Candidate).filter(Candidate.status == status)
        scope = owner_scope(Candidate, user)
        if scope is not None:
            query = query.filter(scope)
        query = query.order_by(Candidate.updated_at.desc())
        if limit is not None:
            query = query.limit(limit)
        return query.all()


def get_shortlisted_candidates(user: dict | None = None):
    """Candidates whose manual recruitment status is ``Shortlisted``."""
    return get_candidates_by_status("Shortlisted", user=user)


def get_pipeline_counts(user: dict | None = None):
    """Real, database-derived counts per controlled status (never mocked)."""
    with session_scope() as session:
        query = session.query(Candidate.status, func.count(Candidate.id))
        scope = owner_scope(Candidate, user)
        if scope is not None:
            query = query.filter(scope)
        rows = query.group_by(Candidate.status).all()
    counts = {status: 0 for status in RECRUITMENT_STATUSES}
    for status, count in rows:
        if status in counts:
            counts[status] = int(count)
    counts["All"] = sum(counts.values())
    return counts


def get_status_history(candidate_id: int, limit: int = 50, user: dict | None = None):
    """Most recent recorded status transitions for a visible candidate, newest first."""
    with session_scope() as session:
        try:
            _require_candidate(session, candidate_id, user)
        except (LookupError, PermissionError):
            return []
        return (
            session.query(CandidateStatusHistory)
            .filter(CandidateStatusHistory.candidate_id == candidate_id)
            .order_by(CandidateStatusHistory.created_at.desc())
            .limit(limit)
            .all()
        )


def get_candidate_detail(candidate_id: int, user: dict | None = None):
    """Candidate identity + current status + latest analysis (no duplicates).

    Returns ``None`` for an unknown candidate (or one invisible to ``user``).
    ``latest_analysis`` carries the most recent analysis row with its ``jd_id``
    linked back to the job description used, so one candidate may be analyzed
    against many jobs over time without duplicating the candidate record.
    """
    from services.analysis_service import get_latest_analysis

    with session_scope() as session:
        candidate = session.get(Candidate, candidate_id)
        if candidate is None or not can_access(user, candidate.created_by_user_id):
            return None
        snapshot = {
            "id": candidate.id,
            "name": candidate.name,
            "email": candidate.email,
            "phone": candidate.phone,
            "location": candidate.location,
            "resume_filename": candidate.resume_filename,
            "status": candidate.status,
            "created_at": candidate.created_at,
            "updated_at": candidate.updated_at,
        }
    return {
        "candidate": snapshot,
        "status": snapshot["status"],
        "latest_analysis": get_latest_analysis(candidate_id),
    }


# ---------------------------------------------------------------------------
# Job descriptions (optional title, dedup by content)
# ---------------------------------------------------------------------------
def _jd_hash(description: str) -> str:
    return hashlib.sha256((description or "").strip().encode("utf-8")).hexdigest()


def save_job_description(
    description: str,
    title: str | None = None,
    created_by_user_id: int | None = None,
):
    """Persist a job description, reusing the existing row if identical.

    Returns the ``JobDescription`` row (committed). Reuse is OWNER-SCOPED:
    an existing row is only returned when it belongs to the same owner, or is a
    legacy/system-owned (``NULL``-owner) shared record. Two recruiters who paste
    the same text get SEPARATE JD rows (isolation), so a recruiter can never
    retro-link their analyses to another recruiter's job description.
    ``created_by_user_id`` is recorded ONLY on a brand-new row so reuse never
    transfers ownership between recruiters.
    """
    description = (description or "").strip()
    if not description:
        return None
    content_hash = _jd_hash(description)
    with session_scope() as session:
        existing = (
            session.query(JobDescription)
            .filter(JobDescription.content_hash == content_hash)
            .order_by(JobDescription.created_at.desc())
            .first()
        )
        if existing is not None and (
            existing.created_by_user_id is None
            or existing.created_by_user_id == created_by_user_id
        ):
            if title and not existing.title:
                existing.title = title[:255]
                audit_service.record_audit_event(
                    created_by_user_id,
                    audit_service.JOB_DESCRIPTION_UPDATED,
                    audit_service.RESOURCE_JOB_DESCRIPTION,
                    existing.id,
                    "Job description title updated.",
                    session=session,
                )
            return existing
        job = JobDescription(
            title=(title or None)[:255] if title else None,
            description=description,
            content_hash=content_hash,
            created_by_user_id=created_by_user_id,
        )
        session.add(job)
        session.flush()
        audit_service.record_audit_event(
            created_by_user_id,
            audit_service.JOB_DESCRIPTION_CREATED,
            audit_service.RESOURCE_JOB_DESCRIPTION,
            job.id,
            "Job description created.",
            session=session,
        )
        return job


def get_job_description(jd_id: int, user: dict | None = None):
    """One job description visible to ``user``, or ``None``."""
    with session_scope() as session:
        jd = session.get(JobDescription, jd_id)
        if jd is None or not can_access(user, jd.created_by_user_id):
            return None
        return jd


def get_job_descriptions(user: dict | None = None):
    """Job descriptions visible to ``user``, newest first."""
    with session_scope() as session:
        query = session.query(JobDescription)
        scope = owner_scope(JobDescription, user)
        if scope is not None:
            query = query.filter(scope)
        return query.order_by(JobDescription.created_at.desc()).all()