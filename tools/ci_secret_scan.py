"""Tracked-file hygiene guard for CI (Step 3, requirement 9).

``.gitignore`` is only a convention: ``git add -f`` overrides it, a bad merge
can reintroduce an ignored path, and a hurried ``git add .`` before a demo can
commit a real ``.env``. CI is the last place that can catch that before the
secret reaches a remote, so this check runs on every push and pull request.

Two classes of problem:

1. **Local state that must never be tracked** - a real ``.env``, a SQLite
   database, a virtualenv, generated reports, caches.
2. **Secret-shaped strings in tracked text files** - Groq keys, GitHub tokens,
   AWS keys, private keys, and database URLs with inline credentials.

Output discipline
-----------------
Only ``path:line`` and the rule name are ever printed. A secret's *value* is
never echoed, because CI logs are readable by anyone with repository access and
are retained for weeks. The message tells you which file and line to open.

Matching is line-scoped and excludes whitespace from credential runs, so a
prose sentence that happens to contain ``mysql://`` cannot span lines and raise
a phantom finding. The repository currently contains two deliberate
documentation fixtures that look like inline-credential URLs; they are listed in
``ALLOWED_FINDINGS`` with a reason, so removing them shows up as a diff rather
than silently becoming an allowlist entry.

Exit code 0 = clean, 1 = at least one problem.
"""

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Executed directly by CI, where pytest.ini's ``pythonpath = .`` does not apply.
# This script currently needs only the standard library, but the bootstrap keeps
# the two CI helpers interchangeable and stops a future import from failing only
# in CI.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Tracked paths that must never exist. Matched case-insensitively against the
# repo-relative POSIX path.
FORBIDDEN_PATH_PATTERNS = (
    (r"(^|/)\.env$", "real .env committed; ship .env.example instead"),
    (
        r"(?i)(^|/)\.env\.(?!example|sample|template|dist|defaults?$)",
        "a .env variant is committed (only .env.example and friends belong in git)",
    ),
    (r"\.db$", "SQLite database committed"),
    (r"\.(db-wal|db-shm|db-journal|sqlite|sqlite3)$", "SQLite sidecar file committed"),
    (r"(^|/)venv/", "virtualenv committed"),
    (r"(^|/)\.venv/", "virtualenv committed"),
    (r"__pycache__/", "bytecode cache committed"),
    (r"\.pyc$", "compiled bytecode committed"),
    (r"(^|/)\.pytest_cache/", "pytest cache committed"),
    (r"^tools/verify_.*\.check$", "stray bytecode artifact from a local compile check"),
)

# Secret shapes. Kept deliberately narrow to limit false positives.
SECRET_PATTERNS = (
    ("groq-api-key", r"gsk_[A-Za-z0-9]{20,}"),
    ("github-token", r"gh[pousr]_[A-Za-z0-9]{30,}"),
    ("aws-access-key", r"AKIA[0-9A-Z]{16}"),
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("openai-style-key", r"sk-[A-Za-z0-9]{32,}"),
    ("slack-token", r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    (
        "inline-credential-db-url",
        r"(?i)\b(?:postgres|postgresql|mysql|mongodb|redis|amqp)://"
        r"[^\s:@/]+:[^\s:@/]+@",
    ),
)

TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".toml", ".ini", ".cfg", ".yml", ".yaml",
    ".json", ".example", ".sh", ".ps1", ".env", ".sql", ".html", ".css",
}

# Documented, intentional matches. path -> (line-substring, why it is safe).
ALLOWED_FINDINGS = {
    "docs/DEPLOYMENT_CHECKLIST.md": (
        "postgresql://",
        "example of a non-SQLite DATABASE_URL that the app must refuse; the "
        "host does not resolve and the password is a placeholder",
    ),
    "tools/verify_failure_modes.py": (
        "postgresql://",
        "test fixture proving a non-SQLite DATABASE_URL is rejected; the host "
        "does not resolve and the password is a placeholder",
    ),
}


def _tracked_files():
    """Repo-relative paths of tracked files, from git itself."""
    result = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "ERROR: could not list tracked files from git. This check must run "
            "inside a git checkout (CI clones the repository first).",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return [p for p in result.stdout.split("\0") if p]


def main():
    tracked = _tracked_files()
    findings = []

    for rel in tracked:
        posix = rel.replace("\\", "/")

        for pattern, reason in FORBIDDEN_PATH_PATTERNS:
            if re.search(pattern, posix, re.IGNORECASE):
                findings.append(f"{posix}: {reason}")

        if Path(posix).suffix.lower() not in TEXT_SUFFIXES:
            continue
        absolute = PROJECT_ROOT / posix
        if not absolute.is_file():
            continue
        try:
            text = absolute.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append(f"{posix}: could not read ({type(exc).__name__})")
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            for rule, pattern in SECRET_PATTERNS:
                if not re.search(pattern, line):
                    continue
                allowed = ALLOWED_FINDINGS.get(posix)
                if allowed and allowed[0] in line:
                    continue
                findings.append(
                    f"{posix}:{lineno}: possible {rule} "
                    f"(value not shown - open this line and remove it)"
                )

    if findings:
        print("FAIL: tracked-file hygiene problems:\n", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        print(
            f"\n{len(findings)} problem(s) in {len(tracked)} tracked file(s).\n"
            "If a finding is a deliberate placeholder, add it to "
            "ALLOWED_FINDINGS in tools/ci_secret_scan.py with a reason.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(tracked)} tracked files clean (no local state, no secrets).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
