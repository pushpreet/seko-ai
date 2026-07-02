"""Integration tests for backup/restore routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seko_ai.deps import get_litellm_client
from tests.fakes import FakeBackend, FakeLiteLLMClient


def _login(client: TestClient) -> None:
    provider = client.app.state.oauth.authelia  # type: ignore[attr-defined]

    async def fake_token(request: Any) -> dict[str, Any]:
        return {"userinfo": {"sub": "u-bk", "preferred_username": "alice", "groups": ["llm_users"]}}

    provider.authorize_access_token = fake_token
    client.get("/auth/callback?code=abc", follow_redirects=False)


@pytest.fixture
def wired(client: TestClient) -> FakeBackend:
    backend = FakeBackend()
    client.app.state.container_backend = backend  # type: ignore[attr-defined]

    async def override_llm() -> AsyncIterator[FakeLiteLLMClient]:
        yield FakeLiteLLMClient()

    client.app.dependency_overrides[get_litellm_client] = override_llm  # type: ignore[attr-defined]
    return backend


def _setup_workspace(client: TestClient) -> None:
    from tests.conftest import VALID_SSH_KEY

    _login(client)
    client.post("/profile/ssh-keys", data={"title": "laptop", "public_key": VALID_SSH_KEY})
    client.post("/workspaces", data={"name": "w"})


def test_on_demand_backup(client: TestClient, wired: FakeBackend) -> None:
    _setup_workspace(client)
    resp = client.post("/workspaces/1/backup")
    assert resp.status_code == 200
    assert "Backup complete" in resp.text
    assert wired.backed_up


def test_backups_page_lists_backup(client: TestClient, wired: FakeBackend) -> None:
    _setup_workspace(client)
    client.post("/workspaces/1/backup")
    page = client.get("/backups")
    assert page.status_code == 200
    assert "manual" in page.text
    assert "No backups yet" not in page.text


def test_terminate_with_backup_takes_snapshot(client: TestClient, wired: FakeBackend) -> None:
    _setup_workspace(client)
    resp = client.post("/workspaces/1/terminate", data={"backup": "1"})
    assert resp.status_code == 200
    assert wired.backed_up  # final snapshot taken
    # backup survives after termination and is restorable
    page = client.get("/backups")
    assert "on_terminate" in page.text


def test_restore_creates_new_workspace(client: TestClient, wired: FakeBackend) -> None:
    _setup_workspace(client)
    client.post("/workspaces/1/backup")
    client.post("/workspaces/1/terminate")  # free quota
    resp = client.post("/backups/1/restore")
    assert resp.status_code == 200
    assert "Restored into a new workspace" in resp.text
    assert wired.restored
    # a new running workspace exists
    ws_page = client.get("/workspaces")
    assert "restored" in ws_page.text.lower()


def test_restore_missing_backup_404(client: TestClient, wired: FakeBackend) -> None:
    _login(client)
    resp = client.post("/backups/999/restore")
    assert resp.status_code == 404


def test_delete_backup_forgets_snapshot_and_removes_row(
    client: TestClient, wired: FakeBackend
) -> None:
    _setup_workspace(client)
    client.post("/workspaces/1/backup")
    snapshot_id = "snap-0001"

    resp = client.post("/backups/1/delete")

    assert resp.status_code == 200
    assert "Backup deleted" in resp.text
    assert wired.forgotten == [snapshot_id]
    page = client.get("/backups")
    assert page.status_code == 200
    assert "manual" not in page.text
    assert "No backups yet" in page.text


def test_delete_missing_backup_404(client: TestClient, wired: FakeBackend) -> None:
    _login(client)
    resp = client.post("/backups/999/delete")
    assert resp.status_code == 404


def test_restore_over_quota_shows_error(client: TestClient, wired: FakeBackend) -> None:
    _setup_workspace(client)  # 1 workspace (limit is 2 in test settings)
    client.post("/workspaces/1/backup")
    client.post("/workspaces", data={"name": "w2"})  # now at limit (2)
    resp = client.post("/backups/1/restore")
    assert resp.status_code == 400
    assert "workspaces" in resp.text.lower()
