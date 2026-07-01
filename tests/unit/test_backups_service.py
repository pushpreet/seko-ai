"""Tests for the backups service and restore-into-new-workspace."""

from __future__ import annotations

from sqlalchemy.orm import Session

from seko_ai.config import Settings
from seko_ai.models import BackupTrigger, User, WorkspaceStatus
from seko_ai.services import backups as bs
from seko_ai.services.workspaces import WorkspaceService
from tests.fakes import FakeBackend, FakeLiteLLMClient


def _user(session: Session) -> User:
    u = User(subject="s", username="alice", ssh_public_key="ssh-ed25519 AAAA")
    session.add(u)
    session.flush()
    return u


async def _make_workspace(session: Session, settings: Settings, backend: FakeBackend):
    svc = WorkspaceService(settings, backend)
    user = _user(session)
    ws = await svc.create_workspace(session, FakeLiteLLMClient(), user, name="w")
    return svc, user, ws


async def test_backup_workspace_records_snapshot(
    db_session: Session, settings: Settings
) -> None:
    backend = FakeBackend()
    _, _, ws = await _make_workspace(db_session, settings, backend)
    backup = bs.backup_workspace(db_session, backend, ws, BackupTrigger.MANUAL)
    assert backup.succeeded is True
    assert backup.snapshot_id == "snap-0001"
    assert backend.backed_up[0][0] == f"{ws.volume_path}/cipher"
    assert "trigger:manual" in backend.backed_up[0][1]
    assert bs.list_workspace_backups(db_session, ws.id) == [backup]


async def test_backup_failure_recorded(db_session: Session, settings: Settings) -> None:
    backend = FakeBackend(fail_on="backup_volume")
    _, _, ws = await _make_workspace(db_session, settings, backend)
    backup = bs.backup_workspace(db_session, backend, ws, BackupTrigger.NIGHTLY)
    assert backup.succeeded is False
    assert backup.snapshot_id == ""
    # Failed backups are excluded from the user-facing list.
    assert bs.list_user_backups(db_session, ws.user_id) == []


async def test_list_user_backups_scoped(db_session: Session, settings: Settings) -> None:
    backend = FakeBackend()
    _, user, ws = await _make_workspace(db_session, settings, backend)
    bs.backup_workspace(db_session, backend, ws, BackupTrigger.MANUAL)
    # a different user has none
    other = User(subject="o", username="eve")
    db_session.add(other)
    db_session.flush()
    assert len(bs.list_user_backups(db_session, user.id)) == 1
    assert bs.list_user_backups(db_session, other.id) == []


async def test_restore_creates_new_workspace_from_snapshot(
    db_session: Session, settings: Settings
) -> None:
    backend = FakeBackend()
    svc, user, ws = await _make_workspace(db_session, settings, backend)
    backup = bs.backup_workspace(db_session, backend, ws, BackupTrigger.MANUAL)
    # terminate the original so we have quota room
    await svc.terminate_workspace(db_session, FakeLiteLLMClient(), ws)

    restored = await svc.restore_workspace(db_session, FakeLiteLLMClient(), user, backup)
    assert restored.id != ws.id
    assert restored.status == WorkspaceStatus.RUNNING
    assert "restored" in restored.name.lower()
    # backend restored the snapshot into the new cipher dir before provisioning
    assert backend.restored[0][0] == backup.snapshot_id
    assert backend.restored[0][1] == f"{restored.volume_path}/cipher"


async def test_get_user_backup_ownership(db_session: Session, settings: Settings) -> None:
    backend = FakeBackend()
    _, user, ws = await _make_workspace(db_session, settings, backend)
    backup = bs.backup_workspace(db_session, backend, ws, BackupTrigger.MANUAL)
    assert bs.get_user_backup(db_session, user.id, backup.id) is backup
    assert bs.get_user_backup(db_session, user.id + 99, backup.id) is None
