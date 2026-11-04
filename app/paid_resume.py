"""Explicitly resume transport/budget refusals, preserving successes and unknown costs."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from app.config import Settings
from app.db import get_session
from app.models import Document, Extraction, Job, LLMCall
from app.services.model_calls import ProcessingError, uncertain_retry_authorized


def resume(settings: Settings, version_id: UUID, note: str, authorize: bool) -> dict[str, Any]:
    if not note.strip():
        raise ProcessingError("Нужно основание явного повтора")
    with get_session(settings.database_url) as session:
        session.execute(text("SELECT pg_advisory_xact_lock(310004)"))
        ids = list(
            session.scalars(select(Document.id).where(Document.corpus_version_id == version_id))
        )
        if not ids:
            raise ProcessingError("Корпус не найден")
        calls = list(
            session.scalars(
                select(LLMCall).where(LLMCall.document_id.in_(ids)).order_by(LLMCall.started_at)
            )
        )
        authorized = []
        for call in calls:
            if call.status != "uncertain" or call.reconciled or uncertain_retry_authorized(call):
                continue
            if not authorize:
                raise ProcessingError("Неопределённый расход требует явного допуска с резервом")
            if call.reserved_rub is None or call.reserved_rub <= 0:
                raise ProcessingError("Нет положительного резерва неизвестной попытки")
            prior = sum(
                uncertain_retry_authorized(c)
                for c in calls
                if c.root_input_hash == call.root_input_hash
            )
            if prior >= 3:
                raise ProcessingError(
                    "Предел: три явно разрешённых повтора неопределённого запроса"
                )
            call.snapshot = {
                **call.snapshot,
                "uncertain_retry_authorization": {
                    "note": note,
                    "authorized_at": datetime.now(UTC).isoformat(),
                    "reserved_rub": str(call.reserved_rub),
                    "cost_remains_unknown": True,
                },
            }
            authorized.append(str(call.id))
        requeued = []
        for job, extraction in session.execute(
            select(Job, Extraction)
            .join(
                Extraction,
                (Extraction.document_id == Job.document_id)
                & (Extraction.model_config_id == Job.model_config_id),
            )
            .where(
                Job.document_id.in_(ids),
                Job.type == "extract",
                Job.status == "error",
                Extraction.is_current,
                ~Extraction.valid,
                ~Extraction.preserved_error,
            )
            .with_for_update()
        ):
            error = extraction.error or ""
            physical = [c for c in calls if c.job_id == job.id]
            if not (
                "Неизвестный расход" in error
                or "бюджет" in error.lower()
                or (
                    physical
                    and all(c.status in {"error", "uncertain", "invalid"} for c in physical)
                    and any(uncertain_retry_authorized(c) for c in physical)
                )
            ):
                continue
            if any(
                c.status == "started"
                or (c.status == "uncertain" and not uncertain_retry_authorized(c))
                for c in physical
            ):
                raise ProcessingError("Нельзя повторять активный или неразрешённый запрос")
            job.status, job.error, job.locked_at = "queued", None, None
            job.payload = {
                **job.payload,
                "explicit_resume": {
                    "at": datetime.now(UTC).isoformat(),
                    "note": note,
                },
            }
            requeued.append(str(job.id))
        session.commit()
        return {
            "corpus_version_id": str(version_id),
            "note": note,
            "authorized_uncertain_call_ids": authorized,
            "requeued_job_ids": requeued,
            "requeued": len(requeued),
            "new_network_calls": 0,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-version", type=UUID, required=True)
    parser.add_argument("--note", required=True)
    parser.add_argument("--authorize-uncertain-reservations", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = resume(
        Settings(), args.corpus_version, args.note, args.authorize_uncertain_reservations
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps({k: v for k, v in result.items() if k != "requeued_job_ids"}, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
