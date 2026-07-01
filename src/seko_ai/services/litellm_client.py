"""Async client for the LiteLLM proxy admin API (virtual key management + usage).

LiteLLM sits in front of vLLM and issues per-user *virtual keys* against a single upstream
key. seko-ai uses the proxy's master key to mint, inspect, and revoke those virtual keys.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

import httpx

from seko_ai.config import Settings
from seko_ai.logging_config import get_logger

log = get_logger("seko_ai.litellm")


class LiteLLMError(RuntimeError):
    """Raised when the LiteLLM proxy returns an error response."""


class LiteLLMClient:
    """Thin wrapper over the LiteLLM proxy admin endpoints."""

    def __init__(
        self,
        base_url: str,
        master_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"}
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_settings(cls, settings: Settings) -> LiteLLMClient:
        return cls(settings.litellm_base_url, settings.litellm_master_key)

    async def __aenter__(self) -> Self:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._require_client()
        try:
            resp = await client.post(
                f"{self._base_url}{path}", json=payload, headers=self._headers
            )
        except httpx.HTTPError as exc:  # network/timeout
            raise LiteLLMError(f"LiteLLM request failed: {exc}") from exc
        return self._parse(resp)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        client = self._require_client()
        try:
            resp = await client.get(
                f"{self._base_url}{path}", params=params, headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise LiteLLMError(f"LiteLLM request failed: {exc}") from exc
        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code >= 400:
            raise LiteLLMError(f"LiteLLM returned {resp.status_code}: {resp.text[:300]}")
        data: dict[str, Any] = resp.json()
        return data

    # --- Virtual key operations ---

    async def generate_key(
        self,
        *,
        user_id: str,
        key_alias: str,
        models: list[str] | None = None,
        max_budget: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Mint a new virtual key. Returns the raw LiteLLM response (contains ``key``)."""
        payload: dict[str, Any] = {"user_id": user_id, "key_alias": key_alias}
        if models is not None:
            payload["models"] = models
        if max_budget is not None:
            payload["max_budget"] = max_budget
        if metadata is not None:
            payload["metadata"] = metadata
        return await self._post("/key/generate", payload)

    async def delete_keys(
        self, *, keys: list[str] | None = None, key_aliases: list[str] | None = None
    ) -> dict[str, Any]:
        """Revoke keys by value or by alias."""
        payload: dict[str, Any] = {}
        if keys:
            payload["keys"] = keys
        if key_aliases:
            payload["key_aliases"] = key_aliases
        if not payload:
            raise ValueError("delete_keys requires keys or key_aliases")
        return await self._post("/key/delete", payload)

    async def key_info(self, key: str) -> dict[str, Any]:
        """Return metadata/spend for a single key."""
        return await self._get("/key/info", params={"key": key})

    async def user_daily_activity(self, user_id: str) -> dict[str, Any]:
        """Return per-user usage/spend aggregates (used by the usage dashboard)."""
        return await self._get("/user/daily/activity", params={"user_id": user_id})
