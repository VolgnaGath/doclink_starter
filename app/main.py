from functools import partial

from fastapi import FastAPI

from app import __version__
from app.config import Settings
from app.db import check_database
from app.health import router as health_router


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    application = FastAPI(title="DocLink API", version=__version__)
    application.state.settings = app_settings
    application.state.check_database = partial(check_database, app_settings.database_url)
    application.include_router(health_router)
    return application


app = create_app()
