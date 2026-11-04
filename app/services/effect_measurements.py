"""Outcome/action-aware extension of experiment review measurements, with exclusions."""

from collections import Counter
from statistics import mean, median
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Document,
    ExperimentResult,
    LinkingResult,
    ReviewAction,
    ReviewSession,
    WorkingCopy,
)
from app.services.effect import OUTCOMES
from app.services.model_calls import fingerprint
from app.services.working_identity import copy_origin


def measurement_audit(
    session: Session,
    run_id: UUID,
    *,
    working_set_id: UUID | None = None,
    selected_result_ids: list[str] | None = None,
) -> dict[str, Any]:
    results = {
        str(r.id): r
        for r in session.scalars(
            select(LinkingResult)
            .join(ExperimentResult, ExperimentResult.result_id == LinkingResult.id)
            .join(Document, Document.id == ExperimentResult.document_id)
            .where(ExperimentResult.run_id == run_id, Document.split == "test")
            .order_by(LinkingResult.id)
        )
    }
    copies = {c.document_id: c for c in session.scalars(select(WorkingCopy))}
    rows = []
    seen = set()
    for review in session.scalars(
        select(ReviewSession).order_by(ReviewSession.opened_at, ReviewSession.id)
    ):
        copy = copies.get(review.document_id)
        if copy is None or (working_set_id and copy.working_set_id != working_set_id):
            continue
        source_id = str(copy.source_result_id)
        if source_id not in results or (
            selected_result_ids is not None and source_id not in selected_result_ids
        ):
            continue
        origin = review.context.get("provenance", {}).get("working_copy") or {}
        current = copy_origin(session, review.document_id) or {}
        actions = list(
            session.scalars(
                select(ReviewAction)
                .where(ReviewAction.session_id == review.id)
                .order_by(ReviewAction.decided_at, ReviewAction.id)
            )
        )
        reason = None
        if source_id in seen:
            reason = "repeat_session"
        elif review.completed_at is None or review.duration_ms is None:
            reason = "unfinished"
        elif not origin.get("initial_inputs") or origin.get("source_result_id") != source_id:
            reason = "non_initial_inputs"
        elif not current.get("initial_inputs"):
            reason = "changed_inputs"
        elif any(a.action in {"edit", "reopen"} for a in actions):
            reason = "edited_or_reopened"
        elif not any(a.action in {"complete", "without_links"} for a in actions):
            reason = "no_completion_action"
        elif any(a.result_id != review.result_id for a in actions):
            reason = "different_result"
        seen.add(source_id)
        rows.append(
            {
                "session_id": str(review.id),
                "document_id": str(review.document_id),
                "source_result_id": source_id,
                "outcome": results[source_id].outcome,
                "opened_at": review.opened_at.isoformat(),
                "completed_at": review.completed_at.isoformat() if review.completed_at else None,
                "duration_ms": review.duration_ms,
                "excluded_reason": reason,
                "actions": [
                    {
                        "id": str(a.id),
                        "action": a.action,
                        "duration_ms": a.duration_ms,
                        "result_id": str(a.result_id) if a.result_id else None,
                    }
                    for a in actions
                ],
            }
        )
    groups = {}
    for outcome in OUTCOMES:
        accepted = [r for r in rows if r["excluded_reason"] is None and r["outcome"] == outcome]
        times = [r["duration_ms"] for r in accepted]
        groups[outcome] = {
            "n": len(times),
            "mean_ms": mean(times) if times else None,
            "median_ms": median(times) if times else None,
            "session_ids": [r["session_id"] for r in accepted],
            "action_ids": [a["id"] for r in accepted for a in r["actions"]],
        }
    return {
        "protocol": "effect-review-v1",
        "working_set_id": str(working_set_id) if working_set_id else None,
        "usable_as_measurement": working_set_id is not None and selected_result_ids is not None,
        "groups": groups,
        "sessions": rows,
        "exclusions": dict(Counter(r["excluded_reason"] for r in rows if r["excluded_reason"])),
        "not_opened_result_ids": sorted(set(selected_result_ids or []) - seen),
        "note": (
            "Длительности действий накопительные; не суммировать. Без протокола — только аудит."
        ),
    }


def validate_protocol(protocol: dict[str, Any]) -> None:
    if not isinstance(protocol, dict):
        raise ValueError("Ожидается объект протокола")
    content = {k: v for k, v in protocol.items() if k != "protocol_hash"}
    if protocol.get("version") != "effect-review-v1" or fingerprint(content) != protocol.get(
        "protocol_hash"
    ):
        raise ValueError("Неверный протокол человеческих замеров")
