"""Integration tests for the OIDC login flow with a mocked Authelia provider."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.responses import RedirectResponse


def _patch_provider(client: TestClient, claims: dict[str, Any]) -> None:
    provider = client.app.state.oauth.authelia  # type: ignore[attr-defined]

    async def fake_authorize_access_token(request: Any) -> dict[str, Any]:
        return {"userinfo": claims}

    async def fake_authorize_redirect(request: Any, redirect_uri: str) -> RedirectResponse:
        return RedirectResponse(url=f"https://auth.pushprh.com/authorize?rd={redirect_uri}")

    provider.authorize_access_token = fake_authorize_access_token
    provider.authorize_redirect = fake_authorize_redirect


def test_login_redirects_to_idp(client: TestClient) -> None:
    _patch_provider(client, {})
    resp = client.get("/auth/login", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "auth.pushprh.com/authorize" in resp.headers["location"]


def test_callback_allows_member_and_sets_session(client: TestClient) -> None:
    _patch_provider(
        client,
        {"sub": "u-1", "preferred_username": "alice", "email": "a@x.com", "groups": ["llm_users"]},
    )
    resp = client.get("/auth/callback?code=abc&state=xyz", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    # Session now authenticated: the home page shows the username, not "Sign in".
    home = client.get("/")
    assert "alice" in home.text
    assert "Sign in" not in home.text


def test_callback_denies_non_member(client: TestClient) -> None:
    _patch_provider(client, {"sub": "u-2", "name": "Eve", "groups": ["homelab_users"]})
    resp = client.get("/auth/callback?code=abc", follow_redirects=False)
    assert resp.status_code == 403
    assert "not authorized" in resp.text


def test_admin_group_sets_admin(client: TestClient) -> None:
    _patch_provider(
        client, {"sub": "u-3", "preferred_username": "root", "groups": ["homelab_admins"]}
    )
    client.get("/auth/callback?code=abc", follow_redirects=False)
    profile = client.get("/profile")
    assert profile.status_code == 200
    assert "Admin" in profile.text


def test_protected_route_redirects_when_anonymous(client: TestClient) -> None:
    resp = client.get("/profile", headers={"accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_logout_clears_session(client: TestClient) -> None:
    _patch_provider(client, {"sub": "u-4", "preferred_username": "bob", "groups": ["llm_users"]})
    client.get("/auth/callback?code=abc", follow_redirects=False)
    assert "bob" in client.get("/").text
    client.get("/auth/logout", follow_redirects=False)
    assert "Sign in" in client.get("/").text


def test_callback_handles_oauth_error(client: TestClient) -> None:
    provider = client.app.state.oauth.authelia  # type: ignore[attr-defined]

    async def boom(request: Any) -> dict[str, Any]:
        from authlib.integrations.starlette_client import OAuthError

        raise OAuthError("access_denied")

    provider.authorize_access_token = boom
    resp = client.get("/auth/callback?code=abc", follow_redirects=False)
    assert resp.status_code == 400
    assert "failed" in resp.text.lower()


def test_callback_rejects_token_without_subject(client: TestClient) -> None:
    _patch_provider(client, {"preferred_username": "nosub", "groups": ["llm_users"]})
    resp = client.get("/auth/callback?code=abc", follow_redirects=False)
    assert resp.status_code == 400
    assert "subject" in resp.text.lower()


@pytest.mark.parametrize("accept", ["application/json", ""])
def test_protected_route_401_for_non_browser(client: TestClient, accept: str) -> None:
    resp = client.get("/profile", headers={"accept": accept}, follow_redirects=False)
    assert resp.status_code == 401


def test_groups_from_userinfo_grant_access(client: TestClient) -> None:
    # Authelia puts groups in the USERINFO endpoint, not the ID token. Simulate an ID token
    # with no groups but a userinfo response that includes llm_users -> access granted.
    provider = client.app.state.oauth.authelia  # type: ignore[attr-defined]

    async def id_token_no_groups(request: Any) -> dict[str, Any]:
        return {"userinfo": {"sub": "u-ui", "preferred_username": "carol"}}

    async def userinfo_with_groups(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"sub": "u-ui", "groups": ["llm_users"], "email": "carol@x.com"}

    provider.authorize_access_token = id_token_no_groups
    provider.userinfo = userinfo_with_groups

    resp = client.get("/auth/callback?code=abc", follow_redirects=False)
    assert resp.status_code == 303
    assert "carol" in client.get("/").text


def test_denied_when_userinfo_lacks_group(client: TestClient) -> None:
    provider = client.app.state.oauth.authelia  # type: ignore[attr-defined]

    async def id_token(request: Any) -> dict[str, Any]:
        return {"userinfo": {"sub": "u-x", "preferred_username": "dan"}}

    async def userinfo_other(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"sub": "u-x", "groups": ["homelab_users"]}

    provider.authorize_access_token = id_token
    provider.userinfo = userinfo_other
    resp = client.get("/auth/callback?code=abc", follow_redirects=False)
    assert resp.status_code == 403
