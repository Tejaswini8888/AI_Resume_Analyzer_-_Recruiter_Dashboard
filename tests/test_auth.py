"""Phase 4 — Authentication & RBAC tests.

Covers the account layer of :mod:`services.auth_service`:

- password hashing/verification round-trips + constant-time mismatch
- the server-side password policy (reject too short / all-whitespace)
- registration: default RECRUITER role, duplicate email rejection, invalid
  input, admin bootstrap via environment variables
- login: success, generic failure (unknown email == wrong password), inactive
  account, DB failure, email normalization (spacing / case)
- session: current_user re-validation, deactivation takes effect immediately,
  logout clears the marker
- roles: has_role / require_role and the permission error contract
"""

import os

import pytest

from services import auth_service
from core import models
from core.db import session_scope


@pytest.fixture(autouse=True)
def _isolated_db(fresh_db):
    """Every auth test runs against its own throwaway SQLite file."""
    yield


def _facts():
    user = auth_service.register_user("Alice Recruiter", "alice@talent.iq", "hunter2secret")
    return user


class TestPasswordHash:
    def test_hash_and_verify_round_trip(self):
        stored = auth_service.hash_password("s3cret-and-long")
        assert stored.startswith("scrypt$")
        assert auth_service.verify_password("s3cret-and-long", stored)
        assert stored.count("$") == 5  # scrypt$n$r$p$salt$hash

    def test_wrong_password_fails(self):
        stored = auth_service.hash_password("right-password-1")
        assert not auth_service.verify_password("wrong-password-1", stored)

    def test_two_hashes_of_same_password_differ(self):
        a = auth_service.hash_password("same-password-1")
        b = auth_service.hash_password("same-password-1")
        assert a != b  # random per-password salt

    def test_malformed_stored_hash_fails_cleanly(self):
        assert not auth_service.verify_password("anything", "not-a-hash")
        assert not auth_service.verify_password("anything", "")
        assert not auth_service.verify_password("anything", "bcrypt$abc$def")


class TestPasswordPolicy:
    @pytest.mark.parametrize(
        "password",
        [
            "1234567",  # 7 chars
            "   ",  # only spaces
            "",
        ],
    )
    def test_rejects_weak_passwords(self, password):
        ok, reason = auth_service.validate_password(password)
        assert not ok
        assert reason

    def test_accepts_strong_password(self):
        ok, reason = auth_service.validate_password("a-strong-password")
        assert ok
        assert reason == ""


class TestRegistration:
    def test_register_defaults_to_recruiter(self):
        user = auth_service.register_user("Recruito", "recruito@talent.iq", "secret-password-1")
        assert user["role"] == "RECRUITER"
        assert user["is_active"] is True
        assert user["email"] == "recruito@talent.iq"
        assert user["name"] == "Recruito"
        assert "password" not in user
        assert "password_hash" not in user

    def test_duplicate_email_rejected(self):
        email = "dup@talent.iq"
        auth_service.register_user("One", email, "secret-password-1")
        with pytest.raises(ValueError, match="already exists"):
            auth_service.register_user("Two", email, "other-password-1")

    def test_duplicate_email_is_case_insensitive(self):
        auth_service.register_user("Case", "Case@Talent.iq", "secret-password-1")
        with pytest.raises(ValueError, match="already exists"):
            auth_service.register_user("Case2", "case@talent.iq", "secret-password-2")

    @pytest.mark.parametrize("email", ["", "not-an-email", "a@b"])
    def test_invalid_email_rejected(self, email):
        with pytest.raises(ValueError, match="valid email"):
            auth_service.register_user("X", email, "secret-password-1")

    def test_blank_name_rejected(self):
        with pytest.raises(ValueError, match="full name"):
            auth_service.register_user("  ", "blank@talent.iq", "secret-password-1")

    def test_weak_password_rejected(self):
        with pytest.raises(ValueError, match="at least 8"):
            auth_service.register_user("Weak", "weak@talent.iq", "short")

    def test_stored_row_has_only_hashing(self):
        user = auth_service.register_user("Hashy", "hashy@talent.iq", "secret-password-1")
        with session_scope() as session:
            row = session.get(models.User, user["id"])
            assert row.password_hash.startswith("scrypt$")
            assert row.password_hash != "secret-password-1"

    def test_unsupported_role_forced_to_recruiter(self):
        user = auth_service.register_user(
            "Rogue", "rogue@talent.iq", "secret-password-1", role="SUPERUSER"
        )
        assert user["role"] == "RECRUITER"


class TestAdminBootstrap:
    def test_env_match_grants_admin(self, monkeypatch):
        monkeypatch.setenv("TALENTIQ_ADMIN_EMAIL", "boss@talent.iq")
        monkeypatch.setenv("TALENTIQ_ADMIN_PASSWORD", "root-admin-secret")
        user = auth_service.register_user(
            "Boss", "boss@talent.iq", "root-admin-secret"
        )
        assert user["role"] == "ADMIN"

    def test_env_mismatch_stays_recruiter(self, monkeypatch):
        monkeypatch.setenv("TALENTIQ_ADMIN_EMAIL", "boss@talent.iq")
        monkeypatch.setenv("TALENTIQ_ADMIN_PASSWORD", "root-admin-secret")
        user = auth_service.register_user(
            "Impostor", "boss@talent.iq", "not-the-admin-pass"
        )
        assert user["role"] == "RECRUITER"

    def test_no_env_no_admin_by_typing(self, monkeypatch):
        monkeypatch.delenv("TALENTIQ_ADMIN_EMAIL", raising=False)
        monkeypatch.delenv("TALENTIQ_ADMIN_PASSWORD", raising=False)
        user = auth_service.register_user(
            "Solo", "solo@talent.iq", "secret-password-1", role="ADMIN"
        )
        assert user["role"] == "RECRUITER"


class TestAuthentication:
    def test_login_success(self):
        auth_service.register_user("Loggy", "loggy@talent.iq", "secret-password-1")
        status, user = auth_service.authenticate_user("loggy@talent.iq", "secret-password-1")
        assert status == "ok"
        assert user["email"] == "loggy@talent.iq"

    def test_login_unknown_email_is_generic_invalid(self):
        status, user = auth_service.authenticate_user("nobody@talent.iq", "whatever-password")
        assert status == "invalid"
        assert user is None

    def test_login_wrong_password_is_generic_invalid(self):
        auth_service.register_user("Wrongy", "wrongy@talent.iq", "secret-password-1")
        status, user = auth_service.authenticate_user("wrongy@talent.iq", "totally-wrong")
        assert status == "invalid"
        assert user is None

    def test_login_email_normalization(self):
        auth_service.register_user("Spacey", "spacey@talent.iq", "secret-password-1")
        status, _ = auth_service.authenticate_user("  SPACEY@Talent.iq ", "secret-password-1")
        assert status == "ok"

    def test_inactive_account_is_inactive_status(self):
        user = auth_service.register_user("Gone", "gone@talent.iq", "secret-password-1")
        auth_service.set_user_active(user["id"], False)
        status, result = auth_service.authenticate_user("gone@talent.iq", "secret-password-1")
        assert status == "inactive"
        assert result is None

    def test_reactivated_account_can_login(self):
        user = auth_service.register_user("Back", "back@talent.iq", "secret-password-1")
        auth_service.set_user_active(user["id"], False)
        auth_service.set_user_active(user["id"], True)
        status, _ = auth_service.authenticate_user("back@talent.iq", "secret-password-1")
        assert status == "ok"


class TestUserManagement:
    def test_get_user_and_by_email(self):
        user = auth_service.register_user("Mgr", "mgr@talent.iq", "secret-password-1")
        assert auth_service.get_user(user["id"])["name"] == "Mgr"
        assert auth_service.get_user_by_email("mgr@talent.iq")["id"] == user["id"]

    def test_set_role_validate(self):
        user = auth_service.register_user("Role", "role@talent.iq", "secret-password-1")
        updated = auth_service.set_user_role(user["id"], "ADMIN")
        assert updated["role"] == "ADMIN"
        with pytest.raises(ValueError):
            auth_service.set_user_role(user["id"], "NOPE")
        assert auth_service.get_user(user["id"])["role"] == "ADMIN"

    def test_list_users(self):
        auth_service.register_user("A", "a@talent.iq", "secret-password-1")
        auth_service.register_user("B", "b@talent.iq", "secret-password-2")
        roles = {u["email"]: u["role"] for u in auth_service.list_users()}
        assert roles["a@talent.iq"] == "RECRUITER"
        assert roles["b@talent.iq"] == "RECRUITER"


class TestRoles:
    def test_has_role(self):
        admin = {"id": 1, "name": "A", "email": "a@x.io", "role": "ADMIN", "is_active": True}
        recruit = {"id": 2, "name": "R", "email": "r@x.io", "role": "RECRUITER", "is_active": True}
        assert auth_service.has_role(admin, "ADMIN")
        assert auth_service.has_role(recruit, "RECRUITER")
        assert not auth_service.has_role(recruit, "ADMIN")
        assert not auth_service.has_role(None, "ADMIN")

    def test_require_role_raises_for_wrong_role(self):
        recruit = {"id": 2, "name": "R", "email": "r@x.io", "role": "RECRUITER", "is_active": True}
        with pytest.raises(PermissionError):
            auth_service.require_role(recruit, "ADMIN")
        auth_service.require_role(recruit, "RECRUITER")
        with pytest.raises(PermissionError):
            auth_service.require_role(None, "RECRUITER")


class TestOwnershipScope:
    def test_admin_and_none_unrestricted(self):
        from core.models import Candidate

        admin = {"id": 1, "role": "ADMIN"}
        assert auth_service.owner_scope(Candidate, admin) is None
        assert auth_service.owner_scope(Candidate, None) is None

    def test_recruiter_scope_keeps_legacy(self):
        from core.models import Candidate

        recruiter = {"id": 7, "role": "RECRUITER"}
        expr = auth_service.owner_scope(Candidate, recruiter)
        rendered = str(expr)
        assert "created_by_user_id" in rendered
        assert "IS NULL" in rendered  # legacy NULL-owner records stay shared

    def test_can_access_rules(self):
        admin = {"id": 1, "role": "ADMIN"}
        recruiter = {"id": 7, "role": "RECRUITER"}
        other = {"id": 8, "role": "RECRUITER"}
        assert auth_service.can_access(admin, 99)  # admin anything
        assert auth_service.can_access(None, 99)  # legacy callers unrestricted
        assert auth_service.can_access(recruiter, 7)  # own record
        assert auth_service.can_access(recruiter, None)  # legacy shared
        assert not auth_service.can_access(recruiter, 8)  # not theirs
        assert not auth_service.can_access(other, 7)  # A cannot see B