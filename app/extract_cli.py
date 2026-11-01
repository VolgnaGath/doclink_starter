"""Queue/resume extraction and print a factual report; gold stays in the selection layer."""

import argparse
import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.config import Settings
from app.db import get_session
from app.models import CorpusVersion, Document, Extraction, Job, LLMCall, ModelConfig, Prompt
from app.seed import seed
from app.services.extraction import configuration
from app.services.extraction_schema import PREPARATION_VERSION
from app.services.jobs import enqueue, work_once
from app.services.model_calls import ProcessingError, fingerprint


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Извлечение реквизитов")
    result.add_argument("--document", type=UUID)
    result.add_argument("--corpus-version", type=UUID)
    result.add_argument(
        "--model", action="append", default=[], help="Имя модели; иначе все активные"
    )
    result.add_argument("--split", choices=["tune", "test"])
    result.add_argument("--retry", action="store_true", help="Явно повторить завершённые задания")
    result.add_argument("--drain", action="store_true", help="Обработать очередь до опустошения")
    result.add_argument("--freeze", action="store_true")
    result.add_argument("--protocol-hash", default="")
    result.add_argument("--report-only", action="store_true")
    result.add_argument("--reconcile-call", type=UUID)
    result.add_argument("--cost-rub", type=Decimal)
    result.add_argument("--note", default="")
    return result


def execute(
    args: argparse.Namespace, settings: Settings, *, paid_entry: bool = False
) -> dict[str, Any]:
    with get_session(settings.database_url) as session:
        if args.reconcile_call:
            call = session.get(LLMCall, args.reconcile_call, with_for_update=True)
            if (
                call is None
                or args.cost_rub is None
                or not args.cost_rub.is_finite()
                or args.cost_rub < 0
                or not args.note.strip()
            ):
                raise ProcessingError(
                    "Для сверки нужны существующий вызов, стоимость >= 0 и пояснение"
                )
            if call.status == "started":
                raise ProcessingError("Сначала восстановите остановленное задание worker")
            call.cost_rub, call.reconciled = args.cost_rub, True
            call.error = (call.error or "") + "\nСверка: " + args.note
            session.commit()
            return {"reconciled": str(call.id), "cost_rub": str(call.cost_rub)}
        seed(session)
        session.commit()
        query = (
            select(ModelConfig).where(ModelConfig.name.in_(args.model))
            if args.model
            else select(ModelConfig).where(ModelConfig.active)
        )
        models = list(session.scalars(query.order_by(ModelConfig.name)))
        if not models or args.model and len(models) != len(set(args.model)):
            raise ProcessingError("Модели не найдены; настройте make seed и /models")
        if (
            any(m.provider in {"yandex", "gigachat"} for m in models)
            and not paid_entry
            and not args.report_only
            and not args.freeze
        ):
            raise ProcessingError("Облачная обработка запускается через make smoke-paid")
        prompt = session.scalar(
            select(Prompt)
            .where(Prompt.name == "extract")
            .order_by(Prompt.created_at.desc())
            .limit(1)
        )
        protocol_hash = fingerprint(
            {
                "models": [configuration(m, prompt) for m in models],
                "preparation": PREPARATION_VERSION,
            }
        )
        version = (
            session.get(CorpusVersion, args.corpus_version, with_for_update=True)
            if args.corpus_version
            else None
        )
        if args.corpus_version and version is None:
            raise ProcessingError("Версия корпуса не найдена")
        if args.freeze:
            if version is None:
                raise ProcessingError("Для фиксации выберите версию корпуса")
            report = dict(version.report)
            protocols = dict(report.get("extraction_protocols", {}))
            protocols.setdefault(
                protocol_hash,
                {
                    "test_started": False,
                    "reused_test": any(p.get("test_started") for p in protocols.values()),
                    "models": [configuration(m, prompt) for m in models],
                    "preparation": PREPARATION_VERSION,
                },
            )
            report["extraction_protocols"] = protocols
            version.report = report
            session.commit()
            return {"protocol_hash": protocol_hash, **protocols[protocol_hash]}
        if args.document:
            query = select(Document).where(Document.id == args.document)
        elif version:
            query = select(Document).where(Document.corpus_version_id == version.id)
            if args.split:
                query = query.where(Document.split == args.split)
        else:
            raise ProcessingError("Укажите --document или --corpus-version")
        documents = list(session.scalars(query.order_by(Document.id)))
        if not documents:
            raise ProcessingError("Документы не найдены")
        includes_test = version and args.split != "tune"
        if includes_test and not args.report_only:
            protocols = version.report.get("extraction_protocols", {})
            if args.protocol_hash != protocol_hash or protocol_hash not in protocols:
                raise ProcessingError(
                    "Перед test зафиксируйте конфигурацию --freeze и передайте --protocol-hash"
                )
            protocols = {key: dict(value) for key, value in protocols.items()}
            protocols[protocol_hash]["test_started"] = True
            protocols[protocol_hash]["reused_test"] = any(
                value.get("test_started")
                for key, value in protocols.items()
                if key != protocol_hash
            ) or protocols[protocol_hash].get("reused_test", False)
            version.report = {**version.report, "extraction_protocols": protocols}
        if not args.report_only:
            for document in documents:
                enqueue(session, document, models, retry=args.retry)
            session.commit()
        document_ids, model_ids = [d.id for d in documents], [m.id for m in models]
        scope_ids = [d.id for d in documents if d.in_scope]
    if args.drain and not args.report_only:
        while work_once(settings, document_ids, model_ids):
            pass
    with get_session(settings.database_url) as session:

        def counts(ids: list[UUID], selected_models: list[UUID] | None = None) -> dict[str, Any]:
            selected_models = model_ids if selected_models is None else selected_models
            results = list(
                session.scalars(
                    select(Extraction).where(
                        Extraction.document_id.in_(ids),
                        Extraction.model_config_id.in_(selected_models),
                        Extraction.is_current,
                    )
                )
            )
            jobs = session.execute(
                select(Job.status, func.count())
                .where(Job.document_id.in_(ids), Job.model_config_id.in_(selected_models))
                .group_by(Job.status)
            ).all()
            return {
                "documents": len(ids),
                "expected_pairs": len(ids) * len(selected_models),
                "success": sum(r.valid for r in results),
                "errors": sum(not r.valid for r in results),
                "cache_hits": sum(r.cache_hit for r in results),
                "blocking_errors": sum(
                    bool(r.error)
                    and any(
                        fragment in r.error
                        for fragment in (
                            "бюджет",
                            "Неизвестный расход",
                            "Платные вызовы",
                            "Ошибка конвейера",
                            "обезличивания",
                            "Не заданы",
                            "Не задан",
                            "HTTP 401",
                            "HTTP 403",
                            "HTTP 404",
                            "Не удалось установить соединение",
                        )
                    )
                    for r in results
                ),
                "jobs": dict(jobs),
            }

        calls = session.execute(
            select(
                func.count(),
                func.sum(LLMCall.cost_rub),
                func.count().filter(LLMCall.cost_rub.is_(None)),
            ).where(LLMCall.document_id.in_(document_ids), LLMCall.model_config_id.in_(model_ids))
        ).one()
        return {
            "all": counts(document_ids),
            "in_scope": counts(scope_ids),
            "by_model": {
                model.name: {
                    "all": counts(document_ids, [model.id]),
                    "in_scope": counts(scope_ids, [model.id]),
                }
                for model in models
            },
            "physical_attempts": calls[0],
            "known_cost_rub": str(calls[1]) if calls[1] is not None else None,
            "unknown_cost_attempts": calls[2],
            "protocol_hash": protocol_hash,
        }


def main() -> None:
    args = parser().parse_args()
    try:
        print(json.dumps(execute(args, Settings()), ensure_ascii=False, indent=2))
    except ProcessingError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
