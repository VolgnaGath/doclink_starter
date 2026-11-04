"""Reproducible technical stage-4 acceptance, not experiment_runs or a full matrix.

Only this selection/evaluation layer reads annotations. Workers receive the same
allowlisted linking_payload as the technical CLI. Cloud models are never called.
"""

import argparse
import hashlib
import json
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.models import (
    CorpusVersion,
    Document,
    DocumentEmbedding,
    Embedding,
    Job,
    LinkSuggestion,
    LLMCall,
    ModelConfig,
    StageTiming,
)
from app.models import (
    LinkingResult as StoredResult,
)
from app.services.embedding_store import embedding_payload, encoding_config
from app.services.embeddings import EncodingConfig
from app.services.evaluation import EvaluationDocument, evaluate
from app.services.extraction_protocols import test_history
from app.services.jobs import work_once
from app.services.linking.types import CandidateScore, LinkingConfig, LinkingInput, LinkingResult
from app.services.linking_acceptance import TuneDocument, select_thresholds
from app.services.linking_store import enqueue_linking, linking_payload
from app.services.model_calls import ProcessingError, fingerprint

ROOT = Path(__file__).resolve().parent.parent


def code_hash() -> str:
    files = [
        *sorted((ROOT / "app/services/linking").glob("*.py")),
        *(
            ROOT / name
            for name in (
                "app/services/linking_store.py",
                "app/services/embeddings.py",
                "app/services/embedding_store.py",
                "app/services/evaluation.py",
                "app/services/linking_acceptance.py",
                "app/services/extraction_schema.py",
                "app/linking_acceptance.py",
            )
        ),
    ]
    return fingerprint(
        {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files
        }
    )


def incoming(session: Session, version_id: UUID) -> list[Document]:
    """Evaluation denominator: gold incoming kind, never the potentially wrong card kind."""
    return list(
        session.scalars(
            select(Document)
            .where(
                Document.corpus_version_id == version_id,
                Document.in_scope,
                Document.gold["fields"]["kind"].astext.in_(["допсоглашение", "счёт", "акт", "УПД"]),
            )
            .order_by(Document.id)
        )
    )


def targets(
    session: Session, version_id: UUID, documents: list[Document]
) -> dict[UUID, frozenset[str]]:
    ids = {
        key: str(id)
        for key, id in session.execute(
            select(Document.corpus_doc_id, Document.id).where(
                Document.corpus_version_id == version_id
            )
        )
    }
    return {
        doc.id: frozenset(ids[link["target"]] for link in doc.gold["links"]) for doc in documents
    }


def annotation_hash(documents: list[Document]) -> str:
    return fingerprint(
        [{"id": str(doc.id), "split": doc.split, "gold": doc.gold} for doc in documents]
    )


def embedding_audit(session: Session, version_id: UUID, encoding: EncodingConfig) -> dict[str, Any]:
    documents = list(
        session.scalars(
            select(Document.id)
            .where(Document.corpus_version_id == version_id)
            .order_by(Document.id)
        )
    )
    rows = []
    for id in documents:
        expected = embedding_payload(session, id, encoding)
        match = session.execute(
            select(DocumentEmbedding, Embedding)
            .join(Embedding, DocumentEmbedding.embedding_id == Embedding.id)
            .where(
                DocumentEmbedding.document_id == id,
                DocumentEmbedding.original_text_hash == expected["original_text_hash"],
                Embedding.input_hash == expected["input_hash"],
            )
        ).first()
        if match is None:
            raise ProcessingError(
                "Не все документы имеют актуальный реальный BGE-M3; выполните make embed"
            )
        origin, vector = match
        rows.append(
            {
                "document_id": str(id),
                "embedding_id": str(vector.id),
                "origin_id": str(origin.id),
                "input_hash": vector.input_hash,
                "cache_hit": origin.cache_hit,
                "tokens": vector.tokens,
                "chunks": vector.chunks,
                "duration_ms": vector.duration_ms,
                "truncated": vector.truncated,
            }
        )
    return {
        "documents": len(rows),
        "unique_vectors": len({row["embedding_id"] for row in rows}),
        "input_hash": fingerprint(rows),
        "rows": rows,
    }


def freeze(
    settings: Settings, version_id: UUID, model_name: str | None, method: str
) -> dict[str, Any]:
    with get_session(settings.database_url) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        version = session.get(CorpusVersion, version_id, with_for_update=True)
        if version is None:
            raise ProcessingError("Версия корпуса не найдена")
        model = (
            session.scalar(select(ModelConfig).where(ModelConfig.name == model_name))
            if model_name
            else session.scalar(select(ModelConfig).where(ModelConfig.default_card))
        )
        if model is None:
            raise ProcessingError("Выберите модель извлечения")
        encoding = encoding_config(settings)
        embeddings = embedding_audit(session, version_id, encoding)
        docs = incoming(session, version_id)
        tune_docs = [doc for doc in docs if doc.split == "tune"]
        gold = targets(session, version_id, tune_docs)
        model_id = model.id if method == "combined" else None
        template = LinkingConfig(method=method, tau_a=0, tau_r=0)
        tuning = select_thresholds(
            tuple(
                TuneDocument(
                    LinkingInput.model_validate(
                        linking_payload(session, doc.id, model_id, template, encoding)["data"]
                    ),
                    gold[doc.id],
                )
                for doc in tune_docs
            ),
            template,
        )
        if tuning["config"] is None:
            return {
                "corpus_version": str(version.id),
                "method": method,
                "model": model.name,
                "tuning": tuning,
                "status": "NO_FEASIBLE_CONFIGURATION",
                "test_executed": False,
            }
        config = LinkingConfig.model_validate(tuning["config"])
        inputs = {
            str(doc.id): linking_payload(session, doc.id, model_id, config, encoding)["input_hash"]
            for doc in docs
        }
        frozen = {
            "acceptance_version": "stage4-acceptance-v1",
            "corpus_version": str(version.id),
            "corpus_fingerprint": version.fingerprint,
            "source_versions": version.report.get("extraction_source_versions", []),
            "extraction_protocols": version.report.get("extraction_protocols", {}),
            "model_id": str(model_id) if model_id else None,
            "selected_model": model.name,
            "config": config.model_dump(mode="json"),
            "encoding": encoding.model_dump(mode="json"),
            "source_code_hash": code_hash(),
            "input_hashes": inputs,
            "annotation_hash": annotation_hash(docs),
            "tuning_hash": fingerprint(tuning),
            "embedding_input_hash": embeddings["input_hash"],
        }
        key = fingerprint(frozen)
        protocols = dict(version.report.get("linking_acceptance_protocols", {}))
        history = test_history(session, version)
        protocols.setdefault(
            key,
            {
                "frozen": frozen,
                "tuning": tuning,
                "frozen_at": datetime.now(UTC).isoformat(),
                "test_started": False,
                "reused_test": bool(history)
                or any(p.get("test_started") for p in protocols.values()),
                "prior_extraction_test_history": history,
            },
        )
        version.report = {**version.report, "linking_acceptance_protocols": protocols}
        session.commit()
        return {
            "protocol_hash": key,
            "status": "FROZEN",
            **protocols[key],
            "embeddings": embeddings,
        }


def validate(
    session: Session, settings: Settings, version_id: UUID, key: str
) -> tuple[dict[str, Any], list[Document], dict[str, dict[str, Any]]]:
    version = session.get(CorpusVersion, version_id, with_for_update=True)
    protocol = version.report.get("linking_acceptance_protocols", {}).get(key) if version else None
    if protocol is None or fingerprint(protocol["frozen"]) != key:
        raise ProcessingError("Не найден корректный замороженный протокол")
    frozen = protocol["frozen"]
    if (
        frozen["source_code_hash"] != code_hash()
        or frozen["corpus_fingerprint"] != version.fingerprint
    ):
        raise ProcessingError("Код/корпус изменён после freeze; нужен новый tune и reused_test")
    encoding = encoding_config(settings)
    if encoding.model_dump(mode="json") != frozen["encoding"]:
        raise ProcessingError("Embedder изменён после freeze")
    docs = incoming(session, version_id)
    if annotation_hash(docs) != frozen["annotation_hash"]:
        raise ProcessingError("Оценочная выборка/разметка изменена после freeze")
    config = LinkingConfig.model_validate(frozen["config"])
    model_id = UUID(frozen["model_id"]) if frozen["model_id"] else None
    payloads = {
        str(doc.id): linking_payload(session, doc.id, model_id, config, encoding) for doc in docs
    }
    if {id: payload["input_hash"] for id, payload in payloads.items()} != frozen["input_hashes"]:
        raise ProcessingError("Карточка/извлечение/вектор изменились после freeze")
    return protocol, docs, payloads


def _result(session: Session, stored: StoredResult) -> LinkingResult:
    candidates = tuple(
        CandidateScore(
            target_id=str(c.target_id),
            features=c.features,
            penalties=c.penalties,
            score=c.score,
            rank=c.rank,
            decision=c.decision,
            link_type=c.link_type,
            source_evidence=c.source_evidence,
            target_evidence=c.target_evidence,
        )
        for c in session.scalars(
            select(LinkSuggestion)
            .where(LinkSuggestion.result_id == stored.id)
            .order_by(LinkSuggestion.rank)
        )
    )
    return LinkingResult(
        outcome=stored.outcome,
        reasons=tuple(stored.reasons),
        recommendations=tuple(stored.recommendations),
        candidates=candidates,
        config=LinkingConfig.model_validate(stored.config),
    )


def run(settings: Settings, version_id: UUID, key: str) -> dict[str, Any]:
    started_at, started = datetime.now(UTC), time.monotonic()
    with get_session(settings.database_url) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        protocol, docs, payloads = validate(session, settings, version_id, key)
        before_calls = session.scalar(select(func.count()).select_from(LLMCall))
        before_embeddings = session.scalar(select(func.count()).select_from(Embedding))
        before_results = set(
            session.scalars(
                select(StoredResult.id).where(StoredResult.source_id.in_([d.id for d in docs]))
            )
        )
        config = LinkingConfig.model_validate(protocol["frozen"]["config"])
        encoding = EncodingConfig.model_validate(protocol["frozen"]["encoding"])
        model_id = UUID(protocol["frozen"]["model_id"]) if protocol["frozen"]["model_id"] else None
        jobs = {
            str(doc.id): enqueue_linking(session, doc.id, model_id, config, encoding)
            for doc in docs
        }
        version = session.get(CorpusVersion, version_id)
        protocols = dict(version.report["linking_acceptance_protocols"])
        protocols[key] = {**protocols[key], "test_started": True}
        version.report = {**version.report, "linking_acceptance_protocols": protocols}
        session.commit()
    while work_once(settings, document_ids=[UUID(id) for id in jobs], job_types=["link"]):
        pass
    with get_session(settings.database_url) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        protocol, docs, payloads = validate(session, settings, version_id, key)
        gold = targets(session, version_id, docs)
        target_names = {
            str(id): name
            for id, name in session.execute(
                select(Document.id, Document.corpus_doc_id).where(
                    Document.corpus_version_id == version_id
                )
            )
        }
        results = {}
        stored_ids = []
        job_counts: Counter[str] = Counter()
        controls = []
        for doc in docs:
            job = session.get(Job, jobs[str(doc.id)])
            job_counts[job.status] += 1
            stored = session.scalar(select(StoredResult).where(StoredResult.job_id == job.id))
            if stored is None:
                stored = session.scalar(
                    select(StoredResult).where(
                        StoredResult.input_hash == payloads[str(doc.id)]["input_hash"],
                        StoredResult.outcome != "error",
                    )
                )
            if stored is None or stored.input_hash != payloads[str(doc.id)]["input_hash"]:
                raise ProcessingError("Задание не сохранило результат замороженного входа")
            stored_ids.append(str(stored.id))
            result = _result(session, stored)
            results[doc.id] = result
            if doc.gold.get("case"):
                case = doc.gold["case"]
                passed = (
                    result.outcome in {"review", "none"}
                    if case == "no_target"
                    else result.outcome == "review"
                )
                explanation = {
                    "multi3": {"three_or_more_references", "three_or_more_targets"},
                    "conflict": {"reference_number_conflict", "reference_date_conflict"},
                    "same_number": {"ambiguous_reference"},
                }.get(case)
                if explanation:
                    passed = passed and bool(explanation.intersection(result.reasons))
                controls.append(
                    {
                        "document": doc.corpus_doc_id,
                        "document_id": str(doc.id),
                        "split": doc.split,
                        "case": case,
                        "expected": doc.gold["expected"],
                        "actual": result.outcome,
                        "reasons": list(result.reasons),
                        "passed": passed,
                        "result_id": str(stored.id),
                        "candidates": [
                            {**c.model_dump(mode="json"), "document": target_names[c.target_id]}
                            for c in result.candidates
                        ],
                    }
                )
        metrics = {
            split: asdict(
                evaluate(
                    tuple(
                        EvaluationDocument(results[doc.id], gold[doc.id])
                        for doc in docs
                        if doc.split == split
                    )
                )
            )
            for split in ("tune", "test")
        }
        physical_calls = session.scalar(select(func.count()).select_from(LLMCall))
        physical_embeddings = session.scalar(select(func.count()).select_from(Embedding))
        if physical_calls != before_calls:
            raise ProcessingError(
                "Во время локальной приёмки изменился журнал физических LLM-вызовов"
            )
        timings = list(
            session.scalars(
                select(StageTiming.duration_ms).where(
                    StageTiming.stage == "link",
                    StageTiming.document_id.in_([d.id for d in docs]),
                    StageTiming.started_at >= started_at,
                )
            )
        )
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "protocol_hash": key,
            "protocol": protocol,
            "metrics_by_split": metrics,
            "incoming_documents": len(docs),
            "jobs": dict(job_counts),
            "result_ids": stored_ids,
            "new_results": sum(UUID(id) not in before_results for id in stored_ids),
            "new_physical_llm_calls": physical_calls - before_calls,
            "new_physical_embeddings": physical_embeddings - before_embeddings,
            "controls": sorted(controls, key=lambda row: row["document"]),
            "control_passed": sum(row["passed"] for row in controls),
            "control_total": len(controls),
            "wall_duration_ms": int((time.monotonic() - started) * 1000),
            "link_timings": {
                "count": len(timings),
                "sum_ms": sum(timings),
                "mean_ms": sum(timings) / len(timings) if timings else None,
            },
            "notes": [
                "Technical stage-4 acceptance, not experiment_runs or the stage-6 matrix.",
                "Tune metrics are not test metrics. Control rows show their split explicitly.",
                "No method changes based on test outcomes; measured failures remain failures.",
            ],
        }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Живая приёмка связывания — этап 4.6",
        "",
        f"Протокол: `{report['protocol_hash']}`.",
        f"reused_test={str(report['protocol']['reused_test']).lower()}. "
        "Пороги выбраны только на tune; контрольная таблица не является отдельной test-метрикой.",
        "",
        "| Часть | N | M | Precision | Recall | Охват | Ошибок обработки |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for split, m in report["metrics_by_split"].items():
        lines.append(
            f"| {split} | {m['n']} | {m['m']} | {m['precision']:.4f} | "
            f"{m['recall']:.4f} | {m['coverage']:.4f} | {m['processing_errors']} |"
        )
    lines.extend(
        [
            "",
            f"Контрольные случаи: **{report['control_passed']}/{report['control_total']}**.",
            "",
            "| Документ | Split | Case | Ожидание | Исход | Причины | "
            "Кандидаты (top-3; полный рейтинг в JSON) | Пройдено |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in report["controls"]:
        candidates = "; ".join(f"{c['document']} ({c['score']:.4f})" for c in row["candidates"][:3])
        lines.append(
            f"| {row['document']} | {row['split']} | {row['case']} | {row['expected']} | "
            f"{row['actual']} | {', '.join(row['reasons'])} | {candidates} | "
            f"{'да' if row['passed'] else 'нет'} |"
        )
    lines.extend(
        [
            "",
            "Новые физические LLM-вызовы: " + str(report["new_physical_llm_calls"]) + ".",
            "Техническая приёмка выбранной конфигурации; "
            "полная матрица и итоговая проверка — этап 6.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["tune", "run", "embedding-audit", "embedding-status"])
    parser.add_argument("--corpus-version", type=UUID, required=True)
    parser.add_argument("--model")
    parser.add_argument("--method", choices=["rules", "semantic", "combined"], default="combined")
    parser.add_argument("--protocol-hash")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings()
    try:
        if args.action == "tune":
            report = freeze(settings, args.corpus_version, args.model, args.method)
        elif args.action == "run":
            if not args.protocol_hash:
                raise ProcessingError("Укажите --protocol-hash из tune/freeze")
            report = run(settings, args.corpus_version, args.protocol_hash)
        elif args.action == "embedding-status":
            with get_session(settings.database_url) as session:
                ids = select(Document.id).where(Document.corpus_version_id == args.corpus_version)
                report = {
                    "documents": session.scalar(
                        select(func.count())
                        .select_from(Document)
                        .where(Document.corpus_version_id == args.corpus_version)
                    ),
                    "jobs": dict(
                        session.execute(
                            select(Job.status, func.count())
                            .where(Job.type == "embed", Job.document_id.in_(ids))
                            .group_by(Job.status)
                        ).all()
                    ),
                    "unique_vectors": session.scalar(
                        select(func.count(func.distinct(DocumentEmbedding.embedding_id))).where(
                            DocumentEmbedding.document_id.in_(ids)
                        )
                    ),
                }
        else:
            with get_session(settings.database_url) as session:
                report = embedding_audit(session, args.corpus_version, encoding_config(settings))
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        if args.action == "run":
            args.output.with_suffix(".md").write_text(markdown(report))
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    **{
                        key: report[key]
                        for key in (
                            "status",
                            "protocol_hash",
                            "metrics_by_split",
                            "jobs",
                            "new_results",
                            "new_physical_llm_calls",
                            "new_physical_embeddings",
                            "control_passed",
                            "control_total",
                            "documents",
                            "unique_vectors",
                        )
                        if key in report
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return int(report.get("status") == "NO_FEASIBLE_CONFIGURATION")
    except (ProcessingError, ValueError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
