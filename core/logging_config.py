"""Centralized logging configuration (Phase 4 · Step 5).

One place to configure logging for the whole application so every module logs
with a consistent, structured format and a level that follows ``settings``.

Hard guardrail: log messages produced here never contain secrets. The safe
operational snapshot (:func:`log_startup_config`) excludes the API key and the
admin credentials, and :func:`redact_secrets` scrubs known secrets from any
string that must be logged defensively.

Levels map to ``LOG_LEVEL`` in the environment (DEBUG/INFO/WARNING/ERROR).
Development defaults to INFO unless ``DEBUG=true`` enables DEBUG.
"""

import logging
import os
import re

from core.config import settings

_CONFIGURED = False


def _resolve_level() -> str:
    if settings.debug and settings.log_level == "INFO":
        return "DEBUG"
    return settings.log_level


def configure_logging() -> None:
    """Configure root logging once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=getattr(logging, _resolve_level(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
    _CONFIGURED = True


def redact_secrets(message: str) -> str:
    """Replace known secret values in ``message`` with ``[REDACTED]``.

    Defense in depth for anything that must be logged: explicit enum of secrets
    so a stray value can never appear in a log line even if a caller forgets
    to whitelist fields.
    """
    if not message:
        return message
    secrets = {
        settings.groq_api_key,
        settings.admin_password,
    }
    # Also scrub any "GROQ_API_KEY=<value>" / "Authorization: Bearer ..." style
    # pairs that might ride along in a captured header/body.
    pairs = [
        (r"(GROQ_API_KEY\s*=\s*)\S+", r"\1[REDACTED]"),
        (r"(Authorization\s*:\s*(?:Bearer\s*)?)\S+", r"\1[REDACTED]"),
        (r"(api[_-]?key\s*[:=]\s*)\S+", r"\1[REDACTED]"),
    ]
    out = message
    for secret in secrets:
        if secret:
            out = re.sub(re.escape(secret), "[REDACTED]", out)
    for pattern, repl in pairs:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return out


def log_startup_config() -> None:
    """Emit a non-secret operational summary + warnings at app startup.

    Missing critical settings are reported BY NAME so operators know what to
    set; values are never shown.
    """
    configure_logging()
    logger = logging.getLogger("core.config")
    summary = settings.safe_summary()
    logger.info("TalentIQ starting (environment=%s)", summary["environment"])
    logger.info("Settings: %s", summary)
    for name in settings.missing_critical_settings():
        logger.warning(
            "Missing critical setting %s. Set it in the environment "
            "(a secret in your hosting platform's settings).", name
        )
    if settings.database_url and settings.database_file:
        # Only SQLite URLs are honoured; a non-SQLite URL is a hard error raised
        # by core.db, so this line is only reached for the SQLite case.
        logger.info(
            "DATABASE_URL is set to a SQLite URL and takes precedence over "
            "TALENTIQ_DB_PATH."
        )
