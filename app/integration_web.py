"""Authenticated HTML status and explicit retry; no payload/secret export."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from app.auth import require_user
from app.documents import DB, render
from app.models import IntegrationAttempt, IntegrationEvent, IntegrationInbox, IntegrationState
from app.services.integration import retry
from app.services.model_calls import ProcessingError

router = APIRouter(
    prefix="/integration", include_in_schema=False, dependencies=[Depends(require_user)]
)


@router.get("")
def status(request: Request, session: DB, document_id: UUID | None = None) -> Response:
    incoming = select(IntegrationInbox).order_by(IntegrationInbox.run_after.desc())
    events = select(IntegrationEvent).order_by(IntegrationEvent.created_at.desc())
    if document_id:
        incoming = incoming.where(IntegrationInbox.document_id == document_id)
        events = events.where(IntegrationEvent.document_id == document_id)
    return render(
        request,
        "integration.html",
        enabled=request.app.state.settings.mock1c_enabled,
        state=session.get(IntegrationState, "mock1c"),
        incoming=session.scalars(incoming.limit(100)).all(),
        events=session.scalars(events.limit(100)).all(),
        attempts=session.scalars(
            select(IntegrationAttempt).order_by(IntegrationAttempt.id.desc()).limit(100)
        ).all(),
    )


@router.post("/retry")
def repeat(session: DB, kind: Annotated[str, Form()], row_id: Annotated[UUID, Form()]) -> Response:
    try:
        retry(session, kind, row_id)
        state = session.get(IntegrationState, "mock1c")
        if state:
            state.next_poll_at = datetime.now(UTC)
        session.commit()
    except ProcessingError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse("/integration", status_code=303)


@router.post("/poll")
def request_poll(session: DB) -> Response:
    state = session.get(IntegrationState, "mock1c")
    if state:
        state.next_poll_at = datetime.now(UTC)
        session.commit()
    return RedirectResponse("/integration", status_code=303)
