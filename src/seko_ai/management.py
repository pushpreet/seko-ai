"""Scheduled maintenance: nightly backups and idle-workspace auto-stop.

Invoked from the host via systemd timers (see the homelab deployment), e.g.:
    python -m seko_ai.management nightly-backups
    python -m seko_ai.management reap-idle
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from seko_ai.config import Settings, get_settings
from seko_ai.logging_config import get_logger
from seko_ai.models import BackupTrigger, Workspace, WorkspaceStatus
from seko_ai.services import backups as backups_service
from seko_ai.services.workspaces import ContainerBackend, WorkspaceService

log = get_logger("seko_ai.management")


def _now() -> datetime:
    return datetime.now(UTC)


def nightly_backups(session: Session, backend: ContainerBackend) -> int:
    """Back up every non-terminated workspace. Returns the count backed up."""
    stmt = select(Workspace).where(Workspace.status != WorkspaceStatus.TERMINATED)
    workspaces = list(session.execute(stmt).scalars().all())
    count = 0
    for ws in workspaces:
        backup = backups_service.backup_workspace(session, backend, ws, BackupTrigger.NIGHTLY)
        if backup.succeeded:
            count += 1
    log.info("nightly_backups_done", total=len(workspaces), succeeded=count)
    return count


def reap_idle_workspaces(
    session: Session, svc: WorkspaceService, *, now: datetime | None = None
) -> list[int]:
    """Stop RUNNING workspaces idle beyond the configured threshold.

    Idle is measured from ``last_active_at`` (falling back to ``updated_at``). The home
    volume is preserved, so a stopped workspace is restartable. Returns stopped ids.
    """
    threshold_hours = svc.settings.workspace_idle_stop_hours
    if threshold_hours <= 0:
        return []
    cutoff = (now or _now()) - timedelta(hours=threshold_hours)
    stmt = select(Workspace).where(Workspace.status == WorkspaceStatus.RUNNING)
    stopped: list[int] = []
    for ws in session.execute(stmt).scalars().all():
        last_active = ws.last_active_at or ws.updated_at
        if last_active.tzinfo is None:
            last_active = last_active.replace(tzinfo=UTC)
        if last_active < cutoff:
            svc.stop_workspace(session, ws)
            stopped.append(ws.id)
    if stopped:
        log.info("reaped_idle", workspaces=stopped)
    return stopped


def _build_backend(settings: Settings) -> ContainerBackend:  # pragma: no cover - needs epyc
    from seko_ai.services.docker_backend import DockerBackend

    return DockerBackend(
        settings.docker_host,
        restic_repository=settings.restic_repository,
        restic_password=settings.restic_password,
    )


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI wiring
    import sys

    from seko_ai.db import session_scope

    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in {"nightly-backups", "reap-idle"}:
        print("usage: python -m seko_ai.management [nightly-backups|reap-idle]")
        return 2
    settings = get_settings()
    backend = _build_backend(settings)
    with session_scope() as session:
        if args[0] == "nightly-backups":
            nightly_backups(session, backend)
        else:
            reap_idle_workspaces(session, WorkspaceService(settings, backend))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
