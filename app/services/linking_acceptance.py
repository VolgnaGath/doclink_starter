"""Stage-4 tune selection after model inference. No gold enters the linking engine."""

from dataclasses import asdict, dataclass
from typing import Any

from app.services.evaluation import EvaluationDocument, evaluate
from app.services.linking.methods import link
from app.services.linking.types import LinkingConfig, LinkingInput, LinkingResult

TUNING_VERSION = "stage4-tune-v1"


@dataclass(frozen=True)
class TuneDocument:
    data: LinkingInput
    gold_targets: frozenset[str]
    split: str = "tune"


def select_thresholds(
    documents: tuple[TuneDocument, ...], template: LinkingConfig
) -> dict[str, Any]:
    """Exhaustive finite breakpoints, not a grid fitted to viewed test results.

    tau_a: maximum document coverage subject to precision >= .98 and M > 0;
    ties: higher precision, then stricter threshold. tau_r: retain the most gold
    links reachable via recommendations/review top-3, then fewer reviews, then
    stricter threshold. This is a documented acceptance policy, not an optimum
    experimental configuration or the full stage-6 tuning system.
    """
    if not documents or any(doc.split != "tune" for doc in documents):
        raise ValueError("Пороги выбираются только на непустой части tune")

    def decisions(config: LinkingConfig) -> tuple[LinkingResult, ...]:
        return tuple(link(doc.data, config) for doc in documents)

    def metrics(results: tuple[LinkingResult, ...]) -> dict[str, Any]:
        return asdict(
            evaluate(
                tuple(
                    EvaluationDocument(result, doc.gold_targets)
                    for doc, result in zip(documents, results, strict=True)
                )
            )
        )

    probe = decisions(template.model_copy(update={"tau_a": 0.0, "tau_r": 0.0}))
    thresholds = sorted(
        {
            0.0,
            1.0,
            *(
                candidate.score
                for result in probe
                for candidate in result.candidates
                if 0 <= candidate.score <= 1
            ),
        }
    )
    curve = []
    feasible = []
    for tau in thresholds:
        config = template.model_copy(update={"tau_a": tau, "tau_r": 0.0})
        measured = metrics(decisions(config))
        row = {"tau_a": tau, **measured}
        curve.append(row)
        if measured["m"] > 0 and measured["correct_links"] * 100 >= 98 * measured["m"]:
            feasible.append(row)
    base = {
        "version": TUNING_VERSION,
        "split": "tune",
        "tau_a_curve": curve,
        "policy": "max coverage; precision>=0.98,M>0; ties higher precision then higher tau_a; "
        "tau_r: max recoverable gold via recommendations/review top3, "
        "fewer reviews, higher threshold",
    }
    if not feasible:
        return {**base, "status": "NO_FEASIBLE_CONFIGURATION", "config": None}
    accepted = max(feasible, key=lambda row: (row["coverage"], row["precision"], row["tau_a"]))
    tau_a = accepted["tau_a"]
    review_curve = []
    for tau_r in [value for value in thresholds if value <= tau_a]:
        config = template.model_copy(update={"tau_a": tau_a, "tau_r": tau_r})
        results = decisions(config)
        reachable = sum(
            len(
                (
                    set(result.recommendations)
                    if result.outcome == "recommendation"
                    else {c.target_id for c in result.candidates[:3]}
                    if result.outcome == "review"
                    else set()
                )
                & doc.gold_targets
            )
            for doc, result in zip(documents, results, strict=True)
        )
        reviews = sum(result.outcome == "review" for result in results)
        review_curve.append({"tau_r": tau_r, "reachable_gold_links": reachable, "reviews": reviews})
    review = max(
        review_curve, key=lambda row: (row["reachable_gold_links"], -row["reviews"], row["tau_r"])
    )
    config = template.model_copy(update={"tau_a": tau_a, "tau_r": review["tau_r"]})
    return {
        **base,
        "status": "FEASIBLE",
        "config": config.model_dump(mode="json"),
        "tau_r_curve": review_curve,
        "selected_tune_metrics": metrics(decisions(config)),
    }
