"""Self-host kit UI: generate a personalized bootstrap to run the workspace locally."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
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
from seko_ai.services import kit as kit_service
from seko_ai.services.litellm_client import LiteLLMClient, LiteLLMError

router = APIRouter(prefix="/selfhost", tags=["selfhost"])
log = get_logger("seko_ai.selfhost")


def _templates() -> Jinja2Templates:
    from seko_ai.app import TEMPLATES

    return TEMPLATES


@router.get("", response_class=HTMLResponse)
def selfhost_page(
    request: Request,
    user: User = Depends(get_current_db_user),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> HTMLResponse:
    """Explain the self-host flow and offer to generate a kit."""
    return _templates().TemplateResponse(
        request,
        "selfhost.html",
        {
            "user": request.session.get("user"),
            "has_ssh_key": bool(user.ssh_public_key),
            "image": settings.workspace_image,
            "kit": None,
            "error": None,
        },
    )


@router.post("/kit", response_class=HTMLResponse)
async def generate_kit(
    request: Request,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    litellm: LiteLLMClient = Depends(get_litellm_client),  # noqa: B008
) -> HTMLResponse:
    """Mint a key and render a personalized kit (files shown once, key included)."""
    if not user.ssh_public_key:
        return _templates().TemplateResponse(
            request,
            "_selfhost_kit.html",
            {"kit": None, "error": "Add an SSH public key on your profile first."},
            status_code=400,
        )
    try:
        _, plaintext = await keys_service.create_key_for_user(session, litellm, user, settings)
    except (LiteLLMError, ValueError) as exc:
        log.warning("kit_key_failed", error=str(exc))
        return _templates().TemplateResponse(
            request,
            "_selfhost_kit.html",
            {"kit": None, "error": "Could not issue a key. Please try again."},
            status_code=502,
        )

    kit = kit_service.build_kit(settings, api_key=plaintext, authorized_keys=user.ssh_public_key)
    log.info("kit_generated", user_id=user.id)
    return _templates().TemplateResponse(
        request, "_selfhost_kit.html", {"kit": kit, "error": None}
    )
