"""Backups view + restore-into-new-workspace."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from seko_ai.db import get_session
from seko_ai.deps import get_current_db_user, get_litellm_client, get_workspace_service
from seko_ai.logging_config import get_logger
from seko_ai.models import User
from seko_ai.services import backups as backups_service
from seko_ai.services.litellm_client import LiteLLMClient
from seko_ai.services.workspaces import WorkspaceError, WorkspaceService

router = APIRouter(prefix="/backups", tags=["backups"])
log = get_logger("seko_ai.backups_router")


def _templates() -> Jinja2Templates:
    from seko_ai.app import TEMPLATES

    return TEMPLATES


@router.get("", response_class=HTMLResponse)
def backups_page(
    request: Request,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> HTMLResponse:
    """List the user's backups with restore actions."""
    user_backups = backups_service.list_user_backups(session, user.id)
    return _templates().TemplateResponse(
        request,
        "backups.html",
        {"user": request.session.get("user"), "backups": user_backups, "error": None},
    )


@router.post("/{backup_id}/restore", response_class=HTMLResponse)
async def restore_backup(
    request: Request,
    backup_id: int,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    svc: WorkspaceService = Depends(get_workspace_service),  # noqa: B008
    litellm: LiteLLMClient = Depends(get_litellm_client),  # noqa: B008
) -> HTMLResponse:
    """Restore a backup into a fresh workspace."""
    backup = backups_service.get_user_backup(session, user.id, backup_id)
    if backup is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup not found")

    error = None
    try:
        await svc.restore_workspace(session, litellm, user, backup)
        log.info("restore_ok", user_id=user.id, backup_id=backup_id)
    except WorkspaceError as exc:
        log.warning("restore_failed", user_id=user.id, error=str(exc))
        error = str(exc)

    user_backups = backups_service.list_user_backups(session, user.id)
    return _templates().TemplateResponse(
        request,
        "_backups_list.html",
        {"backups": user_backups, "error": error, "restored": error is None},
        status_code=400 if error else 200,
    )
