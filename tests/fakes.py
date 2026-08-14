"""A configurable in-memory fake of LiteLLMClient for service/route tests."""

from __future__ import annotations

import uuid
from typing import Any

from seko_ai.services.litellm_client import LiteLLMError


class FakeLiteLLMClient:
    """Records calls and returns canned responses; can be told to fail."""

    def __init__(
        self,
        *,
        fail: bool = False,
        delete_error: Exception | None = None,
    ) -> None:
        self.fail = fail
        self.delete_error = delete_error
        self.generated: list[dict[str, Any]] = []
        self.deleted: list[dict[str, Any]] = []
        self._counter = 0

    async def __aenter__(self) -> FakeLiteLLMClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def generate_key(
        self,
        *,
        user_id: str,
        key_alias: str,
        models: list[str] | None = None,
        max_budget: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.fail:
            raise LiteLLMError("simulated failure")
        self._counter += 1
        self.generated.append({"user_id": user_id, "key_alias": key_alias, "models": models})
        return {"key": f"sk-fake-{self._counter:04d}", "token": f"tok-{uuid.uuid4().hex}"}

    async def delete_keys(
        self, *, keys: list[str] | None = None, key_aliases: list[str] | None = None
    ) -> dict[str, Any]:
        if self.delete_error is not None:
            raise self.delete_error
        if self.fail:
            raise LiteLLMError("simulated failure")
        self.deleted.append({"keys": keys, "key_aliases": key_aliases})
        return {"deleted": True}

    async def key_info(self, key: str) -> dict[str, Any]:
        return {"key": key, "spend": 0.0}

    async def daily_activity(
        self, *, start_date: str, end_date: str, page_size: int = 1000
    ) -> list[dict[str, Any]]:
        return []


class FakeBackend:
    """In-memory ContainerBackend for workspace tests."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on  # method name that should raise
        self.provisioned: list[tuple[str, str]] = []
        self.torn_down: list[str] = []
        self.created: list[Any] = []
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.removed: list[str] = []
        self.backed_up: list[tuple[str, list[str]]] = []
        self.restored: list[tuple[str, str]] = []
        self.forgotten: list[str] = []
        self.deleted_paths: list[tuple[str, str]] = []
        self._states: dict[str, str] = {}
        self._containers: dict[str, Any] = {}
        self._snapshots: dict[str, Any] = {}
        self._workspace_paths: set[str] = set()
        self._snap = 0

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            raise RuntimeError(f"backend failure in {name}")

    def provision_home(self, home_path: str, passphrase: str) -> None:
        self._maybe_fail("provision_home")
        self.provisioned.append((home_path, passphrase))

    def teardown_home(self, home_path: str) -> None:
        self._maybe_fail("teardown_home")
        self.torn_down.append(home_path)

    def create(self, spec: Any) -> None:
        self._maybe_fail("create")
        self.created.append(spec)
        self._states[spec.name] = "running"
        from seko_ai.services.workspaces import ContainerInfo

        self._containers[spec.name] = ContainerInfo(
            name=spec.name,
            status="running",
            owner_id=spec.labels.get("ai.seko.owner"),
            home_path=spec.home_path.removesuffix("/cleartext"),
        )
        self._workspace_paths.add(spec.home_path.removesuffix("/cleartext"))

    def start(self, name: str) -> None:
        self.started.append(name)
        self._states[name] = "running"

    def stop(self, name: str) -> None:
        self.stopped.append(name)
        self._states[name] = "exited"

    def remove(self, name: str) -> None:
        self.removed.append(name)
        self._states.pop(name, None)
        self._containers.pop(name, None)

    def get(self, name: str) -> Any:
        from seko_ai.services.workspaces import ContainerInfo

        if name not in self._states:
            return None
        return ContainerInfo(name=name, status=self._states[name])

    def add_managed_container(
        self, name: str, *, owner_id: str, home_path: str, status: str = "running"
    ) -> None:
        from seko_ai.services.workspaces import ContainerInfo

        self._states[name] = status
        self._containers[name] = ContainerInfo(
            name=name,
            status=status,
            owner_id=owner_id,
            home_path=home_path,
        )
        self._workspace_paths.add(home_path)

    def list_managed_containers(self) -> list[Any]:
        self._maybe_fail("list_managed_containers")
        return list(self._containers.values())

    def add_workspace_path(self, path: str) -> None:
        self._workspace_paths.add(path)

    def list_workspace_paths(self, workspace_root: str) -> list[str]:
        self._maybe_fail("list_workspace_paths")
        prefix = f"{workspace_root.rstrip('/')}/"
        return sorted(path for path in self._workspace_paths if path.startswith(prefix))

    def stop_remove_container(self, name: str) -> None:
        self._maybe_fail("stop_remove_container")
        if name not in self._states:
            return
        self.stopped.append(name)
        self.removed.append(name)
        self._states.pop(name, None)
        self._containers.pop(name, None)

    def backup_volume(self, cipher_path: str, tags: list[str]) -> Any:
        from seko_ai.services.workspaces import BackupResult, SnapshotInfo

        self._maybe_fail("backup_volume")
        self.backed_up.append((cipher_path, tags))
        self._snap += 1
        snapshot_id = f"snap-{self._snap:04d}"
        self._snapshots[snapshot_id] = SnapshotInfo(snapshot_id, tuple(tags))
        return BackupResult(snapshot_id=snapshot_id, size_bytes=1024 * self._snap)

    def restore_snapshot(self, snapshot_id: str, dest_cipher_path: str) -> None:
        self._maybe_fail("restore_snapshot")
        self.restored.append((snapshot_id, dest_cipher_path))

    def forget_snapshot(self, snapshot_id: str) -> None:
        self._maybe_fail("forget_snapshot")
        if snapshot_id not in self._snapshots:
            return
        self.forgotten.append(snapshot_id)
        self._snapshots.pop(snapshot_id, None)

    def add_snapshot(self, snapshot_id: str, *, tags: tuple[str, ...] = ()) -> None:
        from seko_ai.services.workspaces import SnapshotInfo

        self._snapshots[snapshot_id] = SnapshotInfo(snapshot_id, tags)

    def list_snapshots(self) -> list[Any]:
        self._maybe_fail("list_snapshots")
        return list(self._snapshots.values())

    def delete_workspace_path(self, workspace_root: str, path: str) -> None:
        self._maybe_fail("delete_workspace_path")
        self.deleted_paths.append((workspace_root, path))
        self._workspace_paths.discard(path)
