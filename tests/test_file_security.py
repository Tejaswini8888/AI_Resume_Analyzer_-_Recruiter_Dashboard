"""Phase 4 · Step 4 — File & resume security tests.

Locks in production-grade file handling:

- upload validation (valid PDF / wrong extension / oversized / empty /
  malformed) with clean, structured results and typed errors,
- hostile filename handling (path traversal, control characters, length),
- deterministic SHA-256 hashing and byte-identical duplicate detection,
- temporary-file creation + guaranteed cleanup (report builds are fully
  in-memory and leak nothing),
- PDF report generation that escapes user content and never leaves temp files,
- resume-version preservation and the "normal analysis still works" regression.
"""

import os
import types
from io import BytesIO

import pytest
from reportlab.pdfgen import canvas

from modules.parser import extract_text_from_pdf
from modules.resume_profile import build_resume_profile
from services import file_security_service
from services.file_security_service import (
    FILE_INVALID,
    PDF_INVALID,
    UPLOAD_REJECTED,
    calculate_file_hash,
    calculate_file_hash_file,
    cleanup_temp_file,
    create_secure_temp_file,
    file_event_vocabulary,
    is_allowed_file_type,
    is_pdf_bytes,
    log_rejected_upload,
    safe_display_filename,
    sanitize_filename,
    validate_resume_file,
)
from services.audit_service import list_audit_events
from services.candidate_service import create_candidate
from services.resume_version_service import (
    create_or_get_resume_version,
    get_resume_versions,
    resume_version_count,
)

_MIN_PDF = b"%PDF-1.4\n%%EOF\n"


def _fake_file(name="resume.pdf", size=None, data=b""):
    return types.SimpleNamespace(name=name, size=size, getvalue=lambda: data)


def _make_pdf_bytes(text="Resume Text Skills: Python SQL"):
    buf = BytesIO()
    page = canvas.Canvas(buf)
    page.drawString(72, 720, text)
    page.showPage()
    page.save()
    return buf.getvalue()


def _temp_scratch(monkeypatch, tmp_path):
    """Point the service's scratch directory at a test-owned folder."""
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(file_security_service, "TEMP_DIRECTORY", str(scratch))
    return scratch


@pytest.fixture(autouse=True)
def _isolated_db(fresh_db):
    yield


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------
def test_valid_pdf_upload_accepted_and_parses():
    pdf_bytes = _make_pdf_bytes()
    ok, reason = validate_resume_file(
        _fake_file(name="Resume.PDF", size=len(pdf_bytes), data=pdf_bytes)
    )
    assert ok is True
    assert reason == ""
    text = extract_text_from_pdf(BytesIO(pdf_bytes))
    assert "Resume Text" in text


def test_unsupported_extension_rejected_strict():
    with pytest.raises(file_security_service.UnsupportedFileTypeError):
        validate_resume_file(
            _fake_file(name="resume.txt", data=_MIN_PDF), strict=True
        )
    ok, reason = validate_resume_file(_fake_file(name="resume.docx"))
    assert ok is False
    assert "PDF" in reason
    assert is_allowed_file_type("Resume.PDF") is True
    assert is_allowed_file_type("resume.docx") is False


def test_oversized_rejected_strict():
    with pytest.raises(file_security_service.FileTooLargeError):
        validate_resume_file(
            _fake_file(
                name="resume.pdf",
                size=file_security_service.MAX_UPLOAD_BYTES + 1,
                data=_MIN_PDF,
            ),
            strict=True,
        )
    ok, reason = validate_resume_file(
        _fake_file(
            name="resume.pdf",
            size=file_security_service.MAX_UPLOAD_BYTES + 1,
            data=_MIN_PDF,
        )
    )
    assert ok is False
    assert "MB" in reason


def test_empty_file_rejected_strict():
    with pytest.raises(file_security_service.EmptyFileError):
        validate_resume_file(_fake_file(name="resume.pdf", data=b""), strict=True)
    ok, reason = validate_resume_file(_fake_file(name="resume.pdf", data=b""))
    assert ok is False
    assert "empty" in reason.lower()


def test_fake_pdf_without_magic_bytes_rejected():
    ok, reason = validate_resume_file(
        _fake_file(name="resume.pdf", size=10, data=b"<html>not a pdf</html>")
    )
    assert ok is False
    assert "valid PDF" in reason
    with pytest.raises(file_security_service.InvalidPdfError):
        validate_resume_file(
            _fake_file(name="resume.pdf", size=10, data=b"<html>not a pdf</html>"),
            strict=True,
        )


def test_malformed_pdf_parses_to_empty_and_page_raises_clean_error():
    malformed = b"%PDF-1.7\n" + b"\x00\x01garbage" * 64
    ok, reason = validate_resume_file(
        _fake_file(name="resume.pdf", size=len(malformed), data=malformed)
    )
    assert ok is True  # magic bytes present, so it passes pre-parse validation
    assert extract_text_from_pdf(BytesIO(malformed)) == ""
    with pytest.raises(ValueError) as exc_info:
        build_resume_profile(BytesIO(malformed))
    assert "did not contain readable text" in str(exc_info.value)


def test_is_pdf_bytes_checks_magic_prefix():
    assert is_pdf_bytes(b"%PDF-1.7")
    assert not is_pdf_bytes(b"")
    assert not is_pdf_bytes(b"PDF-")


# ---------------------------------------------------------------------------
# Path-traversal / filename sanitization (union of both helper names)
# ---------------------------------------------------------------------------
def test_sanitize_strips_path_traversal():
    hostile = "../../../etc/passwd"
    cleaned = sanitize_filename(hostile)
    assert "/" not in cleaned and "\\" not in cleaned
    assert os.path.isabs(cleaned) is False
    assert os.path.basename(cleaned) == cleaned  # bare label — can't escape a dir


def test_sanitize_strips_separators_and_control_chars():
    hostile = ".. \\ evil\r\n  resume.pdf"
    cleaned = sanitize_filename(hostile)
    assert "/" not in cleaned and "\\" not in cleaned
    assert "\r" not in cleaned and "\n" not in cleaned
    assert cleaned.endswith(".pdf")
    assert safe_display_filename(hostile) == cleaned


def test_sanitize_falls_back_on_hostile_or_empty():
    assert sanitize_filename("   ") == "resume.pdf"
    assert sanitize_filename("") == "resume.pdf"
    assert sanitize_filename(None) == "resume.pdf"


def test_sanitize_truncates_long_names_keeping_extension():
    long_name = "x" * 500 + ".pdf"
    cleaned = sanitize_filename(long_name)
    assert len(cleaned) == 255
    assert cleaned.endswith(".pdf")


# ---------------------------------------------------------------------------
# Hashing + duplicate detection
# ---------------------------------------------------------------------------
def test_sha256_consistency_two_inputs():
    a, b = b"resume one", b"resume two"
    assert calculate_file_hash(a) == "47a73cd658690be80672a21705752d3d1c84a8bbad9b3bdb59d96ecb287a97fd"
    assert calculate_file_hash(a) == calculate_file_hash(a)
    assert calculate_file_hash(a) != calculate_file_hash(b)
    assert calculate_file_hash_file(BytesIO(a)) == calculate_file_hash(a)
    assert calculate_file_hash_file(_fake_file(data=a)) == calculate_file_hash(a)


def test_duplicate_file_hash_reuses_version():
    candidate = create_candidate(resume_hash="c1", resume_filename="a.pdf")
    h = calculate_file_hash(b"same bytes")
    v1 = create_or_get_resume_version(
        candidate_id=candidate.id, file_hash=h, filename="a.pdf"
    )
    v1_again = create_or_get_resume_version(
        candidate_id=candidate.id, file_hash=h, filename="a.pdf"
    )
    assert v1_again.id == v1.id
    assert resume_version_count(candidate.id) == 1


def test_distinct_hashes_create_numbered_versions():
    candidate = create_candidate(resume_hash="c2", resume_filename="a.pdf")
    v1 = create_or_get_resume_version(
        candidate_id=candidate.id,
        file_hash=calculate_file_hash(b"v1"),
        filename="a.pdf",
    )
    v2 = create_or_get_resume_version(
        candidate_id=candidate.id,
        file_hash=calculate_file_hash(b"v2"),
        filename="b.pdf",
    )
    assert v2.version_number == v1.version_number + 1
    assert len(get_resume_versions(candidate.id)) == 2
    assert resume_version_count(candidate.id) == 2


# ---------------------------------------------------------------------------
# Temporary-file lifecycle
# ---------------------------------------------------------------------------
def test_temp_file_create_and_cleanup(monkeypatch, tmp_path):
    scratch = _temp_scratch(monkeypatch, tmp_path)
    path = create_secure_temp_file(prefix="tq_", suffix=".part")
    assert path.startswith(os.path.abspath(str(scratch)))
    assert os.path.exists(path)
    assert scratch.is_dir()
    cleanup_temp_file(path)
    assert not os.path.exists(path)
    cleanup_temp_file(path)  # idempotent — must not raise


# ---------------------------------------------------------------------------
# PDF report: special-char escaping + in-memory (never writes a temp file)
# ---------------------------------------------------------------------------
def test_report_generation_escapes_hostile_content():
    from modules.pdf_report import generate_pdf_report

    candidate = {"name": '<b>& "quote"</b>', "email": "a<b>@c&.com"}
    report = generate_pdf_report(
        candidate=candidate,
        summary="Summary with **bold** and <br/> and a|pipe",
        ats_score=72,
        skills=["R&Python", "<SQL>"],
        match_score=61.5,
        missing_skills=["K8s"],
        ai_analysis="Analysis <script>& tags</script>",
        questions="Q: `code` **x**",
    )
    assert isinstance(report, bytes)
    assert report.startswith(b"%PDF-")
    assert report.endswith(b"%%EOF\n") or b"%%EOF" in report


def test_report_failure_never_leaves_temp_file(monkeypatch, tmp_path):
    from modules import pdf_report as pdf_report_module
    from modules.pdf_report import generate_pdf_report

    scratch = _temp_scratch(monkeypatch, tmp_path)

    class _Boom:
        def build(self, content):
            raise RuntimeError("build exploded")

    monkeypatch.setattr(
        pdf_report_module, "SimpleDocTemplate", lambda *a, **k: _Boom()
    )

    with pytest.raises(RuntimeError):
        generate_pdf_report(
            candidate={"name": "A", "email": "a@b.com"},
            summary="s",
            ats_score=1,
            skills=[],
            match_score=None,
            missing_skills=[],
            ai_analysis="a",
            questions="q",
        )

    assert not scratch.exists() or not list(scratch.iterdir())


# ---------------------------------------------------------------------------
# Safe persisted metadata (storage path invariant)
# ---------------------------------------------------------------------------
def test_safe_storage_path_never_uses_raw_filename():
    candidate = create_candidate(
        resume_hash="c3",
        resume_filename=sanitize_filename("../../etc/passwd"),
    )
    assert candidate.resume_filename.endswith("passwd")
    assert "/" not in candidate.resume_filename
    assert "\\" not in candidate.resume_filename
    assert os.path.basename(candidate.resume_filename) == candidate.resume_filename

    version = create_or_get_resume_version(
        candidate_id=candidate.id,
        file_hash=calculate_file_hash(b"x"),
        filename=sanitize_filename("..\\..\\windows\\system32\\evil.pdf"),
    )
    assert version.filename.endswith("evil.pdf")
    assert "/" not in version.filename and "\\" not in version.filename
    assert os.path.basename(version.filename) == version.filename


# ---------------------------------------------------------------------------
# Security-event logging stays clean (no filenames/content in the log)
# ---------------------------------------------------------------------------
def test_file_event_vocabulary_mapping():
    assert file_event_vocabulary("Only PDF resumes are supported.")[0] == UPLOAD_REJECTED
    assert file_event_vocabulary("The file is larger than 10 MB and was skipped.")[0] == UPLOAD_REJECTED
    assert file_event_vocabulary("The uploaded file is empty.")[0] == FILE_INVALID
    assert file_event_vocabulary("The file does not appear to be a valid PDF.")[0] == PDF_INVALID
    action, description = file_event_vocabulary("Only PDF resumes are supported.")
    assert (action, description) == (UPLOAD_REJECTED, "Unsupported file type rejected.")


def test_rejected_upload_writes_clean_audit_event():
    log_rejected_upload(None, "Only PDF resumes are supported.", resource_id="abc123")
    events = list_audit_events(user=None)
    assert events["total"] == 1
    event = events["events"][0]
    assert event["action"] == UPLOAD_REJECTED
    assert event["resource_type"] == "file"
    assert "secret" not in event["description"]
    assert event["description"] == "Unsupported file type rejected."


# ---------------------------------------------------------------------------
# Regression: the normal resume-analysis flow still works end to end
# ---------------------------------------------------------------------------
def test_normal_resume_analysis_still_works(fresh_db):
    from modules.ats_score import analyze_ats
    from modules.skills import extract_skills
    from services.analysis_service import save_analysis

    pdf_bytes = _make_pdf_bytes()
    uploaded = _fake_file(name="resume.pdf", size=len(pdf_bytes), data=pdf_bytes)

    ok, reason = validate_resume_file(uploaded)
    assert ok is True

    profile = build_resume_profile(BytesIO(pdf_bytes))
    assert profile.raw_text.strip()
    assert "Resume Text" in profile.raw_text

    ats = analyze_ats(profile)
    skills = extract_skills(profile.raw_text)
    resume_hash = calculate_file_hash_file(uploaded)

    candidate = create_candidate(
        resume_hash=resume_hash,
        name=profile.name or "resume.pdf",
        email=profile.email or None,
        resume_filename=sanitize_filename(uploaded.name),
    )
    save_analysis(
        candidate_id=candidate.id,
        jd_id=None,
        ats_score=ats.overall_score,
        jd_match_score=0.0,
        final_score=round(ats.overall_score * 0.4, 2),
        matched_skills=skills,
        missing_required_skills=[],
        missing_preferred_skills=[],
    )
    version = create_or_get_resume_version(
        candidate_id=candidate.id,
        file_hash=resume_hash,
        filename=sanitize_filename(uploaded.name),
    )

    assert candidate.resume_hash == resume_hash
    assert version.file_hash == resume_hash
    assert resume_version_count(candidate.id) == 1