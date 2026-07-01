"""A configurable in-memory fake of LiteLLMClient for service/route tests."""

from __future__ import annotations

from typing import Any

from seko_ai.services.litellm_client import LiteLLMError


class FakeLiteLLMClient:
    """Records calls and returns canned responses; can be told to fail."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.generated: list[dict[str, Any]] = []
        self.deleted: list[dict[str, Any]] = []
        self._counter = 0

    async def __aenter__(self) -> FakeLiteLLMClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def generate_key(
        self,
        *,
        user_id: str,
        key_alias: str,
        models: list[str] | None = None,
        max_budget: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.fail:
            raise LiteLLMError("simulated failure")
        self._counter += 1
        self.generated.append({"user_id": user_id, "key_alias": key_alias, "models": models})
        return {"key": f"sk-fake-{self._counter:04d}", "token": f"tok-{self._counter}"}

    async def delete_keys(
        self, *, keys: list[str] | None = None, key_aliases: list[str] | None = None
    ) -> dict[str, Any]:
        if self.fail:
            raise LiteLLMError("simulated failure")
        self.deleted.append({"keys": keys, "key_aliases": key_aliases})
        return {"deleted": True}

    async def key_info(self, key: str) -> dict[str, Any]:
        return {"key": key, "spend": 0.0}

    async def user_daily_activity(self, user_id: str) -> dict[str, Any]:
        return {"user_id": user_id, "results": []}
