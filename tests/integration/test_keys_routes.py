"""Integration tests for the API key management routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seko_ai.deps import get_litellm_client
from tests.fakes import FakeLiteLLMClient


def _login(client: TestClient, groups: list[str] | None = None) -> None:
    provider = client.app.state.oauth.authelia  # type: ignore[attr-defined]

    async def fake_token(request: Any) -> dict[str, Any]:
        return {
            "userinfo": {
                "sub": "u-keys",
                "preferred_username": "alice",
                "groups": groups or ["llm_users"],
            }
        }

    provider.authorize_access_token = fake_token
    client.get("/auth/callback?code=abc", follow_redirects=False)


@pytest.fixture
def fake_litellm(client: TestClient) -> FakeLiteLLMClient:
    fake = FakeLiteLLMClient()

    async def override() -> AsyncIterator[FakeLiteLLMClient]:
        yield fake

    client.app.dependency_overrides[get_litellm_client] = override  # type: ignore[attr-defined]
    return fake


def test_keys_page_requires_auth(client: TestClient) -> None:
    resp = client.get("/keys", headers={"accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_keys_page_shows_endpoint_and_empty_state(
    client: TestClient, fake_litellm: FakeLiteLLMClient
) -> None:
    _login(client)
    resp = client.get("/keys")
    assert resp.status_code == 200
    assert "no active keys" in resp.text
    assert "/v1" in resp.text  # endpoint shown


def test_create_key_reveals_once_and_lists(
    client: TestClient, fake_litellm: FakeLiteLLMClient
) -> None:
    _login(client)
    resp = client.post("/keys")
    assert resp.status_code == 200
    assert "sk-fake-0001" in resp.text  # one-time reveal
    assert "shown once" in resp.text
    assert len(fake_litellm.generated) == 1
    # Reloading the page must NOT show the plaintext again.
    page = client.get("/keys")
    assert "sk-fake-0001" not in page.text
    assert "…0001" in page.text  # masked hint present


def test_rotate_key(client: TestClient, fake_litellm: FakeLiteLLMClient) -> None:
    _login(client)
    client.post("/keys")
    # Find the key id by rendering the page is awkward; rotate id=1 (first key).
    resp = client.post("/keys/1/rotate")
    assert resp.status_code == 200
    assert "sk-fake-0002" in resp.text
    assert len(fake_litellm.deleted) == 1


def test_revoke_key(client: TestClient, fake_litellm: FakeLiteLLMClient) -> None:
    _login(client)
    client.post("/keys")
    resp = client.post("/keys/1/revoke")
    assert resp.status_code == 200
    assert "no active keys" in resp.text
    assert fake_litellm.deleted[0]["key_aliases"] is not None


def test_rotate_missing_key_404(client: TestClient, fake_litellm: FakeLiteLLMClient) -> None:
    _login(client)
    resp = client.post("/keys/999/rotate")
    assert resp.status_code == 404


def test_create_key_litellm_failure_shows_error(client: TestClient) -> None:
    _login(client)
    fake = FakeLiteLLMClient(fail=True)

    async def override() -> AsyncIterator[FakeLiteLLMClient]:
        yield fake

    client.app.dependency_overrides[get_litellm_client] = override  # type: ignore[attr-defined]
    resp = client.post("/keys")
    assert resp.status_code == 502
    assert "Could not create" in resp.text


def test_cannot_rotate_another_users_key(
    client: TestClient, fake_litellm: FakeLiteLLMClient
) -> None:
    # alice creates key id 1
    _login(client, groups=["llm_users"])
    client.post("/keys")
    # a different user logs in (same test client/session replaced) and tries to rotate id 1
    provider = client.app.state.oauth.authelia  # type: ignore[attr-defined]

    async def other_token(request: Any) -> dict[str, Any]:
        return {
            "userinfo": {"sub": "u-other", "preferred_username": "eve", "groups": ["llm_users"]}
        }

    provider.authorize_access_token = other_token
    client.get("/auth/callback?code=abc", follow_redirects=False)
    resp = client.post("/keys/1/rotate")
    assert resp.status_code == 404
