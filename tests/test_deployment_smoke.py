"""Deployment smoke tests (Phase 5 · Step 1).

These exist because the previous verification was weaker than it looked. A
``GET /`` returning HTTP 200 only proves the Streamlit *server* started — the
app script itself is not executed until a websocket session connects, so a
crash on the first line of ``app.py`` still returns 200. ``AppTest`` runs the
script for real, in-process, so an exception in any executed line fails here.

Covered:
- ``app.py`` and every page actually execute with no unhandled exception.
- The startup-validation and database wiring run without raising.
- Required runtime data resolves from the repository root regardless of cwd.
- A misconfigured deployment fails closed rather than serving silently.

No browser and no network: these are server-side execution checks, not
end-to-end user journeys. See docs/PRODUCTION_LIMITATIONS.md.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Every Streamlit page in the app. A page that cannot even be executed is a
# deployment blocker, so all of them are run.
PAGE_FILES = sorted((PROJECT_ROOT / "pages").glob("*.py"))


def _app_test(path: Path, timeout: int = 120):
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(path), default_timeout=timeout)


def _assert_no_exception(at, label: str) -> None:
    assert not at.exception, (
        f"{label} raised {len(at.exception)} unhandled exception(s): "
        + "; ".join(str(e.value) for e in at.exception)
    )


# ---------------------------------------------------------------------------
# app.py
# ---------------------------------------------------------------------------
def test_app_entrypoint_executes_cleanly():
    """app.py must run top to bottom without an unhandled exception.

    This covers the module-level startup path that a plain HTTP 200 cannot see:
    logging setup, the startup validation pass, and ``init_db()``.
    """
    at = _app_test(PROJECT_ROOT / "app.py").run()
    _assert_no_exception(at, "app.py")


def test_app_entrypoint_renders_content():
    """A script that renders nothing has silently failed, not passed."""
    at = _app_test(PROJECT_ROOT / "app.py").run()
    assert len(at.markdown) + len(at.title) + len(at.header) > 0, (
        "app.py produced no rendered output"
    )


def test_startup_check_module_runs_as_a_script():
    """The documented CLI entrypoint must work in a subprocess.

    Run as a subprocess on purpose: the in-process tests share module state with
    the pytest session, so they cannot prove the ``python -m`` path works.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "core.startup_check", "--json"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    # The test suite runs without a Groq key, so a non-zero exit is expected and
    # correct: that is the fail-closed behaviour. The report must still be valid.
    assert proc.returncode in (0, 1), f"unexpected exit {proc.returncode}: {proc.stderr}"

    import json

    report = json.loads(proc.stdout)
    assert set(report) == {"ok", "checks"}
    assert isinstance(report["checks"], list) and report["checks"]
    for check in report["checks"]:
        assert set(check) == {"name", "status", "detail", "hint"}
        assert check["status"] in {"ok", "warn", "fail"}


def test_startup_check_never_prints_secrets(monkeypatch, capsys):
    """No check may echo an API key, password or cookie secret.

    Values are distinctive so an accidental leak is detectable.
    """
    from core import startup_check
    from core.config import settings

    sentinel_key = "SENTINEL-GROQ-KEY-must-never-appear-0123456789"
    monkeypatch.setattr(settings, "groq_api_key", sentinel_key)
    monkeypatch.setattr(settings, "admin_password", "SENTINEL-ADMIN-PW-abcdef")
    monkeypatch.setattr(settings, "admin_email", "admin@example.com")

    report = startup_check.run_startup_checks()
    rendered = startup_check.format_report(report)
    assert sentinel_key not in rendered
    assert "SENTINEL-ADMIN-PW" not in rendered
    for check in report.checks:
        assert sentinel_key not in check.detail
        assert sentinel_key not in check.hint


def test_startup_check_flags_missing_skill_dataset(monkeypatch, tmp_path):
    """A missing dataset is a failure, not a warning.

    It does not crash the UI, so without this check it would silently zero every
    skill signal instead.
    """
    from core import startup_check

    fake_root = tmp_path / "empty_root"
    (fake_root / "data").mkdir(parents=True)
    (fake_root / ".streamlit").mkdir(parents=True)
    monkeypatch.setattr(startup_check, "PROJECT_ROOT", fake_root)

    report = startup_check.run_startup_checks()
    names = {c.name: c for c in report.checks}
    assert "skill_dataset" in names
    assert names["skill_dataset"].status == startup_check.FAIL
    assert not report.ok


def test_startup_check_flags_db_path_whose_parent_is_a_file(monkeypatch, tmp_path):
    """A database path inside an existing *file* must fail the startup check.

    ``Path.exists()`` is true for a regular file and ``os.access(W_OK)`` happily
    reports a writable file as writable, so checking only those two made this
    misconfiguration pass as healthy and then fail at open time as an opaque
    SQLite error. The check has to require a real directory.
    """
    from core import startup_check

    not_a_dir = tmp_path / "data" / "skills.json"
    not_a_dir.parent.mkdir(parents=True, exist_ok=True)
    not_a_dir.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("TALENTIQ_DB_PATH", str(not_a_dir / "nested.db"))
    monkeypatch.setattr(startup_check, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("core.db._db_path", None, raising=False)

    report = startup_check.run_startup_checks()
    names = {c.name: c for c in report.checks}
    assert "database_path" in names, "the database_path check did not run"
    assert names["database_path"].status == startup_check.FAIL, (
        "a database path whose parent is a regular file was reported as healthy: "
        f"{names['database_path'].detail}"
    )
    assert not report.ok
    # The message must tell the operator what is wrong, not just fail.
    assert "not a directory" in names["database_path"].detail


def test_startup_check_accepts_healthy_db_directory(monkeypatch, tmp_path):
    """The stricter check must not reject a normal writable data directory."""
    from core import startup_check

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("TALENTIQ_DB_PATH", str(tmp_path / "data" / "app.db"))
    monkeypatch.setattr(startup_check, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("core.db._db_path", None, raising=False)

    report = startup_check.run_startup_checks()
    names = {c.name: c for c in report.checks}
    assert "database_path" in names
    assert names["database_path"].status == startup_check.OK, (
        f"a healthy data directory was rejected: {names['database_path'].detail}"
    )


def test_startup_check_flags_insecure_streamlit_config(monkeypatch, tmp_path):
    """Disabling XSRF protection or CORS must fail the startup check."""
    from core import startup_check

    fake_root = tmp_path / "insecure_root"
    (fake_root / "data").mkdir(parents=True)
    cfg = fake_root / ".streamlit"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text(
        "[server]\n"
        "enableXsrfProtection = false\n"
        "enableCORS = false\n"
        "runOnSave = true\n"
        "maxUploadSize = 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(startup_check, "PROJECT_ROOT", fake_root)
    monkeypatch.setattr(startup_check.settings, "max_resume_size_mb", 10)

    report = startup_check.run_startup_checks()
    names = {c.name: c for c in report.checks}
    assert names["streamlit_enableXsrfProtection"].status == startup_check.FAIL
    assert names["streamlit_enableCORS"].status == startup_check.FAIL
    assert names["streamlit_upload_limit"].status == startup_check.FAIL
    assert names["streamlit_run_on_save"].status == startup_check.WARN


def test_shipped_streamlit_config_is_production_sane():
    """The committed .streamlit/config.toml must not ship insecure settings."""
    import tomllib

    config = tomllib.loads(
        (PROJECT_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    )
    server = config.get("server", {})
    assert server.get("enableXsrfProtection", True) is True
    assert server.get("enableCORS", True) is True
    assert server.get("runOnSave") is False
    assert server.get("headless") is True
    assert int(server.get("maxUploadSize", 0)) > 0
    assert config.get("browser", {}).get("gatherUsageStats") is False
    # The product theme is a deliverable and must not be dropped.
    assert config.get("theme", {}).get("base") == "dark"


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "page", PAGE_FILES, ids=lambda p: p.name
)
def test_page_executes_cleanly(page):
    """Every page must execute with no unhandled exception.

    Unauthenticated pages must redirect or render a gate rather than raise.
    """
    at = _app_test(page).run()
    _assert_no_exception(at, page.name)


# ---------------------------------------------------------------------------
# Path resolution / required data
# ---------------------------------------------------------------------------
def test_skill_dataset_is_present_and_valid():
    """data/skills.json is required runtime data and must ship with the app."""
    import json

    path = PROJECT_ROOT / "data" / "skills.json"
    assert path.exists(), (
        "data/skills.json is missing. It is required for skill "
        "canonicalization, ATS scoring and JD matching. Restore it with "
        "`git checkout -- data/skills.json`."
    )
    dataset = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(dataset, dict) and dataset
    assert all(isinstance(v, dict) for v in dataset.values())


def test_skill_loader_raises_actionable_error_when_dataset_missing(monkeypatch):
    """A missing dataset must name the path, not raise a bare FileNotFoundError.

    A bare error is deeply confusing because the first symptom is an empty skill
    list, which silently degrades scoring instead of crashing.
    """
    from modules import skills

    monkeypatch.setattr(
        skills, "_SKILLS_PATH", str(PROJECT_ROOT / "data" / "does_not_exist.json")
    )
    with pytest.raises(FileNotFoundError) as excinfo:
        skills._load_dataset_file()
    message = str(excinfo.value)
    assert "does_not_exist.json" in message
    assert "git checkout" in message


def test_relative_paths_resolve_against_project_root_not_cwd(tmp_path, monkeypatch):
    """Relative configured paths must not follow the process working directory.

    A container, a systemd unit and an IDE can all launch the app from different
    directories; all three must land on the same files.
    """
    from core.config import resolve_project_path

    monkeypatch.chdir(tmp_path)
    resolved = resolve_project_path("data/tmp")
    assert resolved == PROJECT_ROOT / "data" / "tmp"
    assert str(resolved).startswith(str(PROJECT_ROOT))


def test_absolute_paths_are_left_alone():
    """An operator-supplied absolute path must win over the project root."""
    from core.config import resolve_project_path

    absolute = Path("C:/somewhere/else") if sys.platform == "win32" else Path("/tmp/x")
    assert resolve_project_path(str(absolute)) == absolute


def test_temp_file_is_created_under_project_root(tmp_path, monkeypatch):
    """create_secure_temp_file must anchor to the project root, not the cwd."""
    from core.config import settings
    from services import file_security_service as fss

    monkeypatch.setattr(settings, "temp_directory", "data/tmp")
    monkeypatch.chdir(tmp_path)
    path = fss.create_secure_temp_file()
    try:
        resolved = Path(path).resolve()
        assert str(resolved).startswith(str(PROJECT_ROOT / "data" / "tmp"))
        assert resolved.exists()
    finally:
        fss.cleanup_temp_file(path)
    assert not Path(path).exists()


# ---------------------------------------------------------------------------
# Repository hygiene
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "required",
    [
        "app.py",
        "requirements.txt",
        "requirements-dev.txt",
        ".env.example",
        ".streamlit/config.toml",
        "data/skills.json",
        "core/startup_check.py",
        "core/db.py",
        "core/config.py",
        "core/models.py",
        "services/auth_service.py",
        "ui/theme.py",
    ],
)
def test_required_deployment_file_exists(required):
    """A missing file here is a clean-clone failure."""
    assert (PROJECT_ROOT / required).exists(), f"missing {required}"


def test_env_example_contains_no_real_secrets():
    """.env.example must carry placeholders, never a usable credential."""
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "GROQ_API_KEY=" in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if "KEY" in key.upper() or "PASSWORD" in key.upper() or "SECRET" in key.upper():
            assert value.strip() == "" or any(
                marker in value
                for marker in ("your", "changeme", "placeholder", "example", "gsk_", "<")
            ), f"{key} in .env.example looks like a real value"


def test_gitignore_does_not_exclude_required_deployment_files():
    """Nothing the app needs at runtime may be gitignored.

    A silent ignore is how a clean clone ends up missing the skill dataset or
    the theme config.
    """
    import subprocess

    required = [
        "data/skills.json",
        ".streamlit/config.toml",
        ".env.example",
        "requirements.txt",
        "app.py",
    ]
    for path in required:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", "--", path],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0, f"{path} is gitignored but must ship"


def test_dockerignore_does_not_exclude_required_data():
    """The image must contain the skill dataset and the Streamlit config."""
    # Comments explain what must NOT be excluded, so only real rules are read.
    rules = [
        line.strip()
        for line in (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any(rule in ("data/*.json", "data/*", "data") for rule in rules), (
        "a data/*.json or data/ rule would strip the required skill dataset "
        "from the image"
    )
    assert ".env" in rules
    assert any(rule.startswith("data/*.db") for rule in rules)
    # Sanity: the rules list is real, not accidentally empty.
    assert len(rules) > 10


# ---------------------------------------------------------------------------
# Deployment verification harnesses (tools/)
# ---------------------------------------------------------------------------
VERIFY_SCRIPTS = ("verify_deployment.py", "verify_failure_modes.py")


def test_verification_harnesses_are_not_collected_by_pytest():
    """The harnesses make real API calls and boot a server - never auto-collect.

    ``pytest.ini`` sets ``testpaths = tests``, so a file in ``tools/`` is not
    collected even if it were named ``test_*``. This test pins both halves of
    that guarantee: the scripts must live outside ``tests/`` AND must not use a
    ``test_`` prefix, so moving one cannot silently start running (and billing)
    inside the normal test suite.
    """
    for name in VERIFY_SCRIPTS:
        path = PROJECT_ROOT / "tools" / name
        assert path.exists(), f"missing verification harness: tools/{name}"
        assert not name.startswith("test_"), (
            f"tools/{name} must not be named test_*; it is an operational "
            f"harness, not a unit test"
        )
        assert "tests" not in path.relative_to(PROJECT_ROOT).parts, (
            f"{name} must not live under tests/ - it would be collected and "
            f"would make live Groq calls during pytest"
        )


def test_verification_harnesses_compile(tmp_path):
    """They are run as scripts, so a syntax error would only surface at deploy.

    The bytecode target is a ``tmp_path`` file, not ``<script>.check`` in the
    source tree: writing next to the script left untracked build artifacts in
    ``tools/`` after every test run, which is both untidy and a supply-chain
    smell (a stray ``.check`` file is a file nobody reviews).
    """
    import py_compile

    for name in VERIFY_SCRIPTS:
        path = PROJECT_ROOT / "tools" / name
        py_compile.compile(
            str(path),
            doraise=True,
            cfile=str(tmp_path / f"{path.stem}.pyc"),
        )


def test_verification_harnesses_contain_no_hardcoded_secret():
    """Credentials must come from the environment, never from the file."""
    import re

    for name in VERIFY_SCRIPTS:
        source = (PROJECT_ROOT / "tools" / name).read_text(encoding="utf-8")
        # A real Groq key, or anything that looks like one, must not be here.
        assert not re.search(r"gsk_[A-Za-z0-9]{20,}", source), (
            f"tools/{name} appears to contain a hardcoded Groq API key"
        )
        assert not re.search(r"(?i)password\s*=\s*[\"'][^\"']{8,}[\"']", source), (
            f"tools/{name} appears to contain a hardcoded password"
        )
        # Credentials belong in the environment.
        assert "GROQ_API_KEY" in source, (
            f"tools/{name} should read the API key from the environment"
        )


def test_verification_harnesses_drive_the_real_production_call_path():
    """Guard against the harnesses silently drifting from the app.

    An earlier draft called ``analyze_ats(profile=None, resume_text=...)`` and
    ``generate_pdf_report(candidate=<ORM object>)`` - both of which are not how
    the pages actually call them, so the harness was proving less than it
    appeared to. These assertions pin the real call shapes.
    """
    deployment = (PROJECT_ROOT / "tools" / "verify_deployment.py").read_text(
        encoding="utf-8"
    )
    # pages/2 and pages/5 build a structured profile and score that object.
    assert "build_resume_profile(" in deployment
    assert "analyze_ats(profile)" in deployment
    assert "to_details_dict()" in deployment
    # The coach consumes the real dataclass, and the real field name.
    assert "generate_resume_coach(" in deployment
    assert "priority_improvements" in deployment
    # And it must assert on a real, network-backed AI call at least once.
    assert "api.groq.com" not in deployment, (
        "the harness should reach Groq through the app, not by hand"
    )
