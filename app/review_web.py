"""Thin HTML adapters over the same review/card services used by REST."""

import json
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from markupsafe import Markup, escape
from pydantic import ValidationError
from sqlalchemy import func, select

from app.auth import require_user
from app.documents import DB, find_document, parse_uuid, render
from app.models import (
    CardEdit,
    Client,
    Document,
    DocumentText,
    Extraction,
    Link,
    LinkingResult,
    LinkSuggestion,
    ProcessingPipeline,
    ReviewAction,
    ReviewSession,
    ReviewState,
)
from app.services.card_edits import CardPatch, edit_card, effective_values
from app.services.embeddings import EncodingConfig
from app.services.linking.types import LinkingConfig
from app.services.linking_store import enqueue_linking
from app.services.model_calls import ProcessingError
from app.services.review import Decision, ReviewError, decide, latest_result, open_review, state_for

router = APIRouter(prefix="/review", include_in_schema=False, dependencies=[Depends(require_user)])
OUTCOMES = {
    "recommendation": "Рекомендация",
    "review": "Разбор",
    "none": "Нет кандидата",
    "error": "Ошибка обработки",
}


@router.get("")
def queue(
    request: Request,
    session: DB,
    client_id: str = "",
    outcome: str = "",
    status: str = "",
    page: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    if outcome not in {"", *OUTCOMES} or status not in {"", "open", "completed"}:
        raise HTTPException(422, "Неизвестный фильтр")
    latest = (
        select(LinkingResult.id)
        .where(LinkingResult.source_id == Document.id)
        .order_by(LinkingResult.created_at.desc(), LinkingResult.id.desc())
        .correlate(Document)
        .limit(1)
        .scalar_subquery()
    )
    query = (
        select(Document, LinkingResult, ReviewState)
        .join(LinkingResult, LinkingResult.id == latest)
        .outerjoin(ReviewState, ReviewState.document_id == Document.id)
        .where(Document.source != "corpus", Document.doc_kind.is_distinct_from("договор"))
    )
    if client_id:
        query = query.where(Document.client_id == parse_uuid(client_id))
    if outcome:
        query = query.where(LinkingResult.outcome == outcome)
    if status:
        query = query.where(func.coalesce(ReviewState.status, "open") == status)
    subquery = query.subquery()
    counts = dict(
        session.execute(select(subquery.c.outcome, func.count()).group_by(subquery.c.outcome)).all()
    )
    rows = session.execute(
        query.order_by(Document.created_at.desc(), Document.id).offset((page - 1) * 25).limit(25)
    ).all()
    return render(
        request,
        "review_queue.html",
        rows=rows,
        total=sum(counts.values()),
        page=page,
        outcomes=OUTCOMES,
        counts=counts,
        clients=session.scalars(select(Client).order_by(Client.name)).all(),
        selected_client=str(client_id or ""),
        selected_outcome=outcome,
        selected_status=status,
    )


def highlighted(text: str, extractions: list[Extraction]) -> Markup:
    """Only actual original-text matches are marked; all user text is HTML-escaped."""
    spans: list[tuple[int, int]] = []
    for extraction in extractions:
        if not extraction.is_current:
            continue
        for check in extraction.evidence_checks:
            quote = check.get("original_quote") or check.get("quote")
            if check.get("ok") and quote:
                start = text.find(quote)
                if start >= 0:
                    spans.append((start, start + len(quote)))
    end, parts = 0, []
    for start, stop in sorted(set(spans)):
        if start < end:
            continue
        parts.extend(
            [escape(text[end:start]), Markup("<mark>"), escape(text[start:stop]), Markup("</mark>")]
        )
        end = stop
    parts.append(escape(text[end:]))
    return Markup("").join(parts)


@router.get("/{document_id}")
def card(
    request: Request, session: DB, document_id: UUID, session_id: UUID | None = None
) -> Response:
    doc = find_document(session, document_id)
    try:
        if session_id:
            review = session.get(ReviewSession, session_id)
            if review is None or review.document_id != document_id:
                raise ReviewError("Сессия проверки не найдена", 404)
        else:
            review = open_review(session, document_id)
        state = state_for(session, document_id)
        session.commit()
    except ReviewError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    result = latest_result(session, document_id)
    extractions = list(
        session.scalars(
            select(Extraction)
            .where(Extraction.document_id == doc.id)
            .order_by(Extraction.created_at.desc())
        )
    )
    text = session.get(DocumentText, doc.id)
    targets = session.scalars(
        select(Document)
        .where(
            Document.client_id == doc.client_id,
            Document.corpus_version_id == doc.corpus_version_id,
            Document.doc_kind == "договор",
            Document.id != doc.id,
            Document.date <= doc.date if doc.date else False,
            Document.source != "corpus",
        )
        .order_by(Document.date, Document.number)
    ).all()
    return render(
        request,
        "review_card.html",
        document=doc,
        review=review,
        state=state,
        result=result,
        outcomes=OUTCOMES,
        values=effective_values(session, doc),
        suggestions=session.scalars(
            select(LinkSuggestion)
            .where(LinkSuggestion.result_id == result.id)
            .order_by(LinkSuggestion.rank)
        ).all()
        if result
        else [],
        links=session.scalars(
            select(Link).where(Link.source_id == doc.id).order_by(Link.created_at)
        ).all(),
        actions=session.scalars(
            select(ReviewAction)
            .where(ReviewAction.document_id == doc.id)
            .order_by(ReviewAction.decided_at)
        ).all(),
        edits=session.scalars(
            select(CardEdit).where(CardEdit.document_id == doc.id).order_by(CardEdit.created_at)
        ).all(),
        extractions=extractions,
        targets=targets,
        uuid4=uuid4,
        highlighted_text=highlighted(text.text, extractions) if text else "Текст отсутствует",
    )


@router.post("/{document_id}/decision")
async def decision(request: Request, session: DB, document_id: UUID) -> Response:
    form = dict(await request.form())
    try:
        parsed = Decision.model_validate({key: value for key, value in form.items() if value != ""})
        decide(session, document_id, parsed)
        session.commit()
    except (ReviewError, ValidationError) as exc:
        raise HTTPException(exc.status if isinstance(exc, ReviewError) else 422, str(exc)) from exc
    suffix = "" if parsed.action == "reopen" else f"?session_id={parsed.session_id}"
    return RedirectResponse(f"/review/{document_id}{suffix}", status_code=303)


@router.post("/{document_id}/edit")
def edit(
    session: DB,
    document_id: UUID,
    version: Annotated[int, Form()],
    session_id: Annotated[UUID, Form()],
    idempotency_key: Annotated[UUID, Form()],
    field: Annotated[str, Form()],
    value: Annotated[str, Form()] = "",
    reset: Annotated[bool, Form()] = False,
) -> Response:
    try:
        parsed = (
            None
            if reset
            else (
                json.loads(value)
                if field in {"contract_refs", "supp_ref", "invoice_ref"} and value.strip()
                else value.strip() or None
            )
        )
        patch = (
            CardPatch(
                version=version,
                session_id=session_id,
                idempotency_key=idempotency_key,
                reset=[field],
            )
            if reset
            else CardPatch(
                version=version,
                session_id=session_id,
                idempotency_key=idempotency_key,
                values={field: parsed},
            )
        )
        edit_card(session, document_id, patch)
        session.commit()
    except (ReviewError, ValidationError, ValueError, ProcessingError) as exc:
        raise HTTPException(exc.status if isinstance(exc, ReviewError) else 422, str(exc)) from exc
    return RedirectResponse(f"/review/{document_id}", status_code=303)


@router.post("/{document_id}/search")
def search(session: DB, document_id: UUID) -> Response:
    from app.services.review import lock_document

    lock_document(session, document_id)
    if session.get(ProcessingPipeline, document_id):
        from app.services.pipeline import retry_pipeline

        try:
            retry_pipeline(session, document_id, mode="search")
            session.commit()
        except ProcessingError as exc:
            raise HTTPException(409, str(exc)) from exc
        return RedirectResponse(f"/review/{document_id}", status_code=303)
    result = latest_result(session, document_id)
    if result is None:
        raise HTTPException(409, "Сначала выполните первичную обработку")
    try:
        enqueue_linking(
            session,
            document_id,
            result.model_config_id,
            LinkingConfig.model_validate(result.config),
            EncodingConfig.model_validate(result.provenance["encoding"]),
            retry=True,
        )
        session.commit()
    except ProcessingError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/review/{document_id}", status_code=303)
