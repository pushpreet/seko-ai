"""Tests for access evaluation and user provisioning."""

from __future__ import annotations

from sqlalchemy.orm import Session

from seko_ai.auth import extract_claims
from seko_ai.services import users as us


def test_evaluate_access_admin_always_allowed() -> None:
    d = us.evaluate_access(["homelab_admins"], "llm_users", "homelab_admins")
    assert d.allowed is True
    assert d.is_admin is True


def test_evaluate_access_member_allowed_not_admin() -> None:
    d = us.evaluate_access(["llm_users"], "llm_users", "homelab_admins")
    assert d.allowed is True
    assert d.is_admin is False


def test_evaluate_access_denied() -> None:
    d = us.evaluate_access(["homelab_users"], "llm_users", "homelab_admins")
    assert d.allowed is False
    assert d.is_admin is False


def test_evaluate_access_ignores_blank_groups() -> None:
    d = us.evaluate_access(["", "  ", "llm_users"], "llm_users", "homelab_admins")
    assert d.allowed is True


def test_extract_claims_from_userinfo() -> None:
    token = {
        "userinfo": {
            "sub": "abc",
            "preferred_username": "alice",
            "email": "alice@example.com",
            "name": "Alice A",
            "groups": ["llm_users"],
        }
    }
    claims = extract_claims(token)
    assert claims["subject"] == "abc"
    assert claims["username"] == "alice"
    assert claims["groups"] == ["llm_users"]


def test_extract_claims_string_group_normalized() -> None:
    claims = extract_claims({"sub": "x", "name": "X", "groups": "llm_users"})
    assert claims["groups"] == ["llm_users"]


def test_upsert_user_creates_then_updates(db_session: Session) -> None:
    u1 = us.upsert_user(
        db_session,
        subject="s1",
        username="bob",
        email="bob@x.com",
        display_name="Bob",
        is_admin=False,
    )
    assert u1.id is not None
    u2 = us.upsert_user(
        db_session,
        subject="s1",
        username="bob2",
        email="bob2@x.com",
        display_name="Bob Two",
        is_admin=True,
    )
    assert u2.id == u1.id
    assert u2.username == "bob2"
    assert u2.is_admin is True
    assert us.get_user_by_subject(db_session, "s1") is not None


def test_session_payload_shape(db_session: Session) -> None:
    u = us.upsert_user(
        db_session, subject="s2", username="c", email=None, display_name=None, is_admin=False
    )
    payload = us.session_payload(u)
    assert set(payload) == {"id", "subject", "username", "email", "display_name", "is_admin"}
