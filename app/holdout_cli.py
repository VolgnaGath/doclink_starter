"""Install the unchanged addendum and register/freeze a test-only matrix."""

import argparse
import hashlib
import json
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.config import Settings
from app.db import get_session
from app.models import (
    CorpusVersion,
    Document,
    DocumentEmbedding,
    ExperimentSeries,
    Extraction,
    Job,
    LLMCall,
    ModelConfig,
)
from app.services.corpus_import import CorpusError, prepare_corpus
from app.services.experiment_holdout import create_holdout_series, registration
from app.services.model_calls import fingerprint

ARCHIVE_SHA256 = "ccd1166b64d61ede4bd5954a1cb92c15d009ebb6050a37da053eb25d5b224674"


def audit(version_id: UUID) -> dict:
    with get_session(Settings().database_url) as session:
        version = session.get(CorpusVersion, version_id)
        ids = list(
            session.scalars(select(Document.id).where(Document.corpus_version_id == version_id))
        )
        models = {m.id: m.name for m in session.scalars(select(ModelConfig))}
        calls = list(
            session.scalars(
                select(LLMCall).where(LLMCall.document_id.in_(ids)).order_by(LLMCall.started_at)
            )
        )
        extractions = list(
            session.scalars(
                select(Extraction).where(Extraction.document_id.in_(ids), Extraction.is_current)
            )
        )
        jobs = list(session.scalars(select(Job).where(Job.document_id.in_(ids))))
        series = list(
            session.scalars(
                select(ExperimentSeries).where(ExperimentSeries.corpus_version_id == version_id)
            )
        )
        return {
            "created_at": datetime.now(UTC).isoformat(),
            "corpus_version_id": str(version_id),
            "fingerprint": version.fingerprint,
            "registration_hash": version.report.get("holdout_registration", {}).get(
                "registration_hash"
            ),
            "extraction_protocols": {
                key: {k: value.get(k) for k in ("test_started", "reused_test")}
                for key, value in version.report.get("extraction_protocols", {}).items()
            },
            "documents": len(ids),
            "vectors": len(
                list(
                    session.scalars(
                        select(DocumentEmbedding.document_id).where(
                            DocumentEmbedding.document_id.in_(ids)
                        )
                    )
                )
            ),
            "jobs": dict(Counter(f"{j.type}/{j.status}" for j in jobs)),
            "by_model": {
                name: {
                    "valid": sum(e.valid for e in extractions if e.model_config_id == model_id),
                    "errors": dict(
                        Counter(
                            e.error
                            for e in extractions
                            if e.model_config_id == model_id and not e.valid
                        )
                    ),
                    "physical_statuses": dict(
                        Counter(c.status for c in calls if c.model_config_id == model_id)
                    ),
                    "known_cost_rub": str(
                        sum(
                            c.cost_rub
                            for c in calls
                            if c.model_config_id == model_id and c.cost_rub is not None
                        )
                    ),
                    "unknown_cost_calls": [
                        str(c.id)
                        for c in calls
                        if c.model_config_id == model_id and c.cost_rub is None
                    ],
                }
                for model_id, name in models.items()
                if any(e.model_config_id == model_id for e in extractions)
            },
            "uncertain_calls": [
                {
                    "id": str(c.id),
                    "model": models[c.model_config_id],
                    "status": c.status,
                    "cost_rub": str(c.cost_rub) if c.cost_rub is not None else None,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "error": c.error,
                }
                for c in calls
                if c.status in {"started", "uncertain"}
            ],
            "series": [{"id": str(s.id), "status": s.status} for s in series],
            "evaluation_completed": bool(series) and all(s.status == "done" for s in series),
            "physical_call_ids": [str(c.id) for c in calls],
        }


def install(source: Path) -> dict:
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA256:
        raise CorpusError("Архив отличается от дополнения №2")
    with zipfile.ZipFile(source) as archive:
        files = {n: archive.read(n) for n in archive.namelist() if not n.endswith("/")}
    writes = {Path("corpus_history/addendum_2-original.zip"): raw}
    for name, content in files.items():
        if name.startswith("corpus/synth_holdout/") and ".." not in Path(name).parts:
            target = Path(name)
        elif name in {"README.md", "gen_synth.py", "gen_holdout.py"}:
            target = Path("corpus_history/addendum_2") / name
        else:
            raise CorpusError(f"Неожиданный файл: {name}")
        writes[target] = content
    if any(path.exists() and path.read_bytes() != value for path, value in writes.items()):
        raise CorpusError("Установка перезаписала бы изменённый файл")
    for path, value in writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    prepared = prepare_corpus(Path("corpus"), 20261104, 0, Settings(), holdout_only=True)
    if (prepared.report["total"], prepared.report["links"]) != (221, 146):
        raise CorpusError("Неверные количества holdout")
    return {
        "archive_sha256": ARCHIVE_SHA256,
        "fingerprint": prepared.fingerprint,
        **prepared.report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "register", "freeze", "audit"])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--corpus-version", type=UUID)
    parser.add_argument("--source-series", type=UUID)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "install":
        result = install(args.source)
    elif args.action == "audit":
        result = audit(args.corpus_version)
    else:
        settings = Settings()
        with get_session(settings.database_url) as session:
            if args.action == "register":
                result = registration(session, settings, args.corpus_version, args.source_series)
            else:
                series = create_holdout_series(
                    session, settings, args.corpus_version, args.source_series
                )
                result = {"series_id": str(series.id), "request_hash": series.request_hash}
            session.commit()
    if args.output.exists() and json.loads(args.output.read_text()) != result:
        raise ValueError("Не перезаписывать другой отчёт")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("report_hash:", fingerprint(result))


if __name__ == "__main__":
    main()
