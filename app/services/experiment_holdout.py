"""Evaluation-only transfer of an already frozen method to a disjoint test corpus."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    CorpusVersion,
    Document,
    ExperimentRun,
    ExperimentSeries,
    Job,
    ModelConfig,
    Prompt,
)
from app.services.embedding_store import encoding_config
from app.services.extraction import configuration
from app.services.model_calls import ProcessingError, fingerprint


def registration(
    session: Session, settings: Settings, version_id: UUID, source_id: UUID
) -> dict[str, Any]:
    from app.services.experiments import code_hash, runs

    source = session.get(ExperimentSeries, source_id)
    version = session.get(CorpusVersion, version_id)
    if source is None or source.status != "done" or source.request["mode"] != "matrix":
        raise ProcessingError("Нужна завершённая исходная матрица")
    docs = list(session.scalars(select(Document).where(Document.corpus_version_id == version_id)))
    if version is None or not docs or any(d.split != "test" for d in docs):
        raise ProcessingError("Нужен отдельный корпус целиком split=test")
    if source.corpus_version_id == version_id:
        raise ProcessingError("Holdout должен отличаться от исходного корпуса")
    # Relabeling an already seen document does not make it a new test.
    others = list(
        session.scalars(
            select(Document).where(
                Document.corpus_version_id.is_not(None), Document.corpus_version_id != version_id
            )
        )
    )
    seen_ids = {d.corpus_doc_id for d in others}
    seen_texts = {d.sha256 for d in others}
    if any(d.corpus_doc_id in seen_ids or d.sha256 in seen_texts for d in docs):
        raise ProcessingError("Holdout пересекается с прежним корпусом по ID или тексту")
    prompt = session.scalar(
        select(Prompt).where(Prompt.name == "extract").order_by(Prompt.created_at.desc()).limit(1)
    )
    source_runs = runs(session, source_id)
    historical = {
        fingerprint(s): s
        for r in source_runs
        for s in r.snapshot.get("model_snapshots", {}).values()
    }
    models = []
    for model_id in sorted({r.model_config_id for r in source_runs if r.model_config_id}):
        model = session.get(ModelConfig, model_id)
        current = configuration(model, prompt)
        if fingerprint(current) not in historical:
            raise ProcessingError(f"Модель/промпт изменились: {model.name}")
        models.append(current)
    encoding = encoding_config(settings).model_dump(mode="json")
    if any(r.snapshot.get("encoding") != encoding for r in source_runs):
        raise ProcessingError("Изменился эмбеддер")
    content = {
        "version": "holdout-v1",
        "source_series_id": str(source_id),
        "source_dataset_hash": source.dataset["dataset_hash"],
        "selected_model_id": str(source.selected_model_id),
        "corpus_fingerprint": version.fingerprint,
        "source_code_hash": code_hash(),
        "models": models,
        "encoding": encoding,
        "variants": [
            {
                "source_run_id": str(r.id),
                "protocol_hash": r.protocol_hash,
                "variant": r.variant,
                "method": r.method,
                "model_id": str(r.model_config_id) if r.model_config_id else None,
                "ablation": r.ablation,
                "config": r.config,
                "status": r.status,
            }
            for r in source_runs
        ],
    }
    previous = version.report.get("holdout_registration")
    if previous:
        if {k: previous[k] for k in content} != content:
            raise ProcessingError("Регистрация holdout изменилась; прежнюю не перезаписывать")
        return previous
    if session.scalar(
        select(ExperimentRun.id)
        .join(ExperimentSeries)
        .where(
            ExperimentSeries.corpus_version_id == version_id,
            ExperimentRun.test_started_at.is_not(None),
        )
        .limit(1)
    ):
        raise ProcessingError("Holdout уже просмотрен до регистрации")
    saved = {**content, "registered_at": datetime.now(UTC).isoformat()}
    saved["registration_hash"] = fingerprint(saved)
    version.report = {**version.report, "holdout_registration": saved}
    return saved


def create_holdout_series(
    session: Session, settings: Settings, version_id: UUID, source_id: UUID
) -> ExperimentSeries:
    from app.services.experiment_reporting import record_inputs
    from app.services.experiments import _add_run, git_commit, snapshot
    from app.services.linking.types import LinkingConfig

    session.execute(text("SELECT pg_advisory_xact_lock(60806001)"))
    saved = registration(session, settings, version_id, source_id)
    key = uuid5(version_id, saved["registration_hash"])
    previous = session.scalar(
        select(ExperimentSeries).where(ExperimentSeries.idempotency_key == key)
    )
    if previous:
        return previous
    series = ExperimentSeries(
        name="Отложенный test, прежние пороги",
        corpus_version_id=version_id,
        idempotency_key=key,
        request_hash=fingerprint(saved),
        selected_model_id=UUID(saved["selected_model_id"]),
        request={"mode": "holdout", "git_commit": git_commit(), "holdout_registration": saved},
    )
    session.add(series)
    session.flush()
    # All variants are frozen in the same transaction, before a worker can see the job.
    for variant in saved["variants"]:
        run = _add_run(
            session,
            series.id,
            variant["method"],
            UUID(variant["model_id"]) if variant["model_id"] else None,
            variant["ablation"],
        )
        run.started_at = datetime.now(UTC)
        run.report = {"threshold_source_run_id": variant["source_run_id"], "retuned": False}
        if variant["status"] != "done":
            run.status = variant["status"]
            run.error = "В исходном tune нет допустимой конфигурации; пороги не назначаются"
            run.finished_at = datetime.now(UTC)
            continue
        run.config = variant["config"]
        frozen, payloads = snapshot(
            session, settings, series, run, LinkingConfig.model_validate(run.config)
        )
        allowed = {fingerprint(s) for s in saved["models"]}
        if not set(frozen["model_snapshots"]) <= allowed:
            raise ProcessingError("Извлечения отличаются от зарегистрированных моделей/промпта")
        run.snapshot, run.protocol_hash = frozen, fingerprint(frozen)
        run.status, run.total, run.reused_test = "frozen", len(payloads), False
        record_inputs(session, run)
    document = session.scalar(
        select(Document).where(Document.corpus_version_id == version_id).order_by(Document.id)
    )
    job = Job(
        type=f"experiment:{series.id}",
        document_id=document.id,
        payload={"series_id": str(series.id)},
    )
    session.add(job)
    session.flush()
    series.job_id = job.id
    return series
