"""Centralized file-security service (Phase 4 · Step 4).

Every resume file entry point funnels through this module so pages and other
services never re-implement checks, hash twice, or write to disk ad-hoc.

Design notes
------------
- PDF is the ONLY accepted format. ``ALLOWED_RESUME_EXTENSIONS`` is a future
  escape hatch; nothing else is wired up today.
- No user-supplied filename is ever used to build a filesystem path. Uploads
  are read straight from the file object in memory (modules/parser.py) and the
  generated report is built in memory (modules/pdf_report.py), so no resume
  bytes ever touch disk.
- ``sanitize_filename`` reduces a hostile name (path separators, control
  characters, 255+ length) to a harmless, display-safe label that is persisted
  as METADATA only.
- Rejected uploads / invalid files / failed report generation are recorded in
  the centralized audit log through ``log_file_security_event``. Descriptions
  are generic by design: never filenames, never resume text, never credentials
  (the audit layer additionally refuses to persist anything sensitive).
"""

import os
import tempfile
import unicodedata
from hashlib import sha256

from core.config import resolve_project_path, settings
from services.audit_service import (
    FILE_INVALID,
    PDF_INVALID,
    REPORT_GENERATION_FAILED,
    RESOURCE_FILE,
    RESOURCE_REPORT,
    UPLOAD_REJECTED,
    record_audit_event,
)

MAX_UPLOAD_BYTES = settings.max_resume_size_mb * 1024 * 1024
TEMP_DIRECTORY = settings.temp_directory

_PDF_MAGIC = b"%PDF-"
_FILE_FALLBACK_NAME = "resume.pdf"


# ---------------------------------------------------------------------------
# Structured error types. Public upload paths return clean user-safe messages
# via ``validate_resume_file``; internal callers may raise these to keep
# dialects typed instead of relying on ad-hoc string matching.
# ---------------------------------------------------------------------------

class ResumeFileError(Exception):
    """Base class for every structured resume-file failure."""


class UploadRequiredError(ResumeFileError):
    """No file was provided at all."""


class UnsupportedFileTypeError(ResumeFileError):
    """Extension is not in the allowed set (PDF only today)."""


class EmptyFileError(ResumeFileError):
    """File exists but carries zero bytes."""


class FileTooLargeError(ResumeFileError):
    """File exceeds the configured size cap."""


class InvalidPdfError(ResumeFileError):
    """File does not carry the PDF magic bytes it claims to."""


# ---------------------------------------------------------------------------
# Type / size validation
# ---------------------------------------------------------------------------

def _file_bytes(file) -> bytes:
    try:
        data = file.getvalue()
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
    except AttributeError:
        pass
    return b""


def _allowed_extensions():
    return settings.allowed_resume_extensions


def allowed_extensions() -> tuple:
    """Currently accepted resume extensions (from central config)."""
    return _allowed_extensions()


def is_pdf_name(filename: str) -> bool:
    """True when the extension is in the allowed set (case-insensitive)."""
    name = (filename or "").strip().lower()
    return any(name.endswith(ext) for ext in _allowed_extensions())


def is_allowed_file_type(filename: str) -> bool:
    """Public alias for ``is_pdf_name``; file-type checks live in one place."""
    return is_pdf_name(filename)


def is_pdf_bytes(data: bytes) -> bool:
    return (data or b"").startswith(_PDF_MAGIC)


def validate_resume_file(file, *, max_bytes: int | None = None, strict: bool = False):
    """Validate one uploaded resume.

    Returns ``(ok, reason)`` — ``reason`` is a clean, user-safe message when
    ``ok`` is ``False``. With ``strict=True`` it raises a typed
    ``ResumeFileError`` subclass instead, so callers can branch on error kind.

    The size limit is resolved from centralized configuration on every call, so
    ``MAX_RESUME_SIZE_MB`` changes take effect without a restart. When a file
    object reports a ``size`` (its reported length) AND carries readable bytes,
    BOTH are enforced against the limit so a lying ``size`` attribute can never
    smuggle oversized bytes through the reported-length check.
    """
    if max_bytes is None:
        max_bytes = settings.max_resume_size_mb * 1024 * 1024

    def _fail(message, error_type):
        if strict:
            raise error_type(message)
        return False, message

    if file is None:
        return _fail("Upload a resume to continue.", UploadRequiredError)

    name = getattr(file, "name", "") or ""
    if not is_pdf_name(name):
        return _fail("Only PDF resumes are supported.", UnsupportedFileTypeError)

    data = _file_bytes(file)
    size = getattr(file, "size", None)
    if size is None:
        size = len(data)
    if size <= 0:
        return _fail("The uploaded file is empty.", EmptyFileError)
    if size > max_bytes or len(data) > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        return _fail(
            f"The file is larger than {limit_mb} MB and was skipped.",
            FileTooLargeError,
        )

    if not is_pdf_bytes(data):
        return _fail(
            "The file does not appear to be a valid PDF.",
            InvalidPdfError,
        )

    return True, ""


# ---------------------------------------------------------------------------
# Safe filenames (metadata only — never used to build a path)
# ---------------------------------------------------------------------------

def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """Reduce a user-supplied name to a harmless label.

    Path separators and control characters are stripped, the result is bounded
    to ``max_length`` bytes-chars, and hostile/blank input falls back to
    ``"resume.pdf"``. The output is displayable and safe as persisted metadata.
    """
    name = unicodedata.normalize("NFKC", filename or "")
    name = "".join(
        ch for ch in name if ch.isprintable() and ch not in "/\\"
    ).strip()
    if not name:
        return _FILE_FALLBACK_NAME
    return name[-max_length:] if len(name) > max_length else name


def safe_display_filename(filename: str, max_length: int = 255) -> str:
    """Alias kept for call sites that treat the value as display-only."""
    return sanitize_filename(filename, max_length=max_length)


# ---------------------------------------------------------------------------
# Hashing (deterministic SHA-256; the identity key for candidates / versions)
# ---------------------------------------------------------------------------

def calculate_file_hash(data: bytes) -> str:
    """Deterministic SHA-256 of resume file bytes (cross-platform stable)."""
    return sha256(data or b"").hexdigest()


def calculate_file_hash_file(file_object) -> str:
    """Hash a Streamlit upload (``UploadedFile``/``BytesIO``) without mutating it."""
    try:
        data = file_object.getvalue()
    except AttributeError:
        data = file_object.read() if callable(getattr(file_object, "read", None)) else b""
    return calculate_file_hash(data if isinstance(data, (bytes, bytearray)) else b"")


# ---------------------------------------------------------------------------
# Secure temporary-file helpers (for real-path work that needs a disk scratch
# file — the PDF report itself is built in memory and needs none of these).
# ---------------------------------------------------------------------------

def create_secure_temp_file(*, prefix: str = "talentiq_", suffix: str = ".part") -> str:
    """Create a private temp file under ``TEMP_DIRECTORY`` and return its path.

    The directory is created on demand. A relative ``TEMP_DIRECTORY`` is
    resolved against the repository root, never the process working directory,
    so a container, a service manager and an IDE all land in the same place.
    The returned path must be released with ``cleanup_temp_file`` once the
    caller is done (use try/finally).
    """
    temp_dir = resolve_project_path(TEMP_DIRECTORY)
    temp_dir.mkdir(parents=True, exist_ok=True)
    descriptor, path = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=str(temp_dir))
    os.close(descriptor)
    return path


def cleanup_temp_file(path) -> None:
    """Best-effort removal of a temporary file; never raises (idempotent)."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Spillover formula-injection protection (CSV exports)
# ---------------------------------------------------------------------------

_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_neutralize(value) -> str:
    """Neutralize spreadsheet formula injection in one exported cell.

    A cell whose text starts with ``= + - @`` (or a leading tab/CR, which only
    matters inside the sheet) would otherwise be evaluated as a formula when
    the CSV is opened in Excel/Sheets. Prefixing with a single apostrophe turns
    it into inert text in every mainstream spreadsheet application. Numbers and
    booleans pass through unchanged.
    """
    if isinstance(value, (int, float, bool)) or value is None:
        return value if value is not None else None
    text = str(value)
    if text.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + text
    return text


# ---------------------------------------------------------------------------
# Security-event logging (audit trail, never breaks the upload flow)
# ---------------------------------------------------------------------------

def file_event_vocabulary(reason: str):
    """Map a ``validate_resume_file`` message to ``(action, generic description)``.

    Descriptions are deliberately generic: they never include the filename or
    any resume bytes, so hostile names can never smuggle content into the log.
    """
    reason = reason or ""
    if "Only PDF resumes are supported." in reason:
        return UPLOAD_REJECTED, "Unsupported file type rejected."
    if reason.startswith("The file is larger than"):
        return UPLOAD_REJECTED, "Oversized upload rejected."
    if "The uploaded file is empty." in reason:
        return FILE_INVALID, "Empty file rejected."
    if "valid PDF" in reason:
        return PDF_INVALID, "Invalid file rejected."
    return UPLOAD_REJECTED, "Upload rejected."


def log_file_security_event(
    actor_id,
    *,
    action,
    resource_type,
    resource_id=None,
    description,
):
    """Best-effort audit write for a file-security event.

    Never raises and never blocks the calling flow; the audit layer itself
    additionally refuses sensitive content.
    """
    try:
        record_audit_event(
            actor_id,
            action,
            resource_type,
            resource_id,
            description,
        )
    except Exception:
        return


def log_rejected_upload(actor_id, reason: str, resource_id=None):
    """Log one rejected file upload with a generic, safe description."""
    action, description = file_event_vocabulary(reason)
    log_file_security_event(
        actor_id,
        action=action,
        resource_type=RESOURCE_FILE,
        resource_id=resource_id,
        description=description,
    )


def log_report_failure(actor_id, description="Report generation failed."):
    """Log a failed PDF report build."""
    log_file_security_event(
        actor_id,
        action=REPORT_GENERATION_FAILED,
        resource_type=RESOURCE_REPORT,
        resource_id=None,
        description=description,
    )