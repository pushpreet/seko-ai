"""Structured logging setup (structlog)."""

from __future__ import annotations

import logging
import sys
from typing import TextIO

import structlog


def configure_logging(*, debug: bool = False, stream: TextIO | None = None) -> None:
    """Configure structlog + stdlib logging for console (dev) or JSON (prod).

    The server logs to stdout; operator commands pass ``sys.stderr`` so their stdout stays
    machine-readable.
    """
    level = logging.DEBUG if debug else logging.INFO
    stream = stream or sys.stdout
    logging.basicConfig(format="%(message)s", stream=stream, level=level, force=True)

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]
    processors.append(
        structlog.dev.ConsoleRenderer() if debug else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=stream),
        # Not cached: operator commands reconfigure the stream after module-level loggers exist.
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
