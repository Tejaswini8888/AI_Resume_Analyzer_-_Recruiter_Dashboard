"""Centralized SQLite persistence for TalentIQ.

Responsibilities
----------------
- Resolve the SQLite file location from the environment (``TALENTIQ_DB_PATH``),
  defaulting to ``data/talentiq.db`` under the project root.
- Create the data directory automatically.
- Provide one engine, one session factory, and ``session_scope()`` so pages and
  services never construct SQLAlchemy machinery themselves.
- Initialize the schema idempotently on first use: tables are created only if
  missing — the database is never dropped or recreated on startup.

Privacy
-------
The path is never surfaced in user-facing errors. Pages treat persistence as
best-effort and hide details behind clean messages.
"""

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from core.config import ConfigurationError, settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_db_path = None
_engine = None
_session_local = None
_initialized = False


def _database_url_scheme(url: str) -> str:
    """The scheme of a SQLAlchemy-style URL, lowercased, without the ``://``.

    Only the scheme is ever returned. The remainder of a database URL routinely
    embeds a username and password, so it must never be logged or included in
    an error message.
    """
    return url.split("://", 1)[0].strip().lower() if "://" in url else ""


def _resolve_db_path() -> Path:
    """Resolve the SQLite file location from centralized configuration.

    Priority:
    1. ``DATABASE_URL`` — only a ``sqlite:///`` file URL is supported.
    2. ``TALENTIQ_DB_PATH`` — process-level override. It is re-read from the
       environment on every call (via :meth:`Settings.reload`) so test fixtures
       can point each test at its own throwaway database.
    3. ``<project root>/data/talentiq.db`` (safe, predictable default).

    Why a non-SQLite ``DATABASE_URL`` raises instead of falling back
    ---------------------------------------------------------------
    Nearly every PaaS (Heroku, Render, Railway, Fly, Azure, and most managed
    Postgres add-ons) injects ``DATABASE_URL`` automatically as soon as a
    database is attached. Silently ignoring a non-SQLite value and quietly
    falling back to a local file is the worst possible failure: the operator
    believes they configured persistent storage, the app looks healthy, and
    every candidate and user is destroyed on the next deploy because the real
    database was never written to.

    So this refuses. The message names the scheme only — never the URL, which
    would leak embedded credentials into logs.
    """
    settings.reload()  # re-read ENV before reading any setting (fixes order bug)
    url = (settings.database_url or "").strip()
    if url:
        if url.startswith("sqlite:///"):
            raw = url[len("sqlite:///"):]
            if raw and raw != ":memory:":
                path = Path(raw)
                return path if path.is_absolute() else PROJECT_ROOT / path
        elif url.startswith("sqlite://"):
            # sqlite://:memory: and similar in-memory forms are not a file.
            pass
        else:
            scheme = _database_url_scheme(url) or "unknown"
            raise ConfigurationError(
                f"DATABASE_URL is set to a {scheme!r} URL, but TalentIQ only "
                "supports SQLite. External databases (PostgreSQL, MySQL) are "
                "NOT supported and the application will not silently fall back "
                "to a local file, because that would appear to persist data "
                "while losing it on every deploy. Either unset DATABASE_URL, "
                "or set it to a SQLite file URL such as "
                "'sqlite:///data/talentiq.db' and mount that path on "
                "persistent storage."
            )

    configured = (settings.database_file or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else PROJECT_ROOT / path
    return PROJECT_ROOT / "data" / "talentiq.db"


def get_db_path() -> Path:
    """Absolute path of the active SQLite database file."""
    global _db_path
    if _db_path is None:
        _db_path = _resolve_db_path()
    return _db_path


def get_engine():
    """Create (once per process) the SQLAlchemy engine for SQLite.

    Foreign keys are enforced per-connection (``PRAGMA foreign_keys=ON``) so
    dangling references can never be persisted on freshly-created databases.
    """
    global _engine
    if _engine is None:
        database_path = get_db_path()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{database_path.as_posix()}"
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            future=True,
        )

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        _engine = engine
    return _engine


def get_session_local():
    """Create (once per process) the SQLAlchemy session factory."""
    global _session_local
    if _session_local is None:
        _session_local = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
    return _session_local


def init_db() -> None:
    """Create any missing tables. Idempotent — never drops existing data.

    ``create_all`` only creates missing TABLES; it cannot add columns to an
    existing table. A small additive migration step (`_migrate_latest_schema`)
    brings pre-versioning/pre-auth databases up to the current schema (adds
    ``resume_analyses.resume_version_id``, the Phase-4 ownership/actor columns)
    and backfills legacy analyses onto a ``ResumeVersion`` 1 so historical
    analysis stays reachable.
    """
    global _initialized
    if _initialized:
        return
    from core import models  # imported lazily to avoid any import cycle

    engine = get_engine()
    models.Base.metadata.create_all(bind=engine)
    # Set the flag before any nested session work (the backfill opens sessions)
    # so `session_scope()` never re-enters `init_db()`. If the migration fails
    # the flag is cleared again so a later call retries; the app keeps its
    # pre-existing schema either way.
    _initialized = True
    try:
        _migrate_latest_schema(engine)
    except Exception:
        _initialized = False
        raise


def _schema_table_names(engine) -> set[str]:
    from sqlalchemy import inspect

    return set(inspect(engine).get_table_names())


def _schema_column_names(engine, table: str) -> set[str]:
    from sqlalchemy import inspect

    return {column["name"] for column in inspect(engine).get_columns(table)}


def _migrate_latest_schema(engine) -> None:
    """Additive, idempotent migrations for databases created before this code.

    Fresh databases already carry the full schema (``create_all`` applies the
    models), so every step below is gated on the table/column actually missing.
    Columns are added with a plain ``INTEGER`` so SQLite's ``ALTER TABLE ADD
    COLUMN`` is always legal (the model uses the same type for fresh installs).
    """
    from sqlalchemy import text

    tables = _schema_table_names(engine)

    # Phase 4: authentication + ownership/actor columns. The ``users`` table is
    # brand new, so ``create_all`` handles it; only the four legacy tables need
    # additive columns here. These MUST be added before any ORM query below
    # (the ORM selects *all* model columns, including the new ones).
    _ensure_columns(engine, "candidates", {"created_by_user_id": "INTEGER"})
    _ensure_columns(engine, "job_descriptions", {"created_by_user_id": "INTEGER"})
    _ensure_columns(
        engine,
        "candidate_status_history",
        {"actor_user_id": "INTEGER"},
    )
    _ensure_columns(engine, "candidate_activity", {"actor_user_id": "INTEGER"})

    # Phase 3: resume versioning for legacy databases.
    if "resume_analyses" in tables:
        _ensure_columns(engine, "resume_analyses", {"resume_version_id": "INTEGER"})
        _backfill_resume_versions()

    # Must run AFTER the column above exists and BEFORE indexes are created, so
    # the rebuilt table is copied and then indexed exactly like a fresh one.
    _widen_job_description_uniqueness(engine)

    _ensure_indexes(engine)


def _widen_job_description_uniqueness(engine) -> None:
    """Replace a legacy GLOBAL ``UNIQUE(content_hash)`` with owner-scoped.

    Pre-Phase-4 databases declared ``content_hash`` as ``UNIQUE``, which SQLite
    backs with an anonymous ``sqlite_autoindex_*`` entry. SQLite cannot drop an
    index — only a full table rebuild can remove a table-level constraint — so
    the table is rebuilt from the CURRENT model DDL and every row copied across.
    Without this, a second recruiter pasting the same job description text hits
    an IntegrityError instead of getting their own row.

    No-op for fresh databases (their table is already built from the model) and
    idempotent, so it is safe to call on every startup.
    """
    with engine.connect() as conn:
        if not _unique_indexes_on(conn, "job_descriptions", ("content_hash",)):
            return
        _rebuild_table_from_model(
            conn, "job_descriptions", "_job_descriptions_upgrade"
        )


def _rebuild_table_from_model(conn, tablename: str, tempname: str) -> None:
    """Copy ``tablename`` into a fresh table built from the model DDL.

    Uses the CURRENT SQLAlchemy model as the schema source of truth (rendered
    via ``CreateTable``) so the migration can never drift from the schema the
    application expects. Only columns present in both sides are copied.

    This is a SQLite table rebuild, which has three sharp edges that are all
    handled here:

    1. ``PRAGMA foreign_keys`` is a no-op inside a transaction, and
       ``job_descriptions`` is referenced by child tables, so the final
       ``DROP TABLE`` fails with "FOREIGN KEY constraint failed" unless the
       pragma is set *before* any transaction opens.
    2. The pragma is per-connection, so the DDL must run on the SAME connection
       that set it — opening a second ``engine.begin()`` silently hands back a
       different connection and the pragma is lost.
    3. ``ALTER TABLE ... RENAME`` rewrites references in other tables unless
       ``legacy_alter_table`` is on. Here the rename is temp -> final name and
       no table references the temp name, so the rewrite is unnecessary; the
       pragma keeps SQLite from attempting (and failing) reference resolution.

    The original table is left untouched if any step fails, and both pragmas
    are restored before returning, so a failed migration cannot leave the
    connection (and therefore the pooled session) with FK enforcement off.
    """
    from sqlalchemy import inspect, text
    from sqlalchemy.schema import CreateTable

    from core import models

    model_table = models.Base.metadata.tables[tablename]

    existing = {c["name"] for c in inspect(conn).get_columns(tablename)}
    shared = [c.name for c in model_table.columns if c.name in existing]
    if not shared:
        return

    columns_sql = ", ".join(shared)
    # Render the model's DDL under a temporary name.
    original_name = model_table.name
    try:
        model_table.name = tempname
        create_sql = str(CreateTable(model_table).compile(conn))
    finally:
        model_table.name = original_name

    # Autocommit so the pragmas take effect; both are per-connection state.
    # ``inspect().get_columns()`` above opened an implicit transaction via
    # autobegin, and SQLAlchemy refuses to change the isolation level while one
    # is active, so close it out first.
    conn.commit()
    conn = conn.execution_options(isolation_level="AUTOCOMMIT")
    conn.execute(text("PRAGMA foreign_keys=OFF"))
    conn.execute(text("PRAGMA legacy_alter_table=ON"))
    try:
        conn.execute(text("BEGIN"))
        try:
            conn.execute(text(f"DROP TABLE IF EXISTS {tempname}"))
            conn.execute(text(create_sql))
            conn.execute(
                text(
                    f"INSERT INTO {tempname} ({columns_sql}) "
                    f"SELECT {columns_sql} FROM {tablename}"
                )
            )
            conn.execute(text(f"DROP TABLE {tablename}"))
            conn.execute(text(f"ALTER TABLE {tempname} RENAME TO {tablename}"))
            conn.execute(text("COMMIT"))
        except Exception:
            # Leave the original table and its rows in place.
            conn.execute(text("ROLLBACK"))
            raise
    finally:
        conn.commit()  # pragmas above autobegan; close it before restoring
        conn.execute(text("PRAGMA legacy_alter_table=OFF"))
        conn.execute(text("PRAGMA foreign_keys=ON"))
        # Give the connection back to the pool in its normal transactional mode
        # so the next user of it does not inherit AUTOCOMMIT. pysqlite only
        # accepts SERIALIZABLE / READ UNCOMMITTED, and SERIALIZABLE is its
        # default, so that is what "normal" means here.
        conn.commit()
        conn.execution_options(isolation_level="SERIALIZABLE")


def _ensure_indexes(engine) -> None:
    """Bring legacy-table indexes in line with the current models.

    ``create_all`` never touches existing tables, so databases created by older
    code miss the newer lookup indexes and may still carry now-removed UNIQUE
    indexes (duplicate resume hashes must be legal across owners; a job
    description's uniqueness is now owner-scoped, not global). Drops those
    legacy unique indexes and (re)creates the plain lookup indexes idempotently.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_candidates_resume_hash"))
        for name in _unique_indexes_on(conn, "candidates", ("resume_hash",)):
            conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
        for _name, ddl in _INDEX_DEFINITIONS:
            conn.execute(text(ddl))
        # Owner-scoped JD uniqueness. Created explicitly (rather than only by
        # the model) so a table that was rebuilt by the migration above — and
        # whose indexes were dropped with it — ends up identical to a fresh
        # install. Legacy rows share created_by_user_id = NULL, which SQLite
        # treats as distinct per row, so this never conflicts with old data.
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_descriptions_content_owner "
                "ON job_descriptions (content_hash, created_by_user_id)"
            )
        )


def _unique_indexes_on(conn, table: str, columns: tuple) -> list:
    """Names of UNIQUE indexes on exactly ``columns`` for ``table``.

    Used to find a stale uniqueness rule left behind by an older schema. Names
    are never hardcoded because SQLite's implicit ones (``sqlite_autoindex_*``)
    are positional and unstable, so they are read from ``PRAGMA index_list``.
    """
    from sqlalchemy import text

    try:
        indexes = conn.execute(text(f"PRAGMA index_list('{table}')")).fetchall()
    except Exception:  # pragma: no cover - table may not exist yet
        return []

    names = []
    for row in indexes:
        name, unique = row[1], bool(row[2])
        if not unique:
            continue
        indexed = [
            info[2] for info in conn.execute(text(f"PRAGMA index_info('{name}')")).fetchall()
        ]
        if tuple(indexed) == columns:
            names.append(name)
    return names


_INDEX_DEFINITIONS = (
    (
        "ix_candidates_resume_hash",
        "CREATE INDEX IF NOT EXISTS ix_candidates_resume_hash "
        "ON candidates (resume_hash)",
    ),
    (
        "ix_candidates_name",
        "CREATE INDEX IF NOT EXISTS ix_candidates_name ON candidates (name)",
    ),
    (
        "ix_candidates_email",
        "CREATE INDEX IF NOT EXISTS ix_candidates_email ON candidates (email)",
    ),
    (
        "ix_candidates_status",
        "CREATE INDEX IF NOT EXISTS ix_candidates_status ON candidates (status)",
    ),
    (
        "ix_candidates_created_at",
        "CREATE INDEX IF NOT EXISTS ix_candidates_created_at ON candidates (created_at)",
    ),
    (
        "ix_candidates_updated_at",
        "CREATE INDEX IF NOT EXISTS ix_candidates_updated_at ON candidates (updated_at)",
    ),
    (
        "ix_job_descriptions_created_at",
        "CREATE INDEX IF NOT EXISTS ix_job_descriptions_created_at "
        "ON job_descriptions (created_at)",
    ),
)


def _ensure_columns(engine, table: str, columns: dict[str, str]) -> None:
    """Add any missing columns to an existing table via SQLite ``ALTER TABLE``."""
    from sqlalchemy import text

    existing = _schema_column_names(engine, table)
    with engine.begin() as conn:
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def _backfill_resume_versions() -> None:
    """Attach every legacy analysis (``resume_version_id`` NULL) to a version.

    Analysis rows are matched to a ``ResumeVersion`` keyed on
    ``(candidate_id, file_hash)`` so:

    - a candidate whose resume was ALREADY migrated (partially-versioned DB)
      reuses the existing version instead of creating a duplicate, and
    - every unversioned analysis is linked — regardless of whether the
      candidate already has versions — so no history is left dangling.

    The version is keyed off the candidate's stored file hash/filename so no
    file bytes are read and no identity data is duplicated.
    """
    from sqlalchemy import and_, func

    from core.models import Candidate, ResumeAnalysis, ResumeVersion

    with session_scope() as session:
        rows = (
            session.query(ResumeAnalysis, Candidate, ResumeVersion)
            .join(Candidate, Candidate.id == ResumeAnalysis.candidate_id)
            .outerjoin(
                ResumeVersion,
                and_(
                    ResumeVersion.candidate_id == Candidate.id,
                    ResumeVersion.file_hash == func.coalesce(Candidate.resume_hash, ""),
                ),
            )
            .filter(ResumeAnalysis.resume_version_id.is_(None))
            .all()
        )
        for analysis, candidate, version in rows:
            if version is None:
                version_number = (
                    int(
                        session.query(func.max(ResumeVersion.version_number))
                        .filter(ResumeVersion.candidate_id == candidate.id)
                        .scalar()
                        or 0
                    )
                    + 1
                )
                version = ResumeVersion(
                    candidate_id=candidate.id,
                    version_number=version_number,
                    filename=candidate.resume_filename,
                    file_hash=candidate.resume_hash or "",
                )
                session.add(version)
                session.flush()
            analysis.resume_version_id = version.id


@contextmanager
def session_scope():
    """Transactional session; commits on success, rolls back on error.

    Calling ``init_db()`` here means any service/page that uses the database
    triggers safe initialization on demand.
    """
    init_db()
    session = get_session_local()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()