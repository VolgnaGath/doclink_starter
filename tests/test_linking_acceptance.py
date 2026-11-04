"""Infrastructure fixtures only: scripted embeddings are never model quality evidence."""

import hashlib
import json
import os
from collections.abc import Iterator
from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import func, select, text

from app.config import Settings
from app.db import get_engine, get_session
from app.linking_acceptance import freeze, run
from app.models import (
    Client,
    CorpusVersion,
    DocumentEmbedding,
    DocumentText,
    Embedding,
    ModelConfig,
)
from app.models import (
    Document as StoredDocument,
)
from app.models import (
    LinkingResult as StoredResult,
)
from app.services.embeddings import EncodingConfig, prepare_text
from app.services.extraction import save_result
from app.services.extraction_schema import ExtractedFields, check_evidence
from app.services.linking.types import Card, Document, LinkingConfig, LinkingInput, Similarity
from app.services.linking_acceptance import TuneDocument, select_thresholds
from app.services.model_calls import ProcessingError


def tuning_doc(score: float, correct: bool = True, split: str = "tune") -> TuneDocument:
    target = Document(
        card=Card(
            document_id="target",
            client_id="client",
            corpus_version_id="version",
            kind="договор",
            date=date(2025, 1, 1),
        )
    )
    source = Document(
        card=Card(
            document_id="source",
            client_id="client",
            corpus_version_id="version",
            kind="акт",
            date=date(2025, 2, 1),
        )
    )
    return TuneDocument(
        LinkingInput(
            source=source,
            candidates=(target,),
            similarities=(Similarity(target_id="target", cosine=score),),
        ),
        frozenset({"target"} if correct else {"different"}),
        split,
    )


def test_threshold_selects_max_coverage_under_precision_constraint_not_best_test() -> None:
    result = select_thresholds(
        (tuning_doc(0.9), tuning_doc(0.6, False)),
        LinkingConfig(method="semantic", tau_a=0, tau_r=0),
    )
    assert result["status"] == "FEASIBLE"
    assert result["config"]["tau_a"] == 0.9
    assert result["selected_tune_metrics"]["precision"] == 1
    assert result["selected_tune_metrics"]["coverage"] == 0.5
    assert result["selected_tune_metrics"]["m"] == 1


def test_no_feasible_threshold_does_not_invent_configuration() -> None:
    result = select_thresholds(
        (tuning_doc(0.9, False),), LinkingConfig(method="semantic", tau_a=0, tau_r=0)
    )
    assert result["status"] == "NO_FEASIBLE_CONFIGURATION" and result["config"] is None


def test_threshold_selection_rejects_test_rows() -> None:
    with pytest.raises(ValueError, match="только"):
        select_thresholds((tuning_doc(0.9, split="test"),), LinkingConfig(tau_a=0, tau_r=0))


@pytest.fixture
def database(settings: Settings) -> Iterator[Settings]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Изолированная PostgreSQL: make verify-linking-acceptance")
    settings.database_url = url

    def clean() -> None:
        with get_engine(url).begin() as conn:
            conn.execute(
                text("TRUNCATE clients, corpus_versions, embeddings, model_configs CASCADE")
            )

    clean()
    yield settings
    clean()


@pytest.fixture
def sample(database: Settings) -> tuple[UUID, UUID]:
    with get_session(database.database_url) as session:
        client = Client(name="Fixture")
        model = ModelConfig(
            name="Fixture",
            provider="regex",
            model_uri="fixture",
            placement="local",
            active=True,
            default_card=True,
        )
        version = CorpusVersion(
            name="fixture",
            fingerprint="fixture",
            seed=2026,
            tune_share=0.3,
            report={"extraction_protocols": {"prior-test": {"test_started": True}}},
        )
        session.add_all([client, model, version])
        session.flush()
        source_id = None
        for index, kind, split in [(0, "договор", "test"), (1, "акт", "tune"), (2, "акт", "test")]:
            body = f"{kind} № {index} от 02.02.2026" + (
                "\nК договору № 0 от 01.02.2026" if index else ""
            )
            doc = StoredDocument(
                client_id=client.id,
                corpus_version_id=version.id,
                corpus_doc_id=f"doc-{index}",
                source="corpus",
                filename="SECRET_PATH",
                storage_path="SECRET_PATH",
                sha256=str(index),
                mime="text/plain",
                in_scope=True,
                split=split,
                card_model_config_id=model.id,
                gold={
                    "fields": {"kind": kind},
                    "links": [{"target": "doc-0"}] if index else [],
                    "case": "conflict" if index == 2 else None,
                    "expected": "разбор",
                },
            )
            session.add(doc)
            session.flush()
            session.add(
                DocumentText(document_id=doc.id, text=body, pages=1, chars=len(body), ocr_pages=0)
            )
            fields = ExtractedFields.model_validate(
                {
                    "doc_kind": kind,
                    "number": str(index),
                    "date": "02.02.2026" if index else "01.02.2026",
                    "evidence": {"number": body.split("\n")[0]},
                    "contract_refs": [
                        {
                            "number": "0",
                            "date": "01.02.2026",
                            "quote": "К договору № 0 от 01.02.2026",
                        }
                    ]
                    if index
                    else [],
                }
            )
            save_result(
                session,
                doc.id,
                model.id,
                {"fixture": True},
                fields,
                None,
                check_evidence(fields, [body]),
                False,
                None,
            )
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
                    document_id=doc.id,
                    embedding_id=vector.id,
                    original_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                    original_chars=len(body),
                    prepared_chars=len(prepared.text),
                    cache_hit=False,
                )
            )
            if index == 2:
                source_id = doc.id
        session.commit()
        return version.id, source_id


def test_freeze_precedes_test_and_replay_reuses_durable_results(
    database: Settings, sample: tuple[UUID, UUID]
) -> None:
    version_id, _ = sample
    frozen = freeze(database, version_id, None, "combined")
    key = frozen["protocol_hash"]
    assert not frozen["test_started"] and frozen["reused_test"]
    assert "SECRET_PATH" not in json.dumps(frozen)
    report = run(database, version_id, key)
    assert report["metrics_by_split"]["test"]["n"] == 1
    assert report["new_results"] == 2 and report["new_physical_llm_calls"] == 0
    assert report["control_total"] == 1 and report["control_passed"] == 0
    assert (
        report["controls"][0]["actual"] == "recommendation"
    )  # Gold labels cannot block the engine.
    assert report["protocol"]["test_started"]
    repeated = run(database, version_id, key)
    assert repeated["new_results"] == 0 and repeated["new_physical_llm_calls"] == 0
    assert repeated["result_ids"] == report["result_ids"]
    assert repeated["metrics_by_split"] == report["metrics_by_split"]


@pytest.mark.parametrize("change", ["card", "split", "code"])
def test_frozen_input_rejects_changes_before_queue(
    database: Settings, sample: tuple[UUID, UUID], monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    version_id, source_id = sample
    frozen = freeze(database, version_id, None, "combined")
    if change == "code":
        monkeypatch.setattr("app.linking_acceptance.code_hash", lambda: "changed")
    else:
        with get_session(database.database_url) as session:
            doc = session.get(StoredDocument, source_id)
            if change == "card":
                doc.date = date(2020, 1, 1)
            else:
                doc.split = "tune"
            session.commit()
    with pytest.raises(ProcessingError, match="freeze"):
        run(database, version_id, frozen["protocol_hash"])
    with get_session(database.database_url) as session:
        assert session.scalar(select(func.count()).select_from(StoredResult)) == 0
