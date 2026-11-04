"""Durable physical attempts, globally serialized budget reservations, and successful cache."""

import hashlib
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import LLMCall
from app.services.extraction_schema import PREPARATION_VERSION, ExtractedFields
from app.services.providers import Completion, LLMProvider, ProviderError, response_text


class ProcessingError(Exception):
    pass


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()


def request_hash(snapshot: dict[str, Any], messages: list[dict[str, str]], client_id: UUID) -> str:
    request_config = {
        key: snapshot[key]
        for key in ("provider", "model_uri", "params", "prompt", "schema", "prompt_version")
    }
    return fingerprint(
        {
            "snapshot": request_config,
            "messages": messages,
            "client": str(client_id),
            "preparation": PREPARATION_VERSION,
        }
    )


def reserve(
    session: Session, call: LLMCall, settings: Settings, messages: list[dict[str, str]]
) -> None:
    if call.paid:
        if (
            settings.llm_provider == "mock"
            or not settings.paid_calls_enabled
            or settings.budget_rub <= 0
        ):
            raise ProcessingError("Платные вызовы запрещены или бюджет не задан")
        snapshot = call.snapshot
        if snapshot["price_in_1k"] is None or snapshot["price_out_1k"] is None:
            raise ProcessingError("Не заданы тарифы модели")
        session.execute(text("SELECT pg_advisory_xact_lock(310004)"))
        # Reservations remain counted until their actual costs are known/reconciled.
        unknown = session.scalar(
            select(LLMCall.id)
            .where(
                LLMCall.paid,
                ~LLMCall.reconciled,
                (LLMCall.status == "uncertain")
                | ((LLMCall.status != "started") & LLMCall.cost_rub.is_(None)),
            )
            .limit(1)
        )
        if unknown:
            raise ProcessingError("Неизвестный расход: требуется сверка журнала")
        used = session.scalar(
            select(
                func.coalesce(func.sum(func.coalesce(LLMCall.cost_rub, LLMCall.reserved_rub)), 0)
            ).where(LLMCall.paid)
        )
        # UTF-8 bytes upper-bound byte-level tokens; overhead includes schema/context framing.
        input_bound = (
            len(json.dumps(messages, ensure_ascii=False).encode())
            + len(json.dumps(snapshot["schema"], ensure_ascii=False).encode())
            + 4096
        )
        output_bound = int(snapshot["params"].get("max_tokens", 2000))
        if not 1 <= output_bound <= 32768:
            raise ProcessingError("max_tokens должен быть от 1 до 32768")
        call.reserved_rub = (
            Decimal(input_bound) * Decimal(snapshot["price_in_1k"])
            + Decimal(output_bound) * Decimal(snapshot["price_out_1k"])
        ) / 1000
        if used + call.reserved_rub > settings.budget_rub:
            raise ProcessingError("Недостаточно бюджета для резервирования запроса")
        limits = {"yandex": settings.yandex_budget_rub, "gigachat": settings.gigachat_budget_rub}
        if any(limit is not None for limit in limits.values()):
            provider = snapshot["provider"]
            limit = limits.get(provider)
            if limit is None or limit <= 0:
                raise ProcessingError(f"Не задан положительный бюджет провайдера {provider}")
            provider_used = session.scalar(
                select(
                    func.coalesce(
                        func.sum(func.coalesce(LLMCall.cost_rub, LLMCall.reserved_rub)), 0
                    )
                ).where(LLMCall.paid, LLMCall.snapshot["provider"].astext == provider)
            )
            if provider_used + call.reserved_rub > limit:
                raise ProcessingError(f"Недостаточно бюджета провайдера {provider}")
    session.add(call)
    session.commit()  # Attempt is durable BEFORE any network activity.


def tariff_cost(snapshot: dict[str, Any], reply: Completion) -> Decimal | None:
    if reply.input_tokens is None or reply.output_tokens is None:
        return None
    cached = reply.cached_input_tokens or 0
    if cached > reply.input_tokens:
        return None
    regular_rate = Decimal(snapshot["price_in_1k"])
    cached_rate = (
        Decimal(str(snapshot["price_cached_in_1k"]))
        if snapshot.get("price_cached_in_1k") is not None
        else regular_rate
    )
    return (
        (reply.input_tokens - cached) * regular_rate
        + cached * cached_rate
        + reply.output_tokens * Decimal(snapshot["price_out_1k"])
    ) / 1000


def complete_fields(
    session: Session,
    settings: Settings,
    provider: LLMProvider,
    snapshot: dict[str, Any],
    document_id: UUID,
    client_id: UUID,
    model_id: UUID,
    prompt_id: UUID,
    messages: list[dict[str, str]],
    truncated: bool,
    job_id: UUID | None = None,
) -> tuple[ExtractedFields, LLMCall, bool]:
    """Caller holds a session-level advisory lock for the original request key.

    One initial request + one correction, at most 3 safe transport retries in total.
    No job retries multiply this bound. Uncertain sends never retry automatically.
    """
    retries = 0
    root_key = request_hash(snapshot, messages, client_id)
    root_cached = session.scalar(
        select(LLMCall)
        .where(
            LLMCall.root_input_hash == root_key,
            LLMCall.model_config_id == model_id,
            LLMCall.prompt_id == prompt_id,
            LLMCall.status == "success",
        )
        .limit(1)
    )
    if root_cached:
        return (
            ExtractedFields.model_validate_json(response_text(root_cached.response_raw)),
            root_cached,
            True,
        )
    correction_start = 0
    if job_id is not None:
        prior = list(
            session.scalars(
                select(LLMCall)
                .where(LLMCall.job_id == job_id, LLMCall.root_input_hash == root_key)
                .order_by(LLMCall.started_at)
            )
        )
        retries = sum(call.status == "error" for call in prior)
        invalid = [call for call in prior if call.status == "invalid"]
        if invalid:
            if any(call.input_hash != root_key for call in invalid):
                raise ProcessingError("Невалидное извлечение после проверки схемы")
            # A budget refusal happens before journaling/sending the correction.
            # Resume the same job from its durable initial answer, never resend it.
            initial = invalid[-1]
            messages = [
                *messages,
                {"role": "assistant", "content": response_text(initial.response_raw)},
                {"role": "user", "content": "Исправь JSON по схеме. Ошибки: " + initial.error},
            ]
            correction_start = 1
    for correction in range(correction_start, 2):
        key = request_hash(snapshot, messages, client_id)
        cached = session.scalar(
            select(LLMCall).where(
                LLMCall.input_hash == key,
                LLMCall.model_config_id == model_id,
                LLMCall.prompt_id == prompt_id,
                LLMCall.status == "success",
            )
        )
        if cached:
            return (
                ExtractedFields.model_validate_json(response_text(cached.response_raw)),
                cached,
                True,
            )
        # A crash after a send is not permission to repeat it on a manual rerun.
        unresolved = session.scalar(
            select(LLMCall.id)
            .where(
                LLMCall.input_hash == key,
                LLMCall.model_config_id == model_id,
                LLMCall.status.in_(["started", "uncertain"]),
                ~LLMCall.reconciled,
            )
            .limit(1)
        )
        if unresolved:
            raise ProcessingError("Исход предыдущего запроса неизвестен; нужна сверка")
        while True:
            call = LLMCall(
                model_config_id=model_id,
                prompt_id=prompt_id,
                document_id=document_id,
                job_id=job_id,
                input_hash=key,
                root_input_hash=root_key,
                snapshot=snapshot,
                paid=snapshot["provider"] in {"yandex", "gigachat"},
                truncated=truncated,
                status="started",
            )
            reserve(session, call, settings, messages)
            started = time.monotonic()
            try:
                reply = provider.complete(messages, snapshot["schema"])
            except ProviderError as exc:
                call.latency_ms = int((time.monotonic() - started) * 1000)
                call.status = "uncertain" if exc.uncertain else "error"
                call.error, call.response_raw = str(exc), exc.raw
                call.finished_at = datetime.now(UTC)
                # Explicit rejection / failure before send has no generation charge.
                call.cost_rub = None if exc.uncertain else Decimal(0)
                session.commit()
                if exc.retryable and not exc.uncertain and retries < 3:
                    time.sleep(2**retries)
                    retries += 1
                    continue
                raise ProcessingError(str(exc)) from exc
            except Exception as exc:
                call.latency_ms = int((time.monotonic() - started) * 1000)
                call.status, call.cost_rub = "uncertain", None
                call.error = f"Неопределённый исход: {type(exc).__name__}"
                call.finished_at = datetime.now(UTC)
                session.commit()
                raise ProcessingError(call.error) from None
            call.latency_ms = int((time.monotonic() - started) * 1000)
            call.response_raw = reply.raw
            call.input_tokens, call.output_tokens = reply.input_tokens, reply.output_tokens
            call.finished_at = datetime.now(UTC)
            call.snapshot = {
                **call.snapshot,
                "usage_details": {
                    "cached_input_tokens": reply.cached_input_tokens,
                    "cash_debit_rub": None,
                    "cash_debit_note": "Списание не сверено; cost_rub — тарифный эквивалент",
                },
            }
            if not call.paid:
                call.cost_rub = Decimal(0)
            elif reply.input_tokens is not None and reply.output_tokens is not None:
                call.cost_rub = tariff_cost(snapshot, reply)
            try:
                fields = ExtractedFields.model_validate_json(reply.text)
            except ValidationError as exc:
                call.status = "invalid"
                # Omit input_value from validation details (it can be large/untrusted).
                call.error = json.dumps(
                    exc.errors(include_input=False, include_context=False), ensure_ascii=False
                )
                session.commit()
                if correction == 1 or (call.paid and call.cost_rub is None):
                    raise ProcessingError("Невалидное извлечение после проверки схемы") from exc
                messages = [
                    *messages,
                    {"role": "assistant", "content": reply.text},
                    {"role": "user", "content": "Исправь JSON по схеме. Ошибки: " + call.error},
                ]
                break
            call.status = "success"
            session.commit()
            return fields, call, False
    raise ProcessingError("Извлечение не завершено")
