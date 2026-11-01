from functools import partial
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from app import __version__
from app.auth import router as auth_router
from app.config import Settings
from app.db import check_database
from app.documents import router as documents_router
from app.health import router as health_router
from app.web import router as web_router

STATIC_DIR = Path(__file__).parent / "static"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    application = FastAPI(title="DocLink API", version=__version__)
    application.state.settings = app_settings
    application.state.check_database = partial(check_database, app_settings.database_url)
    application.add_middleware(
        SessionMiddleware,
        secret_key=app_settings.secret_key,
        session_cookie="session",
        max_age=SESSION_MAX_AGE_SECONDS,
        same_site="lax",
        https_only=False,
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    application.include_router(auth_router)
    application.include_router(health_router)
    application.include_router(web_router)
    application.include_router(documents_router)
    return application


app = create_app()
