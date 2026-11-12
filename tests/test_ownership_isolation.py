"""Phase 4 — Ownership & recruiter isolation tests.

Verifies that data-ownership scoping (``created_by_user_id``) keeps each
recruiter's candidates / job descriptions isolated at the service layer while
legacy (``NULL`` owner) records stay shared and the ADMIN sees everything.

Two recruiters (A and B) plus one admin own distinct candidates and JDs; a
legacy candidate/JD is seeded without an owner. Most assertions come in
"A/B mirrored" pairs so isolation holds in BOTH directions.
"""

import pytest

from services import (
    auth_service,
    candidate_service,
    recruitment_service,
    resume_version_service,
    analysis_service,
    activity_service,
    candidate_search_service,
    analytics_service,
)
from core import models
from core.db import session_scope


@pytest.fixture(autouse=True)
def _isolated_db(fresh_db):
    """Every ownership test runs against its own throwaway SQLite file."""
    yield


# ---------------------------------------------------------------------------
# Shared seed helpers
# ---------------------------------------------------------------------------
def _mk_user(name, email):
    return auth_service.register_user(name, email, f"secret-{email}")


def _mk_candidate(owner_user, resume_label, *, final=70.0, jd_row=None, skills=None):
    """Owned candidate + resume version + analysis (+ JD when given)."""
    resume_hash = f"test-hash-{resume_label}_{owner_user['email']}"
    cand = candidate_service.create_candidate(
        resume_hash=resume_hash,
        name=f"Candidate {resume_label}",
        resume_filename=f"{resume_label}.pdf",
        created_by_user_id=owner_user["id"],
    )
    version = resume_version_service.create_or_get_resume_version(
        candidate_id=cand.id,
        file_hash=resume_hash,
        filename=f"{resume_label}.pdf",
        user=owner_user,
    )
    analysis_service.save_analysis(
        candidate_id=cand.id,
        jd_id=jd_row["id"] if jd_row is not None else None,
        resume_version_id=version.id,
        ats_score=60.0,
        jd_match_score=64.0,
        final_score=final,
        matched_skills=list(skills or ["Python"]),
        missing_required_skills=[],
        missing_preferred_skills=[],
    )
    return cand.id


def _mk_legacy_candidate(resume_label="legacy"):
    resume_hash = f"test-hash-{resume_label}-legacy"
    cand = candidate_service.create_candidate(
        resume_hash=resume_hash,
        name=f"Legacy {resume_label}",
        resume_filename=f"{resume_label}-legacy.pdf",
        created_by_user_id=None,
    )
    version = resume_version_service.create_or_get_resume_version(
        candidate_id=cand.id,
        file_hash=resume_hash,
        filename=f"{resume_label}-legacy.pdf",
        user=None,
    )
    analysis_service.save_analysis(
        candidate_id=cand.id,
        resume_version_id=version.id,
        ats_score=55.0,
        jd_match_score=58.0,
        final_score=57.0,
        matched_skills=["LegacySkill"],
        missing_required_skills=[],
        missing_preferred_skills=[],
    )
    return cand.id


@pytest.fixture()
def fixture_a_b_admin():
    """Recruiter A, Recruiter B, Admin, and 3 candidates (A, B, legacy)."""
    a = _mk_user("Alice", "alice@talent.iq")
    b = _mk_user("Bob", "bob@talent.iq")
    admin = _mk_user("Admin", "admin@talent.iq")
    auth_service.set_user_role(admin["id"], "ADMIN")
    admin = auth_service.get_user(admin["id"])

    a_cand = _mk_candidate(a, "alice-resume")
    b_cand = _mk_candidate(b, "bob-resume")
    legacy_cand = _mk_legacy_candidate()

    return {
        "a": a,
        "b": b,
        "admin": admin,
        "a_cand": a_cand,
        "b_cand": b_cand,
        "legacy_cand": legacy_cand,
    }


def _result_ids(response):
    return {row["id"] for row in response["results"]}


class TestSearchPoolIsolation:
    def test_recruiter_a_sees_own_and_legacy_only(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        resp = candidate_search_service.search_candidates(user=fx["a"])
        ids = _result_ids(resp)
        assert fx["a_cand"] in ids
        assert fx["legacy_cand"] in ids
        assert fx["b_cand"] not in ids
        assert resp["total_candidates"] == 2

    def test_recruiter_b_sees_own_and_legacy_only(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        resp = candidate_search_service.search_candidates(user=fx["b"])
        ids = _result_ids(resp)
        assert fx["b_cand"] in ids
        assert fx["legacy_cand"] in ids
        assert fx["a_cand"] not in ids
        assert resp["total_candidates"] == 2

    def test_admin_sees_everything(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        resp = candidate_search_service.search_candidates(user=fx["admin"])
        ids = _result_ids(resp)
        assert fx["a_cand"] in ids and fx["b_cand"] in ids and fx["legacy_cand"] in ids
        assert resp["total_candidates"] == 3

    def test_unscoped_caller_sees_everything(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        resp = candidate_search_service.search_candidates(user=None)
        assert resp["total_candidates"] == 3


class TestDetailIsolation:
    def test_owners_can_read_their_detail(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        a_detail = candidate_search_service.get_candidate_details(fx["a_cand"], user=fx["a"])
        assert a_detail is not None
        assert a_detail["candidate"]["name"] == "Candidate alice-resume"
        assert a_detail["latest_analysis"] is not None

    def test_b_cannot_read_a_detail_and_vice_versa(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        assert candidate_search_service.get_candidate_details(fx["a_cand"], user=fx["b"]) is None
        assert candidate_search_service.get_candidate_details(fx["b_cand"], user=fx["a"]) is None

    def test_legacy_candidate_visible_to_everyone(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        for user in (fx["a"], fx["b"], fx["admin"], None):
            detail = candidate_search_service.get_candidate_details(
                fx["legacy_cand"], user=user
            )
            assert detail is not None

    def test_admin_can_read_both(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        assert candidate_search_service.get_candidate_details(fx["a_cand"], user=fx["admin"]) is not None
        assert candidate_search_service.get_candidate_details(fx["b_cand"], user=fx["admin"]) is not None


class TestStatusMutationIsolation:
    def test_b_cannot_update_a_candidate(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        with pytest.raises(PermissionError):
            recruitment_service.update_candidate_status(
                fx["a_cand"], "Shortlisted", user=fx["b"]
            )
        status = recruitment_service.get_candidate_status(fx["a_cand"], user=fx["a"])
        assert status == "New"

    def test_owner_can_update(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        recruitment_service.update_candidate_status(
            fx["a_cand"], "Shortlisted", user=fx["a"]
        )
        assert (
            recruitment_service.get_candidate_status(fx["a_cand"], user=fx["a"])
            == "Shortlisted"
        )

    def test_legacy_candidate_mutable_by_any_recruiter(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        recruitment_service.update_candidate_status(
            fx["legacy_cand"], "Interview", user=fx["b"]
        )
        assert (
            recruitment_service.get_candidate_status(fx["legacy_cand"], user=fx["b"])
            == "Interview"
        )


class TestActivityIsolation:
    def test_b_sees_no_activity_for_a_candidate(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        assert activity_service.get_candidate_activity(fx["a_cand"], user=fx["b"]) == []
        a_activities = activity_service.get_candidate_activity(fx["a_cand"], user=fx["a"])
        assert len(a_activities) >= 1  # RESUME_ANALYZED logged at seed time

    def test_admin_sees_activity(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        assert len(activity_service.get_candidate_activity(fx["b_cand"], user=fx["admin"])) >= 1


class TestResumeVersionIsolation:
    def test_versions_invisible_across_recruiters(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        assert resume_version_service.get_resume_versions(fx["a_cand"], user=fx["b"]) == []
        assert len(resume_version_service.get_resume_versions(fx["a_cand"], user=fx["a"])) >= 1

    def test_cannot_create_version_for_other_owners_candidate(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        with pytest.raises(PermissionError):
            resume_version_service.create_or_get_resume_version(
                candidate_id=fx["a_cand"],
                file_hash="another-hash",
                filename="hijack.pdf",
                user=fx["b"],
            )


class TestWorkspaceIsolation:
    def test_workspace_detail_isolated(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        a_ws = candidate_search_service.get_workspace_detail(fx["a_cand"], user=fx["a"])
        assert a_ws is not None
        assert "jd_contexts" in a_ws
        assert candidate_search_service.get_workspace_detail(fx["a_cand"], user=fx["b"]) is None

    def test_jd_contexts_isolated(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        ctx_b = candidate_search_service.get_candidate_jd_contexts(fx["a_cand"], user=fx["b"])
        assert ctx_b["contexts"] == []
        ctx_a = candidate_search_service.get_candidate_jd_contexts(fx["a_cand"], user=fx["a"])
        assert isinstance(ctx_a["contexts"], list)

    def test_analysis_for_context_isolated(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        assert (
            candidate_search_service.get_analysis_for_context(
                fx["a_cand"], None, None, user=fx["b"]
            )
            is None
        )

    def test_skill_options_scoped_to_visible_pool(self):
        a = _mk_user("Alice4", "alice4@talent.iq")
        b = _mk_user("Bob4", "bob4@talent.iq")
        _mk_candidate(a, "alice4-resume", skills=["AliceSkill"])
        _mk_candidate(b, "bob4-resume", skills=["BobSkill"])
        _mk_legacy_candidate("legacy4")

        a_skills = candidate_search_service.get_skill_options(user=a)
        b_skills = candidate_search_service.get_skill_options(user=b)
        assert "AliceSkill" in a_skills
        assert "LegacySkill" in a_skills
        assert "BobSkill" not in a_skills
        assert "BobSkill" in b_skills
        assert "LegacySkill" in b_skills
        assert "AliceSkill" not in b_skills

    def test_legacy_free_floating_visible(self):
        a = _mk_user("Alice5", "alice5@talent.iq")
        _mk_candidate(a, "alice5-resume")
        resp = candidate_search_service.search_candidates(user=a)
        assert resp["total_candidates"] == 1

    def test_candidate_pool_isolation(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        pool_a = candidate_search_service.get_candidate_pool(user=fx["a"])
        assert fx["a_cand"] in [c["id"] for c in pool_a]
        assert fx["b_cand"] not in [c["id"] for c in pool_a]
        pool_b = candidate_search_service.get_candidate_pool(user=fx["b"])
        assert fx["b_cand"] in [c["id"] for c in pool_b]
        assert fx["a_cand"] not in [c["id"] for c in pool_b]


class TestAnalyticsIsolation:
    def test_pipeline_counts_isolated(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        a_metrics = analytics_service.get_pipeline_metrics(user=fx["a"])
        b_metrics = analytics_service.get_pipeline_metrics(user=fx["b"])
        admin_metrics = analytics_service.get_pipeline_metrics(user=fx["admin"])
        assert a_metrics["total_candidates"] == 2  # A + legacy
        assert b_metrics["total_candidates"] == 2  # B + legacy
        assert admin_metrics["total_candidates"] == 3

    def test_score_metrics_isolated(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        a_scores = analytics_service.get_score_metrics(user=fx["a"])
        b_scores = analytics_service.get_score_metrics(user=fx["b"])
        assert a_scores["candidate_count"] == 2
        assert b_scores["candidate_count"] == 2

    def test_statistics_isolated(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        assert analytics_service.get_candidate_statistics(user=fx["a"])["total_candidates"] == 2
        assert analytics_service.get_candidate_statistics(user=fx["b"])["total_candidates"] == 2
        assert analytics_service.get_candidate_statistics(user=fx["admin"])["total_candidates"] == 3


class TestJobDescriptionIsolation:
    def test_recruiters_only_see_their_jds_plus_legacy(self):
        a = _mk_user("Alice2", "alice2@talent.iq")
        b = _mk_user("Bob2", "bob2@talent.iq")

        jd_a = recruitment_service.save_job_description(
            "Software Engineer A-specific requirements", created_by_user_id=a["id"]
        )
        jd_b = recruitment_service.save_job_description(
            "Data Scientist B-specific requirements", created_by_user_id=b["id"]
        )
        jd_legacy = recruitment_service.save_job_description(
            "Legacy Public JD description", created_by_user_id=None
        )

        a_ids = {j.id for j in recruitment_service.get_job_descriptions(user=a)}
        b_ids = {j.id for j in recruitment_service.get_job_descriptions(user=b)}
        assert jd_a.id in a_ids and jd_legacy.id in a_ids
        assert jd_b.id not in a_ids
        assert jd_b.id in b_ids and jd_legacy.id in b_ids
        assert jd_a.id not in b_ids

        admin = _mk_user("Admin2", "admin2@talent.iq")
        auth_service.set_user_role(admin["id"], "ADMIN")
        admin = auth_service.get_user(admin["id"])
        all_ids = {j.id for j in recruitment_service.get_job_descriptions(user=admin)}
        assert {jd_a.id, jd_b.id, jd_legacy.id} <= all_ids

    def test_cannot_create_jd_for_other_owner(self):
        a = _mk_user("Alice3", "alice3@talent.iq")
        b = _mk_user("Bob3", "bob3@talent.iq")
        _mk_candidate(a, "alice3-resume")
        _mk_candidate(b, "bob3-resume")
        b_jd = recruitment_service.save_job_description(
            "JD owned by B", created_by_user_id=b["id"]
        )
        a_options = analytics_service.get_job_description_options(user=a)
        assert b_jd.id not in {j["id"] for j in a_options}

    def test_identical_text_gives_each_recruiter_their_own_row(self):
        """Regression: a global UNIQUE on ``content_hash`` made B's identical
        paste raise IntegrityError. Uniqueness is owner-scoped instead."""
        a = _mk_user("Alice6", "alice6@talent.iq")
        b = _mk_user("Bob6", "bob6@talent.iq")
        text = "Identical job description pasted by two recruiters"

        jd_a = recruitment_service.save_job_description(
            text, created_by_user_id=a["id"]
        )
        jd_b = recruitment_service.save_job_description(
            text, created_by_user_id=b["id"]
        )

        assert jd_b.id != jd_a.id
        assert jd_a.created_by_user_id == a["id"]
        assert jd_b.created_by_user_id == b["id"]

        # Each recruiter still sees only their own copy of the shared text.
        a_ids = {j.id for j in recruitment_service.get_job_descriptions(user=a)}
        b_ids = {j.id for j in recruitment_service.get_job_descriptions(user=b)}
        assert a_ids == {jd_a.id}
        assert b_ids == {jd_b.id}

    def test_same_owner_reuses_row_instead_of_duplicating(self):
        a = _mk_user("Alice7", "alice7@talent.iq")
        text = "Recruiter re-pastes their own JD text"
        first = recruitment_service.save_job_description(
            text, created_by_user_id=a["id"]
        )
        second = recruitment_service.save_job_description(
            text, created_by_user_id=a["id"]
        )
        assert second.id == first.id
        assert len(recruitment_service.get_job_descriptions(user=a)) == 1


class TestActorRecording:
    def test_status_change_records_actor(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        recruitment_service.update_candidate_status(
            fx["a_cand"], "Interview", user=fx["a"]
        )
        with session_scope() as session:
            latest = (
                session.query(models.CandidateStatusHistory)
                .filter(
                    models.CandidateStatusHistory.candidate_id == fx["a_cand"]
                )
                .order_by(models.CandidateStatusHistory.created_at.desc())
                .first()
            )
        assert latest.actor_user_id == fx["a"]["id"]

    def test_activity_records_actor(self, fixture_a_b_admin):
        fx = fixture_a_b_admin
        recruitment_service.update_candidate_status(
            fx["legacy_cand"], "Selected", user=fx["b"]
        )
        with session_scope() as session:
            actor_ids = {
                a.actor_user_id
                for a in session.query(models.CandidateActivity)
                .filter(
                    models.CandidateActivity.candidate_id == fx["legacy_cand"]
                )
                .all()
            }
        assert fx["b"]["id"] in actor_ids
        assert any(a is not None for a in actor_ids)
        # The RESUME_ANALYZED seed events have no actor — mixed set is fine.
        assert None in actor_ids