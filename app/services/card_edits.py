"""Manual values are a separate, versioned layer; model output/evidence stay immutable."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CardEdit,
    Counterparty,
    Document,
    Extraction,
    Link,
    ProcessingPipeline,
    ReviewAction,
    ReviewSession,
    ReviewState,
)
from app.services.extraction_schema import ExtractedFields
from app.services.model_calls import fingerprint

EDITABLE = {
    "doc_kind",
    "number",
    "date",
    "counterparty_name",
    "counterparty_inn",
    "amount",
    "subject",
    "contract_refs",
    "supp_ref",
    "invoice_ref",
}


class CardPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)
    session_id: UUID
    idempotency_key: UUID
    values: dict[str, Any] = Field(default_factory=dict)
    reset: list[str] = Field(default_factory=list)


def overrides_for(session: Session, document_id: UUID) -> dict[str, Any]:
    return (
        session.scalar(select(ReviewState.overrides).where(ReviewState.document_id == document_id))
        or {}
    )


def extraction_values(extraction: Extraction | None) -> dict[str, Any]:
    if extraction is None:
        return {}
    return {
        "doc_kind": extraction.doc_kind,
        "number": extraction.number,
        "date": extraction.date.strftime("%d.%m.%Y") if extraction.date else None,
        "counterparty_name": extraction.cp_name,
        "counterparty_inn": extraction.cp_inn,
        "amount": str(extraction.amount) if extraction.amount is not None else None,
        "subject": extraction.subject,
        "contract_refs": extraction.contract_refs,
        "supp_ref": extraction.supp_ref,
        "invoice_ref": extraction.invoice_ref,
    }


def effective_values(
    session: Session, doc: Document, model_id: UUID | None = None
) -> dict[str, Any]:
    extraction = session.scalar(
        select(Extraction).where(
            Extraction.document_id == doc.id,
            Extraction.model_config_id == (model_id or doc.card_model_config_id),
            Extraction.is_current,
        )
    )
    return {**extraction_values(extraction), **overrides_for(session, doc.id)}


def apply_card_values(session: Session, doc: Document, values: dict[str, Any]) -> None:
    fields = ExtractedFields.model_validate(values)
    doc.doc_kind = fields.doc_kind if fields.doc_kind != "другое" else None
    doc.number = fields.number
    doc.date = datetime.strptime(fields.date, "%d.%m.%Y").date() if fields.date else None
    doc.counterparty_id = None
    if fields.counterparty_name or fields.counterparty_inn:
        cp = Counterparty(
            client_id=doc.client_id,
            name=fields.counterparty_name or "Неизвестно",
            inn=fields.counterparty_inn,
        )
        session.add(cp)
        session.flush()
        doc.counterparty_id = cp.id


def edit_card(session: Session, document_id: UUID, patch: CardPatch) -> ReviewState:
    # Local import keeps the pure value layer usable by the linking adapter.
    from app.services.embeddings import EncodingConfig
    from app.services.linking.types import LinkingConfig
    from app.services.linking_store import enqueue_linking
    from app.services.review import ReviewError, latest_result, lock_document, state_for

    doc = lock_document(session, document_id)
    previous_kind = doc.doc_kind
    state = state_for(session, doc.id)
    request_hash = fingerprint(patch.model_dump(mode="json"))
    previous = session.scalar(
        select(ReviewAction).where(
            ReviewAction.document_id == doc.id,
            ReviewAction.idempotency_key == patch.idempotency_key,
        )
    )
    if previous:
        if previous.request_hash != request_hash:
            raise ReviewError("Ключ идемпотентности уже использован для другого запроса")
        return state
    if state.version != patch.version:
        raise ReviewError("Карточка изменена; обновите страницу")
    review = session.get(ReviewSession, patch.session_id)
    if review is None or review.document_id != doc.id:
        raise ReviewError("Сессия проверки не найдена", 404)
    if (
        set(patch.values) - EDITABLE
        or set(patch.reset) - EDITABLE
        or set(patch.values) & set(patch.reset)
    ):
        raise ReviewError("Неизвестное поле или одновременная правка и сброс", 422)
    before = dict(state.overrides)
    after = {key: value for key, value in before.items() if key not in patch.reset}
    proposed = {**after, **patch.values}
    if proposed.get("contract_refs", []) is None:
        proposed["contract_refs"] = []
    # Validate and normalize only the edited values; null is an explicit clearing.
    base = effective_values(session, doc)
    for key in patch.reset:
        base.pop(key, None)
    model = session.get(Extraction, doc.card_extraction_id) if doc.card_extraction_id else None
    base = {**extraction_values(model), **base, **proposed}
    fields = ExtractedFields.model_validate(base).model_dump(mode="json")
    after = {key: fields[key] for key in proposed}
    if after == before:
        return state
    state.overrides, state.version, state.needs_review = after, state.version + 1, True
    now = datetime.now(UTC)
    action = ReviewAction(
        document_id=doc.id,
        session_id=review.id,
        result_id=review.result_id,
        action="edit",
        idempotency_key=patch.idempotency_key,
        request_hash=request_hash,
        opened_at=review.opened_at,
        decided_at=now,
        duration_ms=max(0, int((now - review.opened_at).total_seconds() * 1000)),
        state_version=state.version,
    )
    session.add(action)
    session.flush()
    session.add(
        CardEdit(document_id=doc.id, review_action_id=action.id, before=before, after=after)
    )
    apply_card_values(session, doc, base)
    session.flush()
    # Changes to a contract affect every existing dependent search, not only links.
    dependents = list(
        session.scalars(
            select(Document)
            .where(
                Document.client_id == doc.client_id,
                Document.corpus_version_id == doc.corpus_version_id,
                Document.source != "corpus",
            )
            .order_by(Document.id)
        )
    )
    linked_sources = set(
        session.scalars(
            select(Link.source_id).where(
                Link.target_id == doc.id,
                Link.removed_at.is_(None),
            )
        )
    )
    for dependent in dependents:
        if (
            dependent.id != doc.id
            and doc.doc_kind != "договор"
            and dependent.id not in linked_sources
        ):
            # A former contract must still invalidate dependent pools.
            if previous_kind != "договор":
                continue
        dep_state = state_for(session, dependent.id)
        dep_state.needs_review = True
        result = latest_result(session, dependent.id)
        pipeline = session.get(ProcessingPipeline, dependent.id)
        if pipeline:
            # The durable coordinator prepares vectors and the full client pool first.
            if pipeline.stage in {"done", "prepare"}:
                pipeline.stage, pipeline.result_id = "prepare", None
            continue
        if result:
            enqueue_linking(
                session,
                dependent.id,
                result.model_config_id,
                LinkingConfig.model_validate(result.config),
                EncodingConfig.model_validate(result.provenance["encoding"]),
            )
    session.flush()
    return state
