"""Copy working inputs, never gold. Originals, outcomes and calls remain immutable."""

import copy
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import (
    Client,
    CorpusVersion,
    Document,
    DocumentEmbedding,
    DocumentText,
    Embedding,
    Extraction,
    LinkingResult,
    LLMCall,
    OperationalConfig,
    ProcessingPipeline,
    WorkingCopy,
    WorkingSet,
)
from app.services.card_edits import extraction_values
from app.services.embeddings import EncodingConfig, prepare_text
from app.services.extraction import prepared_request, save_result
from app.services.extraction_schema import ExtractedFields
from app.services.linking.types import LinkingConfig
from app.services.linking_store import linking_payload
from app.services.model_calls import fingerprint, request_hash
from app.services.review import ReviewError
from app.services.working_identity import cache_client_id, working_revision


class WorkingSetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    corpus_version_id: UUID
    document_ids: list[UUID] = Field(min_length=1, max_length=1000)
    result_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    idempotency_key: UUID


def create_working_set(session: Session, request: WorkingSetCreate) -> WorkingSet:
    session.execute(text("SELECT pg_advisory_xact_lock(50806001)"))
    request_key = fingerprint(request.model_dump(mode="json"))
    previous = session.scalar(
        select(WorkingSet).where(WorkingSet.idempotency_key == request.idempotency_key)
    )
    if previous:
        if previous.request_hash != request_key:
            raise ReviewError("Ключ идемпотентности уже использован для другого набора")
        return previous
    version = session.get(CorpusVersion, request.corpus_version_id, with_for_update=True)
    if version is None:
        raise ReviewError("Версия корпуса не найдена", 404)
    operational = session.get(OperationalConfig, 1)
    if operational is None:
        raise ReviewError("Оперативная конфигурация не установлена")
    selected = list(session.scalars(select(Document).where(Document.id.in_(request.document_ids))))
    if len(selected) != len(set(request.document_ids)) or any(
        d.source != "corpus" or d.corpus_version_id != version.id or d.doc_kind == "договор"
        for d in selected
    ):
        raise ReviewError("Выберите входящие документы одной версии корпуса", 422)
    origins = {}
    for result in session.scalars(
        select(LinkingResult).where(LinkingResult.id.in_(request.result_ids))
    ):
        if result.source_id not in request.document_ids or result.source_id in origins:
            raise ReviewError("Исходы не соответствуют выбранным документам", 422)
        origins[result.source_id] = result
    if len(origins) != len(set(request.result_ids)):
        raise ReviewError("Исход поиска не найден", 422)
    clients = sorted({d.client_id for d in selected})
    session.execute(
        select(Client.id).where(Client.id.in_(clients)).order_by(Client.id).with_for_update()
    )
    # Unknown cards are potential contracts too; labels/split are never consulted.
    documents = list(
        session.scalars(
            select(Document)
            .where(
                Document.corpus_version_id == version.id,
                Document.client_id.in_(clients),
                (
                    Document.id.in_(request.document_ids)
                    | (Document.doc_kind == "договор")
                    | Document.doc_kind.is_(None)
                ),
            )
            .order_by(Document.id)
        )
    )
    work = WorkingSet(
        name=request.name.strip(),
        corpus_version_id=version.id,
        idempotency_key=request.idempotency_key,
        request_hash=request_key,
    )
    if not work.name:
        raise ReviewError("Укажите название набора", 422)
    session.add(work)
    session.flush()
    client_map = {}
    for id in clients:
        original = session.get(Client, id)
        client = Client(name=original.name, inn=original.inn, namespace=f"working:{work.id}")
        session.add(client)
        session.flush()
        client_map[id] = client.id
    mapping = {}
    for index, original in enumerate(documents, 1):
        source_text = session.get(DocumentText, original.id)
        if source_text is None:
            raise ReviewError("В исходном документе отсутствует текст", 422)
        target = Document(
            client_id=client_map[original.client_id],
            source="upload",
            filename=f"document-{index}.txt",
            sha256=original.sha256,
            mime=original.mime,
            storage_path=original.storage_path,
            is_anonymized=original.is_anonymized,
            card_model_config_id=original.card_model_config_id
            or UUID(operational.snapshot["model_id"]),
        )
        session.add(target)
        session.flush()
        session.add(
            DocumentText(
                document_id=target.id,
                text=source_text.text,
                pages=source_text.pages,
                chars=source_text.chars,
                ocr_pages=source_text.ocr_pages,
            )
        )
        origin = origins.get(original.id)
        if origin is None and original.id in request.document_ids:
            origin = session.scalar(
                select(LinkingResult)
                .where(
                    LinkingResult.source_id == original.id,
                    LinkingResult.model_config_id == UUID(operational.snapshot["model_id"]),
                    LinkingResult.method == operational.snapshot["config"]["method"],
                    LinkingResult.config == operational.snapshot["config"],
                    LinkingResult.provenance["encoding"] == operational.snapshot["encoding"],
                )
                .order_by(LinkingResult.created_at.desc(), LinkingResult.id.desc())
                .limit(1)
            )
        origin_current = False
        if origin and origin.provenance.get("card_source") != "regex":
            actual = linking_payload(
                session,
                original.id,
                origin.model_config_id,
                LinkingConfig.model_validate(origin.config),
                EncodingConfig.model_validate(origin.provenance["encoding"]),
            )
            origin_current = actual["input_hash"] == origin.input_hash
        session.add(
            WorkingCopy(
                document_id=target.id,
                working_set_id=work.id,
                source_document_id=original.id,
                source_result_id=origin.id if origin else None,
                source_result_current=origin_current,
            )
        )
        mapping[original.id] = (target, origin)
    session.flush()
    failed_pipelines = []
    for original in documents:
        target, origin = mapping[original.id]
        snapshot = copy.deepcopy(operational.snapshot)
        if origin and origin.provenance.get("card_source") != "regex":
            if (
                origin.config != snapshot["config"]
                or str(origin.model_config_id) != snapshot["model_id"]
            ):
                snapshot["origin_protocol_hash"] = None
            snapshot["origin_result_id"] = str(origin.id)
            snapshot["config"] = origin.config
            snapshot["encoding"] = origin.provenance["encoding"]
            snapshot["model_id"] = (
                str(origin.model_config_id) if origin.model_config_id else snapshot["model_id"]
            )
        copied = {}
        for extraction in session.scalars(
            select(Extraction).where(Extraction.document_id == original.id, Extraction.is_current)
        ):
            if (
                not original.is_anonymized
                or not {"provider", "model_uri", "params", "prompt", "schema", "prompt_version"}
                <= extraction.snapshot.keys()
            ):
                continue
            old_messages, _, old_cut, _ = prepared_request(session, original, extraction.snapshot)
            new_messages, _, new_cut, _ = prepared_request(session, target, extraction.snapshot)
            key = request_hash(extraction.snapshot, old_messages, original.client_id)
            call = session.get(LLMCall, extraction.llm_call_id) if extraction.llm_call_id else None
            if (
                old_messages != new_messages
                or old_cut != new_cut
                or request_hash(extraction.snapshot, new_messages, cache_client_id(session, target))
                != key
                or extraction.input_hash not in {None, key}
                or (call and call.root_input_hash != key)
                or (call and extraction.valid and call.status != "success")
                or (call is None and extraction.snapshot.get("provider") != "regex")
            ):
                continue
            fields = ExtractedFields.model_validate(
                {**extraction_values(extraction), "evidence": extraction.evidence}
            )
            result = save_result(
                session,
                target.id,
                extraction.model_config_id,
                extraction.snapshot,
                fields if extraction.valid else None,
                extraction.llm_call_id,
                copy.deepcopy(extraction.evidence_checks),
                True,
                None if extraction.valid else extraction.error or "Ошибка исходного извлечения",
            )
            result.source_extraction_id, result.input_hash = extraction.id, key
            result.preserved_error = extraction.preserved_error or not extraction.valid
            copied[extraction.id] = result
            if str(extraction.model_config_id) == snapshot["model_id"]:
                snapshot["extraction"] = extraction.snapshot
                if call:
                    snapshot["prompt_id"] = str(call.prompt_id)
        selected_card = copied.get(original.card_extraction_id)
        if selected_card and selected_card.valid:
            from app.services.extraction import select_card

            select_card(session, selected_card)
        encoding = EncodingConfig.model_validate(snapshot["encoding"])
        prepared = prepare_text(session.get(DocumentText, target.id).text, encoding)
        for row in session.execute(
            select(DocumentEmbedding, Embedding)
            .join(Embedding, Embedding.id == DocumentEmbedding.embedding_id)
            .where(
                DocumentEmbedding.document_id == original.id,
                Embedding.input_hash == prepared.input_hash,
            )
        ):
            old, embedding = row
            import hashlib

            text_hash = hashlib.sha256(
                session.get(DocumentText, target.id).text.encode()
            ).hexdigest()
            if old.original_text_hash == text_hash:
                session.add(
                    DocumentEmbedding(
                        document_id=target.id,
                        embedding_id=embedding.id,
                        original_text_hash=text_hash,
                        original_chars=old.original_chars,
                        prepared_chars=old.prepared_chars,
                        cache_hit=True,
                    )
                )
                break
        model_result = next(
            (e for e in copied.values() if str(e.model_config_id) == snapshot["model_id"]), None
        )
        pipe = ProcessingPipeline(
            document_id=target.id,
            batch_id=work.id,
            snapshot=snapshot,
            stage="prepare" if model_result and selected_card else "extract",
        )
        # Preserved errors must be visible without any attempt to improve them.
        if model_result and not model_result.valid:
            pipe.error = model_result.error or "Сохранённая ошибка исходного извлечения"
        session.add(pipe)
        session.flush()
        if pipe.error:
            failed_pipelines.append((target, pipe))
    session.flush()
    work.baseline = {str(id): working_revision(session, id) for id in client_map.values()}
    session.flush()
    from app.services.pipeline import _record_failure

    for target, pipe in failed_pipelines:
        _record_failure(session, target, pipe)
    return work
