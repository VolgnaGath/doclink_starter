"""Infrastructure tests; fixture embeddings and fields are not experimental evidence."""

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, select
from test_experiments import create
from test_experiments import database as database
from test_experiments import matrix as matrix
from test_experiments import sample as sample

from app.config import Settings
from app.db import get_session
from app.models import (
    CorpusVersion,
    Document,
    DocumentEmbedding,
    DocumentText,
    Embedding,
    ExperimentResult,
    Extraction,
    ThresholdCurve,
)
from app.services.corpus_import import CorpusError, prepare_corpus
from app.services.embeddings import EncodingConfig, prepare_text
from app.services.experiment_holdout import create_holdout_series, registration
from app.services.experiments import execute_series, runs
from app.services.model_calls import ProcessingError


def test_holdout_has_only_explicit_test_and_is_separate(settings: Settings) -> None:
    root = Path(__file__).resolve().parents[1] / "corpus"
    prepared = prepare_corpus(root, 20261104, 0, settings, holdout_only=True)
    assert prepared.report["total"] == 221
    assert prepared.report["links"] == 146
    assert sum(d.gold["fields"]["kind"] != "договор" for d in prepared.documents) == 136
    assert {d.split for d in prepared.documents} == {"test"}
    assert {d.part for d in prepared.documents} == {"synth_holdout"}
    with pytest.raises(CorpusError, match="tune_share"):
        prepare_corpus(root, 20261104, 0.3, settings, holdout_only=True)


def test_holdout_rejects_explicit_tune(settings: Settings, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1] / "corpus"
    part = tmp_path / "synth_holdout"
    part.mkdir()
    raw = (root / "synth_holdout/documents.csv").read_bytes()
    (part / "documents.csv").write_bytes(raw.replace(b";test", b";tune", 1))
    with pytest.raises(CorpusError, match="split=test"):
        prepare_corpus(tmp_path, 20261104, 0, settings, holdout_only=True)


def test_transfer_never_retunes_and_freezes_all_before_test(
    database: Settings, matrix: tuple[UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = create(database, matrix[0])
    execute_series(database, source_id)
    with get_session(database.database_url) as session:
        version = CorpusVersion(
            name="holdout fixture", fingerprint="holdout", seed=20261104, tune_share=0, report={}
        )
        session.add(version)
        session.flush()
        version_id = version.id
        docs = list(session.scalars(select(Document)))
        for doc in docs:
            values = {
                c.name: getattr(doc, c.name)
                for c in Document.__table__.columns
                if c.name not in {"id", "created_at"}
            }
            gold = {
                **doc.gold,
                "case": None,
                "links": [
                    {**link, "target": "hold_" + link["target"]} for link in doc.gold["links"]
                ],
            }
            body = session.get(DocumentText, doc.id).text + "\nHoldout fixture"
            clone = Document(
                **{
                    **values,
                    "corpus_version_id": version.id,
                    "corpus_doc_id": "hold_" + doc.corpus_doc_id,
                    "split": "test",
                    "sha256": hashlib.sha256(body.encode()).hexdigest(),
                    "gold": gold,
                }
            )
            session.add(clone)
            session.flush()
            session.add(
                DocumentText(document_id=clone.id, text=body, pages=1, chars=len(body), ocr_pages=0)
            )
            for extraction in session.scalars(
                select(Extraction).where(Extraction.document_id == doc.id)
            ):
                fields = {
                    c.name: getattr(extraction, c.name)
                    for c in Extraction.__table__.columns
                    if c.name not in {"id", "created_at"}
                }
                session.add(Extraction(**{**fields, "document_id": clone.id}))
            prepared = prepare_text(body, EncodingConfig())
            vector = Embedding(
                input_hash=prepared.input_hash,
                text_hash=prepared.text_hash,
                model="BAAI/bge-m3",
                revision=EncodingConfig().revision,
                snapshot=json.loads(prepared.snapshot),
                vector=[1.0] + [0.0] * 1023,
                tokens=10,
                chunks=1,
                truncated=False,
                duration_ms=1,
            )
            session.add(vector)
            session.flush()
            session.add(
                DocumentEmbedding(
                    document_id=clone.id,
                    embedding_id=vector.id,
                    original_text_hash=clone.sha256,
                    original_chars=len(body),
                    prepared_chars=len(prepared.text),
                    cache_hit=False,
                )
            )
        session.commit()
    monkeypatch.setattr(
        "app.services.experiment_holdout.configuration", lambda model, prompt: {"fixture": True}
    )
    with get_session(database.database_url) as session:
        saved = registration(session, database, version_id, source_id)
        session.commit()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Holdout must never select thresholds")

    monkeypatch.setattr("app.services.experiments.select_thresholds", forbidden)
    with get_session(database.database_url) as session:
        series = create_holdout_series(session, database, version_id, source_id)
        series_id = series.id
        assert series.request["holdout_registration"] == saved
        assert all(r.test_started_at is None for r in runs(session, series_id))
        assert all(r.status == "frozen" or not r.config for r in runs(session, series_id))
        session.commit()
    execute_series(database, series_id)
    with get_session(database.database_url) as session:
        old = {r.variant: r for r in runs(session, source_id)}
        for run in runs(session, series_id):
            assert run.config == old[run.variant].config
            assert not run.reused_test
            assert not run.report["retuned"]
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ThresholdCurve)
                    .where(ThresholdCurve.run_id == run.id)
                )
                == 0
            )
        n = session.scalar(select(func.count()).select_from(ExperimentResult))
        assert create_holdout_series(session, database, version_id, source_id).id == series_id
    execute_series(database, series_id)
    with get_session(database.database_url) as session:
        assert session.scalar(select(func.count()).select_from(ExperimentResult)) == n
        with pytest.raises(ProcessingError, match="split=test"):
            registration(session, database, matrix[0], source_id)
