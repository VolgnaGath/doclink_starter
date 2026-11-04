# Приложение Г — установление связей

Раскладка фрагментов предварительная: внешний макет приложений не предоставлен.

## `link`

Чистый вход метода, фильтр допустимых кандидатов и полный рейтинг.

Источник: `app/services/linking/methods.py:17–74`; версия кода `e040715867d7bf991a67c233a0c509bf1d4997d4`.
SHA-256 полного файла: `06face8ffbce5dcdfe3540abd0af79dd8ea51cf10d71a2107bef54003e65776a`.

```python
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
```

## `decide`

Документное решение, неоднозначность и две независимые ссылки.

Источник: `app/services/linking/decisions.py:38–87`; версия кода `e040715867d7bf991a67c233a0c509bf1d4997d4`.
SHA-256 полного файла: `4f7503b7e1f31843961e500d662d91c8e7bf505ecb2c97f52762243e79689f51`.

```python
def decide(
    scores: tuple[CandidateScore, ...],
    config: LinkingConfig,
    blockers: tuple[str, ...] = (),
    resolved: tuple[str, ...] = (),
) -> LinkingResult:
    ranked = tuple(
        score.model_copy(update={"rank": index})
        for index, score in enumerate(sorted(scores, key=lambda s: (-s.score, s.target_id)), 1)
    )
    chosen: tuple[str, ...] = ()
    if blockers:
        outcome, reasons = "review", blockers
    elif not ranked:
        outcome, reasons = "none", ("empty_pool",)
    elif len(resolved) == 2:
        by_id = {score.target_id: score for score in ranked}
        if all(by_id[target].score >= config.tau_a for target in resolved):
            outcome, reasons, chosen = "recommendation", ("two_resolved_references",), resolved
        else:
            outcome, reasons = "review", ("partial_reference_threshold",)
    else:
        # With an explicit reference, only its resolved target can be recommended.
        leader = next((score for score in ranked if score.target_id in resolved), ranked[0])
        tied = sum(abs(score.score - leader.score) <= 1e-12 for score in ranked) > 1
        if tied and (config.method != "combined" or config.checks):
            outcome, reasons = "review", ("equal_leaders",)
        elif leader.score >= config.tau_a:
            outcome, reasons, chosen = (
                "recommendation",
                ("above_accept_threshold",),
                (leader.target_id,),
            )
        elif ranked[0].score >= config.tau_r:
            outcome, reasons = "review", ("above_review_threshold",)
        else:
            outcome, reasons = "none", ("below_review_threshold",)
    ranked = tuple(
        score.model_copy(
            update={
                "decision": "recommendation"
                if score.target_id in chosen
                else ("review" if outcome == "review" else "none")
            }
        )
        for score in ranked
    )
    return LinkingResult(
        outcome=outcome, reasons=reasons, recommendations=chosen, candidates=ranked, config=config
    )
```
