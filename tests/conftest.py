"""Test configuration for TalentIQ.

Every test runs against its own throwaway SQLite file:

- ``TALENTIQ_DB_PATH`` is pointed at a fresh temp file before the database
  layer is first used, and the ``core.db`` process singletons (path, engine,
  session factory, init flag) are reset between tests so each test starts from
  an empty database. The real ``data/talentiq.db`` is never touched.

The ``factory`` fixture persists candidates and analysis snapshots through the
real service layer (``candidate_service`` + ``analysis_service``) so the tests
exercise exactly the code path the recruiter UI uses.
"""

import os
import tempfile
import uuid

import pytest

# Safe fallback so nothing can silently reach the real database if a test
# module imports the DB layer before any fixture pointed at a temp file.
_FALLBACK_TMP = tempfile.mkdtemp(prefix="talentiq_pytest_")
os.environ.setdefault(
    "TALENTIQ_DB_PATH", os.path.join(_FALLBACK_TMP, "fallback.db")
)


def _reset_db_singletons(db):
    """Point ``core.db`` at nothing so the next connection re-resolves the path."""
    if getattr(db, "_engine", None) is not None:
        try:
            db._engine.dispose()
        except Exception:  # pragma: no cover - teardown is best-effort
            pass
    db._db_path = None
    db._engine = None
    db._session_local = None
    db._initialized = False


@pytest.fixture(autouse=True)
def _no_live_ai_credentials(monkeypatch):
    """Guarantee the suite can never reach Groq or depend on a local ``.env``.

    ``core.config`` calls ``load_dotenv()`` at import time, so without this a
    developer's real ``GROQ_API_KEY`` would leak into every test session:
    assertions would pass or fail based on local secrets, and
    ``ai_analysis._chat`` would issue LIVE billed API requests. Every test
    therefore starts with no key and no cached client; tests that exercise the
    AI layer install a stub with ``monkeypatch.setattr(ai_analysis, "client", ...)``.
    """
    from core.config import settings
    from modules import ai_analysis

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(settings, "groq_api_key", None)
    monkeypatch.setattr(ai_analysis, "client", None)


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    """A fresh, empty SQLite database isolated to the current test."""
    db_path = tmp_path / f"test_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("TALENTIQ_DB_PATH", str(db_path))

    import core.db as db

    _reset_db_singletons(db)
    db.init_db()
    yield db
    _reset_db_singletons(db)


@pytest.fixture()
def factory(fresh_db):
    """Persist a candidate + one analysis snapshot through the real services."""

    from services.analysis_service import save_analysis
    from services.candidate_service import create_candidate

    def _make(
        *,
        name="Candidate",
        email=None,
        resume_filename=None,
        resume_hash=None,
        status="Analyzed",
        skills=None,
        ats_score=70.0,
        jd_match_score=70.0,
        final_score=None,
        jd_id=None,
    ):
        if final_score is None:
            final_score = round(ats_score * 0.4 + jd_match_score * 0.6, 2)
        candidate = create_candidate(
            resume_hash=resume_hash or uuid.uuid4().hex,
            name=name,
            email=email,
            resume_filename=resume_filename,
            status=status,
        )
        analysis = save_analysis(
            candidate_id=candidate.id,
            jd_id=jd_id,
            ats_score=ats_score,
            jd_match_score=jd_match_score,
            final_score=final_score,
            matched_skills=skills,
            missing_required_skills=[],
            missing_preferred_skills=[],
        )
        return candidate, analysis

    return _make