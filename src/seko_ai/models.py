"""ORM models for seko-ai.

The schema is intentionally small (SQLite, <3 users). Durable *workspace* data lives in
encrypted volumes on epyc; this DB stores only metadata, key references, and lifecycle state.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from seko_ai.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class User(TimestampMixin, Base):
    """A person who signed in via Authelia OIDC (identified by the OIDC subject)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(255), index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_admin: Mapped[bool] = mapped_column(default=False)
    # Wrapped (envelope-encrypted) per-user data-encryption key, base64. Admin can unwrap.
    wrapped_dek: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Public SSH key the user registers for hosted-workspace access.
    ssh_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    workspaces: Mapped[list[Workspace]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ApiKey(TimestampMixin, Base):
    """A LiteLLM virtual key issued to a user. We store the LiteLLM token id + a masked hint.

    The full key value is shown to the user exactly once (on creation) and never persisted.
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Set when the key is bound to a hosted workspace (injected into the container). Such keys
    # are managed by the workspace lifecycle and hidden from the user's own /keys list.
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    litellm_key_id: Mapped[str] = mapped_column(String(255), unique=True)
    key_alias: Mapped[str] = mapped_column(String(255))
    masked_key: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(default=True)

    user: Mapped[User] = relationship(back_populates="api_keys")


class WorkspaceStatus(enum.StrEnum):
    PROVISIONING = "provisioning"
    RUNNING = "running"
    STOPPED = "stopped"
    TERMINATING = "terminating"
    TERMINATED = "terminated"
    ERROR = "error"


class Workspace(TimestampMixin, Base):
    """A hosted per-user harness environment (a container on epyc)."""

    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("container_name", name="uq_workspace_container"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    container_name: Mapped[str] = mapped_column(String(255))
    harness: Mapped[str] = mapped_column(String(32), default="pi")
    status: Mapped[WorkspaceStatus] = mapped_column(
        Enum(WorkspaceStatus, native_enum=False, length=20),
        default=WorkspaceStatus.PROVISIONING,
    )
    ssh_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Alias of the workspace-scoped LiteLLM key injected into the container (revoked on
    # terminate). The plaintext key is never stored — only injected at create time.
    litellm_key_alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Path to the ciphertext home volume on epyc (gocryptfs); backed up by restic.
    volume_path: Mapped[str] = mapped_column(String(512))
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="workspaces")
    backups: Mapped[list[Backup]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class BackupTrigger(enum.StrEnum):
    NIGHTLY = "nightly"
    MANUAL = "manual"
    ON_TERMINATE = "on_terminate"


class Backup(TimestampMixin, Base):
    """A restic snapshot of a workspace's encrypted home volume."""

    __tablename__ = "backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[str] = mapped_column(String(255))
    trigger: Mapped[BackupTrigger] = mapped_column(
        Enum(BackupTrigger, native_enum=False, length=16)
    )
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    succeeded: Mapped[bool] = mapped_column(default=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="backups")
