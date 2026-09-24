"""Migration coverage for maintenance window owners."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from seko_ai.config import get_database_settings, get_settings


def test_legacy_owner_marker_becomes_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "migration.db"
    monkeypatch.setenv("SEKO_DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear()
    get_database_settings.cache_clear()
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "0d7e0f1a2b3c")

    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database) as conn:
        conn.execute(
            "INSERT INTO service_state (id, current_status, since, consecutive_failures, "
            "maintenance_active, maintenance_started_at, maintenance_message, created_at, "
            "updated_at) VALUES (1, 'up', ?, 0, 1, ?, 'gpu-stack:llm-b,llm-a', ?, ?)",
            (now, now, now, now),
        )

    command.upgrade(config, "head")
    with sqlite3.connect(database) as conn:
        owners, message = conn.execute(
            "SELECT maintenance_owners, maintenance_message FROM service_state"
        ).fetchone()
    assert json.loads(owners) == ["llm-a", "llm-b"]
    assert message == "Scheduled maintenance"

    command.downgrade(config, "0d7e0f1a2b3c")
    get_settings.cache_clear()
    get_database_settings.cache_clear()
