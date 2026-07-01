"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from seko_ai import __version__
from seko_ai.auth import create_oauth, get_current_user
from seko_ai.config import Settings, get_settings
from seko_ai.logging_config import configure_logging

_PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
TEMPLATES.env.globals["version"] = __version__


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return the seko-ai FastAPI application."""
    settings = settings or get_settings()
    configure_logging(debug=settings.debug)

    app = FastAPI(title="seko-ai", version=__version__, debug=settings.debug)
    app.state.settings = settings
    app.state.oauth = create_oauth(settings)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        https_only=settings.base_url.startswith("https"),
    )

    static_dir = _PACKAGE_DIR / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    from seko_ai.routers import auth, health, keys, profile

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(profile.router)
    app.include_router(keys.router)

    @app.exception_handler(HTTPException)
    async def _auth_redirect(request: Request, exc: HTTPException):  # type: ignore[no-untyped-def]
        """Redirect browser requests for protected pages to the login flow."""
        if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(url="/auth/login", status_code=303)
        return HTMLResponse(str(exc.detail), status_code=exc.status_code)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        user = get_current_user(request)
        return TEMPLATES.TemplateResponse(
            request, "index.html", {"user": user, "version": __version__}
        )

    return app


app = create_app()
