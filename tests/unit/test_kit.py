"""Tests for the self-host kit generator + routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seko_ai.config import Settings
from seko_ai.deps import get_litellm_client
from seko_ai.services import kit as kit_service
from tests.fakes import FakeLiteLLMClient


def test_build_env_contains_key_and_endpoint() -> None:
    env = kit_service.build_env(
        base_url="https://llm.pushprh.com/v1",
        api_key="sk-secret",
        model="qwen3.6-27b",
        authorized_keys="ssh-ed25519 AAAA",
    )
    assert "LLM_BASE_URL=https://llm.pushprh.com/v1" in env
    assert "LLM_API_KEY=sk-secret" in env
    assert "LLM_MODEL=qwen3.6-27b" in env
    assert "SEKO_AUTHORIZED_KEYS=ssh-ed25519 AAAA" in env


def test_build_compose_references_image_and_port() -> None:
    compose = kit_service.build_compose(image="ghcr.io/pushpreet/seko-workspace:latest")
    assert "ghcr.io/pushpreet/seko-workspace:latest" in compose
    assert '"2222:22"' in compose
    assert "no-new-privileges:true" in compose


def test_build_compose_isolates_home_and_mounts_code() -> None:
    compose = kit_service.build_compose(image="ghcr.io/pushpreet/seko-workspace:latest")
    # container state stays in a named volume, not the user's project tree
    assert "- seko-home:/home/dev" in compose
    assert "\nvolumes:\n  seko-home:\n" in compose
    # user code is bind-mounted at a subpath, not as HOME
    assert "${SEKO_CODE_DIR:-./code}:/home/dev/workspace" in compose


def test_build_install_is_bash_and_checks_docker() -> None:
    install = kit_service.build_install()
    assert install.startswith("#!/usr/bin/env bash")
    assert "command -v docker" in install
    assert "docker compose up -d" in install
    assert "ssh dev@localhost -p 2222" in install
    # prepares the code mount dir and matches the container uid to the host user
    assert "mkdir -p code" in install
    assert "SEKO_DEV_UID=$(id -u)" in install
    assert "SEKO_DEV_GID=$(id -g)" in install
    # default harness is pi
    assert "-t pi" in install
    assert "Drive the pi harness" in install


def test_build_install_uses_selected_harness() -> None:
    install = kit_service.build_install(harness="oh-my-pi")
    assert "Drive the oh-my-pi harness" in install
    assert "-t omp" in install
    assert "-t pi" not in install


def test_build_kit_threads_harness(default_settings: Settings) -> None:
    kit = kit_service.build_kit(
        default_settings, api_key="sk-xyz", authorized_keys="k", harness="oh-my-pi"
    )
    assert "-t omp" in kit.install


def test_build_kit_bundles_all_three(default_settings: Settings) -> None:
    kit = kit_service.build_kit(
        default_settings, api_key="sk-xyz", authorized_keys="ssh-ed25519 K"
    )
    assert "sk-xyz" in kit.env
    assert default_settings.workspace_image in kit.compose
    assert kit.install.startswith("#!/usr/bin/env bash")


@pytest.fixture
def default_settings() -> Settings:
    return Settings(master_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")


# --- Route tests ---


def _login(client: TestClient) -> None:
    provider = client.app.state.oauth.authelia  # type: ignore[attr-defined]

    async def fake_token(request: Any) -> dict[str, Any]:
        return {"userinfo": {"sub": "u-sh", "preferred_username": "alice", "groups": ["llm_users"]}}

    provider.authorize_access_token = fake_token
    client.get("/auth/callback?code=abc", follow_redirects=False)


@pytest.fixture
def with_llm(client: TestClient) -> None:
    async def override() -> AsyncIterator[FakeLiteLLMClient]:
        yield FakeLiteLLMClient()

    client.app.dependency_overrides[get_litellm_client] = override  # type: ignore[attr-defined]


def test_selfhost_page_requires_auth(client: TestClient) -> None:
    resp = client.get("/selfhost", headers={"accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 303


def test_generate_kit_requires_ssh_key(client: TestClient, with_llm: None) -> None:
    _login(client)
    resp = client.post("/selfhost/kit")
    assert resp.status_code == 400
    assert "SSH public key" in resp.text


def test_generate_kit_renders_files(client: TestClient, with_llm: None) -> None:
    from tests.conftest import VALID_SSH_KEY

    _login(client)
    client.post("/profile/ssh-keys", data={"title": "laptop", "public_key": VALID_SSH_KEY})
    resp = client.post("/selfhost/kit")
    assert resp.status_code == 200
    assert "docker-compose.yml" in resp.text
    assert "install.sh" in resp.text
    assert "sk-fake-0001" in resp.text  # personalized key embedded once
    assert "AAAAC3NzaC1lZDI1NTE5" in resp.text  # their pubkey blob
    assert "-t pi" in resp.text  # default harness launch hint


def test_generate_kit_honors_harness(client: TestClient, with_llm: None) -> None:
    from tests.conftest import VALID_SSH_KEY

    _login(client)
    client.post("/profile/ssh-keys", data={"title": "laptop", "public_key": VALID_SSH_KEY})
    resp = client.post("/selfhost/kit", data={"harness": "oh-my-pi"})
    assert resp.status_code == 200
    assert "-t omp" in resp.text
    assert "Drive the oh-my-pi harness" in resp.text
