"""User profile page + SSH public key management."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from seko_ai.db import get_session
from seko_ai.deps import get_current_db_user
from seko_ai.models import User

router = APIRouter(tags=["profile"])

_VALID_KEY_PREFIXES = ("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-", "sk-ssh-", "sk-ecdsa-")


def _templates() -> Jinja2Templates:
    from seko_ai.app import TEMPLATES

    return TEMPLATES


@router.get("/profile", response_class=HTMLResponse)
def profile(
    request: Request,
    user: User = Depends(get_current_db_user),  # noqa: B008
) -> HTMLResponse:
    """Render the signed-in user's profile."""
    return _templates().TemplateResponse(
        request,
        "profile.html",
        {"user": _session_user(request), "ssh_public_key": user.ssh_public_key, "error": None},
    )


@router.post("/profile/ssh-key", response_class=HTMLResponse)
def set_ssh_key(
    request: Request,
    ssh_public_key: Annotated[str, Form()],
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> Any:
    """Store or clear the user's SSH public key (used for hosted-workspace access)."""
    key = ssh_public_key.strip()
    if key and not key.startswith(_VALID_KEY_PREFIXES):
        return _templates().TemplateResponse(
            request,
            "profile.html",
            {
                "user": _session_user(request),
                "ssh_public_key": user.ssh_public_key,
                "error": "That doesn't look like an OpenSSH public key.",
            },
            status_code=400,
        )
    user.ssh_public_key = key or None
    session.flush()
    return RedirectResponse(url="/profile", status_code=303)


def _session_user(request: Request) -> dict[str, object] | None:
    user = request.session.get("user")
    return user if isinstance(user, dict) else None
