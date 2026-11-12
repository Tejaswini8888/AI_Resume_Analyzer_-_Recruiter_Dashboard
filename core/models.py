"""SQLAlchemy models for persistent TalentIQ recruitment data.

Entities, one concern each:

- ``User``                — a platform account (recruiter/admin) used for
                            authentication and record ownership.
- ``Candidate``           — person identity + current recruitment status.
- ``CandidateStatusHistory`` — one row per status transition (previous/next/
                         timestamp) so recruiter decisions are auditable.
- ``CandidateActivity``   — the user-facing activity timeline: one row per
                         meaningful event (analysis, shortlist, status moves),
                         each with a type, optional previous/new status and a
                         human description. Append-only and immutable — never
                         edited or deleted by recruiter actions.
- ``ResumeAnalysis``     — a single analysis result, linked to a candidate and
                         (optionally) a job description. No identity fields are
                         duplicated here.
- ``JobDescription``     — the role text used for matching (deduplicated by
                         content hash; title is optional).
- ``AuditLog``           — a central system/security record of who did what,
                         when, and to which resource (admin-only visibility).
                         Deliberately separate from ``CandidateActivity``.
- ``RecruitmentStatus``  values — a controlled vocabulary enforced by a CHECK
                         constraint so a score can never be stored as a status.

Ownership (Phase 4)
-------------------
Records created by an account carry ``created_by_user_id`` so one recruiter's
candidate pool and job descriptions are isolated from another's at the query
layer. ``NULL`` means "legacy / system-owned" (created before accounts existed,
or by the public self-analysis flow): those shared records stay visible to every
authenticated recruiter so pre-Phase-4 data is neither deleted nor fabricated an
owner. ``CandidateStatusHistory.actor_user_id`` and
``CandidateActivity.actor_user_id`` record WHO performed an action for audit.

No raw resume text, no sensitive PII beyond what the resume parser already
surfaces (name/email/phone/location are stored so identity is useful, which is
what the existing UI already displays).
"""

import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

# Controlled platform roles (Phase 4). Extensible: add new tuples + a CHECK
# constraint; never free-text roles.
USER_ROLES = (
    "RECRUITER",
    "ADMIN",
)

_ROLE_CHECK = "role IN ('RECRUITER', 'ADMIN')"

# Controlled recruitment workflow vocabulary. "Score-Eligible" is deliberately
# NOT here: it is a score property, never a status.
RECRUITMENT_STATUSES = (
    "New",
    "Analyzed",
    "Shortlisted",
    "Interview",
    "Selected",
    "Rejected",
)

_STATUS_CHECK = "status IN ('New', 'Analyzed', 'Shortlisted', 'Interview', 'Selected', 'Rejected')"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


class User(Base):
    """One TalentIQ account (recruiter/admin).

    Only a password hash is stored — never the plaintext password. ``email``
    is normalized to lowercase at the service layer and unique across users so
    it can double as the login identifier. ``is_active`` lets an admin
    deactivate an account without deleting its records (data is never wiped).
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_ROLE_CHECK, name="ck_user_role"),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, default="")
    email = Column(String(320), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False, default="RECRUITER")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def __repr__(self):  # pragma: no cover - debugging aid only
        return f"<User id={self.id} email={self.email!r} role={self.role!r}>"


class Candidate(Base):
    """One candidate identity. A candidate exists independently of analyses."""

    __tablename__ = "candidates"
    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="ck_candidate_status"),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(200), default="", nullable=False, index=True)
    email = Column(String(320), nullable=True, index=True)
    phone = Column(String(50), nullable=True)
    location = Column(String(255), nullable=True)
    resume_filename = Column(String(500), nullable=True)
    # SHA-256 of the resume file bytes as a cross-upload identity helper.
    # Deliberately NOT unique: the same resume uploaded as an anonymous / public
    # analysis AND by one or more recruiters must yield separate, owner-scoped
    # Candidate rows (isolation fix). Ownership is enforced at the service layer.
    resume_hash = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="New", index=True)
    # Owning account; NULL = legacy/system-owned (shared across recruiters).
    created_by_user_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, index=True)

    analyses = relationship(
        "ResumeAnalysis",
        back_populates="candidate",
        cascade="all, delete-orphan",
        order_by="ResumeAnalysis.created_at.desc()",
    )
    status_history = relationship(
        "CandidateStatusHistory",
        back_populates="candidate",
        cascade="all, delete-orphan",
        order_by="CandidateStatusHistory.created_at.asc()",
    )
    activities = relationship(
        "CandidateActivity",
        back_populates="candidate",
        cascade="all, delete-orphan",
        order_by="CandidateActivity.created_at.asc()",
    )
    resume_versions = relationship(
        "ResumeVersion",
        back_populates="candidate",
        cascade="all, delete-orphan",
        order_by="ResumeVersion.version_number.asc()",
    )

    def __repr__(self):  # pragma: no cover - debugging aid only
        return f"<Candidate id={self.id} name={self.name!r} status={self.status!r}>"


# Controlled activity vocabulary. RESUME_ANALYZED is the only system-generated
# event; everything else is a recruiter decision. Specific status events map to
# a dedicated type to keep the timeline readable; any other (valid) transition
# falls back to STATUS_CHANGED.
ACTIVITY_TYPES = (
    "RESUME_ANALYZED",
    "STATUS_CHANGED",
    "SHORTLISTED",
    "REMOVED_FROM_SHORTLIST",
    "MOVED_TO_INTERVIEW",
    "MARKED_SELECTED",
    "MARKED_REJECTED",
)


class CandidateStatusHistory(Base):
    """A single recorded recruitment status transition for a candidate.

    ``previous_status`` may be ``None`` when the very first status was set.
    Rows are append-only: they record what happened and when, without ever
    mutating the candidate's current status."""
    __tablename__ = "candidate_status_history"

    id = Column(Integer, primary_key=True)
    candidate_id = Column(
        Integer, ForeignKey("candidates.id"), nullable=False, index=True
    )
    # Which account performed the transition (NULL = legacy/system action).
    actor_user_id = Column(Integer, nullable=True, index=True)
    previous_status = Column(String(32), nullable=True)
    status = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)

    candidate = relationship("Candidate", back_populates="status_history")

    def __repr__(self):  # pragma: no cover - debugging aid only
        return (
            f"<CandidateStatusHistory id={self.id} candidate={self.candidate_id} "
            f"{self.previous_status!r} -> {self.status!r}>"
        )


class CandidateActivity(Base):
    """One immutable, user-facing activity event for a candidate.

    Stores no candidate identity (name, scores, contact) — only the foreign key,
    the event type, the optional status transition, a short human description and
    an aware-UTC timestamp. Rows are never updated or deleted by recruiter
    actions; the current status lives on ``Candidate``, the history lives here.
    """

    __tablename__ = "candidate_activity"
    __table_args__ = (
        CheckConstraint(
            "activity_type IN ('RESUME_ANALYZED', 'STATUS_CHANGED', "
            "'SHORTLISTED', 'REMOVED_FROM_SHORTLIST', 'MOVED_TO_INTERVIEW', "
            "'MARKED_SELECTED', 'MARKED_REJECTED')",
            name="ck_candidate_activity_type",
        ),
    )

    id = Column(Integer, primary_key=True)
    candidate_id = Column(
        Integer, ForeignKey("candidates.id"), nullable=False, index=True
    )
    # Which account performed the action (NULL for legacy/system events).
    actor_user_id = Column(Integer, nullable=True, index=True)
    activity_type = Column(String(50), nullable=False)
    previous_status = Column(String(32), nullable=True)
    new_status = Column(String(32), nullable=True)
    description = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)

    candidate = relationship("Candidate", back_populates="activities")

    def __repr__(self):  # pragma: no cover - debugging aid only
        return (
            f"<CandidateActivity id={self.id} candidate={self.candidate_id} "
            f"type={self.activity_type!r} {self.previous_status!r}->{self.new_status!r}>"
        )


class JobDescription(Base):
    """A job description that resumes are matched against.

    Rules enforced here:
    - ``(content_hash, created_by_user_id)`` is unique so identical text
      pasted by the SAME recruiter reuses one row, while two different
      recruiters pasting the same text each get their own row and can never
      retro-link each other's analyses. A global UNIQUE on ``content_hash``
      alone made that second recruiter's save fail with an IntegrityError, so
      uniqueness is deliberately owner-scoped.
    - That rule is declared as a named standalone ``Index`` rather than a
      ``UniqueConstraint`` on purpose: SQLite backs a table constraint with an
      anonymous ``sqlite_autoindex_*`` entry that can NEVER be dropped, which
      would make the pre-Phase-4 global constraint unremovable by migration.
      A named unique index can be created, inspected and dropped normally.
    - The composite index's leftmost column is ``content_hash``, so it also
      serves the service's "find a JD by content hash" lookup; no separate
      single-column index is declared.
    """

    __tablename__ = "job_descriptions"

    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=False)
    # Owning account; NULL = legacy/system-owned (shared across recruiters).
    created_by_user_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    analyses = relationship("ResumeAnalysis", back_populates="job_description")

    __table_args__ = (
        Index(
            "uq_job_descriptions_content_owner",
            "content_hash",
            "created_by_user_id",
            unique=True,
        ),
    )

    def __repr__(self):  # pragma: no cover - debugging aid only
        return f"<JobDescription id={self.id} title={self.title!r}>"


class ResumeVersion(Base):
    """One distinct version of a candidate's resume document.

    Stores only metadata + a deterministic SHA-256 of the file bytes — never
    the resume text itself and never duplicated candidate identity. Analyses
    link to a specific version (via ``ResumeAnalysis.resume_version_id``), so
    an improved resume creates a NEW version + analysis instead of overwriting
    the previous one.

    Rules enforced here:
    - ``(candidate_id, version_number)`` is unique so version numbers are
      stable (1, 2, 3 ...) and are never renumbered.
    - ``(candidate_id, file_hash)`` is unique so re-uploading byte-identical
      content reuses the existing version instead of creating a duplicate.
    - Versions are append-only: recruiter actions never delete them, which is
      what keeps historical analyses reachable.
    """

    __tablename__ = "resume_versions"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id", "version_number", name="uq_resume_version_number"
        ),
        UniqueConstraint(
            "candidate_id", "file_hash", name="uq_resume_version_file_hash"
        ),
    )

    id = Column(Integer, primary_key=True)
    candidate_id = Column(
        Integer, ForeignKey("candidates.id"), nullable=False, index=True
    )
    version_number = Column(Integer, nullable=False)
    filename = Column(String(500), nullable=True)
    file_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    candidate = relationship("Candidate", back_populates="resume_versions")
    analyses = relationship(
        "ResumeAnalysis",
        back_populates="resume_version",
        cascade="all, delete-orphan",
        order_by="ResumeAnalysis.created_at.asc()",
    )

    def __repr__(self):  # pragma: no cover - debugging aid only
        return (
            f"<ResumeVersion id={self.id} candidate={self.candidate_id} "
            f"version={self.version_number}>"
        )


class ResumeAnalysis(Base):
    """One snapshot of an analysis for a candidate.

    Scores, skill gaps and timestamps live here; identity stays on
    ``Candidate`` so one candidate can accumulate many analyses over time.
    ``resume_version_id`` links the snapshot to the exact resume version that
    produced it (nullable only for legacy rows created before versioning).
    """

    __tablename__ = "resume_analyses"

    id = Column(Integer, primary_key=True)
    candidate_id = Column(
        Integer, ForeignKey("candidates.id"), nullable=False, index=True
    )
    jd_id = Column(
        Integer, ForeignKey("job_descriptions.id"), nullable=True, index=True
    )
    resume_version_id = Column(
        Integer, ForeignKey("resume_versions.id"), nullable=True, index=True
    )
    ats_score = Column(Float, nullable=True)
    jd_match_score = Column(Float, nullable=True)
    final_score = Column(Float, nullable=True)
    semantic_match_score = Column(Float, nullable=True)
    matched_skills = Column(JSON, default=list)
    missing_required_skills = Column(JSON, default=list)
    missing_preferred_skills = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)

    candidate = relationship("Candidate", back_populates="analyses")
    job_description = relationship("JobDescription", back_populates="analyses")
    resume_version = relationship("ResumeVersion", back_populates="analyses")

    def __repr__(self):  # pragma: no cover - debugging aid only
        return f"<ResumeAnalysis id={self.id} candidate={self.candidate_id} final={self.final_score}>"


class AuditLog(Base):
    """Central system/security audit trail (admin-only).

    One row per meaningful action — who, what, when, and against which
    resource. Distinct from ``CandidateActivity`` (the recruiter-facing
    timeline): AuditLog is the immutable security record and is never shown to
    regular recruiters.

    Policy for what may / may not be written (enforced in
    ``services/audit_service.py``):
    - ``action``           MUST come from the controlled vocabulary in
                           ``services/audit_service.AUDITED_ACTIONS``.
    - ``description``      human-readable, never contains passwords, tokens,
                           API keys, hashes or raw resume text.
    - ``metadata_json``    optional, JSON-serializable and never contains
                           secrets.
    - ``user_id``          the acting account id (``NULL`` = system / unknown
                           for events such as failed sign-in attempts).
    - ``resource_type``    the kind of resource being acted on (candidate,
                           user, job description, resume version, system).
    - ``resource_id``      string form of the resource primary key (may be
                           ``NULL`` when there is no concrete resource, e.g. a
                           failed sign-in without a matching account).
    Rows are append-only: nothing deletes or trims them.
    """

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True, index=True)
    action = Column(String(64), nullable=False, index=True)
    resource_type = Column(String(50), nullable=False, index=True)
    resource_id = Column(String(64), nullable=True, index=True)
    description = Column(String(500), nullable=False)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)

    def __repr__(self):  # pragma: no cover - debugging aid only
        return f"<AuditLog id={self.id} action={self.action} resource={self.resource_type}:{self.resource_id}>"