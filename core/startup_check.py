"""Deployment startup validation (Phase 5 · Step 1).

A single, side-effect-light check that answers one question before the UI is
served: *is this deployment able to work at all?* It exists because the two most
common production failures are both silent — a missing skill dataset quietly
degrades every score to zero, and an unconfigured ``GROQ_API_KEY`` only fails
when a user clicks Analyze.

Guarantees
----------
- **No network calls.** The Groq key is checked for *presence* only; the
  configured model id is checked for being a non-empty string. Nothing here ever
  contacts an API, so a health check can run in a sealed network.
- **No secrets in output.** Every finding names a variable or a path, never a
  value.
- **Read-only.** It creates the writable directories it verifies (so a container
  with a read-only root can fail loudly) but never writes, drops or migrates a
  database. Schema initialization stays with :func:`core.db.init_db`.

Run it directly::

    python -m core.startup_check           # human-readable report
    python -m core.startup_check --json    # machine-readable, exit code 1 on failure
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.config import PROJECT_ROOT, resolve_project_path, settings

# Statuses, ordered by severity.
OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass
class Check:
    """One validation result. ``hint`` must never contain a secret value."""

    name: str
    status: str
    detail: str = ""
    hint: str = ""

    @property
    def failed(self) -> bool:
        return self.status == FAIL


@dataclass
class StartupReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", hint: str = "") -> Check:
        check = Check(name=name, status=status, detail=detail, hint=hint)
        self.checks.append(check)
        return check

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == WARN]

    @property
    def ok(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checks": [asdict(c) for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------
def _check_environment(report: StartupReport) -> None:
    """The parsed configuration itself is valid and says what it is."""
    if settings.environment == "development":
        report.add(
            "environment",
            WARN,
            "ENVIRONMENT=development",
            "Set ENVIRONMENT=production in a real deployment.",
        )
    else:
        report.add("environment", OK, f"ENVIRONMENT={settings.environment}")

    if settings.debug and settings.environment == "production":
        report.add(
            "debug_flag",
            WARN,
            "DEBUG=true with ENVIRONMENT=production",
            "Turn DEBUG off in production so verbose detail is not logged.",
        )
    else:
        report.add("debug_flag", OK, f"DEBUG={str(settings.debug).lower()}")


def _check_groq(report: StartupReport) -> None:
    """Presence only — never calls the API and never prints the key."""
    if not (settings.groq_api_key or "").strip():
        report.add(
            "groq_api_key",
            FAIL,
            "GROQ_API_KEY is not set",
            "Set GROQ_API_KEY in the environment. Without it the AI summary, "
            "AI analysis, interview questions and AI resume coach are "
            "unavailable; deterministic ATS and JD scoring still work.",
        )
    else:
        report.add("groq_api_key", OK, "GROQ_API_KEY is set (value not shown)")

    if not (settings.groq_model or "").strip():
        report.add(
            "groq_model",
            FAIL,
            "GROQ_MODEL is empty",
            "Set GROQ_MODEL to a Groq model id, e.g. openai/gpt-oss-120b.",
        )
    else:
        report.add("groq_model", OK, f"GROQ_MODEL={settings.groq_model}")


def _check_database_url_scheme(report: StartupReport) -> None:
    """Refuse to let a non-SQLite DATABASE_URL pass unnoticed.

    PaaS providers auto-inject DATABASE_URL when a database is attached. The
    application cannot use PostgreSQL or MySQL, and silently ignoring the value
    would look like working persistence while losing all data on redeploy. This
    reports the scheme (never the URL, which embeds credentials) so the operator
    can act on it.
    """
    url = (settings.database_url or "").strip()
    if not url:
        return

    if url.startswith("sqlite:"):
        report.add("database_url", OK, "DATABASE_URL is a SQLite URL")
        return

    scheme = url.split("://", 1)[0].strip().lower() if "://" in url else "unknown"
    report.add(
        "database_url",
        FAIL,
        f"DATABASE_URL is a {scheme!r} URL, but only SQLite is supported",
        "TalentIQ has no PostgreSQL/MySQL support and will NOT fall back to a "
        "local file, because that would appear to persist data while losing "
        "it on every deploy. Unset DATABASE_URL, or point it at a SQLite file "
        "URL (e.g. 'sqlite:///data/talentiq.db') on persistent storage.",
    )


def _check_database_path(report: StartupReport) -> None:
    """The database location is writable.

    Deliberately does NOT open or initialize the database: this runs before the
    app, and creating schema is ``core.db.init_db``'s job.
    """
    from core.db import get_db_path

    try:
        path = get_db_path()
    except Exception as exc:
        report.add(
            "database_path",
            FAIL,
            f"Could not resolve the database path ({type(exc).__name__})",
            "Check DATABASE_URL or TALENTIQ_DB_PATH.",
        )
        return

    parent = path.parent
    if not parent.exists():
        report.add(
            "database_path",
            FAIL,
            f"Database directory does not exist: {parent}",
            "Create it, or point TALENTIQ_DB_PATH somewhere that exists.",
        )
        return
    # ``exists()`` is also true for a regular file, and ``os.access(W_OK)``
    # happily reports a writable file as writable - so a misconfigured path
    # whose "directory" is actually a file would pass both tests and only
    # fail later, at open time, as an opaque SQLite error. Require a real
    # directory here so the operator sees the misconfiguration at deploy.
    if not parent.is_dir():
        report.add(
            "database_path",
            FAIL,
            f"Database path's parent is not a directory: {parent}",
            "TALENTIQ_DB_PATH/DATABASE_URL names a path inside an existing "
            "file. Point it at a directory instead.",
        )
        return
    if not os.access(parent, os.W_OK):
        report.add(
            "database_path",
            FAIL,
            f"Database directory is not writable: {parent}",
            "Grant write permission to the user running the app.",
        )
        return
    report.add("database_path", OK, f"SQLite file will live at {path}")


def _check_directories(report: StartupReport) -> None:
    """Writable scratch directories, created on demand."""
    for label, configured in (
        ("temp_directory", settings.temp_directory),
        ("upload_directory", settings.upload_directory),
    ):
        try:
            path = resolve_project_path(configured)
            path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            report.add(
                label,
                FAIL,
                f"Could not create {configured} ({type(exc).__name__})",
                "Point it at a writable location, or mount a writable volume.",
            )
            continue
        if not os.access(path, os.W_OK):
            report.add(
                label,
                FAIL,
                f"{configured} is not writable",
                "Grant write permission, or mount a writable volume there.",
            )
            continue
        report.add(label, OK, f"{configured} -> {path} (writable)")


def _check_skill_dataset(report: StartupReport) -> None:
    """The canonical skill dataset must be present and non-empty.

    A missing dataset does not crash the UI — it silently zeroes every skill
    signal, so it is worth failing a startup check loudly.
    """
    path = PROJECT_ROOT / "data" / "skills.json"
    if not path.exists():
        report.add(
            "skill_dataset",
            FAIL,
            f"Missing {path}",
            "This file ships with the repository. Restore it with "
            "`git checkout -- data/skills.json`.",
        )
        return
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        report.add(
            "skill_dataset",
            FAIL,
            f"{path} is not valid JSON ({type(exc).__name__})",
            "Restore the file with `git checkout -- data/skills.json`.",
        )
        return
    if not isinstance(dataset, dict) or not dataset:
        report.add(
            "skill_dataset",
            FAIL,
            f"{path} is empty or malformed",
            "Restore the file with `git checkout -- data/skills.json`.",
        )
        return
    report.add("skill_dataset", OK, f"{len(dataset)} canonical skills available")


def _check_semantic_model(report: StartupReport) -> None:
    """Is the local embedding model present?

    The model is loaded with ``local_files_only=True``, so it is never
    downloaded at runtime. When it is absent the matcher falls back to the
    deterministic skill + TF-IDF score — the app still works, but the semantic
    signal is silently off, which changes scores. That deserves a warning, not
    a failure. Probing uses the same helper the matcher uses, so it loads the
    model into the process cache and does not make a network call.
    """
    try:
        from services.semantic_matcher import SEMANTIC_MODEL_NAME, semantic_available

        available = semantic_available()
    except Exception as exc:
        report.add(
            "semantic_model",
            WARN,
            f"Could not probe the embedding model ({type(exc).__name__})",
            "JD matching will use the skill + TF-IDF fallback.",
        )
        return

    if available:
        report.add("semantic_model", OK, f"{SEMANTIC_MODEL_NAME} loaded locally")
        return
    report.add(
        "semantic_model",
        WARN,
        f"{SEMANTIC_MODEL_NAME} is not in the local cache",
        "semantic_available() is False, so JD matching silently uses the "
        "skill + TF-IDF fallback and scores differ from a fully provisioned "
        "host. Pre-download the model in your image build, e.g. "
        "`python -c \"from sentence_transformers import SentenceTransformer; "
        "SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')\"`.",
    )


def _check_admin_bootstrap(report: StartupReport) -> None:
    """Is a first ADMIN obtainable?

    Nothing is auto-provisioned: the ADMIN role is granted by *registering* with
    the configured credentials. If they are unset, every account that can be
    created is a RECRUITER and there is no path to ADMIN.
    """
    if settings.admin_email and settings.admin_password:
        report.add(
            "admin_bootstrap",
            OK,
            "Admin bootstrap credentials are configured; register that email "
            "on the login page to create the first ADMIN",
        )
        return
    report.add(
        "admin_bootstrap",
        WARN,
        "TALENTIQ_ADMIN_EMAIL / TALENTIQ_ADMIN_PASSWORD are not both set",
        "Without them no ADMIN account can ever be created, and the admin audit "
        "log page stays inaccessible. Set both to bootstrap the first admin.",
    )


def _check_streamlit_upload_limit(report: StartupReport) -> None:
    """Streamlit's transport cap must not sit below the app's own limit.

    ``maxUploadSize`` in ``.streamlit/config.toml`` bounds how much Streamlit
    buffers before Python sees the request, while ``MAX_RESUME_SIZE_MB`` is what
    the application enforces. If the former is smaller, users get an opaque
    Streamlit error for files the application would have accepted.
    """
    config_path = PROJECT_ROOT / ".streamlit" / "config.toml"
    if not config_path.exists():
        report.add(
            "streamlit_config",
            WARN,
            f"Missing {config_path}",
            "Streamlit will use its built-in defaults; upload limits, theme "
            "and CORS/XSRF settings will not be applied.",
        )
        return

    try:
        import tomllib

        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        report.add(
            "streamlit_config",
            FAIL,
            f"Could not parse {config_path} ({type(exc).__name__})",
            "Fix the TOML syntax, or restore the file from git.",
        )
        return

    server = config.get("server", {})
    limit = server.get("maxUploadSize")
    if limit is None:
        report.add(
            "streamlit_upload_limit",
            WARN,
            "maxUploadSize is not set; Streamlit defaults to 200 MB",
            f"The application accepts {settings.max_resume_size_mb} MB. Set "
            "maxUploadSize to bound memory use.",
        )
        return

    if int(limit) < settings.max_resume_size_mb:
        report.add(
            "streamlit_upload_limit",
            FAIL,
            f"maxUploadSize={limit} MB is below MAX_RESUME_SIZE_MB="
            f"{settings.max_resume_size_mb} MB",
            "Streamlit would reject files the application accepts. Raise "
            "maxUploadSize in .streamlit/config.toml, or lower "
            "MAX_RESUME_SIZE_MB.",
        )
    else:
        report.add(
            "streamlit_upload_limit",
            OK,
            f"maxUploadSize={limit} MB >= MAX_RESUME_SIZE_MB="
            f"{settings.max_resume_size_mb} MB",
        )
    # NOTE: deliberately no early return above. A previous version returned as
    # soon as the upload limit failed, which meant the XSRF/CORS checks below
    # were silently skipped whenever any earlier check failed - i.e. the most
    # security-critical checks were the easiest to miss.
    if server.get("runOnSave"):
        report.add(
            "streamlit_run_on_save",
            WARN,
            "runOnSave is enabled",
            "File watching costs CPU in production. Set runOnSave = false, or "
            "override with STREAMLIT_SERVER_RUN_ON_SAVE=false.",
        )
    for insecure, label in (
        ("enableXsrfProtection", "XSRF protection"),
        ("enableCORS", "CORS"),
    ):
        if server.get(insecure) is False:
            report.add(
                f"streamlit_{insecure}",
                FAIL,
                f"{label} is disabled in .streamlit/config.toml",
                f"Re-enable {insecure}. Do not disable it to work around "
                "proxy errors.",
            )


def _check_data_files(report: StartupReport) -> None:
    """Every other data file the application expects to ship."""
    for name in ("role_skills.json", "skills.csv"):
        path = PROJECT_ROOT / "data" / name
        status = OK if path.exists() else WARN
        report.add(
            f"data_{name.replace('.', '_')}",
            status,
            "present" if path.exists() else f"missing {path}",
            "" if path.exists() else "Not read by the current code; safe to ignore.",
        )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def run_startup_checks() -> StartupReport:
    """Run every check and return the collected report. Never raises."""
    report = StartupReport()
    for check in (
        _check_environment,
        _check_groq,
        _check_database_url_scheme,
        _check_database_path,
        _check_directories,
        _check_skill_dataset,
        _check_semantic_model,
        _check_admin_bootstrap,
        _check_streamlit_upload_limit,
        _check_data_files,
    ):
        try:
            check(report)
        except Exception as exc:  # pragma: no cover - defensive
            report.add(
                getattr(check, "__name__", "check").lstrip("_"),
                FAIL,
                f"check raised {type(exc).__name__}: {exc}",
                "This is a bug in the startup check itself.",
            )
    return report


_ICON = {OK: "PASS", WARN: "WARN", FAIL: "FAIL"}


def format_report(report: StartupReport) -> str:
    """Human-readable report. Contains no secret values by construction."""
    width = max((len(c.name) for c in report.checks), default=10)
    lines = ["TalentIQ startup check", "=" * (22 + width)]
    for check in report.checks:
        lines.append(f"[{_ICON[check.status]}] {check.name.ljust(width)}  {check.detail}")
        if check.hint and check.status != OK:
            lines.append(f"{' ' * (7 + width)}  -> {check.hint}")
    failures = len(report.failures)
    warnings = len(report.warnings)
    lines.append("=" * (22 + width))
    lines.append(
        f"{len(report.checks)} checks: {failures} failed, {warnings} warning(s)"
    )
    if report.ok:
        lines.append("Result: ready to serve.")
    else:
        lines.append("Result: NOT ready — fix the failures above before serving.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    report = run_startup_checks()
    if "--json" in argv:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(format_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
