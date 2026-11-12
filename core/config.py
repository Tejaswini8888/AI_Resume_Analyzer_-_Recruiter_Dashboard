"""Centralized, typed configuration for TalentIQ (Phase 4 · Step 5).

Every environment-provided value funnels through one ``Settings`` object so
services and pages never call ``os.getenv`` directly and no setting can drift
between files.

Rules
-----
- Secrets — ``GROQ_API_KEY`` and the admin bootstrap credentials — come ONLY
  from the environment / ``.env``. They have no defaults, are never printed,
  and are excluded from :meth:`Settings.safe_summary`.
- Non-secret settings carry sensible development defaults so a fresh clone runs
  without any configuration.
- ``settings`` is the single import point: ``from core.config import settings``.
- Call-time overridability: the DB test hook and the admin bootstrap are re-read
  from the process environment on demand via :meth:`Settings.reload` (cheap,
  idempotent) so test fixtures that ``monkeypatch.setenv(...)`` keep working;
  the values still originate here and nowhere else.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Unsupported environments are rejected at parse time.
VALID_ENVIRONMENTS = ("development", "testing", "production")

# Repository root, derived from this file's location. Every relative path setting
# is anchored here so behaviour does not depend on the process working
# directory — a container, a systemd unit and an IDE can all launch the app from
# different directories and must still resolve the same files.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_project_path(value: str) -> Path:
    """Resolve a configured path against the repository root.

    Absolute paths are returned unchanged. Relative paths (the documented
    default, e.g. ``data/tmp``) are anchored to :data:`PROJECT_ROOT` rather than
    to ``os.getcwd()``, which is what made temp-file creation depend on where
    the process happened to be started.
    """
    candidate = Path(value)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate

# Single source for the "Groq is not configured" guidance shown to users and
# logged at startup. Kept here so the page message and the AI layer cannot
# drift apart.
#
# Wording is deliberately deployment-neutral: a hosted instance has no ``.env``
# file to edit, so telling a user to "add it to the .env" would be unactionable
# for exactly the audience most likely to see this message.
MISSING_GROQ_API_KEY_MESSAGE = (
    "GROQ_API_KEY is not configured, so AI features are unavailable. "
    "Deterministic ATS scoring, job-description matching and PDF reports "
    "still work. An administrator must set GROQ_API_KEY as an environment "
    "variable (a secret in your hosting platform's settings)."
)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off", ""}


class ConfigurationError(RuntimeError):
    """Raised when the process environment is structurally invalid."""


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ConfigurationError(f"Invalid boolean for {name}: {raw!r}.")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ConfigurationError(
            f"Invalid integer for {name}: {raw!r}. Expected a whole number."
        ) from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ConfigurationError(
            f"Invalid number for {name}: {raw!r}. Expected a numeric value."
        ) from exc


def _env_extensions() -> tuple:
    raw = (os.getenv("ALLOWED_RESUME_EXTENSIONS") or ".pdf")
    extensions = tuple(
        ext.strip().lower() or ".pdf" for ext in raw.split(",")
    )
    if not extensions or any(not ext.startswith(".") for ext in extensions):
        raise ConfigurationError(
            f"Invalid ALLOWED_RESUME_EXTENSIONS: {raw!r}. "
            "Extensions must be dot-prefixed and comma-separated (e.g. '.pdf')."
        )
    return extensions


@dataclass
class Settings:
    """Typed application settings. Never construct directly when environment
    values are needed — use ``Settings.load()`` or the module ``settings``."""

    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    database_url: str | None = None
    database_file: str | None = None
    max_resume_size_mb: int = 10
    allowed_resume_extensions: tuple = field(default_factory=lambda: (".pdf",))
    upload_directory: str = "data/uploads"
    temp_directory: str = "data/tmp"
    recruiter_score_threshold: float = 60.0
    admin_email: str = ""
    admin_password: str = ""

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    @classmethod
    def _parse_env(cls) -> dict:
        environment = (os.getenv("ENVIRONMENT") or "development").strip().lower()
        if environment not in VALID_ENVIRONMENTS:
            raise ConfigurationError(
                f"Unsupported ENVIRONMENT: {environment!r}. "
                f"Choose one of {', '.join(VALID_ENVIRONMENTS)}."
            )
        max_size = _env_int("MAX_RESUME_SIZE_MB", 10)
        if max_size <= 0 or max_size > 1024:
            raise ConfigurationError(
                f"Invalid MAX_RESUME_SIZE_MB: {max_size}. Must be 1-1024."
            )
        threshold = _env_float("RECRUITER_SCORE_THRESHOLD", 60.0)
        if not 0 <= threshold <= 100:
            raise ConfigurationError(
                f"Invalid RECRUITER_SCORE_THRESHOLD: {threshold}. Must be 0-100."
            )
        log_level = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError(f"Invalid LOG_LEVEL: {log_level!r}.")
        return {
            "environment": environment,
            "debug": _env_bool("DEBUG", False),
            "log_level": log_level,
            "groq_api_key": os.getenv("GROQ_API_KEY") or None,
            "groq_model": (os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b").strip(),
            "database_url": os.getenv("DATABASE_URL") or None,
            "database_file": os.getenv("TALENTIQ_DB_PATH") or None,
            "max_resume_size_mb": max_size,
            "allowed_resume_extensions": _env_extensions(),
            "upload_directory": (os.getenv("UPLOAD_DIRECTORY") or "data/uploads"),
            "temp_directory": (os.getenv("TEMP_DIRECTORY") or "data/tmp"),
            "recruiter_score_threshold": threshold,
            "admin_email": (os.getenv("TALENTIQ_ADMIN_EMAIL") or "").strip().lower(),
            "admin_password": os.getenv("TALENTIQ_ADMIN_PASSWORD") or "",
        }

    @classmethod
    def load(cls) -> "Settings":
        instance = cls()
        instance.reload()
        return instance

    def reload(self) -> None:
        """Re-read non-secret/secret values from the process environment.

        Used sparingly by the DB layer (test database override) and the auth
        bootstrap so per-test ``monkeypatch.setenv`` still works. Cheap.
        """
        for key, value in Settings._parse_env().items():
            setattr(self, key, value)

    def validate(self) -> None:
        """Structural validation. Raises ``ConfigurationError`` on bad input;
        never prints or returns secret values."""
        _ = self._parse_env()  # re-parse so errors surface with current env

    def __repr__(self) -> str:
        """Secret-safe repr: never shows the API key, admin password, or a raw
        database URL (which may embed credentials for non-SQLite engines)."""
        safe = {
            key: value
            for key, value in self.__dict__.items()
            if key not in {"groq_api_key", "admin_password", "database_url"}
        }
        return f"Settings({safe}, secrets=<redacted>)"

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def missing_critical_settings(self) -> list[str]:
        """Names of critical settings that are absent (values, never included)."""
        missing = []
        if not (self.groq_api_key or "").strip():
            missing.append("GROQ_API_KEY")
        return missing

    def safe_summary(self) -> dict:
        """Operational snapshot for logs — deliberately excludes every secret
        (API key, admin credentials) and any raw database URL that could embed
        credentials for non-SQLite engines."""
        return {
            "environment": self.environment,
            "debug": self.debug,
            "log_level": self.log_level,
            "groq_model": self.groq_model,
            "max_resume_size_mb": self.max_resume_size_mb,
            "allowed_resume_extensions": list(self.allowed_resume_extensions),
            "upload_directory": self.upload_directory,
            "temp_directory": self.temp_directory,
            "recruiter_score_threshold": self.recruiter_score_threshold,
            "database_file": self.database_file,
            "database_url": bool(self.database_url),
            "admin_bootstrap_configured": bool(self.admin_email and self.admin_password),
        }


# Single canonical instance. Import this everywhere.
settings = Settings.load()


# ---------------------------------------------------------------------------
# Legacy module-level mirrors (read once from the same singleton) so existing
# ``from core.config import GROQ_MODEL`` style imports still work and all read
# from the single source of truth.
# ---------------------------------------------------------------------------
GROQ_API_KEY = settings.groq_api_key
GROQ_MODEL = settings.groq_model
DATABASE_URL = settings.database_url
MAX_RESUME_SIZE_MB = settings.max_resume_size_mb
ALLOWED_RESUME_EXTENSIONS = settings.allowed_resume_extensions
UPLOAD_DIRECTORY = settings.upload_directory
TEMP_DIRECTORY = settings.temp_directory
ENVIRONMENT = settings.environment
DEBUG = settings.debug