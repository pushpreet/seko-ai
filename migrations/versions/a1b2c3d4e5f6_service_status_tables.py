"""service status + maintenance state tables

Revision ID: a1b2c3d4e5f6
Revises: c2a97f628d57
Create Date: 2026-07-24 12:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = 'c2a97f628d57'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'service_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column(
            'current_status',
            sa.Enum('up', 'down', 'unknown', native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column('since', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_detail', sa.Text(), nullable=True),
        sa.Column('consecutive_failures', sa.Integer(), nullable=False),
        sa.Column('maintenance_active', sa.Boolean(), nullable=False),
        sa.Column('maintenance_started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('maintenance_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'status_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column(
            'from_status',
            sa.Enum('up', 'down', 'unknown', native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column(
            'to_status',
            sa.Enum('up', 'down', 'unknown', native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('during_maintenance', sa.Boolean(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_status_events_at'), 'status_events', ['at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_status_events_at'), table_name='status_events')
    op.drop_table('status_events')
    op.drop_table('service_state')
