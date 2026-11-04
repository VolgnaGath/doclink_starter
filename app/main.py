from functools import partial
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from app import __version__
from app.api import router as api_router
from app.api_errors import install_errors
from app.auth import router as auth_router
from app.config import Settings
from app.db import check_database
from app.documents import router as documents_router
from app.effect_web import router as effect_router
from app.experiment_api import router as experiment_api_router
from app.experiment_web import router as experiment_web_router
from app.health import router as health_router
from app.integration_web import router as integration_router
from app.model_web import router as models_router
from app.review_web import router as review_router
from app.web import router as web_router
from app.working_web import router as working_router

STATIC_DIR = Path(__file__).parent / "static"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    application = FastAPI(title="DocLink API", version=__version__)
    application.state.settings = app_settings
    install_errors(application)
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
    application.include_router(models_router)
    application.include_router(review_router)
    application.include_router(api_router)
    application.include_router(working_router)
    application.include_router(experiment_api_router)
    application.include_router(experiment_web_router)
    application.include_router(integration_router)
    application.include_router(effect_router)
    return application


app = create_app()
