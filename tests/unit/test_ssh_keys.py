"""Tests for SSH public key management."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from seko_ai.models import User
from seko_ai.services import ssh_keys as sk
from tests.conftest import VALID_SSH_KEY as VALID_KEY


def _user(session: Session) -> User:
    u = User(subject="s", username="alice")
    session.add(u)
    session.flush()
    return u


def test_parse_valid_key_returns_fingerprint() -> None:
    normalized, fp = sk.parse_public_key(VALID_KEY)
    assert normalized.startswith("ssh-ed25519 AAAA")
    assert fp.startswith("SHA256:")
    # deterministic
    assert sk.parse_public_key(VALID_KEY)[1] == fp


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-key",
        "ssh-ed25519",  # missing blob
        "ssh-ed25519 !!!notbase64",
        "ssh-rsa AAAAC3NzaC1lZDI1NTE5AAAAIGtft6bOImUHwB+fMzSbM+Rf",  # type/data mismatch
    ],
)
def test_parse_invalid_key_rejected(bad: str) -> None:
    with pytest.raises(sk.InvalidSSHKey):
        sk.parse_public_key(bad)


def test_add_list_delete(db_session: Session) -> None:
    user = _user(db_session)
    assert sk.has_keys(db_session, user.id) is False
    key = sk.add_key(db_session, user, title="laptop", public_key=VALID_KEY)
    assert key.title == "laptop"
    assert key.fingerprint.startswith("SHA256:")
    assert sk.has_keys(db_session, user.id) is True
    assert [k.id for k in sk.list_keys(db_session, user.id)] == [key.id]
    assert sk.authorized_keys(db_session, user.id) == key.public_key
    assert sk.delete_key(db_session, user.id, key.id) is True
    assert sk.list_keys(db_session, user.id) == []


def test_duplicate_key_rejected(db_session: Session) -> None:
    user = _user(db_session)
    sk.add_key(db_session, user, title="a", public_key=VALID_KEY)
    with pytest.raises(sk.InvalidSSHKey, match="already added"):
        sk.add_key(db_session, user, title="b", public_key=VALID_KEY)


def test_delete_scoped_to_user(db_session: Session) -> None:
    u1 = _user(db_session)
    u2 = User(subject="s2", username="bob")
    db_session.add(u2)
    db_session.flush()
    key = sk.add_key(db_session, u1, title="a", public_key=VALID_KEY)
    assert sk.delete_key(db_session, u2.id, key.id) is False
    assert sk.delete_key(db_session, u1.id, key.id) is True


def test_authorized_keys_joins_multiple(db_session: Session) -> None:
    user = _user(db_session)
    k2 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIN0AdctpcCbmf5bN36HdDFTD2mB7dFGJddrjVxFGCBy/ two@seko"
    sk.add_key(db_session, user, title="a", public_key=VALID_KEY)
    sk.add_key(db_session, user, title="b", public_key=k2)
    joined = sk.authorized_keys(db_session, user.id)
    assert len(joined.splitlines()) == 2
