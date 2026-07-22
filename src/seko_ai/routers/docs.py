"""User-facing docs: how to reach the shared LLM via the direct (OpenAI-compatible) API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from seko_ai.auth import get_app_settings
from seko_ai.config import Settings
from seko_ai.deps import get_current_db_user
from seko_ai.models import User

router = APIRouter(prefix="/docs", tags=["docs"])


def _templates() -> Jinja2Templates:
    from seko_ai.app import TEMPLATES

    return TEMPLATES


@router.get("", response_class=HTMLResponse)
def docs_page(
    request: Request,
    user: User = Depends(get_current_db_user),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> HTMLResponse:
    """Explain how to reach the backend LLM via the direct API, with live endpoint/model."""
    return _templates().TemplateResponse(
        request,
        "docs.html",
        {
            "user": request.session.get("user"),
            "llm_base_url": settings.llm_public_url,
            "llm_model": settings.llm_model,
        },
    )
