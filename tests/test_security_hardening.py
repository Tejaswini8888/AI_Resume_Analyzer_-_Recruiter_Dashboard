"""Phase 4 · Step 3 — Security hardening tests.

Locks in the security fixes applied across the app:

- resume uploads are validated by extension, size, emptiness and PDF magic
  bytes (never by trusting the client-side ``type=["pdf"]`` filter),
- user-supplied filenames are reduced to harmless display labels (never used
  as filesystem paths),
- AI helper errors never leak internal exception text or credentials,
- the centralized audit service rejects secret-looking data (covered in depth
  by ``test_audit_log.py``).
"""

import types

import pytest

from modules import ai_analysis
from services.file_security_service import (
    MAX_UPLOAD_BYTES,
    is_pdf_bytes,
    safe_display_filename,
    validate_resume_file,
)

_MIN_PDF = b"%PDF-1.4\n%%EOF\n"


def _fake_file(name="resume.pdf", size=None, data=b""):
    return types.SimpleNamespace(name=name, size=size, getvalue=lambda: data)


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------
def test_validate_rejects_non_pdf_names():
    ok, reason = validate_resume_file(_fake_file(name="resume.txt", data=_MIN_PDF))
    assert ok is False
    assert "PDF" in reason


def test_validate_rejects_empty_file():
    ok, reason = validate_resume_file(_fake_file(name="resume.pdf", data=b""))
    assert ok is False
    assert "empty" in reason.lower()


def test_validate_rejects_oversized_file():
    ok, reason = validate_resume_file(
        _fake_file(name="resume.pdf", size=MAX_UPLOAD_BYTES + 1, data=_MIN_PDF)
    )
    assert ok is False
    assert "MB" in reason


def test_validate_rejects_fake_pdf_without_magic_bytes():
    html = b"<html>not a pdf</html>"
    ok, reason = validate_resume_file(_fake_file(name="resume.pdf", size=len(html), data=html))
    assert ok is False
    assert "valid PDF" in reason


def test_validate_accepts_genuine_pdf():
    ok, reason = validate_resume_file(
        _fake_file(name="Resume.PDF", size=len(_MIN_PDF), data=_MIN_PDF)
    )
    assert ok is True
    assert reason == ""


def test_is_pdf_bytes_checks_magic_prefix():
    assert is_pdf_bytes(b"%PDF-1.7")
    assert not is_pdf_bytes(b"PDF-")
    assert not is_pdf_bytes(b"")


# ---------------------------------------------------------------------------
# Safe filenames
# ---------------------------------------------------------------------------
def test_safe_filename_strips_path_separators_and_controls():
    hostile = ".. \\ evil\r\n..pdf"
    cleaned = safe_display_filename(hostile)
    assert "/" not in cleaned and "\\" not in cleaned
    assert "\r" not in cleaned and "\n" not in cleaned


def test_safe_filename_fallback_on_empty():
    assert safe_display_filename("   ") == "resume.pdf"
    assert safe_display_filename("") == "resume.pdf"


def test_safe_filename_truncates_long_names():
    long_name = "r" * 500 + ".pdf"
    assert len(safe_display_filename(long_name)) == 255
    assert safe_display_filename(long_name).endswith(".pdf")


# ---------------------------------------------------------------------------
# AI error messages never leak internals / credentials
# ---------------------------------------------------------------------------
def test_ai_error_message_does_not_include_exception_text(monkeypatch):
    fake_secret = "sk-super-secret-value-12345"

    class _StubCompletions:
        def create(self, **kwargs):
            raise ConnectionError(f"oauth_token={fake_secret} leaked here")

    class _StubClient:
        chat = types.SimpleNamespace(completions=_StubCompletions())

    monkeypatch.setattr(ai_analysis, "client", _StubClient())

    with pytest.raises(RuntimeError) as exc_info:
        ai_analysis._chat("hello")

    message = str(exc_info.value)
    assert fake_secret not in message
    assert "leaked here" not in message
    assert "sk-" not in message


def test_ai_unconfigured_message_is_stable(monkeypatch):
    monkeypatch.setattr(ai_analysis, "client", None)
    with pytest.raises(RuntimeError) as exc_info:
        ai_analysis._chat("hello")
    assert "GROQ_API_KEY is not configured" in str(exc_info.value)