# Production Limitations

An honest inventory of what this application does not do. Nothing here is a
hidden bug — each item is a deliberate scope boundary, a known constraint, or
an unverified area. Read this before promising a capability to a stakeholder.

Related: [DEPLOYMENT.md](DEPLOYMENT.md).

---

## 1. Not implemented

These are absent, not broken. None of them is planned.

| Gap | Impact |
|---|---|
| **No HTTPS/TLS termination** | Streamlit speaks plain HTTP. A reverse proxy providing TLS is mandatory for anything beyond localhost. |
| **No CSRF token beyond Streamlit's own** | Relies entirely on Streamlit's built-in XSRF protection, which is left enabled. |
| **No rate limiting** | Login, registration and resume upload are unthrottled. See §3. |
| **No email verification or password reset** | An address is never confirmed; a lost admin password cannot be recovered through the UI. |
| **No MFA / SSO / OAuth** | Password-only authentication. |
| **No multi-tenancy isolation beyond owner scoping** | Data is scoped per recruiter by `created_by_user_id`. There is no organisation, workspace or team layer. |
| **No scheduled/background processing** | Every analysis runs synchronously in the request. Large files block the session. |
| **No resume parsing queue or retry** | A failed parse must be retried by the user. |
| **No per-user quota or storage cap** | Disk growth is bounded only by the volume. |
| **No audit log export** | Audit events are queryable in the admin UI and stored in SQLite only. |
| **No database migration versioning** | See §2. |
| **No Prometheus metrics endpoint** | Health is a single boolean endpoint. |
| **No structured log shipping config** | Logs go to stdout/stderr in a human-readable format. |
| **No CI pipeline** | No workflow file, no automated test-on-push. Tests must be run manually. |
| **No dependency lockfile** | `requirements.txt` pins direct dependencies; transitive versions are not pinned. See §2. |
| **No image scanning / SBOM / signing** | The container is neither scanned nor signed. |

---

## 2. Reproducibility caveats

### Pinned vs locked

`requirements.txt` pins **direct** dependencies to the exact versions the suite
is verified against. Transitive dependencies resolve to whatever satisfies those
pins *at install time*.

**Consequence:** a fresh install today can differ from the verified environment,
and a future install can differ again. For a strictly reproducible build, add a
lockfile (`pip-compile` / `pip-tools` to generate, `pip install -r` to consume)
or build the container once and pin the resulting image digest. This is a known
gap, not an oversight.

### Schema migrations are implicit

There is no `alembic` version table. `init_db()` creates missing tables and adds
missing columns on every startup. It is non-destructive and idempotent, and the
`job_descriptions` uniqueness widening rebuilds exactly one table.

**Consequence:** there is no automated downgrade path and no recorded migration
history. A schema change that needs data transformation is not expressible in the
current design. Back up before deploying over an existing database.

### Docker image is built and verified

The image **was built and run successfully** (`docker build -t talentiq:step2 .`).
Verified against the built image:

| Property | Observed |
|---|---|
| Image size | **2.84 GB** |
| Runtime user | `talentiq` (uid/gid `10001`), non-root |
| Port / healthcheck | `8501`, `GET /_stcore/health` |
| `.env` inside the image | none (`.dockerignore` excludes it) |
| Real `GROQ_API_KEY` in `/app` | **absent**; no app secret in the image env config |
| Missing key | container **exits 1** through `python -m core.startup_check` |
| With secrets passed via `-e` | container reached `healthy`; `/` and `/_stcore/health` both HTTP 200 |
| Fresh named volume | `init_db()` created `talentiq.db` with 8 tables, `foreign_key_check` clean, owned by uid 10001, **before** any browser connected |
| Dependency stage | installed `requirements.txt` into a clean Linux venv |

**One caveat.** The build host later exhausted its disk during a *second*,
larger build and its Docker daemon could not be restarted without a
machine-level reset. So the results above come from the first successful build
and have not been re-confirmed on a second run. The build is reproducible, but
re-verify it on your own machine before relying on it. The full 15-step
deployment walkthrough was separately completed on the host against an isolated
copy of the build context — see DEPLOYMENT_CHECKLIST.md.

### A clean-venv install on the host could not complete

`python -m venv` + `pip install -r requirements.txt` in a fresh environment was
attempted on the host and **failed on DNS resolution, not on the manifest**:

```
getaddrinfo failed  →  download-r2.pytorch.org unreachable
```

That is a property of *this* machine's network, not of the manifest. Two things
were verified instead:

1. **Every pin in `requirements.txt` matches the version actually installed in
   the working environment** (10/10 exact matches), and import-smoke tests
   confirm the manifest covers every third-party module the app imports.
2. **A clean Linux install does succeed where the network allows it** — the
   Docker dependency stage installed the full manifest into a fresh venv from
   scratch, and a throwaway clean-environment image was started for the same
   purpose.

So transitive resolution *is* proven, in the container path. It remains unproven
only for a bare-metal host on a network that cannot reach `download.pytorch.org`.

### Docker image size

Expect roughly **2.84 GB**, dominated by CPU-only `torch` and the embedding
model. The Dockerfile installs the CPU `torch` wheel explicitly; without that,
the CUDA build would add several GB. Note that `torch` is the large part, not
TalentIQ's own code.

---

## 3. Security posture

Appropriate for an internal or single-tenant deployment. **Not** appropriate for
direct public exposure without a proxy in front.

| Item | Status |
|---|---|
| Password hashing | scrypt with a per-user salt. Sound. |
| Session handling | Streamlit's own websocket sessions. Not revocable server-side. |
| SQL injection | Prevented by SQLAlchemy parameter binding throughout. |
| Upload validation | Extension allow-list (`.pdf`), magic-byte check, size cap, sanitised filename. |
| Path traversal | Filenames are sanitised and resolved under the configured directory. |
| Secrets in logs / repo | None. `.env` is gitignored; logging is secret-safe by construction. |
| CORS / XSRF | Streamlit's secure defaults, pinned explicitly and asserted by the startup check and the test suite. |
| **Rate limiting** | **Absent.** Login and registration are unthrottled, so credentials are brute-forceable and the account table can be spammed. Put a rate-limiting reverse proxy in front. |
| **Open registration** | **Anyone who can reach the app can create a recruiter account.** No invite, allow-list, or domain restriction. Put it behind SSO or an access proxy. |
| **Admin bootstrap secret** | The first ADMIN is created by registering with `TALENTIQ_ADMIN_EMAIL` / `TALENTIQ_ADMIN_PASSWORD`. Unset the password afterwards, or it stays a valid credential. |
| **No in-app security headers** | `Strict-Transport-Security`, `X-Frame-Options`, `CSP` and friends must be set by the reverse proxy. |
| **Single-instance SQLite** | Not suitable for a multi-replica deployment. |

---

## 4. AI behaviour

| Item | Status |
|---|---|
| AI provider | Groq, cloud-hosted. Resume text is sent to `api.groq.com` for AI summary, AI analysis, interview questions and the AI resume coach. **This is data leaving your infrastructure** — confirm it is acceptable for your candidates. |
| No key, no AI | AI features degrade gracefully to deterministic output. ATS scoring, skill extraction, JD matching and PDF reports are unaffected. |
| Coach retry | The AI resume coach makes **at most two** Groq calls, then returns a deterministic local result. This bound is intentional, not an oversight. |
| Startup check and network | The startup check **never** calls Groq. It checks only that the key is present. |
| Model availability | Verified on one model id (`openai/gpt-oss-120b`). Others are untested. |
| Determinism | AI output is non-deterministic by nature. Only the deterministic ATS/JD scores are reproducible. |
| Prompt injection | Resume text is untrusted and reaches the model. There is no output validation beyond schema shape. |
| Cost | No usage metering, quota, or spend cap. |

---

## 5. Scoring and matching

**The algorithms were not changed in Phase 5, and no scoring behaviour was
altered.** This section describes existing characteristics.

| Item | Status |
|---|---|
| ATS score | Rule-based keyword/format checks. No parser-model extraction. |
| JD match score | Weighted blend of skill overlap, TF-IDF similarity and — when the embedding model is available — semantic similarity. |
| **Semantic fallback is silent in scoring** | If the model is absent, JD matching falls back to skill + TF-IDF. **Scores will differ** from a fully provisioned host, with no user-visible indication. The startup check reports it as `WARN`. |
| Display vs persistence | The recruiter results page now displays the same `overall_match_score` that is persisted, so the ranked and detailed views agree. |
| Recruiter threshold | A configurable cut-off, default 60. It changes who appears on the dashboard, not the scores. |
| Bias | Skills-based matching systematically favours resumes whose vocabulary matches the job description. Not audited for demographic bias. |
| Explainability | Every match exposes matched/missing skills. The semantic component is **not** individually explainable. |

---

## 6. Verification performed

| Verified | How |
|---|---|
| Full test suite passes | `python -m pytest -q` |
| `app.py` and all 10 pages execute | Streamlit `AppTest`, which runs the script for real |
| App serves over HTTP | `GET /` and `GET /_stcore/health` → 200 from an isolated build context |
| Startup check detects failures | Injected missing key, unwritable DB path, missing dataset, disabled XSRF/CORS |
| Startup check leaks no secrets | Sentinel-value test asserting key/password never appear in output |
| Legacy DB migration on populated data | Built a realistic pre-Phase-4 database with populated child tables and FKs; verified all rows preserved and `PRAGMA foreign_key_check` clean |
| Required data present | `data/skills.json` asserted present and valid; asserted not gitignored |
| Docker build context is self-sufficient | Context replicated and the app booted from it; no secrets included |
| No secrets tracked | Repo-wide scan; `.env` gitignored; `.env.example` is placeholders only |

**A note on HTTP 200:** a `GET /` returning 200 only proves the Streamlit
*server* started. The app script does not execute until a websocket session
connects, so a crash on the first line of `app.py` still returns 200. The
`AppTest` smoke tests exist because of this and do execute the script.

---

## 7. Not verified

What has actually been executed is recorded, with results, in
[DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md) — including a 15/15 clean
deployment walkthrough and 7/7 forced failure modes. What remains unproven:

| Item | Why |
|---|---|
| A second, independent `docker build` | The first build succeeded and was verified (see §2), but the build host then exhausted its disk and its daemon could not be restarted without a machine-level reset. Re-verify on your own machine. |
| A clean-venv `pip install` on a **host** | The host's DNS cannot resolve `download-r2.pytorch.org`. The same install **does** succeed in the Docker dependency stage, so the manifest is proven; only this host's bare-metal path is not. See §2. |
| A real container restart / redeploy cycle | Restart persistence was verified in-process and against a mounted volume, but not driven through a platform's actual restart. Do it once by hand. |
| Multiple concurrent users | Load and concurrency behaviour untested. SQLite is the limiting factor. |
| Reverse-proxy configurations | The nginx snippet is conventional but untested against this app. |
| Browser-level user journeys | No Playwright/Selenium coverage. `AppTest` runs every page server-side, and the 15 steps run in-process, but no real browser clicked through the UI. |
| macOS / Linux hosts | The Docker image is Linux and ran there. A **bare-metal** host was not tested; the development host is Windows. |
| Large resumes at the 10 MB limit | Parsing time and memory at the cap are unmeasured. |
| Upgrade from every historical schema | Verified against the pre-Phase-4 schema. Much older databases are untested. |
| The documented PaaS path (section E.4) | Reasoned about, not executed. No Streamlit Community Cloud / Heroku / Render instance was deployed. |

---

## 8. Suggested next steps

Roughly in priority order:

1. **Commit the working tree.** Most of the application is still untracked.
   Nothing is deployable from a fresh clone until this is done — this remains the
   single largest blocker to a real deployment.
2. Re-verify the Docker build on a machine with free disk, then confirm a real
   restart preserves the volume.
3. Put a TLS-terminating, rate-limiting, access-controlled reverse proxy in front.
4. Add a lockfile so installs are reproducible.
5. Add CI (test on push, build and scan the image).
6. Add Alembic so schema changes are versioned and reversible.
7. Replace the `AppTest` smoke tests with a small Playwright suite for the
   critical user journeys.
8. Consider SSO instead of open registration.
