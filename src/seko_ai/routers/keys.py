"""API key management UI (LiteLLM virtual keys)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from seko_ai.auth import get_app_settings
from seko_ai.config import Settings
from seko_ai.db import get_session
from seko_ai.deps import get_current_db_user, get_litellm_client
from seko_ai.logging_config import get_logger
from seko_ai.models import User
from seko_ai.services import keys as keys_service
from seko_ai.services.litellm_client import LiteLLMClient, LiteLLMError

router = APIRouter(prefix="/keys", tags=["keys"])
log = get_logger("seko_ai.keys")


def _templates() -> Jinja2Templates:
    from seko_ai.app import TEMPLATES

    return TEMPLATES


@router.get("", response_class=HTMLResponse)
def keys_page(
    request: Request,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> HTMLResponse:
    """Render the user's API keys and connection details."""
    user_keys = keys_service.list_user_keys(session, user.id)
    return _templates().TemplateResponse(
        request,
        "keys.html",
        {
            "user": _session_user(request),
            "keys": user_keys,
            "endpoint": settings.llm_public_url,
            "model": settings.llm_model,
            "new_key": None,
        },
    )


@router.post("", response_class=HTMLResponse)
async def create_key(
    request: Request,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    client: LiteLLMClient = Depends(get_litellm_client),  # noqa: B008
) -> HTMLResponse:
    """Mint a new virtual key and render the one-time reveal + updated list."""
    try:
        api_key, plaintext = await keys_service.create_key_for_user(
            session, client, user, settings
        )
    except (LiteLLMError, ValueError) as exc:
        log.warning("key_create_failed", error=str(exc))
        return _error_fragment(request, "Could not create a key. Please try again.")

    log.info("key_created", user_id=user.id, key_id=api_key.id)
    return _templates().TemplateResponse(
        request,
        "_keys_panel.html",
        {
            "keys": keys_service.list_user_keys(session, user.id),
            "new_key": plaintext,
        },
    )


@router.post("/{key_id}/rotate", response_class=HTMLResponse)
async def rotate_key(
    request: Request,
    key_id: int,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    client: LiteLLMClient = Depends(get_litellm_client),  # noqa: B008
) -> HTMLResponse:
    """Rotate an existing key (revoke + reissue)."""
    api_key = keys_service.get_key(session, user.id, key_id)
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    try:
        _, plaintext = await keys_service.rotate_key(session, client, user, api_key, settings)
    except (LiteLLMError, ValueError) as exc:
        log.warning("key_rotate_failed", error=str(exc))
        return _error_fragment(request, "Could not rotate the key. Please try again.")

    log.info("key_rotated", user_id=user.id, old_key_id=key_id)
    return _templates().TemplateResponse(
        request,
        "_keys_panel.html",
        {"keys": keys_service.list_user_keys(session, user.id), "new_key": plaintext},
    )


@router.post("/{key_id}/revoke", response_class=HTMLResponse)
async def revoke_key(
    request: Request,
    key_id: int,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    client: LiteLLMClient = Depends(get_litellm_client),  # noqa: B008
) -> HTMLResponse:
    """Revoke a key."""
    api_key = keys_service.get_key(session, user.id, key_id)
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    try:
        await keys_service.revoke_key(session, client, api_key)
    except LiteLLMError as exc:
        log.warning("key_revoke_failed", error=str(exc))
        return _error_fragment(request, "Could not revoke the key. Please try again.")

    log.info("key_revoked", user_id=user.id, key_id=key_id)
    return _templates().TemplateResponse(
        request,
        "_keys_panel.html",
        {"keys": keys_service.list_user_keys(session, user.id), "new_key": None},
    )


def _session_user(request: Request) -> dict[str, object] | None:
    user = request.session.get("user")
    return user if isinstance(user, dict) else None


def _error_fragment(request: Request, message: str) -> HTMLResponse:
    return _templates().TemplateResponse(
        request, "_error.html", {"message": message}, status_code=502
    )
