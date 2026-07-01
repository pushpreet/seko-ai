"""Tests for the keys service business logic."""

from __future__ import annotations

from sqlalchemy.orm import Session

from seko_ai.config import Settings
from seko_ai.models import ApiKey, User
from seko_ai.services import keys as ks
from tests.fakes import FakeLiteLLMClient


def test_make_alias_is_slugged_and_unique() -> None:
    a1 = ks.make_alias("Alice Smith!")
    a2 = ks.make_alias("Alice Smith!")
    assert a1.startswith("seko-alice-smith-")
    assert a1 != a2


def test_make_alias_handles_empty() -> None:
    assert ks.make_alias("!!!").startswith("seko-user-")


def test_mask_key() -> None:
    assert ks.mask_key("sk-abcdefgh1234") == "sk-ab…1234"
    assert ks.mask_key("short") == "****"


def _user(session: Session) -> User:
    u = User(subject="s", username="alice", is_admin=False)
    session.add(u)
    session.flush()
    return u


async def test_create_key_persists_and_returns_plaintext(
    db_session: Session, settings: Settings
) -> None:
    user = _user(db_session)
    client = FakeLiteLLMClient()
    api_key, plaintext = await ks.create_key_for_user(db_session, client, user, settings)
    assert plaintext.startswith("sk-fake-")
    assert api_key.masked_key.endswith(plaintext[-4:])
    assert api_key.active is True
    assert client.generated[0]["models"] == [settings.llm_model]
    assert ks.list_user_keys(db_session, user.id) == [api_key]


async def test_revoke_marks_inactive_and_calls_litellm(
    db_session: Session, settings: Settings
) -> None:
    user = _user(db_session)
    client = FakeLiteLLMClient()
    api_key, _ = await ks.create_key_for_user(db_session, client, user, settings)
    await ks.revoke_key(db_session, client, api_key)
    assert api_key.active is False
    assert client.deleted[0]["key_aliases"] == [api_key.key_alias]
    assert ks.list_user_keys(db_session, user.id) == []


async def test_rotate_revokes_old_and_creates_new(
    db_session: Session, settings: Settings
) -> None:
    user = _user(db_session)
    client = FakeLiteLLMClient()
    old, _ = await ks.create_key_for_user(db_session, client, user, settings)
    new, new_plain = await ks.rotate_key(db_session, client, user, old, settings)
    assert old.active is False
    assert new.active is True
    assert new.id != old.id
    active = ks.list_user_keys(db_session, user.id)
    assert active == [new]


def test_get_key_scoped_to_user(db_session: Session) -> None:
    u1 = User(subject="a", username="a")
    u2 = User(subject="b", username="b")
    db_session.add_all([u1, u2])
    db_session.flush()
    k = ApiKey(user_id=u1.id, litellm_key_id="t", key_alias="al", masked_key="m")
    db_session.add(k)
    db_session.flush()
    assert ks.get_key(db_session, u1.id, k.id) is k
    assert ks.get_key(db_session, u2.id, k.id) is None
