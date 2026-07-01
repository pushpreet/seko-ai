"""Workspace orchestration: backend abstraction, specs, and lifecycle service.

The service contains all the testable business logic (quotas, port allocation, spec
building, encryption wiring, DB state transitions). Actual container/host operations sit
behind :class:`ContainerBackend`, implemented for real by the Docker-over-SSH backend and
faked in tests.
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from seko_ai.config import Settings
from seko_ai.models import User, Workspace, WorkspaceStatus
from seko_ai.services import crypto
from seko_ai.services import keys as keys_service
from seko_ai.services.litellm_client import LiteLLMClient


class WorkspaceError(RuntimeError):
    """Raised for workspace lifecycle errors (quota, missing key, backend failure)."""


class QuotaExceeded(WorkspaceError):
    """Raised when a user is at their concurrent-workspace limit."""


@dataclass(frozen=True)
class WorkspaceSpec:
    """Everything the backend needs to create + run a workspace container."""

    name: str
    image: str
    ssh_port: int
    authorized_keys: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    home_path: str
    cpus: float
    mem: str
    pids_limit: int
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ContainerInfo:
    """Backend-reported state of a workspace container."""

    name: str
    status: str  # e.g. running, exited, created
    ssh_port: int | None = None


@runtime_checkable
class ContainerBackend(Protocol):
    """Abstraction over the host's container engine + encrypted-home provisioning."""

    def provision_home(self, home_path: str, passphrase: str) -> None:
        """Init (if needed) and mount the gocryptfs cleartext home at ``home_path``."""

    def teardown_home(self, home_path: str) -> None:
        """Unmount the gocryptfs cleartext home (ciphertext remains for backup)."""

    def create(self, spec: WorkspaceSpec) -> None:
        """Create and start a container from the spec."""

    def start(self, name: str) -> None: ...

    def stop(self, name: str) -> None: ...

    def remove(self, name: str) -> None: ...

    def get(self, name: str) -> ContainerInfo | None: ...


OWNER_LABEL = "ai.seko.owner"
MANAGED_LABEL = "ai.seko.managed"


class WorkspaceService:
    """Lifecycle operations for hosted workspaces."""

    def __init__(self, settings: Settings, backend: ContainerBackend) -> None:
        self.settings = settings
        self.backend = backend

    # --- Helpers ---

    def _active_workspaces(self, session: Session, user_id: int) -> list[Workspace]:
        stmt = select(Workspace).where(
            Workspace.user_id == user_id,
            Workspace.status != WorkspaceStatus.TERMINATED,
        )
        return list(session.execute(stmt).scalars().all())

    def _used_ports(self, session: Session) -> set[int]:
        stmt = select(Workspace.ssh_port).where(
            Workspace.status != WorkspaceStatus.TERMINATED,
            Workspace.ssh_port.is_not(None),
        )
        return {p for (p,) in session.execute(stmt).all() if p is not None}

    def allocate_port(self, session: Session) -> int:
        """Return a free SSH host port in the configured range."""
        used = self._used_ports(session)
        for port in range(
            self.settings.workspace_ssh_port_min, self.settings.workspace_ssh_port_max + 1
        ):
            if port not in used:
                return port
        raise WorkspaceError("no free SSH ports available in the configured range")

    def ensure_user_dek(self, session: Session, user: User) -> bytes:
        """Return the user's DEK, generating and wrapping a fresh one on first use."""
        master = crypto.load_master_key(self.settings.master_key)
        if user.wrapped_dek:
            return crypto.unwrap_dek(master, user.wrapped_dek)
        dek = crypto.generate_dek()
        user.wrapped_dek = crypto.wrap_dek(master, dek)
        session.flush()
        return dek

    def get_workspace(self, session: Session, user_id: int, workspace_id: int) -> Workspace | None:
        stmt = select(Workspace).where(
            Workspace.id == workspace_id, Workspace.user_id == user_id
        )
        return session.execute(stmt).scalar_one_or_none()

    def list_workspaces(self, session: Session, user_id: int) -> list[Workspace]:
        stmt = (
            select(Workspace)
            .where(Workspace.user_id == user_id, Workspace.status != WorkspaceStatus.TERMINATED)
            .order_by(Workspace.created_at.desc())
        )
        return list(session.execute(stmt).scalars().all())

    # --- Lifecycle ---

    async def create_workspace(
        self,
        session: Session,
        litellm: LiteLLMClient,
        user: User,
        *,
        name: str,
        harness: str = "pi",
    ) -> Workspace:
        """Provision an encrypted home, mint a workspace key, and start the container."""
        if not user.ssh_public_key:
            raise WorkspaceError("Add an SSH public key to your profile before launching.")
        active = self._active_workspaces(session, user.id)
        if len(active) >= self.settings.max_workspaces_per_user:
            raise QuotaExceeded(
                f"You already have {len(active)} workspaces "
                f"(limit {self.settings.max_workspaces_per_user})."
            )

        container_name = f"seko-ws-{user.id}-{uuid.uuid4().hex[:10]}"
        volume_path = f"{self.settings.workspace_data_root.rstrip('/')}/{user.id}/{container_name}"
        ssh_port = self.allocate_port(session)

        api_key, plaintext = await keys_service.create_key_for_user(
            session, litellm, user, self.settings
        )

        workspace = Workspace(
            user_id=user.id,
            name=name,
            container_name=container_name,
            harness=harness,
            status=WorkspaceStatus.PROVISIONING,
            ssh_port=ssh_port,
            litellm_key_alias=api_key.key_alias,
            volume_path=volume_path,
        )
        session.add(workspace)
        session.flush()

        dek = self.ensure_user_dek(session, user)
        try:
            self.backend.provision_home(volume_path, crypto.dek_passphrase(dek))
            self.backend.create(
                WorkspaceSpec(
                    name=container_name,
                    image=self.settings.workspace_image,
                    ssh_port=ssh_port,
                    authorized_keys=user.ssh_public_key,
                    llm_base_url=self.settings.llm_public_url,
                    llm_api_key=plaintext,
                    llm_model=self.settings.llm_model,
                    home_path=f"{volume_path}/cleartext",
                    cpus=self.settings.workspace_cpus,
                    mem=self.settings.workspace_mem,
                    pids_limit=self.settings.workspace_pids_limit,
                    labels={OWNER_LABEL: str(user.id), MANAGED_LABEL: "true"},
                )
            )
        except Exception as exc:  # backend failure: mark error, surface
            workspace.status = WorkspaceStatus.ERROR
            session.flush()
            raise WorkspaceError(f"Failed to start workspace: {exc}") from exc

        workspace.status = WorkspaceStatus.RUNNING
        session.flush()
        return workspace

    def stop_workspace(self, session: Session, workspace: Workspace) -> None:
        self.backend.stop(workspace.container_name)
        self.backend.teardown_home(workspace.volume_path)
        workspace.status = WorkspaceStatus.STOPPED
        session.flush()

    def start_workspace(self, session: Session, user: User, workspace: Workspace) -> None:
        dek = self.ensure_user_dek(session, user)
        self.backend.provision_home(workspace.volume_path, crypto.dek_passphrase(dek))
        self.backend.start(workspace.container_name)
        workspace.status = WorkspaceStatus.RUNNING
        session.flush()

    async def terminate_workspace(
        self, session: Session, litellm: LiteLLMClient, workspace: Workspace
    ) -> None:
        """Remove the container + revoke its key. Ciphertext volume is left for backup."""
        workspace.status = WorkspaceStatus.TERMINATING
        session.flush()
        self.backend.stop(workspace.container_name)
        self.backend.teardown_home(workspace.volume_path)
        self.backend.remove(workspace.container_name)
        if workspace.litellm_key_alias:
            with contextlib.suppress(Exception):  # best-effort revoke; don't block teardown
                await litellm.delete_keys(key_aliases=[workspace.litellm_key_alias])
        workspace.status = WorkspaceStatus.TERMINATED
        session.flush()

    def ssh_command(self, workspace: Workspace) -> str:
        """Return the SSH command a user runs to reach the workspace (Tailscale)."""
        return f"ssh dev@{self.settings.workspace_ssh_host} -p {workspace.ssh_port}"
