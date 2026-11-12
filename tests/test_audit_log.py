"""Phase 4 · Step 3 — Centralized Audit Log tests.

Verifies the system/security audit trail introduced by ``services/audit_service``:

- every meaningful action (sign-in failed/successful, sign-out, account
  creation, role/status changes, candidate create/analyze/shortlist/status,
  resume versions, job descriptions) produces a canonical, safe audit event,
- the audit log is strictly separate from ``CandidateActivity`` (the
  recruiter-facing timeline),
- admin-only access is enforced at the service layer,
- filtering/search/pagination are server-side,
- audit writes are transactional with the actions they describe,
- passwords/tokens/API-key material can NEVER be written to the log.
"""

import sys
import types

import pytest

from services import (
    activity_service,
    analysis_service,
    audit_service,
    auth_service,
    candidate_service,
    recruitment_service,
    resume_version_service,
)


@pytest.fixture(autouse=True)
def _isolated_db(fresh_db):
    """Every audit test runs against its own throwaway SQLite file."""
    yield


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
def _mk_user(name, email, password=None):
    password = password or f"Str0nh-{email}"
    return auth_service.register_user(name, email, password)


def _mk_admin(email="boss@talent.iq"):
    user = _mk_user("The Boss", email)
    return auth_service.set_user_role(user["id"], "ADMIN")


def _mk_candidate(owner, label, resume_hash=None, status="New"):
    return candidate_service.create_candidate(
        resume_hash=resume_hash or f"audit-hash-{label}-{owner['id']}",
        name=f"Candidate {label}",
        resume_filename=f"{label}.pdf",
        status=status,
        created_by_user_id=owner["id"],
    )


def _events(admin, **kwargs):
    """All audit events matching kwargs, fetched as the admin."""
    return audit_service.list_audit_events(user=admin, per_page=1000, **kwargs)


def _event_text(event) -> str:
    parts = [
        event["action"],
        event["resource_type"],
        event["resource_id"] or "",
        event["description"],
    ]
    return " ".join(parts).lower()


# ---------------------------------------------------------------------------
# 1. Successful sign-in is audited
# ---------------------------------------------------------------------------
def test_login_success_creates_audit_event():
    user = _mk_user("Alice", "alice@talent.iq", "CorrectHorse1")
    status, _ = auth_service.authenticate_user("alice@talent.iq", "CorrectHorse1")
    assert status == "ok"

    admin = _mk_admin()
    logins = _events(admin, action=audit_service.AUTH_LOGIN)
    assert logins["total"] == 1
    event = logins["events"][0]
    assert event["user_id"] == user["id"]
    assert event["resource_type"] == audit_service.RESOURCE_USER
    assert event["resource_id"] == str(user["id"])
    assert event["description"] == "User signed in."


def test_registration_is_audited():
    user = _mk_user("Alice", "alice@talent.iq")
    admin = _mk_admin()
    creations = _events(admin, action=audit_service.USER_CREATED)
    assert creations["total"] == 2  # Alice + the admin bootstrap
    alice = next(e for e in creations["events"] if e["user_id"] == user["id"])
    assert alice["resource_type"] == audit_service.RESOURCE_USER
    assert alice["resource_id"] == str(user["id"])


# ---------------------------------------------------------------------------
# 2. Failed sign-in is audited WITHOUT the attempted password
# ---------------------------------------------------------------------------
def test_failed_login_audited_without_password():
    secret = "Super-Secret-Password-123"
    user = _mk_user("Bob", "bob@talent.iq", secret)
    wrong = "Definitely-Not-The-Password"
    status, _ = auth_service.authenticate_user("bob@talent.iq", wrong)
    assert status == "invalid"

    admin = _mk_admin()
    failed = _events(admin, action=audit_service.AUTH_LOGIN_FAILED)
    assert failed["total"] == 1
    event = failed["events"][0]
    assert event["user_id"] == user["id"]

    for e in _events(admin)["events"]:
        assert secret.lower() not in _event_text(e)
        assert wrong.lower() not in _event_text(e)


# ---------------------------------------------------------------------------
# 3. Sign-out is audited and clears the session
# ---------------------------------------------------------------------------
def test_logout_creates_audit_event_and_clears_session(monkeypatch):
    user = _mk_user("Chloe", "chloe@talent.iq", "Chloe-Pass1")
    auth_service.authenticate_user("chloe@talent.iq", "Chloe-Pass1")

    stub = types.SimpleNamespace()
    stub.session_state = {
        "auth_user_id": user["id"],
        "resume_file": "scratch",
        "ws_candidate_nonce": 3,
    }
    monkeypatch.setitem(sys.modules, "streamlit", stub)

    auth_service.logout_user()

    assert "auth_user_id" not in stub.session_state
    assert "resume_file" not in stub.session_state
    assert "ws_candidate_nonce" not in stub.session_state

    admin = _mk_admin()
    signouts = _events(admin, action=audit_service.AUTH_LOGOUT)
    assert signouts["total"] == 1
    assert signouts["events"][0]["user_id"] == user["id"]


# ---------------------------------------------------------------------------
# 4. Candidate creation is audited
# ---------------------------------------------------------------------------
def test_candidate_creation_audited():
    recruiter = _mk_user("Alice", "alice@talent.iq")
    cand = _mk_candidate(recruiter, "A")

    admin = _mk_admin()
    created = _events(admin, action=audit_service.CANDIDATE_CREATED)
    assert created["total"] == 1
    event = created["events"][0]
    assert event["user_id"] == recruiter["id"]
    assert event["resource_type"] == audit_service.RESOURCE_CANDIDATE
    assert event["resource_id"] == str(cand.id)


# ---------------------------------------------------------------------------
# 5. Candidate analysis is audited
# ---------------------------------------------------------------------------
def test_candidate_analysis_audited():
    recruiter = _mk_user("Alice", "alice@talent.iq")
    cand = _mk_candidate(recruiter, "A")
    version = resume_version_service.create_or_get_resume_version(
        candidate_id=cand.id, file_hash="h1", filename="a.pdf", user=recruiter
    )
    analysis_service.save_analysis(
        candidate_id=cand.id,
        resume_version_id=version.id,
        ats_score=80.0,
        jd_match_score=75.0,
        final_score=77.0,
    )

    admin = _mk_admin()
    analyzed = _events(admin, action=audit_service.CANDIDATE_ANALYZED)
    assert analyzed["total"] == 1
    event = analyzed["events"][0]
    assert event["user_id"] == recruiter["id"]
    assert event["resource_id"] == str(cand.id)


# ---------------------------------------------------------------------------
# 6. Shortlisting is audited
# ---------------------------------------------------------------------------
def test_shortlist_audited():
    recruiter = _mk_user("Alice", "alice@talent.iq")
    cand = _mk_candidate(recruiter, "A", status="Analyzed")
    recruitment_service.shortlist_candidate(cand.id, user=recruiter)

    admin = _mk_admin()
    shortlisted = _events(admin, action=audit_service.CANDIDATE_SHORTLISTED)
    assert shortlisted["total"] == 1
    event = shortlisted["events"][0]
    assert event["user_id"] == recruiter["id"]
    assert event["description"] == "Candidate moved to Shortlisted."


# ---------------------------------------------------------------------------
# 7. Generic status change is audited
# ---------------------------------------------------------------------------
def test_status_change_audited():
    recruiter = _mk_user("Alice", "alice@talent.iq")
    cand = _mk_candidate(recruiter, "A", status="Analyzed")
    recruitment_service.update_candidate_status(cand.id, "Interview", user=recruiter)

    admin = _mk_admin()
    changed = _events(admin, action=audit_service.CANDIDATE_STATUS_CHANGED)
    assert changed["total"] == 1
    event = changed["events"][0]
    assert event["user_id"] == recruiter["id"]
    assert event["resource_id"] == str(cand.id)


# ---------------------------------------------------------------------------
# 8. Resume-version creation is audited ONCE per new version
# ---------------------------------------------------------------------------
def test_resume_version_audited_once_per_version():
    recruiter = _mk_user("Alice", "alice@talent.iq")
    cand = _mk_candidate(recruiter, "A")

    v1 = resume_version_service.create_or_get_resume_version(
        candidate_id=cand.id, file_hash="h1", filename="a.pdf", user=recruiter
    )
    again = resume_version_service.create_or_get_resume_version(
        candidate_id=cand.id, file_hash="h1", filename="a.pdf", user=recruiter
    )
    assert again.id == v1.id  # dedup: same bytes → same version

    admin = _mk_admin()
    versions = _events(admin, action=audit_service.RESUME_VERSION_CREATED)
    assert versions["total"] == 1
    assert versions["events"][0]["resource_id"] == str(v1.id)


# ---------------------------------------------------------------------------
# 9. Admin activation is audited (actor recorded)
# ---------------------------------------------------------------------------
def test_admin_activation_audited():
    admin = _mk_admin()
    target = _mk_user("Deactivated", "deact@talent.iq")
    auth_service.set_user_active(target["id"], False, actor=admin)
    auth_service.set_user_active(target["id"], True, actor=admin)

    activated = _events(admin, action=audit_service.USER_ACTIVATED)
    assert activated["total"] == 1
    event = activated["events"][0]
    assert event["user_id"] == admin["id"]
    assert event["resource_type"] == audit_service.RESOURCE_USER
    assert event["resource_id"] == str(target["id"])


# ---------------------------------------------------------------------------
# 10. Admin deactivation is audited and blocks the user
# ---------------------------------------------------------------------------
def test_admin_deactivation_audited_and_blocks_login():
    admin = _mk_admin()
    target = _mk_user("Deactivated", "deact@talent.iq")

    auth_service.set_user_active(target["id"], False, actor=admin)

    deactivated = _events(admin, action=audit_service.USER_DEACTIVATED)
    assert deactivated["total"] == 1
    assert deactivated["events"][0]["user_id"] == admin["id"]

    status, _ = auth_service.authenticate_user("deact@talent.iq", "Str0nh-deact@talent.iq")
    assert status == "inactive"
    assert auth_service.get_user(target["id"]) is None


# ---------------------------------------------------------------------------
# 11. Admin role changes are audited
# ---------------------------------------------------------------------------
def test_admin_role_change_audited():
    admin = _mk_admin()
    target = _mk_user("Promoted", "promoted@talent.iq")
    updated = auth_service.set_user_role(target["id"], "ADMIN", actor=admin)
    assert updated["role"] == "ADMIN"

    changes = _events(admin, action=audit_service.USER_ROLE_CHANGED)
    # Bootstrap promotion of the admin also raises one event; the target change
    # is the second.
    assert len(changes["events"]) == 2
    change = next(e for e in changes["events"] if e["resource_id"] == str(target["id"]))
    assert change["user_id"] == admin["id"]
    assert "RECRUITER" in change["description"] and "ADMIN" in change["description"]


# ---------------------------------------------------------------------------
# 12. Recruiters cannot access the audit log or admin user-management
# ---------------------------------------------------------------------------
def test_recruiter_cannot_view_audit_or_manage_users():
    recruiter = _mk_user("Alice", "alice@talent.iq")
    admin = _mk_admin()
    target = _mk_user("Target", "target@talent.iq")

    with pytest.raises(PermissionError):
        audit_service.list_audit_events(user=recruiter)
    with pytest.raises(PermissionError):
        auth_service.set_user_role(target["id"], "ADMIN", actor=recruiter)
    with pytest.raises(PermissionError):
        auth_service.set_user_active(target["id"], False, actor=recruiter)

    # No events were created by the blocked attempts.
    assert _events(admin, action=audit_service.USER_ROLE_CHANGED)["total"] == 1  # bootstrap only
    assert auth_service.set_user_role(target["id"], "ADMIN")["role"] == "ADMIN"
    assert auth_service.get_user_by_email("target@talent.iq")["role"] == "ADMIN"


# ---------------------------------------------------------------------------
# 13. Admins CAN read the audit log
# ---------------------------------------------------------------------------
def test_admin_can_view_audit():
    recruiter = _mk_user("Alice", "alice@talent.iq")
    cand = _mk_candidate(recruiter, "A")
    admin = _mk_admin()

    result = audit_service.list_audit_events(user=admin, per_page=25)
    assert result["total"] >= 3
    assert 1 <= result["page"] <= result["total_pages"]
    assert all("user_email" in e for e in result["events"])


# ---------------------------------------------------------------------------
# 14. Server-side filtering + search
# ---------------------------------------------------------------------------
def test_filtering_and_search():
    alice = _mk_user("Alice", "alice@talent.iq")
    bob = _mk_user("Bob", "bob@talent.iq")
    cand_a = _mk_candidate(alice, "Alpha")
    cand_b = _mk_candidate(bob, "Beta")
    auth_service.authenticate_user("alice@talent.iq", "Str0nh-alice@talent.iq")
    auth_service.authenticate_user("bob@talent.iq", "Str0nh-bob@talent.iq")
    admin = _mk_admin()

    by_user = _events(admin, user_id=alice["id"])
    assert by_user["total"] >= 3
    assert all(e["user_id"] == alice["id"] for e in by_user["events"])

    by_action = _events(admin, action=audit_service.AUTH_LOGIN)
    assert by_action["total"] == 2

    by_resource = _events(admin, resource_type=audit_service.RESOURCE_CANDIDATE)
    assert by_resource["total"] == 2
    ids = {e["resource_id"] for e in by_resource["events"]}
    assert ids == {str(cand_a.id), str(cand_b.id)}

    by_resource_id = _events(admin, resource_type=audit_service.RESOURCE_CANDIDATE, resource_id=cand_a.id)
    assert by_resource_id["total"] == 1

    # Search matches the user NAME case-insensitively via the join.
    by_search = _events(admin, search="alice")
    assert by_search["total"] >= 3
    assert all(e["user_id"] == alice["id"] for e in by_search["events"])

    # Date-range: today covers every event (end-of-day inclusive).
    from datetime import date, timedelta

    today = _events(admin, start_date=date.today(), end_date=date.today())
    assert today["total"] == _events(admin)["total"]

    tomorrow = _events(
        admin, start_date=date.today() + timedelta(days=1), end_date=date.today() + timedelta(days=1)
    )
    assert tomorrow["total"] == 0


# ---------------------------------------------------------------------------
# 15. Pagination (never loads the whole table)
# ---------------------------------------------------------------------------
def test_pagination():
    admin = _mk_admin()
    baseline = _events(admin)["total"]  # admin registration + role bootstrap
    for i in range(35):
        audit_service.record_audit_event(
            None,
            audit_service.AUTH_LOGIN,
            audit_service.RESOURCE_USER,
            None,
            f"Bulk event {i}.",  # no secrets; keys are numeric
        )

    page1 = audit_service.list_audit_events(user=admin, page=1, per_page=10)
    assert page1["total"] == baseline + 35
    assert len(page1["events"]) == 10
    assert page1["total_pages"] == 4

    page2 = audit_service.list_audit_events(user=admin, page=2, per_page=10)
    assert len(page2["events"]) == 10
    assert {e["id"] for e in page2["events"]} != {e["id"] for e in page1["events"]}

    page4 = audit_service.list_audit_events(user=admin, page=4, per_page=10)
    assert len(page4["events"]) == 7  # 5 bulk + admin's own 2 bootstrap events


# ---------------------------------------------------------------------------
# 16. CandidateActivity and AuditLog are strictly separate
# ---------------------------------------------------------------------------
def test_activity_log_and_audit_log_are_separate():
    recruiter = _mk_user("Alice", "alice@talent.iq")
    cand = _mk_candidate(recruiter, "A", status="Analyzed")
    recruitment_service.update_candidate_status(cand.id, "Interview", user=recruiter)

    materials = activity_service.get_candidate_activity(cand.id, user=recruiter)
    assert len(materials) == 1
    assert materials[0].activity_type == activity_service.activity_type_for_status("Interview")

    admin = _mk_admin()
    status_events = _events(admin, action=audit_service.CANDIDATE_STATUS_CHANGED)
    assert len(status_events["events"]) == 1

    # Audit rows never surface recruiter-facing activity types.
    all_audit = _events(admin)
    activity_types = {
        "SHORTLISTED",
        "RESUME_ANALYZED",
        "REMOVED_FROM_SHORTLIST",
        "MOVED_TO_INTERVIEW",
        "MARKED_SELECTED",
        "MARKED_REJECTED",
    }
    assert {e["action"] for e in all_audit["events"]}.isdisjoint(activity_types)


# ---------------------------------------------------------------------------
# 17. Status change + audit are transactional (failure rolls everything back)
# ---------------------------------------------------------------------------
def test_status_change_and_audit_are_transactional(monkeypatch):
    recruiter = _mk_user("Alice", "alice@talent.iq")
    cand = _mk_candidate(recruiter, "A")  # status "New"
    admin = _mk_admin()

    def _failing_audit(*args, **kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(recruitment_service.audit_service, "record_audit_event", _failing_audit)

    with pytest.raises(RuntimeError):
        recruitment_service.update_candidate_status(cand.id, "Interview", user=recruiter)

    assert _events(admin, action=audit_service.CANDIDATE_STATUS_CHANGED)["total"] == 0
    monkeypatch.undo()

    # Everything rolled back together: status, history, activity, audit.
    assert recruitment_service.get_candidate_status(cand.id, user=recruiter) == "New"
    assert recruitment_service.get_status_history(cand.id, user=recruiter) == []
    assert activity_service.get_candidate_activity(cand.id, user=recruiter) == []


# ---------------------------------------------------------------------------
# 18. Secret material can NEVER be written to the audit log
# ---------------------------------------------------------------------------
def test_secrets_never_written_to_audit(monkeypatch):
    secret = "Secret-Material-XYZ-99"
    user = _mk_user("Sensitive", "sensitive@talent.iq", secret)

    admin = _mk_admin()

    # Direct attempts to log sensitive material are rejected.
    with pytest.raises(audit_service.AuditLogError):
        audit_service.record_audit_event(
            admin["id"],
            audit_service.USER_ACTIVATED,
            audit_service.RESOURCE_USER,
            admin["id"],
            f"Only the holder knows the secret: {secret}",
        )
    with pytest.raises(audit_service.AuditLogError):
        audit_service.record_audit_event(
            admin["id"],
            audit_service.AUTH_LOGIN,
            audit_service.RESOURCE_USER,
            user["id"],
            "Sign in with credentials.",
            metadata={"session_token": "abc-123"},
        )

    # A failed sign-in must not leak the attempted password either.
    wrong = "Wrong-Password-987"
    status, _ = auth_service.authenticate_user("sensitive@talent.iq", wrong)
    assert status == "invalid"

    for e in _events(admin)["events"]:
        text = _event_text(e)
        assert secret.lower() not in text
        assert wrong.lower() not in text

    # Defense-in-depth: scan the raw rows for any forbidden marker.
    from core.db import session_scope
    from core.models import AuditLog

    with session_scope() as session:
        rows = session.query(AuditLog).all()
        assert rows  # events were recorded, but never with secrets
        forbidden = ("password", "api_key", "secret", "token", "authorization")
        for row in rows:
            description = (row.description or "").lower()
            metadata = str(row.metadata_json or "").lower()
            for marker in forbidden:
                assert marker not in description
                assert marker not in metadata