"""User-facing docs for the shared chat, embedding, image, and MCP surfaces."""

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
    """Render the client guide with the deployment's live endpoints and model names."""
    return _templates().TemplateResponse(
        request,
        "docs.html",
        {
            "user": request.session.get("user"),
            "llm_base_url": settings.llm_public_url,
            "llm_model": settings.llm_model,
            "embedding_enabled": settings.llm_embedding_enabled,
            "embedding_model": settings.llm_embedding_model,
            "embedding_dimension": settings.llm_embedding_dimension,
            "image_model": settings.llm_image_model,
            "image_quality_model": settings.llm_image_quality_model,
            "image_generation_enabled": settings.llm_image_generation_enabled,
        },
    )
