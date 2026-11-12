# CI/CD

How TalentIQ is verified automatically before anything is deployed, and how to
reproduce that verification on your own machine.

- **Workflow:** [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)
- **Triggers:** every `push` (any branch) and every `pull_request`, plus manual
  `workflow_dispatch`.
- **Runner:** `ubuntu-latest`, Python 3.11, job timeout 45 minutes.
- **Permissions:** `contents: read` — the workflow cannot write to the repository.

---

## 1. Purpose

The pipeline answers one question before a human decides to deploy: **does this
exact commit still work?** It is deliberately boring and self-contained:

- no GitHub secret is required, so a fork's pull request is verifiable by
  someone who has no access to this repository's configuration;
- no database server, container registry, or cloud account is touched;
- no Groq request is made, so CI costs nothing and cannot be rate-limited;
- the embedding model is not downloaded, because a missing model is a startup
  *warning* by design (the app falls back to skill + TF-IDF matching) and must
  not fail a build.

---

## 2. What each step does

| # | Step | Why it exists |
|---|---|---|
| 1 | `actions/checkout` | Fresh clone. Also proves the app works from tracked files only. |
| 2 | `actions/setup-python` (3.11, pip cache) | The version the pins and suite are verified against. Cache is keyed on both requirement files. |
| 3 | Install **CPU-only** torch | `torch` arrives transitively via `sentence-transformers`. The default Linux wheel drags in the CUDA runtime (GBs, and a real risk of exhausting the runner's disk). Production installs the CPU build (see `Dockerfile`), so CI validates the dependency set that actually ships. |
| 4 | Install `requirements.txt` + `requirements-dev.txt` | Runtime pins plus `pytest`. |
| 5 | `pip check` | Fails on an unsatisfiable or mismatched pinned set. |
| 6 | `tools/ci_secret_scan.py` | `.gitignore` is a convention that `git add -f` overrides. This is the last chance to catch a committed `.env`, `*.db`, virtualenv, or API key. |
| 7 | `python -m compileall` | Catches a syntax error in **any** shipped file, including the Streamlit pages that pytest never imports. |
| 8 | `tools/ci_import_check.py` | Catches a renamed symbol, a bad relative path, or a dependency missing from `requirements.txt` — on a machine whose only source of truth is the requirement files. |
| 9 | `python -m pytest` | The behavioural suite. |
| 10 | `git status --porcelain` | Fails if the test run modified a tracked file or created a non-ignored one. A test that writes into the checkout is a bug, not a convenience. |

Every step is required. There is deliberately **no** `continue-on-error`, no
`|| true`, and no `set +e`. A red pipeline must always mean something is wrong.

---

## 3. Running the same checks locally

From the repository root, with the virtualenv active:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt

python -m pip check
python tools/ci_secret_scan.py
python -m compileall -q app.py core modules services ui pages tools tests
python tools/ci_import_check.py
python -m pytest -ra --tb=short
```

The single command most people want is just:

```bash
python -m pytest -q
```

### Reproducing CI's environment exactly

CI sets a fixed, secret-free configuration. To match it locally — useful when a
test passes on your machine but fails in CI — run in a shell with exactly these
set and no `.env` present:

```bash
export GROQ_API_KEY=ci-placeholder-not-a-real-key
export GROQ_MODEL=openai/gpt-oss-120b
export ENVIRONMENT=testing          # NOT "test" - see below
export DEBUG=false
export MAX_RESUME_SIZE_MB=10
export LOG_LEVEL=WARNING
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
```

> **`ENVIRONMENT` must be `development`, `testing`, or `production`.**
> `core.config` raises `ConfigurationError` for anything else, and because that
> happens at import time a wrong value fails *every* import in the job. This was
> a real bug in the first draft of the workflow, caught by running the commands
> locally before pushing.

`GROQ_API_KEY` is a placeholder and is never used to make a request — see
section 5.

---

## 4. Diagnosing a red pipeline

Read the failing step name first; they fail for different reasons.

| Failing step | Usual cause | What to do |
|---|---|---|
| `Install runtime and test dependencies` | A pin no longer resolves, or a new transitive version conflicts. | Run `pip install -r requirements.txt` locally. Do not "fix" it by loosening a pin without understanding why. |
| `Verify pinned dependencies are consistent` | Installed versions disagree with each other. | `pip check` prints the conflicting pair. Usually a resolver artefact after a partial cache hit. |
| `Check tracked files for committed secrets and local state` | A `.env`, `*.db`, `venv/`, `__pycache__/`, or an API key is tracked. | The message gives `path:line` and the rule name, **never the secret value**. Remove the file, and if the rule is a false positive add it to `ALLOWED_FINDINGS` in `tools/ci_secret_scan.py` with a reason. |
| `Compile every shipped Python file` | A syntax error. | The output names the file and line. `compileall` does not import, so a missing dependency will *not* fail here — that is the next step's job. |
| `Validate imports` | `ImportError`, `ModuleNotFoundError`, a circular import, a renamed symbol, or an invalid `ENVIRONMENT`. | The script prints every failure at once rather than stopping at the first, with the file, module, and exception type. |
| `Run the test suite` | A behavioural regression. | Scroll to the `FAILURES` section. `-ra` prints a summary line for every skipped test, so a silently skipped test is visible. |
| `Confirm the test run left no artifacts` | A test wrote into the checkout. | `git status --porcelain` output is echoed. The fix belongs in the test: use `tmp_path`. |

### Triage locally first

```bash
python -m pytest -q                      # fastest signal
python -m pytest tests/test_auth.py -v   # one area
python -m pytest -k "resume_version"     # one behaviour
python -m pytest --lf                    # only what failed last time
```

---

## 5. What is intentionally mocked

The suite makes **zero** network requests. This is enforced, not merely intended.

`tests/conftest.py` installs an **autouse** fixture that, before every test:

1. deletes `GROQ_API_KEY` from the environment,
2. sets `settings.groq_api_key` to `None`,
3. resets the cached `modules.ai_analysis.client` to `None`.

A test that needs AI installs a stub with `monkeypatch.setattr(...)`. The
invariant is itself asserted by
`tests/test_ai_resilience.py::test_the_suite_itself_has_no_api_key`, so if the
fixture is ever weakened, the suite fails loudly instead of quietly making
billed calls.

What is simulated, and where:

| Behaviour | Simulated as | Covered by |
|---|---|---|
| Successful Groq reply | stub client returning fixed content | `test_ai_resilience.py` |
| Malformed / non-JSON reply | stub returning prose, fences, trailing commas | `test_ai_resilience.py` |
| API failure | stub raising `Exception` | `test_ai_resilience.py` |
| Rate limit / timeout | stub raising `TimeoutError` | `test_ai_resilience.py` |
| Missing credential | `client = None` and no key | `test_ai_resilience.py` |
| Deterministic fallback | `resume_coach._chat` patched to always fail | `test_ai_resilience.py` |
| Live Groq end-to-end | **not in CI** | `tools/verify_deployment.py` step 15 (manual, needs a real key) |

Not mocked, and therefore genuinely exercised: the database (real SQLite files
in a temp directory), authentication and password hashing, RBAC, file
validation and PDF parsing, PDF report generation, and every Streamlit page via
Streamlit's own `AppTest` runner.

---

## 6. Database and file isolation

- Every test gets its own SQLite file under pytest's `tmp_path`, via the
  `fresh_db` fixture. `conftest.py` additionally sets a module-level temp
  fallback *at import time*, so a test module that imports the database layer
  during collection cannot resolve the repository's real path.
- `tests/test_ci_isolation.py` asserts the active database is never
  `data/talentiq.db` and never inside the repository's `data/` directory.
- Upload and scratch directories resolve through `tmp_path`; the repository's
  `data/tmp` and `data/uploads` are git-ignored and are not used for assertions.
- The workflow's final step fails the job if the test run leaves the working
  tree dirty.

Verified: after a full local run, `data/talentiq.db` is byte-for-byte unchanged
(same MD5 before and after).

---

## 7. Required environment variables

CI needs **no secrets**. The variables below are set to safe, non-secret values
in the workflow itself.

| Variable | CI value | Notes |
|---|---|---|
| `GROQ_API_KEY` | `ci-placeholder-not-a-real-key` | Deleted by the conftest fixture before every test; never used for a request. |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Only read, never called. |
| `ENVIRONMENT` | `testing` | Must be `development`/`testing`/`production`. |
| `DEBUG` | `false` | |
| `MAX_RESUME_SIZE_MB` | `10` | |
| `LOG_LEVEL` | `WARNING` | Keeps the log readable. |
| `TALENTIQ_DB_PATH` | `${{ runner.temp }}/talentiq-ci/ci.db` | Outside the checkout. |
| `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE` | `1` | Refuses model downloads. |

**Real deployment** configuration is a different matter and is documented in
[DEPLOYMENT.md](DEPLOYMENT.md) — notably that a real `GROQ_API_KEY` is required
in production and must come from the hosting platform's secret store, never
from a committed file.

---

## 8. Enabling the merge gate

CI is advisory until you make it required. In **Settings → Branches → Branch
protection rules** for `main`:

- enable **Require status checks to pass before merging**;
- select the `Tests, imports and quality checks` job.

Until that is on, a red pipeline can still be merged by someone who does not
notice it.

---

## 9. Known limitations

- **One Python version (3.11).** The pins are verified on 3.11 only. A matrix
  would be better, but each extra version multiplies a ~5 minute job and can
  surface unrelated wheel-availability noise.
- **No code coverage gate.** Deliberate: introducing a threshold now would fail
  the build on unrelated grounds. `pytest-cov` is not installed.
- **No linter or formatter.** The repository had none, and adding one would mean
  reformatting working code. `compileall` plus the import check cover the
  failure modes that actually bite a Streamlit app.
- **No deployment step.** This pipeline verifies; it does not deploy. Deployment
  stays a deliberate human action, which is why the merge gate matters.
- **The semantic model is not exercised in CI.** `services/semantic_matcher.py`
  imports `sentence-transformers` lazily inside a function, so a missing model
  only downgrades a startup check to a warning. Semantic *scoring* is therefore
  covered by unit tests around the fallback, not by a live model. Provisioning
  the model in the image is covered by `tools/verify_deployment.py`.
- **No E2E browser tests.** Streamlit's `AppTest` exercises the real page code,
  but not WebSocket transport, CORS, or reverse-proxy behaviour.
- **GitHub-hosted runners only.** No self-hosted runner, matrix, or ARM build.
