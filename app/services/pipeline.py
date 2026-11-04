"""Reconcile durable pipelines using short transactions; inference stays in jobs.

No in-memory callbacks: the next worker iteration repairs a crash between any
stage commit and admission of its successor. Errors require an explicit retry.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Client,
    Document,
    Extraction,
    Job,
    LinkingResult,
    ModelConfig,
    OperationalConfig,
    ProcessingPipeline,
    Prompt,
)
from app.services.embeddings import EncodingConfig
from app.services.extraction import configuration
from app.services.linking.types import LinkingConfig
from app.services.model_calls import ProcessingError


def install_configuration(
    session: Session,
    model: ModelConfig,
    prompt: Prompt,
    config: LinkingConfig,
    encoding: EncodingConfig,
    *,
    protocol_hash: str | None = None,
) -> dict[str, Any]:
    if config.method != "combined":
        raise ProcessingError("Оперативный метод должен быть combined")
    snapshot = {
        "model_id": str(model.id),
        "prompt_id": str(prompt.id),
        "extraction": configuration(model, prompt),
        "config": config.model_dump(mode="json"),
        "encoding": encoding.model_dump(mode="json"),
        "origin_protocol_hash": protocol_hash,
    }
    row = session.get(OperationalConfig, 1)
    if row is None:
        session.add(OperationalConfig(id=1, snapshot=snapshot))
    else:
        row.snapshot = snapshot
    session.flush()
    return snapshot


def dependencies(
    session: Session, source_id: UUID, model_id: UUID
) -> tuple[bool, list[str], list[dict[str, Any]]]:
    """All possible contracts, never selected by filename, gold, split or folder."""
    pending, errors, origins = False, [], []
    source = session.execute(
        select(Document.id, Document.client_id, Document.corpus_version_id, Document.date).where(
            Document.id == source_id
        )
    ).one()
    for doc in session.execute(
        select(
            Document.id,
            Document.client_id,
            Document.corpus_version_id,
            Document.doc_kind,
            Document.date,
        )
        .where(
            Document.client_id == source.client_id,
            Document.corpus_version_id == source.corpus_version_id,
            Document.id != source.id,
        )
        .order_by(Document.id)
    ):
        pipe = session.get(ProcessingPipeline, doc.id)
        extraction = session.scalar(
            select(Extraction).where(
                Extraction.document_id == doc.id,
                Extraction.model_config_id == model_id,
                Extraction.is_current,
            )
        )
        preparing = bool(pipe and pipe.stage in {"text", "extract"} and not pipe.error)
        # During a re-extraction the old kind is not proof that this is not a contract.
        active = session.scalar(
            select(Job.id)
            .where(
                Job.document_id == doc.id,
                Job.type.in_(["text", "extract"]),
                Job.status.in_(["queued", "running"]),
            )
            .limit(1)
        )
        if preparing or active:
            pending = True
        failed = extraction is None or not extraction.valid
        possible = preparing or bool(active) or failed or doc.doc_kind in {None, "договор"}
        if not possible:
            continue
        # Only a successful, known contract date can establish temporal exclusion.
        if not failed and doc.doc_kind == "договор" and doc.date and source.date:
            if doc.date > source.date:
                continue
        origins.append(
            {
                "id": str(doc.id),
                "kind": doc.doc_kind,
                "date": doc.date.isoformat() if doc.date else None,
                "extraction_id": str(extraction.id) if extraction else None,
                "valid": bool(extraction and extraction.valid),
                "preparing": preparing or bool(active),
            }
        )
        if failed or doc.doc_kind is None or doc.date is None:
            errors.append(f"{doc.id}: потенциальный договор не подготовлен (вид/дата/извлечение)")
    return pending, errors, origins


def _extraction_job(session: Session, doc: Document, pipe: ProcessingPipeline) -> UUID:
    snap = pipe.snapshot
    model_id = UUID(snap["model_id"])
    # Retain the established preserved-error and uncertain-dispatch protections.
    current = session.scalar(
        select(Extraction).where(
            Extraction.document_id == doc.id,
            Extraction.model_config_id == model_id,
            Extraction.is_current,
        )
    )
    if current and current.preserved_error:
        raise ProcessingError("Сохранённая ошибка модели защищена от повторного извлечения")
    active = session.scalar(
        select(Job).where(
            Job.document_id == doc.id,
            Job.model_config_id == model_id,
            Job.type == "extract",
            Job.status.in_(["queued", "running"]),
        )
    )
    if active:
        if active.payload.get("snapshot") != snap["extraction"]:
            raise ProcessingError("Активное извлечение использует другую конфигурацию")
        return active.id
    job = Job(
        type="extract",
        document_id=doc.id,
        model_config_id=model_id,
        payload={
            "prompt_id": snap["prompt_id"],
            "snapshot": snap["extraction"],
            "protocol_hash": None,
        },
    )
    session.add(job)
    session.flush()
    return job.id


def _record_failure(session: Session, doc: Document, pipe: ProcessingPipeline) -> None:
    from app.services.linking_store import linking_payload

    snap = pipe.snapshot
    payload = linking_payload(
        session,
        doc.id,
        UUID(snap["model_id"]),
        LinkingConfig.model_validate(snap["config"]),
        EncodingConfig.model_validate(snap["encoding"]),
    )
    result = LinkingResult(
        source_id=doc.id,
        model_config_id=UUID(snap["model_id"]),
        input_hash=payload["input_hash"],
        method="combined",
        method_version=snap["config"]["version"],
        config=snap["config"],
        input_snapshot=payload["data"],
        provenance={**payload["provenance"], "pipeline_failure_stage": pipe.stage},
        outcome="error",
        reasons=["pipeline_stage_error"],
        recommendations=[],
        error=pipe.error,
    )
    session.add(result)
    session.flush()
    pipe.result_id, doc.status = result.id, "error"


def advance(session: Session, pipe: ProcessingPipeline) -> None:
    from app.services.embedding_store import enqueue_embedding
    from app.services.jobs import enqueue
    from app.services.linking_store import enqueue_linking, linking_payload

    doc = session.get(Document, pipe.document_id)
    snap = pipe.snapshot
    config = LinkingConfig.model_validate(snap["config"])
    encoding = EncodingConfig.model_validate(snap["encoding"])
    model_id = UUID(snap["model_id"])
    if pipe.error:
        return
    job = session.get(Job, pipe.job_id) if pipe.job_id else None
    if job:
        if job.status in {"queued", "running"}:
            return
        if job.status == "error" and pipe.stage != "link":
            pipe.error = job.error or "Ошибка этапа обработки"
            _record_failure(session, doc, pipe)
            return
        pipe.job_id = None
        pipe.stage = {
            "text": "extract",
            "extract": "prepare",
            "embed": "prepare",
            "link": "done",
        }.get(pipe.stage, pipe.stage)
    if pipe.stage == "text":
        pipe.job_id = enqueue(session, doc, text_only=True)[0]
    elif pipe.stage == "extract":
        pipe.job_id = _extraction_job(session, doc, pipe)
    else:
        if doc.doc_kind == "договор":
            pipe.stage = "done"
            return
        # Unknown source requisites are review, independent of pool preparation.
        if doc.doc_kind is not None and doc.date is not None:
            pending, errors, _ = dependencies(session, doc.id, model_id)
            if pending:
                return
            if not errors and config.use_semantic:
                targets = list(
                    session.scalars(
                        select(Document.id).where(
                            Document.client_id == doc.client_id,
                            Document.corpus_version_id == doc.corpus_version_id,
                            Document.doc_kind == "договор",
                            Document.date <= doc.date,
                            Document.id != doc.id,
                        )
                    )
                )
                if targets:
                    for target in [doc.id, *targets]:
                        embed_job = session.get(Job, enqueue_embedding(session, target, encoding))
                        if embed_job.status in {"queued", "running"}:
                            return
                        # Failed vectors stay explicit in linking_payload; no auto retry.
        payload = linking_payload(session, doc.id, model_id, config, encoding)
        latest = session.get(LinkingResult, pipe.result_id) if pipe.result_id else None
        if pipe.stage == "done" and latest and latest.input_hash == payload["input_hash"]:
            return
        pipe.job_id = enqueue_linking(
            session, doc.id, model_id, config, encoding, retry=pipe.stage == "prepare"
        )
        pipe.stage = "link"
        link_job = session.get(Job, pipe.job_id)
        if link_job.status in {"done", "error"}:
            result = session.scalar(
                select(LinkingResult)
                .where(
                    LinkingResult.source_id == doc.id,
                    LinkingResult.input_hash == payload["input_hash"],
                )
                .order_by(LinkingResult.created_at.desc())
                .limit(1)
            )
            if result:
                pipe.result_id = result.id
                pipe.stage, pipe.job_id = "done", None
                doc.status = "error" if result.outcome == "error" else "review"


def reconcile(session: Session) -> None:
    # One short queue coordinator transaction, never held across inference.
    from sqlalchemy import text

    if not session.scalar(text("SELECT pg_try_advisory_xact_lock(50803003)")):
        return
    clients = list(
        session.scalars(
            select(Document.client_id)
            .join(ProcessingPipeline, ProcessingPipeline.document_id == Document.id)
            .distinct()
            .order_by(Document.client_id)
        )
    )
    for client_id in clients:
        session.execute(select(Client.id).where(Client.id == client_id).with_for_update())
        for pipe in session.scalars(
            select(ProcessingPipeline)
            .join(Document, Document.id == ProcessingPipeline.document_id)
            .where(Document.client_id == client_id)
            .order_by(ProcessingPipeline.document_id)
            .with_for_update(of=ProcessingPipeline)
        ):
            try:
                advance(session, pipe)
            except ProcessingError as exc:
                pipe.error = str(exc)
                _record_failure(session, session.get(Document, pipe.document_id), pipe)
    session.flush()


def retry_pipeline(session: Session, document_id: UUID, *, mode: str = "failed") -> None:
    doc = session.get(Document, document_id)
    if doc is None or doc.source == "corpus":
        raise ProcessingError("Рабочий документ не найден")
    session.execute(select(Client.id).where(Client.id == doc.client_id).with_for_update())
    pipe = session.get(ProcessingPipeline, document_id, with_for_update=True)
    if pipe is None:
        raise ProcessingError("Документ не включён в оперативный конвейер")
    if mode not in {"failed", "search", "extract"}:
        raise ProcessingError("Неизвестный режим повтора")
    if pipe.job_id and session.get(Job, pipe.job_id).status in {"queued", "running"}:
        raise ProcessingError("Обработка уже выполняется")
    from app.models import WorkingCopy

    origin = session.get(WorkingCopy, document_id)
    if origin:
        # Explicit working reruns must not be attributed to the original measurement.
        origin.source_result_current = False
    if mode == "failed" and pipe.error and pipe.stage in {"text", "extract"} and pipe.job_id:
        job = session.get(Job, pipe.job_id)
        # Preserve the same physical-attempt journal and successful initial response
        # when resuming, e.g. after a budget stop before the correcting request.
        job.status, job.error, job.locked_at = "queued", None, None
        pipe.error, pipe.result_id = None, None
        return
    if mode == "extract":
        pipe.stage = "extract"
    elif mode == "search" or pipe.stage in {"link", "done"}:
        pipe.stage = "prepare"
    if mode == "failed":
        from app.services.embedding_store import enqueue_embedding

        encoding = EncodingConfig.model_validate(pipe.snapshot["encoding"])
        last_embed = session.scalar(
            select(Job)
            .where(Job.document_id == document_id, Job.type == "embed")
            .order_by(Job.run_after.desc(), Job.id.desc())
            .limit(1)
        )
        if last_embed and last_embed.status == "error":
            enqueue_embedding(session, document_id, encoding, retry=True)
    pipe.error, pipe.job_id, pipe.result_id = None, None, None
