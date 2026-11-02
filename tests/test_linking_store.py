"""DB/worker infrastructure checks; scripted fields/vectors are not model metrics."""

import json
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_engine, get_session
from app.models import (
    Client,
    CorpusVersion,
    Document,
    DocumentEmbedding,
    DocumentText,
    Embedding,
    Extraction,
    Job,
    LinkingResult,
    LinkSuggestion,
    ModelConfig,
    StageTiming,
)
from app.services.embeddings import EncodingConfig, prepare_text
from app.services.extraction import save_result
from app.services.extraction_schema import ExtractedFields, check_evidence
from app.services.jobs import recover, work_once
from app.services.linking.types import LinkingConfig
from app.services.linking_store import enqueue_linking, link_document, linking_payload
from app.services.model_calls import ProcessingError

ENCODING = EncodingConfig()
CONFIG = LinkingConfig(tau_a=0.6, tau_r=0.3, use_semantic=False)


@pytest.fixture
def live(settings: Settings) -> Iterator[Settings]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Изолированная PostgreSQL: make verify-linking")
    settings.database_url = url

    def clean() -> None:
        with get_engine(url).begin() as connection:
            connection.execute(
                text("TRUNCATE clients, corpus_versions, embeddings, model_configs CASCADE")
            )

    clean()
    yield settings
    clean()


@pytest.fixture
def sample(live: Settings) -> tuple[UUID, UUID, UUID]:
    with get_session(live.database_url) as session:
        client = Client(name="Fixture")
        card_model = ModelConfig(
            name="Card Qwen fixture",
            provider="regex",
            model_uri="fixture-card",
            placement="local",
            active=True,
            default_card=True,
        )
        model = ModelConfig(
            name="Selected fixture",
            provider="regex",
            model_uri="fixture-selected",
            placement="local",
            active=True,
        )
        session.add_all([client, card_model, model])
        session.flush()
        ids = []
        for kind, number, day, body in [
            ("счёт", "S1", "02.02.2026", "Счёт № S1 от 02.02.2026\nПо договору № A1 от 01.02.2026"),
            ("договор", "A1", "01.02.2026", "Договор № A1 от 01.02.2026"),
        ]:
            doc = Document(
                client_id=client.id,
                source="upload",
                filename="SECRET_PATH",
                storage_path="SECRET_PATH",
                sha256="fixture",
                mime="text/plain",
                card_model_config_id=card_model.id,
                gold={"expected": "SECRET_EXPECTED", "hard_types": ["SECRET_CASE"]},
                deal_id="SECRET_DEAL",
                split="test",
            )
            session.add(doc)
            session.flush()
            session.add(
                DocumentText(document_id=doc.id, text=body, pages=1, chars=len(body), ocr_pages=0)
            )
            fields = ExtractedFields(
                doc_kind=kind, number=number, date=day, evidence={"number": body.split("\n")[0]}
            )
            save_result(
                session,
                doc.id,
                card_model.id,
                {"model": "card-fixture"},
                fields,
                None,
                check_evidence(fields, [body]),
                False,
                None,
            )
            # Selected model's kind/date deliberately differ: only the common card supplies them.
            selected = fields.model_copy(update={"doc_kind": "другое", "date": "01.01.2020"})
            if kind == "счёт":
                selected = ExtractedFields.model_validate(
                    {
                        **selected.model_dump(),
                        "contract_refs": [
                            {
                                "number": "A1",
                                "date": "01.02.2026",
                                "quote": "По договору № A1 от 01.02.2026",
                            }
                        ],
                    }
                )
            save_result(
                session,
                doc.id,
                model.id,
                {"model": "selected-fixture"},
                selected,
                None,
                check_evidence(selected, [body]),
                False,
                None,
            )
            ids.append(doc.id)
        session.commit()
        return ids[0], ids[1], model.id


def enqueue(
    live: Settings,
    source: UUID,
    model: UUID | None,
    config: LinkingConfig = CONFIG,
    *,
    retry: bool = False,
) -> UUID:
    with get_session(live.database_url) as session:
        id = enqueue_linking(session, source, model, config, ENCODING, retry=retry)
        session.commit()
        return id


def vector_fixture(session: Session, document_id: UUID, config: EncodingConfig = ENCODING) -> None:
    raw = session.get(DocumentText, document_id).text
    prepared = prepare_text(raw, config)
    vector = Embedding(
        input_hash=prepared.input_hash,
        text_hash=prepared.text_hash,
        model=config.model,
        revision=config.revision,
        snapshot=json.loads(prepared.snapshot),
        vector=[1.0] + [0.0] * 1023,
        tokens=10,
        chunks=1,
        truncated=False,
        duration_ms=1,
    )
    session.add(vector)
    session.flush()
    import hashlib

    session.add(
        DocumentEmbedding(
            document_id=document_id,
            embedding_id=vector.id,
            original_text_hash=hashlib.sha256(raw.encode()).hexdigest(),
            original_chars=len(raw),
            prepared_chars=len(prepared.text),
            cache_hit=False,
        )
    )


def test_common_card_selected_fields_no_gold_and_full_history(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    source, target, model = sample
    statements = []

    def sql(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(get_engine(live.database_url), "before_cursor_execute", sql)
    try:
        with get_session(live.database_url) as session:
            payload = linking_payload(session, source, model, CONFIG, ENCODING)
    finally:
        event.remove(get_engine(live.database_url), "before_cursor_execute", sql)
    assert "SECRET" not in json.dumps(payload)
    assert not any(
        f"documents.{name}" in " ".join(statements)
        for name in ("gold", "split", "deal_id", "filename", "storage_path")
    )
    assert payload["data"]["source"]["card"]["date"] == "2026-02-02"
    id = link_document(live, source, payload)
    assert link_document(live, source, payload) == id
    with get_session(live.database_url) as session:
        result = session.get(LinkingResult, id)
        assert result.outcome == "recommendation"
        assert result.recommendations == [str(target)]
        suggestion = session.scalar(select(LinkSuggestion))
        assert suggestion.rank == 1 and suggestion.decision == "recommendation"
        assert suggestion.features["num"] == 1 and suggestion.features["date"] == 1
        assert suggestion.source_evidence["references"][0]["verified"]
        assert session.scalar(select(func.count()).select_from(StageTiming)) == 2
        session.get(Document, source).date = date(2026, 2, 3)
        session.commit()
    new = enqueue(live, source, model)
    assert work_once(live, job_types=["link"])
    with get_session(live.database_url) as session:
        assert session.get(Job, new).status == "done"
        assert session.scalar(select(func.count()).select_from(LinkingResult)) == 2
        assert session.get(LinkingResult, id).input_snapshot == payload["data"]
        assert session.get(Document, source).status == "extracted"


@pytest.mark.parametrize(
    "change,outcome,reason",
    [
        ("empty", "none", "empty_pool"),
        ("unknown", "review", "unknown_source_kind_or_date"),
        ("selected_error", "error", "source_extraction_error"),
        ("card_error", "error", "source_card_error"),
        ("missing_selected", "error", "source_extraction_error"),
    ],
)
def test_terminal_outcomes_without_targets(
    live: Settings, sample: tuple[UUID, UUID, UUID], change: str, outcome: str, reason: str
) -> None:
    source, target, model = sample
    with get_session(live.database_url) as session:
        session.get(Document, target).date = date(2027, 1, 1)
        if change == "empty":
            current = session.scalar(
                select(Extraction).where(
                    Extraction.document_id == source,
                    Extraction.model_config_id == model,
                    Extraction.is_current,
                )
            )
            current.contract_refs = []
        if change == "unknown":
            session.get(Document, source).date = None
        if change in {"selected_error", "card_error", "missing_selected"}:
            model_id = (
                session.get(Document, source).card_model_config_id
                if change == "card_error"
                else model
            )
            current = session.scalar(
                select(Extraction).where(
                    Extraction.document_id == source,
                    Extraction.model_config_id == model_id,
                    Extraction.is_current,
                )
            )
            if change == "missing_selected":
                current.is_current = False
            else:
                current.valid, current.error = False, "fixture failed extraction"
        session.commit()
    id = enqueue(live, source, model)
    assert work_once(live, job_types=["link"])
    with get_session(live.database_url) as session:
        result = session.scalar(select(LinkingResult))
        assert result.outcome == outcome and reason in result.reasons
        assert session.get(Job, id).status == ("error" if outcome == "error" else "done")
        assert session.scalar(select(func.count()).select_from(LinkSuggestion)) == 0
        assert session.scalar(select(StageTiming)).ok == (outcome != "error")


def test_vector_provenance_exact_config_and_no_fallback(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    source, target, model = sample
    config = CONFIG.model_copy(update={"use_semantic": True})
    with get_session(live.database_url) as session:
        vector_fixture(session, source)
        # A vector exists, but for a different encoding: it must not be selected.
        vector_fixture(session, target, EncodingConfig(chunk_tokens=256))
        session.commit()
    id = enqueue(live, source, model, config)
    assert work_once(live, job_types=["link"])
    with get_session(live.database_url) as session:
        assert session.get(Job, id).status == "error"
        vector_fixture(session, target)
        session.commit()
    second = enqueue(live, source, model, config)
    assert second != id
    assert work_once(live, job_types=["link"])
    with get_session(live.database_url) as session:
        result = session.scalar(select(LinkingResult).where(LinkingResult.outcome != "error"))
        assert result.input_snapshot["similarities"][0]["cosine"] == pytest.approx(1)
        assert result.provenance["documents"][str(target)]["embedding"]["id"]
        assert session.scalar(select(func.count()).select_from(LinkingResult)) == 2


@pytest.mark.parametrize("method", ["rules", "semantic"])
def test_baselines_do_not_need_selected_extraction(
    live: Settings, sample: tuple[UUID, UUID, UUID], method: str
) -> None:
    source, target, model = sample
    config = CONFIG.model_copy(update={"method": method, "use_semantic": True})
    with get_session(live.database_url) as session:
        current = session.scalar(
            select(Extraction).where(
                Extraction.document_id == source, Extraction.model_config_id == model
            )
        )
        current.valid = False
        if method == "semantic":
            vector_fixture(session, source)
            vector_fixture(session, target)
        session.commit()
    id = enqueue(live, source, None, config)
    assert work_once(live, job_types=["link"])
    with get_session(live.database_url) as session:
        assert session.get(Job, id).status == "done"
        assert session.scalar(select(LinkingResult)).outcome == "recommendation"


def test_pool_is_same_client_version_date_all_splits_not_top_three(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    source, target, model = sample
    with get_session(live.database_url) as session:
        client = session.get(Document, source).client_id
        version = CorpusVersion(
            name="fixture", fingerprint="fixture", seed=1, tune_share=0.3, report={}
        )
        other = Client(name="other")
        session.add_all([version, other])
        session.flush()
        # Six admissible targets, plus excluded future/foreign/unknown/non-contract targets.
        for i in range(11):
            doc = Document(
                client_id=other.id if i == 7 else client,
                corpus_version_id=version.id if i == 8 else None,
                source="upload",
                filename="SECRET",
                storage_path="SECRET",
                sha256="fixture",
                mime="text/plain",
                doc_kind="счёт" if i == 9 else "договор",
                date=None if i == 10 else date(2027 if i == 6 else 2026, 1, 1),
                split="tune" if i % 2 else "test",
            )
            session.add(doc)
            session.flush()
            body = "Договор № B1 от 01.01.2026"
            session.add(
                DocumentText(
                    document_id=doc.id,
                    text=body,
                    pages=1,
                    chars=len(body),
                    ocr_pages=0,
                )
            )
        session.flush()
        payload = linking_payload(
            session, source, None, CONFIG.model_copy(update={"method": "rules"}), ENCODING
        )
        assert len(payload["data"]["candidates"]) == 7
        assert str(target) in {d["card"]["document_id"] for d in payload["data"]["candidates"]}
        session.commit()
    result_id = link_document(live, source, payload)
    with get_session(live.database_url) as session:
        suggestions = list(
            session.scalars(
                select(LinkSuggestion)
                .where(
                    LinkSuggestion.result_id == result_id,
                )
                .order_by(LinkSuggestion.rank)
            )
        )
        assert len(suggestions) == 7
        assert [row.rank for row in suggestions] == list(range(1, 8))
        assert suggestions[0].target_id == target


def test_new_config_and_extraction_create_new_results(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    source, target, model = sample
    first = enqueue(live, source, model)
    assert work_once(live, job_types=["link"])
    changed = CONFIG.model_copy(update={"tau_a": 0.9})
    second = enqueue(live, source, model, changed)
    assert second != first
    assert work_once(live, job_types=["link"])
    with get_session(live.database_url) as session:
        fields = ExtractedFields(doc_kind="счёт", date="02.02.2026", number="S2")
        save_result(
            session, source, model, {"model": "selected-fixture-v2"}, fields, None, [], False, None
        )
        session.commit()
    third = enqueue(live, source, model, changed)
    assert third not in {first, second}
    assert work_once(live, job_types=["link"])
    with get_session(live.database_url) as session:
        rows = list(session.scalars(select(LinkingResult).order_by(LinkingResult.created_at)))
        assert len(rows) == 3
        assert len({row.input_hash for row in rows}) == 3
        assert rows[0].outcome == "recommendation" and rows[1].outcome == "review"
        assert rows[0].input_snapshot["source"]["fields"]["number"] == "S1"
        assert rows[2].input_snapshot["source"]["fields"]["number"] == "S2"


def test_concurrent_queue_and_results_idempotent(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    source, target, model = sample
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: enqueue(live, source, model), range(2)))
    assert ids[0] == ids[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: work_once(live, job_types=["link"]), range(2)))
    assert sum(results) == 1
    enqueue(live, source, model, retry=True)
    assert work_once(live, job_types=["link"])
    with get_session(live.database_url) as session:
        payload = session.get(Job, ids[0]).payload
    with ThreadPoolExecutor(max_workers=2) as pool:
        same = list(pool.map(lambda _: link_document(live, source, payload), range(2)))
    assert same[0] == same[1]
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(LinkingResult)) == 1
        assert session.scalar(select(func.count()).select_from(LinkSuggestion)) == 1
        assert session.scalar(select(func.count()).select_from(StageTiming)) == 4


@pytest.mark.parametrize("committed", [False, True])
def test_worker_recovery_with_or_without_committed_result(
    live: Settings, sample: tuple[UUID, UUID, UUID], committed: bool
) -> None:
    source, target, model = sample
    id = enqueue(live, source, model)
    with get_session(live.database_url) as session:
        job = session.get(Job, id)
        job.status, job.locked_at = "running", datetime.now(UTC)
        payload = job.payload
        session.commit()
    if committed:
        link_document(live, source, payload, job_id=id)
    with (
        get_engine(live.database_url).connect() as guard,
        get_session(live.database_url) as session,
    ):
        recover(session, guard)
        assert session.get(Job, id).status == "queued"
    assert work_once(live, job_types=["link"])
    with get_session(live.database_url) as session:
        assert session.get(Job, id).status == "done"
        assert session.scalar(select(func.count()).select_from(LinkingResult)) == 1


def test_stale_job_errors_explicit_retry_preserves_history(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    source, target, model = sample
    id = enqueue(live, source, model)
    with get_session(live.database_url) as session:
        session.get(Document, target).date = date(2027, 1, 1)
        session.commit()
    with pytest.raises(ProcessingError, match="другим входом"):
        enqueue(live, source, model)
    assert work_once(live, job_types=["link"])
    with get_session(live.database_url) as session:
        assert session.get(Job, id).status == "error"
        assert "изменился" in session.scalar(select(LinkingResult)).error
    second = enqueue(live, source, model)
    assert second != id
    assert work_once(live, job_types=["link"])
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(LinkingResult)) == 2


def test_error_not_reused_as_success_and_recovery_keeps_error(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    source, target, model = sample
    config = CONFIG.model_copy(update={"use_semantic": True})
    id = enqueue(live, source, model, config)
    assert work_once(live, job_types=["link"])
    assert enqueue(live, source, model, config) == id
    retry = enqueue(live, source, model, config, retry=True)
    assert retry != id
    assert work_once(live, job_types=["link"])
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(LinkingResult)) == 2
        job = session.get(Job, retry)
        job.status, job.locked_at = "running", datetime.now(UTC)
        session.commit()
    assert work_once(live, job_types=["link"])
    with get_session(live.database_url) as session:
        assert session.get(Job, retry).status == "error"
        assert session.scalar(select(func.count()).select_from(LinkingResult)) == 2


def test_candidate_write_failure_rolls_back_entire_ranking(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    source, target, model = sample

    def fail(session: Session, *args: Any) -> None:
        if any(isinstance(row, LinkSuggestion) for row in session.new):
            raise RuntimeError("fixture ranking write failure")

    id = enqueue(live, source, model)
    event.listen(Session, "before_flush", fail)
    try:
        assert work_once(live, job_types=["link"])
    finally:
        event.remove(Session, "before_flush", fail)
    with get_session(live.database_url) as session:
        assert session.get(Job, id).status == "error"
        assert session.scalar(select(func.count()).select_from(LinkSuggestion)) == 0
        results = list(session.scalars(select(LinkingResult)))
        assert len(results) == 1 and results[0].outcome == "error"
        assert not session.scalar(select(StageTiming)).ok
