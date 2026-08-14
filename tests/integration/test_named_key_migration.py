"""Migration coverage for named logical API keys."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config

from seko_ai.config import get_settings

PREVIOUS_REVISION = "a1b2c3d4e5f6"


def _alembic_config(database: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


def test_named_key_migration_backfills_and_downgrades(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "migration.db"
    monkeypatch.setenv("SEKO_DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear()
    config = _alembic_config(database)
    command.upgrade(config, PREVIOUS_REVISION)

    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database) as conn:
        conn.executemany(
            "INSERT INTO users "
            "(id, subject, username, is_admin, created_at, updated_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            [
                (1, "sub-alice", "alice", now, now),
                (2, "sub-bob", "bob", now, now),
            ],
        )
        conn.execute(
            "INSERT INTO workspaces "
            "(id, user_id, name, container_name, harness, status, volume_path, "
            "created_at, updated_at) "
            "VALUES (1, 1, 'legacy', 'seko-legacy', 'pi', 'RUNNING', '/tmp/legacy', ?, ?)",
            (now, now),
        )
        conn.executemany(
            "INSERT INTO api_keys "
            "(id, user_id, workspace_id, litellm_key_id, key_alias, masked_key, active, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (10, 1, None, "tok-a1", "seko-alice-a", "sk-a1", 0, now, now),
                (11, 1, None, "tok-a2", "seko-alice-b", "sk-a2", 1, now, now),
                (12, 2, None, "tok-b1", "seko-bob-a", "sk-b1", 1, now, now),
                (13, 1, 1, "tok-ws", "seko-workspace", "sk-ws", 1, now, now),
            ],
        )

    command.upgrade(config, "f3a8c1d2e4b6")

    with sqlite3.connect(database) as conn:
        identities = conn.execute(
            "SELECT user_id, name, normalized_name FROM api_key_identities ORDER BY id"
        ).fetchall()
        links = conn.execute("SELECT id, identity_id FROM api_keys ORDER BY id").fetchall()

    assert identities == [
        (1, "API Key 1", "api key 1"),
        (1, "API Key 2", "api key 2"),
        (2, "API Key 1", "api key 1"),
    ]
    assert all(identity_id is not None for _, identity_id in links[:3])
    assert links[3] == (13, None)

    command.downgrade(config, PREVIOUS_REVISION)
    with sqlite3.connect(database) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(api_keys)").fetchall()}
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "identity_id" not in columns
    assert "api_key_identities" not in tables
    get_settings.cache_clear()
