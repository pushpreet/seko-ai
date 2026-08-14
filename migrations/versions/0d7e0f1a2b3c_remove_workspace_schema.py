"""remove retired workspace schema

Revision ID: 0d7e0f1a2b3c
Revises: f3a8c1d2e4b6
Create Date: 2026-08-14 01:00:00.000000

The downgrade recreates only the retired schema. Deleted workspace, backup, SSH-key,
wrapped-key, and workspace-scoped API-key data cannot be restored.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0d7e0f1a2b3c"
down_revision: str | None = "f3a8c1d2e4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM api_keys WHERE workspace_id IS NOT NULL"))

    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.drop_constraint("fk_api_keys_workspace_id", type_="foreignkey")
        batch_op.drop_index("ix_api_keys_workspace_id")
        batch_op.drop_column("workspace_id")

    op.drop_table("backups")
    op.drop_table("workspaces")
    op.drop_table("ssh_keys")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("wrapped_dek")
        batch_op.drop_column("ssh_public_key")


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("wrapped_dek", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("ssh_public_key", sa.Text(), nullable=True))

    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("container_name", sa.String(length=255), nullable=False),
        sa.Column("harness", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PROVISIONING",
                "RUNNING",
                "STOPPED",
                "TERMINATING",
                "TERMINATED",
                "ERROR",
                name="workspacestatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("ssh_port", sa.Integer(), nullable=True),
        sa.Column("volume_path", sa.String(length=512), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("litellm_key_alias", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_workspaces_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspaces"),
        sa.UniqueConstraint("container_name", name="uq_workspace_container"),
    )
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.create_index("ix_workspaces_user_id", ["user_id"], unique=False)

    op.create_table(
        "backups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.String(length=255), nullable=False),
        sa.Column(
            "trigger",
            sa.Enum(
                "NIGHTLY",
                "MANUAL",
                "ON_TERMINATE",
                name="backuptrigger",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_backups_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backups"),
    )
    with op.batch_alter_table("backups", schema=None) as batch_op:
        batch_op.create_index("ix_backups_workspace_id", ["workspace_id"], unique=False)

    op.create_table(
        "ssh_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=120), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_ssh_keys_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ssh_keys"),
        sa.UniqueConstraint(
            "user_id",
            "fingerprint",
            name="uq_ssh_key_user_fingerprint",
        ),
    )
    with op.batch_alter_table("ssh_keys", schema=None) as batch_op:
        batch_op.create_index("ix_ssh_keys_fingerprint", ["fingerprint"], unique=False)
        batch_op.create_index("ix_ssh_keys_user_id", ["user_id"], unique=False)

    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.add_column(sa.Column("workspace_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_api_keys_workspace_id", ["workspace_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_api_keys_workspace_id",
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="SET NULL",
        )
