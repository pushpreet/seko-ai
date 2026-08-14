"""A configurable in-memory fake of LiteLLMClient for service/route tests."""

from __future__ import annotations

import uuid
from typing import Any

from seko_ai.services.litellm_client import LiteLLMError


class FakeLiteLLMClient:
    """Records calls and returns canned responses; can be told to fail."""

    def __init__(
        self,
        *,
        fail: bool = False,
        delete_error: Exception | None = None,
    ) -> None:
        self.fail = fail
        self.delete_error = delete_error
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
        return {"key": f"sk-fake-{self._counter:04d}", "token": f"tok-{uuid.uuid4().hex}"}

    async def delete_keys(
        self, *, keys: list[str] | None = None, key_aliases: list[str] | None = None
    ) -> dict[str, Any]:
        if self.delete_error is not None:
            raise self.delete_error
        if self.fail:
            raise LiteLLMError("simulated failure")
        self.deleted.append({"keys": keys, "key_aliases": key_aliases})
        return {"deleted": True}

    async def key_info(self, key: str) -> dict[str, Any]:
        return {"key": key, "spend": 0.0}

    async def daily_activity(
        self, *, start_date: str, end_date: str, page_size: int = 1000
    ) -> list[dict[str, Any]]:
        return []
