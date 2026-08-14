"""Service-status checks and maintenance-window management."""

from __future__ import annotations

from sqlalchemy.orm import Session

from seko_ai.config import Settings, get_settings
from seko_ai.logging_config import get_logger

log = get_logger("seko_ai.management")


def check_status(session: Session, settings: Settings) -> str:
    """Probe the LLM API-key path and fold the result into the persisted status.

    Returns the resulting status string. Emails on a real up<->down transition (unless a
    maintenance window is active). Driven by the ``seko-ai-maint@check-status`` timer.
    """
    from seko_ai.services import status as status_service

    result = status_service.probe(settings)
    state = status_service.record_probe(session, settings, result)
    log.info(
        "check_status_done",
        status=state.current_status.value,
        ok=result.ok,
        failures=state.consecutive_failures,
        maintenance=state.maintenance_active,
    )
    return state.current_status.value


def maintenance(session: Session, settings: Settings, action: str, message: str | None) -> str:
    """Start/end/report the maintenance window (headless, for the ``just`` recipes)."""
    from seko_ai.services import status as status_service

    if action == "start":
        state = status_service.start_maintenance(session, settings, message=message)
    elif action == "end":
        state = status_service.end_maintenance(session, settings)
    elif action == "status":
        state = status_service.get_or_create_state(session)
    else:  # pragma: no cover - guarded by the CLI parser
        raise ValueError(f"unknown maintenance action: {action}")
    active = "active" if state.maintenance_active else "inactive"
    log.info(
        "maintenance_state",
        action=action,
        maintenance=active,
        status=state.current_status.value,
    )
    return active


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI wiring
    import sys

    from seko_ai.db import session_scope

    args = argv if argv is not None else sys.argv[1:]
    commands = {"check-status", "maintenance"}
    if not args or args[0] not in commands:
        print(
            "usage: python -m seko_ai.management "
            "[check-status|maintenance <start|end|status> [--message TEXT]]"
        )
        return 2
    settings = get_settings()
    command = args[0]

    if command == "maintenance":
        if len(args) < 2 or args[1] not in {"start", "end", "status"}:
            print(
                "usage: python -m seko_ai.management "
                "maintenance <start|end|status> [--message TEXT]"
            )
            return 2
        action = args[1]
        message: str | None = None
        if "--message" in args:
            idx = args.index("--message")
            message = args[idx + 1] if idx + 1 < len(args) else None
        with session_scope() as session:
            maintenance(session, settings, action, message)
        return 0

    if command == "check-status":
        with session_scope() as session:
            check_status(session, settings)
        return 0

    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
