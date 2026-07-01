"""A configurable in-memory fake of LiteLLMClient for service/route tests."""

from __future__ import annotations

import uuid
from typing import Any

from seko_ai.services.litellm_client import LiteLLMError


class FakeLiteLLMClient:
    """Records calls and returns canned responses; can be told to fail."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
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
        if self.fail:
            raise LiteLLMError("simulated failure")
        self.deleted.append({"keys": keys, "key_aliases": key_aliases})
        return {"deleted": True}

    async def key_info(self, key: str) -> dict[str, Any]:
        return {"key": key, "spend": 0.0}

    async def user_daily_activity(self, user_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"user_id": user_id, "results": []}


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
        self._states: dict[str, str] = {}
        self._snap = 0

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            raise RuntimeError(f"backend failure in {name}")

    def provision_home(self, home_path: str, passphrase: str) -> None:
        self._maybe_fail("provision_home")
        self.provisioned.append((home_path, passphrase))

    def teardown_home(self, home_path: str) -> None:
        self.torn_down.append(home_path)

    def create(self, spec: Any) -> None:
        self._maybe_fail("create")
        self.created.append(spec)
        self._states[spec.name] = "running"

    def start(self, name: str) -> None:
        self.started.append(name)
        self._states[name] = "running"

    def stop(self, name: str) -> None:
        self.stopped.append(name)
        self._states[name] = "exited"

    def remove(self, name: str) -> None:
        self.removed.append(name)
        self._states.pop(name, None)

    def get(self, name: str) -> Any:
        from seko_ai.services.workspaces import ContainerInfo

        if name not in self._states:
            return None
        return ContainerInfo(name=name, status=self._states[name])

    def backup_volume(self, cipher_path: str, tags: list[str]) -> Any:
        from seko_ai.services.workspaces import BackupResult

        self._maybe_fail("backup_volume")
        self.backed_up.append((cipher_path, tags))
        self._snap += 1
        return BackupResult(snapshot_id=f"snap-{self._snap:04d}", size_bytes=1024 * self._snap)

    def restore_snapshot(self, snapshot_id: str, dest_cipher_path: str) -> None:
        self._maybe_fail("restore_snapshot")
        self.restored.append((snapshot_id, dest_cipher_path))
