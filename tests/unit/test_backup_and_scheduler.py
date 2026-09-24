"""Online backup and the in-process status scheduler."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from seko_ai import management, scheduler
from seko_ai.config import Settings
from seko_ai.services import status as status_service


def test_backup_copies_sqlite_database(tmp_path: Path) -> None:
    source = tmp_path / "seko.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('kept')")
    destination = tmp_path / "seko.db.bak"
    assert management.backup(destination, f"sqlite:///{source}") == destination
    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT v FROM t").fetchall() == [("kept",)]
    assert not (tmp_path / ".seko.db.bak.tmp").exists()


@pytest.mark.parametrize("url", ["sqlite://", "postgresql://u@h/db"])
def test_backup_rejects_non_file_databases(tmp_path: Path, url: str) -> None:
    with pytest.raises(ValueError, match="file-backed SQLite"):
        management.backup(tmp_path / "out.db", url)


def test_check_status_skips_when_another_runner_probed_recently(
    db_session: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(status_service, "probe", lambda s: status_service.ProbeResult(False, "x"))
    state = status_service.get_or_create_state(db_session)
    state.last_checked_at = datetime.now(UTC)
    assert management.check_status(db_session, settings, min_interval=30) is None
    assert state.consecutive_failures == 0
    assert management.check_status(db_session, settings) is not None
    assert state.consecutive_failures == 1


def test_scheduler_run_uses_half_interval_guard(
    db_session: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float | None] = []

    def fake_check(session: Session, configured: Settings, *, min_interval: float) -> str:
        seen.append(min_interval)
        return "up"

    monkeypatch.setattr(management, "check_status", fake_check)
    configured = settings.model_copy(update={"status_probe_interval": 60.0})
    assert scheduler.run_status_check(configured) == "up"
    assert seen == [30.0]


async def test_scheduler_disabled_starts_no_task(settings: Settings) -> None:
    async with scheduler.status_scheduler(settings):
        pass


async def test_scheduler_loop_survives_failures(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    calls: list[int] = []

    def flaky(configured: Settings) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "run_status_check", flaky)
    enabled = settings.model_copy(
        update={"status_scheduler_enabled": True, "status_probe_interval": 0.01}
    )
    async with scheduler.status_scheduler(enabled):
        for _ in range(100):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.01)
    assert len(calls) >= 2
