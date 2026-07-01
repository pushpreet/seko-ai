"""Tests for scheduled maintenance (nightly backups + idle reaper)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from seko_ai import management
from seko_ai.config import Settings
from seko_ai.models import User, WorkspaceStatus
from seko_ai.services.workspaces import WorkspaceService
from tests.fakes import FakeBackend, FakeLiteLLMClient


async def _make_running_workspace(session: Session, settings: Settings, backend: FakeBackend):
    svc = WorkspaceService(settings, backend)
    user = User(subject="s", username="alice", ssh_public_key="ssh-ed25519 AAAA")
    session.add(user)
    session.flush()
    ws = await svc.create_workspace(session, FakeLiteLLMClient(), user, name="w")
    return svc, ws


async def test_nightly_backs_up_all_active(db_session: Session, settings: Settings) -> None:
    backend = FakeBackend()
    _, ws = await _make_running_workspace(db_session, settings, backend)
    count = management.nightly_backups(db_session, backend)
    assert count == 1
    assert backend.backed_up


async def test_nightly_skips_terminated(db_session: Session, settings: Settings) -> None:
    backend = FakeBackend()
    svc, ws = await _make_running_workspace(db_session, settings, backend)
    await svc.terminate_workspace(db_session, FakeLiteLLMClient(), ws)
    assert management.nightly_backups(db_session, backend) == 0


async def test_reap_stops_idle_workspace(db_session: Session, settings: Settings) -> None:
    backend = FakeBackend()
    svc, ws = await _make_running_workspace(db_session, settings, backend)
    # make it look idle (last_active well beyond the threshold)
    ws.last_active_at = datetime.now(UTC) - timedelta(hours=settings.workspace_idle_stop_hours + 1)
    db_session.flush()
    stopped = management.reap_idle_workspaces(db_session, svc)
    assert stopped == [ws.id]
    assert ws.status == WorkspaceStatus.STOPPED


async def test_reap_keeps_active_workspace(db_session: Session, settings: Settings) -> None:
    backend = FakeBackend()
    svc, ws = await _make_running_workspace(db_session, settings, backend)
    # just created -> not idle
    assert management.reap_idle_workspaces(db_session, svc) == []
    assert ws.status == WorkspaceStatus.RUNNING


async def test_reap_disabled_when_threshold_zero(db_session: Session) -> None:
    settings = Settings(
        master_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        workspace_idle_stop_hours=0,
    )
    backend = FakeBackend()
    svc, ws = await _make_running_workspace(db_session, settings, backend)
    ws.last_active_at = datetime.now(UTC) - timedelta(days=30)
    db_session.flush()
    assert management.reap_idle_workspaces(db_session, svc) == []
    assert ws.status == WorkspaceStatus.RUNNING
