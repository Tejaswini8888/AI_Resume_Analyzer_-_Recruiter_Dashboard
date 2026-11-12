"""Centralized system/security audit log (Phase 4 · Step 3).

Every meaningful action across TalentIQ funnels through
``record_audit_event`` — pages and services never write ``AuditLog`` rows
directly, so the vocabulary stays controlled and nothing sensitive can slip
in through one ad-hoc insert.

Distinction from ``CandidateActivity``
--------------------------------------
- ``CandidateActivity`` is the *recruiter-facing* timeline ("Candidate moved to
  Interview") shown inside candidate details and the dashboard.
- ``AuditLog`` is the *system/security* record of who performed an action and
  when; it is admin-only and deliberately separate. A status change therefore
  produces BOTH a ``CandidateActivity`` and an ``AuditLog`` row inside the same
  transaction (see ``services/recruitment_service``).

What is / is not allowed in an audit event
------------------------------------------
- ``action`` MUST be one of the canonical ``AUDIT_EVENTS`` codes below.
- ``description`` / ``metadata`` MUST NOT contain passwords, tokens, API keys,
  authorization headers, or full resume text. ``record_audit_event`` rejects
  anything that looks sensitive and refuses to write it (defense in depth, so a
  future careless call site cannot silently leak credentials into the log).
"""

from datetime import datetime, time

from sqlalchemy import func

from core.db import session_scope
from core.models import AuditLog, User

# ---------------------------------------------------------------------------
# Canonical vocabulary. Add new actions here — never inline ad-hoc strings.
# ---------------------------------------------------------------------------

AUTH_LOGIN = "AUTH_LOGIN"
AUTH_LOGIN_FAILED = "AUTH_LOGIN_FAILED"
AUTH_LOGOUT = "AUTH_LOGOUT"

CANDIDATE_CREATED = "CANDIDATE_CREATED"
CANDIDATE_ANALYZED = "CANDIDATE_ANALYZED"
CANDIDATE_SHORTLISTED = "CANDIDATE_SHORTLISTED"
CANDIDATE_STATUS_CHANGED = "CANDIDATE_STATUS_CHANGED"
CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
CANDIDATE_SELECTED = "CANDIDATE_SELECTED"

RESUME_VERSION_CREATED = "RESUME_VERSION_CREATED"
JOB_DESCRIPTION_CREATED = "JOB_DESCRIPTION_CREATED"
JOB_DESCRIPTION_UPDATED = "JOB_DESCRIPTION_UPDATED"

UPLOAD_REJECTED = "UPLOAD_REJECTED"
FILE_INVALID = "FILE_INVALID"
PDF_INVALID = "PDF_INVALID"
REPORT_GENERATION_FAILED = "REPORT_GENERATION_FAILED"

USER_CREATED = "USER_CREATED"
USER_ACTIVATED = "USER_ACTIVATED"
USER_DEACTIVATED = "USER_DEACTIVATED"
USER_ROLE_CHANGED = "USER_ROLE_CHANGED"

AUDIT_EVENTS = frozenset(
    {
        AUTH_LOGIN,
        AUTH_LOGIN_FAILED,
        AUTH_LOGOUT,
        CANDIDATE_CREATED,
        CANDIDATE_ANALYZED,
        CANDIDATE_SHORTLISTED,
        CANDIDATE_STATUS_CHANGED,
        CANDIDATE_REJECTED,
        CANDIDATE_SELECTED,
        RESUME_VERSION_CREATED,
        JOB_DESCRIPTION_CREATED,
        JOB_DESCRIPTION_UPDATED,
        UPLOAD_REJECTED,
        FILE_INVALID,
        PDF_INVALID,
        REPORT_GENERATION_FAILED,
        USER_CREATED,
        USER_ACTIVATED,
        USER_DEACTIVATED,
        USER_ROLE_CHANGED,
    }
)

# Resource kinds used across the audit trail.
RESOURCE_CANDIDATE = "candidate"
RESOURCE_USER = "user"
RESOURCE_JOB_DESCRIPTION = "job_description"
RESOURCE_RESUME_VERSION = "resume_version"
RESOURCE_FILE = "file"
RESOURCE_REPORT = "report"
RESOURCE_SYSTEM = "system"

RESOURCE_TYPES = (
    (RESOURCE_CANDIDATE, "Candidate"),
    (RESOURCE_USER, "User"),
    (RESOURCE_JOB_DESCRIPTION, "Job description"),
    (RESOURCE_RESUME_VERSION, "Resume version"),
    (RESOURCE_FILE, "File"),
    (RESOURCE_REPORT, "Report"),
    (RESOURCE_SYSTEM, "System"),
)

ACTION_LABELS = {
    AUTH_LOGIN: "Sign in",
    AUTH_LOGIN_FAILED: "Failed sign in",
    AUTH_LOGOUT: "Sign out",
    CANDIDATE_CREATED: "Candidate created",
    CANDIDATE_ANALYZED: "Candidate analyzed",
    CANDIDATE_SHORTLISTED: "Candidate shortlisted",
    CANDIDATE_STATUS_CHANGED: "Status changed",
    CANDIDATE_REJECTED: "Candidate rejected",
    CANDIDATE_SELECTED: "Candidate selected",
    RESUME_VERSION_CREATED: "Resume version created",
    JOB_DESCRIPTION_CREATED: "Job description created",
    JOB_DESCRIPTION_UPDATED: "Job description updated",
    UPLOAD_REJECTED: "Upload rejected",
    FILE_INVALID: "Invalid file",
    PDF_INVALID: "Invalid/parse-failed PDF",
    REPORT_GENERATION_FAILED: "Report generation failed",
    USER_CREATED: "User created",
    USER_ACTIVATED: "User activated",
    USER_DEACTIVATED: "User deactivated",
    USER_ROLE_CHANGED: "Role changed",
}

# Guardrails: no password/token/API-key material may ever reach the audit log.
_SENSITIVE_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "password",
    "secret",
    "token",
)

_DESCRIPTION_MAX = 500
_RESOURCE_ID_MAX = 64


class AuditLogError(ValueError):
    """An audit event was refused (non-canonical action, secret, or overflow)."""


def _contains_sensitive(value) -> bool:
    lowered = str(value).lower()
    return any(marker in lowered for marker in _SENSITIVE_MARKERS)


def record_audit_event(
    user_id,
    action,
    resource_type,
    resource_id,
    description,
    *,
    metadata=None,
    session=None,
):
    """Append one audit event to the log.

    Writes inside the *caller's* transaction when ``session`` is given (so
    events commit atomically with the action they describe — e.g. a candidate
    status change — and any failure rolls both back together). Otherwise opens
    its own ``session_scope()`` and commits immediately.

    Raises ``AuditLogError`` if the event data violates policy, and any
    database error if the insert itself fails — callers that require audit
    completeness must NOT swallow this silently.
    """
    if action not in AUDIT_EVENTS:
        raise AuditLogError(f"Unknown audit action: {action!r}")

    description = str(description)
    if not description or len(description) > _DESCRIPTION_MAX:
        raise AuditLogError("Audit description must be 1-500 characters.")

    resource_type = str(resource_type)
    resource_id = str(resource_id) if resource_id is not None else None
    if resource_id is not None and len(resource_id) > _RESOURCE_ID_MAX:
        raise AuditLogError("Audit resource id is too long.")

    sensitive_inputs = [description, resource_type]
    if resource_id is not None:
        sensitive_inputs.append(resource_id)
    if _contains_sensitive(resource_type) or _contains_sensitive(description):
        raise AuditLogError("Refusing to audit data that looks like a secret.")

    if metadata is not None:
        try:
            import json as _json

            serialized = _json.dumps(metadata, default=str)
        except (TypeError, ValueError):
            raise AuditLogError("Audit metadata must be JSON-serializable.")
        if _contains_sensitive(serialized):
            raise AuditLogError("Refusing to audit metadata that looks like a secret.")

    def _append(session_local):
        session_local.add(
            AuditLog(
                user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                description=description,
                metadata_json=metadata,
            )
        )

    if session is not None:
        _append(session)
        return None

    with session_scope() as session_local:
        _append(session_local)
    return None


# ---------------------------------------------------------------------------
# Read side — admin-only, paginated, filterable, searchable.
# ---------------------------------------------------------------------------

_DATE_EPSILON = time(23, 59, 59, 999999)


def _as_datetime(value, *, end_of_day: bool = False):
    """Normalize ``datetime.date``/``datetime`` into a timezone-free UTC value.

    With ``end_of_day=True`` a bare ``date`` expands to the END of that day so
    a date-range query is inclusive of everything that happened on the end date
    (a naive midnight bound would silently exclude the same day's later events).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    from datetime import date as _date_cls

    if isinstance(value, _date_cls):
        bound = time.max if end_of_day else time.min
        return datetime.combine(value, bound).replace(tzinfo=None)
    return value


def list_audit_events(
    *,
    user=None,
    user_id=None,
    action=None,
    resource_type=None,
    resource_id=None,
    start_date=None,
    end_date=None,
    search=None,
    page=1,
    per_page=25,
):
    """Return a paginated, filtered view of the audit trail (admin only).

    Authorization: when ``user`` (the requesting account dict) is provided and
    is not an ADMIN, ``PermissionError`` is raised — this is the service-level
    gate, independent of any UI hiding.

    Filters (all optional) are applied via SQL — never by loading every row:
    - ``user_id``        events performed BY this account id
    - ``action``         one canonical event code
    - ``resource_type``  one resource kind
    - ``resource_id``    exact resource id match
    - ``start_date`` / ``end_date``  ``date``/``datetime`` bounds on
                           ``created_at``
    - ``search``         case-insensitive over user name/email, resource id and
                           description (no ad-hoc SQL is constructed)
    """
    if user is not None and user.get("role") != "ADMIN":
        raise PermissionError("Only admins can view the audit log.")

    page = max(int(page), 1)
    per_page = min(max(int(per_page), 1), 100)

    conditions = []
    if user_id is not None:
        conditions.append(AuditLog.user_id == user_id)
    if action:
        conditions.append(AuditLog.action == action)
    if resource_type:
        conditions.append(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        conditions.append(AuditLog.resource_id == str(resource_id))
    if start_date:
        conditions.append(AuditLog.created_at >= _as_datetime(start_date))
    if end_date:
        conditions.append(AuditLog.created_at <= _as_datetime(end_date, end_of_day=True))
    if search and search.strip():
        term = "%" + search.strip().lower() + "%"
        conditions.append(
            func.lower(AuditLog.description).like(term)
            | func.lower(AuditLog.resource_id).like(term)
            | func.lower(User.name).like(term)
            | func.lower(User.email).like(term)
        )

    with session_scope() as session:
        base = (
            session.query(AuditLog, User)
            .outerjoin(User, User.id == AuditLog.user_id)
        )

        total = (
            session.query(func.count(AuditLog.id))
            .outerjoin(User, User.id == AuditLog.user_id)
            .filter(*conditions)
            .scalar()
            or 0
        )

        rows = (
            base.filter(*conditions)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        events = [
            {
                "id": audit.id,
                "user_id": audit.user_id,
                "user_name": user.name if user else None,
                "user_email": user.email if user else None,
                "action": audit.action,
                "action_label": ACTION_LABELS.get(audit.action, audit.action),
                "resource_type": audit.resource_type,
                "resource_id": audit.resource_id,
                "description": audit.description,
                "created_at": audit.created_at,
            }
            for audit, user in rows
        ]

    total_pages = max((total + per_page - 1) // per_page, 1)
    return {
        "events": events,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }