"""Tests for the workspace orchestration service (against the fake backend)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from seko_ai.config import Settings
from seko_ai.models import User, Workspace, WorkspaceStatus
from seko_ai.services.workspaces import (
    QuotaExceeded,
    WorkspaceError,
    WorkspaceService,
)
from tests.fakes import FakeBackend, FakeLiteLLMClient


def _user(session: Session, *, with_key: bool = True) -> User:
    u = User(subject="s", username="alice")
    session.add(u)
    session.flush()
    if with_key:
        from tests.conftest import add_ssh_key

        add_ssh_key(session, u)
    return u


def _svc(settings: Settings, backend: FakeBackend | None = None) -> WorkspaceService:
    return WorkspaceService(settings, backend or FakeBackend())


async def test_create_workspace_happy_path(db_session: Session, settings: Settings) -> None:
    user = _user(db_session)
    backend = FakeBackend()
    svc = _svc(settings, backend)
    ws = await svc.create_workspace(db_session, FakeLiteLLMClient(), user, name="dev")

    assert ws.status == WorkspaceStatus.RUNNING
    assert ws.ssh_port == settings.workspace_ssh_port_min
    assert ws.litellm_key_alias is not None
    # backend received provision + create with injected key + authorized keys
    assert backend.provisioned  # gocryptfs provisioned
    spec = backend.created[0]
    assert spec.authorized_keys.startswith("ssh-ed25519 AAAA")
    assert spec.llm_api_key.startswith("sk-fake-")
    assert spec.llm_base_url == settings.llm_public_url
    # user got a wrapped DEK
    assert user.wrapped_dek is not None


async def test_create_requires_ssh_key(db_session: Session, settings: Settings) -> None:
    user = _user(db_session, with_key=False)
    with pytest.raises(WorkspaceError, match="SSH public key"):
        await _svc(settings).create_workspace(
            db_session, FakeLiteLLMClient(), user, name="x"
        )


async def test_quota_enforced(db_session: Session, settings: Settings) -> None:
    user = _user(db_session)
    svc = _svc(settings)
    llm = FakeLiteLLMClient()
    for _ in range(settings.max_workspaces_per_user):
        await svc.create_workspace(db_session, llm, user, name="w")
    with pytest.raises(QuotaExceeded):
        await svc.create_workspace(db_session, llm, user, name="over")


async def test_ports_are_unique_across_workspaces(
    db_session: Session, settings: Settings
) -> None:
    user = _user(db_session)
    svc = _svc(settings)
    llm = FakeLiteLLMClient()
    w1 = await svc.create_workspace(db_session, llm, user, name="a")
    w2 = await svc.create_workspace(db_session, llm, user, name="b")
    assert w1.ssh_port != w2.ssh_port


async def test_backend_failure_marks_error(db_session: Session, settings: Settings) -> None:
    user = _user(db_session)
    backend = FakeBackend(fail_on="create")
    svc = _svc(settings, backend)
    with pytest.raises(WorkspaceError, match="Failed to start"):
        await svc.create_workspace(db_session, FakeLiteLLMClient(), user, name="x")
    ws = db_session.query(Workspace).one()
    assert ws.status == WorkspaceStatus.ERROR


async def test_stop_start_transitions(db_session: Session, settings: Settings) -> None:
    user = _user(db_session)
    backend = FakeBackend()
    svc = _svc(settings, backend)
    ws = await svc.create_workspace(db_session, FakeLiteLLMClient(), user, name="w")

    svc.stop_workspace(db_session, ws)
    assert ws.status == WorkspaceStatus.STOPPED
    assert ws.container_name in backend.stopped
    assert ws.volume_path in backend.torn_down

    svc.start_workspace(db_session, user, ws)
    assert ws.status == WorkspaceStatus.RUNNING
    assert ws.container_name in backend.started


async def test_terminate_revokes_key_and_removes(
    db_session: Session, settings: Settings
) -> None:
    user = _user(db_session)
    backend = FakeBackend()
    svc = _svc(settings, backend)
    llm = FakeLiteLLMClient()
    ws = await svc.create_workspace(db_session, llm, user, name="w")
    alias = ws.litellm_key_alias

    await svc.terminate_workspace(db_session, llm, ws)
    assert ws.status == WorkspaceStatus.TERMINATED
    assert ws.container_name in backend.removed
    assert any(d["key_aliases"] == [alias] for d in llm.deleted)
    # terminated workspaces no longer count toward quota / listing
    assert svc.list_workspaces(db_session, user.id) == []
    # the workspace-scoped key is marked inactive locally (not just deleted at LiteLLM)
    from seko_ai.models import ApiKey

    key = db_session.query(ApiKey).filter_by(key_alias=alias).one()
    assert key.active is False
    assert key.workspace_id == ws.id


async def test_workspace_keys_hidden_from_user_key_list(
    db_session: Session, settings: Settings
) -> None:
    from seko_ai.services import keys as ks

    user = _user(db_session)
    svc = _svc(settings)
    await svc.create_workspace(db_session, FakeLiteLLMClient(), user, name="w")
    # The injected workspace key must NOT appear in the user's own /keys list.
    assert ks.list_user_keys(db_session, user.id) == []


async def test_terminate_frees_the_port(db_session: Session, settings: Settings) -> None:
    user = _user(db_session)
    svc = _svc(settings)
    llm = FakeLiteLLMClient()
    w1 = await svc.create_workspace(db_session, llm, user, name="a")
    port = w1.ssh_port
    await svc.terminate_workspace(db_session, llm, w1)
    w2 = await svc.create_workspace(db_session, llm, user, name="b")
    assert w2.ssh_port == port  # reused after free


def test_allocate_port_exhausted(db_session: Session) -> None:
    settings = Settings(
        master_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        workspace_ssh_port_min=22000,
        workspace_ssh_port_max=22000,
    )
    user = _user(db_session)
    # Occupy the single port with a non-terminated workspace.
    db_session.add(
        Workspace(
            user_id=user.id,
            name="x",
            container_name="c",
            status=WorkspaceStatus.RUNNING,
            ssh_port=22000,
            volume_path="/v",
        )
    )
    db_session.flush()
    with pytest.raises(WorkspaceError, match="no free SSH ports"):
        _svc(settings).allocate_port(db_session)


def test_ssh_command_format(db_session: Session, settings: Settings) -> None:
    ws = Workspace(user_id=1, name="w", container_name="c", ssh_port=22005, volume_path="/v")
    cmd = _svc(settings).ssh_command(ws)
    assert cmd == f"ssh dev@{settings.workspace_ssh_host} -p 22005"


def test_harness_command_appends_binary(db_session: Session, settings: Settings) -> None:
    svc = _svc(settings)
    host = settings.workspace_ssh_host
    pi_ws = Workspace(
        user_id=1, name="w", container_name="c", ssh_port=22005, volume_path="/v", harness="pi"
    )
    omp_ws = Workspace(
        user_id=1,
        name="w2",
        container_name="c2",
        ssh_port=22006,
        volume_path="/v2",
        harness="oh-my-pi",
    )
    assert svc.harness_command(pi_ws) == f"ssh dev@{host} -p 22005 -t pi"
    assert svc.harness_command(omp_ws) == f"ssh dev@{host} -p 22006 -t omp"


async def test_create_workspace_records_harness(
    db_session: Session, settings: Settings
) -> None:
    user = _user(db_session)
    svc = _svc(settings)
    ws = await svc.create_workspace(
        db_session, FakeLiteLLMClient(), user, name="dev", harness="oh-my-pi"
    )
    assert ws.harness == "oh-my-pi"
    assert svc.harness_command(ws).endswith("-t omp")
