"""Infrastructure fixtures are not BGE quality measurements. Real smoke is opt-in."""

import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_engine, get_session
from app.models import (
    Client,
    Document,
    DocumentEmbedding,
    DocumentText,
    Embedding,
    Job,
    StageTiming,
)
from app.services import embedding_store
from app.services.embedding_store import embed_document, encoding_config, enqueue_embedding
from app.services.embeddings import EmbeddingError, EmbeddingResult, EncodingConfig, PreparedText
from app.services.jobs import recover, work_once
from app.services.model_calls import ProcessingError

SAMPLE = "Договор поставки офисной мебели: столы и кресла для рабочих мест."


@pytest.fixture
def live(settings: Settings) -> Iterator[Settings]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Изолированная PostgreSQL: make verify-test")
    settings.database_url = url

    def clean() -> None:
        with get_engine(url).begin() as connection:
            connection.execute(text("TRUNCATE clients, corpus_versions, embeddings CASCADE"))

    clean()
    yield settings
    clean()


def setup(settings: Settings, content: str = SAMPLE) -> UUID:
    with get_session(settings.database_url) as session:
        client = Client(name="Инфраструктурный fixture")
        session.add(client)
        session.flush()
        doc = Document(
            client_id=client.id,
            source="upload",
            filename="GOLD_SECRET_PATH.txt",
            sha256="unused",
            storage_path="unused",
            mime="text/plain",
            status="text",
            is_anonymized=True,
            gold={"expected": "SECRET_LABEL", "deal_id": "SECRET_DEAL"},
        )
        session.add(doc)
        session.flush()
        session.add(
            DocumentText(document_id=doc.id, text=content, chars=len(content), pages=1, ocr_pages=0)
        )
        session.commit()
        return doc.id


class ScriptedEncoder:
    """One-hot infrastructure vector: never used for a semantic quality assertion."""

    def __init__(self) -> None:
        self.inputs: list[PreparedText] = []
        self.lock = Lock()
        self.fail = False

    def encode(self, prepared: PreparedText) -> EmbeddingResult:
        with self.lock:
            self.inputs.append(prepared)
        time.sleep(0.03)
        if self.fail:
            raise EmbeddingError("fixture embed failed")
        return EmbeddingResult(
            prepared.input_hash,
            prepared.text_hash,
            prepared.snapshot,
            (1.0,) + (0.0,) * 1023,
            prepared.original_chars,
            len(prepared.text),
            10,
            1,
            False,
            1,
        )


@pytest.fixture
def encoder(monkeypatch: pytest.MonkeyPatch) -> ScriptedEncoder:
    encoder = ScriptedEncoder()
    monkeypatch.setattr(embedding_store, "_embedder", lambda *args: encoder)
    return encoder


def test_cache_across_documents_history_and_no_labels(
    live: Settings, encoder: ScriptedEncoder
) -> None:
    a, b = setup(live), setup(live, " " + SAMPLE + "\n")
    config = encoding_config(live)
    first = embed_document(live, a, config)
    assert embed_document(live, a, config) == first
    second = embed_document(live, b, config)
    assert second != first
    assert len(encoder.inputs) == 1
    assert "SECRET" not in str(encoder.inputs)
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(Embedding)) == 1
        assert session.scalar(select(func.count()).select_from(DocumentEmbedding)) == 2
        assert session.get(DocumentEmbedding, second).cache_hit
        assert session.get(DocumentEmbedding, first).original_chars == len(SAMPLE)
        assert session.get(DocumentEmbedding, second).original_chars == len(SAMPLE) + 2
        session.get(DocumentText, a).text += " Новое условие."
        session.commit()
    third = embed_document(live, a, config)
    assert third != first
    fourth = embed_document(live, a, EncodingConfig(chunk_tokens=256))
    assert fourth != third
    with get_session(live.database_url) as session:
        assert session.get(DocumentEmbedding, first) is not None
        assert session.scalar(select(func.count()).select_from(Embedding)) == 3
        assert session.scalar(select(func.count()).select_from(StageTiming)) == 5


def test_concurrent_queue_and_encoding_are_idempotent(
    live: Settings, encoder: ScriptedEncoder
) -> None:
    a, b = setup(live), setup(live)
    config = encoding_config(live)

    def enqueue(id: UUID) -> UUID:
        with get_session(live.database_url) as session:
            job = enqueue_embedding(session, id, config)
            session.commit()
            return job

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(enqueue, [a, a]))
    assert jobs[0] == jobs[1]
    enqueue(b)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(pool.map(lambda _: work_once(live, job_types=["embed"]), range(2)))
    assert len(encoder.inputs) == 1
    with get_session(live.database_url) as session:
        assert {job.status for job in session.scalars(select(Job))} == {"done"}
        assert session.scalar(select(func.count()).select_from(Embedding)) == 1


def test_failed_embed_is_logged_not_cached_and_retry_is_explicit(
    live: Settings,
    encoder: ScriptedEncoder,
) -> None:
    doc = setup(live)
    config = encoding_config(live)
    encoder.fail = True
    with get_session(live.database_url) as session:
        first = enqueue_embedding(session, doc, config)
        session.commit()
    assert work_once(live, job_types=["embed"])
    with get_session(live.database_url) as session:
        assert session.get(Job, first).status == "error"
        assert session.scalar(select(func.count()).select_from(Embedding)) == 0
        timing = session.scalar(select(StageTiming))
        assert timing.stage == "embed" and not timing.ok
        assert enqueue_embedding(session, doc, config) == first
        retry = enqueue_embedding(session, doc, config, retry=True)
        assert retry != first
        session.commit()
    encoder.fail = False
    assert work_once(live, job_types=["embed"])
    assert len(encoder.inputs) == 2
    with get_session(live.database_url) as session:
        assert session.get(Job, retry).status == "done"
        assert session.get(Job, first).status == "error"


def test_worker_recovery_reuses_committed_vector(live: Settings, encoder: ScriptedEncoder) -> None:
    doc = setup(live)
    config = encoding_config(live)
    with get_session(live.database_url) as session:
        id = enqueue_embedding(session, doc, config)
        job = session.get(Job, id)
        job.status, job.locked_at = "running", datetime.now(UTC)
        session.commit()
    # Simulate stopping after durable result but before setting job=done.
    embed_document(live, doc, config)
    with (
        get_engine(live.database_url).connect() as guard,
        get_session(live.database_url) as session,
    ):
        recover(session, guard)
        assert session.get(Job, id).status == "queued"
    assert work_once(live, job_types=["embed"])
    assert len(encoder.inputs) == 1


def test_changed_input_does_not_silently_replace_queued_snapshot(
    live: Settings,
    encoder: ScriptedEncoder,
) -> None:
    doc = setup(live)
    config = encoding_config(live)
    with get_session(live.database_url) as session:
        id = enqueue_embedding(session, doc, config)
        session.get(DocumentText, doc).text = "Другой текст"
        session.commit()
    with (
        get_session(live.database_url) as session,
        pytest.raises(ProcessingError, match="другим входом"),
    ):
        enqueue_embedding(session, doc, config)
    assert work_once(live, job_types=["embed"])
    assert not encoder.inputs
    with get_session(live.database_url) as session:
        assert session.get(Job, id).status == "error"
        assert session.scalar(select(func.count()).select_from(Embedding)) == 0


def test_vector_and_provenance_rollback_together(live: Settings, encoder: ScriptedEncoder) -> None:
    doc = setup(live)

    def fail_provenance(session: Session, *args: Any) -> None:
        if any(isinstance(row, DocumentEmbedding) for row in session.new):
            raise RuntimeError("fixture failure after vector insert")

    event.listen(Session, "before_flush", fail_provenance)
    try:
        with pytest.raises(ProcessingError, match="RuntimeError"):
            embed_document(live, doc, encoding_config(live))
    finally:
        event.remove(Session, "before_flush", fail_provenance)
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(Embedding)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentEmbedding)) == 0
        assert not session.scalar(select(StageTiming)).ok


@pytest.mark.skipif(
    os.environ.get("EMBEDDING_SMOKE_ENABLED") != "1", reason="make verify-embedding"
)
def test_real_embedding_cache(live: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = setup(live), setup(live)
    config = encoding_config(live)
    with get_session(live.database_url) as session:
        first = enqueue_embedding(session, a, config)
        session.commit()
    assert work_once(live, job_types=["embed"])
    with get_session(live.database_url) as session:
        job = session.get(Job, first)
        assert job.status == "done", job.error
        embedding = session.scalar(select(Embedding))
        assert len(embedding.vector) == 1024
        assert sum(float(v) ** 2 for v in embedding.vector) == pytest.approx(1, abs=1e-5)
        distance = session.scalar(select(Embedding.vector.cosine_distance(embedding.vector)))
        assert distance == pytest.approx(0, abs=1e-6)

    def no_inference(*args: Any) -> None:
        raise AssertionError("Cache hit must not initialize an encoder")

    monkeypatch.setattr(embedding_store, "_embedder", no_inference)
    with get_session(live.database_url) as session:
        second = enqueue_embedding(session, b, config)
        repeat = enqueue_embedding(session, a, config, retry=True)
        session.commit()
    assert work_once(live, job_types=["embed"])
    assert work_once(live, job_types=["embed"])
    with get_session(live.database_url) as session:
        assert session.get(Job, second).status == session.get(Job, repeat).status == "done"
        assert session.scalar(select(func.count()).select_from(Embedding)) == 1
        assert session.scalar(select(func.count()).select_from(DocumentEmbedding)) == 2
        assert session.scalar(select(func.count()).select_from(StageTiming)) == 3
