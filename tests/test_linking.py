import ast
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.evaluation import EvaluationDocument, evaluate
from app.services.linking import LinkingConfig, LinkingInput, link
from app.services.linking.features import score_pair
from app.services.linking.normalization import name_score, number_score, valid_inn
from app.services.linking.types import (
    Card,
    ContractReference,
    Document,
    Fields,
    LinkingResult,
    Similarity,
)

FIXTURES = json.loads((Path(__file__).parent / "fixtures/linking/decisions.json").read_text())


def document(id: str, **changes: object) -> Document:
    card = Card(
        document_id=id,
        client_id="client",
        corpus_version_id="version",
        kind="договор",
        date=date(2025, 1, 1),
    )
    return Document(
        card=card.model_copy(update=changes), fields=Fields(number="A", number_verified=True)
    )


def scenario(case: dict) -> tuple[LinkingInput, LinkingConfig]:
    source = document(
        "source",
        kind=case.get("source_kind", "акт"),
        date=date.fromisoformat(case["source_date"])
        if case.get("source_date")
        else (None if "source_date" in case else date(2025, 3, 1)),
    )
    source = source.model_copy(
        update={
            "fields": Fields(
                references=tuple(
                    ContractReference.model_validate(ref) for ref in case.get("refs", [])
                ),
                error=case.get("source_error"),
            )
        }
    )
    targets = []
    for target in case["targets"]:
        doc = document(
            target["id"],
            client_id=target.get("client", "client"),
            corpus_version_id=target.get("version", "version"),
            kind=target.get("kind", "договор"),
            date=date.fromisoformat(target["date"])
            if target.get("date")
            else (None if "date" in target else date(2025, 1, 1)),
        )
        targets.append(
            doc.model_copy(
                update={"fields": Fields(number=target.get("number", "A"), number_verified=True)}
            )
        )
    return LinkingInput(
        source=source,
        candidates=tuple(targets),
        similarities=tuple(
            Similarity(target_id=target["id"], cosine=target.get("cosine", 1))
            for target in case["targets"]
        ),
    ), LinkingConfig(tau_a=case.get("tau_a", 0.6), tau_r=case.get("tau_r", 0.3))


@pytest.mark.parametrize("case", FIXTURES, ids=lambda case: case["id"])
def test_decision_fixtures(case: dict) -> None:
    data, config = scenario(case)
    result = link(data, config)
    assert result.outcome == case["outcome"]
    if "reason" in case:
        assert case["reason"] in result.reasons
    if "recommended" in case:
        assert set(result.recommendations) == set(case["recommended"])
    if result.outcome != "recommendation":
        assert not result.recommendations
    if "num" in case:
        assert result.candidates[0].features.num == case["num"]
    if "date_feature" in case:
        assert result.candidates[0].features.date == case["date_feature"]
    assert [score.rank for score in result.candidates] == list(range(1, len(result.candidates) + 1))


@pytest.mark.parametrize("method", ["rules", "semantic", "combined"])
@pytest.mark.parametrize(
    "case_id",
    [
        "F1-future",
        "F3-unknown-date",
        "different-client",
        "different-version",
        "not-contract",
        "target-date-unknown",
    ],
)
def test_shared_constraints(method: str, case_id: str) -> None:
    case = next(case for case in FIXTURES if case["id"] == case_id)
    data, config = scenario(case)
    result = link(data, config.model_copy(update={"method": method, "checks": False}))
    assert result.outcome == case["outcome"]
    assert not result.recommendations


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("12-0К/25", "12-ОК/25", 0.9),
        ("A", "A", 1),
        (" № ab–12 ", "АВ-12", 0.9),
        ("123", "123/25", 0.8),
        ("123", "1234", 0),
        ("ABCD1234", "ABCD1235", 0.7),
        (None, "12", 0),
        (" ", " ", 0),
        ("№", "№1", 0),
    ],
)
def test_number_operations(left: str | None, right: str | None, expected: float) -> None:
    assert number_score(left, right) == expected


def test_name_and_inn_operations() -> None:
    assert name_score("ООО «Ёлка»", 'АО "елка"') == 1
    assert name_score(None, "ООО") == 0
    assert name_score("Альфа", "Бета") < 1
    assert valid_inn("7707083893")
    assert valid_inn("500100732259")
    for value in ("7707083894", "500100732258", "0000000000", "１２３４５６７８９０", "123", ""):
        assert not valid_inn(value)


@pytest.mark.parametrize("amount,penalty", [("125", 0), ("125.01", 0.2)])
def test_penalties_boundary_and_missing_features(amount: str, penalty: float) -> None:
    source = document("s", kind="акт").model_copy(update={"fields": Fields(amount=Decimal(amount))})
    target = document("t").model_copy(update={"fields": Fields(amount=Decimal(100))})
    config = LinkingConfig(tau_a=0.6, tau_r=0.3)
    pair = score_pair(source, target, 0, config)
    assert pair.score == -penalty
    assert sum(p.value for p in pair.penalties) == penalty
    assert pair.features.model_dump(exclude={"reference_index"}) == dict.fromkeys(
        ["num", "date", "inn", "name", "sem"], 0
    )
    bad = source.model_copy(update={"fields": Fields(inn="bad", amount=Decimal(amount))})
    pair = score_pair(bad, target, 0, config)
    assert "source_invalid_inn" in [p.reason for p in pair.penalties]
    assert pair.score == pytest.approx(-penalty - 0.05)
    assert not score_pair(bad, target, 0, config.model_copy(update={"checks": False})).penalties


def test_baselines_and_no_checks_are_deliberately_simple() -> None:
    data, config = scenario(next(case for case in FIXTURES if case["id"] == "F8-number-conflict"))
    assert link(data, config).outcome == "review"
    assert link(data, config.model_copy(update={"method": "semantic"})).outcome == "recommendation"
    # Explicitly configure raw regex baseline; undated references also work.
    source = data.source.model_copy(update={"text": "Акт № 4\nОснование: к договору № A"})
    target = data.candidates[0].model_copy(update={"text": "Договор № A от 01.01.2025"})
    data = data.model_copy(update={"source": source, "candidates": (target,)})
    assert link(data, config.model_copy(update={"method": "rules"})).recommendations == ("a",)
    assert (
        link(data, config.model_copy(update={"checks": False, "tau_a": 0.2, "tau_r": 0.1})).outcome
        == "recommendation"
    )


def test_errors_never_fallback_and_missing_semantics_is_error() -> None:
    data, config = scenario(FIXTURES[0])
    assert link(data.model_copy(update={"similarities": ()}), config).outcome == "error"
    assert (
        link(data.model_copy(update={"semantic_error": "embed failed"}), config).outcome == "error"
    )
    target = data.candidates[0].model_copy(update={"fields": Fields(error="invalid response")})
    assert link(data.model_copy(update={"candidates": (target,)}), config).outcome == "error"
    # no_sem removes only the semantic contribution, without renormalizing weights.
    result = link(
        data.model_copy(update={"similarities": ()}),
        config.model_copy(update={"use_semantic": False, "tau_a": 0.4}),
    )
    assert result.candidates[0].score == 0.45
    assert result.recommendations == ("a",)


def test_unrelated_evidence_does_not_veto_and_input_is_immutable() -> None:
    data, config = scenario(FIXTURES[0])
    before = data.model_dump_json()
    result = link(data, config)
    assert result.recommendations == ("a",)
    assert not result.candidates[0].source_evidence.amount_verified
    assert data.model_dump_json() == before
    assert link(data, config) == result
    target = data.candidates[0].model_copy(update={"fields": Fields(number="A")})
    unverified = link(data.model_copy(update={"candidates": (target,)}), config)
    assert unverified.outcome == "review"
    assert "unverified_target_number" in unverified.reasons


def test_input_allowlist_and_configuration_validation() -> None:
    data, config = scenario(FIXTURES[0])
    for label in ("gold", "case", "expected", "deal_id", "split", "hard_types", "path"):
        payload = data.model_dump()
        payload["source"][label] = "forbidden"
        with pytest.raises(ValidationError):
            LinkingInput.model_validate(payload)
    for changes in ({"tau_r": 0.7}, {"tau_a": float("nan")}, {"weights": {"num": 1}}):
        with pytest.raises(ValidationError):
            LinkingConfig.model_validate(config.model_dump() | changes)
    with pytest.raises(ValidationError):
        LinkingInput.model_validate(data.model_dump() | {"candidates": [data.candidates[0]] * 2})
    for path in (Path(__file__).parents[1] / "app/services/linking").glob("*.py"):
        tree = ast.parse(path.read_text())
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports += [
            name.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for name in node.names
        ]
        assert not any(
            any(word in name for word in ("reference", "evaluation", "sqlalchemy", "app.models"))
            for name in imports
        )


def test_F13_F14_errors_and_empty_recommendations() -> None:
    config = LinkingConfig(tau_a=0.6, tau_r=0.3)
    result = LinkingResult(outcome="error", reasons=("timeout",), config=config)
    metrics = evaluate((EvaluationDocument(result, frozenset({"a"})),))
    assert (metrics.n, metrics.m, metrics.processing_errors) == (1, 0, 1)
    assert metrics.precision == metrics.recall == metrics.coverage == 0
    assert metrics.error_share is None
    assert metrics.document_accuracy == 0
    assert evaluate(()).n == 0


def test_F15_link_precision_and_document_coverage() -> None:
    config = LinkingConfig(tau_a=0.6, tau_r=0.3)
    rows = []
    for index in range(100):
        recommended = ("a", "b") if index < 10 else (("a",) if index < 60 else ())
        gold = frozenset(recommended) if index < 53 else frozenset()
        result = LinkingResult(
            outcome="recommendation" if recommended else "none",
            reasons=("fixture",),
            recommendations=recommended,
            config=config,
        )
        rows.append(EvaluationDocument(result, gold))
    metrics = evaluate(tuple(rows))
    assert (metrics.n, metrics.m, metrics.correct_links) == (100, 70, 63)
    assert metrics.coverage == 0.6
    assert metrics.precision == 0.9
    assert metrics.error_share == pytest.approx(0.1)


def test_alternatives_are_not_recommendations_and_recall_at_3() -> None:
    data, config = scenario(
        next(case for case in FIXTURES if case["id"] == "fourth-hidden-alternative")
    )
    result = link(data, config)
    assert len(result.candidates) == 4
    metrics = evaluate((EvaluationDocument(result, frozenset({"a", "d"})),))
    assert metrics.m == metrics.coverage == metrics.precision == 0
    assert metrics.review_share == 1
    assert metrics.recall_at_3 == 0.5
