"""Pair features: choose number and date from one reference, never independently."""

from decimal import Decimal

from app.services.linking.normalization import name_score, normalize_number, number_score, valid_inn
from app.services.linking.types import (
    CandidateScore,
    ContractReference,
    Document,
    Features,
    LinkingConfig,
    Penalty,
)


def unique_references(refs: tuple[ContractReference, ...]) -> tuple[ContractReference, ...]:
    unique: dict[tuple[str, object], ContractReference] = {}
    for ref in refs:
        key = (normalize_number(ref.number or ""), ref.date)
        previous = unique.get(key)
        # Repeated mentions of the same number/date are one reference. A verified
        # mention suffices, but different dates must never collapse.
        if previous is None or ref.verified:
            unique[key] = ref
    return tuple(unique.values())


def score_pair(
    source: Document, target: Document, cosine: float, config: LinkingConfig
) -> CandidateScore:
    refs = unique_references(source.fields.references)
    pairs = [
        Features(
            num=number_score(ref.number, target.fields.number),
            date=float(ref.date is not None and ref.date == target.card.date),
            reference_index=index,
        )
        for index, ref in enumerate(refs)
        if number_score(ref.number, target.fields.number) > 0
    ]
    best = max(
        pairs,
        key=lambda pair: config.weights.num * pair.num + config.weights.date * pair.date,
        default=Features(),
    )
    features = best.model_copy(
        update={
            "inn": float(
                bool(source.fields.inn)
                and source.fields.inn == target.fields.inn
                and valid_inn(source.fields.inn)
            ),
            "name": name_score(source.fields.name, target.fields.name),
            "sem": max(0, cosine) if config.use_semantic else 0,
        }
    )
    penalties = []
    if config.checks:
        if source.fields.amount is not None and target.fields.amount is not None:
            if source.fields.amount > target.fields.amount * Decimal("1.25"):
                penalties.append(
                    Penalty(reason="amount_over_125_percent", value=config.amount_penalty)
                )
        for side, fields in (("source", source.fields), ("target", target.fields)):
            if fields.inn and not valid_inn(fields.inn):
                penalties.append(
                    Penalty(reason=f"{side}_invalid_inn", value=config.invalid_inn_penalty)
                )
    score = sum(
        getattr(features, key) * weight for key, weight in config.weights.model_dump().items()
    )
    return CandidateScore(
        target_id=target.card.document_id,
        features=features,
        penalties=tuple(penalties),
        score=score - sum(p.value for p in penalties),
        link_type={"допсоглашение": "изменение", "счёт": "основание"}.get(
            source.card.kind, "исполнение"
        ),
        source_evidence=source.fields,
        target_evidence=target.fields,
    )
