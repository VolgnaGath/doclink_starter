"""Text-only extraction pipeline. Evaluation metadata is never part of a model request."""

import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_engine, get_session
from app.models import (
    Client,
    Counterparty,
    Document,
    Extraction,
    LLMCall,
    ModelConfig,
    Prompt,
    StageTiming,
)
from app.services.anonymization import prepare, restore
from app.services.extraction_schema import (
    PREPARATION_VERSION,
    ExtractedFields,
    check_evidence,
    regex_extract,
    truncate,
)
from app.services.model_calls import ProcessingError, complete_fields, request_hash
from app.services.providers import LLMProvider, make_provider


def configuration(model: ModelConfig, prompt: Prompt) -> dict[str, Any]:
    return {
        "preparation_version": PREPARATION_VERSION,
        "name": model.name,
        "provider": model.provider,
        "model_uri": model.model_uri,
        "placement": model.placement,
        "params": {
            "max_tokens": 2000,
            **{
                key: value
                for key, value in model.params.items()
                if key not in {"price_cached_in_1k", "cost_basis"}
            },
        },
        "price_cached_in_1k": model.params.get("price_cached_in_1k"),
        "cost_basis": model.params.get("cost_basis", "token_tariff"),
        "price_in_1k": str(model.price_in_1k) if model.price_in_1k is not None else None,
        "price_out_1k": str(model.price_out_1k) if model.price_out_1k is not None else None,
        "v_prefill": model.v_prefill,
        "v_decode": model.v_decode,
        "prompt": prompt.text,
        "schema": prompt.schema,
        "prompt_version": prompt.version,
    }


def lock_number(key: str) -> int:
    import hashlib

    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)


def prepared_request(
    session: Session, document: Document, snapshot: dict[str, Any]
) -> tuple[list[dict[str, str]], list[str], bool, list[tuple[str, str]]]:
    prepared, context, reverse = prepare(session, document)
    sent, parts, truncated = truncate(prepared)
    messages = [{"role": "user", "content": snapshot["prompt"].format(client=context, text=sent)}]
    return messages, parts, truncated, reverse


def select_card(session: Session, extraction: Extraction) -> None:
    if not extraction.valid:
        raise ProcessingError("Нельзя выбрать невалидное извлечение")
    client_id = session.scalar(
        select(Document.client_id).where(Document.id == extraction.document_id)
    )
    session.execute(select(Client.id).where(Client.id == client_id).with_for_update())
    doc = session.scalar(
        select(Document).where(Document.id == extraction.document_id).with_for_update()
    )
    if doc is None:
        raise ProcessingError("Документ не найден")
    doc.card_model_config_id, doc.card_extraction_id = extraction.model_config_id, extraction.id
    doc.doc_kind = extraction.doc_kind if extraction.doc_kind != "другое" else None
    doc.number, doc.date = extraction.number, extraction.date
    doc.counterparty_id = None
    if extraction.cp_name or extraction.cp_inn:
        cp = Counterparty(
            client_id=doc.client_id, name=extraction.cp_name or "Неизвестно", inn=extraction.cp_inn
        )
        session.add(cp)
        session.flush()
        doc.counterparty_id = cp.id
    doc.status = "extracted" if doc.doc_kind and doc.date else "review"
    from app.services.card_edits import apply_card_values, extraction_values, overrides_for

    overrides = overrides_for(session, doc.id)
    if overrides:
        apply_card_values(session, doc, {**extraction_values(extraction), **overrides})
        doc.status = "extracted" if doc.doc_kind and doc.date else "review"


def save_result(
    session: Session,
    document_id: UUID,
    model_id: UUID,
    snapshot: dict[str, Any],
    fields: ExtractedFields | None,
    call_id: UUID | None,
    checks: list[dict[str, Any]],
    cache_hit: bool,
    error: str | None,
) -> Extraction:
    client_id = session.scalar(select(Document.client_id).where(Document.id == document_id))
    session.execute(select(Client.id).where(Client.id == client_id).with_for_update())
    doc = session.scalar(select(Document).where(Document.id == document_id).with_for_update())
    previous_kind = doc.doc_kind if doc else None
    session.execute(
        update(Extraction)
        .where(
            Extraction.document_id == document_id,
            Extraction.model_config_id == model_id,
            Extraction.is_current,
        )
        .values(is_current=False)
    )
    fields = fields or ExtractedFields()
    result = Extraction(
        document_id=document_id,
        model_config_id=model_id,
        llm_call_id=call_id,
        doc_kind=fields.doc_kind,
        number=fields.number,
        date=datetime.strptime(fields.date, "%d.%m.%Y").date() if fields.date else None,
        cp_name=fields.counterparty_name,
        cp_inn=fields.counterparty_inn,
        amount=fields.amount,
        subject=fields.subject,
        contract_refs=[ref.model_dump() for ref in fields.contract_refs],
        supp_ref=fields.supp_ref.model_dump() if fields.supp_ref else None,
        invoice_ref=fields.invoice_ref.model_dump() if fields.invoice_ref else None,
        evidence=fields.evidence,
        evidence_checks=checks,
        snapshot=snapshot,
        valid=error is None,
        evidence_ok=error is None and all(c["ok"] for c in checks),
        cache_hit=cache_hit,
        error=error,
    )
    session.add(result)
    session.flush()
    if doc and doc.card_model_config_id == model_id:
        if result.valid:
            select_card(session, result)
        else:
            doc.status = "error"
    if doc and doc.source != "corpus":
        from app.models import ReviewState

        # A completed human check remains history; fresh model inputs require rechecking.
        affected = (
            select(Document.id).where(
                Document.client_id == doc.client_id,
                Document.corpus_version_id == doc.corpus_version_id,
            )
            if "договор" in {previous_kind, doc.doc_kind}
            else select(Document.id).where(Document.id == doc.id)
        )
        session.execute(
            update(ReviewState)
            .where(ReviewState.document_id.in_(affected))
            .values(needs_review=True)
        )
    return result


def extract_document(
    settings: Settings,
    document_id: UUID,
    model_id: UUID,
    prompt_id: UUID,
    snapshot: dict[str, Any],
    job_id: UUID | None = None,
    provider: LLMProvider | None = None,
) -> UUID:
    start = time.monotonic()
    stage_started = datetime.now(UTC)
    with get_session(settings.database_url) as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise ProcessingError("Документ не найден")
        from app.services.working_identity import cache_client_id

        client_id = cache_client_id(session, doc)
        try:
            prepared, context, reverse = prepare(session, doc)
            session.add(
                StageTiming(
                    document_id=document_id,
                    stage="anonymize",
                    started_at=stage_started,
                    ok=True,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            error = "Ошибка обезличивания; внешний вызов не выполнен"
            session.add(
                StageTiming(
                    document_id=document_id,
                    stage="anonymize",
                    started_at=stage_started,
                    ok=False,
                    error=error,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            )
            result = save_result(
                session, document_id, model_id, snapshot, None, None, [], False, error
            )
            session.commit()
            raise ProcessingError(error) from None
    sent, parts, truncated = truncate(prepared)
    messages = [{"role": "user", "content": snapshot["prompt"].format(client=context, text=sent)}]
    key = request_hash(snapshot, messages, client_id)
    start = time.monotonic()
    stage_started = datetime.now(UTC)
    owned_provider = None
    # Dedicated connection keeps the advisory lock across journal commits, not a row transaction.
    with get_engine(settings.database_url).connect() as guard:
        guard.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_number(key)})
        guard.commit()
        provider_lock = None
        try:
            if snapshot["provider"] == "gigachat":
                # The Freemium account supports a single generation stream.
                provider_lock = lock_number("generation-stream:gigachat")
                guard.execute(text("SELECT pg_advisory_lock(:key)"), {"key": provider_lock})
                guard.commit()
            with get_session(settings.database_url) as session:
                call, cache_hit, error, checks, fields = None, False, None, [], None
                preserved = session.scalar(
                    select(Extraction)
                    .where(
                        Extraction.document_id == document_id,
                        Extraction.model_config_id == model_id,
                        Extraction.input_hash == key,
                        Extraction.preserved_error,
                    )
                    .limit(1)
                )
                if preserved:
                    raise ProcessingError(
                        preserved.error or "Сохранённая ошибка исходного извлечения"
                    )
                try:
                    if snapshot["provider"] == "regex":
                        fields = regex_extract(sent)
                    else:
                        owned_provider = None if provider else make_provider(settings, snapshot)
                        fields, call, cache_hit = complete_fields(
                            session,
                            settings,
                            provider or owned_provider,
                            snapshot,
                            document_id,
                            client_id,
                            model_id,
                            prompt_id,
                            messages,
                            truncated,
                            job_id,
                        )
                    checks = check_evidence(fields, parts)
                    for check in checks:
                        if check.get("matched"):
                            check["original_quote"] = restore(check["matched"], reverse)
                    fields = ExtractedFields.model_validate(restore(fields.model_dump(), reverse))
                except ProcessingError as exc:
                    error = str(exc)
                except Exception as exc:
                    session.rollback()
                    error = f"Ошибка конвейера: {type(exc).__name__}"
                if call is None and snapshot["provider"] != "regex":
                    call = session.scalar(
                        select(LLMCall)
                        .where(
                            LLMCall.document_id == document_id,
                            LLMCall.model_config_id == model_id,
                            LLMCall.job_id == job_id,
                        )
                        .order_by(LLMCall.started_at.desc())
                        .limit(1)
                    )
                result = save_result(
                    session,
                    document_id,
                    model_id,
                    snapshot,
                    fields if not error else None,
                    call.id if call else None,
                    checks,
                    cache_hit,
                    error,
                )
                result.input_hash = key
                session.add(
                    StageTiming(
                        document_id=document_id,
                        stage="extract",
                        started_at=stage_started,
                        ok=not error,
                        error=error,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                )
                session.commit()
                if error:
                    raise ProcessingError(error)
                return result.id
        finally:
            if owned_provider:
                owned_provider.close()
            if provider_lock is not None:
                guard.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": provider_lock})
            guard.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_number(key)})
            guard.commit()
