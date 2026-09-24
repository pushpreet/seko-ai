"""Tests for service-status and maintenance management commands."""

from __future__ import annotations

from sqlalchemy.orm import Session

from seko_ai import management
from seko_ai.config import Settings
from seko_ai.models import ServiceStatus
from seko_ai.services import status as status_service


def test_check_status_records_probe(
    db_session: Session,
    settings: Settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        status_service,
        "probe",
        lambda configured: status_service.ProbeResult(ok=True, detail="healthy"),
    )

    result = management.check_status(db_session, settings)

    assert result == "up"
    state = status_service.get_or_create_state(db_session)
    assert state.current_status is ServiceStatus.UP
    assert state.last_detail == "healthy"


def test_maintenance_start_status_and_end(
    db_session: Session,
    settings: Settings,
) -> None:
    quiet_settings = settings.model_copy(update={"status_notify_on_maintenance": False})

    assert management.maintenance(db_session, quiet_settings, "start", "planned")["active"]
    state = status_service.get_or_create_state(db_session)
    assert state.maintenance_message == "planned"
    status = management.maintenance(db_session, quiet_settings, "status", None)
    assert status["active"]
    db_session.commit()
    reloaded = management.maintenance(db_session, quiet_settings, "status", None)
    assert reloaded["started_at"].endswith("+00:00")
    assert not management.maintenance(db_session, quiet_settings, "end", None)["active"]
    assert state.maintenance_message is None
