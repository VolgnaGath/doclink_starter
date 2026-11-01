import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base
from app.models import Client, Document, DocumentText, StageTiming
from app.services.corpus_import import import_corpus


def test_migrated_schema_and_constraints(db_session: Session) -> None:
    connection = db_session.connection()
    schema = inspect(connection)
    assert schema.get_pk_constraint("document_texts")["constrained_columns"] == ["document_id"]
    assert any(
        index["column_names"] == ["client_id", "registered_at"]
        for index in schema.get_indexes("documents")
    )
    assert any(
        c["column_names"] == ["corpus_version_id", "corpus_doc_id"]
        for c in schema.get_unique_constraints("documents")
    )
    assert {tuple(fk["constrained_columns"]) for fk in schema.get_foreign_keys("documents")} == {
        ("client_id",),
        ("corpus_version_id",),
        ("counterparty_id",),
        ("card_model_config_id",),
        ("card_extraction_id",),
    }
    assert "run_id" not in {column["name"] for column in schema.get_columns("stage_timings")}
    client = Client(name="Проверка ограничений")
    db_session.add(client)
    db_session.flush()
    for column, bad_value in (
        ("source", "bad"),
        ("split", "bad"),
        ("status", "bad"),
        ("doc_kind", "чек"),
    ):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            document = Document(
                client_id=client.id,
                source="upload",
                status="new",
                filename="file.txt",
                mime="text/plain",
                sha256="hash",
                storage_path="unused",
            )
            setattr(document, column, bad_value)
            db_session.add(document)
            db_session.flush()


def test_concurrent_import_same_snapshot(settings: Settings, tmp_path: Path) -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Изолированная PostgreSQL: make verify-test")
    schema_name = f"verify_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema_name}"))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema_name},public"})
    try:
        # Public contains the migrated tables; checkfirst would see those through search_path.
        Base.metadata.create_all(engine, checkfirst=False)
        settings.files_dir = str(tmp_path / "blobs")
        corpus = Path(__file__).resolve().parents[1] / "corpus"
        barrier = Barrier(2)

        def run() -> dict[str, object]:
            with Session(engine) as session:
                barrier.wait(timeout=10)
                return import_corpus(session, corpus, settings)

        with ThreadPoolExecutor(max_workers=2) as executor:
            reports = list(executor.map(lambda _: run(), range(2)))
        assert reports[0]["version_id"] == reports[1]["version_id"]
        assert sorted(report["reused"] for report in reports) == [False, True]
        with Session(engine) as session:
            for model in (Document, DocumentText, StageTiming):
                assert session.scalar(select(func.count()).select_from(model)) == 233
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema_name} CASCADE"))
        admin.dispose()
