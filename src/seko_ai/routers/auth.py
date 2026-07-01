"""OIDC login/logout routes and the callback that provisions users."""

from __future__ import annotations

from typing import Any

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from starlette.responses import HTMLResponse

from seko_ai import metrics
from seko_ai.auth import (
    OIDC_PROVIDER,
    extract_claims,
    get_app_settings,
    get_oauth,
)
from seko_ai.config import Settings
from seko_ai.db import get_session
from seko_ai.logging_config import get_logger
from seko_ai.services import users as users_service

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger("seko_ai.auth")


def _provider(oauth: OAuth):  # type: ignore[no-untyped-def]
    return getattr(oauth, OIDC_PROVIDER)


@router.get("/login")
async def login(
    request: Request,
    oauth: OAuth = Depends(get_oauth),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> Any:
    """Kick off the OIDC authorization-code flow."""
    redirect_uri = f"{settings.base_url.rstrip('/')}{settings.oidc_redirect_path}"
    return await _provider(oauth).authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def callback(
    request: Request,
    oauth: OAuth = Depends(get_oauth),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> Any:
    """Handle the OIDC redirect: exchange the code, gate on group, provision the user."""
    try:
        token = await _provider(oauth).authorize_access_token(request)
    except OAuthError as exc:
        log.warning("oidc_error", error=str(exc))
        return HTMLResponse("Authentication failed.", status_code=400)

    claims = extract_claims(token)
    if not claims.get("subject"):
        return HTMLResponse("Invalid identity token (no subject).", status_code=400)

    decision = users_service.evaluate_access(
        claims["groups"], settings.oidc_users_group, settings.oidc_admins_group
    )
    if not decision.allowed:
        log.info("access_denied", subject=claims["subject"], groups=claims["groups"])
        return HTMLResponse(
            "Your account is not authorized to use seko-ai. "
            f"Ask an admin to add you to the '{settings.oidc_users_group}' group.",
            status_code=403,
        )

    user = users_service.upsert_user(
        session,
        subject=claims["subject"],
        username=claims["username"],
        email=claims.get("email"),
        display_name=claims.get("display_name"),
        is_admin=decision.is_admin,
    )
    request.session["user"] = users_service.session_payload(user)
    metrics.LOGINS.inc()
    log.info("login", subject=user.subject, is_admin=user.is_admin)
    return RedirectResponse(url="/", status_code=303)


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Clear the local session."""
    request.session.pop("user", None)
    return RedirectResponse(url="/", status_code=303)
