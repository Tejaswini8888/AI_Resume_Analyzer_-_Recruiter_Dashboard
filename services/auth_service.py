"""Authentication, sessions, and role-based access control for TalentIQ.

Dependencies
------------
Deliberately standard-library only: passwords are hashed with
``hashlib.scrypt`` + a per-password random salt and compared in constant time
(``hmac.compare_digest``). No werkzeug/bcrypt/passlib — the project's venv does
not install them and adding binary deps for one login page isn't worth it.

Roles
-----
Two controlled roles from :data:`core.models.USER_ROLES`:
``RECRUITER`` (default for new accounts) and ``ADMIN``. Permissions are never
free-text.

- A user may be created through registration (defaults to RECRUITER).
- An ADMIN account is bootstrapped with environment variables
  ``TALENTIQ_ADMIN_EMAIL`` + ``TALENTIQ_ADMIN_PASSWORD``: when those are set, a
  registration using exactly that email + password is granted the ADMIN role.
  No credentials are ever hardcoded in source.

Session
-------
Streamlit ``st.session_state`` stores ONLY the authenticated user id
(``auth_user_id``) — never the password hash. Every read re-validates against
the database and checks ``is_active``, so deactivated accounts lose access on
their next interaction without any session bookkeeping.

Ownership
---------
``owner_scope()`` returns a SQLAlchemy filter expression for the "records this
user may see" rule:

- ADMIN sees everything.
- A RECRUITER sees records they created **plus** legacy records whose owner is
  ``NULL`` (created pre-Phase-4 or by the public self-analysis flow). This keeps
  historical data usable without fabricating an owner.
- Services invoked with ``user=None`` (legacy/test callers) are unrestricted.

Error semantics
---------------
``authenticate_user`` never distinguishes a bad password from an unknown email
(it always reports ``invalid``); the UI shows one generic message. An account
that is merely deactivated returns ``inactive`` so the UI can show a clean,
non-revealing message. Any unexpected DB failure returns ``error``.
"""

import hashlib
import hmac
import re
import secrets
import string

from sqlalchemy import or_

from core import models
from core.config import settings
from core.db import session_scope
from core.models import USER_ROLES
from services import audit_service

# ---------------------------------------------------------------------------
# Password hashing (stdlib scrypt)
# ---------------------------------------------------------------------------

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16


def _b64encode(value: bytes) -> str:
    import base64

    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str) -> bytes:
    import base64

    return base64.b64decode(value.encode("ascii"))


def hash_password(password: str) -> str:
    """Hash a password into a self-describing ``scrypt$n$r$p$salt$hash`` string."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64encode(salt)}${_b64encode(key)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification of a stored ``hash_password`` string."""
    try:
        scheme, n, r, p, salt_b64, key_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = _b64decode(salt_b64)
        expected = _b64decode(key_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError, IndexError):
        return False
    return hmac.compare_digest(actual, expected)


_PASSWORD_MIN = 8
_PASSWORD_MAX = 128
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def validate_password(password: str) -> tuple[bool, str]:
    """Server-side password policy. Returns ``(ok, reason)``."""
    if not isinstance(password, str) or not password:
        return False, "Enter a password."
    if password.isspace():
        return False, "Password cannot be only spaces."
    if len(password) < _PASSWORD_MIN:
        return False, f"Password must be at least {_PASSWORD_MIN} characters."
    if len(password) > _PASSWORD_MAX:
        return False, f"Password must be at most {_PASSWORD_MAX} characters."
    return True, ""


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.fullmatch((email or "").strip()))


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _admin_bootstrapped_email() -> str:
    settings.reload()  # honor per-test env overrides
    return (settings.admin_email or "").strip().lower()


def _admin_bootstrapped_password() -> str:
    settings.reload()
    return settings.admin_password or ""


def is_valid_role(role: str) -> bool:
    return role in USER_ROLES


def _user_to_dict(user: models.User) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
    }


def _get_user_row_by_email(session, email: str) -> models.User | None:
    return (
        session.query(models.User)
        .filter(models.User.email == normalize_email(email))
        .first()
    )


def _get_user_row_by_id(session, user_id: int) -> models.User | None:
    return (
        session.query(models.User).filter(models.User.id == user_id).first()
    )


# ---------------------------------------------------------------------------
# Account creation / authentication
# ---------------------------------------------------------------------------


def register_user(
    name: str,
    email: str,
    password: str,
    role: str | None = None,
) -> dict:
    """Create an account. Defaults to RECRUITER unless matched against the
    admin bootstrap environment. Raises ``ValueError`` on invalid/duplicate
    input so callers can show a specific message.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Enter your full name.")

    email = normalize_email(email)
    if not is_valid_email(email):
        raise ValueError("Enter a valid email address.")

    ok, reason = validate_password(password)
    if not ok:
        raise ValueError(reason)

    effective_role = role or "RECRUITER"
    if (
        _admin_bootstrapped_email()
        and normalize_email(email) == _admin_bootstrapped_email()
        and _admin_bootstrapped_password()
        and hmac.compare_digest(password, _admin_bootstrapped_password())
    ):
        effective_role = "ADMIN"
    elif effective_role != "RECRUITER":
        # Roles cannot be self-selected: only the env bootstrap grants ADMIN.
        effective_role = "RECRUITER"
    if not is_valid_role(effective_role):
        effective_role = "RECRUITER"

    with session_scope() as session:
        if _get_user_row_by_email(session, email) is not None:
            raise ValueError("An account with this email already exists.")
        user = models.User(
            name=name,
            email=email,
            password_hash=hash_password(password),
            role=effective_role,
            is_active=True,
        )
        session.add(user)
        session.flush()
        audit_service.record_audit_event(
            user.id,
            audit_service.USER_CREATED,
            audit_service.RESOURCE_USER,
            user.id,
            f"Account created as {effective_role}.",
            session=session,
        )
        return _user_to_dict(user)


def authenticate_user(email: str, password: str) -> tuple[str, dict | None]:
    """Validate credentials. Returns ``(status, user)`` where status is one of
    ``"ok"``, ``"invalid"`` (bad email or password — deliberately ambiguous),
    ``"inactive"``, or ``"error"`` (unexpected DB failure).

    Successful and failed attempts are both written to the audit log inside the
    same transaction. Only safe metadata is recorded — the attempted password is
    never written anywhere.
    """
    try:
        with session_scope() as session:
            user = _get_user_row_by_email(session, email)
            if user is None:
                audit_service.record_audit_event(
                    None,
                    audit_service.AUTH_LOGIN_FAILED,
                    audit_service.RESOURCE_USER,
                    None,
                    "Failed sign-in attempt.",
                    session=session,
                )
                return "invalid", None
            if not verify_password(password, user.password_hash):
                audit_service.record_audit_event(
                    user.id,
                    audit_service.AUTH_LOGIN_FAILED,
                    audit_service.RESOURCE_USER,
                    user.id,
                    "Failed sign-in attempt.",
                    session=session,
                )
                return "invalid", None
            if not user.is_active:
                audit_service.record_audit_event(
                    user.id,
                    audit_service.AUTH_LOGIN_FAILED,
                    audit_service.RESOURCE_USER,
                    user.id,
                    "Sign-in attempt on a deactivated account.",
                    session=session,
                )
                return "inactive", None
            audit_service.record_audit_event(
                user.id,
                audit_service.AUTH_LOGIN,
                audit_service.RESOURCE_USER,
                user.id,
                "User signed in.",
                session=session,
            )
            return "ok", _user_to_dict(user)
    except Exception:
        return "error", None


def get_user(user_id: int) -> dict | None:
    """A user dict by id, or ``None`` if missing/inactive (inactive users are
    treated as signed out). Safe to call on any rerun.
    """
    try:
        with session_scope() as session:
            user = _get_user_row_by_id(session, user_id)
    except Exception:
        return None
    if user is None or not user.is_active:
        return None
    return _user_to_dict(user)


def get_user_by_email(email: str) -> dict | None:
    with session_scope() as session:
        user = _get_user_row_by_email(session, email)
        if user is None:
            return None
        return _user_to_dict(user)


def list_users() -> list[dict]:
    with session_scope() as session:
        rows = session.query(models.User).order_by(models.User.created_at).all()
        return [_user_to_dict(row) for row in rows]


def set_user_role(user_id: int, role: str, *, actor: dict | None = None) -> dict | None:
    """Change a user's role. Admin-only: when ``actor`` is provided it must hold
    the ADMIN role, otherwise ``PermissionError`` is raised (service-level
    enforcement, independent of any UI hiding).

    The change is recorded to the audit log inside the same transaction
    (``actor`` is the acting user; the ``resource`` is the affected account).
    """
    if actor is not None and not has_role(actor, "ADMIN"):
        raise PermissionError("Only admins can change user roles.")
    if not is_valid_role(role):
        raise ValueError("Unsupported role.")
    with session_scope() as session:
        user = _get_user_row_by_id(session, user_id)
        if user is None:
            return None
        previous_role = user.role
        if previous_role == role:
            return _user_to_dict(user)
        user.role = role
        session.flush()
        audit_service.record_audit_event(
            actor["id"] if actor else None,
            audit_service.USER_ROLE_CHANGED,
            audit_service.RESOURCE_USER,
            user_id,
            f"Role changed from {previous_role} to {role}.",
            session=session,
        )
        return _user_to_dict(user)


def set_user_active(user_id: int, active: bool, *, actor: dict | None = None) -> dict | None:
    """Activate/deactivate an account. Admin-only: when ``actor`` is provided it
    must hold the ADMIN role, otherwise ``PermissionError`` is raised. The
    change is recorded to the audit log inside the same transaction.
    """
    if actor is not None and not has_role(actor, "ADMIN"):
        raise PermissionError("Only admins can change account status.")
    with session_scope() as session:
        user = _get_user_row_by_id(session, user_id)
        if user is None:
            return None
        user.is_active = bool(active)
        session.flush()
        audit_service.record_audit_event(
            actor["id"] if actor else None,
            audit_service.USER_ACTIVATED if active else audit_service.USER_DEACTIVATED,
            audit_service.RESOURCE_USER,
            user_id,
            "User account activated." if active else "User account deactivated.",
            session=session,
        )
        return _user_to_dict(user)


# ---------------------------------------------------------------------------
# Session helpers (Streamlit)
# ---------------------------------------------------------------------------

_AUTH_SESSION_KEY = "auth_user_id"


def _is_session_initialized() -> bool:
    try:
        import streamlit as st

        return hasattr(st, "session_state")
    except Exception:
        return False


def start_session(user: dict) -> None:
    """Persist only the user id for the duration of the browser session."""
    import streamlit as st

    st.session_state[_AUTH_SESSION_KEY] = user["id"]


def current_user() -> dict | None:
    """The authenticated user dict (re-validated against the DB), or None."""
    import streamlit as st

    user_id = st.session_state.get(_AUTH_SESSION_KEY)
    if user_id is None:
        return None
    return get_user(user_id)


def logout_user() -> None:
    """Clear every ``session_state`` key (auth marker + all scratch data).

    Previously only a fixed key list was removed; anything else (widget
    values, form fields such as a password entry, nonce markers, unscoped
    recruiter keys) could survive a sign-out. Clearing the ENTIRE session
    guarantees no recruiter/candidate data or password-material remains on the
    device after logout. The AUTH_LOGOUT event is recorded best-effort:
    clearing the session must complete even if auditing fails, since keeping a
    signed-in session after a failed audit would be the worse failure.
    """
    import streamlit as st

    user_id = st.session_state.get(_AUTH_SESSION_KEY)
    try:
        if user_id is not None:
            audit_service.record_audit_event(
                user_id,
                audit_service.AUTH_LOGOUT,
                audit_service.RESOURCE_USER,
                user_id,
                "User signed out.",
            )
    except Exception:
        pass

    try:
        for key in list(st.session_state.keys()):
            del st.session_state[key]
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Role checks
# ---------------------------------------------------------------------------


def has_role(user: dict | None, role: str) -> bool:
    return user is not None and user.get("is_active") and user.get("role") == role


def require_role(user: dict | None, role: str) -> None:
    """Raise PermissionError unless the user holds ``role``."""
    if not has_role(user, role):
        raise PermissionError("You do not have permission to do that.")


# ---------------------------------------------------------------------------
# Ownership scoping (used by data services)
# ---------------------------------------------------------------------------


def owner_scope(entity, user: dict | None):
    """SQLAlchemy filter expression for records visible to ``user``.

    Returns ``None`` when unrestricted (user is ``None`` = legacy/service call,
    or the user is an ADMIN). Otherwise ``owner == user`` OR ``owner IS NULL``
    (legacy shared records).
    """
    if user is None or user.get("role") == "ADMIN":
        return None
    owner_column = getattr(entity, "created_by_user_id")
    return or_(owner_column == user["id"], owner_column.is_(None))


def can_access(user: dict | None, owner_id: int | None) -> bool:
    """Post-query access check: may ``user`` see a row owned by ``owner_id``?"""
    if user is None or user.get("role") == "ADMIN":
        return True
    if owner_id is None:
        return True  # legacy/system-owned records are shared
    return owner_id == user["id"]


def identity_prefix(user: dict | None) -> str:
    """Short human label for tooltips ('' for anonymous/system)."""
    if user is None:
        return ""
    return f"{user.get('name', '')} · {user.get('role', '')}"


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def random_session_token(length: int = 32) -> str:
    """CSPRNG token for any future non-persistent session needs."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))