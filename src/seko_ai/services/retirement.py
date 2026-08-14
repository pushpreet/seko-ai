"""Strict, rerunnable retirement of the deprecated hosted-workspace feature."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from seko_ai.config import Settings
from seko_ai.models import ApiKey, Backup, SSHKey, User, Workspace
from seko_ai.services.litellm_client import LiteLLMClient, LiteLLMError
from seko_ai.services.workspaces import ContainerBackend, ContainerInfo

CONFIRMATION = "RETIRE-ALL-WORKSPACES-PERMANENTLY"
_UNSAFE_ROOTS = {
    PurePosixPath("/"),
    PurePosixPath("/bin"),
    PurePosixPath("/boot"),
    PurePosixPath("/dev"),
    PurePosixPath("/etc"),
    PurePosixPath("/home"),
    PurePosixPath("/lib"),
    PurePosixPath("/lib64"),
    PurePosixPath("/media"),
    PurePosixPath("/mnt"),
    PurePosixPath("/opt"),
    PurePosixPath("/proc"),
    PurePosixPath("/root"),
    PurePosixPath("/run"),
    PurePosixPath("/sbin"),
    PurePosixPath("/srv"),
    PurePosixPath("/sys"),
    PurePosixPath("/usr"),
    PurePosixPath("/var"),
}
_WORKSPACE_NAME_RE = re.compile(r"seko-ws-[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class RetirementError(RuntimeError):
    """Raised when retirement cannot safely or completely finish."""


@dataclass(frozen=True)
class WorkspaceResource:
    """Non-secret DB metadata needed to clean one workspace."""

    workspace_id: int
    user_id: int
    container_name: str
    volume_path: str


@dataclass(frozen=True)
class RetirementInventory:
    """Complete non-secret inventory shown in dry-run and consumed by cleanup."""

    workspaces: tuple[WorkspaceResource, ...]
    workspace_api_key_count: int
    aliases: tuple[str, ...]
    backup_count: int
    db_snapshot_ids: tuple[str, ...]
    managed_containers: tuple[ContainerInfo, ...]
    snapshot_ids: tuple[str, ...]
    filesystem_paths: tuple[str, ...]
    volume_paths: tuple[str, ...]
    ssh_key_count: int
    wrapped_dek_user_count: int
    legacy_ssh_key_user_count: int

    def render(self, *, execute: bool) -> str:
        """Return a stable, secret-free operator report."""
        mode = "EXECUTE" if execute else "DRY RUN"
        lines = [
            f"workspace retirement mode: {mode}",
            (
                "database: "
                f"workspaces={len(self.workspaces)} "
                f"workspace_api_keys={self.workspace_api_key_count} "
                f"aliases={len(self.aliases)} backups={self.backup_count} "
                f"ssh_keys={self.ssh_key_count} "
                f"wrapped_dek_users={self.wrapped_dek_user_count} "
                f"legacy_ssh_key_users={self.legacy_ssh_key_user_count}"
            ),
            f"database workspaces ({len(self.workspaces)}): "
            f"{_render_items(_render_workspace(workspace) for workspace in self.workspaces)}",
            f"database snapshot references ({len(self.db_snapshot_ids)}): "
            f"{_render_items(self.db_snapshot_ids)}",
            f"managed containers ({len(self.managed_containers)}): "
            f"{_render_items(container.name for container in self.managed_containers)}",
            f"workspace snapshots ({len(self.snapshot_ids)}): "
            f"{_render_items(self.snapshot_ids)}",
            f"filesystem workspace paths ({len(self.filesystem_paths)}): "
            f"{_render_items(self.filesystem_paths)}",
            f"workspace paths ({len(self.volume_paths)}): "
            f"{_render_items(self.volume_paths)}",
        ]
        if not execute:
            lines.append(f"no changes made; execute with --confirm {CONFIRMATION}")
        return "\n".join(lines)


def _render_items(items: Iterable[object]) -> str:
    values = tuple(str(item) for item in items)
    return ", ".join(values) if values else "(none)"


def _render_workspace(workspace: WorkspaceResource) -> str:
    return (
        f"id={workspace.workspace_id} user={workspace.user_id} "
        f"container={workspace.container_name} path={workspace.volume_path}"
    )


def validate_workspace_root(root_value: str) -> PurePosixPath:
    """Return a normalized absolute root or reject a dangerously broad value."""
    if not root_value or not root_value.strip():
        raise RetirementError("workspace_data_root is empty")
    root = PurePosixPath(root_value)
    if (
        not root.is_absolute()
        or ".." in root.parts
        or str(root).startswith("//")
        or len(root.parts) < 3
    ):
        raise RetirementError(
            f"workspace_data_root must be a normalized absolute path: {root_value!r}"
        )
    if root in _UNSAFE_ROOTS:
        raise RetirementError(f"workspace_data_root is unsafe: {root}")
    return root


def validate_workspace_path(
    root: PurePosixPath,
    path_value: str,
    *,
    user_id: str,
    container_name: str,
) -> str:
    if (
        not user_id.isascii()
        or not user_id.isdecimal()
        or _WORKSPACE_NAME_RE.fullmatch(container_name) is None
        or PurePosixPath(container_name).name != container_name
        or container_name in {".", ".."}
    ):
        raise RetirementError(
            f"unsafe workspace identity: owner={user_id!r}, container={container_name!r}"
        )
    path = PurePosixPath(path_value)
    expected = root / user_id / container_name
    if path != expected or path == root or root not in path.parents:
        raise RetirementError(
            f"unsafe workspace path {path_value!r}; expected exact child {str(expected)!r}"
        )
    return str(path)


def _stage_failures(stage: str, failures: list[str]) -> None:
    if failures:
        detail = "; ".join(failures)
        raise RetirementError(f"{stage} failed: {detail}")


def _is_managed_snapshot(tags: tuple[str, ...]) -> bool:
    workspace_tags = [tag for tag in tags if tag.startswith("workspace:")]
    user_tags = [tag for tag in tags if tag.startswith("user:")]
    return (
        any(tag.removeprefix("workspace:").isdecimal() for tag in workspace_tags)
        and any(tag.removeprefix("user:").isdecimal() for tag in user_tags)
    )


def _is_missing_alias_error(exc: Exception) -> bool:
    if not isinstance(exc, LiteLLMError):
        return False
    message = str(exc).casefold()
    missing_phrases = (
        "no keys found",
        "key alias not found",
        "key aliases not found",
        "key_alias not found",
        "key_aliases not found",
        "key alias does not exist",
        "key aliases do not exist",
        "key alias is absent",
        "key aliases are absent",
    )
    return any(phrase in message for phrase in missing_phrases)


def build_inventory(
    session: Session, settings: Settings, backend: ContainerBackend
) -> RetirementInventory:
    """Discover all DB-known and externally managed workspace resources."""
    root = validate_workspace_root(settings.workspace_data_root)
    workspace_rows = list(session.execute(select(Workspace).order_by(Workspace.id)).scalars())
    workspaces = tuple(
        WorkspaceResource(
            workspace_id=workspace.id,
            user_id=workspace.user_id,
            container_name=workspace.container_name,
            volume_path=validate_workspace_path(
                root,
                workspace.volume_path,
                user_id=str(workspace.user_id),
                container_name=workspace.container_name,
            ),
        )
        for workspace in workspace_rows
    )

    workspace_keys = list(
        session.execute(select(ApiKey).where(ApiKey.workspace_id.is_not(None))).scalars()
    )
    aliases = {
        key.key_alias for key in workspace_keys if key.key_alias
    } | {
        workspace.litellm_key_alias
        for workspace in workspace_rows
        if workspace.litellm_key_alias
    }
    backups = list(session.execute(select(Backup).order_by(Backup.id)).scalars())
    db_snapshot_ids = {backup.snapshot_id for backup in backups if backup.snapshot_id}

    try:
        managed_containers = tuple(
            sorted(backend.list_managed_containers(), key=lambda container: container.name)
        )
    except Exception as exc:
        raise RetirementError(f"managed container discovery failed: {exc}") from exc
    try:
        discovered_paths = backend.list_workspace_paths(settings.workspace_data_root)
    except Exception as exc:
        raise RetirementError(f"workspace path discovery failed: {exc}") from exc
    volume_paths = {workspace.volume_path for workspace in workspaces}
    filesystem_paths: set[str] = set()
    workspace_by_container = {
        workspace.container_name: workspace for workspace in workspaces
    }
    for container in managed_containers:
        known = workspace_by_container.get(container.name)
        if known is not None and (
            container.owner_id not in {None, str(known.user_id)}
            or container.home_path not in {None, known.volume_path}
        ):
            raise RetirementError(
                f"managed container {container.name!r} conflicts with database metadata"
            )
        if container.home_path is None:
            continue
        if not container.owner_id:
            raise RetirementError(
                f"managed container {container.name!r} has a workspace mount but no owner label"
            )
        volume_paths.add(
            validate_workspace_path(
                root,
                container.home_path,
                user_id=container.owner_id,
                container_name=container.name,
            )
        )
    for path_value in discovered_paths:
        path = PurePosixPath(path_value)
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise RetirementError(
                f"discovered workspace path is outside its root: {path_value!r}"
            ) from exc
        if len(relative.parts) != 2:
            raise RetirementError(
                f"discovered workspace path is not an exact child: {path_value!r}"
            )
        discovered_path = validate_workspace_path(
            root,
            path_value,
            user_id=relative.parts[0],
            container_name=relative.parts[1],
        )
        filesystem_paths.add(discovered_path)
        volume_paths.add(discovered_path)

    try:
        snapshots = backend.list_snapshots()
    except Exception as exc:
        raise RetirementError(f"workspace snapshot discovery failed: {exc}") from exc
    snapshot_ids = {
        snapshot.snapshot_id
        for snapshot in snapshots
        if snapshot.snapshot_id in db_snapshot_ids
        or _is_managed_snapshot(snapshot.tags)
    }

    ssh_key_count = len(list(session.execute(select(SSHKey.id)).scalars()))
    wrapped_dek_user_count = len(
        list(session.execute(select(User.id).where(User.wrapped_dek.is_not(None))).scalars())
    )
    legacy_ssh_key_user_count = len(
        list(session.execute(select(User.id).where(User.ssh_public_key.is_not(None))).scalars())
    )
    return RetirementInventory(
        workspaces=workspaces,
        workspace_api_key_count=len(workspace_keys),
        aliases=tuple(sorted(aliases)),
        backup_count=len(backups),
        db_snapshot_ids=tuple(sorted(db_snapshot_ids)),
        managed_containers=managed_containers,
        snapshot_ids=tuple(sorted(snapshot_ids)),
        filesystem_paths=tuple(sorted(filesystem_paths)),
        volume_paths=tuple(sorted(volume_paths)),
        ssh_key_count=ssh_key_count,
        wrapped_dek_user_count=wrapped_dek_user_count,
        legacy_ssh_key_user_count=legacy_ssh_key_user_count,
    )


async def retire_workspaces(
    session: Session,
    settings: Settings,
    backend: ContainerBackend,
    litellm: LiteLLMClient,
    *,
    execute: bool = False,
) -> RetirementInventory:
    """Inventory or permanently retire all workspace resources in strict stage order."""
    inventory = build_inventory(session, settings, backend)
    if not execute:
        return inventory

    if inventory.aliases:
        try:
            await litellm.delete_keys(key_aliases=list(inventory.aliases))
        except Exception as exc:
            if not _is_missing_alias_error(exc):
                raise RetirementError(f"LiteLLM alias revocation failed: {exc}") from exc

    container_names = {
        workspace.container_name for workspace in inventory.workspaces
    } | {
        container.name for container in inventory.managed_containers
    }
    failures: list[str] = []
    for name in sorted(container_names):
        try:
            backend.stop_remove_container(name)
        except Exception as exc:
            failures.append(f"{name}: {exc}")
    _stage_failures("container cleanup", failures)

    failures = []
    for path in inventory.volume_paths:
        try:
            backend.teardown_home(path)
        except Exception as exc:
            failures.append(f"{path}: {exc}")
    _stage_failures("cleartext unmount", failures)

    failures = []
    for snapshot_id in inventory.snapshot_ids:
        try:
            backend.forget_snapshot(snapshot_id)
        except Exception as exc:
            failures.append(f"{snapshot_id}: {exc}")
    _stage_failures("snapshot forget/prune", failures)

    failures = []
    for path in inventory.volume_paths:
        try:
            backend.delete_workspace_path(settings.workspace_data_root, path)
        except Exception as exc:
            failures.append(f"{path}: {exc}")
    _stage_failures("workspace path deletion", failures)

    remaining = build_inventory(session, settings, backend)
    remaining_external: list[str] = []
    if remaining.managed_containers:
        remaining_external.append(
            "containers="
            + ",".join(container.name for container in remaining.managed_containers)
        )
    if remaining.filesystem_paths:
        remaining_external.append("paths=" + ",".join(remaining.filesystem_paths))
    if remaining.snapshot_ids:
        remaining_external.append("snapshots=" + ",".join(remaining.snapshot_ids))
    if remaining_external:
        raise RetirementError(
            "external resource verification failed: " + "; ".join(remaining_external)
        )

    session.execute(delete(ApiKey).where(ApiKey.workspace_id.is_not(None)))
    session.execute(delete(Backup))
    session.execute(delete(Workspace))
    session.execute(delete(SSHKey))
    session.execute(update(User).values(wrapped_dek=None, ssh_public_key=None))
    session.flush()
    return inventory
