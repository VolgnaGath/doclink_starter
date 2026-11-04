# Приложение Ж — извлечение и подтверждение

Раскладка фрагментов предварительная: внешний макет приложений не предоставлен.

## `prepared_request`

Обезличивание и явное усечение перед внешней моделью.

Источник: `app/services/extraction.py:68–74`; версия кода `0297527331b4634df037610030c8bbc7f1e0688d`.
SHA-256 полного файла: `452419bbe83cb020b94eb81ba298196a692bfc149a75a7fe29985c0913ba6f87`.

```python
def prepared_request(
    session: Session, document: Document, snapshot: dict[str, Any]
) -> tuple[list[dict[str, str]], list[str], bool, list[tuple[str, str]]]:
    prepared, context, reverse = prepare(session, document)
    sent, parts, truncated = truncate(prepared)
    messages = [{"role": "user", "content": snapshot["prompt"].format(client=context, text=sent)}]
    return messages, parts, truncated, reverse
```

## `save_result`

История извлечений, валидация и рабочая карточка.

Источник: `app/services/extraction.py:109–180`; версия кода `0297527331b4634df037610030c8bbc7f1e0688d`.
SHA-256 полного файла: `452419bbe83cb020b94eb81ba298196a692bfc149a75a7fe29985c0913ba6f87`.

```python
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
```

## `open_review`

Происхождение и начало человеческой сессии; окончательные связи требуют действия.

Источник: `app/services/review.py:137–149`; версия кода `0297527331b4634df037610030c8bbc7f1e0688d`.
SHA-256 полного файла: `53afe9a99af52919fa19e43c792b2d8f0fb4dfc0818ff1ca1250f2203a44e4e9`.

```python
def open_review(session: Session, document_id: UUID) -> ReviewSession:
    doc = lock_document(session, document_id)
    state_for(session, doc.id)
    context = context_for(session, doc)
    review = ReviewSession(
        document_id=doc.id,
        result_id=UUID(context["result_id"]) if context["result_id"] else None,
        context=context,
        opened_at=datetime.now(UTC),
    )
    session.add(review)
    session.flush()
    return review
```
