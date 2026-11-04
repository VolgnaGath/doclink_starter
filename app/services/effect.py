"""Pure scenario calculation. No database, model calls or experimental re-evaluation."""

import json
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.model_calls import fingerprint

VERSION = "effect-v1"
OUTCOMES = ("recommendation", "review", "none", "error")
RESULT_LABELS = {
    "asis_seconds": "AS-IS, с/документ",
    "tobe_seconds": "TO-BE, с/документ",
    "asis_hours_month": "AS-IS, часов/месяц",
    "tobe_hours_month": "TO-BE, часов/месяц",
    "saved_hours_month": "Экономия, часов/месяц",
    "asis_labor_rub_month": "Труд AS-IS, ₽/месяц",
    "tobe_labor_rub_month": "Труд TO-BE, ₽/месяц",
    "variable_rub_month": "Переменные расходы, ₽/месяц",
    "contract_preparation_rub_month": "Подготовка договоров, ₽/месяц",
    "fixed_rub_month": "Все постоянные расходы, ₽/месяц",
    "contribution_rub_document": "Удельный вклад c, ₽/документ",
    "net_rub_month": "Чистый эффект, ₽/месяц",
    "break_even_documents": "Точка безубыточности N*, документов/месяц",
    "payback_months": "Окупаемость, месяцев",
}
NonNegative = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Positive = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["cloud", "server"] = "cloud"
    monthly_documents: int = Field(default=1640, ge=0)
    monthly_contracts: int = Field(default=60, ge=0)
    card_seconds: NonNegative = Decimal(180)
    exact_search_seconds: NonNegative = Decimal(120)
    incomplete_search_seconds: NonNegative = Decimal(600)
    incomplete_share: Decimal = Field(default=Decimal("0.25"), ge=0, le=1, allow_inf_nan=False)
    common_seconds: NonNegative = Decimal(0)
    recommendation_seconds: NonNegative | None = None
    review_seconds: NonNegative | None = None
    none_seconds: NonNegative | None = None
    error_seconds: NonNegative | None = None
    hourly_rate_rub: NonNegative | None = None
    incoming_price_rub: NonNegative | None = None
    contract_price_rub: NonNegative | None = None
    extra_variable_rub: NonNegative | None = None
    infrastructure_rub: NonNegative | None = None
    other_fixed_rub: NonNegative | None = None
    contract_labor_rub: NonNegative | None = None
    implementation_rub: NonNegative | None = None
    v_prefill: Positive | None = None
    v_decode: Positive | None = None
    sources: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def sourced(self) -> "Scenario":
        for name in type(self).model_fields:
            if name not in {"sources", "mode"} and getattr(self, name) is not None:
                if not self.sources.get(name, "").strip():
                    raise ValueError(f"Укажите источник параметра {name}")
        return self


BASELINE = {
    "monthly_documents": 1640,
    "monthly_contracts": 60,
    "card_seconds": "180",
    "exact_search_seconds": "120",
    "incomplete_search_seconds": "600",
    "incomplete_share": "0.25",
    "common_seconds": "0",
}


def defaults() -> dict[str, Any]:
    return {
        **BASELINE,
        "sources": {
            k: "Исходные допущения черновика §1.3, таблица 1.3"
            if k != "common_seconds"
            else "Сценарное допущение: вне полной проверки дополнительных операций нет"
            for k in BASELINE
        },
    }


def calculate(scenario: Scenario, counts: dict[str, int]) -> dict[str, Any]:
    if set(counts) != set(OUTCOMES) or any(type(n) is not int or n < 0 for n in counts.values()):
        raise ValueError("Нужны четыре неотрицательных целых числителя исходов")
    n = sum(counts.values())
    if n == 0:
        raise ValueError("В выбранном прогоне нет test-документов")
    with localcontext() as ctx:
        ctx.prec = 28
        return _calculate(scenario, counts, n)


def _calculate(s: Scenario, counts: dict[str, int], n: int) -> dict[str, Any]:
    asis = (
        s.card_seconds
        + (1 - s.incomplete_share) * s.exact_search_seconds
        + s.incomplete_share * s.incomplete_search_seconds
    )
    missing = [f"{o}_seconds" for o in OUTCOMES if counts[o] and getattr(s, f"{o}_seconds") is None]
    tobe = (
        None
        if missing
        else s.common_seconds
        + sum(
            (Decimal(counts[o]) * (getattr(s, f"{o}_seconds") or Decimal(0)) for o in OUTCOMES),
            Decimal(0),
        )
        / n
    )
    hours_before = s.monthly_documents * asis / 3600
    hours_after = s.monthly_documents * tobe / 3600 if tobe is not None else None
    saved = hours_before - hours_after if hours_after is not None else None
    price = (
        s.incoming_price_rub + s.extra_variable_rub
        if s.incoming_price_rub is not None and s.extra_variable_rub is not None
        else None
    )
    preparation = (
        s.monthly_contracts * (s.contract_price_rub or Decimal(0)) + s.contract_labor_rub
        if (s.monthly_contracts == 0 or s.contract_price_rub is not None)
        and s.contract_labor_rub is not None
        else None
    )
    fixed = (
        s.infrastructure_rub + s.other_fixed_rub + preparation
        if s.infrastructure_rub is not None
        and s.other_fixed_rub is not None
        and preparation is not None
        else None
    )
    contribution = (
        (asis - tobe) * s.hourly_rate_rub / 3600 - price
        if tobe is not None and s.hourly_rate_rub is not None and price is not None
        else None
    )
    net = (
        s.monthly_documents * contribution - fixed
        if contribution is not None and fixed is not None
        else None
    )
    values = {
        "asis_seconds": asis,
        "tobe_seconds": tobe,
        "asis_hours_month": hours_before,
        "tobe_hours_month": hours_after,
        "saved_hours_month": saved,
        "asis_labor_rub_month": hours_before * s.hourly_rate_rub
        if s.hourly_rate_rub is not None
        else None,
        "tobe_labor_rub_month": hours_after * s.hourly_rate_rub
        if hours_after is not None and s.hourly_rate_rub is not None
        else None,
        "variable_rub_month": s.monthly_documents * price if price is not None else None,
        "contract_preparation_rub_month": preparation,
        "fixed_rub_month": fixed,
        "contribution_rub_document": contribution,
        "net_rub_month": net,
        "break_even_documents": fixed / contribution
        if fixed is not None and contribution is not None and contribution > 0
        else None,
        "payback_months": s.implementation_rub / net
        if s.implementation_rub is not None and net is not None and net > 0
        else None,
    }
    return {
        **{k: str(v) if v is not None else None for k, v in values.items()},
        "test_n": n,
        "outcomes": {
            o: {"numerator": counts[o], "denominator": n, "share": str(Decimal(counts[o]) / n)}
            for o in OUTCOMES
        },
        "missing_times": missing,
        "break_even_status": "unknown"
        if contribution is None or fixed is None
        else "no_finite_break_even"
        if contribution <= 0
        else "available",
        "payback_status": "unknown"
        if net is None or s.implementation_rub is None
        else "no_payback"
        if net <= 0
        else "available",
    }


def snapshot(
    scenario: Scenario, data: dict[str, Any], *, created_at: str | None = None
) -> dict[str, Any]:
    content = {
        "formula_version": VERSION,
        "created_at": created_at or datetime.now(UTC).isoformat(),
        "inputs": scenario.model_dump(mode="json"),
        "data": data,
        "result": calculate(scenario, data["counts"]),
        "label": "Сценарная оценка, не измеренный эффект внедрения",
    }
    return {**content, "calculation_hash": fingerprint(content)}


def replay(saved: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(saved, dict):
        raise ValueError("Ожидается объект снимка")
    content = {k: v for k, v in saved.items() if k != "calculation_hash"}
    if saved.get("formula_version") != VERSION or fingerprint(content) != saved.get(
        "calculation_hash"
    ):
        raise ValueError("Версия формул или хеш снимка не совпадает")
    if (
        calculate(Scenario.model_validate(saved["inputs"]), saved["data"]["counts"])
        != saved["result"]
    ):
        raise ValueError("Результат не воспроизводится")
    return saved


def number(value: str | Decimal | int | None, places: int = 2) -> str:
    if value is None:
        return "нет данных"
    return format(Decimal(value), f",.{places}f").replace(",", " ").replace(".", ",")


def markdown(saved: dict[str, Any]) -> str:
    replay(saved)
    lines = [
        "# Расчёт эффекта",
        "",
        saved["label"],
        "",
        f"Хеш: `{saved['calculation_hash']}`",
        "",
        "| Показатель | Значение |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {RESULT_LABELS[k]} | {number(v)} |"
        for k, v in saved["result"].items()
        if k in RESULT_LABELS
    )
    lines.extend(
        [
            "",
            "Полный воспроизводимый снимок (входы, единицы и источники — docs/EFFECT.md):",
            "",
            "```json",
            json.dumps(saved, ensure_ascii=False, sort_keys=True, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)
