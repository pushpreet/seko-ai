"""Integration tests for the /selfhost kit routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seko_ai.deps import get_litellm_client
from tests.fakes import FakeLiteLLMClient


def _login(client: TestClient) -> None:
    provider = client.app.state.oauth.authelia  # type: ignore[attr-defined]

    async def fake_token(request: Any) -> dict[str, Any]:
        return {
            "userinfo": {
                "sub": "u-sh",
                "preferred_username": "alice",
                "groups": ["llm_users"],
            }
        }

    provider.authorize_access_token = fake_token
    client.get("/auth/callback?code=abc", follow_redirects=False)


@pytest.fixture
def wired_llm(client: TestClient) -> None:
    async def override_llm() -> AsyncIterator[FakeLiteLLMClient]:
        yield FakeLiteLLMClient()

    client.app.dependency_overrides[get_litellm_client] = override_llm  # type: ignore[attr-defined]


def _add_ssh_key(client: TestClient) -> None:
    from tests.conftest import VALID_SSH_KEY

    client.post("/profile/ssh-keys", data={"title": "laptop", "public_key": VALID_SSH_KEY})


def test_selfhost_page_requires_auth(client: TestClient) -> None:
    resp = client.get("/selfhost", headers={"accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 303


def test_kit_requires_ssh_key(client: TestClient, wired_llm: None) -> None:
    _login(client)
    resp = client.post("/selfhost/kit", data={"harness": "pi"})
    assert resp.status_code == 400
    assert "Add an SSH public key" in resp.text


def test_kit_renders_download_all_button(client: TestClient, wired_llm: None) -> None:
    _login(client)
    _add_ssh_key(client)
    resp = client.post("/selfhost/kit", data={"harness": "pi"})
    assert resp.status_code == 200
    # The single client-side "Download all" entry point and its handler.
    assert "Download all (.zip)" in resp.text
    assert "downloadKit(this)" in resp.text
    # Each kit file is tagged so the client-side zip can collect it by name.
    for name in (".env", "docker-compose.yml", "install.sh", "install.ps1"):
        assert f'data-kit-file="{name}"' in resp.text
    # Installers carry their OS so only the selected one is zipped.
    assert 'data-kit-os="unix"' in resp.text
    assert 'data-kit-os="windows"' in resp.text
