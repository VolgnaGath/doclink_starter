"""Shared explicit processing admission for HTML and REST."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import DocumentText, ModelConfig, OperationalConfig, ProcessingPipeline, WorkingCopy
from app.services.jobs import enqueue
from app.services.model_calls import ProcessingError
from app.services.pipeline import retry_pipeline
from app.services.review import lock_document


def request_processing(
    session: Session, document_id: UUID, *, mode: str = "failed", model_id: UUID | None = None
) -> list[UUID]:
    doc = lock_document(session, document_id)
    if model_id is not None:
        if mode != "extract":
            raise ProcessingError("Выбор модели допустим только для повторного извлечения")
        model = session.get(ModelConfig, model_id)
        if model is None or session.get(DocumentText, document_id) is None:
            raise ProcessingError("Нет модели или текста")
        origin = session.get(WorkingCopy, document_id)
        if origin:
            origin.source_result_current = False
        return enqueue(session, doc, [model], retry=True)
    pipe = session.get(ProcessingPipeline, document_id)
    if pipe is None:
        config = session.get(OperationalConfig, 1)
        if config is None:
            raise ProcessingError("Оперативная конфигурация не установлена")
        from uuid import uuid4

        pipe = ProcessingPipeline(
            document_id=doc.id,
            batch_id=uuid4(),
            snapshot=config.snapshot,
            stage="extract" if session.get(DocumentText, doc.id) else "text",
        )
        doc.card_model_config_id = UUID(config.snapshot["model_id"])
        session.add(pipe)
        session.flush()
    else:
        retry_pipeline(session, document_id, mode=mode)
    return [pipe.job_id] if pipe.job_id else []
