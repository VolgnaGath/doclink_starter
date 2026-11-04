"""Authenticated experiment adapters; the queue performs local work asynchronously."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import DB, require_api_user
from app.api_schemas import ErrorBody
from app.models import ExperimentMetric, ExperimentRun, ExperimentSeries, ThresholdCurve
from app.services.experiment_export import export
from app.services.experiments import ExperimentCreate, create_series, runs

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_user)],
    responses={code: {"model": ErrorBody} for code in (401, 404, 409, 422, 500)},
)


class MetricView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    split: Literal["tune", "test"]
    slice: str
    metric: str
    value: float | None
    numerator: int | None
    denominator: int | None


class CurveView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    split: Literal["tune"]
    purpose: Literal["tuning"]
    values: dict[str, Any]


class RunView(BaseModel):
    id: UUID
    variant: str
    method: Literal["rules", "semantic", "combined"]
    ablation: Literal["none", "no_llm", "no_checks", "no_sem"]
    model_config_id: UUID | None
    status: Literal[
        "queued", "frozen", "running", "done", "error", "NO_FEASIBLE_CONFIGURATION", "BLOCKED"
    ]
    progress: int
    total: int
    config: dict[str, Any]
    snapshot: dict[str, Any]
    protocol_hash: str | None
    reused_test: bool
    test_started_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    report: dict[str, Any]
    metrics: list[MetricView]
    threshold_curves: list[CurveView]


class ExperimentView(BaseModel):
    id: UUID
    name: str
    corpus_version_id: UUID
    status: Literal["queued", "running", "done", "error"]
    mode: Literal["matrix", "single", "ablations"]
    selected_model_id: UUID | None
    progress: int
    total: int
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    dataset_hash: str | None
    runs: list[RunView]


def series(session: Session, id: UUID) -> ExperimentSeries:
    row = session.get(ExperimentSeries, id)
    if row is None:
        raise HTTPException(404, "Серия экспериментов не найдена")
    return row


def run_view(session: Session, run: ExperimentRun) -> RunView:
    fields = {
        key: getattr(run, key)
        for key in RunView.model_fields
        if key not in {"metrics", "threshold_curves"}
    }
    return RunView(
        **fields,
        metrics=[
            MetricView.model_validate(m)
            for m in session.scalars(
                select(ExperimentMetric)
                .where(ExperimentMetric.run_id == run.id)
                .order_by(ExperimentMetric.split, ExperimentMetric.slice, ExperimentMetric.metric)
            )
        ],
        threshold_curves=[
            CurveView.model_validate(c)
            for c in session.scalars(
                select(ThresholdCurve)
                .where(ThresholdCurve.run_id == run.id)
                .order_by(ThresholdCurve.index)
            )
        ],
    )


def view(session: Session, row: ExperimentSeries) -> ExperimentView:
    children = [run_view(session, r) for r in runs(session, row.id)]
    return ExperimentView(
        id=row.id,
        name=row.name,
        corpus_version_id=row.corpus_version_id,
        status=row.status,
        mode=row.request["mode"],
        selected_model_id=row.selected_model_id,
        progress=sum(r.progress for r in children),
        total=sum(r.total for r in children),
        error=row.error,
        created_at=row.created_at,
        finished_at=row.finished_at,
        dataset_hash=row.dataset.get("dataset_hash"),
        runs=children,
    )


@router.post("/experiments", status_code=202, response_model=ExperimentView)
def create(body: ExperimentCreate, session: DB) -> ExperimentView:
    row = create_series(session, body)
    session.commit()
    return view(session, row)


@router.get("/experiments/{id}", response_model=ExperimentView)
def detail(id: UUID, session: DB) -> ExperimentView:
    return view(session, series(session, id))


@router.get(
    "/experiments/{id}/export",
    response_model=None,
    responses={
        200: {
            "content": {
                media: {"schema": {"type": "string", "format": "binary"}}
                for media in ("text/csv", "text/markdown", "image/png")
            }
        }
    },
)
def download(id: UUID, session: DB, format: Literal["csv", "md", "png"] = Query(...)) -> Response:
    row = series(session, id)
    content, media_type = export(row.dataset, format)
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="experiment-{id}.{format}"',
            "ETag": f'"{row.dataset["dataset_hash"]}"',
        },
    )
