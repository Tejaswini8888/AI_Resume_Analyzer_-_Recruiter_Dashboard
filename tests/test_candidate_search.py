"""Recruiter Search & Candidate Discovery — service-level tests.

Covers the persistent-database search/filter/sort/pagination contract of
``services.candidate_search_service`` (the "Candidate Intelligence" page):

1.  search by candidate name (partial)        9.  JD filter
2.  search by email                           10. multiple filters combined
3.  search by resume filename                 11. sorting
4.  search by skill (canonical)               12. empty results
5.  status filter                             13. clear filters (default pool)
6.  ATS filter                                17. multiple JDs
7.  JD Match filter                           18. skill alias normalization
8.  Final Score filter                        19. case-insensitive search
                                               20. persistence after restart

Plus the two headline guarantees: searching "Python" finds canonical Python
skills (incl. "Python 3"/"Python3"), and "Java" never returns candidates solely
because they have JavaScript.
"""

import time

import pytest

from services.candidate_search_service import (
    PAGE_SIZE,
    get_candidate_details,
    get_candidate_pool,
    get_skill_options,
    search_candidates,
)


def _names(response):
    return [row["name"] for row in response["results"]]


# ---------------------------------------------------------------------------
# Free-text search
# ---------------------------------------------------------------------------
def test_search_by_name_partial(factory):
    factory(name="Tejas Kumar", resume_filename="tejas_resume.pdf")
    factory(name="Maria Silva", resume_filename="maria_cv.pdf")

    exact = search_candidates(query="Tejas")
    assert _names(exact) == ["Tejas Kumar"]

    partial = search_candidates(query="tej")
    assert _names(partial) == ["Tejas Kumar"]

    # A different name is not matched.
    only_maria = search_candidates(query="Maria")
    assert _names(only_maria) == ["Maria Silva"]


def test_search_by_email(factory):
    factory(name="Tejas Kumar", email="tejas.kumar@example.com")
    factory(name="Maria Silva", email="maria@other.org")

    full = search_candidates(query="tejas.kumar@example.com")
    assert [row["email"] for row in full["results"]] == ["tejas.kumar@example.com"]

    part = search_candidates(query="example.com")
    assert _names(part) == ["Tejas Kumar"]


def test_search_by_resume_filename(factory):
    factory(name="Tejas Kumar", resume_filename="tejas_resume.pdf")
    factory(name="Maria Silva", resume_filename="maria_cv.pdf")

    r = search_candidates(query="resume.pdf")
    assert _names(r) == ["Tejas Kumar"]

    r = search_candidates(query="maria_cv.pdf")
    assert _names(r) == ["Maria Silva"]


def test_search_by_skill(factory):
    factory(name="Py Dev", skills=["Python"])
    factory(name="Py3 Dev", skills=["Python 3"])
    factory(name="Js Dev", skills=["JavaScript"])

    r = search_candidates(query="python")
    assert set(_names(r)) == {"Py Dev", "Py3 Dev"}


def test_java_never_matches_javascript(factory):
    """Java must NOT return a candidate solely because they have JavaScript."""
    alice, _ = factory(
        name="Alice Backend",
        email="alice@example.com",
        resume_filename="backend.pdf",
        skills=["Java"],
    )
    bob, _ = factory(
        name="Bob Frontend",
        email="bob@example.com",
        resume_filename="frontend.pdf",
        skills=["JavaScript"],
    )

    r = search_candidates(query="java")
    assert [row["id"] for row in r["results"]] == [alice.id]

    r = search_candidates(query="javascript")
    assert [row["id"] for row in r["results"]] == [bob.id]


def test_skill_filter_c_never_matches_css_or_cloud(factory):
    factory(name="C Dev", skills=["C"])
    factory(name="CSS Dev", skills=["CSS"])
    factory(name="Cloud Dev", skills=["Cloud"])

    r = search_candidates(skill="C")
    assert _names(r) == ["C Dev"]


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def test_status_filter(factory):
    factory(name="A Analyzed", status="Analyzed")
    factory(name="B Shortlisted", status="Shortlisted")
    factory(name="C Interview", status="Interview")

    r = search_candidates(status="Shortlisted")
    assert _names(r) == ["B Shortlisted"]

    r = search_candidates(status="Interview")
    assert _names(r) == ["C Interview"]


def test_min_ats_filter(factory):
    factory(name="Low ATS", ats_score=50, jd_match_score=50, final_score=50)
    factory(name="High ATS", ats_score=90, jd_match_score=50, final_score=66)

    r = search_candidates(min_ats=70)
    assert _names(r) == ["High ATS"]


def test_min_jd_match_filter(factory):
    factory(name="Low JD", ats_score=90, jd_match_score=40, final_score=60)
    factory(name="High JD", ats_score=90, jd_match_score=85, final_score=87)

    r = search_candidates(min_jd=60)
    assert _names(r) == ["High JD"]


def test_min_final_score_filter(factory):
    factory(name="Low Final", final_score=55)
    factory(name="High Final", final_score=88)

    r = search_candidates(min_final=60)
    assert _names(r) == ["High Final"]


def test_multiple_filters_combined(factory):
    c_target, _ = factory(
        name="Py Shortlist",
        skills=["Python"],
        status="Shortlisted",
        final_score=80,
    )
    factory(name="Py Analyzed", skills=["Python"], status="Analyzed", final_score=80)
    factory(name="Java Low", skills=["Java"], status="Shortlisted", final_score=50)

    r = search_candidates(
        query="Python", status="Shortlisted", min_final=60
    )
    assert [row["id"] for row in r["results"]] == [c_target.id]


def test_job_description_filter(factory):
    from services.recruitment_service import save_job_description

    jd_a = save_job_description("Python role", title="Backend Developer")
    jd_b = save_job_description("React role", title="Frontend Developer")

    factory(name="A1", jd_id=jd_a.id)
    factory(name="A2", jd_id=jd_a.id)
    factory(name="B1", jd_id=jd_b.id)

    r = search_candidates(jd_id=jd_a.id)
    assert set(_names(r)) == {"A1", "A2"}

    r = search_candidates(jd_id=jd_b.id)
    assert _names(r) == ["B1"]


def test_job_description_scopes_scores_and_skill(factory):
    """With a JD selected, scores/skills reflect analyses FOR that JD only.

    Same candidate analyzed against two roles: the JD-scoped pool must show the
    analysis of the selected role, and a skill recorded only in the other JD's
    analysis must not make the candidate match here.
    """
    from services.analysis_service import save_analysis
    from services.recruitment_service import save_job_description

    jd_a = save_job_description("python role", title="Backend Developer")
    jd_b = save_job_description("react role", title="Frontend Developer")

    cand, _ = factory(name="Multi", jd_id=jd_a.id, final_score=70, skills=["Python"])
    save_analysis(
        candidate_id=cand.id,
        jd_id=jd_b.id,
        ats_score=95,
        jd_match_score=95,
        final_score=95,
        matched_skills=["React"],
    )

    r_a = search_candidates(jd_id=jd_a.id)
    assert len(r_a["results"]) == 1
    assert r_a["results"][0]["final_score"] == 70.0

    r_b = search_candidates(jd_id=jd_b.id)
    assert r_b["results"][0]["final_score"] == 95.0

    assert search_candidates(jd_id=jd_b.id, min_final=80)["total"] == 1
    assert search_candidates(jd_id=jd_a.id, min_final=80)["total"] == 0

    # Python is recorded only in JD A's analysis -> cannot match under JD B.
    assert search_candidates(jd_id=jd_b.id, skill="Python")["total"] == 0
    assert search_candidates(jd_id=jd_a.id, skill="Python")["total"] == 1


def test_multiple_jds_do_not_mix(factory):
    from services.recruitment_service import save_job_description

    jd_a = save_job_description("Data role", title="Data Engineer")
    jd_b = save_job_description("Web role", title="Web Developer")

    factory(name="For A", jd_id=jd_a.id)
    factory(name="For B", jd_id=jd_b.id)

    all_r = search_candidates()
    assert all_r["total"] == 2

    a_ids = {row["id"] for row in search_candidates(jd_id=jd_a.id)["results"]}
    b_ids = {row["id"] for row in search_candidates(jd_id=jd_b.id)["results"]}
    assert not (a_ids & b_ids)


# ---------------------------------------------------------------------------
# Skill alias normalization
# ---------------------------------------------------------------------------
def test_skill_alias_normalization(factory):
    factory(name="S1", skills=["Python"])
    factory(name="S2", skills=["Python 3"])
    factory(name="S3", skills=["Python3"])
    factory(name="S4", skills=["Py 3"])

    r = search_candidates(query="python")
    assert set(_names(r)) == {"S1", "S2", "S3", "S4"}


def test_skill_options_collapse_aliases(factory):
    factory(name="S1", skills=["Python"])
    factory(name="S2", skills=["Python 3"])
    factory(name="S3", skills=["Python3"])
    factory(name="S4", skills=["Java"])

    options = get_skill_options()
    assert options.count("Python") == 1
    assert options.count("Java") == 1
    assert "Python" in options and "Java" in options


# ---------------------------------------------------------------------------
# Case-insensitivity
# ---------------------------------------------------------------------------
def test_case_insensitive_search(factory):
    factory(name="Tejas Kumar", email="TEJ@example.com", resume_filename="TeJaS.PDF")

    assert len(search_candidates(query="TEJAS")["results"]) == 1
    assert len(search_candidates(query="tejas")["results"]) == 1
    assert len(search_candidates(query="TeJaS")["results"]) == 1
    assert len(search_candidates(query="example.COM")["results"]) == 1


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------
def test_sort_final_score_desc(factory):
    factory(name="S1", final_score=70)
    factory(name="S2", final_score=90)
    factory(name="S3", final_score=80)

    r = search_candidates(sort="final_desc")
    assert _names(r) == ["S2", "S3", "S1"]


def test_sort_ats_and_jd_desc(factory):
    factory(name="Weak", ats_score=80, jd_match_score=40, final_score=56)
    factory(name="Strong", ats_score=60, jd_match_score=95, final_score=81)

    assert _names(search_candidates(sort="ats_desc")) == ["Weak", "Strong"]
    assert _names(search_candidates(sort="jd_desc")) == ["Strong", "Weak"]


def test_sort_newest_analysis(factory):
    c_old, _ = factory(name="Old")
    time.sleep(0.02)
    c_new, _ = factory(name="New")

    r = search_candidates(sort="newest_analysis")
    assert [row["id"] for row in r["results"]] == [c_new.id, c_old.id]


def test_sort_recently_updated(factory):
    from services.recruitment_service import update_candidate_status

    c1, _ = factory(name="First")
    c2, _ = factory(name="Second")
    time.sleep(0.02)
    update_candidate_status(c1.id, "Shortlisted")  # bumps c1.updated_at

    r = search_candidates(sort="recently_updated")
    assert [row["id"] for row in r["results"]] == [c1.id, c2.id]


# ---------------------------------------------------------------------------
# Pagination & result limits
# ---------------------------------------------------------------------------
def test_pagination(factory):
    for i in range(30):
        factory(name=f"Candidate {i}")

    page1 = search_candidates(page=1)
    assert page1["total"] == 30
    assert page1["total_pages"] == 2
    assert len(page1["results"]) == PAGE_SIZE == 25

    page2 = search_candidates(page=2)
    assert len(page2["results"]) == 5

    first_ids = {row["id"] for row in page1["results"]}
    second_ids = {row["id"] for row in page2["results"]}
    assert not (first_ids & second_ids)


# ---------------------------------------------------------------------------
# Empty states / clear filters
# ---------------------------------------------------------------------------
def test_empty_results(factory):
    factory(name="Tejas")

    r = search_candidates(query="zzzznomatch")
    assert r["results"] == []
    assert r["has_results"] is False
    assert r["has_candidates"] is True
    assert r["total_candidates"] == 1


def test_no_candidates_at_all(fresh_db):
    r = search_candidates()
    assert r["has_candidates"] is False
    assert r["results"] == []


def test_clear_filters_returns_default_pool(factory):
    factory(name="Tejas", status="Analyzed", final_score=88)
    factory(name="Maria", status="Interview", final_score=55)
    factory(name="Ana", status="Shortlisted", final_score=72)

    filtered = search_candidates(
        query="tejas", status="Rejected", min_final=60
    )
    assert filtered["total"] == 0

    # Defaults (equivalent to the page's "Clear Filters") return everything.
    cleared = search_candidates()
    assert cleared["total"] == cleared["total_candidates"] == 3
    assert len(cleared["results"]) == 3


def test_candidate_pool_scoped(factory):
    from services.recruitment_service import save_job_description

    jd_a = save_job_description("Python role", title="Backend Developer")
    factory(name="In A", status="Shortlisted", jd_id=jd_a.id)
    factory(name="Unscoped", status="Analyzed")

    pool_all = get_candidate_pool()
    assert {row["name"] for row in pool_all} == {"In A", "Unscoped"}

    pool_a = get_candidate_pool(jd_id=jd_a.id)
    assert [row["name"] for row in pool_a] == ["In A"]
    assert pool_a[0]["status"] == "Shortlisted"


# ---------------------------------------------------------------------------
# Candidate detail
# ---------------------------------------------------------------------------
def test_candidate_detail_retrieval(factory):
    cand, analysis = factory(
        name="Tejas Kumar",
        email="tejas@example.com",
        skills=["Python", "SQL"],
        final_score=82,
    )

    detail = get_candidate_details(cand.id)
    assert detail is not None
    assert detail["candidate"]["name"] == "Tejas Kumar"
    assert detail["candidate"]["email"] == "tejas@example.com"
    assert detail["status"] == "Analyzed"
    assert detail["latest_analysis"].final_score == 82.0
    assert detail["score_eligible"] is True

    activity_types = {a.activity_type for a in detail["activities"]}
    assert "RESUME_ANALYZED" in activity_types


def test_candidate_detail_unknown_returns_none(factory):
    assert get_candidate_details(99999) is None


def test_score_eligible_is_display_only(factory):
    cand, _ = factory(final_score=95)

    detail = get_candidate_details(cand.id)
    assert detail["score_eligible"] is True
    assert detail["status"] == "Analyzed"  # auto-shortlist must never happen


# ---------------------------------------------------------------------------
# Persistence after restart (requirement 20)
# ---------------------------------------------------------------------------
def test_candidate_persistence_after_restart(factory):
    from core.db import get_db_path

    cand, _ = factory(name="Durable Alice", email="alice@persistent.com")

    # Simulate an app restart: point the DB layer at the SAME file from scratch.
    db_path = str(get_db_path())
    import core.db as db

    db._engine.dispose()
    db._db_path = None
    db._engine = None
    db._session_local = None
    db._initialized = False
    db.init_db()
    assert str(db.get_db_path()) == db_path

    r = search_candidates(query="alice@persistent.com")
    assert [row["id"] for row in r["results"]] == [cand.id]

    detail = get_candidate_details(cand.id)
    assert detail["candidate"]["name"] == "Durable Alice"


def test_default_sort_is_final_score_desc():
    from services.candidate_search_service import DEFAULT_SORT, sort_key_for_label

    assert DEFAULT_SORT == "final_desc"
    assert sort_key_for_label(None) == "final_desc"
    assert sort_key_for_label("ATS Score — High to Low") == "ats_desc"
    assert sort_key_for_label("garbage label") == "final_desc"