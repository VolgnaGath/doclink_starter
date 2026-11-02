"""Whole-document guards, evaluated against the full eligible pool before thresholds."""

from app.services.linking.features import unique_references
from app.services.linking.normalization import number_score
from app.services.linking.types import CandidateScore, Document, LinkingConfig, LinkingResult


def reference_guards(
    source: Document, pool: tuple[Document, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    reasons: list[str] = []
    resolved: list[str] = []
    refs = unique_references(source.fields.references)
    if len(refs) >= 3:
        reasons.append("three_or_more_references")
    for ref in refs:
        if not ref.verified:
            reasons.append("unverified_reference")
        matches = [doc for doc in pool if number_score(ref.number, doc.fields.number) >= 0.9]
        dated = [doc for doc in matches if ref.date is None or ref.date == doc.card.date]
        if not ref.number:
            reasons.append("reference_number_unknown")
        elif not matches:
            reasons.append("reference_number_conflict")
        elif not dated:
            reasons.append("reference_date_conflict")
        elif len(dated) > 1:
            reasons.append("ambiguous_reference")
        else:
            if not dated[0].fields.number_verified:
                reasons.append("unverified_target_number")
            resolved.append(dated[0].card.document_id)
    if len(set(resolved)) >= 3:
        reasons.append("three_or_more_targets")
    return tuple(dict.fromkeys(reasons)), tuple(dict.fromkeys(resolved))


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
