"""Isolated live acceptance preparation/audit. Regex is explicit, BGE is real."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from app.config import Settings
from app.db import get_session
from app.models import (
    Document,
    DocumentEmbedding,
    Embedding,
    Extraction,
    IntegrationAttempt,
    IntegrationEvent,
    IntegrationInbox,
    Job,
    Link,
    LinkingResult,
    LLMCall,
    ModelConfig,
    Prompt,
    StageTiming,
)
from app.services.embeddings import EncodingConfig
from app.services.linking.types import LinkingConfig
from app.services.pipeline import install_configuration
from mock1c.protocol import InboxDocument


def prepare(settings: Settings) -> None:
    with get_session(settings.database_url) as session:
        if session.scalar(select(func.count()).select_from(Document)):
            raise RuntimeError("Приёмка требует чистую изолированную БД")
        model = ModelConfig(
            name="Integration acceptance: real regex",
            provider="regex",
            model_uri="regex",
            placement="local",
        )
        prompt = Prompt(name="extract", version="integration-acceptance", text="{text}", schema={})
        session.add_all([model, prompt])
        session.flush()
        install_configuration(
            session, model, prompt, LinkingConfig(tau_a=0.2, tau_r=0.1), EncodingConfig()
        )
        session.commit()
    owner = uuid4()
    root = Path("/data/accept-inbox")
    now = datetime.now(UTC)
    for filename, body in (
        ("contract.txt", "Договор № INT-7 от 01.02.2026\nПоставка оборудования клиенту."),
        (
            "invoice.txt",
            "Счёт № INT-8 от 02.02.2026\nПо договору № INT-7 от 01.02.2026\n"
            "Поставка оборудования клиенту.",
        ),
    ):
        doc = InboxDocument(
            id=uuid4(),
            client_id=owner,
            client_name="Приёмка интеграции",
            filename=filename,
            registered_at=now,
            is_anonymized=True,
        )
        (root / filename).write_text(body, encoding="utf-8")
        (root / (filename + ".json")).write_text(doc.model_dump_json(), encoding="utf-8")


def audit(settings: Settings, output: Path) -> None:
    with get_session(settings.database_url) as session:
        counts = {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in (
                Document,
                Extraction,
                Embedding,
                DocumentEmbedding,
                LinkingResult,
                LLMCall,
                IntegrationEvent,
                Link,
            )
        }
        assert counts["documents"] == counts["extractions"] == counts["embeddings"] == 2
        assert counts["llm_calls"] == 0 and counts["linking_results"] == counts["links"] == 1
        assert counts["integration_events"] == 2
        assert (
            session.scalar(
                select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running"]))
            )
            == 0
        )
        assert all(row.status == "done" for row in session.scalars(select(IntegrationInbox)))
        assert all(row.status == "done" for row in session.scalars(select(IntegrationEvent)))
        assert all(row.removed_at for row in session.scalars(select(Link)))
        result = session.scalar(select(LinkingResult))
        assert result.outcome == "recommendation"
        journal = httpx.get(settings.mock1c_url + "/journal", timeout=10).json()
        assert [e["operation"] for e in journal["events"]] == ["create", "remove"] and not journal[
            "links"
        ]
        report = {
            "scope": "isolated integration, real regex + real BGE-M3 CPU; not LLM quality",
            "paid_calls_enabled": settings.paid_calls_enabled,
            "counts": counts,
            "result_id": str(result.id),
            "outcome": result.outcome,
            "source_id": str(result.source_id),
            "embeddings": [
                {
                    "document_id": str(row.document_id),
                    "cache_hit": row.cache_hit,
                    "embedding_id": str(row.embedding_id),
                }
                for row in session.scalars(select(DocumentEmbedding))
            ],
            "timings": [
                {"stage": row.stage, "duration_ms": row.duration_ms, "ok": row.ok}
                for row in session.scalars(select(StageTiming).order_by(StageTiming.id))
            ],
            "events": [
                {
                    "id": str(row.id),
                    "operation": row.operation,
                    "status": row.status,
                    "attempts": row.attempts,
                }
                for row in session.scalars(
                    select(IntegrationEvent).order_by(IntegrationEvent.created_at)
                )
            ],
            "exchange_attempts": [
                {
                    "kind": row.kind,
                    "status_code": row.status_code,
                    "duration_ms": row.duration_ms,
                    "error": row.error,
                }
                for row in session.scalars(
                    select(IntegrationAttempt).order_by(IntegrationAttempt.id)
                )
            ],
            "mock1c_journal": journal,
        }
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> int:
    if os.environ.get("INTEGRATION_ACCEPTANCE_ENABLED") != "1":
        raise RuntimeError("Только изолированный стенд accept-integration-up")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "audit"])
    parser.add_argument(
        "--output", type=Path, default=Path("docs/reports/stage7-live-2026-11-04.json")
    )
    args = parser.parse_args()
    settings = Settings()
    if settings.paid_calls_enabled:
        raise RuntimeError("Приёмка интеграции не включает платные вызовы")
    if args.command == "prepare":
        prepare(settings)
    else:
        audit(settings, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
