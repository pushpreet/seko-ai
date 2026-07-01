"""Config loading tests."""

from __future__ import annotations

import pytest

from seko_ai.config import Settings


def test_settings_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEKO_MAX_WORKSPACES_PER_USER", "5")
    monkeypatch.setenv("SEKO_LLM_MODEL", "custom-model")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.max_workspaces_per_user == 5
    assert settings.llm_model == "custom-model"


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.oidc_users_group == "llm_users"
    assert settings.oidc_admins_group == "homelab_admins"
    assert settings.workspace_ssh_port_min < settings.workspace_ssh_port_max
