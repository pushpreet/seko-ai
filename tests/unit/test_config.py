"""Config loading tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from seko_ai.config import Settings


def test_settings_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEKO_LLM_MODEL", "custom-model")
    monkeypatch.setenv("SEKO_LLM_IMAGE_MODEL", "custom-image-model")
    monkeypatch.setenv("SEKO_LLM_IMAGE_QUALITY_MODEL", "custom-quality-image-model")
    monkeypatch.setenv("SEKO_LLM_EMBEDDING_ENABLED", "false")
    monkeypatch.setenv("SEKO_LLM_IMAGE_GENERATION_ENABLED", "false")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.llm_model == "custom-model"
    assert settings.llm_image_model == "custom-image-model"
    assert settings.llm_image_quality_model == "custom-quality-image-model"
    assert settings.llm_embedding_enabled is False
    assert settings.llm_image_generation_enabled is False


def test_service_usage_aliases_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEKO_SERVICE_USAGE_ALIASES", "hermes, foo ")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.service_usage_aliases == ["hermes", "foo"]


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.service_usage_aliases == []
    assert settings.llm_embedding_enabled is False
    assert settings.llm_image_generation_enabled is False
    assert settings.alert_email_from == ""
    assert settings.status_scheduler_enabled is False  # set by the test environment
    assert settings.status_probe_interval == 60.0


@pytest.mark.parametrize(
    "variable",
    [
        "SEKO_BASE_URL",
        "SEKO_SESSION_SECRET",
        "SEKO_OIDC_ISSUER",
        "SEKO_OIDC_USERS_GROUP",
        "SEKO_OIDC_ADMINS_GROUP",
        "SEKO_LITELLM_BASE_URL",
        "SEKO_LLM_PUBLIC_URL",
        "SEKO_LLM_MODEL",
    ],
)
def test_deployment_settings_are_required(monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    monkeypatch.delenv(variable)
    with pytest.raises(ValidationError, match=variable.removeprefix("SEKO_").lower()):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("switch", "expected"),
    [
        ("SEKO_LLM_EMBEDDING_ENABLED", "SEKO_LLM_EMBEDDING_MODEL"),
        ("SEKO_LLM_IMAGE_GENERATION_ENABLED", "SEKO_LLM_IMAGE_MODEL"),
        ("SEKO_RESEND_API_KEY", "SEKO_ALERT_EMAIL_FROM"),
    ],
)
def test_enabled_features_require_their_settings(
    monkeypatch: pytest.MonkeyPatch, switch: str, expected: str
) -> None:
    monkeypatch.setenv(switch, "true")
    with pytest.raises(ValidationError, match=expected):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_defaults_contain_no_deployment_hostnames() -> None:
    for field in Settings.model_fields.values():
        assert "pushprh" not in str(field.default)
        assert "10.37." not in str(field.default)


def test_mcp_url_is_derived_from_public_url() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.effective_mcp_url == "https://llm.example.test/mcp/"
    override = settings.model_copy(update={"llm_mcp_url": "https://mcp.example.test/"})
    assert override.effective_mcp_url == "https://mcp.example.test/"
