"""Config loading tests."""

from __future__ import annotations

import pytest

from seko_ai.config import Settings


def test_settings_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEKO_MAX_WORKSPACES_PER_USER", "5")
    monkeypatch.setenv("SEKO_LLM_MODEL", "custom-model")
    monkeypatch.setenv("SEKO_LLM_IMAGE_MODEL", "custom-image-model")
    monkeypatch.setenv("SEKO_LLM_IMAGE_QUALITY_MODEL", "custom-quality-image-model")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.max_workspaces_per_user == 5
    assert settings.llm_model == "custom-model"
    assert settings.llm_image_model == "custom-image-model"
    assert settings.llm_image_quality_model == "custom-quality-image-model"


def test_service_usage_aliases_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEKO_SERVICE_USAGE_ALIASES", "hermes, foo ")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.service_usage_aliases == ["hermes", "foo"]


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.oidc_users_group == "llm_users"
    assert settings.oidc_admins_group == "homelab_admins"
    assert settings.service_usage_aliases == ["hermes"]
    assert settings.llm_image_model == "flux-2-klein-4b"
    assert settings.llm_image_quality_model == "qwen-image-2512"
    assert settings.workspace_ssh_port_min < settings.workspace_ssh_port_max
