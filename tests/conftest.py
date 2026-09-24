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

# Deployment-specific settings have no defaults; tests supply placeholder values through
# the environment so every ``Settings()`` construction is complete.
REQUIRED_ENV = {
    "SEKO_BASE_URL": "http://testserver",
    "SEKO_SESSION_SECRET": "test-secret",
    "SEKO_OIDC_ISSUER": "https://idp.example.test",
    "SEKO_OIDC_USERS_GROUP": "llm_users",
    "SEKO_OIDC_ADMINS_GROUP": "homelab_admins",
    "SEKO_LITELLM_BASE_URL": "http://litellm.example.test:4000",
    "SEKO_LLM_PUBLIC_URL": "https://llm.example.test/v1",
    "SEKO_LLM_MODEL": "qwen3.6-27b",
    "SEKO_STATUS_SCHEDULER_ENABLED": "false",
}


@pytest.fixture(autouse=True)
def required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        base_url="http://testserver",
        session_secret="test-secret",
        database_url="sqlite://",
        oidc_client_secret="test-oidc-secret",
        litellm_master_key="sk-test-master",
        chat_url="https://chat.example.test",
        llm_embedding_enabled=True,
        llm_embedding_model="embed",
        llm_embedding_dimension=2560,
        llm_image_generation_enabled=True,
        llm_image_model="flux-2-klein-4b",
        llm_image_quality_model="qwen-image-2512",
        service_usage_aliases="hermes",
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
