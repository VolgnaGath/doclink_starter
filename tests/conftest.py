from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_password="test-password",
        secret_key="test-secret-key-with-at-least-32-characters",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    application = create_app(settings)
    application.state.check_database = lambda: True
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
