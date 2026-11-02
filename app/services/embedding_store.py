"""Durable vector cache, append-only provenance, and explicit embed queue admission."""

import hashlib
import json
import time
from datetime import UTC, datetime
from functools import lru_cache
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_engine, get_session
from app.models import Document, DocumentEmbedding, DocumentText, Embedding, Job, StageTiming
from app.services.embeddings import BGEEmbedder, EmbeddingError, EncodingConfig, prepare_text
from app.services.extraction import lock_number
from app.services.model_calls import ProcessingError


def encoding_config(settings: Settings) -> EncodingConfig:
    return EncodingConfig(cpu_threads=settings.embedding_cpu_threads)


@lru_cache(maxsize=1)
def _embedder(cache_dir: str, offline: bool, config_json: str) -> BGEEmbedder:
    return BGEEmbedder(
        cache_dir, config=EncodingConfig.model_validate_json(config_json), local_files_only=offline
    )


def embedding_payload(
    session: Session, document_id: UUID, config: EncodingConfig
) -> dict[str, str]:
    source = session.get(DocumentText, document_id)
    if source is None:
        raise EmbeddingError("Текст документа для embed отсутствует")
    prepared = prepare_text(source.text, config)
    return {
        "input_hash": prepared.input_hash,
        "original_text_hash": hashlib.sha256(source.text.encode()).hexdigest(),
        "config": config.model_dump_json(),
    }


def enqueue_embedding(
    session: Session,
    document_id: UUID,
    config: EncodingConfig,
    *,
    retry: bool = False,
) -> UUID:
    doc = session.scalar(select(Document).where(Document.id == document_id).with_for_update())
    if doc is None:
        raise ProcessingError("Документ не найден")
    try:
        payload = embedding_payload(session, document_id, config)
    except EmbeddingError as exc:
        raise ProcessingError(str(exc)) from exc
    jobs = list(
        session.scalars(
            select(Job)
            .where(Job.document_id == document_id, Job.type == "embed")
            .order_by(Job.run_after.desc(), Job.id.desc())
        )
    )
    for job in jobs:
        if job.status in {"queued", "running"}:
            if job.payload != payload:
                raise ProcessingError("У документа есть активный embed с другим входом")
            return job.id
        if job.payload == payload and not retry:
            return job.id
    job = Job(document_id=document_id, type="embed", payload=payload)
    session.add(job)
    session.flush()
    return job.id


def embed_document(
    settings: Settings,
    document_id: UUID,
    config: EncodingConfig,
    *,
    expected_payload: dict[str, str] | None = None,
) -> UUID:
    """Commit result + timing together; lock by actual input across documents/workers.

    Network downloads are a separate admin command. A CPU crash is safe to retry:
    it has no remote side effect; a committed vector is discovered before inference.
    """
    started_at, start = datetime.now(UTC), time.monotonic()
    try:
        with get_session(settings.database_url) as session:
            source = session.get(DocumentText, document_id)
            if source is None:
                raise EmbeddingError("Текст документа для embed отсутствует")
            raw_text = source.text
            prepared = prepare_text(raw_text, config)
            payload = embedding_payload(session, document_id, config)
            if expected_payload is not None and payload != expected_payload:
                raise EmbeddingError("Вход embed изменился после постановки задания")
        with get_engine(settings.database_url).connect() as guard:
            key = lock_number("embed:" + prepared.input_hash)
            guard.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
            guard.commit()
            try:
                with get_session(settings.database_url) as session:
                    cached = session.scalar(
                        select(Embedding).where(Embedding.input_hash == prepared.input_hash)
                    )
                    cache_hit = cached is not None
                    if cached is None:
                        engine = _embedder(
                            settings.embedding_cache_dir,
                            settings.embedding_local_files_only,
                            config.model_dump_json(),
                        )
                        result = engine.encode(prepared)
                        cached = Embedding(
                            input_hash=result.input_hash,
                            text_hash=result.text_hash,
                            model=config.model,
                            revision=config.revision,
                            snapshot=json.loads(result.snapshot),
                            vector=list(result.vector),
                            tokens=result.tokens,
                            chunks=result.chunks,
                            truncated=result.truncated,
                            duration_ms=result.duration_ms,
                        )
                        session.add(cached)
                        session.flush()
                    existing = session.scalar(
                        select(DocumentEmbedding).where(
                            DocumentEmbedding.document_id == document_id,
                            DocumentEmbedding.embedding_id == cached.id,
                            DocumentEmbedding.original_text_hash == payload["original_text_hash"],
                        )
                    )
                    if existing is None:
                        existing = DocumentEmbedding(
                            document_id=document_id,
                            embedding_id=cached.id,
                            original_text_hash=payload["original_text_hash"],
                            original_chars=len(raw_text),
                            prepared_chars=len(prepared.text),
                            cache_hit=cache_hit,
                        )
                        session.add(existing)
                        session.flush()
                    session.add(
                        StageTiming(
                            document_id=document_id,
                            stage="embed",
                            ok=True,
                            started_at=started_at,
                            duration_ms=int((time.monotonic() - start) * 1000),
                        )
                    )
                    session.commit()
                    return existing.id
            finally:
                guard.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                guard.commit()
    except Exception as exc:
        error = (
            str(exc) if isinstance(exc, EmbeddingError) else f"Ошибка embed: {type(exc).__name__}"
        )
        with get_session(settings.database_url) as session:
            session.add(
                StageTiming(
                    document_id=document_id,
                    stage="embed",
                    ok=False,
                    error=error,
                    started_at=started_at,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            )
            session.commit()
        raise ProcessingError(error) from exc
