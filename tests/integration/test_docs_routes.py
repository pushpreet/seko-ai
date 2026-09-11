"""Integration tests for the /docs user guide route."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from seko_ai.config import Settings


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


def test_docs_includes_image_generation_and_editing(client: TestClient) -> None:
    _login(client, ["llm_users"])
    settings = client.app.state.settings  # type: ignore[attr-defined]
    resp = client.get("/docs")
    assert "Image generation and editing" in resp.text
    assert settings.llm_image_model in resp.text
    assert settings.llm_image_quality_model in resp.text
    assert "Fast generation, editing, inpainting" in resp.text
    assert "Generation only" in resp.text
    assert "4-8 seconds" in resp.text
    assert "4-5 minutes" in resp.text
    assert "/images/generations" in resp.text
    assert "/images/edits" in resp.text
    assert "mask=@mask.png" in resp.text
    assert "same-size RGBA PNG" in resp.text
    assert "Transparent pixels are editable" in resp.text
    assert "20 MiB" in resp.text
    assert "20 million" in resp.text
    assert "8192 pixels per dimension" in resp.text
    assert "one request runs and one waits" in resp.text
    assert "429" in resp.text
    assert "<code>n</code> must be <code>1</code>" in resp.text
    assert "b64_json" in resp.text
    assert "same seko key" in resp.text


def test_docs_includes_hosted_image_surfaces(client: TestClient) -> None:
    _login(client, ["llm_users"])
    resp = client.get("/docs")
    assert "https://chat.pushprh.com/playground/images" in resp.text
    assert "Integrations &rarr; Image" in resp.text
    assert "owned by your authenticated account" in resp.text
    assert "intentionally absent from the chat model picker" in resp.text
    assert "Hermes Discord and Desktop" in resp.text
    assert "exactly one source" in resp.text
    assert "does not expose masks or multiple reference images" in resp.text
    assert "https://llm.pushprh.com/mcp/" in resp.text
    assert "The trailing slash is required" in resp.text
    assert "image-image_generate" in resp.text
    assert "image-image_edit" in resp.text
    assert "inline base64" in resp.text
    assert "standard MCP image content" in resp.text


def test_docs_keeps_production_comfyui_private(client: TestClient) -> None:
    _login(client, ["llm_users"])
    resp = client.get("/docs")
    assert "production ComfyUI worker is private" in resp.text
    assert "no public or LAN Web UI" in resp.text
    assert "OpenAI Compatible Image Generate" in resp.text
    assert "b4acfc2d2773b5d420f8ddb9299fa0513d7e9b18" in resp.text
    assert "Browser-only image frontends" in resp.text
    assert "not the node or workflow JSON" in resp.text
    assert "Chat clients only" not in resp.text
    assert "50-step" not in resp.text
    assert "image-gen-comfyui" not in resp.text
    assert "LITELLM_MASTER_KEY" not in resp.text
    assert ":8188" not in resp.text


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


@pytest.mark.parametrize(
    ("embeddings", "images"),
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_docs_capability_switches_preserve_chat_and_vision(
    client: TestClient, settings: Settings, embeddings: bool, images: bool
) -> None:
    settings.llm_embedding_enabled = embeddings
    settings.llm_image_generation_enabled = images
    settings.llm_embedding_model = "embedding-model-sentinel"
    settings.llm_image_model = "image-model-sentinel"
    settings.llm_image_quality_model = "quality-image-model-sentinel"
    _login(client, ["llm_users"])
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert ('id="codebase-indexing"' in resp.text) is embeddings
    assert ("/embeddings" in resp.text) is embeddings
    assert ("embedding-model-sentinel" in resp.text) is embeddings
    assert ('id="image-generation"' in resp.text) is images
    assert ("/images/generations" in resp.text) is images
    assert ("/images/edits" in resp.text) is images
    assert ("image-image_generate" in resp.text) is images
    assert ("image-image_edit" in resp.text) is images
    assert ("image-model-sentinel" in resp.text) is images
    assert ("quality-image-model-sentinel" in resp.text) is images
    assert ("playground/images" in resp.text) is images
    assert "Direct API integration" in resp.text
    assert "/chat/completions" in resp.text
    assert "at most two images" in resp.text
    assert "4 MP" in resp.text
    if not embeddings:
        assert "keep your existing local indexes and documents" in resp.text
    if not images:
        assert "Image generation/editing is not offered" in resp.text
