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
    # Local embeddings model (stacks/vllm-embed, served-model-name) exposed through the same
    # LiteLLM gateway. Minted user keys include it so a single key covers chat + embeddings
    # (e.g. Zoo Code codebase indexing). `dimension` is the native vector size clients pin.
    llm_embedding_model: str = "embed"
    llm_embedding_dimension: int = 2560
    # Shared Qdrant vector DB (stacks/qdrant) that backs codebase indexing. URL is handed to
    # users on the docs page; the API key is a shared homelab secret (all llm_users share it,
    # so collections are mutually visible) surfaced only on the SSO-gated docs page.
    qdrant_url: str = "http://10.37.20.50:6333"
    qdrant_api_key: str = ""

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

    # --- Service status monitoring + user notifications ---
    # The `check-status` management command probes the API-key path (LiteLLM -> vLLM) and,
    # on a real up<->down transition, emails all seko users via Resend. See the status
    # router for the banner/page and the admin maintenance toggle.
    #
    # Probe mode:
    #   "litellm_health" (default) -> GET {litellm_base_url}/health with the master key;
    #       "up" iff HTTP 200 and at least one healthy endpoint (a served model). This is the
    #       truest "a user's API key can get completions" signal.
    #   "http" -> GET status_probe_url (or {litellm_base_url}/health/liveliness) expecting 200.
    status_probe_mode: str = "litellm_health"
    status_probe_url: str = ""
    status_probe_timeout: float = 10.0
    # Consecutive failed probes before declaring DOWN (hysteresis; one success -> UP). At the
    # ~60s timer cadence this is ~N minutes of sustained failure, so brief blips don't email.
    status_fail_threshold: int = 3

    # Resend (HTTPS API) for the down/up + maintenance emails. Reuses the homelab Resend
    # account; `alert_email_from` must be on a Resend-verified domain (pushprh.com).
    resend_api_key: str = ""
    alert_email_from: str = "alerts@pushprh.com"
    # Send a single announcement email on maintenance start and an "all clear" on end.
    status_notify_on_maintenance: bool = True
    # Auto-clear a forgotten maintenance window after this many hours (0 = never).
    maintenance_max_hours: float = 12.0

    debug: bool = Field(default=False)

    @property
    def effective_status_probe_url(self) -> str:
        """Resolve the URL used by the ``http`` probe mode (falls back to liveliness)."""
        if self.status_probe_url:
            return self.status_probe_url
        return f"{self.litellm_base_url.rstrip('/')}/health/liveliness"

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
