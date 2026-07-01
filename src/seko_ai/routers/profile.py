"""User profile page."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from seko_ai.auth import require_user

router = APIRouter(tags=["profile"])


@router.get("/profile", response_class=HTMLResponse)
def profile(
    request: Request,
    user: dict[str, Any] = Depends(require_user),  # noqa: B008
) -> HTMLResponse:
    """Render the signed-in user's profile."""
    from seko_ai.app import TEMPLATES

    return TEMPLATES.TemplateResponse(request, "profile.html", {"user": user})
