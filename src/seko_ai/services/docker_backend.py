"""Docker-over-SSH backend: runs workspace containers on the LLM host (epyc).

seko-ai runs on core-infra and reaches epyc's Docker engine over SSH (``DOCKER_HOST=ssh://
user@host``) — no exposed TCP socket. Encrypted-home provisioning (gocryptfs) is a *host*
operation, so it is run over the same SSH connection.

NOTE: the live Docker/SSH/gocryptfs paths require epyc + a Docker daemon to validate; only
the pure helpers here (``build_run_kwargs``, ``parse_ssh_target``) are unit-tested in CI.
"""

from __future__ import annotations

import json
import posixpath
import shlex
import subprocess
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from seko_ai.logging_config import get_logger
from seko_ai.services.workspaces import (
    MANAGED_LABEL,
    OWNER_LABEL,
    BackupResult,
    ContainerInfo,
    SnapshotInfo,
    WorkspaceSpec,
)

log = get_logger("seko_ai.docker_backend")

_DISCOVER_PATHS_SCRIPT = r"""
import json
import os
import re
import stat
import sys

root = sys.argv[1]
name_re = re.compile(r"seko-ws-[A-Za-z0-9][A-Za-z0-9._-]*\Z")
records = []
try:
    root_mode = os.lstat(root).st_mode
except FileNotFoundError:
    print("[]")
    raise SystemExit
if stat.S_ISLNK(root_mode):
    print(json.dumps([{"root_symlink": True, "path": root}]))
    raise SystemExit
users = os.scandir(root)
with users:
    for user in users:
        if not user.name.isascii() or not user.name.isdecimal():
            continue
        user_mode = os.lstat(user.path).st_mode
        if stat.S_ISLNK(user_mode):
            records.append({"user_id": user.name, "user_symlink": True})
            continue
        if not stat.S_ISDIR(user_mode):
            continue
        with os.scandir(user.path) as workspaces:
            for workspace in workspaces:
                if name_re.fullmatch(workspace.name) is None:
                    continue
                workspace_mode = os.lstat(workspace.path).st_mode
                records.append(
                    {
                        "user_id": user.name,
                        "container_name": workspace.name,
                        "path": workspace.path,
                        "workspace_symlink": stat.S_ISLNK(workspace_mode),
                        "workspace_directory": stat.S_ISDIR(workspace_mode),
                    }
                )
print(json.dumps(records))
"""
_PATH_STATE_SCRIPT = r"""
import json
import os
import stat
import sys

root, user_id, container_name = sys.argv[1:]
user_path = os.path.join(root, user_id)
target_path = os.path.join(user_path, container_name)

def is_symlink(path):
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False

print(
    json.dumps(
        {
            "canonical_root": os.path.realpath(root),
            "canonical_target": os.path.realpath(target_path),
            "root_symlink": is_symlink(root),
            "user_symlink": is_symlink(user_path),
            "target_symlink": is_symlink(target_path),
        }
    )
)
"""
_DELETE_PATH_SCRIPT = r"""
import errno
import os
import stat
import sys

root, user_id, container_name = sys.argv[1:]
directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

def clear_directory(directory_fd):
    with os.scandir(directory_fd) as entries:
        for entry in entries:
            mode = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode):
                child_fd = os.open(entry.name, directory_flags, dir_fd=directory_fd)
                try:
                    clear_directory(child_fd)
                finally:
                    os.close(child_fd)
                os.rmdir(entry.name, dir_fd=directory_fd)
            else:
                os.unlink(entry.name, dir_fd=directory_fd)

try:
    root_fd = os.open(root, directory_flags)
except FileNotFoundError:
    raise SystemExit
try:
    try:
        user_fd = os.open(user_id, directory_flags, dir_fd=root_fd)
    except FileNotFoundError:
        raise SystemExit
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise RuntimeError(f"refusing symlinked workspace user directory: {user_id}")
        raise
    try:
        try:
            target_fd = os.open(container_name, directory_flags, dir_fd=user_fd)
        except FileNotFoundError:
            raise SystemExit
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise RuntimeError(f"refusing symlinked workspace target: {container_name}")
            raise
        try:
            clear_directory(target_fd)
        finally:
            os.close(target_fd)
        os.rmdir(container_name, dir_fd=user_fd)
    finally:
        os.close(user_fd)
finally:
    os.close(root_fd)
"""


def parse_ssh_target(docker_host: str) -> str:
    """Turn ``ssh://user@host[:port]`` into an ``ssh`` CLI target (``user@host``)."""
    parsed = urlparse(docker_host)
    if parsed.scheme != "ssh" or not parsed.hostname:
        raise ValueError(f"expected ssh:// docker host, got {docker_host!r}")
    user = f"{parsed.username}@" if parsed.username else ""
    return f"{user}{parsed.hostname}"


def parse_workspace_path_discovery(raw: str, workspace_root: str) -> list[str]:
    """Validate host discovery output without following user/workspace symlinks."""
    from seko_ai.services.retirement import RetirementError, validate_workspace_path

    root = PurePosixPath(workspace_root)
    try:
        records = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RetirementError("workspace path discovery returned invalid JSON") from exc
    if not isinstance(records, list):
        raise RetirementError("workspace path discovery returned a non-list result")

    paths: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            raise RetirementError("workspace path discovery returned an invalid record")
        if record.get("root_symlink") is True:
            raise RetirementError(f"symlinked workspace root: {workspace_root!r}")
        user_id = record.get("user_id")
        if not isinstance(user_id, str):
            raise RetirementError("workspace path discovery omitted a user id")
        if record.get("user_symlink") is True:
            raise RetirementError(f"symlinked workspace user directory: {user_id!r}")
        container_name = record.get("container_name")
        path = record.get("path")
        if not isinstance(container_name, str) or not isinstance(path, str):
            raise RetirementError("workspace path discovery omitted workspace identity")
        if record.get("workspace_symlink") is True:
            raise RetirementError(f"symlinked workspace directory: {path!r}")
        if record.get("workspace_directory") is not True:
            continue
        paths.append(
            validate_workspace_path(
                root,
                path,
                user_id=user_id,
                container_name=container_name,
            )
        )
    return sorted(paths)


def validate_canonical_delete_target(
    *,
    canonical_root: str,
    canonical_target: str,
    user_id: str,
    container_name: str,
    root_symlink: bool,
    user_symlink: bool,
    target_symlink: bool,
) -> str:
    """Require the physical target to be exactly root/user/container."""
    from seko_ai.services.retirement import validate_workspace_root

    if root_symlink:
        raise ValueError(f"refusing symlinked workspace root: {canonical_root!r}")
    if user_symlink:
        raise ValueError(f"refusing symlinked workspace user directory: {user_id!r}")
    if target_symlink:
        raise ValueError(f"refusing symlinked workspace target: {canonical_target!r}")
    root = validate_workspace_root(canonical_root)
    target = PurePosixPath(canonical_target)
    expected = root / user_id / container_name
    if not root.is_absolute() or target != expected:
        raise ValueError(
            f"canonical workspace target mismatch: expected {expected!s}, got {target!s}"
        )
    return str(target)


def build_run_kwargs(spec: WorkspaceSpec) -> dict[str, Any]:
    """Build the docker-py ``containers.run`` kwargs for a workspace spec."""
    return {
        "image": spec.image,
        "name": spec.name,
        "detach": True,
        "hostname": spec.name,
        "environment": {
            "SEKO_AUTHORIZED_KEYS": spec.authorized_keys,
            "LLM_BASE_URL": spec.llm_base_url,
            "LLM_API_KEY": spec.llm_api_key,
            "LLM_MODEL": spec.llm_model,
        },
        # Publish the container's sshd (22) on the allocated host port. Bind to the host's
        # Tailscale-reachable interface only (the orchestrator keeps SSH off the public net).
        "ports": {"22/tcp": spec.ssh_port},
        "volumes": {spec.home_path: {"bind": "/home/dev", "mode": "rw"}},
        "nano_cpus": int(spec.cpus * 1_000_000_000),
        "mem_limit": spec.mem,
        "pids_limit": spec.pids_limit,
        # no-new-privileges blocks privilege escalation; we keep Docker's DEFAULT capability
        # set (not cap_drop=ALL) because the embedded sshd needs CAP_SETUID/SETGID to drop to
        # the dev user on login, and CAP_CHOWN/DAC_OVERRIDE to set up the gocryptfs home
        # (which the kernel enforces via default_permissions).
        "security_opt": ["no-new-privileges:true"],
        "labels": spec.labels,
        "restart_policy": {"Name": "unless-stopped"},
    }


class DockerBackend:
    """Concrete :class:`~seko_ai.services.workspaces.ContainerBackend` for epyc."""

    def __init__(
        self,
        docker_host: str,
        *,
        ssh_target: str | None = None,
        restic_repository: str = "",
        restic_password: str = "",
    ) -> None:
        self._docker_host = docker_host
        self._ssh_target = ssh_target or parse_ssh_target(docker_host)
        self._restic_repository = restic_repository
        self._restic_password = restic_password
        self._client: Any = None

    def _client_lazy(self) -> Any:  # pragma: no cover - requires a Docker daemon
        if self._client is None:
            import docker  # imported lazily so the app starts without a daemon

            self._client = docker.DockerClient(
                base_url=self._docker_host, use_ssh_client=True
            )
        return self._client

    def _host_exec(self, argv: list[str], *, stdin: str | None = None) -> str:
        """Run a command on epyc over SSH; returns stdout."""  # pragma: no cover - needs SSH
        cmd = ["ssh", self._ssh_target, "--", shlex.join(argv)]
        try:
            result = subprocess.run(
                cmd,
                input=stdin,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()[:500]
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"remote command failed with exit code {exc.returncode}{suffix}"
            ) from None
        return result.stdout

    # --- Encrypted home (gocryptfs) ---

    def provision_home(self, home_path: str, passphrase: str) -> None:  # pragma: no cover
        cipher = f"{home_path}/cipher"
        clear = f"{home_path}/cleartext"
        self._host_exec(["mkdir", "-p", cipher, clear])
        # Initialise the gocryptfs volume on first use (idempotent: skip if already init'd).
        init_if_needed = (
            f"test -f {shlex.quote(cipher)}/gocryptfs.conf || "
            f"gocryptfs -q -init -passfile /dev/stdin {shlex.quote(cipher)}"
        )
        self._host_exec(["sh", "-c", init_if_needed], stdin=passphrase)
        # Mount cleartext (idempotent: skip if already a mountpoint). --allow_other lets the
        # workspace container access the seko-mounted FUSE; the container's `dev` user shares
        # seko's uid (1001) so it owns its home and can write. Requires user_allow_other in
        # /etc/fuse.conf + an AppArmor allow-rule for this path (set by the llm_host role).
        mount_if_needed = (
            f"mountpoint -q {shlex.quote(clear)} || "
            f"gocryptfs -q -allow_other -passfile /dev/stdin "
            f"{shlex.quote(cipher)} {shlex.quote(clear)}"
        )
        self._host_exec(["sh", "-c", mount_if_needed], stdin=passphrase)

    def teardown_home(self, home_path: str) -> None:  # pragma: no cover
        clear = f"{home_path}/cleartext"
        command = (
            f"mountpoint -q {shlex.quote(clear)}; status=$?; "
            f"if test \"$status\" -eq 0; then fusermount -u {shlex.quote(clear)}; "
            f"elif test \"$status\" -ne 1; then exit \"$status\"; fi"
        )
        self._host_exec(["sh", "-c", command])

    # --- Container lifecycle ---

    def create(self, spec: WorkspaceSpec) -> None:  # pragma: no cover
        client = self._client_lazy()
        # Force a pull so new workspaces pick up a freshly pushed image tag (docker's
        # containers.run only pulls when the image is ABSENT, so a cached :latest would
        # otherwise never update). Fall back to the cached image if the registry is
        # unreachable or the pull fails, so workspace creation stays resilient.
        try:
            client.images.pull(spec.image)
        except Exception as exc:  # noqa: BLE001 - best-effort refresh; cached image is fine
            log.warning("workspace_image_pull_failed", image=spec.image, error=str(exc))
        client.containers.run(**build_run_kwargs(spec))

    def start(self, name: str) -> None:  # pragma: no cover
        self._client_lazy().containers.get(name).start()

    def stop(self, name: str) -> None:  # pragma: no cover
        try:
            self._client_lazy().containers.get(name).stop()
        except Exception as exc:  # already gone
            log.info("stop_noop", name=name, error=str(exc))

    def remove(self, name: str) -> None:  # pragma: no cover
        try:
            self._client_lazy().containers.get(name).remove(force=True)
        except Exception as exc:
            log.info("remove_noop", name=name, error=str(exc))

    def get(self, name: str) -> ContainerInfo | None:  # pragma: no cover
        try:
            container = self._client_lazy().containers.get(name)
        except Exception:
            return None
        return ContainerInfo(name=name, status=container.status)

    def list_managed_containers(self) -> list[ContainerInfo]:  # pragma: no cover
        containers = self._client_lazy().containers.list(
            all=True, filters={"label": f"{MANAGED_LABEL}=true"}
        )
        result: list[ContainerInfo] = []
        for container in containers:
            labels = container.attrs.get("Config", {}).get("Labels", {}) or {}
            home_path: str | None = None
            for mount in container.attrs.get("Mounts", []):
                if mount.get("Destination") == "/home/dev":
                    source = mount.get("Source")
                    if isinstance(source, str) and source.endswith("/cleartext"):
                        home_path = posixpath.dirname(source)
                        break
            result.append(
                ContainerInfo(
                    name=container.name,
                    status=container.status,
                    owner_id=labels.get(OWNER_LABEL),
                    home_path=home_path,
                )
            )
        return result

    def list_workspace_paths(self, workspace_root: str) -> list[str]:  # pragma: no cover
        from seko_ai.services.retirement import validate_workspace_root

        root = str(validate_workspace_root(workspace_root))
        raw = self._host_exec(["python3", "-c", _DISCOVER_PATHS_SCRIPT, root])
        return parse_workspace_path_discovery(raw, root)

    def stop_remove_container(self, name: str) -> None:  # pragma: no cover
        import docker

        try:
            container = self._client_lazy().containers.get(name)
        except docker.errors.NotFound:
            return
        container.stop()
        container.remove(force=True)

    # --- restic backup/restore (ciphertext volumes -> NAS repo) ---

    def _restic_env(self) -> dict[str, str]:  # pragma: no cover
        return {
            "RESTIC_REPOSITORY": self._restic_repository,
            "RESTIC_PASSWORD": self._restic_password,
        }

    def backup_volume(self, cipher_path: str, tags: list[str]) -> BackupResult:  # pragma: no cover
        env = " ".join(f"{k}={shlex.quote(v)}" for k, v in self._restic_env().items())
        tag_args = " ".join(f"--tag {shlex.quote(t)}" for t in tags)
        cmd = (
            f"{env} restic backup --json {tag_args} {shlex.quote(cipher_path)} "
            f"| tail -n1"
        )
        out = self._host_exec(["sh", "-c", cmd])
        summary = json.loads(out) if out.strip() else {}
        return BackupResult(
            snapshot_id=summary.get("snapshot_id", ""),
            size_bytes=summary.get("total_bytes_processed"),
        )

    def restore_snapshot(self, snapshot_id: str, dest_cipher_path: str) -> None:  # pragma: no cover
        env = " ".join(f"{k}={shlex.quote(v)}" for k, v in self._restic_env().items())
        # Restore the snapshot's contents directly into the destination cipher dir.
        cmd = (
            f"mkdir -p {shlex.quote(dest_cipher_path)} && "
            f"{env} restic restore {shlex.quote(snapshot_id)} "
            f"--target {shlex.quote(dest_cipher_path)} --include / "
        )
        self._host_exec(["sh", "-c", cmd])

    def forget_snapshot(self, snapshot_id: str) -> None:  # pragma: no cover
        env = " ".join(f"{k}={shlex.quote(v)}" for k, v in self._restic_env().items())
        cmd = f"{env} restic forget --prune {shlex.quote(snapshot_id)}"
        self._host_exec(["sh", "-c", cmd])

    def list_snapshots(self) -> list[SnapshotInfo]:  # pragma: no cover
        env = " ".join(f"{k}={shlex.quote(v)}" for k, v in self._restic_env().items())
        out = self._host_exec(["sh", "-c", f"{env} restic snapshots --json"])
        raw = json.loads(out) if out.strip() else []
        return [
            SnapshotInfo(
                snapshot_id=str(item["id"]),
                tags=tuple(str(tag) for tag in (item.get("tags") or [])),
            )
            for item in raw
            if item.get("id")
        ]

    def delete_workspace_path(self, workspace_root: str, path: str) -> None:  # pragma: no cover
        from seko_ai.services.retirement import (
            RetirementError,
            validate_workspace_path,
            validate_workspace_root,
        )

        lexical_root = validate_workspace_root(workspace_root)
        lexical_path = PurePosixPath(path)
        try:
            relative = lexical_path.relative_to(lexical_root)
        except ValueError as exc:
            raise RetirementError(f"workspace path is outside its root: {path!r}") from exc
        if len(relative.parts) != 2:
            raise RetirementError(f"workspace path is not an exact child: {path!r}")
        validate_workspace_path(
            lexical_root,
            path,
            user_id=relative.parts[0],
            container_name=relative.parts[1],
        )
        raw_state = self._host_exec(
            [
                "python3",
                "-c",
                _PATH_STATE_SCRIPT,
                str(lexical_root),
                relative.parts[0],
                relative.parts[1],
            ]
        )
        try:
            state = json.loads(raw_state)
            canonical_root = state["canonical_root"]
            canonical_target = state["canonical_target"]
            root_symlink = state["root_symlink"]
            user_symlink = state["user_symlink"]
            target_symlink = state["target_symlink"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RetirementError("workspace path validation returned invalid state") from exc
        if (
            not isinstance(canonical_root, str)
            or not isinstance(canonical_target, str)
            or not isinstance(root_symlink, bool)
            or not isinstance(user_symlink, bool)
            or not isinstance(target_symlink, bool)
        ):
            raise RetirementError("workspace path validation returned invalid values")
        target = validate_canonical_delete_target(
            canonical_root=canonical_root,
            canonical_target=canonical_target,
            user_id=relative.parts[0],
            container_name=relative.parts[1],
            root_symlink=root_symlink,
            user_symlink=user_symlink,
            target_symlink=target_symlink,
        )
        self._host_exec(
            [
                "python3",
                "-c",
                _DELETE_PATH_SCRIPT,
                str(PurePosixPath(target).parents[1]),
                relative.parts[0],
                relative.parts[1],
            ]
        )
