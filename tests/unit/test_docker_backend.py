"""Tests for the pure helpers in the Docker backend (no daemon required)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from seko_ai.services.docker_backend import (
    _DELETE_PATH_SCRIPT,
    build_run_kwargs,
    parse_ssh_target,
    parse_workspace_path_discovery,
    validate_canonical_delete_target,
)
from seko_ai.services.retirement import RetirementError
from seko_ai.services.workspaces import WorkspaceSpec


def _spec() -> WorkspaceSpec:
    return WorkspaceSpec(
        name="seko-ws-1-abc",
        image="ghcr.io/pushpreet/seko-workspace:latest",
        ssh_port=22001,
        authorized_keys="ssh-ed25519 AAAA",
        llm_base_url="https://llm.pushprh.com/v1",
        llm_api_key="sk-abc",
        llm_model="qwen3.6-27b",
        home_path="/opt/appdata/seko-ai/workspaces/1/seko-ws-1-abc/cleartext",
        cpus=8.0,
        mem="16g",
        pids_limit=512,
        labels={"ai.seko.owner": "1"},
    )


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("ssh://pushprh@10.37.20.50", "pushprh@10.37.20.50"),
        ("ssh://pushprh@epyc.pushprh.com:22", "pushprh@epyc.pushprh.com"),
        ("ssh://host-only", "host-only"),
    ],
)
def test_parse_ssh_target(host: str, expected: str) -> None:
    assert parse_ssh_target(host) == expected


@pytest.mark.parametrize("bad", ["tcp://1.2.3.4:2375", "unix:///var/run/docker.sock", "http://x"])
def test_parse_ssh_target_rejects_non_ssh(bad: str) -> None:
    with pytest.raises(ValueError, match="ssh"):
        parse_ssh_target(bad)


def test_build_run_kwargs_hardening_and_env() -> None:
    kwargs = build_run_kwargs(_spec())
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert "cap_drop" not in kwargs  # sshd needs default caps (SETUID/CHOWN/DAC_OVERRIDE)
    assert kwargs["pids_limit"] == 512
    assert kwargs["nano_cpus"] == 8_000_000_000
    assert kwargs["mem_limit"] == "16g"
    assert kwargs["ports"] == {"22/tcp": 22001}
    env = kwargs["environment"]
    assert env["SEKO_AUTHORIZED_KEYS"] == "ssh-ed25519 AAAA"
    assert env["LLM_API_KEY"] == "sk-abc"
    assert env["LLM_BASE_URL"] == "https://llm.pushprh.com/v1"
    assert kwargs["volumes"] == {
        "/opt/appdata/seko-ai/workspaces/1/seko-ws-1-abc/cleartext": {
            "bind": "/home/dev",
            "mode": "rw",
        }
    }
    assert kwargs["restart_policy"] == {"Name": "unless-stopped"}


def test_workspace_path_discovery_accepts_only_exact_directories() -> None:
    root = "/opt/appdata/seko-ai/workspaces"
    raw = json.dumps(
        [
            {
                "user_id": "42",
                "container_name": "seko-ws-42-orphan",
                "path": f"{root}/42/seko-ws-42-orphan",
                "workspace_symlink": False,
                "workspace_directory": True,
            },
            {
                "user_id": "42",
                "container_name": "seko-ws-42-file",
                "path": f"{root}/42/seko-ws-42-file",
                "workspace_symlink": False,
                "workspace_directory": False,
            },
        ]
    )
    assert parse_workspace_path_discovery(raw, root) == [
        f"{root}/42/seko-ws-42-orphan"
    ]


@pytest.mark.parametrize(
    "record",
    [
        {"user_id": "42", "user_symlink": True},
        {
            "user_id": "42",
            "container_name": "seko-ws-42-link",
            "path": "/opt/appdata/seko-ai/workspaces/42/seko-ws-42-link",
            "workspace_symlink": True,
            "workspace_directory": False,
        },
    ],
)
def test_workspace_path_discovery_rejects_symlink_components(
    record: dict[str, object],
) -> None:
    with pytest.raises(RetirementError, match="symlinked"):
        parse_workspace_path_discovery(
            json.dumps([record]),
            "/opt/appdata/seko-ai/workspaces",
        )


def test_workspace_path_discovery_rejects_symlinked_root() -> None:
    root = "/opt/appdata/seko-ai/workspaces"
    with pytest.raises(RetirementError, match="symlinked workspace root"):
        parse_workspace_path_discovery(
            json.dumps([{"root_symlink": True, "path": root}]),
            root,
        )


def test_canonical_delete_target_requires_exact_identity() -> None:
    target = validate_canonical_delete_target(
        canonical_root="/srv/workspaces",
        canonical_target="/srv/workspaces/42/seko-ws-42-safe",
        user_id="42",
        container_name="seko-ws-42-safe",
        root_symlink=False,
        user_symlink=False,
        target_symlink=False,
    )
    assert target == "/srv/workspaces/42/seko-ws-42-safe"

    with pytest.raises(ValueError, match="mismatch"):
        validate_canonical_delete_target(
            canonical_root="/srv/workspaces",
            canonical_target="/srv/workspaces-other/42/seko-ws-42-safe",
            user_id="42",
            container_name="seko-ws-42-safe",
            root_symlink=False,
            user_symlink=False,
            target_symlink=False,
        )

    with pytest.raises(RetirementError, match="workspace_data_root"):
        validate_canonical_delete_target(
            canonical_root="/opt",
            canonical_target="/opt/42/seko-ws-42-safe",
            user_id="42",
            container_name="seko-ws-42-safe",
            root_symlink=False,
            user_symlink=False,
            target_symlink=False,
        )


@pytest.mark.parametrize(("user_symlink", "target_symlink"), [(True, False), (False, True)])
def test_canonical_delete_target_rejects_symlink_components(
    user_symlink: bool, target_symlink: bool
) -> None:
    with pytest.raises(ValueError, match="symlinked"):
        validate_canonical_delete_target(
            canonical_root="/srv/workspaces",
            canonical_target="/srv/workspaces/42/seko-ws-42-safe",
            user_id="42",
            container_name="seko-ws-42-safe",
            root_symlink=False,
            user_symlink=user_symlink,
            target_symlink=target_symlink,
        )


def test_canonical_delete_target_rejects_symlinked_root() -> None:
    with pytest.raises(ValueError, match="symlinked workspace root"):
        validate_canonical_delete_target(
            canonical_root="/srv/workspaces",
            canonical_target="/srv/workspaces/42/seko-ws-42-safe",
            user_id="42",
            container_name="seko-ws-42-safe",
            root_symlink=True,
            user_symlink=False,
            target_symlink=False,
        )


def test_non_following_delete_unlinks_nested_symlinks() -> None:
    scratch = Path.cwd() / f".test-retirement-delete-{uuid.uuid4().hex}"
    target = scratch / "root" / "42" / "seko-ws-42-safe"
    outside = scratch / "outside"
    try:
        target.mkdir(parents=True)
        outside.mkdir(parents=True)
        marker = outside / "must-survive"
        marker.write_text("safe")
        os.symlink(outside, target / "nested-link")

        subprocess.run(
            [
                sys.executable,
                "-c",
                _DELETE_PATH_SCRIPT,
                str(scratch / "root"),
                "42",
                "seko-ws-42-safe",
            ],
            check=True,
        )

        assert not target.exists()
        assert marker.read_text() == "safe"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_non_following_delete_rejects_symlinked_target() -> None:
    scratch = Path.cwd() / f".test-retirement-symlink-{uuid.uuid4().hex}"
    user_path = scratch / "root" / "42"
    outside = scratch / "outside"
    try:
        user_path.mkdir(parents=True)
        outside.mkdir(parents=True)
        marker = outside / "must-survive"
        marker.write_text("safe")
        os.symlink(outside, user_path / "seko-ws-42-link")

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                _DELETE_PATH_SCRIPT,
                str(scratch / "root"),
                "42",
                "seko-ws-42-link",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert "refusing symlinked workspace target" in result.stderr
        assert marker.read_text() == "safe"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
