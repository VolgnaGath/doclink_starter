from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
import test_models_extraction
from sqlalchemy import select

from app.config import Settings
from app.db import get_session
from app.models import LLMCall, ModelConfig
from app.services.model_calls import ProcessingError, reserve, tariff_cost
from app.services.providers import Completion

live = test_models_extraction.live
setup = test_models_extraction.setup


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
