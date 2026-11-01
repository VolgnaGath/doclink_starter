import re
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_user, templates
from app.db import get_session
from app.models import Client, Document, DocumentText, Extraction, Job, ModelConfig, StageTiming
from app.services.extraction import select_card
from app.services.jobs import enqueue
from app.services.model_calls import ProcessingError
from app.services.storage import StorageError, document_mime, save_blob
from app.services.text_extraction import process_text

router = APIRouter(
    prefix="/documents", include_in_schema=False, dependencies=[Depends(require_user)]
)
KINDS = ("договор", "допсоглашение", "счёт", "акт", "УПД")
STATUSES = {
    "new": "Новый",
    "text": "Текст получен",
    "extracted": "Реквизиты извлечены",
    "linked": "Связан",
    "review": "На разборе",
    "manual": "Ручная обработка",
    "error": "Ошибка обработки",
}
PAGE_SIZE = 25


def database(request: Request, user: str = Depends(require_user)) -> Iterator[Session]:
    with get_session(request.app.state.settings.database_url) as session:
        yield session


DB = Annotated[Session, Depends(database)]


def parse_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(422, "Некорректный идентификатор") from exc


def render(request: Request, name: str, **context: object) -> Response:
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={"user": request.session["user"], "statuses": STATUSES, "kinds": KINDS, **context},
    )


def find_document(session: Session, document_id: UUID, *, lock: bool = False) -> Document:
    query = select(Document).where(Document.id == document_id)
    if lock:
        query = query.with_for_update()
    document = session.scalar(query)
    if document is None:
        raise HTTPException(404, "Документ не найден")
    return document


@router.get("")
def document_list(
    request: Request,
    session: DB,
    client_id: str = "",
    kind: str = "",
    status: str = "",
    split: str = "",
    page: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    if (
        kind
        and kind not in KINDS
        or status
        and status not in STATUSES
        or split not in {"", "tune", "test"}
    ):
        raise HTTPException(422, "Некорректный фильтр")
    filters = []
    if client_id:
        filters.append(Document.client_id == parse_uuid(client_id))
    if kind:
        filters.append(Document.doc_kind == kind)
    if status:
        filters.append(Document.status == status)
    if split:
        filters.append(Document.split == split)
    total = session.scalar(select(func.count()).select_from(Document).where(*filters)) or 0
    documents = session.execute(
        select(Document, Client)
        .join(Client, Document.client_id == Client.id)
        .where(*filters)
        .order_by(Document.created_at.desc(), Document.id)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    return render(
        request,
        "documents.html",
        documents=documents,
        clients=session.scalars(select(Client).order_by(Client.name)).all(),
        total=total,
        page=page,
        pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        selected_client=client_id,
        selected_kind=kind,
        selected_status=status,
        selected_split=split,
    )


@router.get("/upload")
def upload_form(request: Request, session: DB, client_id: str = "") -> Response:
    return render(
        request,
        "document_upload.html",
        selected_client=client_id,
        clients=session.scalars(select(Client).order_by(Client.name)).all(),
        max_mb=request.app.state.settings.max_file_bytes // (1024 * 1024),
    )


@router.post("/clients")
def create_client(
    session: DB,
    name: Annotated[str, Form()],
    inn: Annotated[str, Form()] = "",
) -> Response:
    name, inn = name.strip(), inn.strip()
    if not name or len(name) > 300:
        raise HTTPException(422, "Укажите название клиента (до 300 символов)")
    if inn and not re.fullmatch(r"\d{10}|\d{12}", inn):
        raise HTTPException(422, "ИНН должен содержать 10 или 12 цифр")
    client = Client(name=name, inn=inn or None, namespace="upload")
    session.add(client)
    session.commit()
    return RedirectResponse(f"/documents/upload?client_id={client.id}", status_code=303)


@router.post("/upload")
def upload_document(
    request: Request,
    session: DB,
    client_id: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
) -> Response:
    if len(files) != 1:
        raise HTTPException(422, "Загрузите ровно один файл")
    client = session.get(Client, parse_uuid(client_id))
    if client is None:
        raise HTTPException(422, "Клиент не найден")
    upload = files[0]
    filename = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    settings = request.app.state.settings
    try:
        mime = document_mime(filename)
        blob = save_blob(upload.file, Path(settings.files_dir), settings.max_file_bytes)
    except StorageError as exc:
        raise HTTPException(422, str(exc)) from exc
    document = Document(
        client_id=client.id,
        source="upload",
        filename=filename,
        sha256=blob.sha256,
        storage_path=str(blob.path),
        mime=mime,
    )
    session.add(document)
    session.flush()
    process_text(session, document, settings)
    if document.status == "text":
        enqueue(session, document)
    session.commit()
    return RedirectResponse(f"/documents/{document.id}", status_code=303)


@router.get("/{document_id}")
def document_detail(request: Request, session: DB, document_id: UUID) -> Response:
    document = find_document(session, document_id)
    return render(
        request,
        "document_detail.html",
        document=document,
        client=session.get(Client, document.client_id),
        text=session.get(DocumentText, document_id),
        extractions=session.scalars(
            select(Extraction)
            .where(Extraction.document_id == document_id)
            .order_by(Extraction.created_at.desc())
        ).all(),
        models=session.scalars(select(ModelConfig).where(ModelConfig.active)).all(),
        jobs=session.scalars(
            select(Job).where(Job.document_id == document_id).order_by(Job.run_after.desc())
        ).all(),
        timings=session.scalars(
            select(StageTiming)
            .where(StageTiming.document_id == document_id)
            .order_by(StageTiming.started_at, StageTiming.id)
        ).all(),
    )


@router.get("/{document_id}/original")
def original(session: DB, document_id: UUID) -> Response:
    document = find_document(session, document_id)
    if not Path(document.storage_path).is_file():
        raise HTTPException(404, "Оригинал не найден в хранилище")
    return FileResponse(document.storage_path, filename=document.filename, media_type=document.mime)


@router.post("/{document_id}/retry-text")
def retry_text(request: Request, session: DB, document_id: UUID) -> Response:
    document = find_document(session, document_id, lock=True)
    if document.status != "error":
        raise HTTPException(409, "Повтор доступен только для ошибочного документа")
    enqueue(session, document, text_only=True)
    session.commit()
    return RedirectResponse(f"/documents/{document_id}", status_code=303)


@router.post("/{document_id}/extract")
def queue_extraction(session: DB, document_id: UUID, model_id: Annotated[UUID, Form()]) -> Response:
    document = find_document(session, document_id)
    model = session.get(ModelConfig, model_id)
    if model is None or session.get(DocumentText, document_id) is None:
        raise HTTPException(422, "Нет модели или текста")
    try:
        enqueue(session, document, [model], retry=True)
    except ProcessingError as exc:
        raise HTTPException(409, str(exc)) from exc
    session.commit()
    return RedirectResponse(f"/documents/{document_id}", status_code=303)


@router.post("/{document_id}/card")
def choose_card(session: DB, document_id: UUID, extraction_id: Annotated[UUID, Form()]) -> Response:
    find_document(session, document_id)
    result = session.get(Extraction, extraction_id)
    if result is None or result.document_id != document_id or not result.valid:
        raise HTTPException(422, "Извлечение недоступно")
    select_card(session, result)
    session.commit()
    return RedirectResponse(f"/documents/{document_id}", status_code=303)
