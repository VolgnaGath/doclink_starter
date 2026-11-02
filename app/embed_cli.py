"""Explicit local embedding queue. No generation, extraction retry or linking."""

import argparse
import json
from uuid import UUID

from sqlalchemy import select

from app.config import Settings
from app.db import get_session
from app.models import Document, Job
from app.services.embedding_store import encoding_config, enqueue_embedding
from app.services.jobs import work_once


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--document", type=UUID, action="append")
    selector.add_argument("--corpus-version", type=UUID)
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--drain", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    with get_session(settings.database_url) as session:
        ids = args.document
        if args.corpus_version:
            ids = list(
                session.scalars(
                    select(Document.id).where(Document.corpus_version_id == args.corpus_version)
                )
            )
        if not ids:
            parser.error("Документы не найдены")
        jobs = [
            enqueue_embedding(session, id, encoding_config(settings), retry=args.retry)
            for id in ids
        ]
        session.commit()
    if args.drain:
        while work_once(settings, document_ids=ids, job_types=["embed"]):
            pass
    with get_session(settings.database_url) as session:
        rows = list(session.scalars(select(Job).where(Job.id.in_(jobs)).order_by(Job.id)))
        print(
            json.dumps(
                [
                    {
                        "id": str(job.id),
                        "document": str(job.document_id),
                        "status": job.status,
                        "error": job.error,
                    }
                    for job in rows
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return int(any(job.status == "error" for job in rows))


if __name__ == "__main__":
    raise SystemExit(main())
