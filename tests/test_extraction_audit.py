from decimal import Decimal

from app.extraction_audit import model_costs
from app.models import LLMCall


def test_cost_breakdown_uses_each_physical_historical_tariff_and_counts_unknowns() -> None:
    def call(rate: str, cached: int, cost: str | None, **changes: object) -> LLMCall:
        return LLMCall(
            snapshot={
                "provider": "yandex",
                "name": "Qwen",
                "price_in_1k": rate,
                "price_cached_in_1k": "0.05",
                "price_out_1k": "0.3",
                "usage_details": {"cached_input_tokens": cached},
            },
            input_tokens=1000,
            output_tokens=100,
            cost_rub=Decimal(cost) if cost else None,
            **changes,
        )

    result = model_costs(
        [
            call("0.2", 200, "0.20"),
            call("0.4", 0, "0.43"),
            LLMCall(snapshot={"provider": "yandex", "name": "Qwen"}, cost_rub=None),
            LLMCall(snapshot={"provider": "yandex", "name": "Qwen"}, cost_rub=Decimal(0)),
        ]
    )[0]
    assert result["physical_attempts"] == 4
    assert result["unknown_cost_attempts"] == result["incomplete_breakdown_attempts"] == 1
    assert Decimal(result["regular_input_cost_rub"]) == Decimal("0.56")
    assert Decimal(result["cached_input_cost_rub"]) == Decimal("0.01")
    assert Decimal(result["output_cost_rub"]) == Decimal("0.06")
    assert Decimal(result["known_cost_rub"]) == Decimal("0.63")
