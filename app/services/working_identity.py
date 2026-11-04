"""Trusted copy provenance; no evaluation labels or corpus folder names."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CardEdit,
    Client,
    Document,
    DocumentText,
    Extraction,
    ReviewState,
    WorkingCopy,
    WorkingSet,
)
from app.services.model_calls import fingerprint


def cache_client_id(session: Session, doc: Document) -> UUID:
    copy = session.get(WorkingCopy, doc.id)
    if copy is None or not doc.is_anonymized:
        return doc.client_id
    original = session.execute(
        select(Document.client_id, Document.sha256, Document.is_anonymized).where(
            Document.id == copy.source_document_id
        )
    ).one()
    current_client = session.get(Client, doc.client_id)
    original_client = session.get(Client, original.client_id)
    old_text = session.get(DocumentText, copy.source_document_id)
    new_text = session.get(DocumentText, doc.id)
    if (
        original.is_anonymized
        and doc.sha256 == original.sha256
        and old_text
        and new_text
        and old_text.text == new_text.text
        and (current_client.name, current_client.inn) == (original_client.name, original_client.inn)
    ):
        return original.client_id
    return doc.client_id


def working_revision(session: Session, client_id: UUID) -> str:
    values = []
    for doc in session.execute(
        select(
            Document.id,
            Document.doc_kind,
            Document.date,
            Document.number,
            Document.card_extraction_id,
        )
        .where(Document.client_id == client_id)
        .order_by(Document.id)
    ):
        text = session.get(DocumentText, doc.id)
        state = session.get(ReviewState, doc.id)
        extractions = list(
            session.scalars(
                select(Extraction.id)
                .where(Extraction.document_id == doc.id, Extraction.is_current)
                .order_by(Extraction.id)
            )
        )
        values.append(
            [
                str(doc.id),
                doc.doc_kind,
                str(doc.date),
                doc.number,
                str(doc.card_extraction_id),
                [str(e) for e in extractions],
                fingerprint(text.text) if text else None,
                state.overrides if state else {},
            ]
        )
        latest_edit = session.scalar(
            select(CardEdit.id)
            .where(CardEdit.document_id == doc.id)
            .order_by(CardEdit.created_at.desc(), CardEdit.id.desc())
            .limit(1)
        )
        if latest_edit:
            values.append(["card_edit", str(doc.id), str(latest_edit)])
    return fingerprint(values)


def copy_origin(session: Session, document_id: UUID) -> dict[str, Any] | None:
    copy = session.get(WorkingCopy, document_id)
    if copy is None:
        return None
    work = session.get(WorkingSet, copy.working_set_id)
    client_id = session.scalar(select(Document.client_id).where(Document.id == document_id))
    return {
        "working_set_id": str(work.id),
        "source_document_id": str(copy.source_document_id),
        "source_result_id": str(copy.source_result_id) if copy.source_result_id else None,
        "initial_inputs": copy.source_result_current
        and work.baseline.get(str(client_id)) == working_revision(session, client_id),
    }
