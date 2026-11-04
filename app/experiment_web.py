"""Russian server-rendered matrix controls and comparisons, using shared services."""

from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from app.api import search_view
from app.auth import require_user
from app.documents import DB, render
from app.experiment_api import download, run_view, series, view
from app.models import CorpusVersion, ExperimentRun, ExperimentSeries, LinkingResult, ModelConfig
from app.services.experiments import ExperimentCreate, create_series, retry_series
from app.services.working_sets import WorkingSetCreate, create_working_set

router = APIRouter(
    prefix="/experiments", include_in_schema=False, dependencies=[Depends(require_user)]
)


@router.get("")
def index(request: Request, session: DB) -> Response:
    return render(
        request,
        "experiments.html",
        key=uuid4(),
        series=session.scalars(
            select(ExperimentSeries).order_by(ExperimentSeries.created_at.desc())
        ).all(),
        versions=session.scalars(
            select(CorpusVersion).order_by(CorpusVersion.imported_at.desc())
        ).all(),
        models=session.scalars(
            select(ModelConfig).where(ModelConfig.active).order_by(ModelConfig.name)
        ).all(),
        runs=session.scalars(select(ExperimentRun).order_by(ExperimentRun.variant)).all(),
    )


@router.post("")
def create(
    session: DB,
    name: Annotated[str, Form()],
    corpus_version_id: Annotated[UUID, Form()],
    idempotency_key: Annotated[UUID, Form()],
    mode: Annotated[str, Form()] = "matrix",
    method: Annotated[str, Form()] = "combined",
    model_config_id: Annotated[str, Form()] = "",
    ablation: Annotated[str, Form()] = "none",
) -> Response:
    body = ExperimentCreate(
        name=name,
        corpus_version_id=corpus_version_id,
        idempotency_key=idempotency_key,
        mode=mode,
        method=method if mode == "single" else "combined",
        model_config_id=UUID(model_config_id) if model_config_id and mode == "single" else None,
        ablation=ablation if mode == "single" else "none",
    )
    row = create_series(session, body)
    session.commit()
    return RedirectResponse(f"/experiments/{row.id}", status_code=303)


@router.get("/compare")
def compare(
    request: Request,
    session: DB,
    run_ids: Annotated[list[UUID], Query(min_length=1, max_length=24)],
) -> Response:
    selected = session.scalars(
        select(ExperimentRun).where(ExperimentRun.id.in_(run_ids)).order_by(ExperimentRun.variant)
    ).all()
    return render(request, "experiment_compare.html", runs=[run_view(session, r) for r in selected])


@router.get("/{id}")
def show(request: Request, session: DB, id: UUID) -> Response:
    row = series(session, id)
    return render(request, "experiment.html", experiment=view(session, row))


@router.get("/results/{id}")
def result(request: Request, session: DB, id: UUID) -> Response:
    row = session.get(LinkingResult, id)
    if row is None:
        raise HTTPException(404, "Исход не найден")
    return render(
        request,
        "experiment_result.html",
        result=search_view(session, row).model_dump(mode="json"),
        key=uuid4(),
    )


@router.post("/results/{id}/working-copy")
def working_copy(session: DB, id: UUID, idempotency_key: Annotated[UUID, Form()]) -> Response:
    from app.models import Document

    row = session.get(LinkingResult, id)
    if row is None:
        raise HTTPException(404, "Исход не найден")
    document = session.get(Document, row.source_id)
    work = create_working_set(
        session,
        WorkingSetCreate(
            name="Проверка результата эксперимента",
            corpus_version_id=document.corpus_version_id,
            document_ids=[row.source_id],
            result_ids=[row.id],
            idempotency_key=idempotency_key,
        ),
    )
    session.commit()
    return RedirectResponse(f"/working-sets/{work.id}", status_code=303)


@router.post("/{id}/retry")
def retry(session: DB, id: UUID) -> Response:
    retry_series(session, id)
    session.commit()
    return RedirectResponse(f"/experiments/{id}", status_code=303)


@router.get("/{id}/export")
def export(id: UUID, session: DB, format: Literal["csv", "md", "png"] = Query(...)) -> Response:
    return download(id, session, format)
