"""Tests for the LLM status probe, hysteresis, transitions, and maintenance window."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.orm import Session

from seko_ai import management
from seko_ai.config import Settings
from seko_ai.models import ServiceStatus, User
from seko_ai.services import notifications
from seko_ai.services import status as status_service
from seko_ai.services.status import ProbeResult


@pytest.fixture
def settings() -> Settings:
    return Settings(
        master_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        litellm_base_url="http://litellm:4000",
        litellm_master_key="sk-test-master",
        status_fail_threshold=3,
        resend_api_key="re_test",
        base_url="http://testserver",
    )


_UID = 0


def _add_user(session: Session, email: str | None = "u@example.com") -> None:
    global _UID
    _UID += 1
    session.add(User(subject=f"s-{_UID}", username="u", email=email))
    session.flush()


class _CaptureSender:
    """Records notification calls instead of hitting Resend."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in (
            "notify_down",
            "notify_restored",
            "notify_maintenance_start",
            "notify_maintenance_end",
        ):
            monkeypatch.setattr(
                notifications, name, self._make(name), raising=True
            )

    def _make(self, name: str):  # type: ignore[no-untyped-def]
        def _fn(*args: object, **kwargs: object) -> int:
            self.calls.append(name)
            return 1

        return _fn


# --- Probe classification (mocked HTTP) --------------------------------------------------


def _client_returning(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_probe_litellm_health_up(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        assert request.headers["Authorization"] == "Bearer sk-test-master"
        return httpx.Response(
            200,
            json={"healthy_endpoints": [{"model": "qwen"}], "unhealthy_endpoints": []},
        )

    result = status_service.probe(settings, client=_client_returning(handler))
    assert result.ok is True


def test_probe_litellm_health_down_when_no_healthy(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"healthy_endpoints": [], "unhealthy_endpoints": [{"m": 1}]}
        )

    result = status_service.probe(settings, client=_client_returning(handler))
    assert result.ok is False


def test_probe_down_on_connection_error(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    result = status_service.probe(settings, client=_client_returning(handler))
    assert result.ok is False
    assert "failed" in result.detail


def test_probe_http_mode(settings: Settings) -> None:
    settings = settings.model_copy(update={"status_probe_mode": "http", "status_probe_url": "http://x/health"})

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://x/health"
        return httpx.Response(200, text="ok")

    assert status_service.probe(settings, client=_client_returning(handler)).ok is True


# --- Hysteresis + transitions ------------------------------------------------------------


def test_stays_up_below_failure_threshold(db_session: Session, settings: Settings) -> None:
    status_service.record_probe(db_session, settings, ProbeResult(True, "ok"))
    # Two failures (< threshold of 3) must NOT flip to down.
    status_service.record_probe(db_session, settings, ProbeResult(False, "x"))
    state = status_service.record_probe(db_session, settings, ProbeResult(False, "x"))
    assert state.current_status == ServiceStatus.UP
    assert state.consecutive_failures == 2


def test_flips_down_after_threshold_and_emails(
    db_session: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_user(db_session)
    cap = _CaptureSender()
    cap.install(monkeypatch)
    status_service.record_probe(db_session, settings, ProbeResult(True, "ok"))
    for _ in range(3):
        state = status_service.record_probe(db_session, settings, ProbeResult(False, "x"))
    assert state.current_status == ServiceStatus.DOWN
    assert cap.calls == ["notify_down"]
    db_session.flush()
    events = status_service.recent_events(db_session)
    assert (events[0].from_status, events[0].to_status) == (ServiceStatus.UP, ServiceStatus.DOWN)


def test_recovers_and_emails_restored(
    db_session: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_user(db_session)
    cap = _CaptureSender()
    cap.install(monkeypatch)
    for _ in range(3):
        status_service.record_probe(db_session, settings, ProbeResult(False, "x"))
    state = status_service.record_probe(db_session, settings, ProbeResult(True, "ok"))
    assert state.current_status == ServiceStatus.UP
    assert cap.calls == ["notify_down", "notify_restored"]


def test_first_boot_up_does_not_email_restored(
    db_session: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_user(db_session)
    cap = _CaptureSender()
    cap.install(monkeypatch)
    state = status_service.record_probe(db_session, settings, ProbeResult(True, "ok"))
    assert state.current_status == ServiceStatus.UP
    assert cap.calls == []  # UNKNOWN -> UP is not a "restored" event


# --- Maintenance suppression -------------------------------------------------------------


def test_maintenance_suppresses_down_email(
    db_session: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_user(db_session)
    cap = _CaptureSender()
    cap.install(monkeypatch)
    status_service.start_maintenance(db_session, settings, message="gpu")
    for _ in range(3):
        state = status_service.record_probe(db_session, settings, ProbeResult(False, "x"))
    assert state.current_status == ServiceStatus.DOWN
    # start email sent, but no down email during the window.
    assert cap.calls == ["notify_maintenance_start"]


def test_end_maintenance_emails_and_resets(
    db_session: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    cap = _CaptureSender()
    cap.install(monkeypatch)
    status_service.start_maintenance(db_session, settings)
    state = status_service.end_maintenance(db_session, settings)
    assert state.maintenance_active is False
    assert cap.calls == ["notify_maintenance_start", "notify_maintenance_end"]


# --- Notifications recipient collection --------------------------------------------------


def test_recipient_emails_dedupes_and_skips_null(db_session: Session) -> None:
    _add_user(db_session, "a@example.com")
    _add_user(db_session, "a@example.com")
    _add_user(db_session, None)
    assert notifications.recipient_emails(db_session) == ["a@example.com"]


def test_send_email_disabled_without_key(db_session: Session) -> None:
    s = Settings(master_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=", resend_api_key="")
    assert notifications.send_email(s, to=["a@x"], subject="s", text="t") == 0


# --- Management command wiring -----------------------------------------------------------


def test_check_status_command(
    db_session: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(status_service, "probe", lambda s, **k: ProbeResult(True, "ok"))
    assert management.check_status(db_session, settings) == "up"


def test_maintenance_command(db_session: Session, settings: Settings) -> None:
    assert management.maintenance(db_session, settings, "start", "msg") == "active"
    assert management.maintenance(db_session, settings, "status", None) == "active"
    assert management.maintenance(db_session, settings, "end", None) == "inactive"
