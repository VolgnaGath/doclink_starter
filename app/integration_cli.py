"""Repeatable connector administration; no model calls here."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select

from app.config import Settings
from app.db import get_session
from app.models import (
    Document,
    IntegrationEvent,
    IntegrationInbox,
    IntegrationState,
    Job,
    LLMCall,
    OperationalConfig,
)
from app.services.integration import poll, retry
from app.services.model_calls import fingerprint


def main() -> int:
    parser = argparse.ArgumentParser(description="Опрос, статус и повтор обмена mock1c")
    parser.add_argument("command", choices=["poll", "status", "retry", "audit"])
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--kind", choices=["import", "deliver"])
    parser.add_argument("--id", type=UUID)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings()
    if args.command == "poll":
        poll(settings, force=True, full=args.full)
    with get_session(settings.database_url) as session:
        if args.command == "audit":
            projection = select(
                Document.id,
                Document.sha256,
                Document.client_id,
                Document.source,
                Document.doc_kind,
                Document.number,
                Document.date,
                Document.card_extraction_id,
            ).order_by(Document.id)
            snapshot = [
                [str(v) if v is not None else None for v in row]
                for row in session.execute(projection)
            ]
            calls = [
                [str(v) if v is not None else None for v in row]
                for row in session.execute(
                    select(LLMCall.id, LLMCall.status, LLMCall.cost_rub).order_by(LLMCall.id)
                )
            ]
            config = session.get(OperationalConfig, 1)
            report = {
                "documents": len(snapshot),
                "documents_hash": fingerprint(snapshot),
                "llm_calls": len(calls),
                "llm_calls_hash": fingerprint(calls),
                "cost_rub": str(session.scalar(select(func.sum(LLMCall.cost_rub)))),
                "operational_config_hash": fingerprint(config.snapshot) if config else None,
                "paid_calls_enabled": settings.paid_calls_enabled,
                "active_jobs": session.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(
                        Job.status.in_(["queued", "running"]),
                    )
                ),
            }
            encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                args.output.write_text(encoded)
            print(encoded)
            return 0
        if args.command == "retry":
            if not args.kind or not args.id:
                parser.error("retry требует --kind и --id")
            retry(session, args.kind, args.id)
            session.commit()
        state = session.get(IntegrationState, "mock1c")
        print(
            json.dumps(
                {
                    "enabled": settings.mock1c_enabled,
                    "poll_error": state.error if state else None,
                    "last_poll_at": str(state.last_poll_at) if state else None,
                    "inbox": [
                        {
                            "id": str(r.id),
                            "external_id": str(r.external_id),
                            "document_id": str(r.document_id) if r.document_id else None,
                            "status": r.status,
                            "attempts": r.attempts,
                            "error": r.error,
                        }
                        for r in session.scalars(select(IntegrationInbox))
                    ],
                    "events": [
                        {
                            "id": str(r.id),
                            "link_id": str(r.link_id),
                            "operation": r.operation,
                            "status": r.status,
                            "attempts": r.attempts,
                            "error": r.error,
                        }
                        for r in session.scalars(select(IntegrationEvent))
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
