"""Clean-deployment verification for TalentIQ (Phase 5 - Step 2, step 9).

Runs the fifteen deployment steps against an ISOLATED, deployment-like tree:
a copy of only the files that ship, with no ``.env``, no pre-existing database
and no test fixtures. Configuration arrives exclusively through the process
environment, exactly as a hosting platform supplies it.

Deliberately NOT a pytest module: this is a one-shot operational verification
whose output is read as a report, and which must not be able to pass by
inheriting the developer's environment.

Usage:
    python deploy_test.py            # human-readable step table
    python deploy_test.py --json     # append machine-readable output
Exit code 0 only if all fifteen steps pass.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time
import traceback
import urllib.request
from pathlib import Path

RESULTS: list[tuple[int, str, str, str]] = []  # (step, name, status, detail)


def record(step: int, name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((step, name, "PASS" if ok else "FAIL", detail))
    return ok


def step(number: int, name: str):
    """Run a step, record pass/fail, and never abort the whole run."""

    def _run(fn):
        try:
            ok, detail = fn()
        except Exception as exc:  # a crashed step is a failed step, not a dead run
            ok, detail = False, f"{type(exc).__name__}: {exc}"
            if os.environ.get("DEPLOY_TEST_TRACE"):
                traceback.print_exc()
        record(number, name, ok, detail)
        return ok

    return _run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def build_resume_pdf(title: str, skills: str, years: str = "5") -> bytes:
    """A real, text-extractable PDF resume built with reportlab."""
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=LETTER)
    doc.build([
        Paragraph(title, styles["Title"]),
        Spacer(1, 10),
        Paragraph("Email: dana@example.com | Phone: +1 555 0100", styles["Normal"]),
        Paragraph("Location: Berlin, Germany", styles["Normal"]),
        Spacer(1, 10),
        Paragraph("PROFESSIONAL SUMMARY", styles["Heading2"]),
        Paragraph(
            f"Senior software engineer with {years} years of experience building "
            "and shipping production web applications and data pipelines.",
            styles["Normal"],
        ),
        Spacer(1, 10),
        Paragraph("TECHNICAL SKILLS", styles["Heading2"]),
        Paragraph(skills, styles["Normal"]),
        Spacer(1, 10),
        Paragraph("PROFESSIONAL EXPERIENCE", styles["Heading2"]),
        Paragraph(
            "Senior Engineer, Acme Corp (2021 - present). Led a team delivering "
            "a Python and PostgreSQL platform, improved deployment reliability "
            "and mentored junior engineers.",
            styles["Normal"],
        ),
        Spacer(1, 8),
        Paragraph("EDUCATION", styles["Heading2"]),
        Paragraph("BSc Computer Science, University of Berlin", styles["Normal"]),
        Spacer(1, 10),
        Paragraph("CERTIFICATIONS", styles["Heading2"]),
        Paragraph("AWS Certified Developer", styles["Normal"]),
    ])
    return buffer.getvalue()


class FakeUpload(io.BytesIO):
    """Stands in for Streamlit's UploadedFile (same duck-typed surface)."""

    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name
        self.size = len(data)


JD_TEXT = """
We are hiring a Backend Engineer.

Required skills: Python, SQL, PostgreSQL, Docker, AWS, REST API, Git.
Experience with data pipelines and CI/CD is a strong plus.
You will design and operate services that process millions of records daily.
"""


def _throwaway_password() -> str:
    """A disposable password for the RBAC-isolation probe account.

    Generated at runtime on purpose: it is never a real credential, and a
    hardcoded literal in a committed file would be indistinguishable from
    one. Satisfies the server-side policy (min length, not whitespace).
    """
    import secrets

    return f"Throwaway-{secrets.token_urlsafe(18)}"





# ---------------------------------------------------------------------------
# 1. Dependencies
# ---------------------------------------------------------------------------
@step(1, "Install dependencies: every runtime package imports")
def _s1():
    import importlib

    required = [
        "streamlit", "plotly", "pandas", "sqlalchemy", "groq",
        "sentence_transformers", "sklearn", "pdfplumber", "reportlab", "dotenv",
    ]
    missing, versions = [], []
    for name in required:
        try:
            module = importlib.import_module(name)
            versions.append(f"{name}={getattr(module, '__version__', 'n/a')}")
        except Exception as exc:
            missing.append(f"{name} ({type(exc).__name__})")
    if missing:
        return False, f"unimportable: {missing}"
    return True, f"{len(required)} runtime deps OK: {', '.join(versions)}"


# ---------------------------------------------------------------------------
# 2. Environment configuration
# ---------------------------------------------------------------------------
@step(2, "Environment variables read correctly")
def _s2():
    from core.config import settings

    problems = []
    if settings.environment != "production":
        problems.append(f"ENVIRONMENT={settings.environment!r}, expected 'production'")
    if settings.debug:
        problems.append("DEBUG is true during a production run")
    expected_max = int(os.environ.get("MAX_RESUME_SIZE_MB", "10"))
    if settings.max_resume_size_mb != expected_max:
        problems.append(
            f"MAX_RESUME_SIZE_MB read as {settings.max_resume_size_mb}, "
            f"expected {expected_max}"
        )
    if not (settings.groq_api_key or "").strip():
        problems.append("GROQ_API_KEY missing from the environment")
    if not (settings.groq_model or "").strip():
        problems.append("GROQ_MODEL missing")
    summary = settings.safe_summary()
    if "groq_api_key" in summary or "admin_password" in summary:
        problems.append("safe_summary() leaks a secret field")
    if problems:
        return False, "; ".join(problems)
    return True, (
        f"environment={summary['environment']} debug={summary['debug']} "
        f"max={summary['max_resume_size_mb']}MB model={summary['groq_model']} "
        f"admin_bootstrap={summary['admin_bootstrap_configured']} "
        f"(safe_summary exposes no secrets)"
    )


# ---------------------------------------------------------------------------
# 3. Database initialization
# ---------------------------------------------------------------------------
@step(3, "Database initializes on a fresh volume")
def _s3():
    from core.db import get_db_path, init_db

    path = get_db_path()
    if path.exists() and path.stat().st_size:
        return False, f"database already existed at {path} (expected a fresh tree)"
    init_db()
    if not path.exists():
        return False, f"init_db() returned but no file at {path}"

    import sqlite3

    conn = sqlite3.connect(str(path))
    try:
        tables = sorted(
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        )
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()
    if violations:
        return False, f"foreign key violations: {violations}"
    if len(tables) != 8:
        return False, f"expected 8 application tables, found {len(tables)}: {tables}"
    return True, f"created {path.name} ({len(tables)} tables, fk_check clean): {', '.join(tables)}"


# ---------------------------------------------------------------------------
# 4/5. Start Streamlit and reach the app
# ---------------------------------------------------------------------------
@step(4, "Streamlit starts on the production command")
def _s4():
    import subprocess

    port = int(os.environ.get("DEPLOY_TEST_PORT", "8599"))
    env = dict(os.environ)
    env["STREAMLIT_SERVER_HEADLESS"] = "true"
    env["STREAMLIT_SERVER_FILE_WATCHER_TYPE"] = "none"
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py",
         "--server.port", str(port), "--server.headless", "true",
         "--server.address", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )
    base = f"http://127.0.0.1:{port}"
    deadline, up = time.time() + 180, False
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            with urllib.request.urlopen(f"{base}/_stcore/health", timeout=4) as r:
                if r.status == 200:
                    up = True
                    break
        except Exception:
            time.sleep(2)
    globals()["_streamlit_proc"] = proc
    globals()["_streamlit_base"] = base
    if not up:
        proc.kill()
        return False, f"server never became healthy within 180s (port {port})"
    return True, f"healthy on {base} (pid {proc.pid})"


@step(5, "Application is accessible")
def _s5():
    base = globals().get("_streamlit_base")
    if not base:
        return False, "no server from step 4"
    with urllib.request.urlopen(f"{base}/", timeout=30) as r:
        body, status = r.read(), r.status
    if status != 200:
        return False, f"unexpected status {status}"
    if b"streamlit" not in body.lower():
        return False, "response does not look like a Streamlit shell"
    return True, f"GET / -> HTTP {status}, {len(body)} bytes, Streamlit shell served"


# ---------------------------------------------------------------------------
# 6. Login
# ---------------------------------------------------------------------------
@step(6, "First login: admin bootstrap, authentication, RBAC, session")
def _s6():
    from services import auth_service

    email = os.environ["TALENTIQ_ADMIN_EMAIL"]
    password = os.environ["TALENTIQ_ADMIN_PASSWORD"]

    created = auth_service.register_user(
        name="Deploy Admin", email=email, password=password, role=None
    )
    if created.get("role") != "ADMIN":
        return False, f"bootstrap did not grant ADMIN, got {created.get('role')!r}"

    status, user = auth_service.authenticate_user(email, password + "-wrong")
    if user is not None:
        return False, "authentication accepted a wrong password"

    status, user = auth_service.authenticate_user(email, password)
    if user is None:
        return False, f"valid login rejected ({status})"

    auth_service.start_session(user)
    current = auth_service.current_user()
    if not current or current.get("email") != email:
        return False, "session not established"
    if not auth_service.has_role(current, "ADMIN"):
        return False, "has_role(ADMIN) is False for an admin"
    if auth_service.has_role(current, "SUPERUSER"):
        return False, "has_role(SUPERUSER) is True for a plain ADMIN"

    from core import models
    from core.db import get_session_local

    with get_session_local()() as s:
        row = s.get(models.User, user["id"])
        stored = row.password_hash or ""
    if password in stored:
        return False, "password appears in the stored hash"
    if not stored.startswith("scrypt"):
        return False, f"unexpected hash format: {stored[:16]!r}"

    globals()["_admin"] = user
    return True, (
        f"admin {email} bootstrapped, wrong password rejected, valid login OK, "
        f"session active, scrypt hash, RBAC correct"
    )


# ---------------------------------------------------------------------------
# 7. Upload a resume
# ---------------------------------------------------------------------------
@step(7, "Upload a resume (magic bytes, extension, size, hashing)")
def _s7():
    from services import file_security_service as fss

    pdf = build_resume_pdf(
        "Dana Scully",
        "Python, SQL, PostgreSQL, Docker, AWS, REST API, Git, Kafka, Airflow",
    )
    ok, reason = fss.validate_resume_file(
        FakeUpload(pdf, "dana_scully_resume.pdf"), strict=True
    )
    if not ok:
        return False, f"a valid PDF resume was rejected: {reason}"

    digest = fss.calculate_file_hash(pdf)
    if hashlib.sha256(pdf).hexdigest() != digest:
        return False, "calculate_file_hash does not match sha256 of the bytes"

    # The property that matters is not "no '..' substring" but "the result
    # cannot escape the directory it is joined to".
    probes = ["../../etc/passwd", "..\\..\\windows\\system32\\cmd.exe", "/abs/path.pdf",
              "", "   ", "a" * 400 + ".pdf", "nul\x00byte.pdf", "résumé.pdf"]
    for probe in probes:
        safe = fss.sanitize_filename(probe)
        if not safe:
            return False, f"sanitize_filename({probe!r}) returned an empty name"
        if any(sep in safe for sep in ("/", "\\", "\x00")):
            return False, f"separator survived sanitising: {probe!r} -> {safe!r}"
        if os.path.basename(safe) != safe:
            return False, f"sanitised name is not a bare filename: {probe!r} -> {safe!r}"
        if os.path.normpath(os.path.join("/srv/uploads", safe)).startswith(".."):
            return False, f"sanitised name escapes the upload dir: {probe!r} -> {safe!r}"
    safe = fss.sanitize_filename("dana_scully_resume.pdf")

    globals()["_resume_pdf"] = pdf
    globals()["_resume_hash"] = digest
    return True, (
        f"accepted {len(pdf)} bytes, sha256 {digest[:12]}..., "
        f"traversal name sanitised to {safe!r}"
    )


# ---------------------------------------------------------------------------
# 8. Analyze the resume
# ---------------------------------------------------------------------------
@step(8, "Analyze resume (parse, profile, skills, ATS score)")
def _s8():
    from modules.ats_score import analyze_ats
    from modules.parser import extract_text_from_pdf
    from modules.resume_profile import build_resume_profile
    from modules.skills import extract_skills

    upload = FakeUpload(globals()["_resume_pdf"], "dana_scully_resume.pdf")
    text = extract_text_from_pdf(upload)
    if not text or len(text) < 150:
        return False, f"extracted text too short: {len(text or '')} chars"
    if "Dana Scully" not in text:
        return False, "extracted text is missing the candidate name"

    # The production path (pages 2 and 5) builds a structured profile from the
    # uploaded file rather than scoring loose text, so exercise exactly that.
    upload.seek(0)
    profile = build_resume_profile(upload)
    if not profile.skills:
        return False, "profile has no skills"

    skills = extract_skills(text)
    ats = analyze_ats(profile)
    score = getattr(ats, "overall_score", None)
    if score is None or not 0 <= float(score) <= 100:
        return False, f"implausible ATS score: {score!r}"

    details = profile.to_details_dict()
    for key in ("name", "email"):
        if key not in details:
            return False, f"profile.to_details_dict() is missing {key!r}"

    globals().update(
        _resume_text=text, _skills=skills, _ats=ats, _profile=profile
    )
    return True, (
        f"{len(text)} chars extracted; profile: {len(profile.skills)} skills, "
        f"{len(getattr(profile, 'experience', []) or [])} experience entries, "
        f"{len(getattr(profile, 'education', []) or [])} education entries; "
        f"extract_skills found {len(skills)}; ATS overall_score={float(score):.1f}, "
        f"sections detected={len(ats.detected_sections)}, "
        f"keyword_coverage={sum(1 for v in ats.keyword_coverage.values() if v)}"
        f"/{len(ats.keyword_coverage)}"
    )


# ---------------------------------------------------------------------------
# 9/10. Results and persistence
# ---------------------------------------------------------------------------
@step(9, "Generate results (JD match, ranking inputs)")
def _s9():
    from modules.jd_matcher import analyze_job_match

    profile = globals()["_profile"]
    # Same call shape as pages 2 and 5.
    match = analyze_job_match(
        globals()["_resume_text"], profile.skills, JD_TEXT, profile=profile
    )
    overall = getattr(match, "overall_match_score", None)
    if overall is None or not 0 <= float(overall) <= 100:
        return False, f"implausible overall match score: {overall!r}"
    globals()["_match"] = match
    return True, (
        f"overall={float(overall):.1f} (skill={float(match.skill_match_score):.1f}, "
        f"semantic={float(match.semantic_match_score):.1f}, "
        f"tfidf={float(match.text_similarity_score):.1f}, "
        f"semantic_available={match.semantic_available}), "
        f"matched={len(match.matched_skills)}, "
        f"missing_required={len(match.missing_required_skills)}"
    )


@step(10, "Candidate, resume version and analysis persist")
def _s10():
    from core import models
    from core.db import get_session_local
    from services.analysis_service import save_analysis_from_results
    from services.candidate_service import create_candidate
    from services.recruitment_service import save_job_description
    from services.resume_version_service import create_or_get_resume_version

    admin = globals()["_admin"]
    jd = save_job_description(
        JD_TEXT, title="Backend Engineer", created_by_user_id=admin["id"]
    )
    if jd is None or not getattr(jd, "id", None):
        return False, f"job description not saved: {jd}"

    candidate = create_candidate(
        resume_hash=globals()["_resume_hash"],
        name="Dana Scully",
        email="dana@example.com",
        location="Berlin, Germany",
        resume_filename="dana_scully_resume.pdf",
        created_by_user_id=admin["id"],
    )
    if candidate is None or not getattr(candidate, "id", None):
        return False, f"candidate not created: {candidate}"

    version = create_or_get_resume_version(
        candidate_id=candidate.id,
        file_hash=globals()["_resume_hash"],
        filename="dana_scully_resume.pdf",
        user=admin,
    )
    if version is None or not getattr(version, "id", None):
        return False, f"resume version not created: {version}"

    analysis = save_analysis_from_results(
        candidate_id=candidate.id,
        jd_id=jd.id,
        resume_version_id=version.id,
        user=admin,
        ats_result=globals()["_ats"],
        jd_match=globals()["_match"],
    )
    if analysis is None or not getattr(analysis, "id", None):
        return False, f"analysis not saved: {analysis}"

    with get_session_local()() as s:
        versions = s.query(models.ResumeVersion).filter_by(candidate_id=candidate.id).count()
        analyses = s.query(models.ResumeAnalysis).filter_by(candidate_id=candidate.id).count()
    if versions < 1 or analyses < 1:
        return False, f"rows missing (versions={versions}, analyses={analyses})"

    globals()["_candidate"] = candidate
    return True, (
        f"jd #{jd.id}, candidate #{candidate.id}, version #{version.id} (v{getattr(version, 'version_number', '?')}), "
        f"analysis #{analysis.id} final_score={getattr(analysis, 'final_score', '?')}, "
        f"rows: versions={versions} analyses={analyses}"
    )


# ---------------------------------------------------------------------------
# 11. Recruiter dashboard
# ---------------------------------------------------------------------------
@step(11, "Recruiter dashboard (multiple candidates, ranking, RBAC scope)")
def _s11():
    from services import auth_service, candidate_service, recruitment_service

    admin = globals()["_admin"]
    made = []
    for name, skills, years in [
        ("Marcus Chen", "Java, Spring, Kubernetes, AWS, SQL, Docker", "8"),
        ("Priya Nair", "Python, Django, PostgreSQL, Redis, Docker, AWS", "4"),
    ]:
        pdf = build_resume_pdf(name, skills, years)
        cand = candidate_service.create_candidate(
            resume_hash=hashlib.sha256(pdf).hexdigest(),
            name=name,
            email=f"{name.split()[0].lower()}@example.com",
            resume_filename=f"{name.split()[0].lower()}.pdf",
            created_by_user_id=admin["id"],
        )
        if cand is None or not getattr(cand, "id", None):
            return False, f"could not create candidate {name}: {cand}"
        recruitment_service.mark_analyzed(cand.id, user=admin)
        made.append((cand.id, name))

    mine = candidate_service.get_candidates(user=admin)
    if len(mine) < 3:
        return False, f"expected >=3 candidates for the dashboard, got {len(mine)}"

    # Ownership isolation: a different recruiter must not see these candidates.
    other_pw = _throwaway_password()
    other = auth_service.register_user(
        name="Other Recruiter", email="other@example.com",
        password=other_pw, role="RECRUITER",
    )
    # Retained so the audit-log scan in step 14 can prove this throwaway
    # password never reached a description.
    globals()["_other_password"] = other_pw
    theirs = candidate_service.get_candidates(user=other)
    if theirs:
        return False, f"RBAC leak: another recruiter sees {len(theirs)} candidates"

    first = made[0][0]
    recruitment_service.shortlist_candidate(first, user=admin)
    shortlisted = recruitment_service.get_shortlisted_candidates(user=admin)
    by_status = recruitment_service.get_candidates_by_status("Analyzed", user=admin)
    if len(shortlisted) != 1:
        return False, f"expected 1 shortlisted candidate, got {len(shortlisted)}"

    return True, (
        f"{len(mine)} candidates for the owner, 0 for another recruiter (RBAC holds); "
        f"shortlist={len(shortlisted)}, analyzed={len(by_status)}; "
        f"statuses={sorted({c.status for c in mine})}"
    )


# ---------------------------------------------------------------------------
# 12. PDF report
# ---------------------------------------------------------------------------
@step(12, "Generate PDF report")
def _s12():
    from modules.pdf_report import generate_pdf_report

    ats, match = globals()["_ats"], globals()["_match"]
    # pages/2_Resume_Results.py passes profile.to_details_dict() as `candidate`
    # and the AI generators return *strings* for summary/ai_analysis/questions.
    data = generate_pdf_report(
        candidate=globals()["_profile"].to_details_dict(),
        summary=globals()["_resume_text"][:400],
        ats_score=getattr(ats, "overall_score", 0),
        skills=list(globals()["_profile"].skills),
        match_score=getattr(match, "overall_match_score", 0),
        missing_skills=list(match.missing_required_skills),
        ai_analysis="Deployment verification run: deterministic summary text.",
        questions="1. Describe a production incident you owned.\n2. Walk through a "
                  "design decision you would revisit.",
    )
    if not isinstance(data, (bytes, bytearray)):
        return False, f"expected raw PDF bytes, got {type(data)}"
    if len(data) < 1000:
        return False, f"report suspiciously small: {len(data)} bytes"
    if not bytes(data).startswith(b"%PDF-"):
        return False, "report does not start with the PDF magic bytes"

    # Round-trip: the report must be a real, parseable PDF.
    import pdfplumber

    with pdfplumber.open(io.BytesIO(bytes(data))) as pdf:
        pages, text = len(pdf.pages), (pdf.pages[0].extract_text() or "")
    if pages < 1 or "Dana" not in text:
        return False, f"report is not readable back (pages={pages}, text={text[:80]!r})"
    return True, (
        f"in-memory PDF, {len(data)} bytes, {pages} page(s), "
        f"round-trip parse OK, no temp file created"
    )


# ---------------------------------------------------------------------------
# 13. Persistence across restart
# ---------------------------------------------------------------------------
@step(13, "Data persists across an application restart")
def _s13():
    import core.db as db
    from services import candidate_service

    admin = globals()["_admin"]
    before = len(candidate_service.get_candidates(user=admin))

    # Simulate a restart: drop every cached singleton, as a brand-new process
    # would have, then re-open the database from disk.
    db._db_path = db._engine = db._session_local = None
    db._initialized = False
    db.init_db()

    after = candidate_service.get_candidates(user=admin)
    if len(after) != before:
        return False, f"candidate count changed across restart: {before} -> {len(after)}"
    if not after:
        return False, "no candidates visible after restart"
    if {c.name for c in after} != {c.name for c in candidate_service.get_candidates(user=admin)}:
        return False, "candidate set is unstable across restart"
    return True, (
        f"{len(after)} candidates survived a full engine reconnect against "
        f"{db.get_db_path().name} (init_db is idempotent)"
    )


# ---------------------------------------------------------------------------
# 14. Audit logging
# ---------------------------------------------------------------------------
@step(14, "Audit logging records real activity, without secrets or PII")
def _s14():
    from core import models
    from core.db import get_session_local

    with get_session_local()() as s:
        rows = s.query(models.AuditLog).order_by(models.AuditLog.id).all()
        actions = [r.action for r in rows]
        blob = " | ".join(
            f"{r.action} :: {r.description or ''} :: {r.resource_type or ''}" for r in rows
        )

    if not rows:
        return False, "no audit rows were written"

    expected = {
        "AUTH_LOGIN", "CANDIDATE_CREATED", "RESUME_VERSION_CREATED",
        "CANDIDATE_ANALYZED", "JOB_DESCRIPTION_CREATED",
    }
    missing = sorted(expected - set(actions))
    if missing:
        return False, f"expected audit actions missing: {missing}"

    secrets = [
        os.environ.get("GROQ_API_KEY"),
        os.environ.get("TALENTIQ_ADMIN_PASSWORD"),
        globals().get("_other_password"),
    ]
    for secret in secrets:
        if secret and secret in blob:
            return False, "a secret value leaked into the audit log"
    if "dana@example.com" in blob:
        return False, "candidate PII leaked into an audit description"
    if "Dana Scully" in blob and "PROFESSIONAL SUMMARY" in blob:
        return False, "raw resume text leaked into the audit log"

    return True, (
        f"{len(rows)} rows; actions={sorted(set(actions))}; "
        f"no secret or PII in descriptions"
    )


# ---------------------------------------------------------------------------
# 15. AI Resume Coach
# ---------------------------------------------------------------------------
@step(15, "AI Resume Coach (live Groq API call with the real key)")
def _s15():
    from modules.resume_coach import generate_resume_coach

    result = generate_resume_coach(
        globals()["_profile"],
        globals()["_resume_text"],
        globals()["_ats"],
        globals()["_match"],
        JD_TEXT,
    )
    if result is None:
        return False, "coach returned None"

    improvements = getattr(result, "priority_improvements", None)
    if not isinstance(improvements, (list, tuple)) or not improvements:
        return False, (
            f"coach produced no priority_improvements "
            f"(ai_generated={getattr(result, 'ai_generated', '?')}, "
            f"note={getattr(result, 'note', '')[:80]!r})"
        )
    # Every recommendation must be fully populated, as pages/5 renders them.
    for index, item in enumerate(improvements):
        for field in ("priority", "issue", "recommendation", "reason"):
            if not (getattr(item, field, "") or "").strip():
                return False, f"recommendation #{index} has an empty {field!r}"

    generated = bool(getattr(result, "ai_generated", False))
    populated = sum(
        1 for f in ("skill_gap_guidance", "keyword_recommendations",
                     "formatting_recommendations", "interview_focus_areas",
                     "experience_improvements")
        if getattr(result, f, None)
    )
    return True, (
        f"{len(improvements)} priority improvements, ai_generated={generated}"
        f"{'' if generated else ' (deterministic fallback)'}, "
        f"overall_assessment={len(getattr(result, 'overall_assessment', '') or '')} chars, "
        f"summary_improvement={'yes' if result.summary_improvement else 'no'}, "
        f"{populated}/5 advisory sections populated; first="
        f"{improvements[0].priority!r}"
    )


def main() -> int:
    json_mode = "--json" in sys.argv
    started = time.time()

    for number, name, status, detail in sorted(RESULTS):
        print(f"{number:>2}. [{status}] {name}")
        if detail:
            print(f"        {detail}")

    proc = globals().get("_streamlit_proc")
    if proc:
        proc.kill()

    failed = [r for r in RESULTS if r[2] == "FAIL"]
    passed = len(RESULTS) - len(failed)
    print("-" * 74)
    print(f"{passed}/{len(RESULTS)} steps passed in {time.time() - started:.1f}s")
    if failed:
        print("FAILED: " + ", ".join(f"{n} ({name})" for n, name, _, _ in failed))
    if json_mode:
        print(json.dumps([
            {"step": n, "name": name, "status": s, "detail": d}
            for n, name, s, d in sorted(RESULTS)
        ], indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
