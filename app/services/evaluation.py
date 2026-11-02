"""Pure evaluation after linking. Never imported by the linking engine."""

from dataclasses import dataclass

from app.services.linking.types import LinkingResult


@dataclass(frozen=True)
class EvaluationDocument:
    result: LinkingResult
    gold_targets: frozenset[str]


@dataclass(frozen=True)
class Metrics:
    n: int
    m: int
    correct_links: int
    gold_links: int
    precision: float
    recall: float
    f1: float
    coverage: float
    error_share: float | None
    document_accuracy: float
    review_share: float
    processing_errors: int
    recall_at_3: float


def evaluate(documents: tuple[EvaluationDocument, ...]) -> Metrics:
    """Caller selects incoming documents and split; empty gold/error rows stay in N."""
    n = len(documents)
    m = correct = gold = covered = exact = reviews = errors = top3 = review_gold = 0
    for doc in documents:
        result = doc.result
        predicted = set(result.recommendations) if result.outcome == "recommendation" else set()
        m += len(predicted)
        correct += len(predicted & doc.gold_targets)
        gold += len(doc.gold_targets)
        covered += bool(predicted)
        exact += predicted == doc.gold_targets and result.outcome != "error"
        reviews += result.outcome == "review"
        errors += result.outcome == "error"
        if result.outcome == "review":
            review_gold += len(doc.gold_targets)
            top3 += len(
                {candidate.target_id for candidate in result.candidates[:3]} & doc.gold_targets
            )
    precision = correct / m if m else 0
    recall = correct / gold if gold else 0
    return Metrics(
        n=n,
        m=m,
        correct_links=correct,
        gold_links=gold,
        precision=precision,
        recall=recall,
        f1=2 * precision * recall / (precision + recall) if precision + recall else 0,
        coverage=covered / n if n else 0,
        error_share=1 - precision if m else None,
        document_accuracy=exact / n if n else 0,
        review_share=reviews / n if n else 0,
        processing_errors=errors,
        recall_at_3=top3 / review_gold if review_gold else 0,
    )
