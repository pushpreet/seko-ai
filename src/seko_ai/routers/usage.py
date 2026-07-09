"""Usage dashboard: per-user LLM usage; admins can see everyone."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from seko_ai.db import get_session
from seko_ai.deps import get_current_db_user, get_litellm_client
from seko_ai.models import ApiKey, User
from seko_ai.services import usage as usage_service
from seko_ai.services.litellm_client import LiteLLMClient

router = APIRouter(prefix="/usage", tags=["usage"])


def _templates() -> Jinja2Templates:
    from seko_ai.app import TEMPLATES

    return TEMPLATES


@router.get("", response_class=HTMLResponse)
async def usage_page(
    request: Request,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    litellm: LiteLLMClient = Depends(get_litellm_client),  # noqa: B008
) -> HTMLResponse:
    """Show the signed-in user's usage; admins additionally get an all-users table.

    Usage is computed from a single global LiteLLM activity fetch, bucketed per user by
    their keys (see ``services.usage``), so every user gets their own real totals.
    """
    users = (
        list(session.execute(select(User)).scalars().all()) if user.is_admin else [user]
    )
    user_ids = [u.id for u in users]
    api_keys = list(
        session.execute(select(ApiKey).where(ApiKey.user_id.in_(user_ids))).scalars().all()
    )
    summaries = await usage_service.collect(litellm, users, api_keys)

    mine = summaries[user.id]
    everyone = [summaries[u.id] for u in users] if user.is_admin else None
    return _templates().TemplateResponse(
        request,
        "usage.html",
        {"user": request.session.get("user"), "mine": mine, "everyone": everyone},
    )
