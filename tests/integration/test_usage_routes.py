"""Integration tests for the usage dashboard route."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seko_ai.deps import get_litellm_client
from tests.fakes import FakeLiteLLMClient


class UsageFake(FakeLiteLLMClient):
    async def user_daily_activity(self, user_id: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "metadata": {
                "total_tokens": 4200,
                "total_api_requests": 12,
                "total_prompt_tokens": 3000,
                "total_completion_tokens": 1200,
            }
        }


def _login(client: TestClient, groups: list[str]) -> None:
    provider = client.app.state.oauth.authelia  # type: ignore[attr-defined]

    async def fake_token(request: Any) -> dict[str, Any]:
        return {"userinfo": {"sub": "u-usage", "preferred_username": "alice", "groups": groups}}

    provider.authorize_access_token = fake_token
    client.get("/auth/callback?code=abc", follow_redirects=False)


@pytest.fixture
def usage_llm(client: TestClient) -> None:
    async def override() -> AsyncIterator[UsageFake]:
        yield UsageFake()

    client.app.dependency_overrides[get_litellm_client] = override  # type: ignore[attr-defined]


def test_usage_requires_auth(client: TestClient) -> None:
    resp = client.get("/usage", headers={"accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 303


def test_user_sees_own_usage(client: TestClient, usage_llm: None) -> None:
    _login(client, ["llm_users"])
    resp = client.get("/usage")
    assert resp.status_code == 200
    assert "4,200" in resp.text  # tokens formatted
    assert "3,000" in resp.text  # uploaded tokens formatted
    assert "1,200" in resp.text  # generated tokens formatted
    assert "Spend" not in resp.text
    assert "(admin)" not in resp.text


def test_admin_sees_all_users_table(client: TestClient, usage_llm: None) -> None:
    _login(client, ["homelab_admins"])
    resp = client.get("/usage")
    assert resp.status_code == 200
    assert "(admin)" in resp.text
    assert "All users" in resp.text
