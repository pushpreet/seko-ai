"""Tests for usage summarization and metrics."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from seko_ai.services import usage as us


def test_summarize_from_metadata() -> None:
    raw: dict[str, Any] = {
        "metadata": {
            "total_tokens": 1000,
            "total_api_requests": 7,
            "total_prompt_tokens": 700,
            "total_completion_tokens": 300,
        }
    }
    s = us.summarize("alice", raw)
    assert s.total_tokens == 1000
    assert s.total_requests == 7
    assert s.prompt_tokens == 700
    assert s.completion_tokens == 300
    assert s.available is True


def test_summarize_from_results_when_no_metadata() -> None:
    raw: dict[str, Any] = {
        "results": [
            {
                "metrics": {
                    "total_tokens": 100,
                    "api_requests": 2,
                    "prompt_tokens": 70,
                    "completion_tokens": 30,
                }
            },
            {
                "metrics": {
                    "total_tokens": 50,
                    "api_requests": 1,
                    "prompt_tokens": 35,
                    "completion_tokens": 15,
                }
            },
        ]
    }
    s = us.summarize("bob", raw)
    assert s.total_tokens == 150
    assert s.total_requests == 3
    assert s.prompt_tokens == 105
    assert s.completion_tokens == 45


def test_summarize_empty() -> None:
    s = us.summarize("c", {"results": []})
    assert s.total_tokens == 0
    assert s.total_requests == 0
    assert s.prompt_tokens == 0
    assert s.completion_tokens == 0


def test_metrics_endpoint_exposes_series(client: TestClient) -> None:
    body = client.get("/metrics").text
    for name in [
        "seko_workspaces_active",
        "seko_users_total",
        "seko_logins_total",
        "seko_keys_issued_total",
        "seko_backups_total",
    ]:
        assert name in body
