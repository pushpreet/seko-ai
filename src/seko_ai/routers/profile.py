"""Profile / settings: identity + GitHub-style SSH key management."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from seko_ai.db import get_session
from seko_ai.deps import get_current_db_user
from seko_ai.logging_config import get_logger
from seko_ai.models import User
from seko_ai.services import ssh_keys as ssh_keys_service

router = APIRouter(tags=["profile"])
log = get_logger("seko_ai.profile")


def _templates() -> Jinja2Templates:
    from seko_ai.app import TEMPLATES

    return TEMPLATES


def _keys_panel(
    request: Request, session: Session, user_id: int, *, error: str | None = None
) -> HTMLResponse:
    keys = ssh_keys_service.list_keys(session, user_id)
    return _templates().TemplateResponse(
        request,
        "_ssh_keys_panel.html",
        {"ssh_keys": keys, "error": error},
        status_code=422 if error else 200,
    )


@router.get("/profile", response_class=HTMLResponse)
def profile(
    request: Request,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> HTMLResponse:
    """Render the signed-in user's profile + SSH keys."""
    return _templates().TemplateResponse(
        request,
        "profile.html",
        {
            "user": request.session.get("user"),
            "ssh_keys": ssh_keys_service.list_keys(session, user.id),
            "error": None,
        },
    )


@router.post("/profile/ssh-keys", response_class=HTMLResponse)
def add_ssh_key(
    request: Request,
    title: Annotated[str, Form()],
    public_key: Annotated[str, Form()],
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> HTMLResponse:
    """Add a new SSH public key."""
    try:
        key = ssh_keys_service.add_key(session, user, title=title, public_key=public_key)
    except ssh_keys_service.InvalidSSHKey as exc:
        return _keys_panel(request, session, user.id, error=str(exc))
    log.info("ssh_key_added", user_id=user.id, key_id=key.id)
    return _keys_panel(request, session, user.id)


@router.post("/profile/ssh-keys/{key_id}/delete", response_class=HTMLResponse)
def delete_ssh_key(
    request: Request,
    key_id: int,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> HTMLResponse:
    """Delete an SSH public key."""
    ssh_keys_service.delete_key(session, user.id, key_id)
    return _keys_panel(request, session, user.id)
