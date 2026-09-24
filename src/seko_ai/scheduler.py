"""In-process status scheduler: the application owns its recurring availability probe."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from seko_ai.config import Settings
from seko_ai.logging_config import get_logger

log = get_logger("seko_ai.scheduler")


def run_status_check(settings: Settings) -> str | None:
    """Run one guarded probe (skipped if another runner probed within half an interval)."""
    from seko_ai.db import session_scope
    from seko_ai.management import check_status

    with session_scope() as session:
        return check_status(session, settings, min_interval=settings.status_probe_interval / 2)


async def status_loop(settings: Settings) -> None:
    """Probe forever at ``status_probe_interval``; a failed run never stops the loop."""
    log.info("status_scheduler_started", interval=settings.status_probe_interval)
    while True:
        try:
            await asyncio.to_thread(run_status_check, settings)
        except Exception:  # noqa: BLE001 - keep probing; the next run may succeed
            log.exception("status_check_failed")
        await asyncio.sleep(settings.status_probe_interval)


@contextlib.asynccontextmanager
async def status_scheduler(settings: Settings) -> AsyncIterator[None]:
    """Run the status loop for the application's lifetime when enabled."""
    if not settings.status_scheduler_enabled:
        yield
        return
    task = asyncio.create_task(status_loop(settings))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
