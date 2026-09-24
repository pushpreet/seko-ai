"""maintenance window owners (leases)

Revision ID: b7d4e2f9a1c8
Revises: 0d7e0f1a2b3c
Create Date: 2026-09-24 00:00:00.000000

Adds ``service_state.maintenance_owners``: a JSON list of lease owners. A window opened by
``seko-ai maintenance start --owner TOKEN`` stays active until every owner releases it.
Windows that an external operator encoded as ``gpu-stack:<token>[,<token>]`` in the message
(before this interface existed) are converted into owners so no active lease is lost.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d4e2f9a1c8"
down_revision: str | None = "0d7e0f1a2b3c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_PREFIX = "gpu-stack:"


def upgrade() -> None:
    with op.batch_alter_table("service_state") as batch:
        batch.add_column(sa.Column("maintenance_owners", sa.Text(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, maintenance_message FROM service_state WHERE maintenance_active")
    ).fetchall()
    for row_id, message in rows:
        if not message or not message.startswith(_LEGACY_PREFIX):
            continue
        owners = sorted({t for t in message.removeprefix(_LEGACY_PREFIX).split(",") if t})
        bind.execute(
            sa.text(
                "UPDATE service_state SET maintenance_owners = :owners, "
                "maintenance_message = :message WHERE id = :id"
            ),
            {"owners": json.dumps(owners), "message": "Scheduled maintenance", "id": row_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("service_state") as batch:
        batch.drop_column("maintenance_owners")
