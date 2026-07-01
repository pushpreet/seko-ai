"""Console entrypoint: run the ASGI server."""

from __future__ import annotations

import uvicorn

from seko_ai.config import get_settings


def main() -> None:
    """Run seko-ai with uvicorn."""
    settings = get_settings()
    uvicorn.run(
        "seko_ai.app:app",
        host="0.0.0.0",  # noqa: S104 - container-internal; edge is Caddy
        port=8080,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
