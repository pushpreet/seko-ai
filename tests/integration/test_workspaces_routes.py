"""Integration tests for the workspace management routes."""

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
        return {"userinfo": {"sub": "u-ws", "preferred_username": "alice", "groups": ["llm_users"]}}

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


def _add_ssh_key(client: TestClient) -> None:
    client.post("/profile/ssh-key", data={"ssh_public_key": "ssh-ed25519 AAAAtest"})


def test_workspaces_page_requires_auth(client: TestClient) -> None:
    resp = client.get("/workspaces", headers={"accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 303


def test_page_warns_without_ssh_key(client: TestClient, wired: FakeBackend) -> None:
    _login(client)
    resp = client.get("/workspaces")
    assert resp.status_code == 200
    assert "Add an SSH public key" in resp.text


def test_create_list_and_terminate(client: TestClient, wired: FakeBackend) -> None:
    _login(client)
    _add_ssh_key(client)
    # create
    resp = client.post("/workspaces", data={"name": "mydev"})
    assert resp.status_code == 200
    assert "mydev" in resp.text
    assert "running" in resp.text
    assert wired.created  # backend actually created a container
    assert "ssh dev@" in resp.text  # connection hint

    # terminate the workspace (id 1)
    resp = client.post("/workspaces/1/terminate")
    assert resp.status_code == 200
    assert "No workspaces yet" in resp.text
    assert wired.removed


def test_create_without_key_shows_error(client: TestClient, wired: FakeBackend) -> None:
    _login(client)
    resp = client.post("/workspaces", data={"name": "x"})
    assert resp.status_code == 400
    assert "SSH public key" in resp.text


def test_stop_and_start(client: TestClient, wired: FakeBackend) -> None:
    _login(client)
    _add_ssh_key(client)
    client.post("/workspaces", data={"name": "w"})
    stop = client.post("/workspaces/1/stop")
    assert "stopped" in stop.text
    start = client.post("/workspaces/1/start")
    assert "running" in start.text


def test_cannot_touch_another_users_workspace(client: TestClient, wired: FakeBackend) -> None:
    _login(client)
    _add_ssh_key(client)
    client.post("/workspaces", data={"name": "w"})
    # switch user
    provider = client.app.state.oauth.authelia  # type: ignore[attr-defined]

    async def other(request: Any) -> dict[str, Any]:
        return {"userinfo": {"sub": "u-evil", "preferred_username": "eve", "groups": ["llm_users"]}}

    provider.authorize_access_token = other
    client.get("/auth/callback?code=abc", follow_redirects=False)
    resp = client.post("/workspaces/1/terminate")
    assert resp.status_code == 404


def test_ssh_key_validation_rejects_garbage(client: TestClient, wired: FakeBackend) -> None:
    _login(client)
    resp = client.post("/profile/ssh-key", data={"ssh_public_key": "not-a-key"})
    assert resp.status_code == 400
    assert "OpenSSH public key" in resp.text
