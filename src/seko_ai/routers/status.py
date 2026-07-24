"""Service-status UI: a live banner, a status page, and the admin maintenance toggle."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from seko_ai.auth import get_app_settings, get_current_user, require_admin
from seko_ai.config import Settings
from seko_ai.db import get_session
from seko_ai.logging_config import get_logger
from seko_ai.services import status as status_service

router = APIRouter(prefix="/status", tags=["status"])
log = get_logger("seko_ai.status_router")


def _templates() -> Jinja2Templates:
    from seko_ai.app import TEMPLATES

    return TEMPLATES


def _session_user(request: Request) -> dict[str, Any] | None:
    return get_current_user(request)


@router.get("", response_class=HTMLResponse)
def status_page(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008
) -> HTMLResponse:
    """Full status page users can visit any time (up/down, since, maintenance, history)."""
    state = status_service.get_or_create_state(session)
    user = _session_user(request)
    return _templates().TemplateResponse(
        request,
        "status.html",
        {
            "user": user,
            "state": state,
            "events": status_service.recent_events(session),
            "is_admin": bool(user and user.get("is_admin")),
        },
    )


@router.get("/banner", response_class=HTMLResponse)
def status_banner(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008
) -> HTMLResponse:
    """HTMX fragment embedded in every page; renders nothing while the service is up."""
    state = status_service.get_or_create_state(session)
    return _templates().TemplateResponse(request, "_status_banner.html", {"state": state})


@router.post("/maintenance/start", response_class=HTMLResponse)
def maintenance_start(
    request: Request,
    message: str = Form(default=""),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    _admin: dict[str, Any] = Depends(require_admin),  # noqa: B008
) -> RedirectResponse:
    """Admin: begin a maintenance window (suppresses up/down emails)."""
    status_service.start_maintenance(session, settings, message=message)
    return RedirectResponse(url="/status", status_code=303)


@router.post("/maintenance/end", response_class=HTMLResponse)
def maintenance_end(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    _admin: dict[str, Any] = Depends(require_admin),  # noqa: B008
) -> RedirectResponse:
    """Admin: end the maintenance window and resume alerting."""
    status_service.end_maintenance(session, settings)
    return RedirectResponse(url="/status", status_code=303)
