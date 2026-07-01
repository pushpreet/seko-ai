"""Backup lifecycle: snapshot workspace volumes and list them for restore."""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from seko_ai.logging_config import get_logger
from seko_ai.models import Backup, BackupTrigger, Workspace
from seko_ai.services.workspaces import ContainerBackend

log = get_logger("seko_ai.backups")


def list_workspace_backups(session: Session, workspace_id: int) -> list[Backup]:
    """Return a workspace's backups, newest first."""
    stmt = (
        select(Backup)
        .where(Backup.workspace_id == workspace_id)
        .order_by(Backup.created_at.desc())
    )
    return list(session.execute(stmt).scalars().all())


def list_user_backups(session: Session, user_id: int) -> list[Backup]:
    """Return all successful backups belonging to a user's workspaces, newest first."""
    stmt = (
        select(Backup)
        .join(Workspace, Backup.workspace_id == Workspace.id)
        .where(Workspace.user_id == user_id, Backup.succeeded.is_(True))
        .order_by(Backup.created_at.desc())
    )
    return list(session.execute(stmt).scalars().all())


def get_user_backup(session: Session, user_id: int, backup_id: int) -> Backup | None:
    """Return a backup by id if it belongs to one of the user's workspaces."""
    stmt = (
        select(Backup)
        .join(Workspace, Backup.workspace_id == Workspace.id)
        .where(Backup.id == backup_id, Workspace.user_id == user_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def backup_workspace(
    session: Session,
    backend: ContainerBackend,
    workspace: Workspace,
    trigger: BackupTrigger,
) -> Backup:
    """Snapshot a workspace's ciphertext volume with restic and record the result."""
    cipher_path = f"{workspace.volume_path}/cipher"
    tags = [f"workspace:{workspace.id}", f"user:{workspace.user_id}", f"trigger:{trigger.value}"]
    started = time.monotonic()
    try:
        result = backend.backup_volume(cipher_path, tags)
    except Exception as exc:
        log.warning("backup_failed", workspace_id=workspace.id, error=str(exc))
        backup = Backup(
            workspace_id=workspace.id,
            snapshot_id="",
            trigger=trigger,
            succeeded=False,
            detail=str(exc)[:500],
            duration_seconds=time.monotonic() - started,
        )
        session.add(backup)
        session.flush()
        return backup

    backup = Backup(
        workspace_id=workspace.id,
        snapshot_id=result.snapshot_id,
        trigger=trigger,
        size_bytes=result.size_bytes,
        succeeded=True,
        duration_seconds=time.monotonic() - started,
    )
    session.add(backup)
    session.flush()
    log.info("backup_created", workspace_id=workspace.id, snapshot=result.snapshot_id)
    return backup
