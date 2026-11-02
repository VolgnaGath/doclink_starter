"""Explicit technical link batch; thresholds are supplied by a configuration file.

This command does not select thresholds, freeze an experiment or call remote models.
"""

import argparse
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.config import Settings
from app.db import get_session
from app.models import Job
from app.services.embedding_store import encoding_config
from app.services.jobs import work_once
from app.services.linking.types import LinkingConfig
from app.services.linking_store import enqueue_linking


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", type=UUID, action="append", required=True)
    parser.add_argument("--model", type=UUID)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--drain", action="store_true")
    args = parser.parse_args()
    config = LinkingConfig.model_validate_json(args.config.read_text())
    if config.method == "combined" and args.model is None:
        parser.error("Для combined необходим --model UUID")
    settings = Settings()
    with get_session(settings.database_url) as session:
        jobs = [
            enqueue_linking(
                session,
                id,
                args.model,
                config,
                encoding_config(settings),
                retry=args.retry,
            )
            for id in args.document
        ]
        session.commit()
    if args.drain:
        while work_once(
            settings,
            document_ids=args.document,
            model_ids=[args.model] if args.model else None,
            job_types=["link"],
        ):
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
