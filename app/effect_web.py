"""Protected scenario form and immutable JSON/Markdown snapshot downloads."""

import json
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select

from app.auth import require_user
from app.documents import DB, render
from app.effect_cli import prepare_measurements
from app.models import ExperimentRun
from app.services.effect import (
    RESOURCE_NOTE,
    RESULT_LABELS,
    Scenario,
    defaults,
    markdown,
    number,
    replay,
    snapshot,
)
from app.services.effect_data import local_estimate, run_data, scenario_defaults

router = APIRouter(prefix="/effect", include_in_schema=False, dependencies=[Depends(require_user)])

FIELDS = {
    "monthly_documents": "Входящие для связей, документов/месяц",
    "monthly_contracts": "Новые договоры, документов/месяц",
    "card_seconds": "AS-IS: заполнение карточки, с/документ",
    "exact_search_seconds": "AS-IS: точный поиск, с/документ",
    "incomplete_search_seconds": "AS-IS: неполный поиск, с/документ",
    "incomplete_share": "Доля неполных ссылок AS-IS (0–1)",
    "common_seconds": "TO-BE: вне полной проверки, с/документ",
    "recommendation_seconds": "Полная проверка рекомендации, с/документ",
    "review_seconds": "Полный ручной разбор, с/документ",
    "none_seconds": "Полная проверка отсутствия кандидата, с/документ",
    "error_seconds": "Полная обработка ошибки, с/документ",
    "hourly_rate_rub": "Оценочная стоимость часа ресурса (необязательно), ₽/час",
    "incoming_price_rub": "Прогноз обработки входящего, ₽/документ",
    "contract_price_rub": "Прогноз подготовки договора, ₽/документ",
    "extra_variable_rub": "Прочие переменные расходы, ₽/входящий",
    "infrastructure_rub": "API-инфраструктура / сервер, ₽/месяц",
    "other_fixed_rub": "Прочие постоянные расходы, ₽/месяц",
    "contract_labor_rub": "Отдельный труд подготовки договоров, ₽/месяц",
    "implementation_rub": "Внедрение, ₽ разово",
    "v_prefill": "Допущение скорости prefill GPU, токенов/с",
    "v_decode": "Допущение скорости decode GPU, токенов/с",
}
OUTCOME_LABELS = {
    "recommendation": "Рекомендация",
    "review": "Разбор",
    "none": "Нет кандидата",
    "error": "Ошибка обработки",
}


def page(request: Request, session: DB, **context: Any) -> Response:
    return render(
        request,
        "effect.html",
        fields=FIELDS,
        result_labels=RESULT_LABELS,
        resource_note=RESOURCE_NOTE,
        number=number,
        outcome_labels=OUTCOME_LABELS,
        calculation_statuses={
            "unknown": "нет данных",
            "available": "рассчитано",
            "no_finite_break_even": "нет конечной точки",
            "no_payback": "нет окупаемости",
        },
        scope_labels={
            "contracts": "Договоры-кандидаты",
            "incoming": "Входящие test",
            "other": "Прочие исходные вычисления",
        },
        runs=session.scalars(
            select(ExperimentRun)
            .where(ExperimentRun.status == "done")
            .order_by(ExperimentRun.started_at.desc(), ExperimentRun.variant)
        ).all(),
        **context,
    )


@router.get("")
def index(request: Request, session: DB, run_id: UUID | None = None) -> Response:
    try:
        data = run_data(session, run_id) if run_id else None
        values = scenario_defaults(data) if data else defaults()
        return page(
            request,
            session,
            values=values,
            data=data,
            saved=None,
            run_id=str(run_id) if run_id else "",
            error=None,
            protocol_text="",
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("")
async def compute(request: Request, session: DB) -> Response:
    form = await request.form()
    values: dict[str, Any] = {"sources": {}, "mode": str(form.get("mode", "cloud"))}
    run_id, protocol_text = str(form.get("run_id", "")), str(form.get("measurement_protocol", ""))
    data = None
    try:
        data = run_data(
            session, UUID(run_id), json.loads(protocol_text) if protocol_text.strip() else None
        )
        base = scenario_defaults(data, values["mode"])
        for field in FIELDS:
            raw = str(form.get(field, "")).strip()
            measured_field = field in {
                "recommendation_seconds",
                "review_seconds",
                "none_seconds",
                "error_seconds",
            }
            values[field] = (
                raw
                if raw
                else (base.get(field) if protocol_text.strip() and measured_field else None)
            )
            # Only protocol-backed human times may fill a previously empty time field.
            source = str(form.get(f"source_{field}", "")).strip()
            if (
                values["mode"] == "server"
                and field in {"incoming_price_rub", "contract_price_rub"}
                and source.startswith("Прогноз по исходным физическим вызовам")
            ):
                raise ValueError(
                    "Для сервера задайте собственную цену и источник, "
                    "облачный тариф остаётся справочным"
                )
            if not source and values[field] == base.get(field):
                source = base["sources"].get(field, "")
            if field in base and values[field] is not None and base[field] is not None:
                from decimal import Decimal

                if Decimal(str(values[field])) != Decimal(str(base[field])) and source == base[
                    "sources"
                ].get(field):
                    raise ValueError(f"Значение {FIELDS[field]} изменено: укажите новый источник")
            values["sources"][field] = source
        scenario = Scenario.model_validate(values)
        data["local_estimate"] = local_estimate(scenario, data)
        saved = snapshot(scenario, data)
        return page(
            request,
            session,
            values=saved["inputs"],
            data=data,
            saved=saved,
            snapshot_json=json.dumps(saved, ensure_ascii=False),
            run_id=run_id,
            error=None,
            protocol_text=protocol_text,
        )
    except (ValueError, KeyError, TypeError) as exc:
        return page(
            request,
            session,
            values=values,
            data=data,
            saved=None,
            run_id=run_id,
            error=str(exc),
            protocol_text=protocol_text,
        )


@router.post("/measurements")
def measurements(
    session: DB, run_id: Annotated[UUID, Form()], per_outcome: Annotated[int, Form()] = 3
) -> Response:
    try:
        protocol = prepare_measurements(session, run_id, per_outcome)
        session.commit()
        return Response(
            json.dumps(protocol, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="effect-measurements.json"'},
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/export")
def export(
    snapshot_json: Annotated[str, Form()], format: Annotated[Literal["json", "md"], Form()]
) -> Response:
    try:
        saved = replay(json.loads(snapshot_json))
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, "Некорректный снимок расчёта") from exc
    content = (
        json.dumps(saved, ensure_ascii=False, indent=2) + "\n"
        if format == "json"
        else markdown(saved)
    )
    return Response(
        content,
        media_type="application/json" if format == "json" else "text/markdown",
        headers={
            "Content-Disposition": (
                f'attachment; filename="effect-{saved["calculation_hash"][:12]}.{format}"'
            )
        },
    )
