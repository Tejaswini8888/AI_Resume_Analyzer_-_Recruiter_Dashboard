"""Phase 4 · Step 5 — Configuration & environment management tests.

No real API calls, no network, no database writes beyond the isolated temp
SQLite fixture. Verifies:

- settings load with safe defaults,
- missing GROQ_API_KEY yields a clear, value-free error/flag,
- environment variables override defaults (including validation failures),
- GROQ model is centralized and shared by the AI layer,
- database URL/path resolution flows through the centralized settings,
- file limits/extensions/directories are single-sourced,
- secrets never appear in logs or the safe operational summary.
"""

import logging
import re

import pytest

import core.config
import core.db
import services.file_security_service as fss
from core.config import (
    MISSING_GROQ_API_KEY_MESSAGE,
    ConfigurationError,
    Settings,
    settings,
)
from core.logging_config import log_startup_config, redact_secrets

_CONFIG_VARS = (
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "DATABASE_URL",
    "TALENTIQ_DB_PATH",
    "MAX_RESUME_SIZE_MB",
    "ALLOWED_RESUME_EXTENSIONS",
    "UPLOAD_DIRECTORY",
    "TEMP_DIRECTORY",
    "ENVIRONMENT",
    "DEBUG",
    "LOG_LEVEL",
    "RECRUITER_SCORE_THRESHOLD",
    "TALENTIQ_ADMIN_EMAIL",
    "TALENTIQ_ADMIN_PASSWORD",
)


def _clear_env(monkeypatch):
    for name in _CONFIG_VARS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Loading & defaults
# ---------------------------------------------------------------------------
def test_settings_load_with_safe_defaults(monkeypatch):
    _clear_env(monkeypatch)
    s = Settings.load()
    assert s.environment == "development"
    assert s.debug is False
    assert s.log_level == "INFO"
    assert s.groq_api_key is None
    assert s.groq_model == "openai/gpt-oss-120b"
    assert s.database_file is None
    assert s.max_resume_size_mb == 10
    assert s.allowed_resume_extensions == (".pdf",)
    assert s.upload_directory == "data/uploads"
    assert s.temp_directory == "data/tmp"
    assert s.recruiter_score_threshold == 60.0


def test_defaults_are_in_module_level_singleton():
    assert core.config.settings is settings
    assert core.config.GROQ_MODEL == settings.groq_model
    assert core.config.MAX_RESUME_SIZE_MB == settings.max_resume_size_mb
    assert core.config.ENVIRONMENT == settings.environment
    assert core.config.DEBUG == settings.debug


# ---------------------------------------------------------------------------
# Missing critical settings -> clear, value-free error
# ---------------------------------------------------------------------------
def test_missing_groq_key_marked_and_message_is_value_free(monkeypatch):
    _clear_env(monkeypatch)
    s = Settings.load()
    assert s.missing_critical_settings() == ["GROQ_API_KEY"]
    assert "GROQ_API_KEY is not configured" in MISSING_GROQ_API_KEY_MESSAGE
    assert "sk-" not in MISSING_GROQ_API_KEY_MESSAGE


def test_configured_groq_key_not_marked_missing(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-example")
    s = Settings.load()
    assert s.missing_critical_settings() == []
    assert s.groq_api_key == "gsk-example"


# ---------------------------------------------------------------------------
# Environment overrides
# ---------------------------------------------------------------------------
def test_environment_variables_override_defaults(monkeypatch):
    monkeypatch.setenv("MAX_RESUME_SIZE_MB", "25")
    monkeypatch.setenv("RECRUITER_SCORE_THRESHOLD", "70.5")
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("ALLOWED_RESUME_EXTENSIONS", ".pdf,.PDF,.docx")
    monkeypatch.setenv("TEMP_DIRECTORY", "/var/tmp/talentiq")
    monkeypatch.setenv("GROQ_MODEL", "groq/llama-scratch")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-override")
    s = Settings.load()
    assert s.max_resume_size_mb == 25
    assert s.recruiter_score_threshold == 70.5
    assert s.debug is True
    assert s.environment == "production"
    assert s.log_level == "WARNING"
    assert ".docx" in s.allowed_resume_extensions
    assert s.temp_directory == "/var/tmp/talentiq"
    assert s.groq_model == "groq/llama-scratch"
    assert s.groq_api_key == "gsk-override"


def test_invalid_values_raise_clear_configuration_errors(monkeypatch):
    monkeypatch.setenv("DEBUG", "banana")
    with pytest.raises(ConfigurationError) as exc:
        Settings.load()
    assert "Invalid boolean for DEBUG" in str(exc.value)

    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("MAX_RESUME_SIZE_MB", "not-a-number")
    with pytest.raises(ConfigurationError) as exc:
        Settings.load()
    assert "MAX_RESUME_SIZE_MB" in str(exc.value)

    monkeypatch.setenv("MAX_RESUME_SIZE_MB", "10")
    monkeypatch.setenv("ENVIRONMENT", "staging")
    with pytest.raises(ConfigurationError) as exc:
        Settings.load()
    assert "ENVIRONMENT" in str(exc.value)


# ---------------------------------------------------------------------------
# GROQ model centralized + shared by the AI layer
# ---------------------------------------------------------------------------
def test_groq_model_drives_ai_chat_calls(monkeypatch):
    import types

    import modules.ai_analysis as ai_analysis

    assert ai_analysis.settings is core.config.settings  # single source

    calls = {}

    class _StubCompletions:
        def create(self, **kwargs):
            calls.update(kwargs)
            message = types.SimpleNamespace(content="done")
            choice = types.SimpleNamespace(message=message)
            return types.SimpleNamespace(choices=[choice])

    class _StubClient:
        chat = types.SimpleNamespace(completions=_StubCompletions())

    monkeypatch.setattr(ai_analysis, "client", _StubClient())
    monkeypatch.setattr(settings, "groq_model", "groq/x-centralized")

    ai_analysis._chat("ping", temperature=0.4, response_format={"type": "json_object"})
    assert calls["model"] == "groq/x-centralized"


def test_no_other_groq_model_sources():
    """No module defines its own model/key constants — all read ``settings``."""
    import modules.ai_analysis as ai_analysis

    assert not hasattr(ai_analysis, "GROQ_MODEL")
    assert not hasattr(ai_analysis, "GROQ_API_KEY")


# ---------------------------------------------------------------------------
# Database URL centralized
# ---------------------------------------------------------------------------
def test_database_url_flows_through_settings(monkeypatch):
    monkeypatch.delenv("TALENTIQ_DB_PATH", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/prod.db")
    s = Settings.load()
    assert s.database_url == "sqlite:///data/prod.db"
    assert s.database_file is None


def test_database_path_override_resolves_through_singleton(monkeypatch, tmp_path):
    target = tmp_path / "override.db"
    monkeypatch.setenv("TALENTIQ_DB_PATH", str(target))
    monkeypatch.setattr(core.db, "_db_path", None)
    monkeypatch.setattr(core.db, "_engine", None)
    monkeypatch.setattr(core.db, "_session_local", None)
    monkeypatch.setattr(core.db, "_initialized", False)
    from pathlib import Path

    assert core.db.get_db_path() == Path(str(target))


# ---------------------------------------------------------------------------
# File limits centralized
# ---------------------------------------------------------------------------
def test_file_limits_single_sourced():
    assert fss.MAX_UPLOAD_BYTES == settings.max_resume_size_mb * 1024 * 1024
    assert fss.TEMP_DIRECTORY == settings.temp_directory
    assert tuple(fss.allowed_extensions()) == settings.allowed_resume_extensions
    assert core.config.ALLOWED_RESUME_EXTENSIONS == settings.allowed_resume_extensions


def test_recruiter_threshold_flows_to_services():
    from services.candidate_search_service import score_eligible_min

    assert score_eligible_min() == settings.recruiter_score_threshold


# ---------------------------------------------------------------------------
# Dependency manifest hygiene
# ---------------------------------------------------------------------------
_THIRD_PARTY_IMPORT_ROOTS = {
    "dotenv",
    "groq",
    "pandas",
    "pdfplumber",
    "plotly",
    "reportlab",
    "sentence_transformers",
    "sklearn",
    "sqlalchemy",
    "streamlit",
}

# Import name -> distribution name on PyPI.
_IMPORT_TO_DISTRIBUTION = {
    "dotenv": "python-dotenv",
    "groq": "groq",
    "pandas": "pandas",
    "pdfplumber": "pdfplumber",
    "plotly": "plotly",
    "reportlab": "reportlab",
    "sentence_transformers": "sentence-transformers",
    "sklearn": "scikit-learn",
    "sqlalchemy": "SQLAlchemy",
    "streamlit": "streamlit",
}

_SOURCE_DIRS = ("core", "modules", "services", "ui", "pages")


def _requirement_file(name):
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / name


def _declared_requirements(name):
    lines = _requirement_file(name).read_text(encoding="utf-8").splitlines()
    return [
        line.split("==")[0].strip().lower()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def test_requirements_are_utf8_readable():
    """A UTF-16 manifest makes git treat the file as binary (no diffs, no
    review) and breaks tooling that assumes UTF-8."""
    for name in ("requirements.txt", "requirements-dev.txt"):
        raw = _requirement_file(name).read_bytes()
        assert not raw.startswith((b"\xff\xfe", b"\xfe\xff")), (
            f"{name} has a UTF-16 BOM; save it as UTF-8"
        )
        assert b"\x00" not in raw, f"{name} contains NUL bytes (UTF-16 encoded)"
        raw.decode("utf-8")  # raises if not valid UTF-8


def test_requirements_have_no_duplicate_entries():
    for name in ("requirements.txt", "requirements-dev.txt"):
        declared = _declared_requirements(name)
        assert len(declared) == len(set(declared)), (
            f"{name} lists a package more than once: {declared}"
        )


def test_source_files_have_no_byte_order_mark():
    """A UTF-8 BOM in a .py file is tolerated by the import system but breaks
    every tool that re-parses the decoded text (``ast.parse``, some linters)
    and makes git treat the file as binary."""
    import ast
    from pathlib import Path

    project_root = _requirement_file("requirements.txt").parent
    targets = [project_root / "app.py"]
    for directory in _SOURCE_DIRS:
        targets.extend((project_root / directory).rglob("*.py"))
    targets.extend((project_root / "tests").rglob("*.py"))

    offenders = [
        str(path.relative_to(project_root))
        for path in targets
        if path.read_bytes().startswith(b"\xef\xbb\xbf")
    ]
    assert not offenders, f"UTF-8 BOM found (strip it): {offenders}"

    # Every source file must also be parseable as plain text.
    for path in targets:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_every_third_party_import_is_declared():
    """Every third-party module the app imports must be installable.

    Catches the reverse failure too: a requirement for a package nothing
    imports (the old file shipped spaCy + a 12.8 MB language model for a
    dependency-free NLP path).
    """
    import ast

    project_root = _requirement_file("requirements.txt").parent
    imported = set()
    for directory in _SOURCE_DIRS:
        for path in (project_root / directory).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(
                        alias.name.split(".")[0] for alias in node.names
                    )
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        imported.add(node.module.split(".")[0])

    third_party = {name for name in imported if name in _THIRD_PARTY_IMPORT_ROOTS}
    declared = set(_declared_requirements("requirements.txt"))
    missing = {
        _IMPORT_TO_DISTRIBUTION[name]
        for name in third_party
        if _IMPORT_TO_DISTRIBUTION[name].lower() not in declared
    }
    assert not missing, f"undeclared dependencies: {sorted(missing)}"


def test_test_framework_is_not_a_runtime_dependency():
    """pytest belongs to requirements-dev.txt so production images stay lean."""
    runtime = set(_declared_requirements("requirements.txt"))
    dev = set(_declared_requirements("requirements-dev.txt"))
    assert "pytest" not in runtime
    assert "pytest" in dev


# ---------------------------------------------------------------------------
# Secrets never reach logs / summaries
# ---------------------------------------------------------------------------
def test_redact_secrets_scrubs_known_secrets(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "sk-super-secret-12345")
    monkeypatch.setattr(settings, "admin_password", "pw-hunter2-value")

    raw = (
        "key=sk-super-secret-12345 GROQ_API_KEY=sk-super-secret-12345 "
        "Authorization: Bearer pw-hunter2-value ok=1"
    )
    redacted = redact_secrets(raw)
    assert "sk-super-secret-12345" not in redacted
    assert "pw-hunter2-value" not in redacted
    assert redacted.count("[REDACTED]") >= 3


def test_safe_summary_excludes_all_secrets(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "sk-do-not-log")
    monkeypatch.setattr(settings, "admin_password", "pw-do-not-log")
    monkeypatch.setattr(settings, "admin_email", "root@talent.iq")
    summary = settings.safe_summary()
    blob = re.sub(r"\s+", " ", str(summary)).lower()
    assert "sk-do-not-log" not in blob
    assert "pw-do-not-log" not in blob
    assert "root@talent.iq" not in blob


def test_startup_log_includes_only_names_never_values(monkeypatch, caplog):
    import core.logging_config as log_config

    monkeypatch.setattr(log_config, "_CONFIGURED", True)  # keep pytest's handler
    monkeypatch.setattr(settings, "groq_api_key", "sk-caplog-secret")
    monkeypatch.setattr(settings, "admin_password", "pw-caplog-secret")
    with caplog.at_level(logging.INFO, logger="core.config"):
        log_startup_config()
    joined = "\n".join(
        record.getMessage() for record in caplog.records
        if record.name.startswith("core")
    )
    assert "sk-caplog-secret" not in joined  # never the value
    assert "pw-caplog-secret" not in joined
    assert joined  # settings summary is actually logged