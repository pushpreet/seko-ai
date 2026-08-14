"""Migration coverage for final removal of the retired workspace schema."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config

from seko_ai.config import get_settings

PREVIOUS_REVISION = "f3a8c1d2e4b6"


def _alembic_config(database: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _seed_previous_revision(database: Path) -> None:
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database) as conn:
        conn.execute(
            "INSERT INTO users "
            "(id, subject, username, email, display_name, is_admin, wrapped_dek, "
            "ssh_public_key, created_at, updated_at) "
            "VALUES (1, 'sub-alice', 'alice', 'alice@example.com', 'Alice', 1, "
            "'wrapped-secret', 'ssh-ed25519 legacy', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO api_key_identities "
            "(id, user_id, name, normalized_name, created_at, updated_at) "
            "VALUES (20, 1, 'Laptop', 'laptop', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO workspaces "
            "(id, user_id, name, container_name, harness, status, ssh_port, volume_path, "
            "litellm_key_alias, created_at, updated_at) "
            "VALUES (30, 1, 'retired', 'seko-retired', 'pi', 'RUNNING', 22000, "
            "'/opt/appdata/seko-ai/workspaces/1/retired', 'workspace-alias', ?, ?)",
            (now, now),
        )
        conn.executemany(
            "INSERT INTO api_keys "
            "(id, user_id, workspace_id, identity_id, litellm_key_id, key_alias, masked_key, "
            "active, created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?, ?, 1, ?, ?)",
            [
                (40, None, 20, "normal-token", "normal-alias", "sk-no…rmal", now, now),
                (41, 30, None, "workspace-token", "workspace-alias", "sk-wo…pace", now, now),
            ],
        )
        conn.execute(
            "INSERT INTO backups "
            "(id, workspace_id, snapshot_id, trigger, succeeded, created_at, updated_at) "
            "VALUES (50, 30, 'snapshot-1', 'NIGHTLY', 1, ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO ssh_keys "
            "(id, user_id, title, public_key, fingerprint, created_at, updated_at) "
            "VALUES (60, 1, 'Laptop', 'ssh-ed25519 key', 'SHA256:test', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO service_state "
            "(id, current_status, since, last_checked_at, last_detail, consecutive_failures, "
            "maintenance_active, maintenance_started_at, maintenance_message, created_at, "
            "updated_at) VALUES (1, 'up', ?, ?, 'healthy', 0, 0, NULL, NULL, ?, ?)",
            (now, now, now, now),
        )
        conn.execute(
            "INSERT INTO status_events "
            "(id, from_status, to_status, at, during_maintenance, note, created_at, updated_at) "
            "VALUES (70, 'unknown', 'up', ?, 0, 'recovered', ?, ?)",
            (now, now, now),
        )


def test_removal_preserves_normal_keys_users_and_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "workspace-removal.db"
    monkeypatch.setenv("SEKO_DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear()
    config = _alembic_config(database)
    command.upgrade(config, PREVIOUS_REVISION)
    _seed_previous_revision(database)

    command.upgrade(config, "head")

    with sqlite3.connect(database) as conn:
        tables = _tables(conn)
        assert {"workspaces", "backups", "ssh_keys"}.isdisjoint(tables)
        assert "workspace_id" not in _columns(conn, "api_keys")
        assert {"wrapped_dek", "ssh_public_key"}.isdisjoint(_columns(conn, "users"))
        assert conn.execute(
            "SELECT subject, username, email, display_name, is_admin FROM users"
        ).fetchall() == [("sub-alice", "alice", "alice@example.com", "Alice", 1)]
        assert conn.execute(
            "SELECT id, user_id, identity_id, litellm_key_id, key_alias, masked_key, active "
            "FROM api_keys"
        ).fetchall() == [
            (40, 1, 20, "normal-token", "normal-alias", "sk-no…rmal", 1)
        ]
        assert conn.execute(
            "SELECT id, user_id, name, normalized_name FROM api_key_identities"
        ).fetchall() == [(20, 1, "Laptop", "laptop")]
        assert conn.execute(
            "SELECT id, current_status, last_detail FROM service_state"
        ).fetchall() == [(1, "up", "healthy")]
        assert conn.execute(
            "SELECT id, from_status, to_status, note FROM status_events"
        ).fetchall() == [(70, "unknown", "up", "recovered")]

    get_settings.cache_clear()


def test_downgrade_restores_schema_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "workspace-schema-downgrade.db"
    monkeypatch.setenv("SEKO_DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear()
    config = _alembic_config(database)
    command.upgrade(config, PREVIOUS_REVISION)
    _seed_previous_revision(database)
    command.upgrade(config, "head")

    command.downgrade(config, PREVIOUS_REVISION)

    with sqlite3.connect(database) as conn:
        assert {"workspaces", "backups", "ssh_keys"} <= _tables(conn)
        assert "workspace_id" in _columns(conn, "api_keys")
        assert {"wrapped_dek", "ssh_public_key"} <= _columns(conn, "users")
        assert conn.execute("SELECT COUNT(*) FROM workspaces").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM backups").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM ssh_keys").fetchone() == (0,)
        assert conn.execute(
            "SELECT id, workspace_id, litellm_key_id FROM api_keys"
        ).fetchall() == [(40, None, "normal-token")]

    get_settings.cache_clear()
