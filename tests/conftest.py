import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_password="test-password",
        secret_key="test-secret-key-with-at-least-32-characters",
        yandex_api_key="",
        yandex_folder_id="",
        gigachat_credentials="",
        gigachat_ca_bundle="",
        llm_provider="mock",
        paid_calls_enabled=False,
        budget_rub=0,
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


@pytest.fixture
def db_session(tmp_path: Path, settings: Settings) -> Iterator[Session]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Изолированная PostgreSQL: make verify-test")
    settings.database_url = url
    settings.files_dir = str(tmp_path / "blobs")
    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            text(
                "TRUNCATE stage_timings, anon_maps, anon_identities, extractions, llm_calls, jobs, "
                "model_configs, prompts, document_texts, documents, "
                "counterparties, corpus_versions, clients CASCADE"
            )
        )
        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()
    engine.dispose()
