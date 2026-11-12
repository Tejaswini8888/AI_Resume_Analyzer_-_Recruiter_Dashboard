"""Invariants that make the test suite safe to run in CI and on a laptop.

These are not product tests. They assert properties *about the test suite and
the repository* that CI depends on, which means nothing else would catch them
regressing:

- tests never touch a developer's real ``data/talentiq.db``;
- tests never leave build artifacts in the checkout (CI fails on a dirty tree);
- the ignore rules that keep secrets and local state out of git stay in place.

Each of these was a real defect at some point: the harness-compile check used
to write ``tools/*.check`` into the source tree on every run, and a
non-temporary database path would have let a test run rewrite real candidate
data. A regression here is silent, so it is worth pinning.
"""

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_DATA_DIR = PROJECT_ROOT / "data"
REAL_DB = REPO_DATA_DIR / "talentiq.db"


# ---------------------------------------------------------------------------
# Database isolation (requirement: CI must not touch a local/production DB)
# ---------------------------------------------------------------------------
def test_suite_runs_against_a_temporary_database_not_the_repository_one():
    """The active DB path must be a temp file, never ``data/talentiq.db``."""
    import os
    from pathlib import Path as _Path

    configured = os.environ.get("TALENTIQ_DB_PATH", "")
    assert configured, (
        "TALENTIQ_DB_PATH is unset. tests/conftest.py sets a temp fallback at "
        "import time; if this fails, that guard was removed and a test could "
        "write to the real database."
    )
    resolved = _Path(configured).resolve()
    assert resolved != REAL_DB.resolve(), (
        "TALENTIQ_DB_PATH points at the repository's real database. Tests would "
        "overwrite real candidate and user data."
    )
    assert not str(resolved).startswith(str(REPO_DATA_DIR)), (
        f"TALENTIQ_DB_PATH ({resolved}) is inside the repository's data/ "
        "directory; a test run would create a committed-looking artifact."
    )


def test_conftest_declares_a_temp_fallback_before_any_fixture_runs():
    """The module-level guard must exist, not just the per-test fixture.

    A test module that imports the database layer at collection time, before any
    fixture has run, would otherwise resolve the default repository path.
    """
    source = (PROJECT_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "tempfile.mkdtemp" in source, (
        "conftest must create a temp directory at import time as a safety net "
        "for modules imported before any fixture runs"
    )
    assert "TALENTIQ_DB_PATH" in source


def test_real_database_is_git_ignored():
    """A developer's database must never be committable by accident."""
    result = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "check-ignore", "-q", "data/talentiq.db"],
        capture_output=True,
    )
    if result.returncode == 128:
        pytest.skip("not a git checkout")
    assert result.returncode == 0, (
        "data/talentiq.db is not git-ignored; a real candidate database could be "
        "committed"
    )


# ---------------------------------------------------------------------------
# The suite must not litter the checkout (requirement: no permanent files)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "relative",
    [
        "tools/verify_deployment.py.check",
        "tools/verify_failure_modes.py.check",
    ],
)
def test_no_stray_bytecode_artifact_from_a_previous_run(relative):
    """Guards the fix for a test that used to write next to the script.

    ``test_verification_harnesses_compile`` once targeted
    ``<script>.check`` in ``tools/``, dropping an unreviewed 45 KB file into the
    source tree on every run. It now compiles into ``tmp_path``; this asserts
    the old artifact does not reappear.
    """
    stray = PROJECT_ROOT / relative
    assert not stray.exists(), (
        f"{relative} exists. A test is writing build output into the source "
        "tree again; compile into tmp_path instead."
    )


def test_stray_artifact_pattern_is_git_ignored():
    result = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "check-ignore", "-q", "tools/x.check"],
        capture_output=True,
    )
    if result.returncode == 128:
        pytest.skip("not a git checkout")
    assert result.returncode == 0, (
        "*.check is not git-ignored, so a stray compile artifact from any tool "
        "could be committed"
    )


# ---------------------------------------------------------------------------
# Secret hygiene rules that CI enforces (requirement 9)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "pattern",
    [
        ".env",
        "venv/x.py",
        "data/talentiq.db",
        "data/tmp/scratch",
        ".streamlit/secrets.toml",
    ],
)
def test_local_state_paths_stay_ignored(pattern):
    result = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "check-ignore", "-q", pattern],
        capture_output=True,
    )
    if result.returncode == 128:
        pytest.skip("not a git checkout")
    assert result.returncode == 0, f"{pattern} is not git-ignored"


def test_env_example_is_tracked_and_real_env_is_not():
    """The template must ship; the real file must not."""
    tracked = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0:
        pytest.skip("not a git checkout")
    files = {p for p in tracked.stdout.split("\0") if p}
    assert ".env.example" in files, (
        ".env.example must be tracked so a fresh clone documents the variables"
    )
    assert ".env" not in files, "the real .env is tracked"


def test_required_runtime_data_is_not_ignored():
    """data/skills.json is required at runtime; an ignore rule would break CI."""
    result = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "check-ignore", "-q", "data/skills.json"],
        capture_output=True,
    )
    if result.returncode == 128:
        pytest.skip("not a git checkout")
    assert result.returncode != 0, (
        "data/skills.json is git-ignored, but the application requires it at "
        "runtime; a fresh clone would fail to start"
    )


# ---------------------------------------------------------------------------
# The CI helpers themselves must stay runnable as plain scripts
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "script", ["tools/ci_import_check.py", "tools/ci_secret_scan.py"]
)
def test_ci_helper_scripts_are_runnable_and_guarded(script):
    """Each helper must have the import bootstrap and the exit-code guard.

    The import bootstrap is easy to lose: pytest.ini sets ``pythonpath = .``, so
    the suite passes even if a script forgets it, and only CI - which runs the
    script directly - discovers the breakage.
    """
    source = (PROJECT_ROOT / script).read_text(encoding="utf-8")
    assert "sys.path.insert" in source, (
        f"{script} must put the project root on sys.path itself; it is executed "
        "directly in CI, where pytest.ini's pythonpath does not apply"
    )
    assert "raise SystemExit(main())" in source, (
        f"{script} must propagate a non-zero exit code so CI can fail on it"
    )


def test_python_version_is_supported_by_the_workflow():
    """The workflow pins 3.11; fail loudly if the pins ever move without it."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "python-version:" in workflow
    pinned = workflow.split("python-version:", 1)[1]
    pinned = pinned.split('"', 2)[1] if '"' in pinned else pinned
    assert pinned.split(".")[0] == f"{sys.version_info.major}", (
        f"CI pins Python {pinned} but the suite is running on "
        f"{sys.version_info.major}.{sys.version_info.minor}"
    )
