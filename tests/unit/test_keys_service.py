"""Tests for the keys service business logic."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from seko_ai.config import Settings
from seko_ai.models import ApiKey, ApiKeyIdentity, User
from seko_ai.services import keys as ks
from seko_ai.services.litellm_client import LiteLLMError
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
    api_key, plaintext = await ks.create_key_for_user(
        db_session, client, user, settings, name="Laptop"
    )
    assert plaintext.startswith("sk-fake-")
    assert api_key.masked_key.endswith(plaintext[-4:])
    assert api_key.active is True
    assert api_key.identity is not None
    assert api_key.identity.name == "Laptop"
    # No model allowlist is sent: LiteLLM resolves an absent/empty `models` to ALL proxy
    # models, so keys automatically gain new (incl. experimental) models with no re-issuing.
    # Guard against a regression to a pinned list, and against `["*"]` — the wildcard passes
    # request auth but breaks GET /v1/models, which drives the Open WebUI model dropdown.
    assert client.generated[0]["models"] in (None, [])
    assert ks.list_user_keys(db_session, user.id) == [api_key]


async def test_revoke_marks_inactive_and_calls_litellm(
    db_session: Session, settings: Settings
) -> None:
    user = _user(db_session)
    client = FakeLiteLLMClient()
    api_key, _ = await ks.create_key_for_user(db_session, client, user, settings, name="Laptop")
    await ks.revoke_key(db_session, client, api_key)
    assert api_key.active is False
    assert client.deleted[0]["key_aliases"] == [api_key.key_alias]
    assert ks.list_user_keys(db_session, user.id) == []


async def test_rotate_revokes_old_and_creates_new(db_session: Session, settings: Settings) -> None:
    user = _user(db_session)
    client = FakeLiteLLMClient()
    old, _ = await ks.create_key_for_user(db_session, client, user, settings, name="Laptop")
    new, new_plain = await ks.rotate_key(db_session, client, user, old, settings)
    assert old.active is False
    assert new.active is True
    assert new.id != old.id
    assert new.identity_id == old.identity_id
    assert new.identity is not None
    assert new.identity.name == "Laptop"
    active = ks.list_user_keys(db_session, user.id)
    assert active == [new]


async def test_rotate_generation_failure_keeps_old_key_active(
    db_session: Session, settings: Settings
) -> None:
    user = _user(db_session)
    client = FakeLiteLLMClient()
    old, _ = await ks.create_key_for_user(db_session, client, user, settings, name="Laptop")
    client.fail = True

    with pytest.raises(LiteLLMError):
        await ks.rotate_key(db_session, client, user, old, settings)

    assert old.active is True
    assert ks.list_user_keys(db_session, user.id) == [old]


async def test_rotate_revoke_failure_removes_replacement(
    db_session: Session, settings: Settings
) -> None:
    class FailsFirstDelete(FakeLiteLLMClient):
        delete_attempts = 0

        async def delete_keys(
            self,
            *,
            keys: list[str] | None = None,
            key_aliases: list[str] | None = None,
        ) -> dict[str, object]:
            self.delete_attempts += 1
            if self.delete_attempts == 1:
                raise LiteLLMError("old key revoke failed")
            return await super().delete_keys(keys=keys, key_aliases=key_aliases)

    user = _user(db_session)
    client = FailsFirstDelete()
    old, _ = await ks.create_key_for_user(db_session, client, user, settings, name="Laptop")

    with pytest.raises(LiteLLMError):
        await ks.rotate_key(db_session, client, user, old, settings)

    assert old.active is True
    assert ks.list_user_keys(db_session, user.id) == [old]
    assert client.delete_attempts == 2


async def test_rotate_cleanup_failure_keeps_replacement_inactive_for_audit(
    db_session: Session, settings: Settings
) -> None:
    class DeleteFails(FakeLiteLLMClient):
        async def delete_keys(
            self,
            *,
            keys: list[str] | None = None,
            key_aliases: list[str] | None = None,
        ) -> dict[str, object]:
            raise LiteLLMError("delete failed")

    user = _user(db_session)
    client = DeleteFails()
    old, _ = await ks.create_key_for_user(db_session, client, user, settings, name="Laptop")

    with pytest.raises(LiteLLMError, match="replacement cleanup also failed"):
        await ks.rotate_key(db_session, client, user, old, settings)

    rows = db_session.query(ApiKey).order_by(ApiKey.id).all()
    assert rows[0] is old
    assert old.active is True
    assert rows[1].active is False
    assert rows[1].identity_id == old.identity_id
    assert ks.list_user_keys(db_session, user.id) == [old]


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


async def test_key_names_are_normalized_and_unique_per_user(
    db_session: Session, settings: Settings
) -> None:
    user = _user(db_session)
    client = FakeLiteLLMClient()
    first, _ = await ks.create_key_for_user(
        db_session, client, user, settings, name="  Zoo   Code "
    )
    assert first.identity is not None
    assert first.identity.name == "Zoo Code"

    try:
        await ks.create_key_for_user(db_session, client, user, settings, name="zoo code")
    except ks.InvalidKeyName as exc:
        assert "already have" in str(exc)
    else:
        raise AssertionError("duplicate key name was accepted")
    assert len(client.generated) == 1


async def test_rename_identity_is_scoped_and_preserved(
    db_session: Session, settings: Settings
) -> None:
    user = _user(db_session)
    other = User(subject="other", username="other")
    db_session.add(other)
    db_session.flush()
    client = FakeLiteLLMClient()
    key, _ = await ks.create_key_for_user(db_session, client, user, settings, name="Laptop")

    renamed = ks.rename_identity(db_session, user.id, key.id, "Desktop")
    assert renamed is not None
    assert renamed.name == "Desktop"
    assert ks.rename_identity(db_session, other.id, key.id, "Stolen") is None


async def test_malformed_generate_response_does_not_reserve_name(
    db_session: Session, settings: Settings
) -> None:
    class MissingPlaintext(FakeLiteLLMClient):
        async def generate_key(self, **kwargs: object) -> dict[str, str]:
            return {"token": "tok-created"}

    user = _user(db_session)
    client = MissingPlaintext()

    with pytest.raises(ValueError, match="did not return"):
        await ks.create_key_for_user(db_session, client, user, settings, name="Laptop")

    assert db_session.query(ApiKeyIdentity).all() == []
    assert client.deleted[0]["key_aliases"] is not None
