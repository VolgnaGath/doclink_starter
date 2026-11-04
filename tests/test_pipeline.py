"""Real PostgreSQL/worker plumbing. Regex and fixture vectors are not model metrics."""

from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_embedding_store import ScriptedEncoder
from test_embedding_store import encoder as encoder
from test_linking_store import live as live

from app.config import Settings
from app.db import get_session
from app.models import (
    Client,
    Document,
    Extraction,
    Job,
    LinkingResult,
    ModelConfig,
    OperationalConfig,
    ProcessingPipeline,
    Prompt,
)
from app.services.embeddings import EncodingConfig
from app.services.jobs import work_once
from app.services.linking.types import LinkingConfig
from app.services.model_calls import ProcessingError
from app.services.pipeline import install_configuration, reconcile, retry_pipeline
from app.services.registration import IncomingFile, register_batch


@pytest.fixture
def operational(live: Settings, tmp_path: Path) -> tuple[UUID, UUID]:
    live.files_dir = str(tmp_path / "files")
    with get_session(live.database_url) as session:
        client = Client(name="Рабочий клиент")
        model = ModelConfig(
            name="Technical regex", provider="regex", model_uri="regex", placement="local"
        )
        prompt = session.scalar(
            select(Prompt).where(Prompt.name == "extract", Prompt.version == "test")
        )
        prompt = prompt or Prompt(name="extract", version="test", text="{text}", schema={})
        session.add_all([client, model, prompt])
        session.flush()
        install_configuration(
            session,
            model,
            prompt,
            LinkingConfig(tau_a=0.2, tau_r=0.1, use_semantic=False),
            EncodingConfig(),
        )
        session.commit()
        return client.id, model.id


def upload(live: Settings, client: UUID, entries: list[tuple[str, str]]) -> list[UUID]:
    with get_session(live.database_url) as session:
        results = register_batch(
            session,
            live,
            client,
            [IncomingFile(name, BytesIO(body.encode())) for name, body in entries],
        )
        session.commit()
        return [r.document_id for r in results if r.document_id]


def drain(live: Settings) -> None:
    idle = 0
    for _ in range(100):
        idle = 0 if work_once(live) else idle + 1
        if idle == 3:
            return
    pytest.fail("Конвейер не завершился")


@pytest.mark.parametrize("reverse", [False, True])
def test_batch_automatic_barrier_and_no_repeated_success(
    live: Settings, operational: tuple[UUID, UUID], reverse: bool
) -> None:
    client, _ = operational
    entries = [
        ("incoming.txt", "Счёт № S1 от 02.02.2026\nПо договору № A1 от 01.02.2026"),
        ("contract.txt", "Договор № A1 от 01.02.2026"),
    ]
    ids = upload(live, client, entries[::-1] if reverse else entries)
    drain(live)
    with get_session(live.database_url) as session:
        result = session.scalar(select(LinkingResult))
        assert result and result.outcome != "error"
        assert len(result.input_snapshot["candidates"]) == 1
        assert all(session.get(ProcessingPipeline, id).stage == "done" for id in ids)
        jobs = session.scalar(select(func.count()).select_from(Job))
        extractions = session.scalar(select(func.count()).select_from(Extraction))
        frozen = session.get(ProcessingPipeline, ids[0]).snapshot
        session.get(OperationalConfig, 1).snapshot = {**frozen, "origin_protocol_hash": "changed"}
        session.commit()
    drain(live)
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == jobs
        assert session.scalar(select(func.count()).select_from(Extraction)) == extractions == 2
        assert session.get(ProcessingPipeline, ids[0]).snapshot == frozen


def test_partial_admission_blob_identity_and_common_validation(
    live: Settings, operational: tuple[UUID, UUID]
) -> None:
    client, _ = operational
    with get_session(live.database_url) as session:
        files = [
            IncomingFile("a.txt", BytesIO(b"same")),
            IncomingFile("bad.exe", BytesIO(b"bad")),
            IncomingFile("b.txt", BytesIO(b"same")),
        ]
        results = register_batch(session, live, client, files, is_anonymized=True)
        assert [bool(r.document_id) for r in results] == [True, False, True]
        docs = list(session.scalars(select(Document)))
        assert len(docs) == 2 and docs[0].id != docs[1].id
        assert docs[0].storage_path == docs[1].storage_path
        assert all(d.is_anonymized and d.gold is None for d in docs)
        with pytest.raises(ProcessingError, match="клиента"):
            register_batch(session, live, uuid4(), [IncomingFile("unused.txt", BytesIO(b"unused"))])
        assert session.scalar(select(func.count()).select_from(Document)) == 2


@pytest.mark.parametrize("body", ["Неизвестный документ", "Счёт № S1 без даты"])
def test_unknown_source_is_review_even_with_failed_possible_contract(
    live: Settings, operational: tuple[UUID, UUID], body: str
) -> None:
    client, _ = operational
    source, _ = upload(live, client, [("incoming.txt", body), ("broken.pdf", "%PDF-broken")])
    drain(live)
    with get_session(live.database_url) as session:
        result = session.scalar(select(LinkingResult).where(LinkingResult.source_id == source))
        assert result.outcome == "review"
        assert result.reasons == ["unknown_source_kind_or_date"]
        assert session.scalar(select(Extraction).where(Extraction.document_id == source)).valid
        assert session.get(Document, source).date is None


def test_failed_contract_blocks_pool_and_retry_resumes_only_dependencies(
    live: Settings, operational: tuple[UUID, UUID]
) -> None:
    client, _ = operational
    source, target = upload(
        live, client, [("incoming.txt", "Счёт № S1 от 02.02.2026"), ("broken.txt", "")]
    )
    drain(live)
    with get_session(live.database_url) as session:
        result = session.scalar(select(LinkingResult).where(LinkingResult.source_id == source))
        assert result.outcome == "error"
        assert "не подготовлен" in result.input_snapshot["semantic_error"]
        source_extraction = session.scalar(
            select(Extraction.id).where(Extraction.document_id == source)
        )
        # Repair the failed text stage as an operator would repair its source.
        doc = session.get(Document, target)
        doc.storage_path = str(Path(live.files_dir) / "repaired.txt")
        Path(doc.storage_path).write_text("Договор № A1 от 01.02.2026")
        retry_pipeline(session, target)
        session.commit()
    drain(live)
    with get_session(live.database_url) as session:
        results = list(
            session.scalars(
                select(LinkingResult)
                .where(LinkingResult.source_id == source)
                .order_by(LinkingResult.created_at)
            )
        )
        assert results[-1].outcome != "error"
        assert (
            session.scalar(select(Extraction.id).where(Extraction.document_id == source))
            == source_extraction
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Extraction).where(Extraction.document_id == source)
            )
            == 1
        )


def test_unknown_potential_contract_and_independent_client(
    live: Settings, operational: tuple[UUID, UUID]
) -> None:
    client, _ = operational
    source, _ = upload(
        live, client, [("in.txt", "Акт № 1 от 02.02.2026"), ("unknown.txt", "Неизвестно")]
    )
    with get_session(live.database_url) as session:
        other = Client(name="Независимый")
        session.add(other)
        session.commit()
        other_id = other.id
    independent = upload(live, other_id, [("in.txt", "Акт № 2 от 02.02.2026")])[0]
    drain(live)
    with get_session(live.database_url) as session:
        outcomes = {r.source_id: r.outcome for r in session.scalars(select(LinkingResult))}
        assert outcomes[source] == "error" and outcomes[independent] == "none"


def test_preserved_error_retry_guard(live: Settings, operational: tuple[UUID, UUID]) -> None:
    client, _ = operational
    source = upload(live, client, [("in.txt", "Счёт № 1 от 02.02.2026")])[0]
    drain(live)
    with get_session(live.database_url) as session:
        session.scalar(select(Extraction)).preserved_error = True
        retry_pipeline(session, source, mode="extract")
        reconcile(session)
        assert "защищена" in session.get(ProcessingPipeline, source).error
        assert session.scalar(select(func.count()).select_from(Extraction)) == 1


def test_vector_failure_retry_and_successful_stages_retained(
    live: Settings, operational: tuple[UUID, UUID], encoder: ScriptedEncoder
) -> None:
    client, _ = operational
    with get_session(live.database_url) as session:
        row = session.get(OperationalConfig, 1)
        row.snapshot = {**row.snapshot, "config": {**row.snapshot["config"], "use_semantic": True}}
        session.commit()
    source, target = upload(
        live,
        client,
        [("in.txt", "Счёт № S1 от 02.02.2026"), ("contract.txt", "Договор № A1 от 01.02.2026")],
    )
    encoder.fail = True
    drain(live)
    attempts = len(encoder.inputs)
    assert attempts == 2
    drain(live)
    assert len(encoder.inputs) == attempts
    with get_session(live.database_url) as session:
        result = session.scalar(select(LinkingResult).where(LinkingResult.source_id == source))
        assert result.outcome == "error" and "вектор" in result.error
        retry_pipeline(session, source)
        retry_pipeline(session, target)
        session.commit()
    encoder.fail = False
    drain(live)
    assert len(encoder.inputs) == attempts + 2
    with get_session(live.database_url) as session:
        pipe = session.get(ProcessingPipeline, source)
        result = session.get(LinkingResult, pipe.result_id)
        assert result.outcome != "error"
        assert len(result.provenance["documents"]) == 2
        assert all("embedding" in p for p in result.provenance["documents"].values())
        assert session.scalar(select(func.count()).select_from(Extraction)) == 2
    drain(live)
    assert len(encoder.inputs) == attempts + 2


@pytest.mark.parametrize("body", ["Счёт № S1 без даты", "Неизвестный документ от 02.02.2026"])
def test_missing_requisites_corrected_then_search_and_no_reextraction(
    live: Settings, operational: tuple[UUID, UUID], body: str
) -> None:
    from app.services.card_edits import CardPatch, edit_card
    from app.services.review import open_review

    client, _ = operational
    source, _ = upload(
        live, client, [("in.txt", body), ("contract.txt", "Договор № A1 от 01.02.2026")]
    )
    drain(live)
    with get_session(live.database_url) as session:
        assert (
            session.get(LinkingResult, session.get(ProcessingPipeline, source).result_id).outcome
            == "review"
        )
        review = open_review(session, source)
        edit_card(
            session,
            source,
            CardPatch(
                version=0,
                session_id=review.id,
                idempotency_key=uuid4(),
                values={"date": "02.02.2026", "doc_kind": "счёт"},
            ),
        )
        session.commit()
    drain(live)
    with get_session(live.database_url) as session:
        result = session.get(LinkingResult, session.get(ProcessingPipeline, source).result_id)
        assert len(result.input_snapshot["candidates"]) == 1
        assert result.reasons != ["unknown_source_kind_or_date"]
        assert session.scalar(select(func.count()).select_from(Extraction)) == 2


def test_recovery_between_extraction_commit_and_pipeline_transition(
    live: Settings, operational: tuple[UUID, UUID]
) -> None:
    from datetime import UTC, datetime

    client, _ = operational
    source = upload(live, client, [("in.txt", "Акт № 1 от 02.02.2026")])[0]
    assert work_once(live)  # text
    assert work_once(live)  # extraction persisted
    with get_session(live.database_url) as session:
        pipe = session.get(ProcessingPipeline, source)
        job = session.get(Job, pipe.job_id)
        job.status, job.locked_at = "running", datetime.now(UTC)
        # The committed extraction must be newer than the original claim timestamp.
        job.locked_at = session.scalar(select(Extraction.created_at))
        session.commit()
    drain(live)
    with get_session(live.database_url) as session:
        pipe = session.get(ProcessingPipeline, source)
        assert pipe.stage == "done" and pipe.result_id
        assert session.scalar(select(func.count()).select_from(Extraction)) == 1
