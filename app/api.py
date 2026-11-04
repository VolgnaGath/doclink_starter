"""Cookie-authenticated REST adapters; all mutations use shared domain services."""

from collections.abc import Iterator
from dataclasses import asdict
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Security,
    UploadFile,
)
from fastapi.security import APIKeyCookie
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import api_schemas as s
from app.db import get_session
from app.models import (
    Client,
    CorpusVersion,
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
    StageTiming,
    WorkingCopy,
    WorkingSet,
)
from app.services.card_edits import CardPatch, edit_card
from app.services.corpus_archive import unpack_corpus
from app.services.corpus_import import CorpusError, import_corpus
from app.services.model_calls import ProcessingError
from app.services.processing_requests import request_processing
from app.services.registration import IncomingFile, register_batch, register_client
from app.services.review import Decision, decide, latest_result, open_review
from app.services.working_identity import copy_origin
from app.services.working_sets import WorkingSetCreate, create_working_set

cookie = APIKeyCookie(name="session", auto_error=False)


def require_api_user(
    request: Request, token: Annotated[str | None, Security(cookie)] = None
) -> str:
    if not token or not request.session.get("user"):
        raise HTTPException(401, "Требуется вход через /login")
    return str(request.session["user"])


def database(request: Request) -> Iterator[Session]:
    with get_session(request.app.state.settings.database_url) as session:
        yield session


DB = Annotated[Session, Depends(database)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_user)],
    responses={code: {"model": s.ErrorBody} for code in (401, 404, 409, 422, 500)},
)


def document(session: Session, id: UUID) -> Document:
    row = session.get(Document, id)
    if row is None:
        raise HTTPException(404, "Документ не найден")
    return row


def summary(row: Document) -> s.DocumentSummary:
    values = {key: getattr(row, key) for key in s.DocumentSummary.model_fields if key != "kind"}
    values["kind"] = row.doc_kind
    values["filename"] = row.filename.replace("\\", "/").rsplit("/", 1)[-1]
    return s.DocumentSummary.model_validate(values)


def search_view(session: Session, row: LinkingResult) -> s.SearchView:
    values = {
        key: getattr(row, key)
        for key in s.SearchView.model_fields
        if key not in {"working_copy", "candidates"}
    }
    values["working_copy"] = row.provenance.get("working_copy")
    values["candidates"] = list(
        session.scalars(
            select(LinkSuggestion)
            .where(LinkSuggestion.result_id == row.id)
            .order_by(LinkSuggestion.rank)
        )
    )
    return s.SearchView.model_validate(values)


def working_set_view(session: Session, row: WorkingSet) -> s.WorkingSetView:
    return s.WorkingSetView(
        id=row.id,
        name=row.name,
        corpus_version_id=row.corpus_version_id,
        created_at=row.created_at,
        copies=[
            s.CopyView(
                document_id=c.document_id,
                source_document_id=c.source_document_id,
                source_result_id=c.source_result_id,
                client_id=client_id,
            )
            for c, client_id in session.execute(
                select(WorkingCopy, Document.client_id)
                .join(Document, Document.id == WorkingCopy.document_id)
                .where(WorkingCopy.working_set_id == row.id)
                .order_by(WorkingCopy.document_id)
            )
        ],
    )


def session_view(row: ReviewSession) -> s.SessionView:
    return s.SessionView(
        id=row.id,
        document_id=row.document_id,
        result_id=row.result_id,
        opened_at=row.opened_at,
        completed_at=row.completed_at,
        duration_ms=row.duration_ms,
        input_revision=row.context["input_revision"],
        working_copy=row.context.get("provenance", {}).get("working_copy"),
    )


def paginate(session: Session, query: Any, limit: int, offset: int) -> dict[str, Any]:
    return {
        "items": list(session.scalars(query.limit(limit).offset(offset))),
        "total": session.scalar(select(func.count()).select_from(query.order_by(None).subquery())),
        "limit": limit,
        "offset": offset,
    }


@router.get("/clients", response_model=s.Page[s.ClientView])
def clients(
    session: DB, limit: Limit = 50, offset: Offset = 0, working_only: bool = True
) -> dict[str, Any]:
    query = select(Client).order_by(Client.name, Client.id)
    if working_only:
        query = query.where(~Client.namespace.startswith("corpus"))
    return paginate(session, query, limit, offset)


@router.post("/clients", response_model=s.ClientView, status_code=201)
def add_client(session: DB, body: s.ClientCreate) -> Client:
    try:
        row = register_client(session, body.name, body.inn)
    except ProcessingError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return row


@router.post("/documents", response_model=s.UploadResponse, status_code=201)
def upload(
    request: Request,
    session: DB,
    client_id: Annotated[UUID, Form()],
    files: Annotated[list[UploadFile], File()],
    is_anonymized: Annotated[bool, Form()] = False,
) -> s.UploadResponse:
    try:
        results = register_batch(
            session,
            request.app.state.settings,
            client_id,
            [IncomingFile(f.filename or "", f.file) for f in files],
            is_anonymized=is_anonymized,
        )
    except ProcessingError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return s.UploadResponse(results=[s.UploadItem(**asdict(r)) for r in results])


@router.get("/documents", response_model=s.Page[s.DocumentSummary])
def documents(
    session: DB,
    client_id: UUID | None = None,
    kind: s.Kind | None = None,
    status: s.Status | None = None,
    split: Literal["tune", "test"] | None = None,
    source: Literal["upload", "mock1c", "corpus"] | None = None,
    corpus_version_id: UUID | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    query = select(Document).order_by(Document.created_at.desc(), Document.id)
    for column, value in [
        (Document.client_id, client_id),
        (Document.doc_kind, kind),
        (Document.status, status),
        (Document.split, split),
        (Document.source, source),
        (Document.corpus_version_id, corpus_version_id),
    ]:
        if value is not None:
            query = query.where(column == value)
    page = paginate(session, query, limit, offset)
    page["items"] = [summary(d) for d in page["items"]]
    return page


@router.get("/documents/{id}", response_model=s.DocumentDetail)
def detail(session: DB, id: UUID) -> s.DocumentDetail:
    row = document(session, id)
    extractions = []
    for e in session.scalars(
        select(Extraction)
        .where(Extraction.document_id == id)
        .order_by(Extraction.created_at.desc())
    ):
        values = {key: getattr(e, key) for key in s.ExtractionView.model_fields}
        values["amount"] = str(e.amount) if e.amount is not None else None
        extractions.append(s.ExtractionView.model_validate(values))
    result = latest_result(session, id)
    return s.DocumentDetail(
        document=summary(row),
        text=session.get(DocumentText, id),
        extractions=extractions,
        result=search_view(session, result) if result else None,
        links=list(
            session.scalars(select(Link).where(Link.source_id == id).order_by(Link.created_at))
        ),
        review_state=session.get(ReviewState, id),
        review_actions=list(
            session.scalars(
                select(ReviewAction)
                .where(ReviewAction.document_id == id)
                .order_by(ReviewAction.decided_at)
            )
        ),
        review_sessions=[
            session_view(v)
            for v in session.scalars(
                select(ReviewSession)
                .where(ReviewSession.document_id == id)
                .order_by(ReviewSession.opened_at)
            )
        ],
        stage_timings=list(
            session.scalars(
                select(StageTiming)
                .where(StageTiming.document_id == id)
                .order_by(StageTiming.started_at)
            )
        ),
        pipeline=session.get(ProcessingPipeline, id),
        working_copy=copy_origin(session, row.id),
    )


@router.post("/documents/{id}/process", response_model=s.ProcessAccepted, status_code=202)
def process(session: DB, id: UUID, body: s.ProcessRequest | None = None) -> s.ProcessAccepted:
    body = body or s.ProcessRequest()
    ids = request_processing(session, id, mode=body.mode, model_id=body.model_id)
    session.commit()
    return s.ProcessAccepted(
        document_id=id, job_ids=ids, pipeline=session.get(ProcessingPipeline, id)
    )


@router.patch("/documents/{id}/card", response_model=s.ReviewStateView)
def patch_card(session: DB, id: UUID, body: CardPatch) -> ReviewState:
    row = edit_card(session, id, body)
    session.commit()
    return row


@router.post("/documents/{id}/review-sessions", response_model=s.SessionView, status_code=201)
def review_session(session: DB, id: UUID) -> s.SessionView:
    row = open_review(session, id)
    session.commit()
    return session_view(row)


@router.post("/documents/{id}/review", response_model=s.ActionView)
def review_document(session: DB, id: UUID, body: Decision) -> ReviewAction:
    row = decide(session, id, body)
    session.commit()
    return row


@router.post("/suggestions/{id}/review", response_model=s.ActionView)
def review_suggestion(session: DB, id: UUID, body: Decision) -> ReviewAction:
    suggestion = session.get(LinkSuggestion, id)
    if suggestion is None:
        raise HTTPException(404, "Кандидат не найден")
    if body.suggestion_id is not None and body.suggestion_id != id:
        raise HTTPException(422, "Идентификатор кандидата не совпадает с URL")
    return review_document(
        session, suggestion.source_id, body.model_copy(update={"suggestion_id": id})
    )


@router.get("/suggestions", response_model=s.Page[s.SearchView])
def suggestions(
    session: DB,
    decision: s.Outcome | None = None,
    document_id: UUID | None = None,
    client_id: UUID | None = None,
    history: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    query = select(LinkingResult).join(Document, Document.id == LinkingResult.source_id)
    if not history:
        from sqlalchemy.orm import aliased

        other = aliased(LinkingResult)
        latest = (
            select(other.id)
            .where(other.source_id == LinkingResult.source_id)
            .order_by(other.created_at.desc(), other.id.desc())
            .correlate(LinkingResult)
            .limit(1)
            .scalar_subquery()
        )
        query = query.where(LinkingResult.id == latest)
    if decision is not None:
        query = query.where(LinkingResult.outcome == decision)
    if document_id:
        query = query.where(LinkingResult.source_id == document_id)
    if client_id:
        query = query.where(Document.client_id == client_id)
    page = paginate(
        session, query.order_by(LinkingResult.created_at.desc(), LinkingResult.id), limit, offset
    )
    page["items"] = [search_view(session, row) for row in page["items"]]
    return page


@router.get("/links", response_model=s.Page[s.LinkView])
def links(
    session: DB,
    document_id: UUID | None = None,
    client_id: UUID | None = None,
    link_type: Literal["изменение", "основание", "исполнение"] | None = None,
    origin: Literal["confirmed", "manual"] | None = None,
    include_removed: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    query = (
        select(Link)
        .join(Document, Document.id == Link.source_id)
        .order_by(Link.created_at, Link.id)
    )
    if document_id:
        query = query.where(or_(Link.source_id == document_id, Link.target_id == document_id))
    if client_id:
        query = query.where(Document.client_id == client_id)
    if link_type:
        query = query.where(Link.link_type == link_type)
    if origin:
        query = query.where(Link.origin == origin)
    if not include_removed:
        query = query.where(Link.removed_at.is_(None))
    return paginate(session, query, limit, offset)


@router.get("/corpus", response_model=s.Page[s.CorpusVersionView])
def corpus(session: DB, limit: Limit = 50, offset: Offset = 0) -> dict[str, Any]:
    return paginate(
        session,
        select(CorpusVersion).order_by(CorpusVersion.imported_at.desc(), CorpusVersion.id),
        limit,
        offset,
    )


@router.post("/corpus/import", response_model=s.ImportResult)
def import_archive(
    request: Request,
    session: DB,
    archive: Annotated[UploadFile, File()],
    seed: Annotated[int, Form()] = 2026,
    tune_share: Annotated[float, Form(ge=0, le=1)] = 0.3,
) -> s.ImportResult:
    try:
        settings = request.app.state.settings
        with unpack_corpus(archive.file, settings.max_file_bytes) as root:
            result = import_corpus(session, root, settings, seed, tune_share)
    except CorpusError as exc:
        raise HTTPException(422, str(exc)) from exc
    return s.ImportResult(
        corpus_version_id=result["version_id"],
        reused=result["reused"],
        documents_imported=result["total"],
        links_imported=result["links"],
    )


@router.post("/working-sets", response_model=s.WorkingSetView, status_code=201)
def create_set(session: DB, body: WorkingSetCreate) -> s.WorkingSetView:
    row = create_working_set(session, body)
    session.commit()
    return working_set_view(session, row)


@router.get("/working-sets", response_model=s.Page[s.WorkingSetSummary])
def working_sets(session: DB, limit: Limit = 50, offset: Offset = 0) -> dict[str, Any]:
    return paginate(
        session,
        select(WorkingSet).order_by(WorkingSet.created_at.desc(), WorkingSet.id),
        limit,
        offset,
    )


@router.get("/working-sets/{id}", response_model=s.WorkingSetView)
def get_set(session: DB, id: UUID) -> s.WorkingSetView:
    row = session.get(WorkingSet, id)
    if row is None:
        raise HTTPException(404, "Рабочий набор не найден")
    return working_set_view(session, row)
