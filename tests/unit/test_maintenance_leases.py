"""Maintenance lease semantics exposed through ``seko-ai maintenance --owner``."""

from __future__ import annotations

import pytest
import structlog
from sqlalchemy.orm import Session

from seko_ai import management
from seko_ai.config import Settings


@pytest.fixture
def quiet(settings: Settings) -> Settings:
    return settings.model_copy(update={"status_notify_on_maintenance": False})


def _run(session: Session, settings: Settings, action: str, **kwargs: object) -> dict:
    return management.maintenance(session, settings, action, **kwargs)  # type: ignore[arg-type]


def test_lease_opens_and_closes_window(db_session: Session, quiet: Settings) -> None:
    started = _run(db_session, quiet, "start", owner="llm-op1", message="GPU swap")
    assert started["active"] and started["owned"]
    assert started["owners"] == ["llm-op1"]
    assert started["message"] == "GPU swap"

    ended = _run(db_session, quiet, "end", owner="llm-op1")
    assert ended == {**ended, "active": False, "owned": True, "window_ended": True}


def test_window_stays_until_last_owner_releases(db_session: Session, quiet: Settings) -> None:
    _run(db_session, quiet, "start", owner="a")
    assert _run(db_session, quiet, "start", owner="b")["owners"] == ["a", "b"]
    first = _run(db_session, quiet, "end", owner="a")
    assert first["active"] and first["owned"] and not first["window_ended"]
    assert _run(db_session, quiet, "end", owner="b")["window_ended"]


def test_lease_never_takes_over_a_manual_window(db_session: Session, quiet: Settings) -> None:
    _run(db_session, quiet, "start", message="operator testing")
    joined = _run(db_session, quiet, "start", owner="llm-op1")
    assert joined["active"] and not joined["owned"] and joined["owners"] == []
    released = _run(db_session, quiet, "end", owner="llm-op1")
    assert released["active"] and not released["owned"]
    assert released["message"] == "operator testing"


def test_manual_start_takes_over_a_leased_window(db_session: Session, quiet: Settings) -> None:
    _run(db_session, quiet, "start", owner="llm-op1")
    taken = _run(db_session, quiet, "start", message="keep it down")
    assert taken["active"] and taken["owners"] == []
    assert _run(db_session, quiet, "end", owner="llm-op1")["active"]


def test_unowned_end_closes_any_window(db_session: Session, quiet: Settings) -> None:
    _run(db_session, quiet, "start", owner="a")
    ended = _run(db_session, quiet, "end")
    assert not ended["active"] and ended["owners"] == []


def test_leases_can_suppress_notifications(
    db_session: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from seko_ai.services import notifications

    sent: list[str] = []
    monkeypatch.setattr(
        notifications, "notify_maintenance_start", lambda *a, **k: sent.append("start")
    )
    monkeypatch.setattr(notifications, "notify_maintenance_end", lambda *a, **k: sent.append("end"))
    _run(db_session, settings, "start", owner="a", notify=False)
    _run(db_session, settings, "end", owner="a", notify=False)
    assert sent == []


@pytest.mark.parametrize("owner", ["", "Upper", "x;id", "-lead", "a" * 65])
def test_cli_rejects_invalid_owner(owner: str) -> None:
    with pytest.raises(SystemExit):
        management.build_parser().parse_args(["maintenance", "start", "--owner", owner])


def test_cli_prints_json(
    db_session: Session, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    monkeypatch.setenv("SEKO_STATUS_NOTIFY_ON_MAINTENANCE", "false")
    management.get_settings.cache_clear()
    try:
        assert management.run(["maintenance", "start", "--owner", "op", "--json"]) == 0
    finally:
        management.get_settings.cache_clear()
        structlog.reset_defaults()  # drop the CLI's stderr stream bound to capsys
    payload = json.loads(capsys.readouterr().out)
    assert payload["owned"] is True and payload["owners"] == ["op"]
