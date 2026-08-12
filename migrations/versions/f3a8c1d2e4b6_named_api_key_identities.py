"""named API key identities

Revision ID: f3a8c1d2e4b6
Revises: a1b2c3d4e5f6
Create Date: 2026-08-11 08:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "f3a8c1d2e4b6"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_key_identities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("normalized_name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "normalized_name",
            name="uq_api_key_identity_user_normalized_name",
        ),
    )
    with op.batch_alter_table("api_key_identities", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_api_key_identities_user_id"), ["user_id"], unique=False
        )

    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.add_column(sa.Column("identity_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_api_keys_identity_id"), ["identity_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_api_keys_identity_id",
            "api_key_identities",
            ["identity_id"],
            ["id"],
            ondelete="SET NULL",
        )

    conn = op.get_bind()
    identities = sa.table(
        "api_key_identities",
        sa.column("id", sa.Integer()),
        sa.column("user_id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("normalized_name", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows = conn.execute(
        sa.text(
            "SELECT id, user_id FROM api_keys "
            "WHERE workspace_id IS NULL ORDER BY user_id, created_at, id"
        )
    ).fetchall()
    now = datetime.now(UTC)
    counts: dict[int, int] = {}
    for key_id, user_id in rows:
        counts[user_id] = counts.get(user_id, 0) + 1
        name = f"API Key {counts[user_id]}"
        result = conn.execute(
            sa.insert(identities).values(
                user_id=user_id,
                name=name,
                normalized_name=name.casefold(),
                created_at=now,
                updated_at=now,
            )
        )
        identity_id = result.lastrowid
        if identity_id is None:
            raise RuntimeError("Could not determine backfilled API key identity id")
        conn.execute(
            sa.text("UPDATE api_keys SET identity_id = :identity_id WHERE id = :key_id"),
            {"identity_id": identity_id, "key_id": key_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.drop_constraint("fk_api_keys_identity_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_api_keys_identity_id"))
        batch_op.drop_column("identity_id")

    with op.batch_alter_table("api_key_identities", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_api_key_identities_user_id"))

    op.drop_table("api_key_identities")
