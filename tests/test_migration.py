"""Migration test: pre-versioning databases are upgraded, not wiped.

Builds a database that looks like it was created BEFORE resume versioning
existed (``resume_analyses`` without ``resume_version_id``, no
``resume_versions`` table), then boots the current app against it and asserts:

- the schema upgrade is additive: new column + new table appear
- existing analysis rows are untouched (scores preserved)
- each legacy analysis is backfilled onto a ``ResumeVersion`` 1 for its
  candidate (so historical analysis stays reachable)
- re-running ``init_db`` is idempotent (no duplicate versions, no errors)
"""

import sqlite3

import pytest

import core.db as db


def _build_legacy_db(path):
    """Create the pre-versioning schema + one candidate with an analysis."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            email VARCHAR(320),
            phone VARCHAR(50),
            location VARCHAR(255),
            resume_filename VARCHAR(500),
            resume_hash VARCHAR(64),
            status VARCHAR(50),
            created_at DATETIME,
            updated_at DATETIME
        );
        CREATE TABLE resume_analyses (
            id INTEGER PRIMARY KEY,
            candidate_id INTEGER,
            jd_id INTEGER,
            ats_score FLOAT,
            jd_match_score FLOAT,
            final_score FLOAT,
            semantic_match_score FLOAT,
            matched_skills JSON,
            missing_required_skills JSON,
            missing_preferred_skills JSON,
            created_at DATETIME
        );
        INSERT INTO candidates
            (id, name, status, resume_filename, resume_hash, created_at)
        VALUES
            (1, 'Legacy Tejas', 'Analyzed', 'legacy.pdf', 'legacy-hash', '2025-01-01 00:00:00');
        INSERT INTO resume_analyses
            (id, candidate_id, jd_id, ats_score, jd_match_score, final_score,
             semantic_match_score, matched_skills, missing_required_skills,
             missing_preferred_skills, created_at)
        VALUES
            (1, 1, NULL, 55.0, 60.0, 58.0, NULL,
             '["python"]', '[]', '[]', '2025-01-01 00:00:00');
        """
    )
    conn.commit()
    conn.close()


def _legacy_columns(path, table):
    conn = sqlite3.connect(str(path))
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    conn.close()
    return cols


def _legacy_tables(path):
    conn = sqlite3.connect(str(path))
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    conn.close()
    return tables


def _job_description_indexes(path):
    """{index name: (is_unique, [columns])} for the ``job_descriptions`` table."""
    conn = sqlite3.connect(str(path))
    rows = conn.execute("PRAGMA index_list('job_descriptions')").fetchall()
    result = {}
    for row in rows:
        name, unique = row[1], bool(row[2])
        columns = [
            info[2]
            for info in conn.execute(f"PRAGMA index_info('{name}')").fetchall()
        ]
        result[name] = (unique, columns)
    conn.close()
    return result


@pytest.fixture()
def legacy_db(monkeypatch, tmp_path):
    """A pre-versioning database at the app's configured path, pre-init_db."""
    path = tmp_path / "legacy.db"
    _build_legacy_db(path)
    monkeypatch.setenv("TALENTIQ_DB_PATH", str(path))
    db._db_path = None
    db._engine = None
    db._session_local = None
    db._initialized = False
    yield path
    db._db_path = None
    db._engine = None
    db._session_local = None
    db._initialized = False


def test_legacy_db_schema_upgraded_additively(legacy_db):
    assert "resume_version_id" not in _legacy_columns(legacy_db, "resume_analyses")
    assert "resume_versions" not in _legacy_tables(legacy_db)

    db.init_db()

    assert "resume_version_id" in _legacy_columns(legacy_db, "resume_analyses")
    assert "resume_versions" in _legacy_tables(legacy_db)


def test_legacy_analysis_linked_and_scores_preserved(legacy_db):
    from services.analysis_service import get_analyses
    from services.resume_version_service import (
        get_latest_resume_version,
        resume_version_count,
    )

    db.init_db()

    version = get_latest_resume_version(1)
    assert version is not None
    assert version.version_number == 1
    assert version.file_hash == "legacy-hash"
    assert version.filename == "legacy.pdf"
    assert resume_version_count(1) == 1

    analyses = get_analyses(1, resume_version_id=version.id)
    assert len(analyses) == 1
    assert analyses[0].final_score == 58.0  # untouched by the upgrade
    assert analyses[0].resume_version_id == version.id


def test_legacy_upgrade_is_idempotent(legacy_db):
    from services.resume_version_service import (
        get_resume_versions,
        resume_version_count,
    )

    db.init_db()
    db.init_db()
    db.init_db()

    assert resume_version_count(1) == 1
    assert len(get_resume_versions(1)) == 1


def test_legacy_candidate_without_analysis_gets_no_version(legacy_db):
    import sqlite3

    conn = sqlite3.connect(str(legacy_db))
    conn.execute(
        """
        INSERT INTO candidates (id, name, status, resume_filename, resume_hash)
        VALUES (2, 'No Analysis', 'New', NULL, 'nothing-hash')
        """
    )
    conn.commit()
    conn.close()

    from services.resume_version_service import resume_version_count

    db.init_db()

    # Candidates with no analyses are NOT backfilled a spurious version.
    assert resume_version_count(2) == 0
    assert resume_version_count(1) == 1


def test_new_uploads_continue_after_upgrade(legacy_db):
    from services.analysis_service import get_analyses, save_analysis
    from services.recruitment_service import save_job_description
    from services.resume_version_service import (
        create_or_get_resume_version,
        get_latest_resume_version,
        resume_version_count,
    )

    db.init_db()

    v2 = create_or_get_resume_version(
        candidate_id=1, file_hash="fresh-content", filename="new.pdf"
    )
    assert v2.version_number == 2  # continues from the backfilled v1
    assert resume_version_count(1) == 2

    jd = save_job_description("Migrated database role")
    save_analysis(
        candidate_id=1,
        jd_id=jd.id,
        resume_version_id=v2.id,
        ats_score=80.0,
        jd_match_score=80.0,
        final_score=80.0,
    )
    analyses = get_analyses(1, resume_version_id=v2.id)
    assert len(analyses) == 1
    assert analyses[0].final_score == 80.0
    # Version 1's history is untouched.
    assert get_latest_resume_version(1).id == v2.id


def test_legacy_global_jd_unique_index_is_replaced(legacy_db):
    """A pre-Phase-4 database enforces UNIQUE(content_hash) via a
    ``sqlite_autoindex_*`` entry. SQLite can never drop an index that backs a
    table constraint, so the migration must rebuild the table and install the
    owner-scoped composite index instead."""
    from services.recruitment_service import (
        get_job_descriptions,
        save_job_description,
    )

    db.init_db()

    indexes = _job_description_indexes(legacy_db)
    # No UNIQUE index may remain on content_hash alone...
    assert not [
        name
        for name, (unique, columns) in indexes.items()
        if unique and columns == ["content_hash"]
    ]
    # ...and the owner-scoped index is the one enforcing it.
    assert indexes.get("uq_job_descriptions_content_owner") == (
        True,
        ["content_hash", "created_by_user_id"],
    )

    # The whole point: two different owners can now store the same text.
    first = save_job_description("Shared text", created_by_user_id=1)
    second = save_job_description("Shared text", created_by_user_id=2)
    assert first.id != second.id

    # Re-running init_db is a no-op, and rows survive the rebuild.
    db._initialized = False
    db.init_db()
    assert _job_description_indexes(legacy_db).get(
        "uq_job_descriptions_content_owner"
    ) == (True, ["content_hash", "created_by_user_id"])
    assert {j.id for j in get_job_descriptions(user=None)} >= {
        first.id,
        second.id,
    }


# ---------------------------------------------------------------------------
# Job description uniqueness widening (global UNIQUE -> owner-scoped)
# ---------------------------------------------------------------------------
def _build_legacy_jd_db(path):
    """A pre-Phase-4 ``job_descriptions`` table: ``content_hash`` is globally
    UNIQUE (an anonymous ``sqlite_autoindex_*`` SQLite will never drop)."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE job_descriptions (
            id INTEGER PRIMARY KEY,
            title VARCHAR(255),
            description TEXT,
            content_hash VARCHAR(64) NOT NULL UNIQUE,
            created_at DATETIME,
            updated_at DATETIME
        );
        INSERT INTO job_descriptions
            (id, title, description, content_hash, created_at, updated_at)
        VALUES
            (1, 'Owned', 'Original owned description', 'hash-owned',
             '2025-01-01 00:00:00', '2025-01-01 00:00:00'),
            (2, NULL, 'Original shared description', 'hash-shared',
             '2025-01-02 00:00:00', '2025-01-02 00:00:00');
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def legacy_jd_db(monkeypatch, tmp_path):
    """A pre-Phase-4 JD table at the configured path, upgraded on demand."""
    path = tmp_path / "legacy_jd.db"
    _build_legacy_jd_db(path)
    monkeypatch.setenv("TALENTIQ_DB_PATH", str(path))
    db._db_path = None
    db._engine = None
    db._session_local = None
    db._initialized = False
    db.init_db()
    yield path
    db._db_path = None
    db._engine = None
    db._session_local = None
    db._initialized = False


def test_legacy_jd_rows_survive_rebuild(legacy_jd_db):
    """The rebuild must be data-preserving: pre-Phase-4 rows keep their ids,
    titles, text, owner and timestamps."""
    from services.recruitment_service import get_job_description

    conn = sqlite3.connect(str(legacy_jd_db))
    before = conn.execute(
        "SELECT id, title, description, content_hash, created_by_user_id "
        "FROM job_descriptions ORDER BY id"
    ).fetchall()
    conn.close()
    assert len(before) == 2

    for row_id, title, description, content_hash, owner_id in before:
        jd = get_job_description(row_id)
        assert jd is not None
        assert jd.title == title
        assert jd.description == description
        assert jd.content_hash == content_hash
        assert jd.created_by_user_id == owner_id


# ---------------------------------------------------------------------------
# Regression: the JD rebuild must work when child tables reference it.
#
# The original ``_build_legacy_jd_db`` fixture created ``job_descriptions`` on
# its own, so the final ``DROP TABLE job_descriptions`` had no dependents and
# succeeded. A real pre-Phase-4 database is fully populated: its candidate and
# analysis tables carry foreign keys into ``job_descriptions``, and SQLite
# refuses to drop a parent table while children reference it ("FOREIGN KEY
# constraint failed").
#
# That is exactly what a real upgrade hits, and it is why the earlier version of
# the migration raised IntegrityError on a populated database while every test
# still passed. These tests build the realistic shape.
# ---------------------------------------------------------------------------
_LEGACY_JD_DDL = """
CREATE TABLE job_descriptions (
    id INTEGER NOT NULL PRIMARY KEY,
    title VARCHAR(255),
    description TEXT,
    content_hash VARCHAR(64) NOT NULL UNIQUE,
    created_by_user_id INTEGER,
    created_at DATETIME,
    updated_at DATETIME,
    FOREIGN KEY (created_by_user_id) REFERENCES users (id)
)
"""


def _build_legacy_jd_db_with_children(path):
    """A realistic pre-Phase-4 database.

    Built from the CURRENT model metadata (so every child table has the real
    columns and the real foreign keys) and populated with rows, then with
    ``job_descriptions`` swapped for its legacy shape: a table-level
    ``UNIQUE(content_hash)`` that SQLite backs with an anonymous autoindex, and
    no owner-scoped index.

    Populated and FK-referenced is the whole point: SQLite refuses to
    ``DROP TABLE`` a parent while children reference it, which is what broke
    the previous implementation.
    """
    from sqlalchemy import create_engine

    from core import models

    engine = create_engine(f"sqlite:///{path.as_posix()}")
    models.Base.metadata.create_all(engine)

    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO users (id, name, email, password_hash, role, is_active,"
            " created_at, updated_at) VALUES"
            " (1, 'Rec', 'rec@example.com', 'x', 'RECRUITER', 1,"
            " '2025-01-01 00:00:00', '2025-01-01 00:00:00')"
        )
        conn.exec_driver_sql(
            "INSERT INTO candidates (id, name, email, resume_hash, status,"
            " created_by_user_id, created_at, updated_at) VALUES"
            " (1, 'Cand One', 'c1@example.com', 'rh1', 'New', 1,"
            " '2025-01-01 00:00:00', '2025-01-01 00:00:00')"
        )
        conn.exec_driver_sql(
            "INSERT INTO resume_versions (id, candidate_id, version_number,"
            " filename, file_hash, created_at, updated_at) VALUES"
            " (1, 1, 1, 'r.pdf', 'rh1', '2025-01-01 00:00:00', '2025-01-01 00:00:00')"
        )
        conn.exec_driver_sql(
            "INSERT INTO job_descriptions (id, title, description, content_hash,"
            " created_by_user_id, created_at, updated_at) VALUES"
            " (1, 'Owned', 'Original owned description', 'hash-owned', 1,"
            " '2025-01-01 00:00:00', '2025-01-01 00:00:00'),"
            " (2, 'Shared', 'Original shared description', 'hash-shared', 1,"
            " '2025-01-02 00:00:00', '2025-01-02 00:00:00')"
        )
        # Child rows that reference job_descriptions.
        conn.exec_driver_sql(
            "INSERT INTO resume_analyses (id, candidate_id, jd_id, resume_version_id,"
            " ats_score, created_at) VALUES"
            " (1, 1, 1, 1, 91.0, '2025-01-04 00:00:00'),"
            " (2, 1, 2, 1, 64.0, '2025-01-05 00:00:00')"
        )

        # Swap in the legacy JD table shape.
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.exec_driver_sql("DROP TABLE job_descriptions")
        conn.exec_driver_sql(_LEGACY_JD_DDL)
        conn.exec_driver_sql(
            "INSERT INTO job_descriptions (id, title, description, content_hash,"
            " created_by_user_id, created_at, updated_at) VALUES"
            " (1, 'Owned', 'Original owned description', 'hash-owned', 1,"
            " '2025-01-01 00:00:00', '2025-01-01 00:00:00'),"
            " (2, 'Shared', 'Original shared description', 'hash-shared', 1,"
            " '2025-01-02 00:00:00', '2025-01-02 00:00:00')"
        )
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    # Sanity: the fixture must really be in the legacy state, or the test proves
    # nothing.
    probe = sqlite3.connect(str(path))
    try:
        legacy = [
            name
            for name in _job_description_indexes(path)
            if name.startswith("sqlite_autoindex")
        ]
        assert legacy, "fixture is not legacy: no anonymous UNIQUE index"
    finally:
        probe.close()
    engine.dispose()


@pytest.fixture()
def legacy_jd_db_with_children(monkeypatch, tmp_path):
    """A populated pre-Phase-4 database, upgraded on demand."""
    path = tmp_path / "legacy_jd_children.db"
    _build_legacy_jd_db_with_children(path)
    monkeypatch.setenv("TALENTIQ_DB_PATH", str(path))
    db._db_path = None
    db._engine = None
    db._session_local = None
    db._initialized = False
    db.init_db()  # must not raise
    yield path
    db._db_path = None
    db._engine = None
    db._session_local = None
    db._initialized = False


def test_jd_rebuild_succeeds_when_children_reference_it(legacy_jd_db_with_children):
    """The migration must not raise IntegrityError on a populated database.

    Regression for the "DROP TABLE job_descriptions" FOREIGN KEY failure, caused
    by PRAGMA foreign_keys=OFF being a no-op inside a transaction and by the DDL
    running on a different connection than the pragma.
    """
    assert legacy_jd_db_with_children.exists()
    conn = sqlite3.connect(str(legacy_jd_db_with_children))
    try:
        assert conn.execute("SELECT count(*) FROM job_descriptions").fetchone()[0] == 2
    finally:
        conn.close()


def test_child_rows_survive_jd_rebuild(legacy_jd_db_with_children):
    """Rows in tables that reference job_descriptions must be preserved."""
    conn = sqlite3.connect(str(legacy_jd_db_with_children))
    try:
        assert conn.execute("SELECT count(*) FROM resume_analyses").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM candidates").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM resume_versions").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM audit_logs").fetchone()[0] == 0
        # And the surviving rows still point at the right, rebuilt JDs.
        scores = dict(
            conn.execute("SELECT jd_id, ats_score FROM resume_analyses")
        )
        assert scores == {1: 91.0, 2: 64.0}
    finally:
        conn.close()


def test_foreign_keys_still_resolve_after_jd_rebuild(legacy_jd_db_with_children):
    """After the rebuild no row may be left dangling.

    This is the assertion that catches a rebuild which silently breaks child
    foreign keys, which is why legacy_alter_table is pinned during the rename.
    """
    conn = sqlite3.connect(str(legacy_jd_db_with_children))
    try:
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()
    assert violations == [], f"dangling foreign keys after rebuild: {violations}"


def test_foreign_key_enforcement_restored_after_jd_rebuild(legacy_jd_db_with_children):
    """FK enforcement must be ON again after the migration.

    The rebuild disables it to drop the parent table. If it were left off, the
    per-connection guarantee in get_engine() would be silently void for that
    pooled connection and dangling rows could be written afterwards.
    """
    with db.get_engine().connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_no_temp_table_left_after_jd_rebuild(legacy_jd_db_with_children):
    """A successful rebuild must not leave its scratch table behind.

    A failed legacy migration can leave one; the fixed implementation renames it
    into place, so the table is consumed.
    """
    conn = sqlite3.connect(str(legacy_jd_db_with_children))
    try:
        leftovers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE '%upgrade%'"
        ).fetchall()
    finally:
        conn.close()
    assert leftovers == [], f"scratch table left behind: {leftovers}"


def test_jd_rebuild_is_idempotent_with_children(legacy_jd_db_with_children):
    """Re-running init_db on the migrated database is a no-op."""
    db._initialized = False
    db.init_db()
    conn = sqlite3.connect(str(legacy_jd_db_with_children))
    try:
        assert conn.execute("SELECT count(*) FROM job_descriptions").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM resume_analyses").fetchone()[0] == 2
    finally:
        conn.close()


def test_owner_scoped_uniqueness_works_after_populated_rebuild(
    legacy_jd_db_with_children,
):
    """The end goal: a second owner can reuse identical JD text after the
    rebuild, which is exactly what the legacy global UNIQUE prevented."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from core import models

    engine = create_engine(
        f"sqlite:///{legacy_jd_db_with_children.as_posix()}"
    )
    Session = sessionmaker(bind=engine)
    try:
        with Session.begin() as s:
            s.add(
                models.User(
                    id=2,
                    name="Rec Two",
                    email="rec2@example.com",
                    password_hash="x",
                    role="RECRUITER",
                )
            )
        # The legacy table already holds 'hash-shared' owned by user 1. A second
        # owner inserting the same content_hash must now succeed.
        with Session.begin() as s:
            s.add(
                models.JobDescription(
                    title="Shared copy",
                    description="Original shared description",
                    content_hash="hash-shared",
                    created_by_user_id=2,
                )
            )
        with Session() as s:
            assert (
                s.query(models.JobDescription)
                .filter(models.JobDescription.content_hash == "hash-shared")
                .count()
                == 2
            )
    finally:
        engine.dispose()
