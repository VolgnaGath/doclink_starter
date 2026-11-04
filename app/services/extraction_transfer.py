"""Offline preflight and transactional cross-version reuse, separate from evaluation."""

import copy
import json
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    CorpusVersion,
    Document,
    DocumentText,
    Extraction,
    Job,
    LLMCall,
    ModelConfig,
    Prompt,
    StageTiming,
)
from app.services.anonymization import restore
from app.services.extraction import configuration, prepared_request, save_result
from app.services.extraction_protocols import test_history
from app.services.extraction_schema import ExtractedFields, check_evidence
from app.services.model_calls import ProcessingError, fingerprint, request_hash
from app.services.providers import response_text


def preflight(
    session: Session, source_id: UUID, target_id: UUID, settings: Settings
) -> dict[str, Any]:
    if source_id == target_id:
        raise ProcessingError("Исходная и целевая версии должны различаться")
    source, target = session.get(CorpusVersion, source_id), session.get(CorpusVersion, target_id)
    if source is None or target is None:
        raise ProcessingError("Версия корпуса не найдена")
    prompt = session.scalar(
        select(Prompt)
        .where(Prompt.name == "extract")
        .order_by(Prompt.created_at.desc(), Prompt.version.desc())
        .limit(1)
    )
    models = list(
        session.scalars(select(ModelConfig).where(ModelConfig.active).order_by(ModelConfig.name))
    )
    if prompt is None or not models or sum(model.default_card for model in models) != 1:
        raise ProcessingError("Нужны активные модели, инструкция и один источник карточки")
    originals = {
        d.corpus_doc_id: d
        for d in session.scalars(select(Document).where(Document.corpus_version_id == source_id))
    }
    documents = list(
        session.scalars(
            select(Document)
            .where(Document.corpus_version_id == target_id)
            .order_by(Document.corpus_doc_id)
        )
    )
    if not documents or not originals:
        raise ProcessingError("В версии нет документов")
    blockers = []
    if set(originals) - {d.corpus_doc_id for d in documents}:
        blockers.append("Целевая версия не содержит все стабильные идентификаторы исходной")
    pairs = []
    snapshots = [configuration(model, prompt) for model in models]
    reservations: dict[str, list[Decimal]] = {}
    for document in documents:
        original = originals.get(document.corpus_doc_id)
        stored = session.get(DocumentText, document.id)
        old_text = session.get(DocumentText, original.id) if original else None
        identical = (
            original is not None
            and stored is not None
            and old_text is not None
            and (
                original.client_id == document.client_id
                and original.sha256 == document.sha256
                and original.is_anonymized == document.is_anonymized
                and old_text.text == stored.text
            )
        )
        if original and not identical:
            blockers.append(f"Изменены клиент/содержимое/подготовка: {document.corpus_doc_id}")
        for model, snapshot in zip(models, snapshots, strict=True):
            row: dict[str, Any] = {
                "document_id": str(document.id),
                "corpus_doc_id": document.corpus_doc_id,
                "source_document_id": str(original.id) if original else None,
                "model_id": str(model.id),
                "model": model.name,
                "prompt_id": str(prompt.id),
                "snapshot_hash": fingerprint(snapshot),
            }
            try:
                messages, _, truncated, _ = prepared_request(session, document, snapshot)
            except (ValueError, KeyError) as exc:
                blockers.append(f"Подготовка {document.corpus_doc_id}: {type(exc).__name__}")
                row["action"] = "blocked"
                pairs.append(row)
                continue
            key = request_hash(snapshot, messages, document.client_id)
            row.update(input_hash=key, truncated=truncated)
            current = session.scalar(
                select(Extraction).where(
                    Extraction.document_id == document.id,
                    Extraction.model_config_id == model.id,
                    Extraction.is_current,
                )
            )
            previous = (
                session.scalar(
                    select(Extraction).where(
                        Extraction.document_id == original.id,
                        Extraction.model_config_id == model.id,
                        Extraction.is_current,
                    )
                )
                if identical
                else None
            )
            call = (
                session.get(LLMCall, previous.llm_call_id)
                if previous and previous.llm_call_id
                else None
            )
            row["source_extraction_id"] = str(previous.id) if previous else None
            if previous and not previous.valid:
                # Terminal evaluation errors must never be upgraded through a global success cache.
                if (
                    not call
                    or call.status != "invalid"
                    or call.root_input_hash != key
                    or call.prompt_id != prompt.id
                ):
                    row["action"] = "blocked"
                    blockers.append(
                        "Ошибка источника не соответствует ключу/схеме: "
                        f"{document.corpus_doc_id}/{model.name}"
                    )
                else:
                    row.update(action="preserve_error", call_id=str(call.id))
            else:
                cached = session.scalar(
                    select(LLMCall)
                    .where(
                        LLMCall.root_input_hash == key,
                        LLMCall.model_config_id == model.id,
                        LLMCall.prompt_id == prompt.id,
                        LLMCall.status == "success",
                    )
                    .order_by(LLMCall.started_at)
                    .limit(1)
                )
                if cached:
                    row.update(action="reuse_success", call_id=str(cached.id))
                elif original:
                    row["action"] = "blocked"
                    blockers.append(
                        f"Нет совпадающего успешного ключа: {document.corpus_doc_id}/{model.name}"
                    )
                else:
                    row["action"] = "generate"
            if current:
                current_call = (
                    session.get(LLMCall, current.llm_call_id) if current.llm_call_id else None
                )
                if current.snapshot == snapshot and (
                    current_call
                    and current_call.root_input_hash == key
                    or current.input_hash == key
                ):
                    row.update(
                        action="existing",
                        extraction_id=str(current.id),
                        valid=current.valid,
                        preserved_error=current.preserved_error,
                    )
                    if (
                        not current.valid
                        and not current.preserved_error
                        and "бюджет" in (current.error or "").lower()
                    ):
                        row["action"] = "resume_budget"
                else:
                    blockers.append(
                        "Цель уже содержит другое извлечение: "
                        f"{document.corpus_doc_id}/{model.name}"
                    )
                    row["action"] = "blocked"
            active = session.scalar(
                select(Job.id)
                .where(
                    Job.document_id == document.id,
                    Job.type == "extract",
                    Job.model_config_id == model.id,
                    Job.status.in_(["queued", "running"]),
                )
                .limit(1)
            )
            if active:
                blockers.append(f"Есть активное задание: {document.corpus_doc_id}/{model.name}")
            if (
                row["action"] in {"generate", "resume_budget"}
                and snapshot["price_in_1k"] is not None
                and snapshot["price_out_1k"] is not None
            ):
                input_bound = (
                    len(json.dumps(messages, ensure_ascii=False).encode())
                    + len(json.dumps(snapshot["schema"], ensure_ascii=False).encode())
                    + 4096
                )
                output_bound = int(snapshot["params"].get("max_tokens", 2000))
                initial = (
                    Decimal(input_bound) * Decimal(snapshot["price_in_1k"])
                    + Decimal(output_bound) * Decimal(snapshot["price_out_1k"])
                ) / 1000
                # One correction includes the full initial answer and validation feedback.
                correction = (
                    initial
                    + Decimal(output_bound * 4 + 8192) * Decimal(snapshot["price_in_1k"]) / 1000
                )
                reservations.setdefault(model.provider, []).append(initial + correction)
                row["reservation_initial_rub"] = str(initial)
                row["reservation_with_correction_rub"] = str(initial + correction)
            pairs.append(row)
    calls = list(session.scalars(select(LLMCall).where(LLMCall.paid)))
    estimates = {}
    for model, snapshot in zip(models, snapshots, strict=True):
        roots: dict[str, list[LLMCall]] = defaultdict(list)
        for call in calls:
            if call.model_config_id == model.id and all(
                call.snapshot.get(key) == snapshot.get(key)
                for key in ("model_uri", "params", "prompt", "schema", "prompt_version")
            ):
                roots[call.root_input_hash].append(call)
        complete = [rows for rows in roots.values() if all(c.cost_rub is not None for c in rows)]
        count = sum(
            p["action"] in {"generate", "resume_budget"} and p["model_id"] == str(model.id)
            for p in pairs
        )
        mean = (
            sum((sum((c.cost_rub for c in rows), Decimal(0)) for rows in complete), Decimal(0))
            / len(complete)
            if complete
            else None
        )
        estimates[model.name] = {
            "provider": model.provider,
            "new_pairs": count,
            "historical_roots": len(complete),
            "historical_correction_roots": sum(
                any(c.input_hash != c.root_input_hash for c in rows) for rows in complete
            ),
            "mean_pair_cost_with_observed_corrections_rub": str(mean) if mean is not None else None,
            "estimated_new_cost_rub": str(mean * count)
            if mean is not None
            else ("0" if count == 0 else None),
        }
    budgets = {}
    providers = {str(model.id): model.provider for model in models}
    for provider in ("yandex", "gigachat"):
        rows = [call for call in calls if call.snapshot["provider"] == provider]
        used = sum(
            (c.cost_rub if c.cost_rub is not None else c.reserved_rub or Decimal(0) for c in rows),
            Decimal(0),
        )
        limit = getattr(settings, f"{provider}_budget_rub")
        bound = sum(reservations.get(provider, []), Decimal(0))
        estimated = [
            e["estimated_new_cost_rub"] for e in estimates.values() if e["provider"] == provider
        ]
        predicted = (
            sum((Decimal(value) for value in estimated), Decimal(0))
            if all(value is not None for value in estimated)
            else None
        )
        budgets[provider] = {
            "used_or_reserved_rub": str(used),
            "limit_rub": str(limit) if limit is not None else None,
            "remaining_rub": str(limit - used) if limit is not None else None,
            "new_pairs": sum(
                p["action"] in {"generate", "resume_budget"}
                and p["model_id"] in {str(m.id) for m in models if m.provider == provider}
                for p in pairs
            ),
            "reservation_bound_with_corrections_rub": str(bound),
            "estimated_new_cost_with_observed_corrections_rub": str(predicted)
            if predicted is not None
            else None,
            "max_initial_reservation_rub": str(
                max(
                    (
                        Decimal(p["reservation_initial_rub"])
                        for p in pairs
                        if p.get("reservation_initial_rub") and providers[p["model_id"]] == provider
                    ),
                    default=Decimal(0),
                )
            ),
            "unknown_attempts": sum(c.cost_rub is None and not c.reconciled for c in rows),
        }
    protocol_hash = fingerprint(
        {"models": snapshots, "preparation": snapshots[0]["preparation_version"]}
    )
    return {
        "source_version": str(source.id),
        "source_fingerprint": source.fingerprint,
        "target_version": str(target.id),
        "target_fingerprint": target.fingerprint,
        "extraction_protocol_hash": protocol_hash,
        "models": snapshots,
        "card_model_id": str(next(model.id for model in models if model.default_card)),
        "test_history": test_history(session, target),
        "source_test_history": test_history(session, source),
        "counts": dict(Counter(row["action"] for row in pairs)),
        "pairs": pairs,
        "budgets": budgets,
        "model_cost_estimates": estimates,
        "total_limit_rub": str(settings.budget_rub),
        "total_used_or_reserved_rub": str(
            sum(
                (
                    c.cost_rub if c.cost_rub is not None else c.reserved_rub or Decimal(0)
                    for c in calls
                ),
                Decimal(0),
            )
        ),
        "blockers": sorted(set(blockers)),
        "notes": [
            "No generations or jobs; preparation dictionaries are persisted locally.",
            "Reservation bounds are conservative UTF-8 byte estimates including one correction; "
            "safe rejected transport retries add no generation cost.",
            "Physical call costs belong to their original document/version and are not copied.",
            "Historical mean per root includes observed corrections, not a guaranteed cost bound. "
            "Reservation totals are not simultaneous spend: each request releases its reservation "
            "when the actual cost is known. Per-call/provider/global hard gates always apply.",
        ],
    }


def paid_blockers(report: dict[str, Any]) -> list[str]:
    """Admission uses empirical estimate incl. corrections; hard reservations remain per-call."""
    blockers = list(report["blockers"])
    estimated_total = Decimal(0)
    for provider, budget in report["budgets"].items():
        if budget["unknown_attempts"]:
            blockers.append(f"Неизвестный расход: {provider}")
        if not budget["new_pairs"]:
            continue
        estimate = budget["estimated_new_cost_with_observed_corrections_rub"]
        if estimate is None or budget["remaining_rub"] is None:
            blockers.append(f"Нет оценки/лимита: {provider}")
            continue
        estimated_total += Decimal(estimate)
        if max(Decimal(estimate), Decimal(budget["max_initial_reservation_rub"])) > Decimal(
            budget["remaining_rub"]
        ):
            blockers.append(f"Недостаточно бюджета по оценке/резерву: {provider}")
    if Decimal(report["total_used_or_reserved_rub"]) + estimated_total > Decimal(
        report["total_limit_rub"]
    ):
        blockers.append("Недостаточно общего бюджета по оценке")
    return blockers


def transfer(session: Session, report: dict[str, Any]) -> dict[str, int]:
    """Apply a saved preflight atomically; no provider construction or paid entry point."""
    target = session.get(CorpusVersion, UUID(report["target_version"]), with_for_update=True)
    source = session.get(CorpusVersion, UUID(report["source_version"]))
    if (
        target is None
        or source is None
        or target.fingerprint != report["target_fingerprint"]
        or source.fingerprint != report["source_fingerprint"]
    ):
        raise ProcessingError("Версии preflight изменились")
    if report["blockers"]:
        raise ProcessingError("Preflight содержит блокеры; перенос отменён")
    protocol = target.report.get("extraction_protocols", {}).get(report["extraction_protocol_hash"])
    if protocol is None or protocol.get("models") != report["models"]:
        raise ProcessingError("До переноса зафиксируйте протокол целевой версии")
    models = {str(m.id): m for m in session.scalars(select(ModelConfig).where(ModelConfig.active))}
    expected_documents = set(
        session.scalars(select(Document.id).where(Document.corpus_version_id == target.id))
    )
    expected_pairs = {
        (str(document_id), model_id) for document_id in expected_documents for model_id in models
    }
    actual_pairs = {(p["document_id"], p["model_id"]) for p in report["pairs"]}
    if expected_pairs != actual_pairs or len(actual_pairs) != len(report["pairs"]):
        raise ProcessingError("Состав документов/моделей preflight изменился")
    prompt = session.scalar(
        select(Prompt)
        .where(Prompt.name == "extract")
        .order_by(Prompt.created_at.desc(), Prompt.version.desc())
        .limit(1)
    )
    result_counts: Counter[str] = Counter()
    for pair in report["pairs"]:
        model = models.get(pair["model_id"])
        if model is None or prompt is None or str(prompt.id) != pair["prompt_id"]:
            raise ProcessingError("Модель/инструкция preflight изменились")
        snapshot = configuration(model, prompt)
        if fingerprint(snapshot) != pair["snapshot_hash"]:
            raise ProcessingError("Конфигурация preflight изменилась")
        document = session.get(Document, UUID(pair["document_id"]), with_for_update=True)
        if document is None or document.corpus_version_id != target.id:
            raise ProcessingError("Документ preflight изменился")
        if pair["source_document_id"]:
            original = session.get(Document, UUID(pair["source_document_id"]))
            old_text = session.get(DocumentText, original.id) if original else None
            new_text = session.get(DocumentText, document.id)
            if (
                original is None
                or original.corpus_version_id != source.id
                or original.corpus_doc_id != document.corpus_doc_id
                or original.client_id != document.client_id
                or original.sha256 != document.sha256
                or original.is_anonymized != document.is_anonymized
                or old_text is None
                or new_text is None
                or old_text.text != new_text.text
            ):
                raise ProcessingError("Соответствие документов preflight изменилось")
        messages, parts, _, reverse = prepared_request(session, document, snapshot)
        if request_hash(snapshot, messages, document.client_id) != pair["input_hash"]:
            raise ProcessingError("Фактический ключ preflight изменился")
        if session.scalar(
            select(Job.id)
            .where(
                Job.document_id == document.id,
                Job.type == "extract",
                Job.model_config_id == model.id,
                Job.status.in_(["queued", "running"]),
            )
            .limit(1)
        ):
            raise ProcessingError("Есть активное задание; перенос отменён")
        current = session.scalar(
            select(Extraction).where(
                Extraction.document_id == document.id,
                Extraction.model_config_id == model.id,
                Extraction.is_current,
            )
        )
        if current:
            if current.input_hash != pair["input_hash"] or current.snapshot != snapshot:
                raise ProcessingError("Целевое извлечение preflight изменилось")
            if pair["action"] == "preserve_error" and (
                current.valid or not current.preserved_error
            ):
                raise ProcessingError("Сохранённая ошибка preflight изменена")
            result_counts["existing"] += 1
            continue
        if pair["action"] == "generate":
            result_counts["pending_generation"] += 1
            continue
        from app.services.jobs import frozen_protocol

        frozen_protocol(session, document, [snapshot])
        if pair["action"] not in {"reuse_success", "preserve_error"}:
            raise ProcessingError("Неизвестное действие preflight")
        call = session.get(LLMCall, UUID(pair["call_id"]))
        previous = (
            session.get(Extraction, UUID(pair["source_extraction_id"]))
            if pair.get("source_extraction_id")
            else None
        )
        if (
            call is None
            or call.root_input_hash != pair["input_hash"]
            or call.model_config_id != model.id
            or call.prompt_id != prompt.id
        ):
            raise ProcessingError("Исходный вызов preflight изменился")
        started = time.monotonic()
        at = datetime.now(UTC)
        fields, checks, error = None, [], None
        preserved = pair["action"] == "preserve_error"
        if preserved:
            if (
                previous is None
                or previous.valid
                or previous.llm_call_id != call.id
                or call.status != "invalid"
            ):
                raise ProcessingError("Исходная ошибка preflight изменилась")
            error = previous.error
        else:
            if call.status != "success":
                raise ProcessingError("Исходный успешный вызов preflight изменился")
            fields = ExtractedFields.model_validate_json(response_text(call.response_raw))
            checks = check_evidence(fields, parts)
            for check in checks:
                if check.get("matched"):
                    check["original_quote"] = restore(check["matched"], reverse)
            fields = ExtractedFields.model_validate(restore(fields.model_dump(), reverse))
        if document.card_model_config_id is None:
            document.card_model_config_id = UUID(report["card_model_id"])
        result = save_result(
            session, document.id, model.id, snapshot, fields, call.id, checks, not preserved, error
        )
        result.input_hash = pair["input_hash"]
        result.source_extraction_id = previous.id if previous else None
        result.preserved_error = preserved
        session.add(
            StageTiming(
                document_id=document.id,
                stage="extract_transfer",
                started_at=at,
                duration_ms=int((time.monotonic() - started) * 1000),
                ok=not preserved,
                error=error,
            )
        )
        result_counts[pair["action"]] += 1
    parent_ids = target.report.get("extraction_source_versions", [])
    if parent_ids and parent_ids != [str(source.id)]:
        raise ProcessingError("У версии уже другое происхождение")
    target.report = {
        **target.report,
        "extraction_source_versions": [str(source.id)],
        "extraction_transfer": {
            "preflight_hash": fingerprint(report),
            "source_fingerprint": source.fingerprint,
            "extraction_protocol_hash": report["extraction_protocol_hash"],
            "counts": dict(result_counts),
        },
    }
    protocols = {key: dict(value) for key, value in target.report["extraction_protocols"].items()}
    protocols[report["extraction_protocol_hash"]]["source_versions"] = [str(source.id)]
    target.report = {**target.report, "extraction_protocols": protocols}
    return dict(result_counts)


def card_snapshot(session: Session, target_id: UUID) -> dict[str, Any]:
    """Separate linking input manifest. Thresholds remain unset until tune acceptance."""
    from app.services.embeddings import EncodingConfig
    from app.services.linking.types import METHOD_VERSION, Weights

    version = session.get(CorpusVersion, target_id)
    if version is None:
        raise ProcessingError("Версия корпуса не найдена")
    history = test_history(session, version)
    cards = []
    for doc in session.scalars(
        select(Document).where(Document.corpus_version_id == target_id).order_by(Document.id)
    ):
        cards.append(
            {
                "document_id": str(doc.id),
                "client_id": str(doc.client_id),
                "kind": doc.doc_kind,
                "date": str(doc.date) if doc.date else None,
                "number": doc.number,
                "card_model_id": str(doc.card_model_config_id)
                if doc.card_model_config_id
                else None,
                "card_extraction_id": str(doc.card_extraction_id)
                if doc.card_extraction_id
                else None,
            }
        )
    return {
        "corpus_version": str(target_id),
        "corpus_fingerprint": version.fingerprint,
        "source_versions": version.report.get("extraction_source_versions", []),
        "test_history": history,
        "reused_test": bool(history),
        "cards_hash": fingerprint(cards),
        "cards": copy.deepcopy(cards),
        "embedder": EncodingConfig().model_dump(mode="json"),
        "method_version": METHOD_VERSION,
        "weights": Weights().model_dump(),
        "amount_penalty": 0.20,
        "invalid_inn_penalty": 0.05,
        "tau_a": None,
        "tau_r": None,
        "status": "pending_tune_not_frozen",
    }
