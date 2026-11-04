"""Local matrix acceptance and exports; never invokes extraction or embedding models."""

import argparse
import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.config import Settings
from app.db import get_session
from app.models import (
    Embedding,
    ExperimentSeries,
    Extraction,
    Job,
    LinkingResult,
    LLMCall,
    ModelConfig,
)
from app.services.experiment_export import export
from app.services.experiments import (
    ExperimentCreate,
    create_series,
    execute_series,
    retry_series,
    runs,
    validate_run,
)
from app.services.jobs import work_once
from app.services.model_calls import ProcessingError, fingerprint


def counts(settings: Settings) -> dict[str, int]:
    with get_session(settings.database_url) as session:
        return {
            name: session.scalar(select(func.count()).select_from(model))
            for name, model in (
                ("llm_calls", LLMCall),
                ("embeddings", Embedding),
                ("results", LinkingResult),
            )
        }


def signature(dataset: dict[str, Any]) -> str:
    return fingerprint(
        [
            {k: r.get(k) for k in ("variant", "status", "config", "metric_rows")}
            | {"result_ids": r["report"].get("result_ids"), "controls": r["report"].get("controls")}
            for r in dataset["runs"]
        ]
    )


def audit(settings: Settings, series_id: UUID, baseline_path: Path) -> dict[str, Any]:
    baseline = json.loads(baseline_path.read_text())
    with get_session(settings.database_url) as session:
        series = session.get(ExperimentSeries, series_id)
        if series is None or series.status != "done":
            raise ProcessingError("Для аудита нужна завершённая серия")
        qwen = next(r for r in series.dataset["runs"] if r["variant"] == "combined/Qwen/none")
        comparisons = {
            "config_equal": qwen["config"] == baseline["protocol"]["frozen"]["config"],
            "input_hashes_equal": qwen["snapshot"]["input_hashes"]
            == baseline["protocol"]["frozen"]["input_hashes"],
            "result_ids_equal": qwen["report"]["result_ids"] == baseline["result_ids"],
            "metrics_equal": {
                split: rows["all"] for split, rows in qwen["report"]["metrics"].items()
            }
            == baseline["metrics_by_split"],
            "controls_equal": qwen["report"]["controls"]
            == [
                {k: v for k, v in c.items() if k != "candidates"}
                | {
                    "candidates": [
                        {k: v for k, v in candidate.items() if k != "document"}
                        for candidate in c["candidates"]
                    ]
                }
                for c in baseline["controls"]
            ],
        }
        if not all(comparisons.values()):
            raise ProcessingError("Combined+Qwen не совпал с исторической приёмкой 4.6")
        selected = next(
            r
            for r in series.dataset["runs"]
            if r["model_id"] == str(series.selected_model_id)
            and r["ablation"] == "none"
            and r["method"] == "combined"
        )
        controls = selected["report"]["controls"]
        preserved = list(
            session.scalars(
                select(Extraction).where(
                    Extraction.id.in_(
                        [
                            UUID(e["id"])
                            for r in series.dataset["runs"]
                            for e in r["snapshot"].get("extractions", [])
                        ]
                    ),
                    Extraction.preserved_error,
                )
            )
        )
        costs = [
            {
                "model": model.name,
                "cost_rub": str(
                    session.scalar(
                        select(func.sum(LLMCall.cost_rub)).where(
                            LLMCall.model_config_id == model.id
                        )
                    )
                ),
            }
            for model in session.scalars(
                select(ModelConfig).where(ModelConfig.active).order_by(ModelConfig.name)
            )
        ]
        return {
            "series_id": str(series_id),
            "stage4_comparison": comparisons,
            "selected_variant": selected["variant"],
            "controls_passed": sum(c["passed"] for c in controls),
            "controls_total": len(controls),
            "preserved_errors": [
                {
                    "extraction_id": str(e.id),
                    "source_extraction_id": str(e.source_extraction_id),
                    "llm_call_id": str(e.llm_call_id),
                    "error": e.error,
                }
                for e in preserved
            ],
            "all_physical_llm_calls": session.scalar(select(func.count()).select_from(LLMCall)),
            "all_cost_rub": str(session.scalar(select(func.sum(LLMCall.cost_rub)))),
            "unknown_cost_calls": session.scalar(
                select(func.count()).select_from(LLMCall).where(LLMCall.cost_rub.is_(None))
            ),
            "costs_by_model": costs,
            "active_jobs": session.scalar(
                select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running"]))
            ),
            "paid_calls_enabled": settings.paid_calls_enabled,
            "signature": signature(series.dataset),
        }


def accept(
    settings: Settings,
    version: UUID | None,
    series_id: UUID | None,
    repeat_of: UUID | None,
    mode: str,
    key: UUID | None,
) -> dict[str, Any]:
    before, start = counts(settings), time.monotonic()
    with get_session(settings.database_url) as session:
        reference = session.get(ExperimentSeries, repeat_of) if repeat_of else None
        if repeat_of and (reference is None or reference.status != "done"):
            raise ProcessingError("Для повтора нужна завершённая серия")
        if series_id:
            series = session.get(ExperimentSeries, series_id)
            if series is None:
                raise ProcessingError("Серия не найдена")
            retry_series(session, series_id)
        else:
            if reference:
                request = ExperimentCreate.model_validate(
                    {
                        k: v
                        for k, v in reference.request.items()
                        if k in ExperimentCreate.model_fields
                    }
                ).model_copy(update={"idempotency_key": key or uuid4()})
            else:
                if version is None:
                    raise ProcessingError("Укажите --corpus-version")
                request = ExperimentCreate(
                    name="Матрица этапа 6",
                    corpus_version_id=version,
                    idempotency_key=key or uuid4(),
                    mode=mode,
                )
            series = create_series(session, request)
            series_id = series.id
        session.commit()
    # The same queue handler is used by the normal worker and this controlled drain.
    while work_once(settings, job_types=[f"experiment:{series_id}"]):
        pass
    with get_session(settings.database_url) as session:
        series = session.get(ExperimentSeries, series_id)
        if series.status != "done":
            raise ProcessingError(series.error or "Серия ещё выполняется другим worker")
        for run in runs(session, series_id):
            if run.status == "done":
                validate_run(session, settings, run)
        dataset = series.dataset
        reference = session.get(ExperimentSeries, repeat_of) if repeat_of else None
        repeated_equal = signature(reference.dataset) == signature(dataset) if reference else None
        if repeated_equal is False:
            raise ProcessingError("Повтор не совпадает с исходной серией")
    after = counts(settings)
    if after["llm_calls"] != before["llm_calls"] or after["embeddings"] != before["embeddings"]:
        raise ProcessingError("Локальная матрица изменила число физических вызовов/векторов")
    return {
        "series_id": str(series_id),
        "dataset_hash": dataset["dataset_hash"],
        "new_physical_llm_calls": after["llm_calls"] - before["llm_calls"],
        "new_physical_embeddings": after["embeddings"] - before["embeddings"],
        "new_results": after["results"] - before["results"],
        "wall_duration_ms": int((time.monotonic() - start) * 1000),
        "repeat_of": str(repeat_of) if repeat_of else None,
        "repeat_equal": repeated_equal,
        "result_signature": signature(dataset),
        "dataset": dataset,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["accept", "status", "resume", "export", "audit"])
    parser.add_argument("--corpus-version", type=UUID)
    parser.add_argument("--series", type=UUID)
    parser.add_argument("--repeat-of", type=UUID)
    parser.add_argument("--idempotency-key", type=UUID)
    parser.add_argument("--mode", choices=["matrix", "ablations"], default="matrix")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    settings = Settings()
    if args.action == "accept":
        report = accept(
            settings,
            args.corpus_version,
            args.series,
            args.repeat_of,
            args.mode,
            args.idempotency_key,
        )
    elif args.action == "audit":
        if args.series is None or args.baseline is None:
            raise ProcessingError("Укажите --series и --baseline (JSON приёмки 4.6)")
        report = audit(settings, args.series, args.baseline)
    else:
        if args.series is None:
            raise ProcessingError("Укажите --series")
        if args.action == "resume":
            with get_session(settings.database_url) as session:
                retry_series(session, args.series)
                session.commit()
            execute_series(settings, args.series)
        with get_session(settings.database_url) as session:
            series = session.get(ExperimentSeries, args.series)
            if series is None:
                raise ProcessingError("Серия не найдена")
            report = {
                "series_id": str(series.id),
                "status": series.status,
                "error": series.error,
                "runs": [
                    {
                        "variant": r.variant,
                        "status": r.status,
                        "progress": r.progress,
                        "total": r.total,
                    }
                    for r in runs(session, series.id)
                ],
            }
            if args.action == "export":
                report["dataset"] = series.dataset
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        if report.get("dataset"):
            for format in ("csv", "md", "png"):
                args.output.with_suffix("." + format).write_bytes(
                    export(report["dataset"], format)[0]
                )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "dataset"}, ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    try:
        main()
    except ProcessingError as exc:
        raise SystemExit(str(exc)) from None
