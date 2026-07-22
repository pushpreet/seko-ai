"""Application configuration, loaded from the environment (12-factor)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for seko-ai.

    Values come from environment variables (or an optional ``.env`` for local dev).
    In the homelab these are rendered from SOPS into the stack's ``.env``.
    """

    model_config = SettingsConfigDict(
        env_prefix="SEKO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Core web app ---
    base_url: str = "https://seko.pushprh.com"
    session_secret: str = "dev-insecure-change-me"
    database_url: str = "sqlite:///./seko-ai.db"
    # Master key (base64, 32 bytes) used to wrap per-user data-encryption keys.
    master_key: str = ""

    # --- Authelia OIDC (existing homelab IdP) ---
    oidc_issuer: str = "https://auth.pushprh.com"
    oidc_client_id: str = "seko-ai"
    oidc_client_secret: str = ""
    oidc_redirect_path: str = "/auth/callback"
    # LLDAP groups conveyed in the OIDC ``groups`` claim.
    oidc_users_group: str = "llm_users"
    oidc_admins_group: str = "homelab_admins"

    # --- LiteLLM proxy (per-user virtual keys) ---
    litellm_base_url: str = "http://10.37.20.50:4000"
    litellm_master_key: str = ""
    service_usage_aliases_raw: str = Field(
        default="hermes",
        validation_alias=AliasChoices(
            "service_usage_aliases_raw",
            "service_usage_aliases",
            "SEKO_SERVICE_USAGE_ALIASES",
        ),
    )
    # Public endpoint handed to users/workspaces for their key.
    llm_public_url: str = "https://llm.pushprh.com/v1"
    llm_model: str = "qwen3.6-27b"

    # --- Workspace orchestration (Docker-over-SSH to epyc) ---
    docker_host: str = "ssh://pushprh@10.37.20.50"
    workspace_image: str = "ghcr.io/pushpreet/seko-workspace:latest"
    workspace_data_root: str = "/opt/appdata/seko-ai/workspaces"
    workspace_ssh_port_min: int = 22000
    workspace_ssh_port_max: int = 22099
    workspace_ssh_host: str = "epyc.pushprh.com"

    # --- Quotas & lifecycle (admin-configurable defaults) ---
    max_workspaces_per_user: int = 5
    workspace_cpus: float = 8.0
    workspace_mem: str = "16g"
    workspace_pids_limit: int = 512
    workspace_idle_stop_hours: float = 8.0

    # --- Backups (restic -> NAS, reuses homelab pattern) ---
    restic_repository: str = ""
    restic_password: str = ""

    debug: bool = Field(default=False)

    @property
    def service_usage_aliases(self) -> list[str]:
        """Alias prefixes shown as service/agent rows in the usage dashboard."""
        return [
            alias.strip() for alias in self.service_usage_aliases_raw.split(",") if alias.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()
