"""Recruiter actions from search results — service-level tests.

Verifies that status changes made from Candidate Intelligence use the existing
``recruitment_service`` (a single status-management system), that every change
creates the appropriate activity history, and that "Score-Eligible" never
mutates a status by itself.
"""

from services.activity_service import get_candidate_activity
from services.candidate_search_service import get_candidate_details, search_candidates
from services.recruitment_service import (
    get_candidate_status,
    get_status_history,
    remove_from_shortlist,
    shortlist_candidate,
    update_candidate_status,
)


def _first_result_id(factory, query):
    factory(name=query, status="Analyzed")
    response = search_candidates(query=query)
    assert response["total"] == 1
    return response["results"][0]["id"]


# ---------------------------------------------------------------------------
# Status update from search results (requirement 15)
# ---------------------------------------------------------------------------
def test_status_update_from_search_results(factory):
    candidate_id = _first_result_id(factory, "Tejas Kumar")

    assert get_candidate_status(candidate_id) == "Analyzed"

    update_candidate_status(candidate_id, "Interview")

    assert get_candidate_status(candidate_id) == "Interview"

    history = get_status_history(candidate_id)
    assert history[0].previous_status == "Analyzed"
    assert history[0].status == "Interview"


def test_shortlist_from_search_results(factory):
    candidate_id = _first_result_id(factory, "Maria Silva")

    shortlist_candidate(candidate_id)

    assert get_candidate_status(candidate_id) == "Shortlisted"


def test_reject_from_search_results(factory):
    candidate_id = _first_result_id(factory, "Ana Pereira")

    update_candidate_status(candidate_id, "Rejected")

    assert get_candidate_status(candidate_id) == "Rejected"


def test_move_back_to_analyzed(factory):
    candidate_id = _first_result_id(factory, "Joao")

    update_candidate_status(candidate_id, "Selected")
    update_candidate_status(candidate_id, "Analyzed")

    assert get_candidate_status(candidate_id) == "Analyzed"


def test_remove_from_shortlist(factory):
    candidate_id = _first_result_id(factory, "Pedro")

    shortlist_candidate(candidate_id)
    remove_from_shortlist(candidate_id)

    assert get_candidate_status(candidate_id) == "Analyzed"
    activity_types = {a.activity_type for a in get_candidate_activity(candidate_id)}
    assert "SHORTLISTED" in activity_types
    assert "REMOVED_FROM_SHORTLIST" in activity_types


# ---------------------------------------------------------------------------
# Activity creation after status update (requirement 16)
# ---------------------------------------------------------------------------
def test_activity_created_after_status_update(factory):
    candidate_id = _first_result_id(factory, "Rita")

    update_candidate_status(candidate_id, "Shortlisted")
    update_candidate_status(candidate_id, "Interview")
    update_candidate_status(candidate_id, "Selected")

    activities = get_candidate_activity(candidate_id)
    by_type = {a.activity_type: a for a in activities}

    assert by_type["SHORTLISTED"].previous_status == "Analyzed"
    assert by_type["SHORTLISTED"].new_status == "Shortlisted"
    assert by_type["MOVED_TO_INTERVIEW"].new_status == "Interview"
    assert by_type["MARKED_SELECTED"].new_status == "Selected"


def test_status_update_is_atomic_with_activity(factory):
    candidate_id = _first_result_id(factory, "Bruno")

    update_candidate_status(candidate_id, "Rejected")

    activities = get_candidate_activity(candidate_id)
    rejections = [a for a in activities if a.activity_type == "MARKED_REJECTED"]
    assert len(rejections) == 1
    assert rejections[0].previous_status == "Analyzed"
    assert rejections[0].new_status == "Rejected"


def test_noop_status_update_records_nothing(factory):
    candidate_id = _first_result_id(factory, "Carla")

    count_before = len(get_candidate_activity(candidate_id))
    update_candidate_status(candidate_id, "Analyzed")  # already Analyzed
    count_after = len(get_candidate_activity(candidate_id))

    assert count_after == count_before


# ---------------------------------------------------------------------------
# Score-Eligible vs Shortlisted are independent (requirement 18)
# ---------------------------------------------------------------------------
def test_score_eligible_never_auto_shortlists(factory):
    cand, _ = factory(final_score=92)

    assert get_candidate_status(cand.id) == "Analyzed"
    assert get_candidate_details(cand.id)["score_eligible"] is True


def test_shortlisted_cannot_be_derived_from_scores(factory):
    """A low-scoring candidate can still be manually shortlisted; a high-scoring
    one is not shortlisted until a recruiter says so."""
    cand, _ = factory(final_score=38)

    shortlist_candidate(cand.id)

    assert get_candidate_status(cand.id) == "Shortlisted"
    assert get_candidate_details(cand.id)["score_eligible"] is False


# ---------------------------------------------------------------------------
# Detail retrieval from candidate data (requirement 14)
# ---------------------------------------------------------------------------
def test_detail_includes_latest_analysis_and_timeline(factory):
    cand, analysis = factory(
        name="Detail Case",
        skills=["Python"],
        ats_score=80,
        jd_match_score=70,
        final_score=74,
    )
    update_candidate_status(cand.id, "Shortlisted")

    detail = get_candidate_details(cand.id)
    assert detail["candidate"] is not None
    assert detail["latest_analysis"].ats_score == 80.0
    assert detail["latest_analysis"].jd_match_score == 70.0
    assert detail["latest_analysis"].final_score == 74.0
    assert sorted(a.activity_type for a in detail["activities"]) == [
        "RESUME_ANALYZED",
        "SHORTLISTED",
    ]


def test_persistence_survives_reconnect(factory):
    """Seeded through search-driven actions, data survives a reconnect.

    This is the same scenario as the page's refresh (every rerun re-opens the
    database): nothing is held in session state, so a fresh connection still
    sees the updated status and history.
    """
    from core.db import get_db_path

    candidate_id = _first_result_id(factory, "Durable")
    update_candidate_status(candidate_id, "Interview")

    db_path = str(get_db_path())
    import core.db as db

    db._engine.dispose()
    db._db_path = None
    db._engine = None
    db._session_local = None
    db._initialized = False
    db.init_db()
    assert str(db.get_db_path()) == db_path

    assert get_candidate_status(candidate_id) == "Interview"
    assert get_status_history(candidate_id)[0].status == "Interview"

    r = search_candidates(status="Interview")
    assert [row["id"] for row in r["results"]] == [candidate_id]