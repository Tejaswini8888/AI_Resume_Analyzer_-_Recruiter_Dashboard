# Deploying TalentIQ

The operational guide. Sections **A–I** run in order: each one assumes the ones
before it are done.

For what this application deliberately does **not** do, read
[PRODUCTION_LIMITATIONS.md](PRODUCTION_LIMITATIONS.md) before assuming a
feature is missing by accident. For a tick-off list, use
[DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md).

The single most important thing in this document is **section D**: TalentIQ
stores everything in one SQLite file, and if that file is not on persistent
storage, a redeploy deletes every user and candidate.

---

## A. What you need

| Requirement | Version | Notes |
|---|---|---|
| Python | **3.11+** | 3.11 is the floor: the startup check parses `config.toml` with `tomllib` from the standard library. Verified on 3.11.9. |
| Disk | **3 GB free** | The built image is 2.84 GB. Almost all of it is `torch` plus the sentence-transformers model. |
| RAM | 1 GB minimum, 2 GB recommended | The embedding model loads into memory on first JD match. |
| Outbound HTTPS | To `api.groq.com` | Only for AI features. Parsing, ATS scoring, skill extraction, JD matching and PDF reports work fully offline. |
| Writable disk at runtime | A persistent `data/` | SQLite database, temp files, uploads. See section D. |

No GPU, no Node, no build toolchain, no database server.

---

## B. Get the code and install dependencies

```bash
git clone <your-repo-url> talentiq
cd talentiq

python -m venv venv
# Linux/macOS
source venv/bin/activate
# Windows PowerShell
.\venv\Scripts\Activate.ps1

pip install --upgrade pip

# CPU-only torch FIRST, or pip resolves the ~2 GB CUDA build from PyPI.
pip install torch --index-url https://download.pytorch.org/whl/cpu

pip install -r requirements.txt
```

> **Why torch is separate.** `sentence-transformers` depends on `torch`, but the
> default PyPI wheel bundles CUDA libraries this application never uses. The CPU
> build is the biggest single lever on install time and disk. Skip it if the host
> already has a working CPU `torch`.

`requirements.txt` is fully pinned to the exact versions this was verified
against:

```
streamlit==1.60.0      pandas==3.0.5        sentence-transformers==5.2.3
plotly==6.9.0          SQLAlchemy==2.0.54   scikit-learn==1.9.0
groq==1.5.0            pdfplumber==0.11.10  reportlab==5.0.0
                       python-dotenv==1.2.2
```

### B.1 Pre-download the embedding model

```bash
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"
```

Do this **at build/deploy time, not at first use.** The application loads the
model with `local_files_only=True` and never downloads it at runtime, so a host
that skipped this step starts and looks healthy while silently scoring
candidates on the fallback path. `python -m core.startup_check` reports this as
a `WARN`.

The `Dockerfile` does this for you in the build stage. The container therefore
needs **no outbound network** to score resumes.

### B.2 Confirm the required data files are present

```bash
python -c "import json,pathlib; print(len(json.loads(pathlib.Path('data/skills.json').read_text())))"
```

`data/skills.json` is **required**, not optional. It is small and it *is* tracked
by git, but because so much of this tree was previously untracked, confirm it
survived your clone:

| File | Required | If missing |
|---|---|---|
| `data/skills.json` | **yes** | `git checkout -- data/skills.json` |
| `data/skills.csv` | yes | Startup check `WARN` |
| `data/role_skills.json` | yes | Startup check `WARN` |

---

## C. Secrets and environment variables

**Never put a secret in a committed file.** Two supported ways to supply them:

- **Container / VM / any platform with an environment UI** — set the variables in
  the platform's secret or environment store. This is the recommended path.
- **Local development only** — copy `.env.example` to `.env` and edit it. `.env`
  is gitignored and `.dockerignore` excludes it, so it cannot reach an image.

A `.env` file is a **development convenience, not a deployment mechanism.** Most
hosting platforms inject environment variables directly; a `.env` file in the
image would bake a secret into a layer where it stays readable forever.

### C.1 Required

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | — | **Required for AI features.** Without it the AI summary, AI analysis, interview questions and AI Resume Coach are unavailable. Deterministic ATS scoring, skill extraction, JD matching and PDF reports all still work — but the container's startup check **refuses to start** without it, so set it even if you do not intend to use AI yet. |

### C.2 Strongly recommended

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | Set to `production` in a real deployment. |
| `DEBUG` | `false` | Keep `false` in production. |
| `TALENTIQ_ADMIN_EMAIL` | — | Bootstrap identity for the first `ADMIN`. See C.4. |
| `TALENTIQ_ADMIN_PASSWORD` | — | Bootstrap password for the first `ADMIN`. See C.4. |

### C.3 Operational

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | *(unset)* | SQLite only. See section D. A non-SQLite value **fails startup on purpose**. |
| `TALENTIQ_DB_PATH` | `data/talentiq.db` | SQLite file location. Relative paths resolve against the project root, never the process working directory. |
| `MAX_RESUME_SIZE_MB` | `10` | Application-level upload limit. Keep in sync with Streamlit — see the note below. |
| `TEMP_DIRECTORY` | `data/tmp` | Scratch space for PDF parsing. Created on demand. |
| `UPLOAD_DIRECTORY` | `data/uploads` | Reserved; resume bytes are processed **in memory**, not stored here. |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Model id. An unknown id degrades AI features to the deterministic fallback rather than crashing. |
| `LOG_LEVEL` | `INFO` | Startup logging verbosity. |

> **Keep the upload limits in sync.** If you raise `MAX_RESUME_SIZE_MB`, also
> raise `maxUploadSize` in `.streamlit/config.toml` (or set
> `STREAMLIT_SERVER_MAX_UPLOAD_SIZE`). Streamlit buffers the upload *before*
> Python sees it, so that setting — not the app — is what bounds memory. If
> Streamlit's limit is *lower* than the app's, users get an opaque Streamlit
> error for files the app would have accepted. The startup check **fails** if
> the two disagree in that direction.

### C.4 First admin

There is no default account and no auto-provisioning. The `ADMIN` role is granted
to whichever account **registers** with the configured bootstrap credentials:

1. Set `TALENTIQ_ADMIN_EMAIL` and `TALENTIQ_ADMIN_PASSWORD` in the environment.
2. Start the app and register that exact email and password on the login page.
3. Remove `TALENTIQ_ADMIN_PASSWORD` from the running environment once the
   account exists.

Skip step 2 and every account that can be created is a `RECRUITER`, which makes
the admin audit-log page unreachable. The startup check warns about this.

> **Registration is open.** Anyone who can reach the app can register a recruiter
> account. Put the app behind an identity-aware proxy or SSO before exposing it
> to an untrusted network. See PRODUCTION_LIMITATIONS.md.

### C.5 What the logs will never show

Startup output is safe to paste into a ticket. The following are excluded by
construction and asserted by the test suite:

- `GROQ_API_KEY` and `TALENTIQ_ADMIN_PASSWORD` values.
- A `DATABASE_URL` containing embedded credentials — only the **scheme** is
  reported (e.g. `'postgresql'`), never the user, password, host or port.
- Exception *messages* that could contain a path or DSN. Only the exception
  **type** is logged.
- Absolute filesystem paths in anything a browser can see.

---

## D. Persistent storage — the one thing that matters

Everything durable is **one SQLite file** at `TALENTIQ_DB_PATH` (default
`data/talentiq.db`): users, sessions' role assignments, candidates, resume
versions, analyses, audit logs.

### D.1 Only SQLite is supported, and a non-SQLite URL fails on purpose

`DATABASE_URL` accepts **only** `sqlite:///`. Many Streamlit platforms
(Streamlit Community Cloud, and most PaaS add-ons) inject
`DATABASE_URL=postgresql://...` automatically as soon as a Postgres add-on is
attached. Silently ignoring that and quietly using a local file is the worst
possible outcome: the operator believes they configured persistent storage, the
app looks healthy, and every candidate is destroyed on the next deploy because
the real database was never written to.

So TalentIQ **refuses to start** and says:

```
DATABASE_URL is set to a 'postgresql' URL, but TalentIQ only supports SQLite.
```

Either unset `DATABASE_URL` and use a mounted volume, or point it at a SQLite
file URL such as `sqlite:///data/talentiq.db` on persistent storage.

### D.2 Verify your platform actually has persistent storage

Ask the platform directly: **does a container restart or redeploy preserve files
written outside the source tree?** If the answer is no, TalentIQ is not safe to
deploy there — the data will not survive. On such a platform use Docker on a VM,
or accept that the instance is disposable.

### D.3 Mount it

```bash
# Docker
docker volume create talentiq-data
# ... then
docker run -d --name talentiq -v talentiq-data:/app/data ...
```

Confirm after first boot that the file is on the volume and owned by the
non-root user:

```bash
docker exec talentiq ls -l /app/data/talentiq.db   # owner uid 10001 (talentiq)
```

### D.4 Backups

Never copy a live SQLite file. Use SQLite's own tooling:

```bash
sqlite3 data/talentiq.db ".backup '/backups/talentiq-$(date +%F).db'"
```

### D.5 One replica only

SQLite files cannot be shared over a network filesystem and are not safe for
concurrent writers. Run **one** instance. Scaling horizontally needs a real
database, which TalentIQ does not support today (see PRODUCTION_LIMITATIONS.md).

---

## E. Deploy

### E.1 Docker (recommended)

```bash
docker build -t talentiq .

docker run -d --name talentiq \
  -p 8501:8501 \
  -v talentiq-data:/app/data \
  -e GROQ_API_KEY \
  -e GROQ_MODEL=openai/gpt-oss-120b \
  -e ENVIRONMENT=production \
  -e DEBUG=false \
  -e TALENTIQ_ADMIN_EMAIL \
  -e TALENTIQ_ADMIN_PASSWORD \
  --restart unless-stopped \
  talentiq
```

Pass secrets with `-e NAME` (Docker reads the value from your shell) or
`--env-file`; **never** `ENV` in the Dockerfile and never `-e KEY=literal` on a
shared machine, where it lands in your shell history and `docker inspect`.

What the image does:

| Property | Value |
|---|---|
| Runtime user | `talentiq`, uid/gid `10001` (non-root) |
| Only writable path | `/app/data` |
| Port | `8501` |
| Healthcheck | `GET /_stcore/health` |
| `.env` inside the image | **none** (`.dockerignore` excludes it) |
| Image size | ~2.84 GB (CPU-only torch + baked model) |
| `CMD` | `init_db()` → `python -m core.startup_check` → `streamlit run app.py` |

`init_db()` runs **before** the server binds, so a fresh volume has its schema
created before the first browser can connect — no "no such table" first-load
error. The startup check then gates serving: a misconfigured container exits
non-zero instead of coming up half-broken.

To rebuild without re-downloading torch and the model:

```bash
docker build --cache-from talentiq -t talentiq .
```

To skip the model prefetch and accept the scoring fallback:

```bash
docker build --build-arg PREFETCH_MODEL=false -t talentiq .
```

### E.2 systemd on a VM

```ini
[Unit]
Description=TalentIQ
After=network-online.target

[Service]
Type=simple
User=talentiq
WorkingDirectory=/opt/talentiq
EnvironmentFile=/etc/talentiq/talentiq.env
ExecStartPre=/opt/talentiq/venv/bin/python -m core.startup_check
ExecStart=/opt/talentiq/venv/bin/streamlit run app.py \
  --server.port=8501 --server.address=127.0.0.1
Restart=on-failure

# The process only needs to write to its data directory.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/talentiq/data

[Install]
WantedBy=multi-user.target
```

`ExecStartPre` is the fail-closed behaviour: systemd will not start a
misconfigured instance. `/etc/talentiq/talentiq.env` holds the section C
variables and should be `chmod 600`, owned by root. Put a reverse proxy in front
and bind Streamlit to `127.0.0.1` so it is never directly exposed.

### E.3 Local development

```bash
streamlit run app.py
```

`runOnSave` is `false` in the committed config, so opt back in with:

```bash
STREAMLIT_SERVER_RUN_ON_SAVE=true streamlit run app.py
```

### E.4 A platform's own PaaS (Streamlit Community Cloud, Heroku, Render…)

Requirements, all of which must hold:

1. A **persistent, writable** disk (section D). If the platform has none, do not
   deploy — the data will not survive.
2. Python 3.11+ and the ability to run `pip install -r requirements.txt`.
3. A `PORT` environment variable, passed through to Streamlit:
   `streamlit run app.py --server.port=$PORT --server.address=0.0.0.0`.
4. The section C variables set in the platform's environment/secret store.
5. Enough build time and disk for torch — Community Cloud's default limits are
   the usual blocker. Use a Docker image on a VM if it does not fit.

The app's own `startup_check` is what tells you whether these were met; run it
from the platform's shell or logs.

---

## F. Verify the deployment

**Always do this before serving traffic.**

### F.1 Startup check

```bash
python -m core.startup_check
python -m core.startup_check --json    # machine-readable
```

No network calls, no secrets in the output, non-zero exit on failure — so it
works as a container `HEALTHCHECK` or a CI gate. A correctly configured
production host prints:

```
TalentIQ startup check
============================================
[PASS] environment             ENVIRONMENT=production
[PASS] debug_flag              DEBUG=false
[PASS] groq_api_key            GROQ_API_KEY is set (value not shown)
[PASS] groq_model              GROQ_MODEL=openai/gpt-oss-120b
[PASS] database_path           SQLite file will live at /app/data/talentiq.db
[PASS] temp_directory          data/tmp -> /app/data/tmp (writable)
[PASS] upload_directory        data/uploads -> /app/data/uploads (writable)
[PASS] skill_dataset           55 canonical skills available
[PASS] semantic_model          sentence-transformers/all-MiniLM-L6-v2 loaded locally
[PASS] admin_bootstrap         Admin bootstrap credentials are configured; register that email on the login page to create the first ADMIN
[PASS] streamlit_upload_limit  maxUploadSize=10 MB >= MAX_RESUME_SIZE_MB=10 MB
[PASS] data_role_skills_json   present
[PASS] data_skills_csv         present
============================================
13 checks: 0 failed, 0 warning(s)
Result: ready to serve.
```

> Loading the embedding model prints a `BertModel LOAD REPORT` table to stderr.
> The `embeddings.position_ids | UNEXPECTED` line is expected and harmless for
> this model. It is noise, not a failure.

### F.2 The application must actually execute

`GET /` returning HTTP 200 only proves the Streamlit *server* started — the app
script does not run until a websocket session connects, so a crash on the first
line of `app.py` still returns 200. Therefore:

- `python -m pytest -q` runs `app.py` **and all 10 pages** through Streamlit's
  `AppTest`, so a script that cannot execute fails the suite. The suite is
  hermetic: it stubs the Groq client, uses a temporary database, and makes no
  paid API call.
- In a browser, actually open the app, log in, and upload one PDF. A health
  endpoint cannot tell you the UI works.

### F.3 Walk the fifteen deployment steps

The full list, with expected results, is in
[DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md). It covers clean install,
external configuration, database initialization, startup, access, login, upload,
analysis, results, PDF report, recruiter dashboard, multiple candidates,
restart persistence, audit logs, and the AI Resume Coach.

---

## G. Operate it

### G.1 Health checks

| Check | Command | Proves |
|---|---|---|
| Liveness | `GET /_stcore/health` | The server is serving. Nothing else. |
| Configuration | `python -m core.startup_check` | Keys, data files, model, storage and Streamlit config are correct. |
| Deep | `python -m pytest -q` | Every page actually executes. |

Liveness will not notice a missing key or an absent model. That is why the
container runs the configuration check *before* starting rather than only after.

### G.2 Reverse proxy

Streamlit needs WebSocket upgrade. With nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:8501;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 600s;
}
```

**Do not** "fix" CORS errors by setting `enableCORS = false` in
`.streamlit/config.toml`. `enableCORS` and `enableXsrfProtection` are pinned
`true` deliberately and the startup check fails if either is disabled. Configure
the proxy instead.

### G.3 Migrations

`init_db()` runs on every startup. It only creates missing tables and adds
missing columns — it never drops data. A pre-Phase-4 database is upgraded in
place, including the `job_descriptions` uniqueness widening, which rebuilds that
one table to replace a legacy global `UNIQUE(content_hash)` with an owner-scoped
unique index. The rebuild runs with foreign keys disabled inside a single
transaction, then restores enforcement; `PRAGMA foreign_key_check` is clean
afterwards.

Back up before the first deploy over an old database (section D.4).

### G.4 File handling

Resume bytes are parsed **in memory** and never written to `UPLOAD_DIRECTORY`.
Temporary files are created under `TEMP_DIRECTORY` and removed on a best-effort
basis. PDF reports are built entirely in memory, so there is no report file to
clean up. Nothing accumulates in the container filesystem between requests, but
`TEMP_DIRECTORY` still needs to be writable and on the persistent volume if you
want crash-time leftovers to be inspectable.

---

## H. Failure modes and what the operator sees

Every one of these was exercised deliberately. "User sees" means the message a
browser can render.

| Failure | Detected by | User sees | Verified |
|---|---|---|---|
| `GROQ_API_KEY` missing | startup check, exit 1 | Nothing — the container does not start | yes |
| `DATABASE_URL` is PostgreSQL/MySQL | startup check + `ConfigurationError` | Nothing — refuses to start, names the scheme only | yes |
| Database path's parent is a file | startup check | Nothing — refuses to start | yes |
| Database directory not writable | startup check | Nothing — refuses to start | yes |
| `data/skills.json` missing | startup check | Nothing — refuses to start | yes |
| XSRF protection or CORS disabled | startup check | Nothing — refuses to start | yes |
| Embedding model not cached | startup check `WARN` | Scores silently use the documented fallback | yes |
| Scratch directory missing | none | Nothing — created on demand | yes |
| Scratch directory read-only | startup check | Nothing — refuses to start | yes |
| Non-PDF / wrong extension / empty file | upload validation | A specific, non-technical reason | yes |
| File over `MAX_RESUME_SIZE_MB` | upload validation | The limit in MB | yes |
| PDF with no extractable text | parser | "The PDF did not contain readable text (scanned or empty file)." | yes |
| Groq down, rate-limited or erroring | coach, bounded retries | Deterministic evidence-based recommendations, `ai_generated=False`, no transport text | yes |
| Unknown `GROQ_MODEL` | AI call | Deterministic fallback | yes |
| Database locked or corrupt | query layer | Error state; the error propagates rather than reading as "no data yet" | yes |
| Unauthorized access to another's data | RBAC | Empty list / not found — never the other user's rows | yes |

The rule: **a broken dependency degrades a feature, a broken configuration stops
the deploy.** Nothing silently pretends to work.

---

## I. Troubleshooting and rollback

| Symptom | Cause | Fix |
|---|---|---|
| Startup check fails on `groq_api_key` | Key absent or blank | Set it in the platform's environment store |
| `DATABASE_URL is set to a 'postgresql' URL` | Platform injected a Postgres URL | Unset it and mount a persistent volume (section D) |
| `Database path's parent is not a directory` | `TALENTIQ_DB_PATH` points inside a file | Point it at a directory |
| `Database directory is not writable` | Permissions or a missing mount | Check the volume mount and ownership (uid 10001) |
| `skill_dataset` FAIL | `data/skills.json` missing | `git checkout -- data/skills.json` |
| `semantic_model` WARN | Model not in the local cache | Pre-download it (B.1) or bake it into the image |
| `streamlit_upload_limit` FAIL | `maxUploadSize` < `MAX_RESUME_SIZE_MB` | Raise `maxUploadSize` in `.streamlit/config.toml` |
| Opaque Streamlit error on a valid PDF | `maxUploadSize` is *lower* than the app limit | Same as above |
| `FOREIGN KEY constraint failed` at startup | A table was modified by hand | Restore from backup; the migration is non-destructive by design |
| Every skill-based score is 0 | Skill dataset empty or unloaded | Check the `skill_dataset` startup check |
| Users/candidates vanish after an update | No volume mounted | Mount `-v talentiq-data:/app/data` (section D.3) |
| Scores differ between environments | One has the model, the other does not | Compare the `semantic_model` check on both |
| `no such table` on first load | Schema not initialised | The image runs `init_db()` first; on a bare host run `python -c "from core.db import init_db; init_db()"` |
| AI features return fallback text | Missing key, bad model id, or Groq outage | Check `GROQ_API_KEY` and `GROQ_MODEL`; fallback is expected behaviour, not a bug |

### Rollback

1. Stop serving traffic.
2. Restore the SQLite backup from section D.4.
3. Redeploy the previous image tag.
4. Re-run `python -m core.startup_check`, then the checklist's login and
   upload steps.

Migrations only ever add, so rolling the *code* back does not require rolling the
*schema* back.

---

> Startup log lines record the **exception type only**, never the message. A
> database path or DSN can appear inside an exception message, so it is
> deliberately not logged. Reproduce locally against a copy of the database to
> see the detail.
