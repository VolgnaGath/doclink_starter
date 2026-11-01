"""Infrastructure fixtures are never experiment results. All transports are offline."""

import json
import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select, text

from app.config import Settings
from app.db import get_engine, get_session
from app.main import create_app
from app.models import (
    AnonIdentity,
    Client,
    Document,
    DocumentText,
    Extraction,
    Job,
    LLMCall,
    ModelConfig,
    Prompt,
)
from app.seed import seed
from app.services.anonymization import prepare, restore
from app.services.corpus_import import import_corpus
from app.services.extraction import configuration, extract_document, lock_number
from app.services.extraction_schema import ExtractedFields, match_quote, regex_extract, truncate
from app.services.jobs import enqueue, recover, work_once
from app.services.model_calls import ProcessingError, reserve
from app.services.providers import ChatProvider, Completion, ProviderError

SAMPLE = "Счёт № А-1 от 01.01.2026\nК договору № Д-1 от 01.12.2025.\nИтого 1200 рублей."


def response(content: str, usage: bool = True) -> dict[str, Any]:
    payload = {"choices": [{"message": {"content": content}}]}
    if usage:
        payload["usage"] = {"prompt_tokens": 100, "completion_tokens": 20}
    return payload


class Scripted:
    def __init__(self, *replies: str | ProviderError, usage: bool = True) -> None:
        self.replies, self.usage, self.count = replies, usage, 0
        self.messages: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> Completion:
        item = self.replies[self.count]
        self.count += 1
        self.messages.append(messages)
        if isinstance(item, ProviderError):
            raise item
        return Completion(
            item,
            json.dumps(response(item, self.usage)),
            100 if self.usage else None,
            20 if self.usage else None,
        )


@pytest.fixture
def live(settings: Settings, tmp_path: Path) -> Iterator[Settings]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Изолированная PostgreSQL: make verify-test")
    settings.database_url, settings.files_dir = url, str(tmp_path)
    settings.llm_provider, settings.paid_calls_enabled, settings.budget_rub = (
        "live",
        True,
        Decimal(100),
    )

    def clean() -> None:
        with get_engine(url).begin() as conn:
            conn.execute(text("TRUNCATE clients, corpus_versions, model_configs, prompts CASCADE"))

    clean()
    yield settings
    clean()


def setup(
    settings: Settings, *, provider: str = "regex", anonymized: bool = True, content: str = SAMPLE
) -> tuple[UUID, UUID, UUID, dict[str, Any]]:
    with get_session(settings.database_url) as session:
        seed(session)
        model = session.scalar(select(ModelConfig).where(ModelConfig.provider == provider).limit(1))
        model.price_in_1k = model.price_out_1k = Decimal("0.1")
        client = Client(name="ООО «Реальный клиент»", inn="7707083893")
        session.add(client)
        session.flush()
        doc = Document(
            client_id=client.id,
            source="upload",
            filename="SECRET_GOLD_PATH.txt",
            sha256="abc",
            storage_path="unused",
            mime="text/plain",
            status="text",
            is_anonymized=anonymized,
            card_model_config_id=model.id,
            gold={"deal_id": "GOLD_SECRET", "number": "GOLD_NUMBER"},
            deal_id="SECRET_DEAL",
        )
        session.add(doc)
        session.flush()
        session.add(
            DocumentText(document_id=doc.id, text=content, pages=1, chars=len(content), ocr_pages=0)
        )
        prompt = session.scalar(select(Prompt))
        snapshot = configuration(model, prompt)
        session.commit()
        return doc.id, model.id, prompt.id, snapshot


def test_schema_dates_decimals_unknowns_and_regex() -> None:
    assert ExtractedFields.model_validate_json('{"amount": "0.10"}').amount == Decimal("0.10")
    for value in ('{"date":"31.02.2026"}', '{"amount": "NaN"}', '{"unknown": 1}'):
        with pytest.raises(ValidationError):
            ExtractedFields.model_validate_json(value)
    fields = regex_extract(SAMPLE)
    assert (fields.doc_kind, fields.number, fields.date) == ("счёт", "А-1", "01.01.2026")
    assert fields.contract_refs[0].number == "Д-1"
    assert fields.counterparty_inn is None
    assert regex_extract("Неизвестный текст").doc_kind is None


def test_quote_matching_truncation_and_boundaries() -> None:
    assert match_quote("Точная цитата", ["До. Точная цитата. После."])["method"] == "exact"
    assert match_quote("точная цитата", ["точная\n   цитата"])["method"] == "whitespace"
    quote = "Достаточно длинная точная цитата для проверки распознавания"
    assert match_quote(quote, [quote[:-1] + "е"])["method"] == "fuzzy"
    assert not match_quote("другая выдуманная цитата", [SAMPLE])["ok"]
    sent, parts, cut = truncate("a" * 6000 + "middle" * 1000 + "b" * 2500)
    assert cut and len(parts[0]) == 6000 and len(parts[1]) == 2500
    assert not match_quote("aaabbb", parts)["ok"]
    assert "ПРОПУЩЕН" in sent


@pytest.mark.parametrize("provider", ["yandex", "ollama"])
def test_chat_transport_and_unknown_usage(settings: Settings, provider: str) -> None:
    settings.llm_provider = "live"
    settings.yandex_api_key, settings.yandex_folder_id = "secret-key", "folder"
    requests = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=response("{}", usage=False))

    client = httpx.Client(transport=httpx.MockTransport(transport))
    adapter = ChatProvider(settings, provider, "model/latest", {"max_tokens": 50}, client)
    result = adapter.complete([{"role": "user", "content": "sample"}], {})
    assert result.input_tokens is None and result.output_tokens is None
    assert json.loads(requests[0].content)["max_tokens"] == 50
    if provider == "yandex":
        assert json.loads(requests[0].content)["model"] == "gpt://folder/model/latest"
        assert requests[0].headers["x-folder-id"] == "folder"


def test_gigachat_oauth_refresh(settings: Settings) -> None:
    settings.llm_provider, settings.gigachat_credentials = "live", "secret-auth"
    oauth, generations = 0, 0

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal oauth, generations
        if "oauth" in request.url.path:
            oauth += 1
            assert request.headers["Authorization"] == "Basic secret-auth"
            return httpx.Response(
                200,
                json={
                    "access_token": f"token-{oauth}",
                    "expires_at": int((time.time() + 1800) * 1000),
                },
            )
        generations += 1
        if generations == 2:
            return httpx.Response(401)
        return httpx.Response(200, json=response("{}"))

    adapter = ChatProvider(
        settings,
        "gigachat",
        "GigaChat-2",
        {},
        httpx.Client(transport=httpx.MockTransport(transport)),
    )
    adapter.complete([], {})
    with pytest.raises(ProviderError, match="401"):
        adapter.complete([], {})
    adapter.complete([], {})
    assert oauth == 2 and generations == 3


@pytest.mark.parametrize(
    "failure,uncertain,retryable",
    [
        (httpx.ReadTimeout("timeout"), True, False),
        (httpx.ConnectTimeout("timeout"), False, True),
        (429, False, True),
        (500, True, False),
        (403, False, False),
    ],
)
def test_transport_failure_classification(
    settings: Settings, failure: object, uncertain: bool, retryable: bool
) -> None:
    settings.llm_provider = "live"

    def transport(request: httpx.Request) -> httpx.Response:
        if isinstance(failure, Exception):
            raise failure
        return httpx.Response(failure)

    adapter = ChatProvider(
        settings, "ollama", "local", {}, httpx.Client(transport=httpx.MockTransport(transport))
    )
    with pytest.raises(ProviderError) as error:
        adapter.complete([], {})
    assert (error.value.uncertain, error.value.retryable) == (uncertain, retryable)


def test_seed_idempotence_and_anonymization(live: Settings) -> None:
    content = (
        SAMPLE + "\nООО «Секретная фирма» ИНН 7707083893 КПП 123456789\n"
        "Иванов Иван Иванович; +7 (999) 123-45-67; secret@example.org\n"
        "Адрес: г. Москва, ул. Ленина, д. 1\n"
        "р/с 40702810100000000001 ОГРН 1234567890123\n"
        "СНИЛС 123-456-789 01\nCN=Секрет\n"
        "Договор № 123456789012345 от 01.01.2026\nИтого 1234567890 рублей."
    )
    doc_id, model_id, _, _ = setup(live, anonymized=False, content=content)
    with get_session(live.database_url) as session:
        model = session.get(ModelConfig, model_id)
        model.params = {"temperature": 0.5}
        seed(session)
        assert session.scalar(select(func.count()).select_from(ModelConfig)) == 5
        assert model.params == {"temperature": 0.5}
        doc = session.get(Document, doc_id)
        sent, context, reverse = prepare(session, doc)
        session.commit()
        for secret in [
            "Секретная фирма",
            "Реальный клиент",
            "7707083893",
            "123456789",
            "Иванов",
            "+7 (999) 123-45-67",
            "secret@example.org",
            "Ленина",
            "40702810100000000001",
        ]:
            assert (
                secret
                not in sent.replace("123456789012345", "").replace("1234567890 рублей", "")
                + context
            )
        assert "№ 123456789012345" in sent and "1234567890 рублей" in sent
        assert restore(sent, reverse)  # reverse dictionary is local only
        again = prepare(session, doc)
        assert again[0] == sent and again[1] == context


def test_client_dictionary_concurrency_and_isolation(live: Settings) -> None:
    doc_id, _, _, _ = setup(live, anonymized=False, content="ООО «Секрет» ИНН 7707083893")

    def anonymize() -> tuple[str, str, list[tuple[str, str]]]:
        with get_session(live.database_url) as session:
            result = prepare(session, session.get(Document, doc_id))
            session.commit()
            return result

    with ThreadPoolExecutor(2) as pool:
        a, b = list(pool.map(lambda _: anonymize(), range(2)))
    assert a[:2] == b[:2]
    other, _, _, _ = setup(live, anonymized=False, content="ООО «Секрет» ИНН 7707083893")
    with get_session(live.database_url) as session:
        prepare(session, session.get(Document, other))
        session.commit()
        identities = session.scalars(select(AnonIdentity).where(AnonIdentity.kind == "inn")).all()
        assert len(identities) == 2 and identities[0].client_id != identities[1].client_id
        assert identities[0].replacement != identities[1].replacement


def test_pipeline_correction_cache_history_and_gold_isolation(live: Settings) -> None:
    doc_id, model, prompt, snapshot = setup(live, provider="yandex")
    reply = (
        '{"doc_kind":"счёт","number":"А-1","date":"01.01.2026",'
        '"evidence":{"number":"выдуманная цитата"}}'
    )
    provider = Scripted("not json", reply)
    first = extract_document(live, doc_id, model, prompt, snapshot, provider=provider)
    second = extract_document(live, doc_id, model, prompt, snapshot, provider=provider)
    assert provider.count == 2
    assert all(
        value not in json.dumps(provider.messages)
        for value in ["GOLD_SECRET", "GOLD_NUMBER", "SECRET_GOLD_PATH", "SECRET_DEAL"]
    )
    with get_session(live.database_url) as session:
        assert not session.get(Extraction, first).is_current
        result = session.get(Extraction, second)
        assert result.cache_hit and result.valid and not result.evidence_ok
        assert session.get(Document, doc_id).card_extraction_id == second
        calls = session.scalars(select(LLMCall).order_by(LLMCall.started_at)).all()
        assert [c.status for c in calls] == ["invalid", "success"]
        assert calls[0].input_hash != calls[1].input_hash
        assert calls[0].root_input_hash == calls[1].root_input_hash
        assert sum(c.cost_rub for c in calls) == Decimal("0.024")


def test_unknown_usage_blocks_future_paid_calls(live: Settings) -> None:
    doc, model, prompt, snapshot = setup(live, provider="yandex")
    provider = Scripted("{}", usage=False)
    extract_document(live, doc, model, prompt, snapshot, provider=provider)
    with get_session(live.database_url) as session:
        call = session.scalar(select(LLMCall))
        assert call.cost_rub is None and call.input_tokens is None
    changed = {**snapshot, "params": {"max_tokens": 20}}
    with pytest.raises(ProcessingError, match="Неизвестный расход"):
        extract_document(live, doc, model, prompt, changed, provider=provider)
    assert provider.count == 1
    # A known successful cache entry can still be reused, without any further spend.
    extract_document(live, doc, model, prompt, snapshot, provider=provider)
    assert provider.count == 1


def test_uncertain_send_and_budget_lock(live: Settings) -> None:
    doc, model, prompt, snapshot = setup(live, provider="yandex")
    provider = Scripted(ProviderError("unknown", uncertain=True), "{}")
    with pytest.raises(ProcessingError, match="unknown"):
        extract_document(live, doc, model, prompt, snapshot, provider=provider)
    with pytest.raises(ProcessingError, match="неизвестен"):
        extract_document(live, doc, model, prompt, snapshot, provider=provider)
    assert provider.count == 1
    with get_session(live.database_url) as session:
        call = session.scalar(select(LLMCall))
        assert call.status == "uncertain" and call.cost_rub is None
        call.reconciled, call.cost_rub = True, Decimal("0.02")
        session.commit()
    live.budget_rub = Decimal("0.02001")
    with pytest.raises(ProcessingError, match="бюджета"):
        extract_document(live, doc, model, prompt, snapshot, provider=provider)
    assert provider.count == 1


def test_retries_are_bounded_and_each_attempt_is_durable(
    live: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, model, prompt, snapshot = setup(live, provider="yandex")
    monkeypatch.setattr("app.services.model_calls.time.sleep", lambda _: None)
    provider = Scripted(*[ProviderError("connect", retryable=True)] * 5)
    with pytest.raises(ProcessingError):
        extract_document(live, doc, model, prompt, snapshot, provider=provider)
    assert provider.count == 4
    with get_session(live.database_url) as session:
        calls = session.scalars(select(LLMCall)).all()
        assert len(calls) == 4 and all(c.status == "error" for c in calls)
        assert all(c.cost_rub == 0 for c in calls)
        result = session.scalar(select(Extraction).where(Extraction.is_current))
        assert not result.valid and result.error


def test_queue_idempotence_worker_and_card_selection(live: Settings) -> None:
    doc, model, _, _ = setup(live)
    with get_session(live.database_url) as session:
        ids = enqueue(session, session.get(Document, doc))
        session.commit()
        assert enqueue(session, session.get(Document, doc), retry=True) == []
        session.commit()
        assert len(ids) == 1
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(lambda _: work_once(live), range(2)))
    assert sum(outcomes) == 1
    with get_session(live.database_url) as session:
        job = session.get(Job, ids[0])
        assert job.status == "done" and job.attempts == 1
        result = session.scalar(select(Extraction))
        assert result.valid and result.llm_call_id is None
        assert session.scalar(select(func.count()).select_from(LLMCall)) == 0
        docrow = session.get(Document, doc)
        assert docrow.number == "А-1" and docrow.card_model_config_id == model
        assert enqueue(session, docrow) == ids


def test_concurrent_cache_has_one_physical_attempt(live: Settings) -> None:
    doc, model, prompt, snapshot = setup(live, provider="yandex")
    provider = Scripted("{}")

    def run() -> UUID:
        return extract_document(live, doc, model, prompt, snapshot, provider=provider)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert len(set(results)) == 2 and provider.count == 1
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(LLMCall)) == 1
        assert (
            session.scalar(
                select(func.count()).select_from(Extraction).where(Extraction.is_current)
            )
            == 1
        )


def test_recovery_never_resends_uncertain_request(live: Settings) -> None:
    doc, model, prompt, snapshot = setup(live, provider="yandex")
    with get_session(live.database_url) as session:
        ids = enqueue(session, session.get(Document, doc), [session.get(ModelConfig, model)])
        job = session.get(Job, ids[0])
        from datetime import UTC, datetime

        job.status, job.locked_at = "running", datetime.now(UTC)
        call = LLMCall(
            model_config_id=model,
            prompt_id=prompt,
            document_id=doc,
            job_id=job.id,
            input_hash="x",
            root_input_hash="x",
            snapshot=snapshot,
            paid=True,
            reserved_rub=Decimal(1),
            truncated=False,
            status="started",
        )
        session.add(call)
        session.commit()
    with (
        get_engine(live.database_url).connect() as guard,
        get_session(live.database_url) as session,
    ):
        recover(session, guard)
        assert session.get(Job, ids[0]).status == "error"
        assert session.get(LLMCall, call.id).status == "uncertain"
        assert not session.scalar(select(Extraction)).valid
    assert not work_once(live)


def test_no_send_before_paid_gate_and_no_anonymization_failure_send(live: Settings) -> None:
    doc, model, prompt, snapshot = setup(live, provider="yandex")
    live.paid_calls_enabled = False
    provider = Scripted("{}")
    with pytest.raises(ProcessingError, match="запрещены"):
        extract_document(live, doc, model, prompt, snapshot, provider=provider)
    assert provider.count == 0
    with get_session(live.database_url) as session:
        session.execute(text("DELETE FROM document_texts"))
        session.commit()
    with pytest.raises(ProcessingError, match="обезличивания"):
        extract_document(live, doc, model, prompt, snapshot, provider=provider)
    assert provider.count == 0


def test_web_models_calls_queue_card_and_authorization(live: Settings) -> None:
    doc, model, _, _ = setup(live)
    with TestClient(create_app(live)) as client:
        for path in ("/models", "/calls"):
            assert client.get(path, follow_redirects=False).status_code == 303
        client.post("/login", data={"username": "admin", "password": "test-password"})
        assert client.get("/models").status_code == 200
        assert (
            client.post(
                f"/models/{model}",
                data={
                    "model_uri": "regex-v1",
                    "params": "{}",
                    "price_in": "0",
                    "price_out": "0",
                    "active": "true",
                    "default_card": "true",
                },
            ).status_code
            == 200
        )
        assert (
            client.post(f"/documents/{doc}/extract", data={"model_id": str(model)}).status_code
            == 200
        )
        assert work_once(live)
        with get_session(live.database_url) as session:
            result = session.scalar(select(Extraction))
        assert (
            client.post(
                f"/documents/{doc}/card", data={"extraction_id": str(result.id)}
            ).status_code
            == 200
        )
        detail = client.get(f"/documents/{doc}")
        assert "Текущий результат" in detail.text and "А-1" in detail.text
        assert client.get("/calls").status_code == 200
        assert client.get(f"/calls/{uuid4()}").status_code == 404
        assert (
            client.post(
                f"/models/{model}", data={"model_uri": "regex", "params": '{"api_key":"secret"}'}
            ).status_code
            == 422
        )


def test_schema_artifact_accepts_same_fields() -> None:
    # Both definitions expose exactly the same wire fields/references, including unknowns.
    artifact = json.loads(
        (Path(__file__).parent.parent / "prompts/extract_v1.schema.json").read_text()
    )
    generated = ExtractedFields.model_json_schema()
    assert set(artifact["properties"]) == set(generated["properties"])
    assert set(artifact["$defs"]["Reference"]["properties"]) == set(
        generated["$defs"]["Reference"]["properties"]
    )


def test_two_invalid_responses_preserve_journal_and_error(live: Settings) -> None:
    doc, model, prompt, snapshot = setup(live, provider="yandex")
    provider = Scripted('{"date":"31.02.2026"}', "still not json")
    with pytest.raises(ProcessingError, match="Невалидное"):
        extract_document(live, doc, model, prompt, snapshot, provider=provider)
    assert provider.count == 2
    with get_session(live.database_url) as session:
        assert [c.status for c in session.scalars(select(LLMCall))] == ["invalid", "invalid"]
        assert not session.scalar(select(Extraction)).valid


def test_budget_reservations_serialize_concurrent_requests(live: Settings) -> None:
    doc, model, prompt, snapshot = setup(live, provider="yandex")
    live.budget_rub = Decimal("0.9")

    def attempt(index: int) -> bool:
        with get_session(live.database_url) as session:
            call = LLMCall(
                model_config_id=model,
                prompt_id=prompt,
                document_id=doc,
                input_hash=str(index),
                root_input_hash=str(index),
                snapshot=snapshot,
                paid=True,
                truncated=False,
                status="started",
            )
            try:
                reserve(session, call, live, [{"role": "user", "content": "text"}])
                return True
            except ProcessingError:
                session.rollback()
                return False

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(attempt, [0, 1]))
    assert sorted(outcomes) == [False, True]
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.sum(LLMCall.reserved_rub))) <= live.budget_rub


def test_call_journal_is_visible_before_provider_send(live: Settings) -> None:
    doc, model, prompt, snapshot = setup(live, provider="yandex")

    class Observed(Scripted):
        def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> Completion:
            with get_session(live.database_url) as session:
                pending = session.scalar(select(LLMCall).where(LLMCall.status == "started"))
                assert pending is not None and pending.reserved_rub > 0
            return super().complete(messages, schema)

    extract_document(live, doc, model, prompt, snapshot, provider=Observed("{}"))


def test_pre_send_recovery_and_live_worker_lock(live: Settings) -> None:
    from datetime import UTC, datetime

    doc, _, _, _ = setup(live)
    with get_session(live.database_url) as session:
        ids = enqueue(session, session.get(Document, doc))
        job = session.get(Job, ids[0])
        job.status, job.locked_at = "running", datetime.now(UTC)
        session.commit()
    key = lock_number("job:" + str(ids[0]))
    with get_engine(live.database_url).connect() as owner:
        owner.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        owner.commit()
        try:
            assert not work_once(live)
        finally:
            owner.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            owner.commit()
    assert work_once(live)
    with get_session(live.database_url) as session:
        assert session.get(Job, ids[0]).status == "done"


def test_text_retry_runs_in_worker_then_queues_extraction(live: Settings, tmp_path: Path) -> None:
    doc, _, _, _ = setup(live)
    original = tmp_path / "retry.txt"
    original.write_text(SAMPLE)
    with get_session(live.database_url) as session:
        row = session.get(Document, doc)
        row.storage_path, row.status = str(original), "error"
        enqueue(session, row, text_only=True)
        session.commit()
    assert work_once(live)  # text
    assert work_once(live)  # regex
    assert not work_once(live)
    with get_session(live.database_url) as session:
        assert session.get(Document, doc).status == "extracted"
        assert {j.status for j in session.scalars(select(Job))} == {"done"}


def test_independent_models_and_explicit_card_source(live: Settings) -> None:
    doc, regex, prompt, regex_config = setup(live)
    with get_session(live.database_url) as session:
        cloud = session.scalar(select(ModelConfig).where(ModelConfig.name == "Qwen"))
        cloud.price_in_1k = cloud.price_out_1k = Decimal("0.1")
        cloud_config = configuration(cloud, session.get(Prompt, prompt))
        session.commit()
    first = extract_document(live, doc, regex, prompt, regex_config)
    second = extract_document(
        live,
        doc,
        cloud.id,
        prompt,
        cloud_config,
        provider=Scripted('{"doc_kind":"другое","number":"B-2"}'),
    )
    with get_session(live.database_url) as session:
        row = session.get(Document, doc)
        assert row.card_extraction_id == first and row.number == "А-1"
        from app.services.extraction import select_card

        select_card(session, session.get(Extraction, second))
        session.commit()
        assert row.doc_kind is None and row.number == "B-2" and row.status == "review"
        assert session.get(Extraction, first).is_current


def test_cli_full_corpus_regex_freeze_and_resume(live: Settings) -> None:
    from app.extract_cli import execute, parser

    corpus = Path(__file__).parent.parent / "corpus"
    with get_session(live.database_url) as session:
        report = import_corpus(session, corpus, live)
        version = report["version_id"]
    base = ["--corpus-version", str(version)]
    frozen = execute(parser().parse_args([*base, "--freeze"]), live)
    args = parser().parse_args(
        [*base, "--protocol-hash", frozen["protocol_hash"], "--drain", "--workers", "3"]
    )
    report = execute(args, live)
    assert report["all"]["documents"] == report["all"]["success"] == 233
    assert report["in_scope"]["documents"] == report["in_scope"]["success"] == 231
    assert report["physical_attempts"] == 0
    assert execute(args, live)["all"] == report["all"]
    with get_session(live.database_url) as session:
        model = session.scalar(select(ModelConfig).where(ModelConfig.provider == "regex"))
        model.params = {"temperature": 0.4}
        session.commit()
    next_frozen = execute(parser().parse_args([*base, "--freeze"]), live)
    assert next_frozen["reused_test"] and next_frozen["protocol_hash"] != frozen["protocol_hash"]
    with pytest.raises(ProcessingError, match="зафиксируйте"):
        execute(args, live)


def test_unexpected_transport_exception_is_uncertain(live: Settings) -> None:
    doc, model, prompt, snapshot = setup(live, provider="yandex")

    class Broken:
        def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> Completion:
            raise RuntimeError("sensitive transport detail")

    with pytest.raises(ProcessingError, match="Неопределённый исход"):
        extract_document(live, doc, model, prompt, snapshot, provider=Broken())
    with get_session(live.database_url) as session:
        call = session.scalar(select(LLMCall))
        assert call.status == "uncertain" and call.cost_rub is None
        assert "sensitive" not in call.error


def test_tariff_snapshot_preserves_history_and_does_not_invalidate_cache(live: Settings) -> None:
    doc, model, prompt, snapshot = setup(live, provider="yandex")
    provider = Scripted("{}")
    extract_document(live, doc, model, prompt, snapshot, provider=provider)
    changed = {**snapshot, "price_in_1k": "0.9", "price_out_1k": "0.9"}
    result_id = extract_document(live, doc, model, prompt, changed, provider=provider)
    assert provider.count == 1
    with get_session(live.database_url) as session:
        result = session.get(Extraction, result_id)
        call = session.get(LLMCall, result.llm_call_id)
        assert result.cache_hit and result.snapshot["price_in_1k"] == "0.9"
        assert Decimal(call.snapshot["price_in_1k"]) == Decimal("0.1")
        assert call.cost_rub == Decimal("0.012")


def test_anonymized_model_response_restores_counterparty_and_quote(live: Settings) -> None:
    import re

    content = SAMPLE + "\nКонтрагент ООО «Секретная фирма», ИНН 7801000000."
    doc, model, prompt, snapshot = setup(live, provider="yandex", anonymized=False, content=content)

    class Reflect:
        def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> Completion:
            sent = messages[0]["content"]
            assert "Секретная фирма" not in sent and "Реальный клиент" not in sent
            assert "7801000000" not in sent and "7707083893" not in sent
            quote = re.search(r"Контрагент ООО «[^»]+», ИНН \d+\.", sent)[0]
            fields = json.dumps(
                {
                    "counterparty_name": re.search(r"ООО «[^»]+»", quote)[0],
                    "counterparty_inn": re.search(r"\d{10}", quote)[0],
                    "evidence": {"counterparty": quote},
                },
                ensure_ascii=False,
            )
            return Completion(fields, json.dumps(response(fields)), 100, 20)

    result_id = extract_document(live, doc, model, prompt, snapshot, provider=Reflect())
    with get_session(live.database_url) as session:
        result = session.get(Extraction, result_id)
        assert result.cp_name == "ООО «Секретная фирма»" and result.cp_inn == "7801000000"
        assert result.evidence_ok
        assert result.evidence_checks[0]["original_quote"] in content
