"""Smoke tests for the application scaffolding."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_metrics_exposition(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


def test_index_anonymous_shows_sign_in(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Sign in" in resp.text
