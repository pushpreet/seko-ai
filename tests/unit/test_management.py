"""Tests for scheduled maintenance (nightly backups + idle reaper)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from seko_ai import management
from seko_ai.config import Settings
from seko_ai.models import (
    ApiKey,
    Backup,
    BackupTrigger,
    ServiceState,
    ServiceStatus,
    SSHKey,
    StatusEvent,
    User,
    Workspace,
    WorkspaceStatus,
)
from seko_ai.services import backups as backups_service
from seko_ai.services.litellm_client import LiteLLMError
from seko_ai.services.retirement import (
    CONFIRMATION,
    RetirementError,
    retire_workspaces,
)
from seko_ai.services.workspaces import WorkspaceService
from tests.fakes import FakeBackend, FakeLiteLLMClient


async def _make_running_workspace(session: Session, settings: Settings, backend: FakeBackend):
    svc = WorkspaceService(settings, backend)
    user = User(subject="s", username="alice")
    session.add(user)
    session.flush()
    from tests.conftest import add_ssh_key

    add_ssh_key(session, user)
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


async def _make_retirement_resources(
    session: Session, settings: Settings, backend: FakeBackend
) -> tuple[User, Workspace, ApiKey]:
    _, workspace = await _make_running_workspace(session, settings, backend)
    user = workspace.user
    user.ssh_public_key = "legacy-public-key"
    normal_key = ApiKey(
        user_id=user.id,
        workspace_id=None,
        litellm_key_id="normal-token",
        key_alias="normal-alias",
        masked_key="sk-no…rmal",
        active=True,
    )
    session.add(normal_key)
    backups_service.backup_workspace(
        session,
        backend,
        workspace,
        trigger=BackupTrigger.MANUAL,
    )
    session.add(ServiceState(id=1))
    session.add(
        StatusEvent(
            from_status=ServiceStatus.UNKNOWN,
            to_status=ServiceStatus.UP,
        )
    )
    session.flush()
    return user, workspace, normal_key


async def test_workspace_retirement_defaults_to_secret_free_dry_run(
    db_session: Session, settings: Settings
) -> None:
    backend = FakeBackend()
    user, workspace, _ = await _make_retirement_resources(db_session, settings, backend)
    litellm = FakeLiteLLMClient()

    inventory = await retire_workspaces(db_session, settings, backend, litellm)
    report = inventory.render(execute=False)

    assert "workspace retirement mode: DRY RUN" in report
    assert workspace.container_name in report
    assert workspace.volume_path in report
    assert "snap-0001" in report
    assert CONFIRMATION in report
    assert user.wrapped_dek not in report
    assert user.ssh_public_key not in report
    assert not litellm.deleted
    assert not backend.stopped
    assert db_session.get(Workspace, workspace.id) is workspace


async def test_workspace_retirement_success_preserves_normal_keys_and_status(
    db_session: Session, settings: Settings
) -> None:
    backend = FakeBackend()
    user, workspace, normal_key = await _make_retirement_resources(
        db_session, settings, backend
    )
    backend.add_managed_container(
        "seko-ws-99-orphan",
        owner_id="99",
        home_path=f"{settings.workspace_data_root}/99/seko-ws-99-orphan",
    )
    backend.add_snapshot("orphan-snapshot", tags=("workspace:99", "user:99"))
    litellm = FakeLiteLLMClient()

    inventory = await retire_workspaces(
        db_session, settings, backend, litellm, execute=True
    )

    assert litellm.deleted == [
        {"keys": None, "key_aliases": [workspace.litellm_key_alias]}
    ]
    assert backend.removed == [workspace.container_name, "seko-ws-99-orphan"]
    assert backend.torn_down == [
        workspace.volume_path,
        f"{settings.workspace_data_root}/99/seko-ws-99-orphan",
    ]
    assert backend.forgotten == ["orphan-snapshot", "snap-0001"]
    assert inventory.backup_count == 1
    assert list(db_session.scalars(select(Workspace))) == []
    assert list(db_session.scalars(select(Backup))) == []
    assert list(db_session.scalars(select(SSHKey))) == []
    assert list(db_session.scalars(select(ApiKey))) == [normal_key]
    assert db_session.get(ServiceState, 1) is not None
    assert len(list(db_session.scalars(select(StatusEvent)))) == 1
    assert user.wrapped_dek is None
    assert user.ssh_public_key is None


async def test_workspace_retirement_partial_failure_is_explicit_and_rerunnable(
    db_session: Session, settings: Settings
) -> None:
    backend = FakeBackend(fail_on="stop_remove_container")
    _, workspace, normal_key = await _make_retirement_resources(
        db_session, settings, backend
    )
    litellm = FakeLiteLLMClient()

    with pytest.raises(RetirementError, match="container cleanup failed"):
        await retire_workspaces(db_session, settings, backend, litellm, execute=True)

    assert db_session.get(Workspace, workspace.id) is workspace
    assert list(db_session.scalars(select(Backup)))
    assert not backend.torn_down
    assert not backend.forgotten

    backend.fail_on = None
    await retire_workspaces(db_session, settings, backend, litellm, execute=True)
    await retire_workspaces(db_session, settings, backend, litellm, execute=True)
    assert list(db_session.scalars(select(Workspace))) == []
    assert list(db_session.scalars(select(ApiKey))) == [normal_key]


@pytest.mark.parametrize(
    "missing_message",
    [
        "LiteLLM returned 404: No keys found for the provided key aliases",
        "LiteLLM returned 404: key aliases are absent",
    ],
)
async def test_workspace_retirement_treats_missing_litellm_aliases_as_idempotent(
    db_session: Session, settings: Settings, missing_message: str
) -> None:
    backend = FakeBackend()
    _, workspace, normal_key = await _make_retirement_resources(
        db_session, settings, backend
    )
    litellm = FakeLiteLLMClient(delete_error=LiteLLMError(missing_message))

    await retire_workspaces(
        db_session,
        settings,
        backend,
        litellm,
        execute=True,
    )

    assert db_session.get(Workspace, workspace.id) is None
    assert list(db_session.scalars(select(ApiKey))) == [normal_key]


async def test_workspace_retirement_keeps_other_litellm_errors_fatal(
    db_session: Session, settings: Settings
) -> None:
    backend = FakeBackend()
    _, workspace, _ = await _make_retirement_resources(db_session, settings, backend)
    litellm = FakeLiteLLMClient(
        delete_error=LiteLLMError("LiteLLM request failed: connection refused")
    )

    with pytest.raises(RetirementError, match="alias revocation failed"):
        await retire_workspaces(
            db_session,
            settings,
            backend,
            litellm,
            execute=True,
        )

    assert db_session.get(Workspace, workspace.id) is workspace
    assert backend.removed == []


async def test_workspace_retirement_removes_label_discovered_orphan_container(
    db_session: Session, settings: Settings
) -> None:
    backend = FakeBackend()
    orphan_path = f"{settings.workspace_data_root}/42/seko-ws-42-orphan"
    backend.add_managed_container(
        "seko-ws-42-orphan", owner_id="42", home_path=orphan_path
    )

    await retire_workspaces(
        db_session,
        settings,
        backend,
        FakeLiteLLMClient(),
        execute=True,
    )

    assert backend.removed == ["seko-ws-42-orphan"]
    assert backend.torn_down == [orphan_path]
    assert backend.deleted_paths == [(settings.workspace_data_root, orphan_path)]


async def test_workspace_retirement_preserves_metadata_when_resources_race_cleanup(
    db_session: Session, settings: Settings
) -> None:
    race_path = f"{settings.workspace_data_root}/91/seko-ws-91-raced"

    class RacingBackend(FakeBackend):
        injected = False

        def delete_workspace_path(self, workspace_root: str, path: str) -> None:
            super().delete_workspace_path(workspace_root, path)
            if not self.injected:
                self.injected = True
                self.add_managed_container(
                    "seko-ws-91-raced",
                    owner_id="91",
                    home_path=race_path,
                )
                self.add_snapshot(
                    "raced-snapshot",
                    tags=("workspace:91", "user:91"),
                )

    backend = RacingBackend()
    _, workspace, normal_key = await _make_retirement_resources(
        db_session, settings, backend
    )

    with pytest.raises(RetirementError, match="external resource verification failed") as exc:
        await retire_workspaces(
            db_session,
            settings,
            backend,
            FakeLiteLLMClient(),
            execute=True,
        )

    assert "containers=seko-ws-91-raced" in str(exc.value)
    assert f"paths={race_path}" in str(exc.value)
    assert "snapshots=raced-snapshot" in str(exc.value)
    assert db_session.get(Workspace, workspace.id) is workspace
    assert list(db_session.scalars(select(Backup)))
    assert len(list(db_session.scalars(select(SSHKey)))) == 1
    assert any(
        key.workspace_id == workspace.id
        for key in db_session.scalars(select(ApiKey))
    )
    assert workspace.user.wrapped_dek is not None
    assert workspace.user.ssh_public_key is not None

    await retire_workspaces(
        db_session,
        settings,
        backend,
        FakeLiteLLMClient(
            delete_error=LiteLLMError("No keys found for the provided key aliases")
        ),
        execute=True,
    )
    assert list(db_session.scalars(select(Workspace))) == []
    assert list(db_session.scalars(select(ApiKey))) == [normal_key]


async def test_workspace_retirement_removes_filesystem_only_orphan(
    db_session: Session, settings: Settings
) -> None:
    backend = FakeBackend()
    orphan_path = f"{settings.workspace_data_root}/77/seko-ws-77-filesystem-only"
    backend.add_workspace_path(orphan_path)

    inventory = await retire_workspaces(
        db_session,
        settings,
        backend,
        FakeLiteLLMClient(),
    )
    assert inventory.volume_paths == (orphan_path,)

    await retire_workspaces(
        db_session,
        settings,
        backend,
        FakeLiteLLMClient(),
        execute=True,
    )
    assert backend.torn_down == [orphan_path]
    assert backend.deleted_paths == [(settings.workspace_data_root, orphan_path)]


async def test_workspace_retirement_rerun_rediscovers_path_after_container_removal(
    db_session: Session, settings: Settings
) -> None:
    backend = FakeBackend(fail_on="forget_snapshot")
    orphan_path = f"{settings.workspace_data_root}/88/seko-ws-88-rerun"
    backend.add_managed_container(
        "seko-ws-88-rerun",
        owner_id="88",
        home_path=orphan_path,
    )
    backend.add_snapshot("rerun-snapshot", tags=("workspace:88", "user:88"))

    with pytest.raises(RetirementError, match="snapshot forget/prune failed"):
        await retire_workspaces(
            db_session,
            settings,
            backend,
            FakeLiteLLMClient(),
            execute=True,
        )

    assert backend.list_managed_containers() == []
    assert backend.list_workspace_paths(settings.workspace_data_root) == [orphan_path]
    backend.fail_on = None

    inventory = await retire_workspaces(
        db_session,
        settings,
        backend,
        FakeLiteLLMClient(),
        execute=True,
    )
    assert inventory.managed_containers == ()
    assert inventory.volume_paths == (orphan_path,)
    assert backend.deleted_paths == [(settings.workspace_data_root, orphan_path)]


@pytest.mark.parametrize("unsafe_root", ["", "/", "/opt"])
async def test_workspace_retirement_refuses_unsafe_root(
    db_session: Session, settings: Settings, unsafe_root: str
) -> None:
    unsafe_settings = settings.model_copy(update={"workspace_data_root": unsafe_root})
    with pytest.raises(RetirementError, match="workspace_data_root"):
        await retire_workspaces(
            db_session,
            unsafe_settings,
            FakeBackend(),
            FakeLiteLLMClient(),
            execute=True,
        )


async def test_workspace_retirement_refuses_root_as_workspace_path(
    db_session: Session, settings: Settings
) -> None:
    user = User(subject="unsafe", username="unsafe")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        Workspace(
            user_id=user.id,
            name="unsafe",
            container_name="seko-ws-unsafe",
            volume_path=settings.workspace_data_root,
        )
    )
    db_session.flush()

    with pytest.raises(RetirementError, match="unsafe workspace path"):
        await retire_workspaces(
            db_session,
            settings,
            FakeBackend(),
            FakeLiteLLMClient(),
            execute=True,
        )


def test_retirement_confirmation_is_exact() -> None:
    assert management._retirement_execute(["retire-workspaces"]) is False
    assert management._retirement_execute(
        ["retire-workspaces", "--confirm", CONFIRMATION]
    ) is True
    with pytest.raises(RetirementError, match="confirmation mismatch"):
        management._retirement_execute(
            ["retire-workspaces", "--confirm", "yes-delete-workspaces"]
        )
