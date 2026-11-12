# TalentIQ — Security Checklist

Phase 4 · Step 6 production-readiness audit.

**Status legend:** `[x]` verified in code **and** covered by a test or an
executed check · `[ ]` not implemented / not verified · `[-]` deliberately out
of scope for this phase · `[!]` implemented, but carries a documented risk

Every `[x]` below names the file that implements it so the claim can be
re-checked. Nothing is marked complete on the basis of intent alone.

---

## 1. Authentication

- [x] **Passwords hashed with a memory-hard KDF.** `hashlib.scrypt`
      (`n=2**14, r=8, p=1, dklen=32`) with a 16-byte `secrets.token_bytes` salt
      per password, stored self-describing as `scrypt$n$r$p$salt$hash`.
      — `services/auth_service.py:63-96`
- [x] **Password verification is constant-time.** `hmac.compare_digest`.
      — `services/auth_service.py:99-117`
- [x] **No plaintext or reversible password storage anywhere.**
      — `services/auth_service.py`, asserted by `tests/test_auth.py`
- [x] **Password policy enforced at registration** (length + character
      requirements) with a validation failure path that never echoes the
      password. — `services/auth_service.py:125`
- [x] **Login errors are deliberately ambiguous** — a bad email and a bad
      password return the same result, so the endpoint is not a user-enumeration
      oracle. — `services/auth_service.py:248-250`
- [x] **Sessions store only the user id.** `st.session_state` holds no
      credentials or role claims. — `services/auth_service.py:22-24`
- [x] **Sessions are revalidated against the database on every request**,
      including an `is_active` check, so a deactivated account loses access
      immediately without any session-invalidation bookkeeping.
      — `services/auth_service.py:26-27`
- [x] **Sign-in attempts (success and failure) are audit-logged** in the same
      transaction as the authentication itself, recording no password material.
      — `services/auth_service.py:253-287`
- [ ] **No rate limiting or account lockout on sign-in.** Failed attempts are
      logged but not throttled, so passwords remain brute-forceable. *This is
      the highest-value remaining security gap.*
- [ ] **No MFA or SSO.** *Deliberately out of scope for this phase
      (`[-]` by requirement); credentials are local-only.*
- [!] **Admin bootstrap uses a plaintext environment password**
      (`TALENTIQ_ADMIN_PASSWORD`) compared at registration. Documented in
      `.env.example`; unset it in production unless bootstrapping is intended.

## 2. Authorization & data isolation

- [x] **Roles are a closed enum** (`RECRUITER`, `ADMIN`) enforced by a database
      `CHECK` constraint, never free text. — `core/models.py:60-63`
- [x] **Role checks are centralized** (`can_access`, `owner_scope`,
      `require_authenticated`) rather than re-implemented per page.
      — `services/auth_service.py`, `ui/auth_gate.py`
- [x] **Ownership is enforced at the service layer**, not in the UI, for
      candidates, job descriptions, analyses, resume versions, activity and
      analytics. — `services/*.py`
- [x] **Recruiter A cannot read or mutate recruiter B's data** — verified in
      both directions for detail views, status changes, activity, resume
      versions, workspace and analytics.
      — `tests/test_ownership_isolation.py`
- [x] **A recruiter cannot retro-link analyses to another recruiter's job
      description**; job-description reuse is owner-scoped and ownership is
      never transferred on reuse. — `services/recruitment_service.py:279-336`
- [x] **Job-description uniqueness is owner-scoped**
      (`(content_hash, created_by_user_id)`), so identical text pasted by two
      recruiters yields two private rows instead of colliding or leaking.
      — `core/models.py:250-283`, `tests/test_ownership_isolation.py`
- [x] **Legacy `NULL`-owner records are explicitly documented as a shared
      pool** and the behaviour is asserted by tests, so it is a recorded
      decision rather than an accident. — `tests/test_ownership_isolation.py`
- [x] **ADMIN role can read across owners**, verified by test.
      — `tests/test_ownership_isolation.py`
- [ ] **No per-field/column-level authorization.** Access is enforced per
      record, not per attribute.

## 3. File upload handling

- [x] **Uploads are validated server-side**, never trusting the client-side
      `type=["pdf"]` filter. — `services/file_security_service.py:114`
- [x] **Extension allowlist** (`.pdf` by default, configurable).
      — `services/file_security_service.py:90-107`
- [x] **Size limit enforced** from centralized configuration
      (`MAX_RESUME_SIZE_MB`, default 10 MB) with an in-memory read.
      — `services/file_security_service.py:39,114`
- [x] **Empty uploads rejected.**
- [x] **PDF magic bytes verified** (`%PDF-`), so a renamed HTML/text file is
      rejected. — `services/file_security_service.py:110`
- [x] **Filenames are sanitized and used only as display labels**, never as
      filesystem paths: separators and control characters stripped, truncated
      to 255 characters, empty names replaced with a safe default.
      — `services/file_security_service.py:168-191`
- [x] **SHA-256 content hashing** for integrity and dedup.
      — `services/file_security_service.py:193-198`
- [x] **Temporary files are created securely and cleaned up**, and upload/temp
      directories are never committed to git. — `services/file_security_service.py:212-238`
- [x] **Rejected uploads are audit-logged** with a controlled vocabulary, so
      repeated probing is visible. — `services/file_security_service.py:280-315`
- [x] **PDF extraction is bounded** (page/size caps) to resist decompression
      and memory-exhaustion payloads. — `modules/parser.py`
- [ ] **No antivirus / content scanning** of uploaded documents.
- [ ] **No per-user upload quota** — the size limit is global.

## 4. Secret handling

- [x] **No secret is committed to git.** `.env` is ignored; verified with
      `git ls-files` and a repository-wide scan for `gsk_`/`sk-` patterns
      (no matches).
- [x] **`.env.example` contains placeholders only** — no real values.
- [x] **All configuration funnels through one typed `Settings` object**; no
      module calls `os.getenv` directly, so there is a single place to audit.
      — `core/config.py`
- [x] **Secrets are never logged.** `Settings.safe_summary()` and a secret-safe
      `__repr__` exclude the API key, admin password and raw database URL.
      — `core/config.py:176-213`
- [x] **`redact_secrets()` scrubs known secret values and
      `KEY=value`/`Authorization: Bearer` patterns** as defense in depth.
      — `core/logging_config.py:44-70`
- [x] **Startup logging reports missing settings by name only**, never value.
      — `core/logging_config.py:73-89`
- [x] **The AI layer never returns raw exception text or credentials** to the
      user; failures are logged by exception *type* and surfaced as a fixed
      message. — `modules/ai_analysis.py:56-70`
- [x] **The test suite cannot leak or require secrets.** `core.config` calls
      `load_dotenv()` at import time, so an autouse fixture clears
      `GROQ_API_KEY` and the cached client for every test. The suite therefore
      never depends on a local `.env` and never issues a live billed API call.
      — `tests/conftest.py`
- [x] **Streamlit local secrets are ignored** (`.streamlit/secrets.toml`)
      while the theme config is tracked. — `.gitignore`
- [ ] **No secrets manager integration.** Credentials come from environment
      variables only.
- [ ] **No key rotation support.** Rotating `GROQ_API_KEY` requires a restart.

## 5. Audit logging

- [x] **Append-only activity and audit tables** — no update/delete path exists
      in the service layer. — `core/models.py`
- [x] **Controlled event vocabulary** (fixed constants, not free text).
      — `services/audit_service.py`
- [x] **The audit service rejects secret-looking data**, so a caller cannot
      accidentally log a token. — `services/audit_service.py`,
      `tests/test_audit_log.py`
- [x] **Actor attribution recorded** on status changes, analyses and activity.
      — `services/recruitment_service.py`, `tests/test_ownership_isolation.py`
- [x] **Ownership is never transferred by a reuse/update path.**
      — `services/recruitment_service.py:279-336`

## 6. Output handling

- [x] **HTML is escaped** before rendering user-controlled text in the UI and
      in generated reports. — `modules/pdf_report.py`, `html.escape` in pages
- [x] **CSV exports are neutralized** against spreadsheet formula injection.
      — `services/file_security_service.py:241`
- [x] **PDF reports are generated in memory**, never written to a
      world-readable path. — `modules/pdf_report.py`
- [x] **Report-generation failures are audit-logged** rather than surfacing
      internals. — `services/file_security_service.py:317`

## 7. Data protection at rest

- [ ] **The SQLite database is not encrypted at rest.** Resume text, candidate
      PII and password hashes are stored in a plain file. Requires OS-level
      disk encryption or SQLCipher.
- [ ] **No PII retention or right-to-erasure workflow.** Candidate records are
      not automatically purged.
- [ ] **No backup/encryption procedure** for the database file.
- [x] **Foreign keys are enforced on every connection**
      (`PRAGMA foreign_keys=ON`), so dangling references cannot be written.
      — `core/db.py:86-91`
- [x] **Password hashes are not reversible** even if the database leaks.

## 8. Transport

- [ ] **No TLS termination in the application.** Streamlit is served over
      plain HTTP; TLS must be provided by a reverse proxy in front of it.
- [x] **Usage telemetry is disabled** (`gatherUsageStats = false`), so the app
      does not phone home. — `.streamlit/config.toml`

---

## Summary

**Verified and enforced in code:** password hashing and constant-time
verification, session revalidation, role-based access control, per-record data
ownership and isolation, server-side upload validation, secret-safe logging,
audit logging, and output escaping.

**Top gaps, in priority order:**

1. No rate limiting or lockout on sign-in.
2. No encryption at rest for the SQLite database (resume text + PII).
3. No TLS in-app (must be terminated upstream).
4. No PII retention/erasure policy.
5. No MFA/SSO (explicitly deferred).
