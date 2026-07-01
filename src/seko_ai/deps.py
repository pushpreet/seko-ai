"""Shared FastAPI dependencies (current DB user, LiteLLM client)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from seko_ai.auth import get_app_settings, require_user
from seko_ai.db import get_session
from seko_ai.models import User
from seko_ai.services.litellm_client import LiteLLMClient
from seko_ai.services.users import get_user_by_subject
from seko_ai.services.workspaces import ContainerBackend, WorkspaceService


def get_current_db_user(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008
    session_user: dict[str, Any] = Depends(require_user),  # noqa: B008
) -> User:
    """Load the ORM user for the authenticated session, or 401 if it vanished."""
    user = get_user_by_subject(session, session_user["subject"])
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
    return user


async def get_litellm_client(request: Request) -> AsyncIterator[LiteLLMClient]:
    """Yield a LiteLLM admin client bound to the app settings."""
    settings = get_app_settings(request)
    async with LiteLLMClient.from_settings(settings) as client:
        yield client


def get_container_backend(request: Request) -> ContainerBackend:
    """Return the app's container backend (Docker-over-SSH), cached on app.state."""
    backend = getattr(request.app.state, "container_backend", None)
    if backend is None:
        from seko_ai.services.docker_backend import DockerBackend

        settings = get_app_settings(request)
        backend = DockerBackend(settings.docker_host)
        request.app.state.container_backend = backend
    return backend


def get_workspace_service(request: Request) -> WorkspaceService:
    """Build a WorkspaceService from settings + the container backend."""
    settings = get_app_settings(request)
    return WorkspaceService(settings, get_container_backend(request))
