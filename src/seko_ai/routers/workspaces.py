"""Hosted workspace management UI (create / list / stop / start / terminate)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from seko_ai.auth import get_app_settings
from seko_ai.config import Settings
from seko_ai.db import get_session
from seko_ai.deps import get_current_db_user, get_litellm_client, get_workspace_service
from seko_ai.harness import DEFAULT_HARNESS, HARNESS_CHOICES, normalize_harness
from seko_ai.logging_config import get_logger
from seko_ai.models import BackupTrigger, User, Workspace
from seko_ai.services import backups as backups_service
from seko_ai.services import ssh_keys as ssh_keys_service
from seko_ai.services.litellm_client import LiteLLMClient
from seko_ai.services.workspaces import WorkspaceError, WorkspaceService

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
log = get_logger("seko_ai.workspaces")


def _templates() -> Jinja2Templates:
    from seko_ai.app import TEMPLATES

    return TEMPLATES


def _panel(
    request: Request,
    svc: WorkspaceService,
    session: Session,
    user_id: int,
    *,
    error: str | None = None,
    notice: str | None = None,
) -> HTMLResponse:
    workspaces = svc.list_workspaces(session, user_id)
    rows = [(ws, svc.ssh_command(ws), svc.harness_command(ws)) for ws in workspaces]
    status_code = 400 if error else 200
    return _templates().TemplateResponse(
        request,
        "_workspaces_panel.html",
        {"rows": rows, "error": error, "notice": notice},
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
def workspaces_page(
    request: Request,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    svc: WorkspaceService = Depends(get_workspace_service),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> HTMLResponse:
    """Render the user's workspaces and the create form."""
    workspaces = svc.list_workspaces(session, user.id)
    rows = [(ws, svc.ssh_command(ws), svc.harness_command(ws)) for ws in workspaces]
    return _templates().TemplateResponse(
        request,
        "workspaces.html",
        {
            "user": request.session.get("user"),
            "rows": rows,
            "has_ssh_key": ssh_keys_service.has_keys(session, user.id),
            "max_workspaces": settings.max_workspaces_per_user,
            "harness_choices": HARNESS_CHOICES,
            "default_harness": DEFAULT_HARNESS,
            "error": None,
        },
    )


@router.post("", response_class=HTMLResponse)
async def create_workspace(
    request: Request,
    name: Annotated[str, Form()] = "workspace",
    harness: Annotated[str, Form()] = DEFAULT_HARNESS,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    svc: WorkspaceService = Depends(get_workspace_service),  # noqa: B008
    litellm: LiteLLMClient = Depends(get_litellm_client),  # noqa: B008
) -> HTMLResponse:
    """Provision a new hosted workspace."""
    try:
        await svc.create_workspace(
            session,
            litellm,
            user,
            name=name.strip() or "workspace",
            harness=normalize_harness(harness),
        )
    except WorkspaceError as exc:
        log.warning("workspace_create_failed", user_id=user.id, error=str(exc))
        return _panel(request, svc, session, user.id, error=str(exc))
    log.info("workspace_created", user_id=user.id, harness=normalize_harness(harness))
    return _panel(request, svc, session, user.id)


def _owned_workspace(session: Session, user: User, workspace_id: int) -> Workspace:
    stmt_ws = session.get(Workspace, workspace_id)
    if stmt_ws is None or stmt_ws.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return stmt_ws


@router.post("/{workspace_id}/stop", response_class=HTMLResponse)
def stop_workspace(
    request: Request,
    workspace_id: int,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    svc: WorkspaceService = Depends(get_workspace_service),  # noqa: B008
) -> HTMLResponse:
    ws = _owned_workspace(session, user, workspace_id)
    svc.stop_workspace(session, ws)
    return _panel(request, svc, session, user.id)


@router.post("/{workspace_id}/start", response_class=HTMLResponse)
def start_workspace(
    request: Request,
    workspace_id: int,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    svc: WorkspaceService = Depends(get_workspace_service),  # noqa: B008
) -> HTMLResponse:
    ws = _owned_workspace(session, user, workspace_id)
    svc.start_workspace(session, user, ws)
    return _panel(request, svc, session, user.id)


@router.post("/{workspace_id}/backup", response_class=HTMLResponse)
def backup_workspace(
    request: Request,
    workspace_id: int,
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    svc: WorkspaceService = Depends(get_workspace_service),  # noqa: B008
) -> HTMLResponse:
    """Take an on-demand restic backup of the workspace's encrypted volume."""
    ws = _owned_workspace(session, user, workspace_id)
    backup = backups_service.backup_workspace(
        session, svc.backend, ws, BackupTrigger.MANUAL
    )
    if not backup.succeeded:
        return _panel(request, svc, session, user.id, error="Backup failed. Please try again.")
    return _panel(request, svc, session, user.id, notice="Backup complete.")


@router.post("/{workspace_id}/terminate", response_class=HTMLResponse)
async def terminate_workspace(
    request: Request,
    workspace_id: int,
    backup: Annotated[str, Form()] = "",
    user: User = Depends(get_current_db_user),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    svc: WorkspaceService = Depends(get_workspace_service),  # noqa: B008
    litellm: LiteLLMClient = Depends(get_litellm_client),  # noqa: B008
) -> HTMLResponse:
    ws = _owned_workspace(session, user, workspace_id)
    # The UI prompts the user to back up on terminate; honor an opt-in final snapshot.
    if backup == "1":
        backups_service.backup_workspace(session, svc.backend, ws, BackupTrigger.ON_TERMINATE)
    await svc.terminate_workspace(session, litellm, ws)
    return _panel(request, svc, session, user.id)
