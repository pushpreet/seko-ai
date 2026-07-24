"""Email notifications via the Resend HTTPS API.

Used by the status monitor (down/up transitions) and the maintenance toggle. Mirrors the
homelab's off-site watchdog (``external/azure-uptime``) which also sends via Resend's HTTPS
API. Reuses the same Resend account; the ``from`` address must be on a verified domain.

Sends are best-effort and synchronous (tiny recipient list). A missing API key disables
delivery (logged, not raised) so status tracking still works in dev/tests.
"""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from seko_ai.config import Settings
from seko_ai.logging_config import get_logger
from seko_ai.models import User

log = get_logger("seko_ai.notifications")

RESEND_ENDPOINT = "https://api.resend.com/emails"


def recipient_emails(session: Session) -> list[str]:
    """Return the distinct, non-empty emails of all known seko users."""
    rows = session.execute(select(User.email).where(User.email.is_not(None))).scalars().all()
    seen: dict[str, None] = {}
    for email in rows:
        addr = (email or "").strip()
        if addr:
            seen.setdefault(addr, None)
    return list(seen)


def send_email(
    settings: Settings,
    *,
    to: list[str],
    subject: str,
    text: str,
    client: httpx.Client | None = None,
) -> int:
    """Send ``text`` to each recipient individually. Returns the number delivered.

    Recipients are emailed one-at-a-time so nobody sees anyone else's address, and one bad
    address doesn't block the rest. Delivery is skipped (and logged) if Resend isn't
    configured.
    """
    if not settings.resend_api_key:
        log.warning("email_disabled_no_resend_key", subject=subject, recipients=len(to))
        return 0
    if not to:
        log.info("email_no_recipients", subject=subject)
        return 0

    owns_client = client is None
    client = client or httpx.Client(timeout=15.0)
    headers = {"Authorization": f"Bearer {settings.resend_api_key}"}
    sent = 0
    try:
        for addr in to:
            payload = {
                "from": settings.alert_email_from,
                "to": [addr],
                "subject": subject,
                "text": text,
            }
            try:
                resp = client.post(RESEND_ENDPOINT, json=payload, headers=headers)
                if resp.status_code >= 400:
                    log.error("resend_failed", status=resp.status_code, body=resp.text[:300])
                    continue
                sent += 1
            except httpx.HTTPError as exc:
                log.error("resend_exception", error=str(exc), recipient=addr)
    finally:
        if owns_client:
            client.close()
    log.info("email_sent", subject=subject, delivered=sent, recipients=len(to))
    return sent


# --- Message templates -------------------------------------------------------------------


def _dashboard(settings: Settings) -> str:
    return settings.base_url.rstrip("/") + "/status"


def notify_down(session: Session, settings: Settings, *, detail: str | None = None) -> int:
    """Email users that the LLM API is unavailable."""
    body = (
        "Heads up — the shared LLM API is currently unavailable, so your API key "
        "requests will fail until it's back.\n\n"
        f"Live status: {_dashboard(settings)}\n\n"
        "You'll get another email when service is restored."
    )
    return send_email(
        settings,
        to=recipient_emails(session),
        subject="[seko] LLM API is DOWN",
        text=body,
    )


def notify_restored(session: Session, settings: Settings) -> int:
    """Email users that the LLM API is back up."""
    body = (
        "Good news — the shared LLM API is back up and serving requests again.\n\n"
        f"Live status: {_dashboard(settings)}"
    )
    return send_email(
        settings,
        to=recipient_emails(session),
        subject="[seko] LLM API is back UP",
        text=body,
    )


def notify_maintenance_start(
    session: Session, settings: Settings, *, message: str | None = None
) -> int:
    """Email users that a planned maintenance window has begun."""
    extra = f"\n\nNote: {message}" if message else ""
    body = (
        "The shared LLM API is entering a planned maintenance window. It may be "
        "intermittently unavailable while work is in progress; this is expected and you "
        f"won't get repeated outage emails during the window.{extra}\n\n"
        f"Live status: {_dashboard(settings)}"
    )
    return send_email(
        settings,
        to=recipient_emails(session),
        subject="[seko] Planned LLM maintenance started",
        text=body,
    )


def notify_maintenance_end(session: Session, settings: Settings) -> int:
    """Email users that the maintenance window has ended."""
    body = (
        "The planned LLM maintenance window has ended and normal availability monitoring "
        "has resumed.\n\n"
        f"Live status: {_dashboard(settings)}"
    )
    return send_email(
        settings,
        to=recipient_emails(session),
        subject="[seko] LLM maintenance complete",
        text=body,
    )
