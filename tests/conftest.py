"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from seko_ai import db as db_module
from seko_ai import models  # noqa: F401  (register models on Base.metadata)
from seko_ai.app import create_app
from seko_ai.config import Settings
from seko_ai.db import Base

# A real ed25519 public key usable across tests.
VALID_SSH_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGtft6bOImUHwB+fMzSbM+Rf6NKTWQHWFBbFQzLbtHgK test@seko"
)


def add_ssh_key(session: object, user: object, title: str = "test") -> object:
    """Attach a valid SSH key to a user (test helper)."""
    from seko_ai.services import ssh_keys as sk

    return sk.add_key(session, user, title=title, public_key=VALID_SSH_KEY)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return Settings(
        base_url="http://testserver",
        session_secret="test-secret",
        database_url="sqlite://",
        master_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",  # b64 of 32 bytes
        oidc_client_secret="test-oidc-secret",
        litellm_master_key="sk-test-master",
        max_workspaces_per_user=2,  # small limit so quota paths are exercised in tests
    )


@pytest.fixture
def db_session(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    """In-memory SQLite session, wired into the app's session factory."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(settings: Settings, db_session: Session) -> Iterator[TestClient]:
    app = create_app(settings)

    # Authelia serves groups from the userinfo endpoint. Default it to empty so tests that
    # only mock the ID token (authorize_access_token) don't make a network call; tests that
    # exercise userinfo override this.
    async def _empty_userinfo(*args: object, **kwargs: object) -> dict[str, object]:
        return {}

    app.state.oauth.authelia.userinfo = _empty_userinfo  # type: ignore[attr-defined]
    with TestClient(app) as test_client:
        yield test_client
