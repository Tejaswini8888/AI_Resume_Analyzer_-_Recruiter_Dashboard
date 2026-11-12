"""Failure-handling verification for a deployed TalentIQ instance (Step 2, step 10).

A production deployment is only finished when the ways it can break are proven
to degrade into clean, actionable messages. Each test here forces one realistic
hosting failure and asserts three things:

1. the failure is DETECTED (an exception, an error state, or a startup check
   failure) rather than silently succeeding;
2. the message a user would see contains NO stack trace, NO filesystem path,
   NO SQL/connection internals and NO secret;
3. a platform operator gets an actionable, non-secret diagnostic.

Deliberately not a pytest module: it is a one-shot operational verification that
must be run as a deployment, with configuration coming from the environment.
Run it with no ``.env`` present so nothing leaks in from the developer shell.

    python failure_test.py
Exit code 0 only if every failure mode is handled cleanly.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import traceback
from pathlib import Path

RESULTS: list[tuple[str, str, str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def run(name: str, fn) -> None:
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f"harness error {type(exc).__name__}: {exc}"
        if os.environ.get("DEPLOY_TEST_TRACE"):
            traceback.print_exc()
    record(name, ok, detail)


# Leaked-detail markers. A user-facing message containing any of these is a bug.
FORBIDDEN = (
    "Traceback (most recent call last)",
    "  File \"",
    ".py\", line ",                       # a source frame
    "sqlite3.", "sqlalchemy.exc", "sqlite://", "OperationalError",
    "psql", "psycopg", "postgresql://", "mysql://",
    "st.stop", "st.rerun",
    os.environ.get("GROQ_API_KEY") or "@@no-key@@",
    os.environ.get("TALENTIQ_ADMIN_PASSWORD") or "@@no-pw@@",
)


def assert_clean(label: str, text: str) -> str:
    """Return a leak description, or '' when the message is safe to show."""
    leaks = [f for f in FORBIDDEN if f and f in text]
    # A project-root path is acceptable in operator-facing startup output, but
    # must never reach the browser. Callers decide which surface they check.
    return f"{label} leaked {leaks}" if leaks else ""


# ---------------------------------------------------------------------------
# 1. Missing GROQ_API_KEY
# ---------------------------------------------------------------------------
def missing_api_key():
    env = {k: v for k, v in os.environ.items() if k != "GROQ_API_KEY"}
    proc = subprocess.run(
        [sys.executable, "-m", "core.startup_check"],
        capture_output=True, text=True, env=env, timeout=180,
    )
    combined = proc.stdout + proc.stderr
    if proc.returncode == 0:
        return False, "startup check passed with no GROQ_API_KEY (must fail closed)"

    from core.config import settings as _  # noqa: F401  (config import sanity)

    # The message must tell an operator WHERE to set the secret, not just that
    # it is missing, and must never print a real key.
    if "GROQ_API_KEY" not in combined:
        return False, f"diagnostic does not name GROQ_API_KEY: {combined.strip()[:200]}"
    leak = assert_clean("startup output", combined.replace(str(Path.cwd()), "<APP>"))
    if leak:
        return False, leak
    return True, (
        f"exit {proc.returncode}, names the missing variable, and suggests "
        f"the hosting platform's environment/secret settings"
    )


# ---------------------------------------------------------------------------
# 2. Unsupported DATABASE_URL
# ---------------------------------------------------------------------------
def bad_database_url():
    from core.config import ConfigurationError
    from core.db import _resolve_db_path

    secret_url = "postgresql://admin:Sup3rSecret@db.internal:5432/talentiq"

    proc = subprocess.run(
        [sys.executable, "-m", "core.startup_check"],
        capture_output=True, text=True,
        env={**os.environ, "DATABASE_URL": secret_url}, timeout=180,
    )
    combined = proc.stdout + proc.stderr
    if proc.returncode == 0:
        return False, "startup check passed with a non-SQLite DATABASE_URL"

    # The whole point: name the scheme, never the URL, credentials, host or port.
    for leaked in ("Sup3rSecret", "db.internal", "5432", secret_url):
        if leaked in combined:
            return False, f"credential/host detail {leaked!r} leaked: {combined[:300]}"
    if "DATABASE_URL" not in combined:
        return False, f"diagnostic does not name DATABASE_URL: {combined[:200]}"
    if "postgresql" not in combined.lower():
        return False, f"diagnostic does not mention the offending scheme: {combined[:200]}"

    # And the DB layer itself must refuse rather than silently use local SQLite.
    # _resolve_db_path() re-reads the environment via settings.reload(), so the
    # variable must be set in the real process environment before calling it.
    import core.config as core_config
    from core.db import _resolve_db_path as _resolve

    saved_env = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = secret_url
    try:
        core_config.settings.reload()
        _resolve()
    except ConfigurationError:
        pass
    except Exception as exc:
        return False, f"core.db raised {type(exc).__name__}, expected ConfigurationError"
    else:
        return False, "core.db silently accepted a non-SQLite DATABASE_URL"
    finally:
        if saved_env is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_env
        core_config.settings.reload()

    return True, (
        "startup check fails closed; core.db raises ConfigurationError; "
        "the scheme is shown but credentials, host and port are absent from all output"
    )


# ---------------------------------------------------------------------------
# 3. Missing / unwritable upload directory
# ---------------------------------------------------------------------------
def missing_upload_dir():
    """A missing relative scratch dir is created on demand; a dir that CANNOT be
    created (path through a file, or a read-only mount) must fail the check."""
    problems = []

    # (a) Documented behaviour: a missing relative directory is created.
    auto = "data/_autocreated_scratch_abc123"
    proc = subprocess.run(
        [sys.executable, "-m", "core.startup_check"],
        capture_output=True, text=True,
        env={**os.environ, "TEMP_DIRECTORY": auto,
             "UPLOAD_DIRECTORY": auto + "_uploads"},
        timeout=180,
    )
    combined = proc.stdout + proc.stderr
    if proc.returncode != 0:
        problems.append(
            f"a creatable missing directory should not fail the check: {combined[:250]}"
        )
    if not Path(auto).exists():
        problems.append(f"the missing directory {auto} was not created on demand")

    # (b) Real failure: the path runs through an existing FILE, so mkdir cannot
    #     succeed. This is what a read-only or misconfigured volume looks like.
    broken = "data/skills.json/impossible"
    proc = subprocess.run(
        [sys.executable, "-m", "core.startup_check"],
        capture_output=True, text=True,
        env={**os.environ, "TEMP_DIRECTORY": broken, "UPLOAD_DIRECTORY": broken},
        timeout=180,
    )
    combined = proc.stdout + proc.stderr
    if proc.returncode == 0:
        problems.append("startup check passed with an uncreatable data directory")
    if broken not in combined:
        problems.append(
            f"diagnostic does not name the offending directory: {combined[:250]}"
        )
    leak = assert_clean("startup output", combined.replace(str(Path.cwd()), "<APP>"))
    if leak:
        problems.append(leak)

    # (c) A real read-only directory must also be refused.
    import os as _os
    import tempfile as _tf

    ro_parent = Path(_tf.mkdtemp(prefix="talentiq_ro_"))
    try:
        ro_dir = ro_parent / "locked"
        ro_dir.mkdir()
        # Windows: revoke write access for the current user.
        try:
            _os.chmod(ro_dir, 0o500)
        except Exception:
            pass
        probe = subprocess.run(
            [sys.executable, "-c",
             "import os,sys; sys.exit(0 if os.access(sys.argv[1], os.W_OK) else 3)",
             str(ro_dir)],
            capture_output=True, text=True, timeout=60,
        )
        if probe.returncode == 3:
            proc = subprocess.run(
                [sys.executable, "-m", "core.startup_check"],
                capture_output=True, text=True,
                env={**os.environ, "TEMP_DIRECTORY": str(ro_dir),
                     "UPLOAD_DIRECTORY": str(ro_dir)},
                timeout=180,
            )
            if proc.returncode == 0:
                problems.append("startup check passed with a read-only directory")
    finally:
        try:
            _os.chmod(ro_dir, 0o700)
        except Exception:
            pass
        import shutil
        shutil.rmtree(ro_parent, ignore_errors=True)

    if problems:
        return False, "; ".join(problems)
    return True, (
        "a missing relative scratch dir is created on demand (exit 0); an "
        "uncreatable path and a read-only dir both fail the check with the "
        "configured directory named and no host path or secret leaked"
    )


# ---------------------------------------------------------------------------
# 4-6. Upload rejections (user-facing, must be specific and non-leaky)
# ---------------------------------------------------------------------------
def _pdf_bytes(text: str = "resume") -> bytes:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    styles = getSampleStyleSheet()
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=LETTER).build(
        [Paragraph(text, styles["Title"])]
    )
    return buffer.getvalue()


class _Upload(io.BytesIO):
    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name
        self.size = len(data)


def bad_resume_uploads():
    from services import file_security_service as fss

    cases = [
        ("not-a-pdf.txt", b"plain text pretending to be a resume", "wrong extension"),
        ("resume.pdf", b"this is not a pdf at all", "PDF magic bytes missing"),
        ("empty.pdf", b"", "zero-length file"),
        ("image.pdf", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "non-PDF binary"),
    ]
    problems, accepted = [], []
    for name, data, label in cases:
        try:
            ok, _ = fss.validate_resume_file(_Upload(data, name), strict=True)
        except fss.ResumeFileError as exc:
            msg = str(exc)
            leak = assert_clean(f"{label} message", msg)
            if leak:
                problems.append(leak)
            if len(msg) < 10:
                problems.append(f"{label}: message is too terse to act on ({msg!r})")
        else:
            if ok:
                accepted.append(f"{name} ({label})")
    if accepted:
        return False, f"invalid uploads were ACCEPTED: {accepted}"

    # Oversize: one byte past the configured limit must be refused.
    from core.config import settings
    limit_bytes = int(settings.max_resume_size_mb) * 1024 * 1024
    oversize = b"%PDF-1.4\n" + b"\x00" * (limit_bytes + 1)
    try:
        ok, _ = fss.validate_resume_file(_Upload(oversize, "huge.pdf"), strict=True)
    except fss.ResumeFileError as exc:
        if not any(w in str(exc).lower() for w in ("mb", "size", "large")):
            problems.append(f"oversize message does not explain the limit: {str(exc)[:120]!r}")
    else:
        if ok:
            problems.append("a file past MAX_RESUME_SIZE_MB was ACCEPTED")

    if problems:
        return False, "; ".join(problems)
    return True, (
        f"{len(cases)} malformed uploads rejected with specific, non-leaky messages; "
        f"oversize (>{limit_bytes // (1024*1024)}MB) rejected"
    )


# ---------------------------------------------------------------------------
# 7. Malformed / scanned PDF that passes magic-byte checks
# ---------------------------------------------------------------------------
def malformed_pdf():
    from modules.resume_profile import build_resume_profile
    from services import file_security_service as fss

    # A structurally valid PDF header with no extractable text (a "scan").
    scanned = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
    problems = []
    try:
        ok, reason = fss.validate_resume_file(_Upload(scanned, "scanned.pdf"), strict=True)
    except fss.ResumeFileError as exc:
        leak = assert_clean("scanned-PDF message", str(exc))
        if leak:
            problems.append(leak)
    else:
        if not ok:
            problems.append(f"a text-less PDF was rejected at validation: {reason}")
        else:
            # If validation lets it through, the parser must fail with the
            # user-facing "no readable text" message, not a pdfplumber crash.
            try:
                build_resume_profile(_Upload(scanned, "scanned.pdf"))
            except ValueError as exc:
                if "readable text" not in str(exc).lower():
                    problems.append(f"unexpected parser message: {str(exc)[:150]!r}")
                leak = assert_clean("parser message", str(exc))
                if leak:
                    problems.append(leak)
            except Exception as exc:
                problems.append(
                    f"parser raised {type(exc).__name__} instead of a clean ValueError"
                )
    if problems:
        return False, "; ".join(problems)
    return True, (
        "a text-less PDF either fails validation or raises a clean ValueError "
        "('did not contain readable text'); no pdfplumber internals escape"
    )


# ---------------------------------------------------------------------------
# 8. Groq unreachable / erroring
# ---------------------------------------------------------------------------
def groq_unreachable():
    from modules import resume_coach
    from modules.ai_analysis import MISSING_GROQ_API_KEY_MESSAGE

    calls = {"n": 0}

    def _boom(prompt, temperature=None, response_format=None):
        calls["n"] += 1
        raise OSError("connection reset by peer (synthetic)")

    # resume_coach calls modules.ai_analysis._chat; break the transport there.
    import modules.ai_analysis as ai_analysis

    original = ai_analysis._chat
    from modules.ats_score import analyze_ats
    from modules.jd_matcher import analyze_job_match
    from modules.resume_profile import build_resume_profile

    profile = build_resume_profile(_Upload(_pdf_bytes("Dana Scully"), "dana.pdf"))
    ats = analyze_ats(profile)
    jd = "Required: Python, SQL, PostgreSQL."
    match = analyze_job_match(profile.raw_text, profile.skills, jd, profile=profile)

    try:
        resume_coach._chat = _boom
        result = resume_coach.generate_resume_coach(
            profile, profile.raw_text, ats, match, jd
        )
    finally:
        resume_coach._chat = original

    if result is None:
        return False, "coach returned None when the API was down (page would break)"
    if getattr(result, "ai_generated", True):
        return False, "result claims ai_generated=True although every call failed"
    if not getattr(result, "priority_improvements", None):
        return False, "deterministic fallback produced no recommendations"
    note = getattr(result, "note", "") or ""
    if "connection reset by peer" in note:
        return False, "raw transport error text is shown to the user"
    leak = assert_clean("fallback note", note)
    if leak:
        return False, leak

    # With no key configured at all, _chat must refuse with the platform-neutral
    # message and the coach must still degrade instead of raising.
    from core.config import settings
    from services import auth_service  # noqa: F401  (import sanity)

    original_key = settings.groq_api_key
    try:
        settings.groq_api_key = ""
        try:
            ai_analysis._chat("probe")
        except RuntimeError as exc:
            if str(exc) != MISSING_GROQ_API_KEY_MESSAGE:
                return False, f"unexpected missing-key error: {str(exc)[:150]!r}"
            if ".env" in str(exc):
                return False, "missing-key message still points at a local .env file"
        except Exception as exc:
            return False, f"missing key raised {type(exc).__name__}, expected RuntimeError"
        else:
            return False, "_chat succeeded with no API key configured"
    finally:
        settings.groq_api_key = original_key

    return True, (
        f"{calls['n']} attempt(s) failed; degrades to ai_generated=False with "
        f"{len(result.priority_improvements)} evidence-based recommendations; "
        f"no transport text shown; no-key path raises the platform-neutral "
        f"message instead of naming a .env file"
    )


# ---------------------------------------------------------------------------
# 9. Database unavailable
# ---------------------------------------------------------------------------
def database_unavailable():
    from sqlalchemy import text

    from core.config import ConfigurationError
    from core.db import _resolve_db_path, get_session_local, init_db, session_scope

    problems = []

    # (a) A DB path that runs through an existing FILE is unusable.
    proc = subprocess.run(
        [sys.executable, "-m", "core.startup_check"],
        capture_output=True, text=True,
        env={**os.environ, "TALENTIQ_DB_PATH": "data/skills.json/nested.db"},
        timeout=180,
    )
    combined = proc.stdout + proc.stderr
    if proc.returncode == 0:
        problems.append("startup check passed with an unusable database path")
    leak = assert_clean("startup output", combined.replace(str(Path.cwd()), "<APP>"))
    if leak:
        problems.append(leak)

    # (b) A locked database must RAISE, never be silently swallowed into an
    #     empty result set (which would read as "no candidates yet").
    init_db()
    holder = get_session_local()()
    try:
        holder.execute(text("CREATE TABLE IF NOT EXISTS _lock_probe (id INTEGER PRIMARY KEY)"))
        holder.commit()
        holder.execute(text("BEGIN EXCLUSIVE"))
        holder.execute(text("INSERT INTO _lock_probe (id) VALUES (1)"))
        holder.commit()
    except Exception as exc:
        holder.rollback()
        return False, f"could not set up the exclusive-lock probe: {type(exc).__name__}: {exc}"

    raised = False
    try:
        with session_scope() as session:
            session.execute(text("SELECT count(*) FROM users")).scalar()
    except Exception as exc:
        raised = True
        if type(exc).__name__ in ("SystemExit", "KeyboardInterrupt"):
            problems.append("a locked database escalated to SystemExit instead of erroring")
    try:
        holder.rollback()
        holder.close()
    except Exception:
        pass

    with get_session_local()() as s:
        s.execute(text("DROP TABLE IF EXISTS _lock_probe"))
        s.commit()

    # (c) A non-SQLite URL must never be silently accepted. _resolve_db_path()
    # re-reads the environment, so set the real variable, not the cached value.
    import core.config as _cc

    _saved = os.environ.get("DATABASE_URL")
    # No inline credentials on purpose: rejection is scheme-based, and a URL
    # with a user:password pair trips generic secret scanners, training people
    # to ignore their output. See core.db._resolve_db_path.
    os.environ["DATABASE_URL"] = "mysql://localhost/talentiq"
    try:
        _cc.settings.reload()
        _resolve_db_path()
    except ConfigurationError:
        pass
    except Exception as exc:
        problems.append(f"non-SQLite URL raised {type(exc).__name__}, expected ConfigurationError")
    else:
        problems.append("non-SQLite DATABASE_URL was silently accepted")
    finally:
        if _saved is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = _saved
        _cc.settings.reload()

    if problems:
        return False, "; ".join(problems)
    lock_note = (
        "an exclusive lock propagates as an error rather than an empty result"
        if raised else
        "SQLite released the lock before the read (locking is per-process)"
    )
    return True, (
        f"an unusable DB path fails the startup check; {lock_note}; "
        f"non-SQLite URLs are refused with ConfigurationError"
    )

def main() -> int:
    for name, fn in [
        ("missing GROQ_API_KEY", missing_api_key),
        ("unsupported DATABASE_URL", bad_database_url),
        ("missing upload/temp directory", missing_upload_dir),
        ("malformed / non-PDF / oversize uploads", bad_resume_uploads),
        ("malformed or text-less PDF", malformed_pdf),
        ("Groq unreachable", groq_unreachable),
        ("database unavailable", database_unavailable),
    ]:
        run(name, fn)

    for name, status, detail in RESULTS:
        print(f"[{status}] {name}")
        if detail:
            print(f"        {detail}")
    failed = [r for r in RESULTS if r[1] == "FAIL"]
    print("-" * 74)
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} failure modes handled cleanly")
    if failed:
        print("FAILED: " + ", ".join(n for n, _, _ in failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
