"""Console entrypoint: ``seko-ai [serve]`` runs the server; other subcommands operate it."""

from __future__ import annotations

import sys

import uvicorn

from seko_ai.config import get_settings


def serve() -> None:
    """Run seko-ai with uvicorn."""
    settings = get_settings()
    uvicorn.run(
        "seko_ai.app:app",
        host="0.0.0.0",  # noqa: S104 - container-internal; a reverse proxy is the edge
        port=8080,
        reload=settings.debug,
    )


def main() -> None:
    """Dispatch ``seko-ai`` subcommands (default: ``serve``)."""
    args = sys.argv[1:]
    if not args or args[0] == "serve":
        serve()
        return
    from seko_ai.management import run

    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
