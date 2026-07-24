"""Integration tests for the status page, banner, and admin maintenance toggle."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def _login(client: TestClient, groups: list[str] | None = None) -> None:
    provider = client.app.state.oauth.authelia  # type: ignore[attr-defined]

    async def fake_token(request: Any) -> dict[str, Any]:
        return {
            "userinfo": {
                "sub": "u-status",
                "preferred_username": "alice",
                "email": "alice@example.com",
                "groups": groups or ["llm_users"],
            }
        }

    provider.authorize_access_token = fake_token
    client.get("/auth/callback?code=abc", follow_redirects=False)


def test_status_page_renders_without_login(client: TestClient) -> None:
    resp = client.get("/status")
    assert resp.status_code == 200
    assert "Service status" in resp.text


def test_banner_polls_and_not_down_by_default(client: TestClient) -> None:
    resp = client.get("/status/banner")
    assert resp.status_code == 200
    assert "currently unavailable" not in resp.text
    assert 'id="status-banner"' in resp.text  # keeps polling


def test_banner_shows_operational_when_up(client: TestClient, db_session: Any) -> None:
    from seko_ai.config import get_settings
    from seko_ai.services import status as status_service

    status_service.record_probe(
        db_session,
        get_settings(),
        status_service.ProbeResult(ok=True, detail="ok"),
        notify=False,
    )
    db_session.commit()
    resp = client.get("/status/banner")
    assert resp.status_code == 200
    assert "operational" in resp.text.lower()
    assert 'id="status-banner"' in resp.text


def test_maintenance_toggle_requires_admin(client: TestClient) -> None:
    _login(client, groups=["llm_users"])
    resp = client.post("/status/maintenance/start", data={"message": "x"}, follow_redirects=False)
    assert resp.status_code == 403


def test_admin_can_toggle_maintenance_and_banner_reflects_it(client: TestClient) -> None:
    _login(client, groups=["homelab_admins"])
    resp = client.post(
        "/status/maintenance/start", data={"message": "GPU update"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/status"

    banner = client.get("/status/banner")
    assert "Scheduled maintenance in progress" in banner.text
    assert "GPU update" in banner.text

    end = client.post("/status/maintenance/end", follow_redirects=False)
    assert end.status_code == 303
    assert "Scheduled maintenance" not in client.get("/status/banner").text
