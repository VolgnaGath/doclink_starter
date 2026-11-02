"""Three methods with mandatory shared card/client/version/date constraints."""

import re

from app.services.extraction_schema import regex_extract
from app.services.linking.decisions import decide, reference_guards
from app.services.linking.features import score_pair
from app.services.linking.types import (
    CandidateScore,
    Features,
    LinkingConfig,
    LinkingInput,
    LinkingResult,
)


def link(data: LinkingInput, config: LinkingConfig) -> LinkingResult:
    source = data.source

    def terminal(outcome: str, reason: str) -> LinkingResult:
        return LinkingResult(outcome=outcome, reasons=(reason,), config=config)

    if source.card.error:
        return terminal("error", "source_card_error")
    if config.method == "combined" and source.fields.error:
        return terminal("error", "source_extraction_error")
    if config.method in {"combined", "semantic"} and config.use_semantic and data.semantic_error:
        return terminal("error", "semantic_error")
    if source.card.kind is None or source.card.date is None:
        return terminal("review", "unknown_source_kind_or_date")
    if source.card.kind not in {"допсоглашение", "счёт", "акт", "УПД"}:
        return terminal("review", "unsupported_source_kind")
    pool = tuple(
        doc
        for doc in data.candidates
        if (
            doc.card.document_id != source.card.document_id
            and doc.card.client_id == source.card.client_id
            and doc.card.corpus_version_id == source.card.corpus_version_id
            and doc.card.kind == "договор"
            and doc.card.date is not None
            and doc.card.date <= source.card.date
        )
    )
    if any(doc.card.error for doc in pool):
        return terminal("error", "candidate_card_error")
    if config.method == "combined" and any(doc.fields.error for doc in pool):
        return terminal("error", "candidate_extraction_error")
    similarities = {score.target_id: score.cosine for score in data.similarities}
    if config.method in {"combined", "semantic"} and config.use_semantic:
        if any(doc.card.document_id not in similarities for doc in pool):
            return terminal("error", "missing_semantic_score")
    blockers, resolved = (), ()
    if config.method == "combined" and config.checks:
        blockers, resolved = reference_guards(source, pool)
    scores: list[CandidateScore] = []
    # Baseline deliberately uses raw exact numbers, including undated regex references.
    raw_refs = set(re.findall(r"договор[ауеом]*\s*№\s*([^\s,;]+)", source.text, re.I))
    for target in pool:
        cosine = similarities.get(target.card.document_id, 0)
        pair = score_pair(source, target, cosine, config)
        if config.method == "rules":
            number = regex_extract(target.text).number
            value = float(bool(number) and number in raw_refs)
            pair = pair.model_copy(
                update={"features": Features(num=value), "penalties": (), "score": value}
            )
        elif config.method == "semantic":
            value = max(0, cosine)
            pair = pair.model_copy(
                update={"features": Features(sem=value), "penalties": (), "score": value}
            )
        scores.append(pair)
    return decide(tuple(scores), config, blockers, resolved)
