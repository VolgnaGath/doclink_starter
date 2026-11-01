"""PostgreSQL queue; retries are explicit, generation retry policy belongs to model_calls."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_engine, get_session
from app.models import CorpusVersion, Document, Extraction, Job, LLMCall, ModelConfig, Prompt
from app.services.extraction import configuration, extract_document, lock_number, save_result
from app.services.model_calls import ProcessingError, fingerprint


def frozen_protocol(
    session: Session, document: Document, snapshots: list[dict[str, Any]]
) -> str | None:
    """Selection-layer guard only. Neither split nor protocol metadata enters model input."""
    if document.corpus_version_id is None or document.split != "test":
        return None
    version = session.get(CorpusVersion, document.corpus_version_id, with_for_update=True)
    protocols = {
        key: dict(value) for key, value in version.report.get("extraction_protocols", {}).items()
    }
    requested = {fingerprint(s) for s in snapshots}
    matches = [
        key
        for key, value in protocols.items()
        if requested <= {fingerprint(s) for s in value.get("models", [])}
    ]
    if not matches:
        raise ProcessingError(
            "До обработки test зафиксируйте конфигурацию через make freeze-extraction"
        )
    key = matches[0]
    protocols[key]["reused_test"] = protocols[key].get("reused_test", False) or any(
        p.get("test_started") for other, p in protocols.items() if other != key
    )
    protocols[key]["test_started"] = True
    version.report = {**version.report, "extraction_protocols": protocols}
    return key


def enqueue(
    session: Session,
    document: Document,
    models: list[ModelConfig] | None = None,
    *,
    retry: bool = False,
    text_only: bool = False,
) -> list[UUID]:
    session.flush()
    if document.corpus_version_id is not None and document.split == "test":
        session.get(CorpusVersion, document.corpus_version_id, with_for_update=True)
    # Serialize enqueue even where nullable model IDs would evade a unique index.
    document = session.scalar(select(Document).where(Document.id == document.id).with_for_update())
    if text_only:
        existing = session.scalar(
            select(Job).where(
                Job.document_id == document.id,
                Job.type == "text",
                Job.status.in_(["queued", "running"]),
            )
        )
        if existing:
            return [existing.id]
        job = Job(type="text", document_id=document.id)
        session.add(job)
        session.flush()
        return [job.id]
    if models is None:
        models = list(session.scalars(select(ModelConfig).where(ModelConfig.active)))
    prompt = session.scalar(
        select(Prompt)
        .where(Prompt.name == "extract")
        .order_by(Prompt.created_at.desc(), Prompt.version.desc())
        .limit(1)
    )
    if not prompt or not models:
        return []
    protocol_hash = frozen_protocol(session, document, [configuration(m, prompt) for m in models])
    defaults = [m for m in models if m.default_card]
    if document.card_model_config_id is None and len(defaults) == 1:
        document.card_model_config_id = defaults[0].id
    ids = []
    for model in models:
        # Resume does not automatically requeue terminal errors or successful results.
        previous = session.scalar(
            select(Job)
            .where(
                Job.document_id == document.id,
                Job.model_config_id == model.id,
                Job.type == "extract",
            )
            .order_by(Job.run_after.desc())
            .limit(1)
        )
        current = session.scalar(
            select(Extraction.id).where(
                Extraction.document_id == document.id,
                Extraction.model_config_id == model.id,
                Extraction.is_current,
            )
        )
        if not retry and (previous or current):
            if previous:
                ids.append(previous.id)
            continue
        statement = insert(Job).values(
            type="extract",
            document_id=document.id,
            model_config_id=model.id,
            payload={
                "prompt_id": str(prompt.id),
                "snapshot": configuration(model, prompt),
                "protocol_hash": protocol_hash,
            },
        )
        statement = statement.on_conflict_do_nothing(
            index_elements=[Job.type, Job.document_id, Job.model_config_id],
            index_where=text("status IN ('queued','running')"),
        ).returning(Job.id)
        if job_id := session.scalar(statement):
            ids.append(job_id)
    return ids


def recover(session: Session, guard: Connection) -> None:
    """A live worker owns a session advisory lock. A released lock proves worker loss."""
    for job in session.scalars(
        select(Job).where(Job.status == "running").with_for_update(skip_locked=True)
    ):
        key = lock_number("job:" + str(job.id))
        if not guard.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}):
            continue
        try:
            pending = list(
                session.scalars(
                    select(LLMCall).where(LLMCall.job_id == job.id, LLMCall.status == "started")
                )
            )
            if pending:
                for call in pending:
                    call.status, call.error = "uncertain", "Worker остановлен во время запроса"
                job.status, job.error = "error", "Неопределённый исход; требуется сверка"
                save_result(
                    session,
                    job.document_id,
                    job.model_config_id,
                    job.payload["snapshot"],
                    None,
                    pending[-1].id,
                    [],
                    False,
                    job.error,
                )
            else:
                result = session.scalar(
                    select(Extraction).where(
                        Extraction.document_id == job.document_id,
                        Extraction.model_config_id == job.model_config_id,
                        Extraction.is_current,
                        Extraction.created_at >= job.locked_at,
                    )
                )
                calls = list(session.scalars(select(LLMCall).where(LLMCall.job_id == job.id)))
                if result:
                    job.status, job.error = ("done" if result.valid else "error"), result.error
                elif calls and not any(c.status == "success" for c in calls):
                    job.status, job.error = "error", "Worker остановлен после неуспешной попытки"
                    save_result(
                        session,
                        job.document_id,
                        job.model_config_id,
                        job.payload["snapshot"],
                        None,
                        calls[-1].id,
                        [],
                        False,
                        job.error,
                    )
                else:
                    # Not sent, or durable successful response reusable through the cache.
                    job.status, job.locked_at = "queued", None
        finally:
            guard.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
    session.commit()


def work_once(
    settings: Settings, document_ids: list[UUID] | None = None, model_ids: list[UUID] | None = None
) -> bool:
    with (
        get_engine(settings.database_url).connect() as guard,
        get_session(settings.database_url) as session,
    ):
        recover(session, guard)
        filters = [Job.status == "queued", Job.run_after <= datetime.now(UTC)]
        if document_ids is not None:
            filters.append(Job.document_id.in_(document_ids))
        if model_ids is not None:
            filters.append(Job.model_config_id.in_(model_ids))
        job = session.scalar(
            select(Job)
            .where(*filters)
            .order_by(Job.run_after, Job.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return False
        key = lock_number("job:" + str(job.id))
        guard.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        guard.commit()
        job.status, job.locked_at = "running", datetime.now(UTC)
        job.attempts += 1
        session.commit()
        error = None
        try:
            if job.type == "text":
                from app.services.text_extraction import process_text

                doc = session.get(Document, job.document_id)
                if doc.corpus_version_id is not None and doc.split == "test":
                    session.get(CorpusVersion, doc.corpus_version_id, with_for_update=True)
                process_text(session, doc, settings)
                if doc.status == "error":
                    raise ProcessingError("Ошибка получения текста")
                enqueue(session, doc, retry=True)
                session.commit()
            else:
                extract_document(
                    settings,
                    job.document_id,
                    job.model_config_id,
                    UUID(job.payload["prompt_id"]),
                    job.payload["snapshot"],
                    job.id,
                )
        except ProcessingError as exc:
            error = str(exc)
        except Exception as exc:
            session.rollback()
            error = f"Ошибка worker: {type(exc).__name__}"
        finally:
            job.status, job.error = ("error" if error else "done"), error
            session.commit()
            guard.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            guard.commit()
        return True
