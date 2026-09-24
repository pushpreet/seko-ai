"""Operator commands: status checks, maintenance windows, and database backups.

These are seko-ai's public operator interface, exposed through the ``seko-ai`` console
script (``python -m seko_ai.management`` remains an alias). Their arguments and ``--json``
output are part of the documented configuration contract (``docs/configuration.md``).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from seko_ai.config import Settings, get_database_settings, get_settings
from seko_ai.logging_config import get_logger

log = get_logger("seko_ai.management")

OWNER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def check_status(
    session: Session,
    settings: Settings,
    *,
    min_interval: float | None = None,
) -> str | None:
    """Probe the LLM API-key path and fold the result into the persisted status.

    Returns the resulting status string, or ``None`` when ``min_interval`` is set and another
    runner recorded a probe more recently than that (so concurrent schedulers never double
    count failures). Emails on a real up<->down transition unless a maintenance window is
    active.
    """
    from seko_ai.services import status as status_service

    result = status_service.probe(settings)
    status_service.lock_state(session)
    state = status_service.get_or_create_state(session)
    if min_interval is not None and state.last_checked_at is not None:
        last = state.last_checked_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if datetime.now(UTC) - last < timedelta(seconds=min_interval):
            log.info("check_status_skipped", reason="recent probe by another runner")
            return None
    state = status_service.record_probe(session, settings, result)
    log.info(
        "check_status_done",
        status=state.current_status.value,
        ok=result.ok,
        failures=state.consecutive_failures,
        maintenance=state.maintenance_active,
    )
    return state.current_status.value


def maintenance(
    session: Session,
    settings: Settings,
    action: str,
    message: str | None = None,
    *,
    owner: str | None = None,
    notify: bool = True,
) -> dict[str, Any]:
    """Start, end, or report the maintenance window and return its state as a dict.

    Without ``owner`` this is the manual window: ``start`` opens (or takes over) the
    window and ``end`` closes it unconditionally. With ``owner`` it is a lease: ``start``
    joins or opens a leased window (never taking over a manual one) and ``end`` releases
    only that owner's lease; the window closes when the last owner releases it.
    """
    from seko_ai.services import status as status_service

    status_service.lock_state(session)
    extra: dict[str, Any] = {}
    if action == "start" and owner:
        lease = status_service.acquire_maintenance(
            session, settings, owner=owner, message=message, notify=notify
        )
        state = lease.state
        extra = {"owned": lease.owned}
    elif action == "end" and owner:
        lease = status_service.release_maintenance(session, settings, owner=owner, notify=notify)
        state = lease.state
        extra = {"owned": lease.owned, "window_ended": lease.window_ended}
    elif action == "start":
        state = status_service.start_maintenance(session, settings, message=message, notify=notify)
    elif action == "end":
        state = status_service.end_maintenance(session, settings, notify=notify)
    elif action == "status":
        state = status_service.get_or_create_state(session)
    else:
        raise ValueError(f"unknown maintenance action: {action}")
    started = state.maintenance_started_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    result: dict[str, Any] = {
        "active": state.maintenance_active,
        "message": state.maintenance_message,
        "owners": status_service.maintenance_owners(state),
        "started_at": started.isoformat() if started else None,
        "status": state.current_status.value,
        **extra,
    }
    log.info("maintenance_state", action=action, owner=owner, **result)
    return result


def backup(destination: Path, database_url: str | None = None) -> Path:
    """Write a consistent online copy of the SQLite database to ``destination``.

    Uses SQLite's backup API, so it is safe while the application is serving. The copy is
    written beside the destination and renamed into place, so a reader never sees a partial
    file.
    """
    url = make_url(database_url or get_database_settings().database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("backup supports file-backed SQLite databases only")
    destination = destination.resolve()
    temporary = destination.with_name(f".{destination.name}.tmp")
    source = sqlite3.connect(f"file:{url.database}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    temporary.replace(destination)
    log.info("backup_written", path=str(destination))
    return destination


def _owner(value: str) -> str:
    if not OWNER_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "owner must be 1-64 lowercase letters, digits or hyphens, starting alphanumeric"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    """Return the parser for the ``seko-ai`` console script."""
    parser = argparse.ArgumentParser(prog="seko-ai", description="seko-ai control plane")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="run the web application (default)")
    commands.add_parser("check-status", help="probe the LLM once and record the result")

    maint = commands.add_parser("maintenance", help="manage the maintenance window")
    maint.add_argument("action", choices=["start", "end", "status"])
    maint.add_argument("--message", help="banner/email message when starting a window")
    maint.add_argument(
        "--owner",
        type=_owner,
        help="lease owner token; the window ends only when every owner releases it",
    )
    maint.add_argument(
        "--no-notify", action="store_true", help="do not send maintenance start/end emails"
    )
    maint.add_argument("--json", action="store_true", help="print the resulting state as JSON")

    backup_cmd = commands.add_parser("backup", help="write an online SQLite backup")
    backup_cmd.add_argument("destination", type=Path)
    return parser


def run(argv: list[str]) -> int:
    """Run an operator command (everything except ``serve``)."""
    import sys

    from seko_ai.db import session_scope
    from seko_ai.logging_config import configure_logging

    args = build_parser().parse_args(argv)
    configure_logging(stream=sys.stderr)
    if args.command == "backup":
        print(backup(args.destination))
        return 0
    settings = get_settings()
    if args.command == "check-status":
        with session_scope() as session:
            print(check_status(session, settings))
        return 0
    if args.command == "maintenance":
        with session_scope() as session:
            result = maintenance(
                session,
                settings,
                args.action,
                args.message,
                owner=args.owner,
                notify=not args.no_notify,
            )
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            print("active" if result["active"] else "inactive")
        return 0
    build_parser().print_usage()
    return 2


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI wiring
    import sys

    return run(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
