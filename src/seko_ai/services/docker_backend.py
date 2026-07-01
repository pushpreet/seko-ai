"""Docker-over-SSH backend: runs workspace containers on the LLM host (epyc).

seko-ai runs on core-infra and reaches epyc's Docker engine over SSH (``DOCKER_HOST=ssh://
user@host``) — no exposed TCP socket. Encrypted-home provisioning (gocryptfs) is a *host*
operation, so it is run over the same SSH connection.

NOTE: the live Docker/SSH/gocryptfs paths require epyc + a Docker daemon to validate; only
the pure helpers here (``build_run_kwargs``, ``parse_ssh_target``) are unit-tested in CI.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import Any
from urllib.parse import urlparse

from seko_ai.logging_config import get_logger
from seko_ai.services.workspaces import BackupResult, ContainerInfo, WorkspaceSpec

log = get_logger("seko_ai.docker_backend")


def parse_ssh_target(docker_host: str) -> str:
    """Turn ``ssh://user@host[:port]`` into an ``ssh`` CLI target (``user@host``)."""
    parsed = urlparse(docker_host)
    if parsed.scheme != "ssh" or not parsed.hostname:
        raise ValueError(f"expected ssh:// docker host, got {docker_host!r}")
    user = f"{parsed.username}@" if parsed.username else ""
    return f"{user}{parsed.hostname}"


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
        cmd = ["ssh", self._ssh_target, "--", *argv]
        result = subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            check=True,
        )
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
        self._host_exec(["sh", "-c", f"mountpoint -q {shlex.quote(clear)} && "
                         f"fusermount -u {shlex.quote(clear)} || true"])

    # --- Container lifecycle ---

    def create(self, spec: WorkspaceSpec) -> None:  # pragma: no cover
        self._client_lazy().containers.run(**build_run_kwargs(spec))

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
