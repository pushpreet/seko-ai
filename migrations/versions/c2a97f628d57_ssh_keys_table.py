"""ssh_keys table

Revision ID: c2a97f628d57
Revises: 4ea211529f1c
Create Date: 2026-07-01 09:41:25.428740
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision: str = 'c2a97f628d57'
down_revision: str | None = '4ea211529f1c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fingerprint(public_key: str) -> str | None:
    parts = public_key.split()
    if len(parts) < 2:
        return None
    try:
        decoded = base64.b64decode(parts[1], validate=True)
    except Exception:
        return None
    return "SHA256:" + base64.b64encode(hashlib.sha256(decoded).digest()).decode().rstrip("=")


def upgrade() -> None:
    op.create_table('ssh_keys',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('public_key', sa.Text(), nullable=False),
    sa.Column('fingerprint', sa.String(length=120), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'fingerprint', name='uq_ssh_key_user_fingerprint')
    )
    with op.batch_alter_table('ssh_keys', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ssh_keys_fingerprint'), ['fingerprint'], unique=False)
        batch_op.create_index(batch_op.f('ix_ssh_keys_user_id'), ['user_id'], unique=False)

    # Data migration: move any legacy single ssh_public_key into the new table.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, ssh_public_key FROM users WHERE ssh_public_key IS NOT NULL")
    ).fetchall()
    now = datetime.now(UTC)
    for user_id, pubkey in rows:
        fp = _fingerprint(pubkey)
        if not fp:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO ssh_keys "
                "(user_id, title, public_key, fingerprint, created_at, updated_at) "
                "VALUES (:uid, :title, :pk, :fp, :now, :now)"
            ),
            {"uid": user_id, "title": "default", "pk": pubkey.strip(), "fp": fp, "now": now},
        )


def downgrade() -> None:
    with op.batch_alter_table('ssh_keys', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ssh_keys_user_id'))
        batch_op.drop_index(batch_op.f('ix_ssh_keys_fingerprint'))

    op.drop_table('ssh_keys')
