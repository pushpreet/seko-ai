"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from seko_ai import __version__
from seko_ai.config import Settings, get_settings
from seko_ai.logging_config import configure_logging

_PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return the seko-ai FastAPI application."""
    settings = settings or get_settings()
    configure_logging(debug=settings.debug)

    app = FastAPI(title="seko-ai", version=__version__, debug=settings.debug)
    app.state.settings = settings
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, https_only=True)

    static_dir = _PACKAGE_DIR / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    from seko_ai.routers import health

    app.include_router(health.router)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        user = request.session.get("user")
        return TEMPLATES.TemplateResponse(
            request, "index.html", {"user": user, "version": __version__}
        )

    return app


app = create_app()
