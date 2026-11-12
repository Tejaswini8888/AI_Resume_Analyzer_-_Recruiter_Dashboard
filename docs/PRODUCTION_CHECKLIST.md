# TalentIQ — Production Readiness Checklist

Phase 4 · Step 6 production-readiness audit.

**Status legend:** `[x]` verified by an executed check · `[ ]` not implemented ·
`[-]` out of scope for this phase

Each `[x]` records how it was verified, so the claim can be reproduced rather
than taken on trust.

---

## 1. Build & dependencies

- [x] **Dependency manifest is valid UTF-8.** It was previously saved as
      UTF-16 with a BOM, which made git treat the file as binary (no diffs, no
      review) and broke any tool assuming UTF-8.
      — verified by `tests/test_config.py::test_requirements_are_utf8_readable`
- [x] **Manifest lists direct dependencies only.** The previous file was a
      283-package whole-environment snapshot that shipped `spacy` plus a 12.8 MB
      `en_core_web_sm` language model the application never imports.
      — verified by `tests/test_config.py::test_every_third_party_import_is_declared`
- [x] **Pinned versions match the verified environment.** The old pins did not
      match what was actually installed and tested (e.g. `streamlit` 1.31.0 vs
      1.60.0, `pandas` 2.3.3 vs 3.0.5, `groq` 1.0.0 vs 1.5.0), so a fresh clone
      installed a different application from the tested one.
      — pins taken from the passing test environment
- [x] **No duplicate entries.** `groq` was listed twice (once pinned, once not).
      — verified by `tests/test_config.py::test_requirements_have_no_duplicate_entries`
- [x] **Test tooling separated from runtime.** `pytest` moved to
      `requirements-dev.txt` so production images stay lean.
      — verified by `tests/test_config.py::test_test_framework_is_not_a_runtime_dependency`
- [x] **No dependency known to be required is missing.** Every third-party
      import in `core/`, `modules/`, `services/`, `ui/` and `pages/` resolves to
      a declared distribution.
- [x] **No stray byte-order marks in source.** `pages/4_Recruiter_Results.py`
      carried a UTF-8 BOM, which git treats as binary and which breaks any tool
      that re-parses the decoded text. Removed; a test now guards the whole tree.
      — `tests/test_config.py::test_source_files_have_no_byte_order_mark`
- [ ] **No lockfile with hashes** (`requirements.lock` / `uv.lock` /
      `poetry.lock`). Installs are version-pinned but not hash-verified, so a
      compromised or re-uploaded artifact would not be detected.
- [ ] **No automated dependency vulnerability scanning** (`pip-audit`,
      `Dependabot`, `safety`). Not installed and not run in CI.

## 2. Configuration

- [x] **Single typed configuration object.** `core/config.Settings` is the only
      place environment values are read; services and pages never call
      `os.getenv`. — `core/config.py`
- [x] **`.env.example` documents every supported variable** with safe defaults
      and contains placeholders only.
- [x] **Invalid configuration fails fast with a clear, value-free error**
      (bad booleans, integers, floats, extensions, environment, log level).
      — `tests/test_config.py::test_invalid_values_raise_clear_configuration_errors`
- [x] **Secrets have no defaults** and are reported missing *by name*.
- [x] **A single source of truth for the AI model.** No module defines its own
      model or key constant; a test asserts the AI layer reads `settings`.
      — `tests/test_config.py::test_no_other_groq_model_sources`
- [x] **File limits, allowed extensions and directories are single-sourced**
      from configuration. — `tests/test_config.py::test_file_limits_single_sourced`
- [x] **The AI layer remains importable and usable without an API key** (the
      client is built lazily), and users get a clear configuration message
      rather than a crash. — `modules/ai_analysis.py:15-24`

## 3. Database

- [x] **Idempotent initialization.** `init_db()` only creates missing tables and
      never drops or recreates existing data.
- [x] **Migrations are additive and non-destructive**, and are re-runnable.
      Existing rows and scores are preserved; verified by
      `tests/test_migration.py`.
- [x] **Legacy databases are upgraded, never wiped** — pre-versioning and
      pre-auth schemas are migrated in place and historical analyses are
      backfilled onto resume version 1.
- [x] **Foreign keys are enforced on every connection.**
      — `core/db.py:86-91`
- [x] **A schema defect found during this audit was fixed with a data-preserving
      migration.** `job_descriptions.content_hash` carried a *global* `UNIQUE`,
      which contradicted the documented owner-scoped dedup: when a second
      recruiter pasted the same job description text the save failed with an
      `IntegrityError`. Uniqueness is now `(content_hash, created_by_user_id)`.
      Because SQLite cannot drop an index that backs a table constraint, the
      migration rebuilds the table from the current model DDL and copies every
      row. — `core/models.py`, `core/db.py`,
      `tests/test_migration.py::test_legacy_global_jd_unique_index_is_replaced`,
      `tests/test_migration.py::test_legacy_jd_rows_survive_rebuild`
- [x] **The fix has a regression test at the isolation level** (two recruiters,
      identical text, separate private rows) and at the same-owner level (reuse,
      not duplicate).
- [x] **No deprecated SQLAlchemy API remains.** All `session.query(X).get(id)`
      calls were migrated to `session.get(X, id)`; verified no occurrences remain.
- [x] **Lookups are indexed**, including the composite
      `(content_hash, created_by_user_id)` index whose leftmost column also
      serves the by-hash lookup.
- [x] **Tests never touch the real database.** Every test runs against its own
      throwaway SQLite file and resets the process singletons.
      — `tests/conftest.py`
- [ ] **No database backup or restore procedure.** A production deployment has
      no documented, tested way to back up `data/talentiq.db`.
- [ ] **No migration version table.** Schema state is inferred by probing
      tables/columns, which is safe and idempotent but cannot record a version
      history or support ordered downgrades.

## 4. Reliability & error handling

- [x] **Database initialization failures are no longer silently discarded.**
      `app.py` swallowed every exception with `except Exception: pass`, so a
      broken database was invisible in production while the UI appeared to
      work. The failure is now logged by exception *type* (never the message,
      which could embed a path or DSN) and the UI still loads.
      — `app.py`
- [x] **AI failures degrade gracefully.** `_chat` converts any provider error
      into a fixed, non-leaking message; the resume coach falls back to
      deterministic, evidence-based recommendations so no page renders blank.
- [x] **AI request volume is bounded.** The coach makes at most **two** Groq
      calls (one JSON-mode, one plain-text parse fallback) before falling back,
      so a failure cannot fan out into unbounded billed retries.
      — `modules/resume_coach.py:435-467`
- [x] **Semantic model loading is local and cached**, so scoring works without
      a network call. — `services/semantic_matcher.py`
- [x] **Persistence failures never break the UI** — pages treat persistence as
      best-effort and show clean, non-technical messages.
- [ ] **No health-check endpoint** for a load balancer or orchestrator.
- [ ] **No structured log shipping or error reporting** (Sentry et al.); logs
      go to the process stdout only.
- [ ] **No alerting** on error rates, failed logins or AI failure rates.
- [ ] **No retry with backoff** for transient provider/network errors; failures
      surface immediately to the user.

## 5. Correctness

- [x] **One canonical JD match score across the whole product.**
      `pages/2_Resume_Results.py` displayed `jd_match.similarity_score` (the raw
      TF-IDF cosine *component*) as "Match Score" while persisting
      `jd_match.overall_match_score`. A candidate therefore saw one number and
      the recruiter later saw a different number for the identical analysis.
      The page now displays the combined score it also persists; the TF-IDF
      component remains available as `text_similarity_score` for breakdowns.
      — `pages/2_Resume_Results.py`, `tests/test_score_consistency.py`
- [x] **The scoring formulas themselves are unchanged** — the fix aligned the
      *display* with the already-persisted, already-ranked value.
- [x] **Analysis rejects a dangling `jd_id`/`resume_version_id`** rather than
      writing a broken reference, and a version must belong to the candidate.
      — `services/analysis_service.py`, `tests/test_resume_versioning.py`
- [x] **Resume versioning is append-only**; historical analyses stay reachable
      and version numbers are never renumbered.
      — `tests/test_resume_versioning.py`

## 6. Testing

- [x] **Full suite passes: 298 tests, 0 failures.**
      `.\venv\Scripts\python.exe -m pytest -q`
- [x] **The suite is hermetic.** An autouse fixture clears `GROQ_API_KEY` and
      the cached Groq client for every test. Previously `core.config` called
      `load_dotenv()` at import time, so a developer's real key was injected
      into the session: results depended on local secrets and
      `ai_analysis._chat` issued **live, billed API requests** during the test
      run. — `tests/conftest.py`, `tests/test_security_hardening.py`
- [x] **Every page and module is verified loadable.** 62 smoke tests import
      every module for real, byte-compile every page without executing its UI,
      and resolve every `from core|modules|services|ui import X` in a page
      against the real module. This is the check that would have caught the
      deleted `SCORE_ELIGIBLE_MIN` constant, which shipped unnoticed because a
      Streamlit page only executes when a user navigates to it.
      — `tests/test_import_smoke.py`
- [x] **Tests cover auth, ownership isolation, audit logging, file security,
      migration, versioning, ranking, search, analytics and configuration.**
- [ ] **No coverage measurement.** No `pytest-cov` run, so the true coverage of
      the UI layer (Streamlit pages) is unknown.
- [ ] **No end-to-end browser tests.** Page behaviour is verified by import and
      static analysis, not by driving the UI.

## 7. Runtime & deployment

- [x] **The application boots and serves traffic.** `streamlit run app.py`
      returns HTTP 200 with a rendered shell and logs no errors or warnings.
- [x] **Product theme and server settings are version-controlled.**
      `.gitignore` excluded the entire `.streamlit/` directory, so a fresh
      clone silently lost the dark theme. It now tracks `config.toml` while
      still ignoring `secrets.toml` and local logs.
- [x] **Usage telemetry is disabled.** — `.streamlit/config.toml`
- [x] **Runtime artifacts are ignored**: `__pycache__`, `venv`, `*.log`,
      `data/*.db*`, `data/uploads/`, `data/tmp/`, test and build artifacts.
- [ ] **No container image.** No `Dockerfile` or `docker-compose.yml`.
- [ ] **No CI pipeline.** No GitHub Actions workflow; nothing runs the tests or
      the audit automatically on push.
- [ ] **No reverse proxy or TLS configuration** is provided.
- [ ] **No production process supervisor** (systemd, gunicorn/uvicorn worker
      configuration, restart policy).
- [ ] **Single-process SQLite only.** There is no multi-worker configuration;
      `check_same_thread=False` is set for Streamlit's threading, but concurrent
      writers are not designed for and there is no WAL/queue strategy.
- [ ] **No staging environment** and no documented promote-to-production path.
- [x] **Setup documentation exists and matches the manifest split.**
      `README.md` covers features, tech stack, project structure, installation,
      configuration (every environment variable), running the app, and running
      the tests. Its install and test commands were corrected to match the new
      `requirements.txt` / `requirements-dev.txt` split.
- [ ] **No deployment documentation.** Nothing covers TLS termination, process
      supervision, backup/restore, or promotion between environments.

---

## Minimum before a real production launch

1. Back up `data/talentiq.db` and document restore.
2. Terminate TLS in front of Streamlit and set `ENVIRONMENT=production`.
3. Set `GROQ_API_KEY` from a secrets manager; leave the admin bootstrap unset
   once the admin exists.
4. Add a `Dockerfile` and a CI job that runs `pytest -q` and the smoke tests.
5. Add sign-in rate limiting and disk encryption for the database file.
6. Write deployment documentation (TLS, process supervision, backup/restore).
