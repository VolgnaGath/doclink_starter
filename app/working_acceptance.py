"""Offline acceptance on saved real inputs; never creates an accountant decision."""

import argparse
import json
import time
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import (
    Document,
    Extraction,
    Job,
    LinkingResult,
    LLMCall,
    ProcessingPipeline,
    WorkingCopy,
)
from app.services.jobs import work_once
from app.services.model_calls import fingerprint
from app.services.pipeline import reconcile


def source_audit(settings: Settings, version_id: UUID) -> dict[str, object]:
    with get_session(settings.database_url) as session:
        ids = select(Document.id).where(Document.corpus_version_id == version_id)
        cards = session.execute(
            select(
                Document.id,
                Document.client_id,
                Document.doc_kind,
                Document.number,
                Document.date,
                Document.card_extraction_id,
                Document.gold,
                Document.deal_id,
                Document.split,
            )
            .where(Document.corpus_version_id == version_id)
            .order_by(Document.id)
        ).all()
        extractions = session.execute(
            select(
                Extraction.id,
                Extraction.input_hash,
                Extraction.valid,
                Extraction.error,
                Extraction.is_current,
            )
            .where(Extraction.document_id.in_(ids))
            .order_by(Extraction.id)
        ).all()
        results = session.execute(
            select(LinkingResult.id, LinkingResult.input_hash, LinkingResult.outcome)
            .where(LinkingResult.source_id.in_(ids))
            .order_by(LinkingResult.id)
        ).all()
        return {
            "source_hash": fingerprint(
                [list(map(list, rows)) for rows in [cards, extractions, results]]
            ),
            "llm_calls": session.scalar(select(func.count()).select_from(LLMCall)),
            "cost_rub": str(session.scalar(select(func.sum(LLMCall.cost_rub)))),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-version", type=UUID, required=True)
    parser.add_argument("--document", type=UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings()
    if settings.paid_calls_enabled:
        raise ValueError("Приёмка рабочих копий требует PAID_CALLS_ENABLED=false")
    before = source_audit(settings, args.corpus_version)
    # HTTPX ASGI uses the same route/dependency/serialization path as the browser.
    import asyncio

    import httpx

    async def create() -> dict[str, object]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://acceptance"
        ) as client:
            login = await client.post(
                "/login", data={"username": settings.app_user, "password": settings.app_password}
            )
            assert login.status_code == 303
            response = await client.post(
                "/api/v1/working-sets",
                json={
                    "name": "Приёмка 5.6 — реальные рабочие копии",
                    "corpus_version_id": str(args.corpus_version),
                    "document_ids": [str(args.document)],
                    "idempotency_key": str(
                        uuid5(
                            NAMESPACE_URL, f"doclink-stage5.6:{args.corpus_version}:{args.document}"
                        )
                    ),
                },
            )
            assert response.status_code == 201, response.text
            return response.json()

    work = asyncio.run(create())
    work_id = UUID(str(work["id"]))
    with get_session(settings.database_url) as session:
        ids = list(
            session.scalars(
                select(WorkingCopy.document_id).where(WorkingCopy.working_set_id == work_id)
            )
        )
    for _ in range(300):
        with get_session(settings.database_url) as session:
            reconcile(session)
            session.commit()
            pipes = list(
                session.scalars(
                    select(ProcessingPipeline).where(ProcessingPipeline.document_id.in_(ids))
                )
            )
            if pipes and all(p.stage == "done" or p.error for p in pipes):
                break
        if not work_once(settings, document_ids=ids):
            time.sleep(0.1)
    else:
        raise RuntimeError("Рабочие копии не завершились")
    after = source_audit(settings, args.corpus_version)
    if before != after:
        raise RuntimeError("Изменились оригинальные данные или журнал вызовов")
    with get_session(settings.database_url) as session:
        copy = session.scalar(
            select(WorkingCopy).where(
                WorkingCopy.working_set_id == work_id,
                WorkingCopy.source_document_id == args.document,
            )
        )
        pipe = session.get(ProcessingPipeline, copy.document_id)
        result = session.get(LinkingResult, pipe.result_id) if pipe.result_id else None
        if pipe.error or result is None or result.outcome == "error":
            raise RuntimeError(pipe.error or (result.error if result else "Нет исхода поиска"))
        report = {
            "working_set_id": str(work_id),
            "source_document_id": str(args.document),
            "document_id": str(copy.document_id),
            "copied_documents": len(ids),
            "result_id": str(result.id),
            "outcome": result.outcome,
            "candidates": len(result.input_snapshot["candidates"]),
            "origin": result.provenance.get("working_copy"),
            "before": before,
            "after": after,
            "new_llm_calls": 0,
            "paid_calls_enabled": False,
            "extraction_jobs": session.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.document_id.in_(ids), Job.type == "extract")
            ),
        }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
