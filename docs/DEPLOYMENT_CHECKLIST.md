# TalentIQ deployment checklist

Work top to bottom. Every box is either **verified** — it was actually executed
and the result is recorded — or explicitly marked as needing your own
infrastructure. Nothing here is aspirational.

Legend: **[x]** verified in this repo · **[ ]** requires your host/platform

---

## How these results were produced

Two harnesses were run against an **isolated deployment tree** — a copy of only
the files that ship (`app.py`, `core/`, `modules/`, `services/`, `ui/`,
`.streamlit/`, `data/skills.json`, `requirements.txt`), with **no `.env`, no
`.git`, no `tests/`, no `docs/`, and no pre-existing database**. Configuration
arrived only through the process environment, the way a hosting platform
supplies it.

| Harness | Result |
|---|---|
| `deploy_test.py` — the 15 deployment steps | **15/15 passed** |
| `failure_test.py` — forced failure modes | **7/7 handled cleanly** |
| `pytest -q` | **345 passed** |
| `docker build` + run | image built, ran healthy (see E) |

---

## 1. Clean install

- [x] **1.1** All 10 pinned runtime dependencies import in a fresh environment:
      `streamlit 1.60.0`, `plotly 6.9.0`, `pandas 3.0.5`, `sqlalchemy 2.0.54`,
      `groq 1.5.0`, `sentence-transformers 5.2.3`, `scikit-learn 1.9.0`,
      `pdfplumber 0.11.10`, `reportlab 5.0.0`, `python-dotenv 1.2.2`.
- [x] **1.2** `requirements.txt` resolves on a clean Linux venv inside the Docker
      dependency stage.
- [x] **1.3** Embedding model resolves from the local cache with
      `local_files_only=True` — no runtime download.
- [ ] **1.4** *(your host)* Dependencies install on **your** target platform.
      Not verified here: this machine's pip could not resolve
      `download-r2.pytorch.org` (`getaddrinfo failed`). Docker's network could,
      which is why 1.2 is verified and this is not.
- [ ] **1.5** *(your host)* Required data files present after your clone:
      `data/skills.json` (55 skills), `data/skills.csv`, `data/role_skills.json`.

## 2. External configuration

- [x] **2.1** `GROQ_API_KEY`, `GROQ_MODEL`, `ENVIRONMENT=production`, `DEBUG=false`,
      `MAX_RESUME_SIZE_MB=10` all read from the environment, not a `.env` file.
- [x] **2.2** The deployment tree contained **no** `.env`; the real API key was
      never written to disk in the deploy tree.
- [x] **2.3** `settings.safe_summary()` exposes no secret field, and startup
      output shows `GROQ_API_KEY is set (value not shown)`.
- [ ] **2.4** *(your platform)* You have set the section C variables in your
      platform's secret/environment store.

## 3. Database initialization

- [x] **3.1** `init_db()` on a fresh volume created the schema **before** any
      browser connected.
- [x] **3.2** All 8 tables created: `audit_logs`, `candidate_activity`,
      `candidate_status_history`, `candidates`, `job_descriptions`,
      `resume_analyses`, `resume_versions`, `users`.
- [x] **3.3** `PRAGMA foreign_key_check` clean on a fresh database.
- [x] **3.4** Database file owned by uid/gid `10001` (non-root `talentiq`).
- [x] **3.5** `init_db()` is idempotent — a second call changes nothing.
- [ ] **3.6** *(your platform)* **You have persistent storage and you have
      confirmed the database file is on it.** See section D of DEPLOYMENT.md.
      This is the single item that most often silently breaks a deployment.

## 4. Streamlit start

- [x] **4.1** `streamlit run app.py` reaches a healthy state and stays up.
- [x] **4.2** `GET /_stcore/health` → HTTP `200`, body `ok`.
- [x] **4.3** Container health status reaches `healthy`.
- [x] **4.4** Runtime user is non-root; `/app/data` is the only writable path.
- [x] **4.5** Missing `GROQ_API_KEY` makes the container **exit 1** instead of
      serving — fail-closed, verified in the container.

## 5. Application access

- [x] **5.1** `GET /` → HTTP `200`, Streamlit shell served.
- [x] **5.2** `app.py` **and all 10 pages** execute with no unhandled exception
      under `AppTest` — proving the script runs, not just that the server bound
      a port.
- [ ] **5.3** *(your browser)* The app renders and the theme is the intended
      dark theme. Not verifiable headlessly; please look at it once.

## 6. Login

- [x] **6.1** First registration with the bootstrap credentials is granted `ADMIN`.
- [x] **6.2** A wrong password is rejected.
- [x] **6.3** The correct password authenticates and establishes a session.
- [x] **6.4** `has_role(ADMIN)` is true for the admin; `has_role(SUPERUSER)` is
      false — RBAC resolves correctly.
- [x] **6.5** Passwords are stored as `scrypt` hashes; the plaintext does not
      appear in the stored hash.
- [ ] **6.6** *(your deployment)* You registered the real admin, then **removed
      `TALENTIQ_ADMIN_PASSWORD`** from the running environment.

## 7. Upload

- [x] **7.1** A valid PDF resume is accepted (magic bytes, extension, size).
- [x] **7.2** `calculate_file_hash` matches a plain SHA-256 of the bytes.
- [x] **7.3** `sanitize_filename` neutralises 8 hostile names — path traversal,
      absolute paths, empty, whitespace, over-length, NUL byte, unicode — so the
      result is always a bare filename that cannot escape its directory.
- [x] **7.4** Upload limit is enforced at `MAX_RESUME_SIZE_MB`.

## 8. Analyze

- [x] **8.1** Text is extracted from a real PDF (590 chars in the fixture).
- [x] **8.2** A structured profile is built: 7 skills, 2 experience entries,
      1 education entry.
- [x] **8.3** ATS score is computed and in range (`overall_score=74.0`, 5 sections
      detected, 3/3 keyword coverage).
- [x] **8.4** This runs the **same** call path as `pages/2_Resume_Results.py`
      (`build_resume_profile` → `analyze_ats(profile)`), not an alternate entry
      point.

## 9. Results

- [x] **9.1** JD match produced a bounded score with the expected component
      breakdown (`overall=79.6`, `skill=100.0`, `semantic=78.9`, `tfidf=29.6`,
      `semantic_available=True`).
- [x] **9.2** Matched and missing-required skills are both computed.

## 10. Persistence

- [x] **10.1** Job description, candidate, resume version (`v1`) and analysis all
      saved with ids.
- [x] **10.2** Row counts confirmed directly in SQLite.
- [x] **10.3** `final_score` persisted (`77.36`).
- [x] **10.4** After dropping every cached singleton and re-opening the database
      (a restart), all data is still present.

## 11. Recruiter dashboard

- [x] **11.1** Multiple candidates (3) listed for the owning user.
- [x] **11.2** Status transitions work: `New → Analyzed → Shortlisted`, with
      shortlist and by-status queries returning the right counts.
- [x] **11.3** **Ownership isolation holds:** another recruiter sees **0** of those
      candidates.

## 12. PDF report

- [x] **12.1** Report generated entirely in memory — no temp file created.
- [x] **12.2** Output starts with the `%PDF-` magic bytes.
- [x] **12.3** Output round-trips through `pdfplumber` (1 page, text extractable),
      so it is a real, readable PDF.

## 13. Multiple candidates and restart persistence

- [x] **13.1** Three distinct candidates with different skills analysed in one run.
- [x] **13.2** All survived a simulated restart.
- [ ] **13.3** *(your deployment)* **A real container restart or redeploy keeps
      the data.** Verified in-process and on a mounted volume, but not through
      your platform's actual restart cycle. Do this once by hand.

## 14. Audit logs

- [x] **14.1** 11 audit rows written for real activity: `AUTH_LOGIN`,
      `AUTH_LOGIN_FAILED`, `USER_CREATED`, `CANDIDATE_CREATED`,
      `RESUME_VERSION_CREATED`, `JOB_DESCRIPTION_CREATED`, `CANDIDATE_ANALYZED`,
      `CANDIDATE_SHORTLISTED`.
- [x] **14.2** No secret (API key, admin password, test password) appears in any
      audit description.
- [x] **14.3** No candidate PII or raw resume text leaks into audit descriptions.

## 15. AI Resume Coach

- [x] **15.1** A **live Groq API call** with the real key returned
      `ai_generated=True`, 3 priority improvements, a 280-character overall
      assessment, and all 5 advisory sections populated.
- [x] **15.2** Every recommendation has all four fields populated
      (`priority`, `issue`, `recommendation`, `reason`), as the page renders them.
- [x] **15.3** Bounded retries were observed working against a **real** transient
      `APIConnectionError`, which the second attempt recovered from.
- [x] **15.4** A bad `GROQ_MODEL` degrades to the deterministic fallback rather
      than crashing.
- [ ] **15.5** *(your deployment)* Confirm your provider account's quota and
      billing. The verification calls were made against the local `.env` key.

---

## Forced failure modes — all handled cleanly

- [x] **F.1** `GROQ_API_KEY` missing → startup check exits 1 and names the
      variable, pointing at the platform's secret store rather than a `.env` file.
- [x] **F.2** `DATABASE_URL=postgresql://admin:Sup3rSecret@db.internal:5432/...`
      → fail-closed. The scheme is shown; the **username, password, host and port
      never appear** in any output. `core.db` raises `ConfigurationError`.
- [x] **F.3** A missing relative scratch directory is **created on demand**
      (correct, not an error); an uncreatable path and a **read-only** directory
      both fail the check with the configured directory named.
- [x] **F.4** Four malformed uploads rejected (wrong extension, no PDF magic
      bytes, zero-length, non-PDF binary) plus an oversize file — each with a
      specific, non-technical message.
- [x] **F.5** A text-less PDF either fails validation or raises a clean
      `ValueError` ("did not contain readable text"); no pdfplumber internals escape.
- [x] **F.6** Groq unreachable → degrades to `ai_generated=False` with 5
      evidence-based recommendations; no transport error text shown to the user.
- [x] **F.7** Database unavailable → an unusable path fails the startup check; a
      locked database raises rather than reading as an empty result; a non-SQLite
      URL is refused with `ConfigurationError`.

**Gap found and fixed during this step:** the database-path check accepted a
path whose parent was a regular *file*, because `Path.exists()` is true for
files and `os.access(W_OK)` reports a writable file as writable. That
misconfiguration passed as healthy and only failed later as an opaque SQLite
error. The check now requires a real directory, with a regression test
(`test_startup_check_flags_db_path_whose_parent_is_a_file`) and a positive test
that a healthy directory is still accepted.

---

## Container verification (completed before the build host ran out of disk)

- [x] **C.1** `docker build -t talentiq:step2 .` succeeded from a clean context;
      the dependency stage installed `requirements.txt` into a fresh Linux venv.
- [x] **C.2** Image: 2.84 GB, user `talentiq`, port 8501, healthcheck configured.
- [x] **C.3** No `.env` in `/app`; the real `GROQ_API_KEY` value is **not**
      present anywhere in `/app`; no app secret in the image's env config.
- [x] **C.4** Without `GROQ_API_KEY` the container exits `1` through
      `python -m core.startup_check`.
- [x] **C.5** With secrets passed via `-e` (never `-e KEY=value` in a committed
      file) the container reached `healthy`.
- [x] **C.6** `GET /_stcore/health` → 200 `ok`; `GET /` → 200.
- [x] **C.7** On a **fresh named volume**, `init_db()` created
      `/app/data/talentiq.db` with 8 tables, `foreign_key_check` clean, owned by
      uid 10001 — before any browser connected.

- [ ] **C.8** *(repeat on your machine)* Rebuild and rerun C.2–C.7. The
      verification host exhausted its disk during a second, larger build and its
      Docker daemon could not be restarted without a machine-level reset, so
      these results are from the first successful build only. **The image build is
      reproducible; please re-verify before you rely on it.**

---

## Regression suite

- [x] **R.1** `python -m pytest -q` → **345 passed**.
- [x] **R.2** Hermetic: Groq stubbed, temporary database, no paid API call.
- [x] **R.3** Migration regressions cover populated child rows, FK integrity after
      migration, pragma restoration, cleanup, idempotency, and owner-scoped
      duplicate job-description text.

---

## Known gaps — not fixed, by design

These are real limitations, documented in PRODUCTION_LIMITATIONS.md. None is
fixed by configuration.

- [ ] **G.1** **SQLite only.** No PostgreSQL/MySQL support. A platform that only
      offers Postgres cannot host this safely.
- [ ] **G.2** **Single replica.** No horizontal scaling; SQLite cannot be shared
      over a network filesystem.
- [ ] **G.3** **No browser-level E2E or load testing.** No Playwright/Selenium
      coverage and no multi-user concurrency testing. The 15 steps are executed
      server-side and in-process.
- [ ] **G.4** **Registration is open.** Anyone who can reach the app can create a
      recruiter account. Put it behind SSO or an identity-aware proxy.
- [ ] **G.5** **No cloud object storage.** Resume bytes and PDF reports are
      in-memory only; nothing is persisted for later retrieval.
- [ ] **G.6** **No rate limiting or brute-force lockout** on the login page.
- [ ] **G.7** **No automated backup rotation.** Section D.4 gives you the command;
      scheduling and retention are yours.

---

## Reproducing these results

The two harnesses live in `tools/` and are deliberately **not** pytest modules:
each one must run as a real deployment, with configuration arriving from the
environment, or it could pass by inheriting a developer's shell. Both are also
excluded from the test suite so a paid API call can never happen during `pytest`.

```bash
# 1. Build an isolated tree of only the files that ship.
#    No .env, no .git, no tests/, no docs/, no pre-existing database.
python - <<'PY'
import shutil, pathlib
src, dst = pathlib.Path("."), pathlib.Path("/tmp/deployroot")
if dst.exists(): shutil.rmtree(dst)
dst.mkdir(parents=True)
for item in ["app.py", "requirements.txt", ".streamlit", "core", "modules",
             "services", "ui", "data"]:
    s = src / item
    if s.exists(): shutil.copytree(s, dst / item, dirs_exist_ok=True)
for pat in ["talentiq.db", "talentiq.db-wal", "talentiq.db-shm", "*.db-journal"]:
    for f in dst.rglob(pat): f.unlink()
for junk in ["data/tmp", "data/uploads"]:
    shutil.rmtree(dst / junk, ignore_errors=True)
shutil.copy("tools/verify_deployment.py", dst)
shutil.copy("tools/verify_failure_modes.py", dst)
print("built", dst)
PY

# 2. Supply configuration ONLY through the environment, as a platform would.
export GROQ_API_KEY=...            # never written into the tree
export GROQ_MODEL=openai/gpt-oss-120b
export ENVIRONMENT=production
export DEBUG=false
export MAX_RESUME_SIZE_MB=10
export TALENTIQ_ADMIN_EMAIL=admin@example.com
export TALENTIQ_ADMIN_PASSWORD=...

# 3. Run both harnesses from inside the isolated tree.
cd /tmp/deployroot
python verify_deployment.py        # -> 15/15 steps passed
python verify_failure_modes.py     # -> 7/7 failure modes handled cleanly
```

Both exit `0` only if every check passes, so they work as CI or post-deploy
gates. Set `DEPLOY_TEST_TRACE=1` to print the traceback of any failing step.

> The 15-step harness makes **real** Groq API calls (step 15) and starts a real
> Streamlit server (step 4). It is a post-deploy smoke test, not a unit test.
