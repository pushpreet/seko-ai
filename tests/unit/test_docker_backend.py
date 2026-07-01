"""Tests for the pure helpers in the Docker backend (no daemon required)."""

from __future__ import annotations

import pytest

from seko_ai.services.docker_backend import build_run_kwargs, parse_ssh_target
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
    assert kwargs["cap_drop"] == ["ALL"]
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
