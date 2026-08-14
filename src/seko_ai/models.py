"""ORM models for the seko-ai API-key control plane."""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
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
    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    api_key_identities: Mapped[list[ApiKeyIdentity]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ApiKeyIdentity(TimestampMixin, Base):
    """A user-named logical key whose physical LiteLLM token can be rotated."""

    __tablename__ = "api_key_identities"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "normalized_name", name="uq_api_key_identity_user_normalized_name"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    normalized_name: Mapped[str] = mapped_column(String(64))

    user: Mapped[User] = relationship(back_populates="api_key_identities")
    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="identity")


class ApiKey(TimestampMixin, Base):
    """A LiteLLM virtual key issued to a user. We store the LiteLLM token id + a masked hint.

    The full key value is shown to the user exactly once (on creation) and never persisted.
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    identity_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_key_identities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    litellm_key_id: Mapped[str] = mapped_column(String(255), unique=True)
    key_alias: Mapped[str] = mapped_column(String(255))
    masked_key: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(default=True)

    user: Mapped[User] = relationship(back_populates="api_keys")
    identity: Mapped[ApiKeyIdentity | None] = relationship(back_populates="api_keys")


class ServiceStatus(enum.StrEnum):
    """Availability of the LLM API-key path (LiteLLM -> vLLM)."""

    UP = "up"
    DOWN = "down"
    UNKNOWN = "unknown"


class ServiceState(TimestampMixin, Base):
    """Singleton (id=1) tracking current LLM availability + the maintenance window.

    Written by the ``check-status`` management command (probe + hysteresis) and by the admin
    maintenance toggle; read by the status page/banner. Kept as one row for simplicity.
    """

    __tablename__ = "service_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    current_status: Mapped[ServiceStatus] = mapped_column(
        Enum(ServiceStatus, native_enum=False, length=16),
        default=ServiceStatus.UNKNOWN,
    )
    # When the current status was entered (drives "down since …").
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Rolling count of consecutive failed probes (hysteresis across timer runs).
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)

    # Manual maintenance window: while active, up/down emails are suppressed and the banner
    # shows a "scheduled maintenance" message instead of an outage.
    maintenance_active: Mapped[bool] = mapped_column(default=False)
    maintenance_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    maintenance_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class StatusEvent(TimestampMixin, Base):
    """A recorded up<->down (or maintenance) transition, for the recent-incidents list."""

    __tablename__ = "status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_status: Mapped[ServiceStatus] = mapped_column(
        Enum(ServiceStatus, native_enum=False, length=16)
    )
    to_status: Mapped[ServiceStatus] = mapped_column(
        Enum(ServiceStatus, native_enum=False, length=16)
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    # True if the transition happened during a maintenance window (email suppressed).
    during_maintenance: Mapped[bool] = mapped_column(default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
