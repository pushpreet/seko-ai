"""Prometheus metrics for seko-ai (scraped by the homelab Prometheus)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from seko_ai.models import User

LOGINS = Counter("seko_logins_total", "Successful sign-ins")
KEYS_ISSUED = Counter("seko_keys_issued_total", "LiteLLM virtual keys issued")

USERS_TOTAL = Gauge("seko_users_total", "Provisioned users")


def refresh_gauges(session: Session) -> None:
    """Refresh point-in-time gauges from the database (called on scrape)."""
    users = session.execute(select(func.count()).select_from(User)).scalar_one()
    USERS_TOTAL.set(users)
