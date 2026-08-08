"""Integration tests for the /docs user guide route."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def _login(client: TestClient, groups: list[str]) -> None:
    provider = client.app.state.oauth.authelia  # type: ignore[attr-defined]

    async def fake_token(request: Any) -> dict[str, Any]:
        return {"userinfo": {"sub": "u-docs", "preferred_username": "alice", "groups": groups}}

    provider.authorize_access_token = fake_token
    client.get("/auth/callback?code=abc", follow_redirects=False)


def test_docs_requires_auth(client: TestClient) -> None:
    resp = client.get("/docs", headers={"accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 303


def test_docs_renders_direct_api(client: TestClient) -> None:
    _login(client, ["llm_users"])
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "Direct API integration" in resp.text
    # The deprecated workspace/self-host sections are gone from the website.
    assert "Local workspaces" not in resp.text
    assert "Remote workspaces" not in resp.text


def test_docs_includes_live_endpoint_values(client: TestClient) -> None:
    _login(client, ["llm_users"])
    settings = client.app.state.settings  # type: ignore[attr-defined]
    resp = client.get("/docs")
    assert settings.llm_public_url in resp.text
    assert settings.llm_model in resp.text


def test_docs_includes_recommended_client_limits(client: TestClient) -> None:
    _login(client, ["llm_users"])
    resp = client.get("/docs")
    assert "Zoo Code 3.76+" in resp.text
    assert "Hermes Desktop" in resp.text
    assert "245760" in resp.text
    assert "8192" in resp.text
    assert "at most two images" in resp.text
    assert "4 MP" in resp.text


def test_docs_recommends_private_tailscale_path(client: TestClient) -> None:
    _login(client, ["llm_users"])
    settings = client.app.state.settings  # type: ignore[attr-defined]
    resp = client.get("/docs")
    assert "Enable Tailscale for long requests" in resp.text
    assert "sudo tailscale set --accept-routes=true" in resp.text
    assert "Use Tailscale DNS settings" in resp.text
    assert "same URL falls back to Cloudflare" in resp.text
    assert "disable Tailscale to deliberately" in resp.text
    assert "cf-ray" in resp.text
    assert f"{settings.llm_public_url.removesuffix('/v1')}/health/liveliness" in resp.text


def test_docs_includes_codebase_indexing(client: TestClient) -> None:
    _login(client, ["llm_users"])
    settings = client.app.state.settings  # type: ignore[attr-defined]
    resp = client.get("/docs")
    assert "Codebase indexing" in resp.text
    assert settings.llm_embedding_model in resp.text
    assert str(settings.llm_embedding_dimension) in resp.text


def test_docs_points_qdrant_at_the_users_own_machine(client: TestClient) -> None:
    """Users run their own Qdrant; the homelab instance is internal-only.

    The page must never leak the homelab's private address or the shared Qdrant API
    key, and must link out to the official install docs instead of restating them.
    """
    _login(client, ["llm_users"])
    resp = client.get("/docs")
    assert "http://localhost:6333" in resp.text
    assert "qdrant.tech/documentation/installation" in resp.text
    assert "10.37.20.50" not in resp.text
    assert "qdrant_url" not in resp.text
    assert "qdrant_api_key" not in resp.text
