"""Snapshots and pre-registered human measurement copies. No paid calls."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.models import WorkingCopy
from app.services.effect import OUTCOMES, Scenario, markdown, replay, snapshot
from app.services.effect_data import local_estimate, run_data, scenario_defaults
from app.services.effect_measurements import validate_protocol
from app.services.model_calls import fingerprint
from app.services.working_sets import WorkingSetCreate, create_working_set


def prepare_measurements(session: Session, run_id: UUID, per_outcome: int = 3) -> dict[str, Any]:
    if not 1 <= per_outcome <= 30:
        raise ValueError("Число документов каждого исхода: от 1 до 30")
    data = run_data(session, run_id)
    selected = []
    for outcome in OUTCOMES:
        selected.extend(
            sorted(
                (r for r in data["results"] if r["outcome"] == outcome),
                key=lambda r: r["result_id"],
            )[:per_outcome]
        )
    work = create_working_set(
        session,
        WorkingSetCreate(
            name=f"Замеры effect-review-v1 {run_id}, k={per_outcome}",
            corpus_version_id=UUID(data["corpus_version_id"]),
            document_ids=[UUID(r["document_id"]) for r in selected],
            result_ids=[UUID(r["result_id"]) for r in selected],
            idempotency_key=uuid5(run_id, f"effect-review-v1:{per_outcome}"),
        ),
    )
    copies = {
        str(c.source_result_id): c
        for c in session.scalars(select(WorkingCopy).where(WorkingCopy.working_set_id == work.id))
    }
    content = {
        "version": "effect-review-v1",
        "run_id": str(run_id),
        "dataset_hash": data["dataset_hash"],
        "run_protocol_hash": data["protocol_hash"],
        "registered_at": work.created_at.isoformat(),
        "working_set_id": str(work.id),
        "per_outcome": per_outcome,
        "selection_rule": (
            "Первые k test-результатов каждого исхода по ID; "
            "порядок исходов recommendation/review/none/error"
        ),
        "session_rule": "Первая открытая сессия; незавершённую/изменённую не заменять повтором",
        "documents": [
            {
                "source_result_id": r["result_id"],
                "outcome": r["outcome"],
                "document_id": str(copies[r["result_id"]].document_id),
                "url": f"/review/{copies[r['result_id']].document_id}",
            }
            for r in selected
        ],
    }
    return {**content, "protocol_hash": fingerprint(content)}


def extension_selection(
    results: list[dict[str, Any]], base: dict[str, Any], allocation: dict[str, int]
) -> list[dict[str, Any]]:
    validate_protocol(base)
    if (
        set(allocation) != set(OUTCOMES)
        or any(type(n) is not int or not 0 <= n <= 30 for n in allocation.values())
        or not sum(allocation.values())
    ):
        raise ValueError("Задайте число 0–30 для каждого исхода; сумма должна быть положительной")
    excluded = {d["source_result_id"] for d in base["documents"]}
    selected = []
    for outcome in OUTCOMES:
        available = sorted(
            (r for r in results if r["outcome"] == outcome and r["result_id"] not in excluded),
            key=lambda r: r["result_id"],
        )
        if len(available) < allocation[outcome]:
            raise ValueError(f"Недостаточно новых документов исхода {outcome}")
        selected.extend(available[: allocation[outcome]])
    return selected


def extend_measurements(
    session: Session, run_id: UUID, base: dict[str, Any], allocation: dict[str, int]
) -> dict[str, Any]:
    data = run_data(session, run_id, base)
    selected = extension_selection(data["results"], base, allocation)
    key = fingerprint({"base": base["protocol_hash"], "allocation": allocation})
    work = create_working_set(
        session,
        WorkingSetCreate(
            name=f"Расширение замеров {run_id}, +{len(selected)}",
            corpus_version_id=UUID(data["corpus_version_id"]),
            document_ids=[UUID(r["document_id"]) for r in selected],
            result_ids=[UUID(r["result_id"]) for r in selected],
            idempotency_key=uuid5(run_id, f"effect-review-extension:{key}"),
        ),
    )
    copies = {
        str(c.source_result_id): c
        for c in session.scalars(select(WorkingCopy).where(WorkingCopy.working_set_id == work.id))
    }
    extension = {
        "version": "effect-review-v1",
        "run_id": str(run_id),
        "dataset_hash": data["dataset_hash"],
        "run_protocol_hash": data["protocol_hash"],
        "registered_at": work.created_at.isoformat(),
        "working_set_id": str(work.id),
        "base_protocol_hash": base["protocol_hash"],
        "allocation": allocation,
        "selection_rule": (
            "Следующие test-результаты по ID каждого исхода, исключая базовый протокол"
        ),
        "session_rule": base["session_rule"],
        "documents": [
            {
                "source_result_id": r["result_id"],
                "outcome": r["outcome"],
                "document_id": str(copies[r["result_id"]].document_id),
                "url": f"/review/{copies[r['result_id']].document_id}",
            }
            for r in selected
        ],
    }
    extension["protocol_hash"] = fingerprint(extension)
    batches = [*base.get("batches", [base]), extension]
    content = {
        "version": "effect-review-v2",
        "run_id": str(run_id),
        "dataset_hash": data["dataset_hash"],
        "run_protocol_hash": data["protocol_hash"],
        "registered_at": work.created_at.isoformat(),
        "session_rule": base["session_rule"],
        "batches": batches,
        "documents": [doc for batch in batches for doc in batch["documents"]],
    }
    return {**content, "protocol_hash": fingerprint(content)}


def extension_instructions(protocol: dict[str, Any]) -> str:
    extension = protocol["batches"][-1]
    lines = [
        f"# Расширение человеческих замеров до {len(protocol['documents'])} документов",
        "",
        f"Общий протокол: `{protocol['protocol_hash']}`.",
        f"Регистрация расширения: {extension['registered_at']}.",
        f"Новый набор: http://localhost:8000/working-sets/{extension['working_set_id']}.",
        "",
        f"Ниже только новые карточки: {len(extension['documents'])}. Старые не повторяйте.",
        "Открывайте по одной, в указанном порядке, только когда готовы работать: "
        "открытие запускает таймер.",
        "Прочитайте документ, проверьте поля/основания и кандидатов, "
        "примите все решения и завершите проверку.",
        "Не подтверждайте связь ради приёмки. Перерыв делайте между карточками, не внутри сессии.",
        "Первая сессия учитывается; незавершённые/изменённые не заменяются повтором, "
        "причины остаются в аудите.",
        "Ошибочное решение не исключается по правильности; "
        "правка входов/reopen исключает замер по прежнему правилу.",
        "Если исход рабочей копии отличается от зарегистрированного, "
        "сессия сохраняется в аудите с исключением different_outcome.",
        "",
        "| № | Исход алгоритма | Карточка |",
        "|---:|---|---|",
    ]
    for index, doc in enumerate(extension["documents"], 1):
        label = {
            "recommendation": "Рекомендация",
            "review": "Разбор",
            "none": "Нет кандидата",
            "error": "Ошибка обработки",
        }[doc["outcome"]]
        lines.append(f"| {index} | {label} | [Открыть](http://localhost:8000{doc['url']}) |")
    lines.extend(
        [
            "",
            "После завершения сообщите об этом. Итоговый refresh JSON/Markdown будет перезаписан",
            "по реальным сессиям обоих наборов. "
            "До этого объём протокола не означает число готовых замеров.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["snapshot", "replay", "prepare-measurements", "extend-measurements"]
    )
    parser.add_argument("--run", type=UUID)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-outcome", type=int, default=3)
    parser.add_argument("--base-protocol", type=Path)
    parser.add_argument("--allocation", help="recommendation=11,review=10,none=0,error=0")
    args = parser.parse_args()
    if args.command == "replay":
        saved = replay(json.loads(args.input.read_text()))
    else:
        if args.run is None:
            parser.error("--run обязателен")
        with get_session(Settings().database_url) as session:
            if args.command in {"prepare-measurements", "extend-measurements"}:
                if args.command == "extend-measurements":
                    if args.base_protocol is None or args.allocation is None:
                        parser.error("--base-protocol и --allocation обязательны для расширения")
                    base = json.loads(args.base_protocol.read_text())
                    allocation = {
                        k: int(v)
                        for k, v in (item.split("=") for item in args.allocation.split(","))
                    }
                    saved = extend_measurements(session, args.run, base, allocation)
                else:
                    saved = prepare_measurements(session, args.run, args.per_outcome)
                if args.output.exists():
                    previous = json.loads(args.output.read_text())
                    validate_protocol(previous)
                    if previous != saved:
                        raise ValueError("Нельзя перезаписать другой протокол")
                session.commit()
            else:
                protocol = json.loads(args.protocol.read_text()) if args.protocol else None
                data = run_data(session, args.run, protocol)
                values = (
                    json.loads(args.input.read_text()) if args.input else scenario_defaults(data)
                )
                scenario = Scenario.model_validate(values)
                data["local_estimate"] = local_estimate(scenario, data)
                saved = snapshot(scenario, data, created_at=datetime.now(UTC).isoformat())
    args.output.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n")
    if args.command == "extend-measurements":
        args.output.with_suffix(".md").write_text(extension_instructions(saved))
    elif args.command != "prepare-measurements":
        args.output.with_suffix(".md").write_text(markdown(saved))
    print(
        json.dumps(
            {
                k: saved[k]
                for k in ("calculation_hash", "result", "protocol_hash", "documents")
                if k in saved
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
