import json
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
import pytest
import test_models_extraction
from sqlalchemy import select

from app.config import Settings
from app.db import get_session
from app.models import LLMCall, ModelConfig
from app.services.extraction_schema import ExtractedFields, check_evidence
from app.services.model_calls import ProcessingError, reserve, tariff_cost
from app.services.providers import ChatProvider, Completion

live = test_models_extraction.live
setup = test_models_extraction.setup


def test_absent_evidence_for_unknown_field_is_not_a_fabricated_quote() -> None:
    unknown = ExtractedFields(evidence={"date": None, "amount": None})
    assert check_evidence(unknown, ["Технический текст"]) == []
    populated = ExtractedFields(number="1", evidence={"number": None})
    checks = check_evidence(populated, ["Документ № 1"])
    assert len(checks) == 1 and not checks[0]["ok"]


def test_gigachat_native_cache_is_free_and_schema_is_sent(settings: Settings) -> None:
    settings.llm_provider, settings.gigachat_credentials = "live", "test-auth"

    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("oauth"):
            return httpx.Response(
                200,
                json={
                    "access_token": "test-access",
                    "expires_at": int((time.time() + 1800) * 1000),
                },
            )
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["schema"]["required"] == ["number"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "precached_prompt_tokens": 900,
                    "total_tokens": 150,
                },
            },
        )

    provider = ChatProvider(
        settings,
        "gigachat",
        "GigaChat-2",
        {"json_mode": True},
        httpx.Client(transport=httpx.MockTransport(transport)),
    )
    reply = provider.complete([], {"properties": {"number": {"type": ["string", "null"]}}})
    assert reply.input_tokens == 1000 and reply.cached_input_tokens == 900
    assert tariff_cost(
        {"price_in_1k": "0.065", "price_out_1k": "0.065", "price_cached_in_1k": "0"}, reply
    ) == Decimal("0.00975")


def test_tariff_equivalent_uses_measured_cache_tokens() -> None:
    snapshot = {"price_in_1k": "0.2", "price_out_1k": "0.3", "price_cached_in_1k": "0.05"}
    reply = Completion("{}", "{}", 1000, 200, 800)
    assert tariff_cost(snapshot, reply) == Decimal("0.14")
    assert tariff_cost(snapshot, Completion("{}", "{}", None, 200)) is None
    assert tariff_cost(snapshot, Completion("{}", "{}", 100, 200, 101)) is None
    snapshot["price_cached_in_1k"] = "0"
    assert tariff_cost(snapshot, reply) == Decimal("0.10")


def test_concurrent_provider_cap_cannot_borrow_other_providers_budget(live: Settings) -> None:
    doc, model, prompt, snapshot = setup(live, provider="yandex")
    live.budget_rub = Decimal("100")
    live.yandex_budget_rub, live.gigachat_budget_rub = Decimal("0.9"), Decimal("50")

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
                return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, [0, 1])) == [False, True]
    with get_session(live.database_url) as session:
        assert len(list(session.scalars(select(LLMCall)))) == 1


def test_provider_with_no_allowance_is_blocked(live: Settings) -> None:
    doc, model, prompt, snapshot = setup(live, provider="yandex")
    live.yandex_budget_rub, live.gigachat_budget_rub = Decimal(0), Decimal(50)
    with get_session(live.database_url) as session:
        call = LLMCall(
            model_config_id=model,
            prompt_id=prompt,
            document_id=doc,
            input_hash="x",
            root_input_hash="x",
            snapshot=snapshot,
            paid=True,
            truncated=False,
            status="started",
        )
        with pytest.raises(ProcessingError, match="бюджет провайдера"):
            reserve(session, call, live, [])
        assert session.get(ModelConfig, model).provider == "yandex"
