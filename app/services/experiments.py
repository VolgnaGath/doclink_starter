"""Durable local experiment executor. Evaluation metadata never crosses the engine seam.

One recoverable jobs entry owns a series. Each document/result/progress commit is
atomic; an interrupted worker resumes from persisted associations, including errors.
"""

import hashlib
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_engine, get_session
from app.linking_acceptance import (
    annotation_hash,
    embedding_audit,
    incoming,
    targets,
)
from app.linking_acceptance import (
    code_hash as linking_code_hash,
)
from app.models import (
    CorpusVersion,
    Document,
    ExperimentResult,
    ExperimentRun,
    ExperimentSeries,
    Extraction,
    Job,
    ModelConfig,
    StageTiming,
    ThresholdCurve,
)
from app.models import LinkingResult as StoredResult
from app.services.embedding_store import encoding_config
from app.services.extraction import lock_number, prepared_request
from app.services.extraction_protocols import test_history
from app.services.linking import link
from app.services.linking.types import LinkingConfig, LinkingInput
from app.services.linking_acceptance import TuneDocument, select_thresholds
from app.services.linking_store import _save, linking_payload
from app.services.model_calls import ProcessingError, fingerprint, request_hash

ROOT = Path(__file__).resolve().parents[2]
MODEL_ORDER = ("Qwen", "YandexGPT 5.1 Pro", "GigaChat 2 Lite")
TERMINAL = {"done", "NO_FEASIBLE_CONFIGURATION", "BLOCKED"}


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    corpus_version_id: UUID
    idempotency_key: UUID
    mode: Literal["matrix", "single", "ablations"] = "matrix"
    method: Literal["rules", "semantic", "combined"] = "combined"
    model_config_id: UUID | None = None
    ablation: Literal["none", "no_llm", "no_checks", "no_sem"] = "none"

    @model_validator(mode="after")
    def coherent(self) -> "ExperimentCreate":
        if not self.name.strip():
            raise ValueError("Укажите название")
        if self.mode == "single":
            if self.method != "combined" and (self.ablation != "none" or self.model_config_id):
                raise ValueError("Baselines не зависят от выбранной LLM и не имеют абляций")
            if self.method == "combined" and self.model_config_id is None:
                raise ValueError("Выберите модель combined")
        elif self.model_config_id or self.ablation != "none" or self.method != "combined":
            raise ValueError("Матрица использует исходный протокол и выбор модели только на tune")
        return self


def code_hash() -> str:
    files = [ROOT / "docs/EXPERIMENTS.md", *sorted((ROOT / "app/services").glob("experiment*.py"))]
    return fingerprint(
        {
            "linking": linking_code_hash(),
            **{str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        }
    )


def git_commit() -> str:
    built = os.environ.get("DOCLINK_GIT_COMMIT", "unknown")
    if built != "unknown":
        return built
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        raise ProcessingError(
            "Не удалось зафиксировать git commit; запускайте через Makefile"
        ) from None


def runs(session: Session, series_id: UUID) -> list[ExperimentRun]:
    return list(
        session.scalars(
            select(ExperimentRun)
            .where(ExperimentRun.series_id == series_id)
            .order_by(ExperimentRun.variant)
        )
    )


def create_series(session: Session, request: ExperimentCreate) -> ExperimentSeries:
    session.execute(text("SELECT pg_advisory_xact_lock(60806001)"))
    key = fingerprint(request.model_dump(mode="json"))
    previous = session.scalar(
        select(ExperimentSeries).where(ExperimentSeries.idempotency_key == request.idempotency_key)
    )
    if previous:
        if previous.request_hash != key:
            raise ProcessingError("Ключ идемпотентности использован с другим запросом")
        return previous
    version = session.get(CorpusVersion, request.corpus_version_id)
    if version is None:
        raise ProcessingError("Версия корпуса не найдена")
    documents = incoming(session, version.id)
    if not documents or not any(d.split == "tune" for d in documents):
        raise ProcessingError("Нужен корпус с входящими tune-документами")
    configurations: list[tuple[str, UUID | None, str]] = []
    if request.mode == "single":
        if request.model_config_id and session.get(ModelConfig, request.model_config_id) is None:
            raise ProcessingError("Модель не найдена")
        configurations.append((request.method, request.model_config_id, request.ablation))
    else:
        if request.mode == "matrix":
            configurations.extend((method, None, "none") for method in ("rules", "semantic"))
        for name in MODEL_ORDER:
            model = session.scalar(select(ModelConfig).where(ModelConfig.name == name))
            if model is None:
                raise ProcessingError(f"Модель {name} не найдена")
            configurations.append(("combined", model.id, "none"))
    series = ExperimentSeries(
        name=request.name.strip(),
        corpus_version_id=version.id,
        idempotency_key=request.idempotency_key,
        request_hash=key,
        request={**request.model_dump(mode="json"), "git_commit": git_commit()},
    )
    session.add(series)
    session.flush()
    for method, model_id, ablation in configurations:
        _add_run(session, series.id, method, model_id, ablation)
    job = Job(
        type=f"experiment:{series.id}",
        document_id=documents[0].id,
        payload={"series_id": str(series.id)},
    )
    session.add(job)
    session.flush()
    series.job_id = job.id
    return series


def _add_run(
    session: Session, series_id: UUID, method: str, model_id: UUID | None, ablation: str
) -> ExperimentRun:
    name = session.get(ModelConfig, model_id).name if model_id else "shared"
    run = ExperimentRun(
        series_id=series_id,
        method=method,
        model_config_id=model_id,
        ablation=ablation,
        variant=f"{method}/{name}/{ablation}",
    )
    session.add(run)
    session.flush()
    return run


def _template(run: ExperimentRun) -> LinkingConfig:
    return LinkingConfig(
        method=run.method,
        tau_a=0,
        tau_r=0,
        checks=run.ablation != "no_checks",
        use_semantic=run.ablation != "no_sem",
    )


def snapshot(
    session: Session,
    settings: Settings,
    series: ExperimentSeries,
    run: ExperimentRun,
    config: LinkingConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    version = session.get(CorpusVersion, series.corpus_version_id)
    docs = incoming(session, version.id)
    all_docs = list(
        session.scalars(
            select(Document).where(Document.corpus_version_id == version.id).order_by(Document.id)
        )
    )
    encoding = encoding_config(settings)
    # Whole-version audit, not merely existence of an arbitrary old vector.
    embeddings = embedding_audit(session, version.id, encoding)
    selected = (
        run.model_config_id if run.method == "combined" and run.ablation != "no_llm" else None
    )
    extraction_rows = []
    model_snapshots = {}
    extraction_objects = []
    model_ids = {d.card_model_config_id for d in all_docs if d.card_model_config_id}
    if run.ablation == "no_llm":
        model_ids = set()
    if selected:
        model_ids.add(selected)
    for model_id in sorted(model_ids):
        model = session.get(ModelConfig, model_id)
        for doc in all_docs:
            extraction = session.scalar(
                select(Extraction).where(
                    Extraction.document_id == doc.id,
                    Extraction.model_config_id == model_id,
                    Extraction.is_current,
                )
            )
            if extraction is None:
                raise ProcessingError(f"Preflight: извлечение отсутствует: {doc.id}/{model.name}")
            if model.provider != "regex":
                if extraction.input_hash is None or extraction.llm_call_id is None:
                    raise ProcessingError("Preflight: нет происхождения реального извлечения")
                messages, _, _, _ = prepared_request(session, doc, extraction.snapshot)
                if (
                    request_hash(extraction.snapshot, messages, doc.client_id)
                    != extraction.input_hash
                ):
                    raise ProcessingError(
                        "Preflight: извлечение не соответствует фактическому входу"
                    )
                if not extraction.valid and not extraction.preserved_error:
                    raise ProcessingError("Preflight: ошибка не закреплена исходным протоколом")
            extraction_rows.append(
                {
                    "id": str(extraction.id),
                    "document_id": str(doc.id),
                    "model_id": str(model_id),
                    "input_hash": extraction.input_hash,
                    "snapshot_hash": fingerprint(extraction.snapshot),
                    "valid": extraction.valid,
                    "error": extraction.error,
                    "fields_hash": fingerprint(
                        {
                            column.name: str(getattr(extraction, column.name))
                            for column in Extraction.__table__.columns
                        }
                    ),
                }
            )
            model_snapshots[fingerprint(extraction.snapshot)] = extraction.snapshot
            extraction_objects.append(extraction)
    payloads = {
        str(d.id): linking_payload(
            session, d.id, selected, config, encoding, no_llm=run.ablation == "no_llm"
        )
        for d in docs
    }
    frozen = {
        "version": "stage6-v1",
        "corpus_version": str(version.id),
        "corpus_fingerprint": version.fingerprint,
        "split_seed": version.seed,
        "git_commit": series.request["git_commit"],
        "source_code_hash": code_hash(),
        "config": config.model_dump(mode="json"),
        "ablation": run.ablation,
        "model_id": str(selected) if selected else None,
        "encoding": encoding.model_dump(mode="json"),
        "embedding_input_hash": embeddings["input_hash"],
        "input_hashes": {id: p["input_hash"] for id, p in payloads.items()},
        "annotation_hash": annotation_hash(all_docs),
        "extractions": extraction_rows,
        "model_snapshots": model_snapshots,
        "prior_test_history": test_history(session, version),
        "prior_linking_test": sorted(
            k
            for k, p in version.report.get("linking_acceptance_protocols", {}).items()
            if p.get("test_started")
        ),
    }
    from app.services.experiment_reporting import physical_calls

    calls = physical_calls(session, extraction_objects)
    frozen["source_calls_hash"] = fingerprint(
        [
            {
                "id": str(c.id),
                "status": c.status,
                "cost": str(c.cost_rub),
                "input_tokens": c.input_tokens,
                "output_tokens": c.output_tokens,
                "latency_ms": c.latency_ms,
                "snapshot": c.snapshot,
                "response_hash": hashlib.sha256((c.response_raw or "").encode()).hexdigest(),
            }
            for c in calls
        ]
    )
    return frozen, payloads


def tune_run(settings: Settings, run_id: UUID) -> None:
    with get_session(settings.database_url) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        run = session.get(ExperimentRun, run_id)
        if run.status != "queued":
            return
        series = session.get(ExperimentSeries, run.series_id)
        frozen, payloads = snapshot(session, settings, series, run, _template(run))
        docs = incoming(session, series.corpus_version_id)
        gold = targets(session, series.corpus_version_id, docs)
        tuning = select_thresholds(
            tuple(
                TuneDocument(LinkingInput.model_validate(payloads[str(d.id)]["data"]), gold[d.id])
                for d in docs
                if d.split == "tune"
            ),
            _template(run),
        )
        run.started_at = datetime.now(UTC)
        run.report = {"tuning": tuning}
        for i, point in enumerate(tuning["tau_a_curve"]):
            session.add(ThresholdCurve(run_id=run.id, index=i, values=point))
        if tuning["config"] is None:
            run.status, run.error = (
                "NO_FEASIBLE_CONFIGURATION",
                "Нет τ_a с Precision≥0,98 и M>0 на tune",
            )
            run.snapshot = frozen
            run.protocol_hash = fingerprint(frozen)
            run.finished_at = datetime.now(UTC)
        else:
            config = LinkingConfig.model_validate(tuning["config"])
            # Changing thresholds cannot change the prepared pool. Rehash each complete payload.
            for payload in payloads.values():
                payload["config"] = config.model_dump(mode="json")
                payload["input_hash"] = fingerprint(
                    {k: v for k, v in payload.items() if k != "input_hash"}
                )
            frozen["config"] = config.model_dump(mode="json")
            frozen["input_hashes"] = {id: p["input_hash"] for id, p in payloads.items()}
            run.config, run.snapshot, run.protocol_hash = (
                frozen["config"],
                frozen,
                fingerprint(frozen),
            )
            run.total, run.status = len(docs), "frozen"
        version = session.get(CorpusVersion, series.corpus_version_id)
        run.reused_test = bool(
            test_history(session, version)
            or frozen["prior_linking_test"]
            or session.scalar(
                select(ExperimentRun.id)
                .join(ExperimentSeries)
                .where(
                    ExperimentSeries.corpus_version_id == series.corpus_version_id,
                    ExperimentRun.test_started_at.is_not(None),
                )
                .limit(1)
            )
        )
        from app.services.experiment_reporting import record_inputs

        record_inputs(session, run)
        session.commit()


def validate_run(session: Session, settings: Settings, run: ExperimentRun) -> dict[str, Any]:
    series = session.get(ExperimentSeries, run.series_id)
    current, payloads = snapshot(
        session, settings, series, run, LinkingConfig.model_validate(run.config)
    )
    if fingerprint(run.snapshot) != run.protocol_hash or current != run.snapshot:
        raise ProcessingError(
            "Входы/код/разметка изменились после freeze; создайте новую серию (reused_test)"
        )
    return payloads


def _execute_run(settings: Settings, run_id: UUID) -> None:
    with get_session(settings.database_url) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        run = session.get(ExperimentRun, run_id)
        payloads = validate_run(session, settings, run)
        run.status = "running"
        run.test_started_at = run.test_started_at or datetime.now(UTC)
        session.commit()  # Durable before the first test document is evaluated.
    for document_id, payload in payloads.items():
        start, started_at = time.monotonic(), datetime.now(UTC)
        with get_session(settings.database_url) as session:
            if session.get(ExperimentResult, (run_id, UUID(document_id))):
                continue
            # Serializes shared success cache across series and technical link workers.
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": lock_number("link:" + payload["input_hash"])},
            )
            stored = session.scalar(
                select(StoredResult).where(
                    StoredResult.input_hash == payload["input_hash"],
                    StoredResult.outcome != "error",
                )
            )
            if stored is None:
                # Explicit replay of a measured document error from another experiment,
                # never a success-cache hit and never an infrastructure failure fallback.
                stored = session.scalar(
                    select(StoredResult)
                    .join(ExperimentResult, ExperimentResult.result_id == StoredResult.id)
                    .where(
                        StoredResult.input_hash == payload["input_hash"],
                        StoredResult.outcome == "error",
                        StoredResult.job_id.is_(None),
                    )
                    .order_by(StoredResult.id)
                    .limit(1)
                )
            reused = stored is not None
            if stored is None:
                config = LinkingConfig.model_validate(payload["config"])
                result = link(LinkingInput.model_validate(payload["data"]), config)
                stored = _save(
                    session,
                    UUID(document_id),
                    payload,
                    result,
                    None,
                    "; ".join(result.reasons) if result.outcome == "error" else None,
                )
            timing = StageTiming(
                document_id=UUID(document_id),
                stage="link",
                started_at=started_at,
                duration_ms=int((time.monotonic() - start) * 1000),
                ok=stored.outcome != "error",
                error=stored.error,
            )
            session.add(timing)
            session.flush()
            session.add(
                ExperimentResult(
                    run_id=run_id,
                    document_id=UUID(document_id),
                    result_id=stored.id,
                    timing_id=timing.id,
                    reused=reused,
                )
            )
            run = session.get(ExperimentRun, run_id)
            run.progress += 1
            session.commit()
    with get_session(settings.database_url) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        run = session.get(ExperimentRun, run_id)
        validate_run(session, settings, run)
        from app.services.experiment_reporting import finish_run

        finish_run(session, run)
        run.status, run.finished_at = "done", datetime.now(UTC)
        session.commit()


def execute_series(settings: Settings, series_id: UUID) -> None:
    """Session lock also protects direct CLI use, independent of the queue lease."""
    with get_engine(settings.database_url).connect() as guard:
        key = lock_number("experiment:" + str(series_id))
        if not guard.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}):
            raise ProcessingError("Серия уже выполняется")
        guard.commit()
        try:
            with get_session(settings.database_url) as session:
                series = session.get(ExperimentSeries, series_id)
                if series is None:
                    raise ProcessingError("Серия не найдена")
                if series.status == "done":
                    return
                series.status, series.error = "running", None
                ids = [r.id for r in runs(session, series_id) if r.ablation == "none"]
                # Single ablation has no base runs.
                if series.request["mode"] == "single":
                    ids = [r.id for r in runs(session, series_id)]
                session.commit()
            for id in ids:
                tune_run(settings, id)
            with get_session(settings.database_url) as session:
                series = session.get(ExperimentSeries, series_id)
                if series.request["mode"] != "single":
                    eligible = [
                        r
                        for r in runs(session, series_id)
                        if r.method == "combined" and r.ablation == "none" and r.config
                    ]
                    selected = max(
                        eligible,
                        key=lambda r: (
                            r.report["tuning"]["selected_tune_metrics"]["coverage"],
                            r.report["tuning"]["selected_tune_metrics"]["precision"],
                            -MODEL_ORDER.index(session.get(ModelConfig, r.model_config_id).name),
                        ),
                        default=None,
                    )
                    series.selected_model_id = selected.model_config_id if selected else None
                    if not any(r.ablation != "none" for r in runs(session, series_id)):
                        for ablation in ("no_llm", "no_checks", "no_sem"):
                            run = _add_run(
                                session, series_id, "combined", series.selected_model_id, ablation
                            )
                            if selected is None:
                                run.status, run.error = (
                                    "BLOCKED",
                                    "Нет допустимой combined-модели на tune",
                                )
                    session.commit()
                ids = [r.id for r in runs(session, series_id) if r.status == "queued"]
            for id in ids:
                tune_run(settings, id)
            with get_session(settings.database_url) as session:
                ids = [r.id for r in runs(session, series_id) if r.status not in TERMINAL]
            for id in ids:
                _execute_run(settings, id)
            with get_session(settings.database_url) as session:
                from app.services.experiment_reporting import build_dataset

                series = session.get(ExperimentSeries, series_id)
                series.dataset = build_dataset(session, series)
                series.status, series.finished_at = "done", datetime.now(UTC)
                session.commit()
        except Exception as exc:
            with get_session(settings.database_url) as session:
                series = session.get(ExperimentSeries, series_id)
                if series:
                    series.status = "error"
                    series.error = (
                        str(exc)
                        if isinstance(exc, ProcessingError)
                        else f"Ошибка эксперимента: {type(exc).__name__}"
                    )
                    for run in runs(session, series_id):
                        if run.status in {"running", "frozen"}:
                            run.status, run.error = "error", series.error
                    session.commit()
            raise
        finally:
            guard.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            guard.commit()


def retry_series(session: Session, series_id: UUID) -> ExperimentSeries:
    """Explicit retry preserves freeze and already committed outcomes, including errors."""
    series = session.get(ExperimentSeries, series_id, with_for_update=True)
    if series is None:
        raise ProcessingError("Серия не найдена")
    if series.status == "error":
        job = session.get(Job, series.job_id, with_for_update=True)
        job.status, job.error, job.locked_at = "queued", None, None
        series.status, series.error = "queued", None
    return series
