"""Tests for usage summarization and metrics."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from seko_ai.services import usage as us


def test_summarize_from_metadata() -> None:
    raw: dict[str, Any] = {
        "metadata": {"total_spend": 1.2345, "total_tokens": 1000, "total_api_requests": 7}
    }
    s = us.summarize("alice", raw)
    assert s.total_spend == 1.2345
    assert s.total_tokens == 1000
    assert s.total_requests == 7
    assert s.available is True


def test_summarize_from_results_when_no_metadata() -> None:
    raw: dict[str, Any] = {
        "results": [
            {"metrics": {"spend": 0.5, "total_tokens": 100, "api_requests": 2}},
            {"metrics": {"spend": 0.25, "total_tokens": 50, "api_requests": 1}},
        ]
    }
    s = us.summarize("bob", raw)
    assert s.total_spend == 0.75
    assert s.total_tokens == 150
    assert s.total_requests == 3


def test_summarize_empty() -> None:
    s = us.summarize("c", {"results": []})
    assert s.total_spend == 0.0
    assert s.total_tokens == 0
    assert s.total_requests == 0


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
