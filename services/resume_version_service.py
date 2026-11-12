"""Persistence service: resume version history for a candidate.

Append-only, per-candidate numbered versions of the resume document. Analysis
snapshots reference a specific version, so an improved resume creates a NEW
version + analysis instead of overwriting history. Only metadata + a
deterministic SHA-256 hash are stored — never the resume text, never duplicated
candidate identity.

Versioning rules
----------------
- Identity: ``(candidate_id, file_hash)`` — re-uploading byte-identical content
  reuses the existing version (no "Version 2" for the same file, no duplicate
  version activity).
- Numbering: 1, 2, 3 ... assigned from ``max(version_number) + 1``. Versions
  are never deleted by recruiter actions, so numbers stay stable and old
  versions are never renumbered.
- A new resume version NEVER changes the candidate's recruitment status:
  document history and recruiter decisions are independent.

UI keeps thin: pages call these functions; no version logic lives in pages.
"""

from sqlalchemy import func

from core.db import session_scope
from core.models import Candidate, ResumeVersion
from services import audit_service
from services.auth_service import can_access


def _require_candidate(
    session, candidate_id: int, user: dict | None = None
) -> Candidate:
    """Fetch the candidate and enforce visibility (raises when blocked)."""
    candidate = session.get(Candidate, candidate_id)
    if candidate is None:
        raise LookupError(f"No candidate with id {candidate_id}")
    if not can_access(user, candidate.created_by_user_id):
        raise PermissionError("You do not have access to this candidate.")
    return candidate


def create_or_get_resume_version(
    *,
    candidate_id: int,
    file_hash: str,
    filename: str | None = None,
    user: dict | None = None,
) -> ResumeVersion:
    """Return the existing version for ``(candidate, file_hash)`` or create the
    next numbered version (existing rows are returned untouched, so identical
    re-uploads never produce duplicates or extra version rows)."""
    with session_scope() as session:
        candidate = _require_candidate(session, candidate_id, user)

        existing = (
            session.query(ResumeVersion)
            .filter(
                ResumeVersion.candidate_id == candidate_id,
                ResumeVersion.file_hash == (file_hash or ""),
            )
            .first()
        )
        if existing is not None:
            return existing

        next_number = (
            int(
                session.query(func.max(ResumeVersion.version_number))
                .filter(ResumeVersion.candidate_id == candidate_id)
                .scalar()
                or 0
            )
            + 1
        )
        version = ResumeVersion(
            candidate_id=candidate_id,
            version_number=next_number,
            filename=filename or "",
            file_hash=file_hash or "",
        )
        session.add(version)
        session.flush()
        actor_user_id = (
            user.get("id") if user is not None else candidate.created_by_user_id
        )
        audit_service.record_audit_event(
            actor_user_id,
            audit_service.RESUME_VERSION_CREATED,
            audit_service.RESOURCE_RESUME_VERSION,
            version.id,
            f"Resume Version {next_number} created for candidate {candidate_id}.",
            session=session,
        )
        return version


def get_resume_versions(
    candidate_id: int, *, newest_first: bool = True, user: dict | None = None
):
    """All versions for a visible candidate, newest (highest number) first by
    default. Returns ``[]`` when the candidate is unknown/invisible."""
    with session_scope() as session:
        try:
            _require_candidate(session, candidate_id, user)
        except (LookupError, PermissionError):
            return []
        query = session.query(ResumeVersion).filter(
            ResumeVersion.candidate_id == candidate_id
        )
        order = (
            ResumeVersion.version_number.desc()
            if newest_first
            else ResumeVersion.version_number.asc()
        )
        return query.order_by(order).all()


def get_resume_version(version_id: int, user: dict | None = None):
    """One version by its id if its candidate is visible to ``user``, or None."""
    with session_scope() as session:
        version = session.get(ResumeVersion, version_id)
        if version is None:
            return None
        try:
            _require_candidate(session, version.candidate_id, user)
        except (LookupError, PermissionError):
            return None
        return version


def get_resume_version_by_number(
    candidate_id: int, version_number: int, user: dict | None = None
):
    """One version by (candidate, stable version number) if visible, or None."""
    with session_scope() as session:
        try:
            _require_candidate(session, candidate_id, user)
        except (LookupError, PermissionError):
            return None
        return (
            session.query(ResumeVersion)
            .filter(
                ResumeVersion.candidate_id == candidate_id,
                ResumeVersion.version_number == int(version_number),
            )
            .first()
        )


def get_latest_resume_version(
    candidate_id: int, user: dict | None = None
):
    """The candidate's current (highest-numbered) resume version, or None."""
    with session_scope() as session:
        try:
            _require_candidate(session, candidate_id, user)
        except (LookupError, PermissionError):
            return None
        return (
            session.query(ResumeVersion)
            .filter(ResumeVersion.candidate_id == candidate_id)
            .order_by(ResumeVersion.version_number.desc())
            .first()
        )


def resume_version_count(candidate_id: int, user: dict | None = None) -> int:
    """Number of distinct resume versions for a visible candidate."""
    with session_scope() as session:
        try:
            _require_candidate(session, candidate_id, user)
        except (LookupError, PermissionError):
            return 0
        return (
            session.query(func.count(ResumeVersion.id))
            .filter(ResumeVersion.candidate_id == candidate_id)
            .scalar()
            or 0
        )


def resume_version_snapshot(version, *, latest_number: int | None = None) -> dict:
    """Compact dict for UI lists/tables (no identity duplication).

    ``is_current`` is derived by comparing to the candidate's highest version
    number — a display property, never a stored flag.
    """
    if version is None:
        return None
    if latest_number is None:
        latest_number = version.version_number
    return {
        "id": version.id,
        "version_number": version.version_number,
        "filename": version.filename,
        "file_hash": version.file_hash,
        "created_at": version.created_at,
        "is_current": version.version_number == latest_number,
    }