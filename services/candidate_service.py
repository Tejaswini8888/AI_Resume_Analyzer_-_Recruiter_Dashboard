"""Persistence service: candidate identity.

Responsibilities
----------------
- Create or reuse a candidate record, keyed by a deterministic SHA-256 hash of
  the resume file bytes (never by filename).
- Retrieve candidates individually or as a list.
- Expose the hashing helpers so pages avoid computing hashes differently.

Design notes
------------
- Re-uploading the same resume reuses the existing candidate (contact fields are
  back-filled only when empty) instead of creating duplicates.
- Phase 4 ownership: a recruiter adds ``created_by_user_id`` so the candidate is
  scoped to their account; legacy ``NULL``-owner rows stay visible to every
  recruiter. ``user`` params (a dict from ``auth_service``) restrict reads to
  the owner + legacy pool; ``None`` keeps the historical unrestricted behavior.
- Services own all SQLAlchemy access; Streamlit pages never touch models.
- No raw resume text is persisted.
"""

import logging

from core.db import session_scope
from core.models import Candidate
from services import audit_service
from services.auth_service import can_access, owner_scope
from services.file_security_service import calculate_file_hash, calculate_file_hash_file

logger = logging.getLogger(__name__)


def resume_hash_bytes(data: bytes) -> str:
    """Deterministic SHA-256 of resume file bytes (cross-platform stable)."""
    return calculate_file_hash(data)


def resume_hash_file(file_object) -> str:
    """Hash a Streamlit upload (``UploadedFile``/``BytesIO``) without mutating it."""
    return calculate_file_hash_file(file_object)


def find_candidate_by_resume_hash(resume_hash: str, user: dict | None = None):
    """Return the candidate visible to ``user`` for a resume hash, or ``None``.

    Anonymous/public callers (``user=None``) may only resolve candidates from
    the shared public pool (``created_by_user_id IS NULL``) so the public
    self-analysis flow can never read or act on a recruiter's owned candidate.
    """
    with session_scope() as session:
        query = session.query(Candidate).filter(Candidate.resume_hash == resume_hash)
        scope = owner_scope(Candidate, user)
        if scope is not None:
            query = query.filter(scope)
        elif user is None:
            query = query.filter(Candidate.created_by_user_id.is_(None))
        return query.order_by(Candidate.updated_at.desc()).first()


def create_candidate(
    *,
    resume_hash: str,
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    location: str | None = None,
    resume_filename: str | None = None,
    status: str = "New",
    created_by_user_id: int | None = None,
):
    """Create a candidate, or reuse the existing one for ``resume_hash``.

    ``status`` may be any value from ``RECRUITMENT_STATUSES`` (defaults to
    "New"). When the resume was already seen, identity fields are only
    back-filled if currently empty so re-analysis never loses data.

    Reuse is OWNER-SCOPED: an existing candidate is only reused when it belongs
    to the same owner, or when it is a legacy/system-owned (``NULL``-owner)
    shared record. The same resume uploaded by a recruiter AND analysed by the
    anonymous public flow therefore yields separate, isolated candidate rows —
    the anonymous flow can never hijack a recruiter's candidate. The owner is
    only ever SET at creation; reusing an existing candidate never transfers
    ownership from a recruiter who already claimed it.
    """
    with session_scope() as session:
        existing = (
            session.query(Candidate)
            .filter(Candidate.resume_hash == resume_hash)
            .order_by(Candidate.updated_at.desc())
            .first()
        )
        if existing is not None and (
            existing.created_by_user_id is None
            or existing.created_by_user_id == created_by_user_id
        ):
            if name and not existing.name:
                existing.name = name
            if email and not existing.email:
                existing.email = email
            if phone and not existing.phone:
                existing.phone = phone
            if location and not existing.location:
                existing.location = location
            existing.resume_filename = resume_filename or existing.resume_filename
            return existing

        candidate = Candidate(
            name=name or "",
            email=email,
            phone=phone,
            location=location,
            resume_filename=resume_filename,
            resume_hash=resume_hash,
            status=status,
            created_by_user_id=created_by_user_id,
        )
        session.add(candidate)
        session.flush()
        audit_service.record_audit_event(
            created_by_user_id,
            audit_service.CANDIDATE_CREATED,
            audit_service.RESOURCE_CANDIDATE,
            candidate.id,
            "Candidate created.",
            session=session,
        )
        return candidate


def get_candidate(candidate_id: int, user: dict | None = None):
    """Return one candidate by id if visible to ``user``, or ``None``."""
    with session_scope() as session:
        candidate = session.get(Candidate, candidate_id)
        if candidate is None or not can_access(user, candidate.created_by_user_id):
            return None
        return candidate


def get_candidates(user: dict | None = None):
    """Candidates visible to ``user``, newest first (identity/contact only)."""
    with session_scope() as session:
        query = session.query(Candidate)
        scope = owner_scope(Candidate, user)
        if scope is not None:
            query = query.filter(scope)
        return query.order_by(Candidate.created_at.desc()).all()