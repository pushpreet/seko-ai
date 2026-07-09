"""Integration tests for the usage dashboard route."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from seko_ai.deps import get_litellm_client
from seko_ai.models import ApiKey
from seko_ai.services.users import get_user_by_subject
from tests.fakes import FakeLiteLLMClient

TOKEN = "tok-alice"
ALIAS = "seko-alice-1"


class UsageFake(FakeLiteLLMClient):
    """Returns a per-key activity breakdown attributable to alice's key."""

    async def daily_activity(
        self, *, start_date: str, end_date: str, page_size: int = 1000
    ) -> list[dict[str, Any]]:
        return [
            {
                "date": "2026-07-08",
                "breakdown": {
                    "api_keys": {
                        TOKEN: {
                            "metrics": {
                                "total_tokens": 4200,
                                "api_requests": 12,
                                "prompt_tokens": 3000,
                                "completion_tokens": 1200,
                            },
                            "metadata": {"key_alias": ALIAS},
                        }
                    }
                },
            }
        ]


def _login(client: TestClient, groups: list[str]) -> None:
    provider = client.app.state.oauth.authelia  # type: ignore[attr-defined]

    async def fake_token(request: Any) -> dict[str, Any]:
        return {"userinfo": {"sub": "u-usage", "preferred_username": "alice", "groups": groups}}

    provider.authorize_access_token = fake_token
    client.get("/auth/callback?code=abc", follow_redirects=False)


def _give_alice_a_key(session: Session) -> None:
    user = get_user_by_subject(session, "u-usage")
    assert user is not None
    session.add(
        ApiKey(
            user_id=user.id,
            litellm_key_id=TOKEN,
            key_alias=ALIAS,
            masked_key="sk-fa…lice",
            active=True,
        )
    )
    session.commit()


@pytest.fixture
def usage_llm(client: TestClient) -> None:
    async def override() -> AsyncIterator[UsageFake]:
        yield UsageFake()

    client.app.dependency_overrides[get_litellm_client] = override  # type: ignore[attr-defined]


def test_usage_requires_auth(client: TestClient) -> None:
    resp = client.get("/usage", headers={"accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 303


def test_user_sees_own_usage(client: TestClient, usage_llm: None, db_session: Session) -> None:
    _login(client, ["llm_users"])
    _give_alice_a_key(db_session)
    resp = client.get("/usage")
    assert resp.status_code == 200
    assert "4,200" in resp.text  # tokens formatted
    assert "3,000" in resp.text  # uploaded tokens formatted
    assert "1,200" in resp.text  # generated tokens formatted
    assert "Spend" not in resp.text
    assert "(admin)" not in resp.text


def test_admin_sees_all_users_table(
    client: TestClient, usage_llm: None, db_session: Session
) -> None:
    _login(client, ["homelab_admins"])
    _give_alice_a_key(db_session)
    resp = client.get("/usage")
    assert resp.status_code == 200
    assert "(admin)" in resp.text
    assert "All users" in resp.text
    assert "4,200" in resp.text  # admin's own key usage shown in the table
