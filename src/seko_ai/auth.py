"""Authelia OIDC integration: OAuth client, claim extraction, and request dependencies."""

from __future__ import annotations

from typing import Any

from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, HTTPException, Request, status

from seko_ai.config import Settings, get_settings

OIDC_PROVIDER = "authelia"


def create_oauth(settings: Settings) -> OAuth:
    """Register the Authelia OIDC client and return the OAuth registry."""
    oauth = OAuth()
    oauth.register(
        name=OIDC_PROVIDER,
        server_metadata_url=f"{settings.oidc_issuer}/.well-known/openid-configuration",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        client_kwargs={"scope": "openid profile email groups"},
    )
    return oauth


def extract_claims(token: dict[str, Any]) -> dict[str, Any]:
    """Pull normalized identity fields out of an OIDC token's claims.

    Prefers the parsed ``userinfo`` block that Authlib attaches, falling back to the raw
    token. Returns a dict with subject, username, email, display_name, and groups.
    """
    claims: dict[str, Any] = token.get("userinfo") or token
    groups_raw = claims.get("groups") or []
    groups = [groups_raw] if isinstance(groups_raw, str) else list(groups_raw)

    subject = claims.get("sub")
    username = claims.get("preferred_username") or claims.get("name") or subject
    return {
        "subject": subject,
        "username": username,
        "email": claims.get("email"),
        "display_name": claims.get("name") or username,
        "groups": groups,
    }


def get_current_user(request: Request) -> dict[str, Any] | None:
    """Return the session user dict, or None if not signed in."""
    user = request.session.get("user")
    return user if isinstance(user, dict) else None


def require_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency: require an authenticated user."""
    user = get_current_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"Location": "/auth/login"},
        )
    return user


def require_admin(
    user: dict[str, Any] = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """FastAPI dependency: require an authenticated admin user."""
    if not user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return user


def get_oauth(request: Request) -> OAuth:
    """Return the app's OAuth registry."""
    oauth: OAuth = request.app.state.oauth
    return oauth


def get_app_settings(request: Request) -> Settings:
    """Return the app's settings (falls back to the global singleton)."""
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()
