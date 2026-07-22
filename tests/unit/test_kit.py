"""Tests for the self-host kit generator (service-level; the selfhost routes are deprecated)."""

from __future__ import annotations

import pytest

from seko_ai.config import Settings
from seko_ai.services import kit as kit_service


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


def test_build_install_ps1_is_powershell_and_checks_docker() -> None:
    install = kit_service.build_install_ps1()
    assert install.startswith("#requires -Version 5")
    assert "Get-Command docker" in install
    assert "docker compose version" in install
    assert "docker compose up -d" in install
    assert "ssh dev@localhost -p 2222" in install
    # creates the code mount dir the PowerShell way
    assert "New-Item -ItemType Directory -Force -Path 'code'" in install
    # no Linux uid matching on Docker Desktop
    assert "id -u" not in install
    assert "SEKO_DEV_UID" not in install
    # default harness is pi
    assert "-t pi" in install
    assert "Drive the pi harness" in install


def test_build_install_ps1_uses_selected_harness() -> None:
    install = kit_service.build_install_ps1(harness="oh-my-pi")
    assert "Drive the oh-my-pi harness" in install
    assert "-t omp" in install
    assert "-t pi" not in install


def test_build_install_uses_selected_harness() -> None:
    install = kit_service.build_install(harness="oh-my-pi")
    assert "Drive the oh-my-pi harness" in install
    assert "-t omp" in install
    assert "-t pi" not in install


def test_build_kit_threads_harness(default_settings: Settings) -> None:
    kit = kit_service.build_kit(
        default_settings, api_key="sk-xyz", authorized_keys="k", harness="oh-my-pi"
    )
    assert "-t omp" in kit.install_sh
    assert "-t omp" in kit.install_ps1


def test_build_kit_bundles_all_files(default_settings: Settings) -> None:
    kit = kit_service.build_kit(
        default_settings, api_key="sk-xyz", authorized_keys="ssh-ed25519 K"
    )
    assert "sk-xyz" in kit.env
    assert default_settings.workspace_image in kit.compose
    assert kit.install_sh.startswith("#!/usr/bin/env bash")
    assert kit.install_ps1.startswith("#requires -Version 5")


@pytest.fixture
def default_settings() -> Settings:
    return Settings(master_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
