"""LLM availability probing + status state machine.

The ``check-status`` management command probes the API-key path (LiteLLM -> vLLM) every
~60s (systemd timer). This module owns the pure logic: classify a probe, apply hysteresis
across runs (state persists in ``service_state``), record transitions, and — outside a
maintenance window — email all users on a real up<->down change.

Everything here is synchronous (tiny workload) so the CLI and the sync status routes share
one code path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from seko_ai.config import Settings
from seko_ai.logging_config import get_logger
from seko_ai.models import ServiceState, ServiceStatus, StatusEvent
from seko_ai.services import notifications

log = get_logger("seko_ai.status")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single health probe."""

    ok: bool
    detail: str


def probe(settings: Settings, *, client: httpx.Client | None = None) -> ProbeResult:
    """Probe the LLM API-key path once and classify it up/down.

    ``litellm_health`` mode (default) asks LiteLLM's ``/health`` (authenticated) which pings
    the backing vLLM deployment — "up" means at least one healthy served model. ``http``
    mode just expects a 200 from ``effective_status_probe_url``.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=settings.status_probe_timeout)
    try:
        if settings.status_probe_mode == "http":
            return _probe_http(client, settings.effective_status_probe_url)
        return _probe_litellm_health(client, settings)
    finally:
        if owns_client:
            client.close()


def _probe_http(client: httpx.Client, url: str) -> ProbeResult:
    try:
        resp = client.get(url)
    except httpx.HTTPError as exc:
        return ProbeResult(ok=False, detail=f"request failed: {exc}")
    if resp.status_code == 200:
        return ProbeResult(ok=True, detail=f"200 {url}")
    return ProbeResult(ok=False, detail=f"{resp.status_code} {url}")


def _probe_litellm_health(client: httpx.Client, settings: Settings) -> ProbeResult:
    url = f"{settings.litellm_base_url.rstrip('/')}/health"
    headers = {"Authorization": f"Bearer {settings.litellm_master_key}"}
    try:
        resp = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return ProbeResult(ok=False, detail=f"request failed: {exc}")
    if resp.status_code != 200:
        return ProbeResult(ok=False, detail=f"litellm /health {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        return ProbeResult(ok=False, detail="litellm /health: non-JSON body")
    healthy = data.get("healthy_endpoints") or []
    unhealthy = data.get("unhealthy_endpoints") or []
    if healthy:
        return ProbeResult(ok=True, detail=f"healthy={len(healthy)} unhealthy={len(unhealthy)}")
    return ProbeResult(ok=False, detail=f"no healthy endpoints (unhealthy={len(unhealthy)})")


# --- State persistence -------------------------------------------------------------------


def get_or_create_state(session: Session) -> ServiceState:
    """Return the singleton service_state row, creating it (UNKNOWN) on first use."""
    state = session.get(ServiceState, 1)
    if state is None:
        state = ServiceState(id=1, current_status=ServiceStatus.UNKNOWN, since=_now())
        session.add(state)
        session.flush()
    return state


def recent_events(session: Session, *, limit: int = 10) -> list[StatusEvent]:
    """Return the most recent status transitions (newest first)."""
    stmt = select(StatusEvent).order_by(StatusEvent.at.desc()).limit(limit)
    return list(session.execute(stmt).scalars().all())


def record_probe(
    session: Session,
    settings: Settings,
    result: ProbeResult,
    *,
    now: datetime | None = None,
    notify: bool = True,
) -> ServiceState:
    """Fold a probe result into the persisted state, emailing on a real transition.

    Hysteresis: ``status_fail_threshold`` consecutive failures flips to DOWN; a single
    success flips to UP. Emails are suppressed while a maintenance window is active.
    """
    now = now or _now()
    state = get_or_create_state(session)
    _maybe_expire_maintenance(session, settings, state, now=now, notify=notify)

    state.last_checked_at = now
    state.last_detail = result.detail

    if result.ok:
        state.consecutive_failures = 0
        new_status = ServiceStatus.UP
    else:
        state.consecutive_failures += 1
        if state.consecutive_failures >= settings.status_fail_threshold:
            new_status = ServiceStatus.DOWN
        else:
            # Not enough failures yet — hold the current status (don't flap).
            new_status = state.current_status

    if new_status != state.current_status and new_status in (
        ServiceStatus.UP,
        ServiceStatus.DOWN,
    ):
        _transition(session, settings, state, new_status, now=now, notify=notify)
    return state


def _transition(
    session: Session,
    settings: Settings,
    state: ServiceState,
    new_status: ServiceStatus,
    *,
    now: datetime,
    notify: bool,
) -> None:
    previous = state.current_status
    session.add(
        StatusEvent(
            from_status=previous,
            to_status=new_status,
            at=now,
            during_maintenance=state.maintenance_active,
            note=state.last_detail,
        )
    )
    state.current_status = new_status
    state.since = now
    log.info(
        "status_transition",
        **{"from": previous.value, "to": new_status.value},
        maintenance=state.maintenance_active,
    )

    if not notify or state.maintenance_active:
        return
    if new_status == ServiceStatus.DOWN:
        notifications.notify_down(session, settings, detail=state.last_detail)
    elif new_status == ServiceStatus.UP and previous == ServiceStatus.DOWN:
        # Only announce "restored" after a real outage (not on first-boot UNKNOWN -> UP).
        notifications.notify_restored(session, settings)


# --- Maintenance window ------------------------------------------------------------------


def start_maintenance(
    session: Session,
    settings: Settings,
    *,
    message: str | None = None,
    notify: bool = True,
) -> ServiceState:
    """Begin a manual maintenance window (suppresses up/down emails)."""
    state = get_or_create_state(session)
    if state.maintenance_active:
        return state
    state.maintenance_active = True
    state.maintenance_started_at = _now()
    state.maintenance_message = (message or "").strip() or None
    log.info("maintenance_started", message=state.maintenance_message)
    if notify and settings.status_notify_on_maintenance:
        notifications.notify_maintenance_start(session, settings, message=state.maintenance_message)
    return state


def end_maintenance(
    session: Session,
    settings: Settings,
    *,
    notify: bool = True,
) -> ServiceState:
    """End the maintenance window and resume normal alerting."""
    state = get_or_create_state(session)
    if not state.maintenance_active:
        return state
    state.maintenance_active = False
    state.maintenance_started_at = None
    state.maintenance_message = None
    state.consecutive_failures = 0
    log.info("maintenance_ended")
    if notify and settings.status_notify_on_maintenance:
        notifications.notify_maintenance_end(session, settings)
    return state


def _maybe_expire_maintenance(
    session: Session,
    settings: Settings,
    state: ServiceState,
    *,
    now: datetime,
    notify: bool,
) -> None:
    """Auto-clear a forgotten maintenance window past ``maintenance_max_hours``."""
    if not state.maintenance_active or settings.maintenance_max_hours <= 0:
        return
    started = state.maintenance_started_at
    if started is None:
        return
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if now - started >= timedelta(hours=settings.maintenance_max_hours):
        log.info("maintenance_auto_expired", hours=settings.maintenance_max_hours)
        end_maintenance(session, settings, notify=notify)
