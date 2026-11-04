"""Human decisions: serialized per client, atomic with links, no changes to model outcomes.

Callers own the transaction. Session IDs identify a browser tab; a retry must retain
its idempotency key and request body. Timestamps are always taken on the server.
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Client,
    Document,
    Extraction,
    Link,
    LinkingResult,
    LinkSuggestion,
    ReviewAction,
    ReviewSession,
    ReviewState,
)
from app.services.embeddings import EncodingConfig
from app.services.linking.types import LinkingConfig
from app.services.linking_store import linking_payload
from app.services.model_calls import fingerprint


class ReviewError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    version: int = Field(ge=0)
    idempotency_key: UUID
    action: Literal[
        "confirm",
        "choose_other",
        "reject",
        "manual",
        "complete",
        "without_links",
        "reopen",
        "remove",
    ]
    suggestion_id: UUID | None = None
    target_id: UUID | None = None
    link_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=2000)


def lock_document(session: Session, document_id: UUID) -> Document:
    client_id = session.scalar(select(Document.client_id).where(Document.id == document_id))
    if client_id is None:
        raise ReviewError("Документ не найден", 404)
    session.execute(select(Client.id).where(Client.id == client_id).with_for_update())
    doc = session.scalar(
        select(Document)
        .where(Document.id == document_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if doc.source == "corpus":
        raise ReviewError("Корпус доступен только для просмотра; создайте рабочую копию", 422)
    return doc


def state_for(session: Session, document_id: UUID) -> ReviewState:
    state = session.get(ReviewState, document_id)
    if state is None:
        state = ReviewState(document_id=document_id)
        session.add(state)
        session.flush()
    return state


def latest_result(session: Session, document_id: UUID) -> LinkingResult | None:
    return session.scalar(
        select(LinkingResult)
        .where(LinkingResult.source_id == document_id)
        .order_by(LinkingResult.created_at.desc(), LinkingResult.id.desc())
        .limit(1)
    )


def context_for(session: Session, doc: Document) -> dict[str, Any]:
    """A working-input revision also exists when there is no candidate/result."""
    pool = session.execute(
        select(Document.id, Document.doc_kind, Document.date, Document.card_extraction_id)
        .where(
            Document.client_id == doc.client_id, Document.corpus_version_id == doc.corpus_version_id
        )
        .order_by(Document.id)
    ).all()
    ids = [row.id for row in pool]
    extractions = session.execute(
        select(Extraction.id, Extraction.document_id, Extraction.model_config_id)
        .where(Extraction.document_id.in_(ids), Extraction.is_current)
        .order_by(Extraction.id)
    ).all()
    overrides = session.execute(
        select(ReviewState.document_id, ReviewState.overrides)
        .where(ReviewState.document_id.in_(ids))
        .order_by(ReviewState.document_id)
    ).all()
    result = latest_result(session, doc.id)
    return {
        "input_revision": fingerprint(
            {
                "cards": [
                    [
                        str(r.id),
                        r.doc_kind,
                        r.date.isoformat() if r.date else None,
                        str(r.card_extraction_id),
                    ]
                    for r in pool
                ],
                "extractions": [[str(v) for v in row] for row in extractions],
                "overrides": [[str(row.document_id), row.overrides] for row in overrides],
            }
        ),
        "result_id": str(result.id) if result else None,
        "config": result.config if result else None,
        "provenance": result.provenance if result else {},
    }


def open_review(session: Session, document_id: UUID) -> ReviewSession:
    doc = lock_document(session, document_id)
    state_for(session, doc.id)
    context = context_for(session, doc)
    review = ReviewSession(
        document_id=doc.id,
        result_id=UUID(context["result_id"]) if context["result_id"] else None,
        context=context,
        opened_at=datetime.now(UTC),
    )
    session.add(review)
    session.flush()
    return review


def _fresh_result(session: Session, result: LinkingResult) -> None:
    payload = linking_payload(
        session,
        result.source_id,
        result.model_config_id,
        LinkingConfig.model_validate(result.config),
        EncodingConfig.model_validate(result.provenance["encoding"]),
    )
    if payload["input_hash"] != result.input_hash:
        raise ReviewError("Вход поиска устарел; обновите поиск")


def decide(session: Session, document_id: UUID, request: Decision) -> ReviewAction:
    doc = lock_document(session, document_id)
    request_hash = fingerprint(request.model_dump(mode="json"))
    previous = session.scalar(
        select(ReviewAction).where(
            ReviewAction.document_id == doc.id,
            ReviewAction.idempotency_key == request.idempotency_key,
        )
    )
    if previous:
        if previous.request_hash != request_hash:
            raise ReviewError("Ключ идемпотентности уже использован для другого запроса")
        return previous
    state = state_for(session, doc.id)
    review = session.get(ReviewSession, request.session_id)
    if review is None or review.document_id != doc.id:
        raise ReviewError("Сессия проверки не найдена", 404)
    if state.version != request.version:
        raise ReviewError("Проверка изменена в другой вкладке; обновите карточку")
    if request.action != "reopen" and (state.status != "open" or review.completed_at):
        raise ReviewError("Проверка завершена; сначала откройте её повторно")
    if request.action == "reopen" and state.status != "completed":
        raise ReviewError("Проверка уже открыта")
    if review.context != context_for(session, doc):
        raise ReviewError("Входы или результат изменились; откройте актуальную проверку")
    suggestion = (
        session.get(LinkSuggestion, request.suggestion_id) if request.suggestion_id else None
    )
    if request.suggestion_id and (
        suggestion is None
        or suggestion.source_id != doc.id
        or suggestion.result_id != review.result_id
    ):
        raise ReviewError("Кандидат не относится к текущему результату", 422)
    result = session.get(LinkingResult, review.result_id) if review.result_id else None
    if request.action in {"confirm", "choose_other", "reject"}:
        if suggestion is None or result is None:
            raise ReviewError("Выберите кандидата", 422)
        _fresh_result(session, result)
    if request.action == "confirm" and suggestion.decision != "recommendation":
        raise ReviewError("Этот кандидат не является рекомендацией", 422)
    if request.action == "manual" and request.suggestion_id is not None:
        raise ReviewError("Для ручной связи suggestion_id не передаётся", 422)
    if request.action != "remove" and request.link_id is not None:
        raise ReviewError("link_id допустим только при отмене связи", 422)
    if request.action in {"complete", "without_links", "reopen", "remove"} and (
        request.target_id is not None or request.suggestion_id is not None
    ):
        raise ReviewError("Для этого действия кандидат и цель не передаются", 422)
    target_id = request.target_id
    if request.action == "reject":
        if target_id is not None and target_id != suggestion.target_id:
            raise ReviewError("Цель не совпадает с отклоняемым кандидатом", 422)
        target_id = suggestion.target_id
    if request.action == "confirm":
        target_id = suggestion.target_id
        if request.target_id and request.target_id != target_id:
            raise ReviewError("Цель не совпадает с рекомендацией", 422)
    link_type = None
    if request.action in {"confirm", "choose_other", "manual"}:
        target = session.scalar(
            select(Document)
            .where(
                Document.id == target_id,
                Document.client_id == doc.client_id,
            )
            .with_for_update()
        )
        link_type = {
            "допсоглашение": "изменение",
            "счёт": "основание",
            "акт": "исполнение",
            "УПД": "исполнение",
        }.get(doc.doc_kind)
        if not link_type or doc.date is None:
            raise ReviewError("Сначала заполните вид и дату источника", 422)
        if (
            target is None
            or target.id == doc.id
            or target.doc_kind != "договор"
            or (
                target.client_id != doc.client_id
                or target.corpus_version_id != doc.corpus_version_id
                or target.date is None
                or target.date > doc.date
                or target.source == "corpus"
            )
        ):
            raise ReviewError(
                "Недопустимая цель: нужен договор того же клиента не позже документа", 422
            )
        if session.scalar(
            select(Link.id).where(
                Link.source_id == doc.id, Link.target_id == target_id, Link.removed_at.is_(None)
            )
        ):
            raise ReviewError("Активная связь с этим договором уже существует")
    active = list(
        session.scalars(select(Link).where(Link.source_id == doc.id, Link.removed_at.is_(None)))
    )
    removed = None
    if request.action == "remove":
        removed = next((v for v in active if v.id == request.link_id), None)
        if removed is None:
            raise ReviewError("Активная связь не найдена", 422)
        target_id = removed.target_id
    if request.action in {"complete", "without_links"}:
        if request.action == "without_links":
            if active or not (request.reason or "").strip():
                raise ReviewError(
                    "Для завершения без связей нужна причина и отсутствие активных связей", 422
                )
        elif not active:
            raise ReviewError("Без связей завершайте с указанием причины", 422)
        if result and result.outcome != "error":
            _fresh_result(session, result)
        recommendations = (
            set(
                session.scalars(
                    select(LinkSuggestion.id).where(
                        LinkSuggestion.result_id == review.result_id,
                        LinkSuggestion.decision == "recommendation",
                    )
                )
            )
            if review.result_id
            else set()
        )
        resolved = set(
            session.scalars(
                select(ReviewAction.suggestion_id).where(
                    ReviewAction.document_id == doc.id,
                    ReviewAction.result_id == review.result_id,
                    ReviewAction.action.in_(["confirm", "choose_other", "reject"]),
                )
            )
        )
        if recommendations - resolved:
            raise ReviewError("Сначала примите решение по всем рекомендациям")
    now = datetime.now(UTC)
    duration = max(0, int((now - review.opened_at).total_seconds() * 1000))
    state.version += 1
    action = ReviewAction(
        document_id=doc.id,
        session_id=review.id,
        result_id=review.result_id,
        suggestion_id=request.suggestion_id,
        chosen_target_id=target_id,
        action=request.action,
        reason=request.reason,
        idempotency_key=request.idempotency_key,
        request_hash=request_hash,
        opened_at=review.opened_at,
        decided_at=now,
        duration_ms=duration,
        state_version=state.version,
    )
    session.add(action)
    session.flush()
    if link_type:
        link = Link(
            source_id=doc.id,
            target_id=target_id,
            link_type=link_type,
            origin="manual" if request.action == "manual" else "confirmed",
            review_action_id=action.id,
            suggestion_id=request.suggestion_id,
        )
        session.add(link)
        from app.services.integration import create_event

        create_event(session, link, action, "create")
    if removed:
        removed.removed_at, removed.removal_action_id = now, action.id
        from app.services.integration import create_event

        create_event(session, removed, action, "remove")
    if request.action in {"complete", "without_links"}:
        state.status, state.needs_review = "completed", False
        review.completed_at, review.duration_ms = now, duration
    elif request.action == "reopen":
        state.status = "open"
    session.flush()
    return action
