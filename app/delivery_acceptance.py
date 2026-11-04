"""Final isolated technical scenario; LLM quality comes from the replayed real matrix."""

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
    ExperimentSeries,
    Job,
    Link,
    LLMCall,
    ModelConfig,
    Prompt,
)
from app.services.embeddings import EncodingConfig
from app.services.linking.types import LinkingConfig
from app.services.pipeline import install_configuration
from mock1c.protocol import InboxDocument


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "audit"])
    parser.add_argument(
        "--output", type=Path, default=Path("docs/reports/stage8-delivery-live-2026-11-04.json")
    )
    args = parser.parse_args()
    settings = Settings()
    if os.environ.get("DELIVERY_ACCEPTANCE_ENABLED") != "1" or settings.paid_calls_enabled:
        raise ValueError("Только изолированный стенд без платных вызовов")
    with get_session(settings.database_url) as session:
        if args.command == "prepare":
            if session.scalar(
                select(func.count()).select_from(Document).where(Document.source == "mock1c")
            ):
                raise ValueError("Новый сценарий уже подготовлен")
            model = ModelConfig(
                name="Delivery: real regex", provider="regex", model_uri="regex", placement="local"
            )
            prompt = Prompt(name="extract", version="delivery", text="{text}", schema={})
            session.add_all([model, prompt])
            session.flush()
            install_configuration(
                session, model, prompt, LinkingConfig(tau_a=0.2, tau_r=0.1), EncodingConfig()
            )
            session.commit()
            root, client_id = Path("/data/accept-inbox"), uuid4()
            for filename, body in (
                ("contract.txt", "Договор № FINAL-8 от 01.02.2026\nПоставка оборудования."),
                (
                    "invoice.txt",
                    "Счёт № FINAL-9 от 02.02.2026\nПо договору № FINAL-8 от 01.02.2026\n"
                    "Поставка оборудования.",
                ),
            ):
                item = InboxDocument(
                    id=uuid4(),
                    client_id=client_id,
                    client_name="Сквозная сдача",
                    filename=filename,
                    registered_at=datetime.now(UTC),
                    is_anonymized=True,
                )
                (root / filename).write_text(body)
                (root / (filename + ".json")).write_text(item.model_dump_json())
        else:
            docs = list(session.scalars(select(Document).where(Document.source == "mock1c")))
            assert len(docs) == 2
            links = list(
                session.scalars(
                    select(Link).where(
                        Link.source_id.in_([d.id for d in docs]), Link.removed_at.is_(None)
                    )
                )
            )
            assert len(links) == 1 and links[0].origin == "confirmed"
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.status.in_(["queued", "running"]))
                )
                == 0
            )
            journal = httpx.get(settings.mock1c_url + "/journal").json()
            assert len(journal["links"]) == len(journal["events"]) == 1
            report = {
                "scenario": (
                    "Новый mock1c документ → реальные regex/BGE-M3 → рекомендация "
                    "→ Chromium confirm → mock1c"
                ),
                "quality_scope": (
                    "Не LLM-оценка; реальные LLM и их ошибки отдельно воспроизведены матрицей"
                ),
                "paid_calls_enabled": settings.paid_calls_enabled,
                "document_ids": [str(d.id) for d in docs],
                "link_id": str(links[0].id),
                "review_action_id": str(links[0].review_action_id),
                "llm_calls": session.scalar(select(func.count()).select_from(LLMCall)),
                "embeddings": [
                    {
                        "document_id": str(e.document_id),
                        "embedding_id": str(e.embedding_id),
                        "cache_hit": e.cache_hit,
                    }
                    for e in session.scalars(
                        select(DocumentEmbedding).where(
                            DocumentEmbedding.document_id.in_([d.id for d in docs])
                        )
                    )
                ],
                "datasets": [
                    {"id": str(s.id), "hash": s.dataset["dataset_hash"]}
                    for s in session.scalars(
                        select(ExperimentSeries).where(ExperimentSeries.status == "done")
                    )
                ],
                "journal": journal,
            }
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
