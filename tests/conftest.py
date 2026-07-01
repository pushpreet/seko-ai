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


@pytest.fixture
def settings() -> Settings:
    return Settings(
        base_url="http://testserver",
        session_secret="test-secret",
        database_url="sqlite://",
        master_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",  # b64 of 32 bytes
        oidc_client_secret="test-oidc-secret",
        litellm_master_key="sk-test-master",
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
    with TestClient(app) as test_client:
        yield test_client
