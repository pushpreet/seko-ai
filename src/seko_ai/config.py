"""Application configuration, loaded from the environment (12-factor)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SETTINGS_CONFIG = SettingsConfigDict(
    env_prefix="SEKO_",
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
)

DEFAULT_DATABASE_URL = "sqlite:///./seko-ai.db"


class DatabaseSettings(BaseSettings):
    """The subset of configuration needed to open the database (engine + migrations)."""

    model_config = _SETTINGS_CONFIG

    database_url: str = DEFAULT_DATABASE_URL


class Settings(BaseSettings):
    """Runtime configuration for seko-ai.

    Values come from ``SEKO_*`` environment variables (or an optional ``.env`` for local
    development). Settings without a default are deployment-specific and must be supplied;
    the full contract is documented in ``docs/configuration.md``.
    """

    model_config = _SETTINGS_CONFIG

    # --- Core web app ---
    base_url: str
    session_secret: str
    database_url: str = DEFAULT_DATABASE_URL
    # --- OIDC identity provider ---
    oidc_issuer: str
    oidc_client_id: str = "seko-ai"
    oidc_client_secret: str = ""
    oidc_redirect_path: str = "/auth/callback"
    # Groups conveyed in the OIDC ``groups`` claim (ID token or userinfo).
    oidc_users_group: str
    oidc_admins_group: str

    # --- LiteLLM proxy (per-user virtual keys) ---
    litellm_base_url: str
    litellm_master_key: str = ""
    service_usage_aliases_raw: str = Field(
        default="",
        validation_alias=AliasChoices(
            "service_usage_aliases_raw",
            "service_usage_aliases",
            "SEKO_SERVICE_USAGE_ALIASES",
        ),
    )
    # Public OpenAI-compatible endpoint handed to users for their key.
    llm_public_url: str
    llm_model: str
    # Streamable-HTTP MCP endpoint shown in the user guide. Empty = derived from
    # ``llm_public_url`` (its ``/v1`` suffix replaced by ``/mcp/``, LiteLLM's MCP path).
    llm_mcp_url: str = ""
    # Hosted chat UI (Open WebUI) root shown in the user guide. Empty = section omitted.
    chat_url: str = ""
    # Embeddings model exposed through the same LiteLLM gateway, so a single key covers
    # chat + embeddings (e.g. codebase indexing). ``dimension`` is the native vector size.
    llm_embedding_enabled: bool = False
    llm_embedding_model: str = ""
    llm_embedding_dimension: int = 0
    # OpenAI Images-compatible models exposed through the same LiteLLM virtual key.
    llm_image_generation_enabled: bool = False
    llm_image_model: str = ""
    llm_image_quality_model: str = ""
    # NOTE: there is deliberately no `qdrant_url` / `qdrant_api_key` setting. The vector
    # store for codebase indexing runs on the USER's own machine (localhost:6333) — a
    # deployment's Qdrant is internal-only and is never handed out. Exposing one shared,
    # manage-scoped Qdrant would let any key holder read every other user's indexed source
    # chunks or drop their collections, and Qdrant's collection-scoped JWTs cannot create
    # collections (which Zoo Code does automatically), so per-user scoping is not possible.

    # --- Service status monitoring + user notifications ---
    # An in-process scheduler probes the API-key path (LiteLLM -> model) every
    # ``status_probe_interval`` seconds and, on a real up<->down transition, emails users via
    # Resend. See the status router for the banner/page and the admin maintenance toggle.
    status_scheduler_enabled: bool = True
    status_probe_interval: float = 60.0
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
    # default ~60s cadence this is ~N minutes of sustained failure, so blips don't email.
    status_fail_threshold: int = 3

    # Resend (HTTPS API) for the down/up + maintenance emails. Empty key = emails off;
    # when set, ``alert_email_from`` is required and must be on a Resend-verified domain.
    resend_api_key: str = ""
    alert_email_from: str = ""
    # Restrict the down/up + maintenance emails to admin users only (leaving the banner and
    # status page unchanged for everyone). Use this to stop notifying all seko users while
    # availability is flaky; set back to false to resume emailing every user.
    status_alert_admins_only: bool = False
    # Send a single announcement email on maintenance start and an "all clear" on end.
    status_notify_on_maintenance: bool = True
    # Auto-clear a forgotten maintenance window after this many hours (0 = never).
    maintenance_max_hours: float = 12.0

    debug: bool = Field(default=False)

    @model_validator(mode="after")
    def _check_dependent_settings(self) -> Settings:
        required_when = [
            (self.llm_embedding_enabled, "llm_embedding_model", self.llm_embedding_model),
            (self.llm_embedding_enabled, "llm_embedding_dimension", self.llm_embedding_dimension),
            (self.llm_image_generation_enabled, "llm_image_model", self.llm_image_model),
            (
                self.llm_image_generation_enabled,
                "llm_image_quality_model",
                self.llm_image_quality_model,
            ),
            (bool(self.resend_api_key), "alert_email_from", self.alert_email_from),
        ]
        missing = [
            f"SEKO_{name.upper()}"
            for enabled, name, value in required_when
            if enabled and not value
        ]
        if missing:
            raise ValueError(f"missing required settings: {', '.join(missing)}")
        if self.status_probe_interval <= 0:
            raise ValueError("SEKO_STATUS_PROBE_INTERVAL must be positive")
        return self

    @property
    def effective_mcp_url(self) -> str:
        """Resolve the MCP endpoint advertised to users."""
        if self.llm_mcp_url:
            return self.llm_mcp_url
        base = self.llm_public_url.rstrip("/").removesuffix("/v1")
        return f"{base}/mcp/"

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
    return Settings()  # required values come from the environment


@lru_cache
def get_database_settings() -> DatabaseSettings:
    """Return only the database configuration (no other settings are required)."""
    return DatabaseSettings()
