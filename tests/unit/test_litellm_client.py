"""Tests for the LiteLLM admin client (HTTP mocked with respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from seko_ai.services.litellm_client import LiteLLMClient, LiteLLMError

BASE = "http://litellm.test:4000"


def make_client() -> LiteLLMClient:
    return LiteLLMClient(BASE, "sk-master")


@respx.mock
async def test_generate_key_sends_expected_payload() -> None:
    route = respx.post(f"{BASE}/key/generate").mock(
        return_value=httpx.Response(200, json={"key": "sk-abc123", "token": "hashed"})
    )
    async with make_client() as client:
        result = await client.generate_key(
            user_id="seko-user-1", key_alias="seko-alice-xyz", models=["qwen3.6-27b"]
        )
    assert result["key"] == "sk-abc123"
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer sk-master"
    import json

    body = json.loads(sent.content)
    assert body["user_id"] == "seko-user-1"
    assert body["key_alias"] == "seko-alice-xyz"
    assert body["models"] == ["qwen3.6-27b"]


@respx.mock
async def test_delete_keys_by_alias() -> None:
    route = respx.post(f"{BASE}/key/delete").mock(
        return_value=httpx.Response(200, json={"deleted_keys": ["seko-alice-xyz"]})
    )
    async with make_client() as client:
        await client.delete_keys(key_aliases=["seko-alice-xyz"])
    import json

    body = json.loads(route.calls.last.request.content)
    assert body == {"key_aliases": ["seko-alice-xyz"]}


async def test_delete_keys_requires_argument() -> None:
    async with make_client() as client:
        with pytest.raises(ValueError, match="requires keys or key_aliases"):
            await client.delete_keys()


@respx.mock
async def test_error_status_raises() -> None:
    respx.post(f"{BASE}/key/generate").mock(
        return_value=httpx.Response(401, text="invalid master key")
    )
    async with make_client() as client:
        with pytest.raises(LiteLLMError, match="401"):
            await client.generate_key(user_id="u", key_alias="a")


@respx.mock
async def test_network_error_raises() -> None:
    respx.post(f"{BASE}/key/generate").mock(side_effect=httpx.ConnectError("boom"))
    async with make_client() as client:
        with pytest.raises(LiteLLMError, match="request failed"):
            await client.generate_key(user_id="u", key_alias="a")


@respx.mock
async def test_key_info_and_usage() -> None:
    respx.get(f"{BASE}/key/info").mock(return_value=httpx.Response(200, json={"spend": 1.5}))
    respx.get(f"{BASE}/user/daily/activity").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    async with make_client() as client:
        info = await client.key_info("sk-abc")
        usage = await client.user_daily_activity("seko-user-1")
    assert info["spend"] == 1.5
    assert usage == {"results": []}
