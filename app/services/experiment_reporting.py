"""Saved evaluation dataset and explicit physical provenance, separate from execution."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.linking_acceptance import _result, incoming, targets
from app.models import (
    Document,
    Embedding,
    ExperimentArtifact,
    ExperimentMetric,
    ExperimentResult,
    ExperimentRun,
    ExperimentSeries,
    Extraction,
    LLMCall,
    ModelConfig,
    ReviewSession,
    StageTiming,
    ThresholdCurve,
)
from app.models import LinkingResult as StoredResult
from app.services.evaluation import EvaluationDocument
from app.services.experiment_metrics import field_accuracy, in_slice, linking_metrics, slices
from app.services.model_calls import fingerprint


def physical_calls(session: Session, extractions: list[Extraction]) -> list[LLMCall]:
    """Final call plus all attempts of that same immutable root/physical job."""
    final = list(
        session.scalars(
            select(LLMCall).where(
                LLMCall.id.in_([e.llm_call_id for e in extractions if e.llm_call_id])
            )
        )
    )
    if not final:
        return []
    roots = {c.root_input_hash for c in final}
    jobs = {c.job_id for c in final if c.job_id}
    return list(
        session.scalars(
            select(LLMCall)
            .where(or_(LLMCall.root_input_hash.in_(roots), LLMCall.job_id.in_(jobs)))
            .order_by(LLMCall.id)
        )
    )


def associate(session: Session, run_id: UUID, field: str, ids: set[Any]) -> None:
    for id in sorted(ids):
        session.execute(
            insert(ExperimentArtifact).values(run_id=run_id, **{field: id}).on_conflict_do_nothing()
        )


def record_inputs(session: Session, run: ExperimentRun) -> None:
    ids = {UUID(e["id"]) for e in run.snapshot["extractions"]}
    rows = list(session.scalars(select(Extraction).where(Extraction.id.in_(ids))))
    associate(session, run.id, "extraction_id", ids)
    associate(session, run.id, "llm_call_id", {c.id for c in physical_calls(session, rows)})
    associate(
        session,
        run.id,
        "timing_id",
        set(
            session.scalars(
                select(StageTiming.id).where(
                    StageTiming.document_id.in_({e.document_id for e in rows}),
                    StageTiming.stage != "link",
                    StageTiming.started_at <= run.started_at,
                )
            )
        ),
    )
    if run.model_config_id and run.ablation != "no_llm":
        series = session.get(ExperimentSeries, run.series_id)
        run.report = {
            **run.report,
            "extraction": extraction_metrics(
                session, series.corpus_version_id, run.model_config_id
            ),
        }


def resources(calls: list[LLMCall], n: int, model: ModelConfig | None = None) -> dict[str, Any]:
    known = sum((c.cost_rub for c in calls if c.cost_rub is not None), Decimal(0))
    unknown = sum(c.cost_rub is None for c in calls)
    missing_time = sum(c.latency_ms is None for c in calls)
    missing_tokens = sum(c.input_tokens is None or c.output_tokens is None for c in calls)
    cost = None if unknown else known
    latency = None if missing_time else sum(c.latency_ms for c in calls)
    estimated = None
    if model and model.placement == "open-weights-cloud" and model.v_prefill and model.v_decode:
        if model.v_prefill > 0 and model.v_decode > 0 and not missing_tokens:
            estimated = sum(
                c.input_tokens / model.v_prefill + c.output_tokens / model.v_decode for c in calls
            )
    return {
        "documents": n,
        "physical_calls": len(calls),
        "call_ids": [str(c.id) for c in calls],
        "known_cost_rub": str(known),
        "unknown_cost_calls": unknown,
        "source_cost_rub": str(cost) if cost is not None else None,
        "source_cost_per_document_rub": str(cost / n) if cost is not None and n else None,
        "source_latency_ms": latency,
        "source_latency_per_document_ms": latency / n if latency is not None and n else None,
        "input_tokens": sum(c.input_tokens for c in calls) if not missing_tokens else None,
        "output_tokens": sum(c.output_tokens for c in calls) if not missing_tokens else None,
        "estimated_local_seconds": estimated,
        "local_time_label": "расчётное",
        "v_prefill": model.v_prefill if model else None,
        "v_decode": model.v_decode if model else None,
        "cash_debit_rub": None,
        "new_llm_cost_rub": "0",
        "new_physical_llm_calls": 0,
    }


def extraction_metrics(session: Session, version_id: UUID, model_id: UUID) -> dict[str, Any]:
    model = session.get(ModelConfig, model_id)
    data = list(
        session.execute(
            select(Document, Extraction)
            .join(Extraction, Extraction.document_id == Document.id)
            .where(
                Document.corpus_version_id == version_id,
                Document.in_scope,
                Extraction.model_config_id == model_id,
                Extraction.is_current,
            )
            .order_by(Document.id)
        )
    )
    result = {}
    for split in ("tune", "test"):
        rows = [(d, e) for d, e in data if d.split == split]
        checks = [c for _, e in rows for c in e.evidence_checks]
        supplied = [c for c in checks if c.get("quote") and str(c["quote"]).strip()]
        calls = physical_calls(session, [e for _, e in rows])
        result[split] = {
            "n": len(rows),
            "fields": field_accuracy(
                [
                    {
                        "gold": d.gold,
                        "valid": e.valid,
                        "fields": {
                            "kind": e.doc_kind,
                            "number": e.number,
                            "date": e.date,
                            "cp_inn": e.cp_inn,
                            "amount": e.amount,
                        },
                    }
                    for d, e in rows
                ]
            ),
            "invalid": {
                "numerator": sum(not e.valid for _, e in rows),
                "denominator": len(rows),
                "value": sum(not e.valid for _, e in rows) / len(rows) if rows else None,
            },
            "unmatched_quotes": {
                "numerator": sum(not c.get("ok") for c in supplied),
                "denominator": len(supplied),
                "value": sum(not c.get("ok") for c in supplied) / len(supplied)
                if supplied
                else None,
            },
            "missing_evidence": sum(not str(c.get("quote") or "").strip() for c in checks),
            "resources": resources(calls, len(rows), model),
        }
    return {
        "model": model.name,
        "model_id": str(model.id),
        "model_uri": model.model_uri,
        "placement": "открытые веса, облачное размещение"
        if model.placement == "open-weights-cloud"
        else model.placement,
        "splits": result,
    }


def control_row(doc: Document, result: Any, stored_id: UUID) -> dict[str, Any]:
    case = doc.gold["case"]
    passed = (
        result.outcome in {"review", "none"} if case == "no_target" else result.outcome == "review"
    )
    required = {
        "multi3": {"three_or_more_references", "three_or_more_targets"},
        "conflict": {"reference_number_conflict", "reference_date_conflict"},
        "same_number": {"ambiguous_reference"},
    }.get(case)
    if required:
        passed = passed and bool(required.intersection(result.reasons))
    return {
        "document": doc.corpus_doc_id,
        "document_id": str(doc.id),
        "split": doc.split,
        "case": case,
        "expected": doc.gold["expected"],
        "actual": result.outcome,
        "reasons": list(result.reasons),
        "passed": passed,
        "result_id": str(stored_id),
        "candidates": [c.model_dump(mode="json") for c in result.candidates],
    }


def finish_run(session: Session, run: ExperimentRun) -> None:
    series = session.get(ExperimentSeries, run.series_id)
    docs = incoming(session, series.corpus_version_id)
    gold = targets(session, series.corpus_version_id, docs)
    associations = list(
        session.scalars(select(ExperimentResult).where(ExperimentResult.run_id == run.id))
    )
    stored = {r.document_id: session.get(StoredResult, r.result_id) for r in associations}
    results = {id: _result(session, row) for id, row in stored.items()}
    metrics = {}
    for split in ("tune", "test"):
        metrics[split] = {}
        for name in slices():
            selected = tuple(
                EvaluationDocument(results[d.id], gold[d.id])
                for d in docs
                if d.split == split and in_slice(d.gold, name)
            )
            values = linking_metrics(selected)
            metrics[split][name] = {k: v["value"] for k, v in values.items()}
            for metric, value in values.items():
                session.add(
                    ExperimentMetric(run_id=run.id, split=split, slice=name, metric=metric, **value)
                )
    extraction_ids = {UUID(e["id"]) for e in run.snapshot["extractions"]}
    extractions = list(session.scalars(select(Extraction).where(Extraction.id.in_(extraction_ids))))
    calls = physical_calls(session, extractions)
    embedding_ids = {
        UUID(origin["embedding"]["id"])
        for row in stored.values()
        for origin in row.provenance["documents"].values()
        if origin.get("embedding")
    }
    for field, ids in (
        ("extraction_id", extraction_ids),
        ("llm_call_id", {c.id for c in calls}),
        ("embedding_id", embedding_ids),
        ("timing_id", {r.timing_id for r in associations}),
    ):
        associate(session, run.id, field, ids)
    timings = list(
        session.scalars(
            select(StageTiming).where(StageTiming.id.in_([r.timing_id for r in associations]))
        )
    )
    source_embeddings = list(
        session.scalars(select(Embedding).where(Embedding.id.in_(embedding_ids)))
    )
    controls = [control_row(d, results[d.id], stored[d.id].id) for d in docs if d.gold.get("case")]
    # Model-level extraction table is independent of source linking denominator.
    extraction_table = (
        extraction_metrics(session, series.corpus_version_id, run.model_config_id)
        if run.model_config_id and run.ablation != "no_llm"
        else None
    )
    run.report = {
        **run.report,
        "metrics": metrics,
        "controls": sorted(controls, key=lambda r: r["document"]),
        "extraction": extraction_table,
        "resources": {
            **resources(calls, len(docs)),
            "resource_scope": "все исходные извлечения карточки/выбранной модели версии",
            "reused_results": sum(r.reused for r in associations),
            "reuse_share": sum(r.reused for r in associations) / len(docs) if docs else None,
            "execution_link_ms": sum(t.duration_ms for t in timings),
            "execution_link_per_document_ms": sum(t.duration_ms for t in timings) / len(docs)
            if docs
            else None,
            "source_embedding_ms": sum(e.duration_ms for e in source_embeddings),
            "embedding_ids": [str(e.id) for e in source_embeddings],
        },
        "result_ids": [str(stored[d.id].id) for d in docs],
    }


def review_measurements(session: Session, run_id: UUID) -> list[dict[str, Any]]:
    """Completed unedited initial copies only. No AS-IS/assisted savings inferred here."""
    result_ids = select(ExperimentResult.result_id).where(ExperimentResult.run_id == run_id)
    rows = []
    for review in session.scalars(
        select(ReviewSession).where(ReviewSession.completed_at.is_not(None))
    ):
        copy = review.context.get("provenance", {}).get("working_copy") or {}
        if not copy.get("initial_inputs"):
            continue
        source = copy.get("source_result_id")
        if source and session.scalar(
            select(ExperimentResult.run_id)
            .where(ExperimentResult.run_id == run_id, ExperimentResult.result_id == UUID(source))
            .limit(1)
        ):
            rows.append(
                {
                    "session_id": str(review.id),
                    "source_result_id": source,
                    "duration_ms": review.duration_ms,
                    "document_id": str(review.document_id),
                }
            )
        elif review.result_id and session.scalar(
            select(StoredResult.id).where(
                StoredResult.id == review.result_id, StoredResult.id.in_(result_ids)
            )
        ):
            rows.append(
                {
                    "session_id": str(review.id),
                    "source_result_id": str(review.result_id),
                    "duration_ms": review.duration_ms,
                    "document_id": str(review.document_id),
                }
            )
    return rows


def build_dataset(session: Session, series: ExperimentSeries) -> dict[str, Any]:
    from app.services.experiments import MODEL_ORDER, runs

    run_rows = runs(session, series.id)
    records = []
    models = {}
    for run in run_rows:
        if run.report.get("extraction"):
            models[run.report["extraction"]["model"]] = run.report["extraction"]
        records.append(
            {
                "id": str(run.id),
                "variant": run.variant,
                "method": run.method,
                "ablation": run.ablation,
                "status": run.status,
                "error": run.error,
                "model_id": str(run.model_config_id) if run.model_config_id else None,
                "baseline_models": list(MODEL_ORDER) if run.method != "combined" else [],
                "config": run.config,
                "protocol_hash": run.protocol_hash,
                "reused_test": run.reused_test,
                "snapshot": run.snapshot,
                "report": run.report,
                "metric_rows": [
                    {
                        "split": m.split,
                        "slice": m.slice,
                        "metric": m.metric,
                        "value": m.value,
                        "numerator": m.numerator,
                        "denominator": m.denominator,
                    }
                    for m in session.scalars(
                        select(ExperimentMetric)
                        .where(ExperimentMetric.run_id == run.id)
                        .order_by(
                            ExperimentMetric.split, ExperimentMetric.slice, ExperimentMetric.metric
                        )
                    )
                ],
                "curves": [
                    c.values
                    for c in session.scalars(
                        select(ThresholdCurve)
                        .where(ThresholdCurve.run_id == run.id)
                        .order_by(ThresholdCurve.index)
                    )
                ],
                "review_measurements": review_measurements(session, run.id),
            }
        )
    call_ids = set(
        session.scalars(
            select(ExperimentArtifact.llm_call_id).where(
                ExperimentArtifact.run_id.in_([r.id for r in run_rows]),
                ExperimentArtifact.llm_call_id.is_not(None),
            )
        )
    )
    calls = list(
        session.scalars(select(LLMCall).where(LLMCall.id.in_(call_ids)).order_by(LLMCall.id))
    )
    dataset = {
        "version": "stage6-dataset-v1",
        "series_id": str(series.id),
        "name": series.name,
        "corpus_version_id": str(series.corpus_version_id),
        "selected_model_id": str(series.selected_model_id) if series.selected_model_id else None,
        "runs": records,
        "table_2_6": list(models.values()),
        "resources": resources(calls, len(incoming(session, series.corpus_version_id))),
        "notes": [
            "Сравнение моделей условно на общей карточке Qwen.",
            "Baselines общие для трёх моделей; не независимые повторы.",
            "Трудные срезы пересекаются и не суммируются.",
            "Кривые только tune; пороги по test не выбираются.",
            "Макет параграфа 2.3 отсутствует; раскладка соответствует плану этапа 6.",
        ],
    }
    return {**dataset, "dataset_hash": fingerprint(dataset)}
