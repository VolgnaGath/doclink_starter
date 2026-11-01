import json
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select, text, update

from app.auth import require_user
from app.documents import DB, render
from app.models import LLMCall, ModelConfig

router = APIRouter(include_in_schema=False, dependencies=[Depends(require_user)])


@router.get("/models")
def models(request: Request, session: DB) -> Response:
    return render(
        request,
        "models.html",
        models=session.scalars(
            select(ModelConfig).order_by(ModelConfig.created_at, ModelConfig.name)
        ).all(),
    )


@router.post("/models/{model_id}")
def edit_model(
    session: DB,
    model_id: UUID,
    model_uri: Annotated[str, Form()],
    params: Annotated[str, Form()],
    price_in: Annotated[str, Form()] = "",
    price_out: Annotated[str, Form()] = "",
    active: Annotated[bool, Form()] = False,
    default_card: Annotated[bool, Form()] = False,
) -> Response:
    session.execute(text("SELECT pg_advisory_xact_lock(310005)"))
    model = session.get(ModelConfig, model_id)
    if model is None:
        raise HTTPException(404, "Модель не найдена")
    try:
        parsed = json.loads(params)
        if not isinstance(parsed, dict) or set(parsed) - {
            "temperature",
            "max_tokens",
            "top_p",
            "seed",
            "json_mode",
        }:
            raise ValueError("Недопустимые параметры")
        if not 1 <= int(parsed.get("max_tokens", 2000)) <= 32768:
            raise ValueError("Недопустимый max_tokens")
        prices = [Decimal(value) if value.strip() else None for value in (price_in, price_out)]
        if any(p is not None and (not p.is_finite() or p < 0) for p in prices):
            raise ValueError("Недопустимый тариф")
        if (
            not model_uri.strip()
            or len(model_uri) > 500
            or "://" in model_uri
            and not model_uri.startswith("gpt://")
        ):
            raise ValueError("Недопустимый идентификатор модели")
    except (ValueError, ArithmeticError, TypeError):
        raise HTTPException(422, "Проверьте URI, JSON-параметры и тарифы") from None
    if default_card:
        session.execute(update(ModelConfig).values(default_card=False))
    model.model_uri, model.params = model_uri.strip(), parsed
    model.price_in_1k, model.price_out_1k = prices
    model.active, model.default_card = active, default_card
    session.commit()
    return RedirectResponse("/models", status_code=303)


@router.get("/calls")
def calls(
    request: Request,
    session: DB,
    model_id: UUID | None = None,
    status: str = "",
    page: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    filters = []
    if model_id:
        filters.append(LLMCall.model_config_id == model_id)
    if status:
        filters.append(LLMCall.status == status)
    totals = session.execute(
        select(
            func.count(LLMCall.id),
            func.sum(LLMCall.cost_rub),
            func.sum(LLMCall.input_tokens),
            func.sum(LLMCall.output_tokens),
            func.count(LLMCall.id).filter(LLMCall.cost_rub.is_(None)),
        ).where(*filters)
    ).one()
    return render(
        request,
        "calls.html",
        calls=session.scalars(
            select(LLMCall)
            .where(*filters)
            .order_by(LLMCall.started_at.desc())
            .offset((page - 1) * 50)
            .limit(50)
        ).all(),
        totals=totals,
        models=session.scalars(select(ModelConfig)).all(),
        selected_model=str(model_id or ""),
        selected_status=status,
        page=page,
    )


@router.get("/calls/{call_id}")
def call_detail(request: Request, session: DB, call_id: UUID) -> Response:
    call = session.get(LLMCall, call_id)
    if call is None:
        raise HTTPException(404, "Вызов не найден")
    return render(request, "call_detail.html", call=call)
