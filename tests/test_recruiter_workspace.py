"""Recruiter Workspace (Phase 3 Step 7): unified Candidate Pool + Candidate Detail.

Covers the persisted-database behaviors the new ``pages/7_Candidate_Intelligence``
drives:
- pool = one row per candidate, current resume version attached, never duplicated
- workspace detail loads persisted identity/versions/activity (no recalculation)
- exact (candidate, resume version, JD) analysis context: V1+JDA -> A1, V2+JDA -> A2,
  V2+JDB -> B1, V1+JDB -> None (a version's scores never bleed into another)
- distinct JD contexts with ``no_jd`` flag
- pool CSV export = exact current result set, one row per candidate
- status workflow through the single status system, with activity + pool reflection
- Score-Eligible stays a display-only score property, never a status
- persistence across a simulated app restart (same database file)
"""

from uuid import uuid4

from services.activity_service import get_candidate_activity
from services.analysis_service import save_analysis
from services.candidate_search_service import (
    candidate_pool_csv,
    get_analysis_for_context,
    get_candidate_jd_contexts,
    get_workspace_detail,
    score_eligible_min,
    search_candidates,
)
from services.candidate_service import create_candidate, get_candidate
from services.recruitment_service import (
    save_job_description,
    update_candidate_status,
)
from services.resume_version_service import (
    create_or_get_resume_version,
    get_latest_resume_version,
)

import core.db as db


def _candidate(name="Tejas", email=None, *, status="Analyzed"):
    return create_candidate(
        resume_hash=str(uuid4()),
        name=name,
        email=email or f"{name.lower()}@example.com",
        status=status,
    )


def _version(candidate_id, file_hash, filename=None):
    return create_or_get_resume_version(
        candidate_id=candidate_id, file_hash=file_hash, filename=filename
    )


def _analyze(candidate_id, *, version_id=None, jd_id=None, final=70.0,
             skills=None, missing_required=None, missing_preferred=None):
    """Analysis tied to a concrete resume version (the real page flow)."""
    if version_id is None:
        version_id = _version(candidate_id, str(uuid4())).id
    return save_analysis(
        candidate_id=candidate_id,
        jd_id=jd_id,
        resume_version_id=version_id,
        ats_score=final,
        jd_match_score=final,
        final_score=final,
        matched_skills=skills or [],
        missing_required_skills=missing_required or [],
        missing_preferred_skills=missing_preferred or [],
    )


def _jd(title, description=None):
    return save_job_description(description or f"{title} role", title=title)


# ---------------------------------------------------------------------------
# Workspace detail load (persisted identity, never recalculated)
# ---------------------------------------------------------------------------
def test_workspace_detail_loads_persisted_identity(fresh_db):
    candidate = _candidate(name="Priya")
    v1 = _version(candidate.id, "hash-A", "r1.pdf")
    analysis = _analyze(candidate.id, version_id=v1.id, final=74.0, skills=["python"])
    detail = get_workspace_detail(candidate.id)
    assert detail is not None
    assert detail["candidate"]["name"] == "Priya"
    assert detail["candidate"]["status"] == "Analyzed"
    assert [v["version_number"] for v in detail["resume_versions"]] == [1]
    assert detail["resume_versions"][0]["id"] == v1.id
    assert detail["resume_versions"][0]["is_current"] is True
    assert detail["latest_analysis"].id == analysis.id
    assert detail["score_eligible"] is True  # 74.0 >= score_eligible_min()


def test_workspace_detail_unknown_candidate_returns_none(fresh_db):
    assert get_workspace_detail(999_999) is None


def test_workspace_detail_uses_persisted_analysis_without_recalculation(fresh_db):
    """The detail pane shows the SAVED snapshot — nothing is recomputed."""
    candidate = _candidate()
    v1 = _version(candidate.id, "hash-A")
    analysis = _analyze(
        candidate.id,
        version_id=v1.id,
        final=88.0,
        skills=["python", "sql"],
        missing_required=["aws"],
        missing_preferred=["kubernetes"],
    )
    detail = get_workspace_detail(candidate.id)
    latest = detail["latest_analysis"]
    assert latest.id == analysis.id
    assert latest.final_score == 88.0
    assert latest.matched_skills == ["python", "sql"]
    assert latest.missing_required_skills == ["aws"]
    assert latest.missing_preferred_skills == ["kubernetes"]
    assert latest.jd_id == analysis.jd_id


# ---------------------------------------------------------------------------
# Pool: one row per candidate + current version attached
# ---------------------------------------------------------------------------
def test_pool_one_row_per_candidate_despite_multiple_versions(fresh_db):
    candidate = _candidate(name="Tejas")
    v1 = _version(candidate.id, "hash-A")
    v2 = _version(candidate.id, "hash-B")
    _analyze(candidate.id, version_id=v1.id, final=50.0)
    _analyze(candidate.id, version_id=v2.id, final=90.0)
    response = search_candidates(query="tejas")
    assert response["has_candidates"] is True
    assert response["total"] == 1
    assert len(response["results"]) == 1


def test_pool_attaches_latest_resume_version(fresh_db):
    candidate = _candidate(name="Tejas")
    _version(candidate.id, "hash-A")
    v2 = _version(candidate.id, "hash-B")
    _analyze(candidate.id, version_id=v2.id, final=84.0)
    row = search_candidates(query="tejas")["results"][0]
    assert row["latest_version"] == 2
    assert get_latest_resume_version(candidate.id).version_number == 2


def test_pool_latest_version_when_candidate_has_no_versions(fresh_db):
    """Legacy rows (analysis without a resume version) still appear once."""
    candidate = _candidate(name="Solo")
    save_analysis(
        candidate_id=candidate.id,
        jd_id=None,
        ats_score=60.0,
        jd_match_score=60.0,
        final_score=60.0,
    )
    row = search_candidates(query="solo")["results"][0]
    assert row["latest_version"] is None
    assert row["status"] == "Analyzed"


def test_pool_empty_database_reports_no_candidates(fresh_db):
    response = search_candidates()
    assert response["has_candidates"] is False
    assert response["has_results"] is False
    assert response["results"] == []


# ---------------------------------------------------------------------------
# Exact (version, JD) analysis context — never mix
# ---------------------------------------------------------------------------
def test_analysis_context_matrix(fresh_db):
    """V1+JDA -> A1, V2+JDA -> A2, V2+JDB -> B1, V1+JDB -> None."""
    candidate = _candidate(name="Matrix")
    v1 = _version(candidate.id, "hash-A")
    v2 = _version(candidate.id, "hash-B")
    jd_a = _jd("Frontend")
    jd_b = _jd("Backend")

    a1 = _analyze(candidate.id, version_id=v1.id, jd_id=jd_a.id, final=41.0)
    a2 = _analyze(candidate.id, version_id=v2.id, jd_id=jd_a.id, final=77.0)
    b1 = _analyze(candidate.id, version_id=v2.id, jd_id=jd_b.id, final=65.0)

    resolved_a1 = get_analysis_for_context(candidate.id, v1.id, jd_a.id)
    resolved_a2 = get_analysis_for_context(candidate.id, v2.id, jd_a.id)
    resolved_b1 = get_analysis_for_context(candidate.id, v2.id, jd_b.id)
    resolved_b_missing = get_analysis_for_context(candidate.id, v1.id, jd_b.id)

    assert resolved_a1.id == a1.id and resolved_a1.final_score == 41.0
    assert resolved_a2.id == a2.id and resolved_a2.final_score == 77.0
    assert resolved_b1.id == b1.id and resolved_b1.final_score == 65.0
    assert resolved_b_missing is None


def test_analysis_context_without_jd_resolves_none_jd_only(fresh_db):
    candidate = _candidate(name="Generic")
    v1 = _version(candidate.id, "hash-A")
    v2 = _version(candidate.id, "hash-B")
    _analyze(candidate.id, version_id=v1.id, jd_id=None, final=80.0)
    assert get_analysis_for_context(candidate.id, v1.id, None).final_score == 80.0
    # JD A was never analyzed for V2 (and v2 has no analysis at all).
    jd_a = _jd("Data")
    assert get_analysis_for_context(candidate.id, v2.id, jd_a.id) is None
    assert get_analysis_for_context(candidate.id, v2.id, None) is None


# ---------------------------------------------------------------------------
# Distinct JD contexts + no_jd flag for the detail selectors
# ---------------------------------------------------------------------------
def test_jd_contexts_distinct_and_counted(fresh_db):
    candidate = _candidate(name="Multi")
    v1 = _version(candidate.id, "hash-A")
    jd_a = _jd("Data Engineer")
    jd_b = _jd("ML Engineer")
    _analyze(candidate.id, version_id=v1.id, jd_id=jd_a.id, final=50.0)
    _analyze(candidate.id, version_id=v1.id, jd_id=jd_a.id, final=60.0)  # dedup
    _analyze(candidate.id, version_id=v1.id, jd_id=jd_b.id, final=70.0)

    bundle = get_candidate_jd_contexts(candidate.id)
    assert bundle["no_jd"] is False
    titles = {ctx["title"] for ctx in bundle["contexts"]}
    assert titles == {"Data Engineer", "ML Engineer"}
    by_title = {ctx["title"]: ctx for ctx in bundle["contexts"]}
    assert by_title["Data Engineer"]["analysis_count"] == 1  # dedup counts once
    assert by_title["ML Engineer"]["analysis_count"] == 1
    assert by_title["Data Engineer"]["id"] == jd_a.id


def test_jd_contexts_report_no_jd_analyses(fresh_db):
    candidate = _candidate(name="NoJd")
    v1 = _version(candidate.id, "hash-A")
    jd_a = _jd("Platform")
    _analyze(candidate.id, version_id=v1.id, jd_id=jd_a.id, final=50.0)
    _analyze(candidate.id, version_id=v1.id, jd_id=None, final=55.0)

    bundle = get_candidate_jd_contexts(candidate.id)
    assert bundle["no_jd"] is True
    assert [ctx["title"] for ctx in bundle["contexts"]] == ["Platform"]


def test_jd_contexts_empty_for_candidate_without_jds(fresh_db):
    candidate = _candidate(name="Solo")
    v1 = _version(candidate.id, "hash-A")
    _analyze(candidate.id, version_id=v1.id, jd_id=None, final=60.0)
    bundle = get_candidate_jd_contexts(candidate.id)
    assert bundle["contexts"] == []
    assert bundle["no_jd"] is True


# ---------------------------------------------------------------------------
# Pool CSV = exact current result set
# ---------------------------------------------------------------------------
def test_pool_csv_matches_current_result_set(fresh_db):
    first = _candidate(name="Alpha")
    second = _candidate(name="Beta")
    _analyze(first.id, final=82.0)
    _analyze(second.id, final=55.0)

    results = search_candidates()["results"]
    assert len(results) == 2

    csv_text = candidate_pool_csv(results)
    assert csv_text.startswith("Candidate,Status,Score-Eligible,ATS Score,JD Match")
    lines = [ln for ln in csv_text.strip().splitlines()]
    assert len(lines) == 3  # header + 2 candidates, one row each
    assert "Alpha" in csv_text
    assert "Beta" in csv_text
    assert csv_text.count("82.0") >= 1
    assert csv_text.count("55.0") >= 1


def test_pool_csv_reflects_status_filter(fresh_db):
    first = _candidate(name="Alpha")
    second = _candidate(name="Beta")
    _analyze(first.id, final=82.0)
    _analyze(second.id, final=55.0)
    update_candidate_status(first.id, "Shortlisted")

    results = search_candidates(status="Shortlisted")["results"]
    assert [r["name"] for r in results] == ["Alpha"]
    csv_text = candidate_pool_csv(results)
    assert "Beta" not in csv_text
    assert "Shortlisted" in csv_text


# ---------------------------------------------------------------------------
# Status workflow through the single status system + activity
# ---------------------------------------------------------------------------
def test_status_change_reflects_in_pool_and_detail_with_activity(fresh_db):
    candidate = _candidate(name="Flow")
    _version(candidate.id, "hash-A")
    _analyze(candidate.id, final=70.0)

    update_candidate_status(candidate.id, "Shortlisted")

    pool_row = search_candidates(query="flow")["results"][0]
    assert pool_row["status"] == "Shortlisted"
    detail = get_workspace_detail(candidate.id)
    assert detail["candidate"]["status"] == "Shortlisted"
    assert detail["status"] == "Shortlisted"
    events = get_candidate_activity(candidate.id)
    assert any(a.activity_type == "SHORTLISTED" for a in events)
    assert any(
        a.activity_type == "SHORTLISTED" and "shortlisted" in (a.description or "").lower()
        for a in events
    )


def test_status_move_back_allowed_and_recorded(fresh_db):
    candidate = _candidate(name="U-turn")
    _version(candidate.id, "hash-A")
    _analyze(candidate.id, final=70.0)
    update_candidate_status(candidate.id, "Selected")
    update_candidate_status(candidate.id, "Shortlisted")
    update_candidate_status(candidate.id, "Rejected")
    update_candidate_status(candidate.id, "Analyzed")

    detail = get_workspace_detail(candidate.id)
    assert detail["candidate"]["status"] == "Analyzed"
    descs = [a.description for a in get_candidate_activity(candidate.id)]
    assert any("Analyzed" in d for d in descs)


def test_score_eligible_never_changes_status(fresh_db):
    """The display-only eligibility tag must not leak into recruitment status."""
    candidate = _candidate(name="Borderline")
    _version(candidate.id, "hash-A")
    below = score_eligible_min() - 0.5
    _analyze(candidate.id, final=below)
    assert get_workspace_detail(candidate.id)["score_eligible"] is False
    assert get_candidate(candidate.id).status == "Analyzed"

    # Status can be moved regardless of eligibility, but the score is untouched.
    update_candidate_status(candidate.id, "Shortlisted")
    assert get_candidate(candidate.id).status == "Shortlisted"
    assert get_workspace_detail(candidate.id)["score_eligible"] is False

    # And eligibility changes never touch status (score stays a property).
    save_analysis(
        candidate_id=candidate.id,
        jd_id=None,
        resume_version_id=get_latest_resume_version(candidate.id).id,
        allow_duplicate=True,
        ats_score=90.0,
        jd_match_score=90.0,
        final_score=90.0,
    )
    assert get_workspace_detail(candidate.id)["score_eligible"] is True
    assert get_candidate(candidate.id).status == "Shortlisted"


# ---------------------------------------------------------------------------
# Restart persistence (same database file, fresh engine)
# ---------------------------------------------------------------------------
def test_workspace_state_persists_across_restart(fresh_db):
    candidate = _candidate(name="Persist")
    v1 = _version(candidate.id, "hash-A", "r1.pdf")
    v2 = _version(candidate.id, "hash-B", "r2.pdf")
    jd_a = _jd("Backend")
    _analyze(candidate.id, version_id=v1.id, jd_id=jd_a.id, final=35.0)
    _analyze(candidate.id, version_id=v2.id, jd_id=jd_a.id, final=66.0)
    update_candidate_status(candidate.id, "Interview")

    db._db_path = None
    db._engine = None
    db._session_local = None
    db._initialized = False
    db.init_db()

    detail = get_workspace_detail(candidate.id)
    assert detail is not None
    assert [v["version_number"] for v in detail["resume_versions"]] == [2, 1]
    assert detail["candidate"]["status"] == "Interview"
    assert {"Backend"} == {c["title"] for c in detail["jd_contexts"]}
    assert get_analysis_for_context(candidate.id, v1.id, jd_a.id).final_score == 35.0
    assert get_analysis_for_context(candidate.id, v2.id, jd_a.id).final_score == 66.0

    pool_row = search_candidates(query="persist")["results"][0]
    assert pool_row["status"] == "Interview"
    assert pool_row["latest_version"] == 2