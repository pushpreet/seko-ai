"""Prometheus metrics for seko-ai (scraped by the homelab Prometheus)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from seko_ai.models import User, Workspace, WorkspaceStatus

LOGINS = Counter("seko_logins_total", "Successful sign-ins")
KEYS_ISSUED = Counter("seko_keys_issued_total", "LiteLLM virtual keys issued")
WORKSPACES_CREATED = Counter("seko_workspaces_created_total", "Workspaces created")
WORKSPACES_TERMINATED = Counter("seko_workspaces_terminated_total", "Workspaces terminated")
BACKUPS = Counter("seko_backups_total", "Backups taken", ["trigger", "result"])

WORKSPACES_ACTIVE = Gauge("seko_workspaces_active", "Non-terminated workspaces")
USERS_TOTAL = Gauge("seko_users_total", "Provisioned users")


def refresh_gauges(session: Session) -> None:
    """Refresh point-in-time gauges from the database (called on scrape)."""
    active = session.execute(
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.status != WorkspaceStatus.TERMINATED)
    ).scalar_one()
    users = session.execute(select(func.count()).select_from(User)).scalar_one()
    WORKSPACES_ACTIVE.set(active)
    USERS_TOTAL.set(users)
