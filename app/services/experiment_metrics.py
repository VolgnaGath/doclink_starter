"""Evaluation only. Explicit denominators, missing annotations and intersecting slices."""

import re
import unicodedata
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Any

from app.services.evaluation import EvaluationDocument, evaluate


def normalize(field: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if field == "amount":
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    if field == "date":
        return date.fromisoformat(str(value)).isoformat()
    if field == "cp_inn":
        return re.sub(r"\D", "", str(value))
    if field == "number":
        value = unicodedata.normalize("NFKC", str(value)).casefold()
        value = re.sub(r"[‐‑‒–—−]", "-", value)
        return re.sub(r"[\s№]", "", value)
    return value


def field_accuracy(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for field in ("kind", "number", "date", "cp_inn", "amount"):
        correct = annotated = absent = 0
        for row in rows:
            gold = row["gold"]
            expected = normalize(field, gold["fields"].get(field))
            if field not in gold.get("annotated_fields", []):
                continue
            if gold["provenance"]["part"] == "real" and expected is None:
                continue
            annotated += 1
            absent += expected is None
            correct += row["valid"] and normalize(field, row["fields"].get(field)) == expected
        result[field] = {
            "value": correct / annotated if annotated else None,
            "numerator": correct,
            "denominator": annotated,
            "unannotated": len(rows) - annotated,
            "gold_absent": absent,
        }
    return result


def slices() -> tuple[str, ...]:
    return (
        "all",
        "real",
        "synth",
        "synth_extra",
        "hard",
        *(f"hard:{i}" for i in range(1, 10)),
        *(f"kind:{kind}" for kind in ("допсоглашение", "счёт", "акт", "УПД")),
    )


def in_slice(gold: dict[str, Any], name: str) -> bool:
    if name == "all":
        return True
    if name == "hard":
        return bool(gold["hard_types"])
    if name.startswith("hard:"):
        return int(name.split(":")[1]) in gold["hard_types"]
    if name.startswith("kind:"):
        return gold["fields"]["kind"] == name.split(":")[1]
    return gold["provenance"]["part"] == name


def linking_metrics(documents: tuple[EvaluationDocument, ...]) -> dict[str, dict[str, Any]]:
    metrics = asdict(evaluate(documents))
    n, m, correct, gold = (metrics[key] for key in ("n", "m", "correct_links", "gold_links"))
    covered = sum(
        d.result.outcome == "recommendation" and bool(d.result.recommendations) for d in documents
    )
    exact = sum(
        d.result.outcome != "error"
        and (set(d.result.recommendations) if d.result.outcome == "recommendation" else set())
        == d.gold_targets
        for d in documents
    )
    reviews = sum(d.result.outcome == "review" for d in documents)
    top3 = sum(
        len({c.target_id for c in d.result.candidates[:3]} & d.gold_targets)
        for d in documents
        if d.result.outcome == "review"
    )
    review_gold = sum(len(d.gold_targets) for d in documents if d.result.outcome == "review")
    fractions = {
        "precision": (correct, m),
        "recall": (correct, gold),
        "f1": (2 * correct, m + gold),
        "coverage": (covered, n),
        "error_share": (m - correct, m),
        "document_accuracy": (exact, n),
        "review_share": (reviews, n),
        "recall_at_3": (top3, review_gold),
    }
    return {
        name: {
            "value": value,
            "numerator": fractions[name][0] if name in fractions else int(value),
            "denominator": fractions[name][1] if name in fractions else None,
        }
        for name, value in metrics.items()
    }
