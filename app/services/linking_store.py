"""Allowlisted DB adapter, immutable inputs, atomic outcomes and explicit link jobs.

No evaluation imports or label-based candidate selection. The queue freezes the whole
eligible pool, not top-k; a changed input is an error requiring a new explicit enqueue.
"""

import hashlib
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_engine, get_session
from app.models import (
    Document,
    DocumentEmbedding,
    DocumentText,
    Embedding,
    Extraction,
    Job,
    LinkSuggestion,
    ProcessingPipeline,
    StageTiming,
)
from app.models import (
    LinkingResult as StoredResult,
)
from app.services.card_edits import overrides_for
from app.services.embeddings import EmbeddingError, EncodingConfig, cosine, prepare_text
from app.services.extraction import lock_number
from app.services.extraction_schema import check_evidence, regex_extract
from app.services.linking import link
from app.services.linking.types import (
    Card,
    ContractReference,
    Fields,
    LinkingConfig,
    LinkingInput,
    LinkingResult,
    Similarity,
)
from app.services.linking.types import (
    Document as EngineDocument,
)
from app.services.model_calls import ProcessingError, fingerprint

ADAPTER_VERSION = "linking-store-v1"
# Explicit SQL projection: gold, split, paths, deal_id and filenames are never loaded.
CARD_COLUMNS = (
    Document.id,
    Document.client_id,
    Document.corpus_version_id,
    Document.doc_kind,
    Document.date,
    Document.card_model_config_id,
    Document.card_extraction_id,
)


def _current(session: Session, document_id: UUID, model_id: UUID | None) -> Extraction | None:
    if model_id is None:
        return None
    return session.scalar(
        select(Extraction).where(
            Extraction.document_id == document_id,
            Extraction.model_config_id == model_id,
            Extraction.is_current,
        )
    )


def _fields(extraction: Extraction | None) -> Fields:
    if extraction is None:
        return Fields(error="Извлечение выбранной модели отсутствует")
    if not extraction.valid:
        return Fields(error=extraction.error or "Ошибка извлечения выбранной модели")
    verified = {c["field"] for c in extraction.evidence_checks if c.get("ok") is True}
    return Fields(
        number=extraction.number,
        inn=extraction.cp_inn,
        name=extraction.cp_name,
        amount=extraction.amount,
        references=tuple(
            ContractReference(
                number=ref.get("number"),
                date=datetime.strptime(ref["date"], "%d.%m.%Y").date() if ref.get("date") else None,
                verified=f"contract_refs.{i}" in verified,
            )
            for i, ref in enumerate(extraction.contract_refs)
        ),
        number_verified="number" in verified,
        counterparty_verified="counterparty" in verified,
        amount_verified="amount" in verified,
    )


def linking_payload(
    session: Session,
    document_id: UUID,
    model_id: UUID | None,
    config: LinkingConfig,
    encoding: EncodingConfig,
    *,
    no_llm: bool = False,
) -> dict[str, Any]:
    """Build a frozen, label-free input and provenance from a consistent DB snapshot."""
    if config.method == "combined" and model_id is None and not no_llm:
        raise ProcessingError("Для combined выберите модель извлечения")
    source = session.execute(select(*CARD_COLUMNS).where(Document.id == document_id)).first()
    if source is None:
        raise ProcessingError("Документ не найден")
    regex_rows: dict[UUID, tuple[str, Any]] = {}

    def regex_card(row: Any) -> Any:
        raw = session.scalar(select(DocumentText.text).where(DocumentText.document_id == row.id))
        if raw is None:
            raise ProcessingError("Текст для regex-карточки отсутствует")
        parsed = regex_extract(raw)
        regex_rows[row.id] = (raw, parsed)
        return SimpleNamespace(
            **{
                **dict(row._mapping),
                "doc_kind": parsed.doc_kind,
                "date": datetime.strptime(parsed.date, "%d.%m.%Y").date() if parsed.date else None,
                "card_model_config_id": None,
                "card_extraction_id": None,
            }
        )

    pool = []
    if no_llm:
        source = regex_card(source)
        rows = session.execute(
            select(*CARD_COLUMNS)
            .where(
                Document.id != document_id,
                Document.client_id == source.client_id,
                Document.corpus_version_id == source.corpus_version_id,
            )
            .order_by(Document.id)
        ).all()
        parsed_pool = [regex_card(row) for row in rows]
        pool = [
            row
            for row in parsed_pool
            if row.doc_kind == "договор"
            and row.date is not None
            and source.date is not None
            and row.date <= source.date
        ]
    elif source.date is not None and source.doc_kind is not None:
        pool = list(
            session.execute(
                select(*CARD_COLUMNS)
                .where(
                    Document.id != document_id,
                    Document.client_id == source.client_id,
                    Document.corpus_version_id == source.corpus_version_id,
                    Document.doc_kind == "договор",
                    Document.date <= source.date,
                )
                .order_by(Document.id)
            )
        )
    provenance: dict[str, Any] = {}
    vectors: dict[str, tuple[float, ...]] = {}
    semantic_errors: list[str] = []
    preparation_error = None
    pipeline = None if no_llm else session.get(ProcessingPipeline, document_id)
    dependency_origins = None
    if pipeline and source.doc_kind is not None and source.date is not None:
        from app.services.pipeline import dependencies

        pending, errors, dependency_origins = dependencies(session, document_id, model_id)
        if pending:
            errors.append("Подготовка карточек клиента ещё выполняется")
        semantic_errors.extend(errors)
        preparation_error = "; ".join(errors) or None
    need_vectors = config.method in {"semantic", "combined"} and config.use_semantic and bool(pool)
    need_vectors = need_vectors and not semantic_errors

    def adapt(row: Any) -> EngineDocument:
        id = str(row.id)
        card_current = _current(session, row.id, row.card_model_config_id)
        card_error = None
        if row.card_model_config_id is not None and (
            card_current is None or not card_current.valid
        ):
            card_error = "Ошибка или отсутствие извлечения общей карточки"
        origin: dict[str, Any] = {
            "card": {
                "model_config_id": str(row.card_model_config_id)
                if row.card_model_config_id
                else None,
                "extraction_id": str(row.card_extraction_id) if row.card_extraction_id else None,
                "current_extraction_id": str(card_current.id) if card_current else None,
            },
        }
        fields = Fields()
        if config.method == "combined" and not no_llm:
            extraction = _current(session, row.id, model_id)
            fields = _fields(extraction)
            origin["extraction"] = (
                {
                    "id": str(extraction.id),
                    "llm_call_id": str(extraction.llm_call_id) if extraction.llm_call_id else None,
                    "source_extraction_id": str(extraction.source_extraction_id)
                    if extraction.source_extraction_id
                    else None,
                    "input_hash": extraction.input_hash,
                    "preserved_error": extraction.preserved_error,
                    "snapshot": extraction.snapshot,
                    "valid": extraction.valid,
                }
                if extraction
                else None
            )
            overrides = overrides_for(session, row.id)
            if overrides:
                origin["manual_overrides"] = overrides
                updates: dict[str, Any] = {}
                for field, engine_field in {
                    "number": "number",
                    "counterparty_inn": "inn",
                    "counterparty_name": "name",
                    "amount": "amount",
                }.items():
                    if field in overrides:
                        updates[engine_field] = overrides[field]
                if "number" in overrides:
                    updates["number_verified"] = False
                if {"counterparty_name", "counterparty_inn"} & overrides.keys():
                    updates["counterparty_verified"] = False
                if "amount" in overrides:
                    updates["amount_verified"] = False
                if "contract_refs" in overrides:
                    updates["references"] = tuple(
                        ContractReference(
                            number=ref.get("number"),
                            date=datetime.strptime(ref["date"], "%d.%m.%Y").date()
                            if ref.get("date")
                            else None,
                            verified=False,
                        )
                        for ref in (overrides["contract_refs"] or [])
                    )
                fields = Fields.model_validate({**fields.model_dump(), **updates})
        if no_llm:
            raw, parsed = regex_rows[row.id]
            origin = {
                "regex": {"version": "regex-card-v1", "fields": parsed.model_dump(mode="json")}
            }
            verified = {c["field"] for c in check_evidence(parsed, [raw]) if c.get("ok")}
            fields = Fields(
                number=parsed.number,
                inn=parsed.counterparty_inn,
                name=parsed.counterparty_name,
                amount=parsed.amount,
                number_verified="number" in verified,
                counterparty_verified="counterparty" in verified,
                amount_verified="amount" in verified,
                references=tuple(
                    ContractReference(
                        number=ref.number,
                        date=datetime.strptime(ref.date, "%d.%m.%Y").date() if ref.date else None,
                        verified=f"contract_refs.{i}" in verified,
                    )
                    for i, ref in enumerate(parsed.contract_refs)
                ),
            )
        else:
            raw = session.scalar(
                select(DocumentText.text).where(DocumentText.document_id == row.id)
            )
        origin["text_hash"] = hashlib.sha256(raw.encode()).hexdigest() if raw is not None else None
        if config.method == "rules" and not raw:
            card_error = "Текст для rules отсутствует"
        if need_vectors:
            try:
                if raw is None:
                    raise EmbeddingError("Текст смыслового входа отсутствует")
                prepared = prepare_text(raw, encoding)
                vector_row = session.execute(
                    select(DocumentEmbedding.id, Embedding)
                    .join(Embedding, Embedding.id == DocumentEmbedding.embedding_id)
                    .where(
                        DocumentEmbedding.document_id == row.id,
                        DocumentEmbedding.original_text_hash == origin["text_hash"],
                        Embedding.input_hash == prepared.input_hash,
                    )
                ).first()
                if vector_row is None:
                    raise EmbeddingError("Актуальный вектор требуемой конфигурации отсутствует")
                provenance_id, embedding = vector_row
                origin["embedding"] = {
                    "document_embedding_id": str(provenance_id),
                    "id": str(embedding.id),
                    "input_hash": embedding.input_hash,
                    "snapshot": embedding.snapshot,
                }
                vectors[id] = tuple(float(v) for v in embedding.vector)
            except EmbeddingError as exc:
                origin["embedding_error"] = str(exc)
                semantic_errors.append(f"{id}: {exc}")
        provenance[id] = origin
        return EngineDocument(
            card=Card(
                document_id=id,
                client_id=str(row.client_id),
                corpus_version_id=str(row.corpus_version_id) if row.corpus_version_id else None,
                kind=row.doc_kind,
                date=row.date,
                error=card_error,
            ),
            fields=fields,
            text=raw or "" if config.method == "rules" else "",
        )

    source_input = adapt(source)
    if preparation_error:
        source_input = source_input.model_copy(
            update={"card": source_input.card.model_copy(update={"error": preparation_error})}
        )
    candidates = tuple(adapt(row) for row in pool)
    similarities = []
    if need_vectors and not semantic_errors:
        try:
            similarities = [
                Similarity(
                    target_id=doc.card.document_id,
                    cosine=cosine(vectors[str(document_id)], vectors[doc.card.document_id]),
                )
                for doc in candidates
            ]
        except EmbeddingError as exc:
            semantic_errors.append(str(exc))
    data = LinkingInput(
        source=source_input,
        candidates=candidates,
        similarities=tuple(similarities),
        semantic_error="; ".join(semantic_errors) or None,
    )
    payload = {
        "adapter_version": ADAPTER_VERSION,
        "model_id": str(model_id) if model_id else None,
        "config": config.model_dump(mode="json"),
        "encoding": encoding.model_dump(mode="json"),
        "data": data.model_dump(mode="json"),
        "provenance": {
            "adapter_version": ADAPTER_VERSION,
            "encoding": encoding.model_dump(mode="json"),
            "documents": provenance,
        },
    }
    if pipeline:
        payload["provenance"]["operational_configuration"] = pipeline.snapshot
        payload["provenance"]["dependencies"] = dependency_origins
    if no_llm:
        payload["no_llm"] = True
        payload["provenance"]["card_source"] = "regex"
    from app.services.working_identity import copy_origin

    origin = copy_origin(session, document_id)
    if origin:
        payload["provenance"]["working_copy"] = origin
    return {**payload, "input_hash": fingerprint(payload)}


def enqueue_linking(
    session: Session,
    document_id: UUID,
    model_id: UUID | None,
    config: LinkingConfig,
    encoding: EncodingConfig,
    *,
    retry: bool = False,
) -> UUID:
    # Serializes nullable model IDs too; the existing active-job index alone cannot.
    session.execute(select(Document.id).where(Document.id == document_id).with_for_update())
    payload = linking_payload(session, document_id, model_id, config, encoding)
    jobs = session.scalars(
        select(Job)
        .where(
            Job.type == "link",
            Job.document_id == document_id,
            Job.model_config_id == model_id,
        )
        .order_by(Job.run_after.desc(), Job.id.desc())
    )
    for job in jobs:
        if job.status in {"queued", "running"}:
            if job.payload != payload:
                raise ProcessingError("У документа есть активный link с другим входом")
            return job.id
        if job.payload == payload and not retry:
            return job.id
    job = Job(type="link", document_id=document_id, model_config_id=model_id, payload=payload)
    session.add(job)
    session.flush()
    return job.id


def _save(
    session: Session,
    document_id: UUID,
    payload: dict[str, Any],
    result: LinkingResult,
    job_id: UUID | None,
    error: str | None,
) -> StoredResult:
    stored = StoredResult(
        source_id=document_id,
        model_config_id=UUID(payload["model_id"]) if payload["model_id"] else None,
        job_id=job_id,
        input_hash=payload["input_hash"],
        method=result.config.method,
        method_version=result.config.version,
        config=payload["config"],
        input_snapshot=payload["data"],
        provenance=payload["provenance"],
        outcome=result.outcome,
        reasons=list(result.reasons),
        recommendations=list(result.recommendations),
        error=error,
    )
    session.add(stored)
    session.flush()
    for candidate in result.candidates:
        values = candidate.model_dump(mode="json")
        values["target_id"] = UUID(values["target_id"])
        session.add(LinkSuggestion(result_id=stored.id, source_id=document_id, **values))
    session.flush()
    return stored


def link_document(
    settings: Settings,
    document_id: UUID,
    payload: dict[str, Any],
    *,
    job_id: UUID | None = None,
) -> UUID:
    """Atomic outcome/ranking/timing. Errors are durable history, never success-cache hits."""
    started_at, start = datetime.now(UTC), time.monotonic()
    config = LinkingConfig.model_validate(payload["config"])
    encoding = EncodingConfig.model_validate(payload["encoding"])
    error = None
    result_id = None
    with get_engine(settings.database_url).connect() as guard:
        key = lock_number("link:" + payload["input_hash"])
        guard.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        guard.commit()
        try:
            with get_session(settings.database_url) as session:
                # All adapter reads see one committed snapshot, including the entire pool.
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                try:
                    # Covers a crash after result commit, including an error result.
                    previous = (
                        session.scalar(select(StoredResult).where(StoredResult.job_id == job_id))
                        if job_id
                        else None
                    )
                    if previous is None:
                        current = linking_payload(
                            session,
                            document_id,
                            UUID(payload["model_id"]) if payload["model_id"] else None,
                            config,
                            encoding,
                            no_llm=payload.get("no_llm", False),
                        )
                        if current != payload:
                            raise ProcessingError("Вход link изменился после постановки задания")
                        previous = session.scalar(
                            select(StoredResult).where(
                                StoredResult.input_hash == payload["input_hash"],
                                StoredResult.outcome != "error",
                            )
                        )
                    if previous is not None:
                        stored = previous
                        error = stored.error
                    else:
                        result = link(LinkingInput.model_validate(payload["data"]), config)
                        error = (
                            (
                                payload["data"]["source"]["card"].get("error")
                                or payload["data"].get("semantic_error")
                                or "; ".join(result.reasons)
                            )
                            if result.outcome == "error"
                            else None
                        )
                        stored = _save(session, document_id, payload, result, job_id, error)
                    result_id = stored.id
                except Exception as exc:
                    session.rollback()
                    error = (
                        str(exc)
                        if isinstance(exc, ProcessingError)
                        else f"Ошибка link: {type(exc).__name__}"
                    )
                    result = LinkingResult(
                        outcome="error", reasons=("link_processing_error",), config=config
                    )
                    result_id = _save(session, document_id, payload, result, job_id, error).id
                session.add(
                    StageTiming(
                        document_id=document_id,
                        stage="link",
                        ok=error is None,
                        error=error,
                        started_at=started_at,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                )
                session.commit()
        finally:
            guard.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            guard.commit()
    if error:
        raise ProcessingError(error)
    return result_id
