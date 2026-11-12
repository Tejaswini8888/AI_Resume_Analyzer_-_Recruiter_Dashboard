"""Resume versioning + per-version candidate analysis history.

Covers the Phase 3 Step 6 behaviors:
- numbered, append-only resume versions (stable 1, 2, 3 ...)
- same-file dedup (``(candidate_id, file_hash)``) reusing a version
- analyses linked to their exact version; historical results always reachable
- analysis dedup on (candidate, resume version, JD) incl. activity events
- search / analytics counting a candidate exactly once regardless of versions
- version uploads never changing recruitment status
- versions surviving an app restart (same database file)
"""

from uuid import uuid4

import pytest

from services.activity_service import get_candidate_activity
from services.analysis_service import (
    get_analyses,
    get_latest_analysis,
    save_analysis,
)
from services.analytics_service import get_score_metrics
from services.candidate_search_service import (
    get_candidate_details,
    search_candidates,
)
from services.candidate_service import create_candidate, get_candidate
from services.recruitment_service import (
    mark_analyzed,
    save_job_description,
    shortlist_candidate,
)
from services.resume_version_service import (
    create_or_get_resume_version,
    get_latest_resume_version,
    get_resume_version,
    get_resume_version_by_number,
    get_resume_versions,
    resume_version_count,
    resume_version_snapshot,
)

import core.db as db


def _candidate(name="Candidate", *, status="Analyzed", resume_hash=None):
    return create_candidate(
        resume_hash=resume_hash or str(uuid4()), name=name, status=status
    )


def _version(candidate_id, file_hash, filename=None):
    return create_or_get_resume_version(
        candidate_id=candidate_id, file_hash=file_hash, filename=filename
    )


def _jd(description="Role"):
    """A real persisted JobDescription.

    ``save_analysis`` validates that ``jd_id`` resolves to an existing row (so a
    dangling reference can never be written), so JD-scoped tests must create the
    row rather than hardcoding an id.
    """
    return save_job_description(f"{description} {uuid4().hex}")


def _analyze(candidate_id, *, version_id, final=70.0, jd_id=None, skills=None,
             unique_suffix=None):
    file_hash = unique_suffix or str(uuid4())
    if version_id is None:
        version_id = _version(candidate_id, file_hash).id
    return save_analysis(
        candidate_id=candidate_id,
        jd_id=jd_id,
        resume_version_id=version_id,
        ats_score=final,
        jd_match_score=final,
        final_score=final,
        matched_skills=skills or [],
        missing_required_skills=[],
        missing_preferred_skills=[],
    )


# ---------------------------------------------------------------------------
# Version service
# ---------------------------------------------------------------------------
def test_first_upload_creates_version_1(fresh_db):
    candidate = _candidate()
    version = _version(candidate.id, "hash-A", "resume.pdf")
    assert version.version_number == 1
    assert version.file_hash == "hash-A"
    assert version.filename == "resume.pdf"
    assert resume_version_count(candidate.id) == 1


def test_identical_reupload_reuses_version(fresh_db):
    candidate = _candidate()
    v1 = _version(candidate.id, "same-content")
    v2 = _version(candidate.id, "same-content", "renamed.pdf")
    assert v2.id == v1.id
    assert v2.version_number == 1
    assert resume_version_count(candidate.id) == 1


def test_different_content_creates_next_numbered_versions(fresh_db):
    candidate = _candidate()
    v1 = _version(candidate.id, "hash-A")
    v2 = _version(candidate.id, "hash-B")
    v3 = _version(candidate.id, "hash-C")
    assert [v1.version_number, v2.version_number, v3.version_number] == [1, 2, 3]


def test_version_numbers_are_stable_never_renumbered(fresh_db):
    candidate = _candidate()
    v1 = _version(candidate.id, "hash-A")
    v2 = _version(candidate.id, "hash-B")
    _version(candidate.id, "hash-C")  # v3 added later
    assert get_resume_version(v1.id).version_number == 1
    assert get_resume_version(v2.id).version_number == 2
    total = resume_version_count(candidate.id)
    assert total == 3


def test_get_latest_resume_version(fresh_db):
    candidate = _candidate()
    _version(candidate.id, "hash-A")
    v2 = _version(candidate.id, "hash-B")
    latest = get_latest_resume_version(candidate.id)
    assert latest.id == v2.id
    assert latest.version_number == 2


def test_get_resume_version_by_number(fresh_db):
    candidate = _candidate()
    v1 = _version(candidate.id, "hash-A")
    _version(candidate.id, "hash-B")
    assert get_resume_version_by_number(candidate.id, 1).id == v1.id
    assert get_resume_version_by_number(candidate.id, 99) is None


def test_versions_sorted_newest_first(fresh_db):
    candidate = _candidate()
    _version(candidate.id, "hash-A", "v1.pdf")
    _version(candidate.id, "hash-B", "v2.pdf")
    versions = get_resume_versions(candidate.id)
    assert [v.version_number for v in versions] == [2, 1]
    assert versions[0].filename == "v2.pdf"


def test_snapshot_marks_current_version(fresh_db):
    candidate = _candidate()
    v1 = _version(candidate.id, "hash-A")
    v2 = _version(candidate.id, "hash-B")
    snap_v1 = resume_version_snapshot(v1, latest_number=2)
    snap_v2 = resume_version_snapshot(v2, latest_number=2)
    assert snap_v2["is_current"] is True
    assert snap_v1["is_current"] is False
    assert snap_v1["version_number"] == 1


# ---------------------------------------------------------------------------
# Analysis ↔ version linkage + dedup
# ---------------------------------------------------------------------------
def test_analysis_links_to_its_version(fresh_db):
    candidate = _candidate()
    version = _version(candidate.id, "hash-A")
    analysis = _analyze(candidate.id, version_id=version.id, final=62.0)
    assert analysis.resume_version_id == version.id


def test_same_version_same_jd_dedup(fresh_db):
    candidate = _candidate()
    version = _version(candidate.id, "hash-A")
    jd = _jd("Dedup Role")
    first = _analyze(candidate.id, version_id=version.id, jd_id=jd.id, final=62.0)
    second = _analyze(candidate.id, version_id=version.id, jd_id=jd.id, final=90.0)
    assert second.id == first.id
    assert len(get_analyses(candidate.id, resume_version_id=version.id)) == 1
    # No duplicate timeline event for the deduplicated save.
    analyze_events = [
        a for a in get_candidate_activity(candidate.id)
        if a.activity_type == "RESUME_ANALYZED"
    ]
    assert len(analyze_events) == 1


def test_allow_duplicate_creates_new_row(fresh_db):
    candidate = _candidate()
    version = _version(candidate.id, "hash-A")
    jd = _jd("Duplicate Role")
    first = _analyze(candidate.id, version_id=version.id, jd_id=jd.id)
    second = save_analysis(
        candidate_id=candidate.id,
        jd_id=jd.id,
        resume_version_id=version.id,
        allow_duplicate=True,
        ats_score=80.0,
        jd_match_score=80.0,
        final_score=80.0,
    )
    assert second.id != first.id
    assert len(get_analyses(candidate.id, resume_version_id=version.id)) == 2


def test_same_version_different_jd_keeps_both(fresh_db):
    candidate = _candidate()
    version = _version(candidate.id, "hash-A")
    jd_a, jd_b = _jd("Role A"), _jd("Role B")
    _analyze(candidate.id, version_id=version.id, jd_id=jd_a.id)
    _analyze(candidate.id, version_id=version.id, jd_id=jd_b.id)
    analyses = get_analyses(candidate.id, resume_version_id=version.id)
    assert len(analyses) == 2
    assert {a.jd_id for a in analyses} == {jd_a.id, jd_b.id}


def test_new_version_creates_its_own_analysis(fresh_db):
    candidate = _candidate()
    v1 = _version(candidate.id, "hash-A")
    v2 = _version(candidate.id, "hash-B")
    jd = _jd("Versioned Role")
    a1 = _analyze(
        candidate.id, version_id=v1.id, final=55.0, jd_id=jd.id, skills=["java"]
    )
    a2 = _analyze(
        candidate.id,
        version_id=v2.id,
        final=88.0,
        jd_id=jd.id,
        skills=["java", "python"],
    )
    assert a1.id != a2.id
    assert a1.resume_version_id == v1.id
    assert a2.resume_version_id == v2.id
    assert a1.final_score == 55.0
    assert a2.final_score == 88.0


def test_analysis_rejects_unknown_jd_id(fresh_db):
    """A dangling ``jd_id`` is refused instead of written."""
    candidate = _candidate()
    version = _version(candidate.id, "hash-A")
    with pytest.raises(LookupError):
        _analyze(candidate.id, version_id=version.id, jd_id=987_654)
    assert get_analyses(candidate.id, resume_version_id=version.id) == []


def test_historical_analysis_never_overwritten(fresh_db):
    candidate = _candidate()
    v1 = _version(candidate.id, "hash-A")
    v2 = _version(candidate.id, "hash-B")
    _analyze(candidate.id, version_id=v1.id, final=40.0)
    _analyze(candidate.id, version_id=v2.id, final=95.0)
    v1_latest = get_latest_analysis(candidate.id, resume_version_id=v1.id)
    v2_latest = get_latest_analysis(candidate.id, resume_version_id=v2.id)
    global_latest = get_latest_analysis(candidate.id)
    assert v1_latest.final_score == 40.0
    assert v2_latest.final_score == 95.0
    assert global_latest.final_score == 95.0  # current version wins globally


def test_activity_descriptions_name_the_version(fresh_db):
    candidate = _candidate()
    _version(candidate.id, "hash-A")
    v2 = _version(candidate.id, "hash-B")
    _analyze(candidate.id, version_id=v2.id)
    events = [
        a for a in get_candidate_activity(candidate.id)
        if a.activity_type == "RESUME_ANALYZED"
    ]
    descriptions = {a.description for a in events}
    assert "Resume Version 2 uploaded and analyzed" in descriptions


# ---------------------------------------------------------------------------
# Search + analytics count a candidate once, using the current version's data
# ---------------------------------------------------------------------------
def test_search_counts_candidate_once_with_versions(fresh_db):
    candidate = _candidate(name="Tejas")
    v1 = _version(candidate.id, "hash-A")
    v2 = _version(candidate.id, "hash-B")
    _analyze(candidate.id, version_id=v1.id, final=50.0)
    _analyze(candidate.id, version_id=v2.id, final=85.0, skills=["python"])
    response = search_candidates(query="tejas")
    assert response["has_candidates"] is True
    assert response["total"] == 1
    assert len(response["results"]) == 1


def test_search_uses_latest_version_analysis(fresh_db):
    candidate = _candidate(name="Tejas")
    v1 = _version(candidate.id, "hash-A")
    v2 = _version(candidate.id, "hash-B")
    _analyze(candidate.id, version_id=v1.id, final=30.0)
    _analyze(candidate.id, version_id=v2.id, final=90.0)
    row = search_candidates(query="tejas")["results"][0]
    assert row["final_score"] == 90.0
    assert row["ats_score"] == 90.0
    assert row["jd_match_score"] == 90.0


def test_analytics_counts_candidate_once_with_versions(fresh_db):
    candidate = _candidate(name="Tejas")
    v1 = _version(candidate.id, "hash-A")
    v2 = _version(candidate.id, "hash-B")
    _analyze(candidate.id, version_id=v1.id, final=50.0)
    _analyze(candidate.id, version_id=v2.id, final=85.0)
    metrics = get_score_metrics()
    assert metrics["candidate_count"] == 1


def test_candidate_details_list_versions_newest_first(fresh_db):
    candidate = _candidate(name="Tejas")
    v1 = _version(candidate.id, "hash-A", "old.pdf")
    v2 = _version(candidate.id, "hash-B", "new.pdf")
    _analyze(candidate.id, version_id=v1.id, final=50.0)
    _analyze(candidate.id, version_id=v2.id, final=85.0, skills=["python"])
    detail = get_candidate_details(candidate.id)
    assert detail is not None
    versions = detail["resume_versions"]
    assert [v["version_number"] for v in versions] == [2, 1]
    assert versions[0]["is_current"] is True
    assert versions[1]["is_current"] is False
    assert versions[0]["id"] == v2.id
    assert detail["latest_analysis"].final_score == 85.0


# ---------------------------------------------------------------------------
# Status independence: uploads never change recruiter decisions
# ---------------------------------------------------------------------------
def test_uploading_new_version_never_changes_status(fresh_db):
    candidate = _candidate(name="Tejas")
    shortlist_candidate(candidate.id)
    assert get_candidate(candidate.id).status == "Shortlisted"
    _version(candidate.id, "hash-A")
    _version(candidate.id, "hash-B")
    _analyze(candidate.id, version_id=_version(candidate.id, "hash-B").id)
    assert get_candidate(candidate.id).status == "Shortlisted"


def test_mark_analyzed_only_promotes_new(fresh_db):
    candidate = _candidate(name="Tejas")
    shortlist_candidate(candidate.id)
    mark_analyzed(candidate.id)
    assert get_candidate(candidate.id).status == "Shortlisted"


# ---------------------------------------------------------------------------
# Restart persistence (same database file, fresh engine)
# ---------------------------------------------------------------------------
def test_versions_persist_across_restart(fresh_db):
    candidate = _candidate(name="Tejas")
    v1 = _version(candidate.id, "hash-A", "r1.pdf")
    v2 = _version(candidate.id, "hash-B", "r2.pdf")
    _analyze(candidate.id, version_id=v1.id, final=55.0)
    _analyze(candidate.id, version_id=v2.id, final=90.0)

    # Simulate an app restart: discard singletons, keep the SAME database file.
    db._db_path = None
    db._engine = None
    db._session_local = None
    db._initialized = False
    db.init_db()

    versions = get_resume_versions(candidate.id)
    assert [v.version_number for v in versions] == [2, 1]
    assert resume_version_count(candidate.id) == 2
    historical = get_latest_analysis(candidate.id, resume_version_id=v1.id)
    assert historical.final_score == 55.0
    assert get_latest_analysis(candidate.id).final_score == 90.0


# ---------------------------------------------------------------------------
# Page persistence flow (mirrors pages/2 and pages/4 `_persist_*` helpers)
# ---------------------------------------------------------------------------
class _Stub:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_page_persistence_flow_uses_resume_version(fresh_db):
    from services.analysis_service import save_analysis_from_results
    from services.recruitment_service import mark_analyzed

    candidate = create_candidate(
        resume_hash="hash-v2",
        name="Tejas",
        resume_filename="resume_v2.pdf",
    )
    mark_analyzed(candidate.id)
    assert get_candidate(candidate.id).status == "Analyzed"

    ats_result = _Stub(
        overall_score=72.0,
        section_scores={},
        strengths=[],
        issues=[],
        detected_sections=[],
        missing_sections=[],
        keyword_coverage={},
    )
    jd_match = _Stub(
        overall_match_score=88.0,
        semantic_match_score=0.0,
        matched_skills=["python"],
        missing_required_skills=[],
        missing_preferred_skills=["aws"],
    )

    version = create_or_get_resume_version(
        candidate_id=candidate.id,
        file_hash="hash-v2",
        filename="resume_v2.pdf",
    )
    analysis = save_analysis_from_results(
        candidate_id=candidate.id,
        jd_id=None,
        resume_version_id=version.id,
        ats_result=ats_result,
        jd_match=jd_match,
        final_score=81.6,
    )
    assert analysis.resume_version_id == version.id
    assert analysis.ats_score == 72.0
    assert analysis.jd_match_score == 88.0
    assert analysis.final_score == 81.6
    assert analysis.matched_skills == ["python"]
    assert analysis.missing_preferred_skills == ["aws"]

    # Re-running the SAME upload (identical file) reuses the version and
    # deduplicates the analysis — no duplicates, no extra timeline events.
    same_version = create_or_get_resume_version(
        candidate_id=candidate.id,
        file_hash="hash-v2",
        filename="resume_v2.pdf",
    )
    assert same_version.id == version.id
    again = save_analysis_from_results(
        candidate_id=candidate.id,
        jd_id=None,
        resume_version_id=same_version.id,
        ats_result=ats_result,
        jd_match=jd_match,
        final_score=81.6,
    )
    assert again.id == analysis.id
    assert len(get_analyses(candidate.id, resume_version_id=version.id)) == 1