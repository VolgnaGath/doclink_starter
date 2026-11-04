"""Small browser adapter for isolated corpus working copies."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from app.auth import require_user
from app.documents import DB, render
from app.models import CorpusVersion, Document, WorkingCopy, WorkingSet
from app.services.working_sets import WorkingSetCreate, create_working_set

router = APIRouter(
    prefix="/working-sets", include_in_schema=False, dependencies=[Depends(require_user)]
)


@router.get("")
def index(request: Request, session: DB, corpus_version_id: UUID | None = None) -> Response:
    versions = list(
        session.scalars(select(CorpusVersion).order_by(CorpusVersion.imported_at.desc()))
    )
    chosen = corpus_version_id or (versions[0].id if versions else None)
    documents = (
        list(
            session.scalars(
                select(Document)
                .where(
                    Document.corpus_version_id == chosen,
                    Document.source == "corpus",
                    Document.doc_kind.is_distinct_from("договор"),
                )
                .order_by(Document.filename)
            )
        )
        if chosen
        else []
    )
    return render(
        request,
        "working_sets.html",
        versions=versions,
        chosen=chosen,
        documents=documents,
        sets=session.scalars(select(WorkingSet).order_by(WorkingSet.created_at.desc())).all(),
        key=uuid4(),
    )


@router.post("")
def create(
    session: DB,
    corpus_version_id: Annotated[UUID, Form()],
    document_ids: Annotated[list[UUID], Form()],
    idempotency_key: Annotated[UUID, Form()],
    name: Annotated[str, Form()] = "Проверка корпуса",
) -> Response:
    work = create_working_set(
        session,
        WorkingSetCreate(
            name=name,
            corpus_version_id=corpus_version_id,
            document_ids=document_ids,
            idempotency_key=idempotency_key,
        ),
    )
    session.commit()
    return RedirectResponse(f"/working-sets/{work.id}", status_code=303)


@router.get("/{id}")
def show(request: Request, session: DB, id: UUID) -> Response:
    work = session.get(WorkingSet, id)
    if work is None:
        raise HTTPException(404, "Рабочий набор не найден")
    rows = session.execute(
        select(Document, WorkingCopy)
        .join(WorkingCopy, WorkingCopy.document_id == Document.id)
        .where(WorkingCopy.working_set_id == id)
        .order_by(Document.doc_kind, Document.number)
    ).all()
    return render(request, "working_set.html", work=work, rows=rows)
