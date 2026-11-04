"""Real DB isolation/provenance checks; scripted inputs are not model measurements."""

import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from test_linking_store import live as live
from test_linking_store import vector_fixture
from test_pipeline import drain
from test_pipeline import operational as operational

from app.config import Settings
from app.db import get_engine, get_session
from app.models import (
    Client,
    CorpusVersion,
    Document,
    DocumentEmbedding,
    DocumentText,
    Extraction,
    Job,
    Link,
    LinkingResult,
    LLMCall,
    OperationalConfig,
    ProcessingPipeline,
    WorkingCopy,
    WorkingSet,
)
from app.services.card_edits import CardPatch, edit_card
from app.services.embeddings import EncodingConfig
from app.services.extraction import extract_document
from app.services.linking.types import LinkingConfig
from app.services.linking_store import link_document, linking_payload
from app.services.review import ReviewError, open_review
from app.services.working_identity import cache_client_id, copy_origin
from app.services.working_sets import WorkingSetCreate, create_working_set


@pytest.fixture
def corpus_sample(live: Settings, operational: tuple[UUID, UUID]) -> tuple[UUID, list[UUID], UUID]:
    client, model = operational
    with get_session(live.database_url) as session:
        session.get(Client, client).namespace = "corpus:fixture"
        version = CorpusVersion(
            name="Corpus fixture", fingerprint=str(uuid4()), seed=2026, tune_share=0.3, report={}
        )
        session.add(version)
        session.flush()
        ids = []
        for body in [
            "Счёт № S1 от 02.02.2026\nПо договору № A1 от 01.02.2026",
            "Договор № A1 от 01.02.2026",
            "Договор № FUTURE от 01.01.2027",
        ]:
            doc = Document(
                client_id=client,
                corpus_version_id=version.id,
                source="corpus",
                filename="SECRET_FOLDER/input.txt",
                storage_path="SECRET_STORAGE_PATH",
                sha256=hashlib.sha256(body.encode()).hexdigest(),
                mime="text/plain",
                is_anonymized=True,
                card_model_config_id=model,
                gold={"SECRET_GOLD": True, "links": ["deliberately-not-the-pool"]},
                split="test",
                deal_id="SECRET_DEAL",
                corpus_doc_id=str(uuid4()),
            )
            session.add(doc)
            session.flush()
            session.add(
                DocumentText(document_id=doc.id, text=body, pages=1, chars=len(body), ocr_pages=0)
            )
            ids.append(doc.id)
        snapshot = copy.deepcopy(session.get(OperationalConfig, 1).snapshot)
        session.commit()
        version_id = version.id
    for id in ids:
        extract_document(live, id, model, UUID(snapshot["prompt_id"]), snapshot["extraction"])
    with get_session(live.database_url) as session:
        vector_fixture(session, ids[0])
        vector_fixture(session, ids[1])
        payload = linking_payload(
            session,
            ids[0],
            model,
            LinkingConfig.model_validate(snapshot["config"]),
            EncodingConfig(),
        )
        session.commit()
    result = link_document(live, ids[0], payload)
    return version_id, ids, result


def create(live: Settings, request: WorkingSetCreate) -> UUID:
    with get_session(live.database_url) as session:
        row = create_working_set(session, request)
        session.commit()
        return row.id


def test_isolated_copy_all_contracts_exact_reuse_and_fresh_search(
    live: Settings, corpus_sample: tuple[UUID, list[UUID], UUID]
) -> None:
    version, ids, result = corpus_sample
    request = WorkingSetCreate(
        name="Working",
        corpus_version_id=version,
        document_ids=[ids[0]],
        result_ids=[result],
        idempotency_key=uuid4(),
    )
    work_id = create(live, request)
    assert create(live, request) == work_id
    drain(live)
    with get_session(live.database_url) as session:
        copies = list(
            session.scalars(select(WorkingCopy).where(WorkingCopy.working_set_id == work_id))
        )
        assert {c.source_document_id for c in copies} == set(
            ids
        )  # includes future contract, no gold selection
        source = next(c for c in copies if c.source_document_id == ids[0])
        target_ids = {c.document_id for c in copies if c.source_document_id != ids[0]}
        for c in copies:
            doc = session.get(Document, c.document_id)
            original = session.get(Document, c.source_document_id)
            assert doc.client_id != original.client_id and doc.corpus_version_id is None
            assert (
                doc.gold is None and doc.deal_id is None and doc.split is None and not doc.in_scope
            )
            assert doc.corpus_doc_id is None and "SECRET" not in doc.filename
            ext = session.scalar(select(Extraction).where(Extraction.document_id == doc.id))
            old = session.get(Extraction, ext.source_extraction_id)
            assert ext.cache_hit and ext.input_hash == old.input_hash
            assert cache_client_id(session, doc) == original.client_id
            assert session.get(Document, original.id).gold["SECRET_GOLD"]
        pipe = session.get(ProcessingPipeline, source.document_id)
        new = session.get(LinkingResult, pipe.result_id)
        assert new.id != result and new.source_id == source.document_id
        assert {
            UUID(c["card"]["document_id"]) for c in new.input_snapshot["candidates"]
        } <= target_ids
        assert len(new.input_snapshot["candidates"]) == 1
        assert new.provenance["working_copy"]["source_result_id"] == str(result)
        assert new.provenance["working_copy"]["initial_inputs"]
        assert session.scalar(select(func.count()).select_from(LLMCall)) == 0
        assert session.scalar(select(func.count()).select_from(Link)) == 0
        assert session.scalar(select(func.count()).select_from(Extraction)) == 6
        assert session.scalar(select(func.count()).select_from(DocumentEmbedding)) == 4
        assert (
            session.scalar(select(func.count()).select_from(Job).where(Job.type == "extract")) == 0
        )
        assert "SECRET" not in str(new.input_snapshot) and "SECRET" not in str(new.provenance)
        copied_source, model_id = source.document_id, new.model_config_id
        stored_config = LinkingConfig.model_validate(new.config)
    statements = []

    def sql(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    event.listen(get_engine(live.database_url), "before_cursor_execute", sql)
    try:
        with get_session(live.database_url) as session:
            linking_payload(session, copied_source, model_id, stored_config, EncodingConfig())
    finally:
        event.remove(get_engine(live.database_url), "before_cursor_execute", sql)
    assert not any(
        f"documents.{name}" in " ".join(statements)
        for name in ("gold", "split", "deal_id", "filename", "storage_path")
    )


def test_manual_changes_distinguish_timing_origin_and_preserve_original(
    live: Settings, corpus_sample: tuple[UUID, list[UUID], UUID]
) -> None:
    version, ids, result = corpus_sample
    work = create(
        live,
        WorkingSetCreate(
            name="Edit", corpus_version_id=version, document_ids=[ids[0]], idempotency_key=uuid4()
        ),
    )
    drain(live)
    with get_session(live.database_url) as session:
        source = session.scalar(
            select(WorkingCopy).where(
                WorkingCopy.working_set_id == work, WorkingCopy.source_document_id == ids[0]
            )
        )
        id = source.document_id
        doc = session.get(Document, id)
        review = open_review(session, id)
        assert review.context["provenance"]["working_copy"]["initial_inputs"]
        edit_card(
            session,
            id,
            CardPatch(
                version=0,
                session_id=review.id,
                idempotency_key=uuid4(),
                values={"number": "MANUAL"},
            ),
        )
        assert not copy_origin(session, doc.id)["initial_inputs"]
        session.commit()
    drain(live)
    with get_session(live.database_url) as session:
        new = session.get(LinkingResult, session.get(ProcessingPipeline, id).result_id)
        assert not new.provenance["working_copy"]["initial_inputs"]
        assert new.provenance["working_copy"]["source_result_id"] == str(result)
        assert session.get(Document, ids[0]).number == "S1"
        assert (
            session.get(LinkingResult, result).input_snapshot["source"]["fields"]["number"] == "S1"
        )
        # Resetting a value does not erase the post-edit provenance of the session.
        review = open_review(session, id)
        edit_card(
            session,
            id,
            CardPatch(version=1, session_id=review.id, idempotency_key=uuid4(), reset=["number"]),
        )
        session.commit()
    drain(live)
    with get_session(live.database_url) as session:
        new = session.get(LinkingResult, session.get(ProcessingPipeline, id).result_id)
        assert new.input_snapshot["source"]["fields"]["number"] == "S1"
        assert not new.provenance["working_copy"]["initial_inputs"]


def test_cache_scope_breaks_on_changed_text_or_client_context(
    live: Settings, corpus_sample: tuple[UUID, list[UUID], UUID]
) -> None:
    version, ids, _ = corpus_sample
    work = create(
        live,
        WorkingSetCreate(
            name="Scope", corpus_version_id=version, document_ids=[ids[0]], idempotency_key=uuid4()
        ),
    )
    with get_session(live.database_url) as session:
        copy = session.scalar(
            select(WorkingCopy).where(
                WorkingCopy.working_set_id == work, WorkingCopy.source_document_id == ids[0]
            )
        )
        doc = session.get(Document, copy.document_id)
        original_scope = cache_client_id(session, doc)
        assert original_scope != doc.client_id
        body = session.get(DocumentText, doc.id)
        body.text += " changed"
        session.flush()
        assert cache_client_id(session, doc) == doc.client_id
        body.text = session.get(DocumentText, ids[0]).text
        session.get(Client, doc.client_id).name += " changed"
        session.flush()
        assert cache_client_id(session, doc) == doc.client_id


def test_mismatched_extraction_key_is_not_reused(
    live: Settings, corpus_sample: tuple[UUID, list[UUID], UUID]
) -> None:
    version, ids, _ = corpus_sample
    with get_session(live.database_url) as session:
        session.scalar(
            select(Extraction).where(Extraction.document_id == ids[0])
        ).input_hash = "wrong"
        session.commit()
    work = create(
        live,
        WorkingSetCreate(
            name="Mismatch",
            corpus_version_id=version,
            document_ids=[ids[0]],
            idempotency_key=uuid4(),
        ),
    )
    with get_session(live.database_url) as session:
        target = session.scalar(
            select(WorkingCopy).where(
                WorkingCopy.working_set_id == work, WorkingCopy.source_document_id == ids[0]
            )
        )
        assert (
            session.scalar(select(Extraction).where(Extraction.document_id == target.document_id))
            is None
        )
        assert session.get(ProcessingPipeline, target.document_id).stage == "extract"


def test_concurrent_creation_and_idempotency_conflict(
    live: Settings, corpus_sample: tuple[UUID, list[UUID], UUID]
) -> None:
    version, ids, _ = corpus_sample
    request = WorkingSetCreate(
        name="Concurrent", corpus_version_id=version, document_ids=[ids[0]], idempotency_key=uuid4()
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        works = list(pool.map(lambda _: create(live, request), range(2)))
    assert works[0] == works[1]
    with pytest.raises(ReviewError, match="Ключ"):
        create(live, request.model_copy(update={"name": "Changed"}))


def test_invalid_selection_atomic_rollback(
    live: Settings, corpus_sample: tuple[UUID, list[UUID], UUID]
) -> None:
    version, ids, _ = corpus_sample
    with pytest.raises(ReviewError, match="входящие"):
        create(
            live,
            WorkingSetCreate(
                name="Bad",
                corpus_version_id=version,
                document_ids=[ids[1]],
                idempotency_key=uuid4(),
            ),
        )
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(WorkingSet)) == 0
        assert session.scalar(select(func.count()).select_from(Document)) == 3


def test_failed_mid_copy_rolls_back_clients_and_documents(
    live: Settings, corpus_sample: tuple[UUID, list[UUID], UUID]
) -> None:
    version, ids, _ = corpus_sample
    with get_session(live.database_url) as session:
        session.delete(session.get(DocumentText, ids[2]))
        session.commit()
    with pytest.raises(ReviewError, match="текст"):
        create(
            live,
            WorkingSetCreate(
                name="Rollback",
                corpus_version_id=version,
                document_ids=[ids[0]],
                idempotency_key=uuid4(),
            ),
        )
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(WorkingSet)) == 0
        assert session.scalar(select(func.count()).select_from(Client)) == 1


def test_stale_original_result_is_not_initial_experiment_input(
    live: Settings, corpus_sample: tuple[UUID, list[UUID], UUID]
) -> None:
    from datetime import date

    version, ids, result = corpus_sample
    with get_session(live.database_url) as session:
        session.get(Document, ids[1]).date = date(2026, 2, 3)
        session.commit()
    work = create(
        live,
        WorkingSetCreate(
            name="Stale source",
            corpus_version_id=version,
            document_ids=[ids[0]],
            result_ids=[result],
            idempotency_key=uuid4(),
        ),
    )
    with get_session(live.database_url) as session:
        copied = session.scalar(
            select(WorkingCopy).where(
                WorkingCopy.working_set_id == work, WorkingCopy.source_document_id == ids[0]
            )
        )
        assert copied.source_result_id == result and not copied.source_result_current
        assert not copy_origin(session, copied.document_id)["initial_inputs"]


def test_preserved_cloud_error_is_copied_and_cannot_be_improved(
    live: Settings, corpus_sample: tuple[UUID, list[UUID], UUID]
) -> None:
    from app.models import ModelConfig, Prompt
    from app.services.extraction import configuration, prepared_request, save_result
    from app.services.model_calls import ProcessingError, request_hash
    from app.services.pipeline import reconcile, retry_pipeline

    version, ids, _ = corpus_sample
    with get_session(live.database_url) as session:
        prompt = session.scalar(
            select(Prompt).where(Prompt.name == "extract", Prompt.version == "test")
        )
        model = ModelConfig(
            name="Preserved Yandex fixture",
            provider="yandex",
            model_uri="fixture",
            placement="cloud",
            active=True,
        )
        session.add(model)
        session.flush()
        snapshot = configuration(model, prompt)
        source = session.get(Document, ids[0])
        messages, _, _, _ = prepared_request(session, source, snapshot)
        key = request_hash(snapshot, messages, source.client_id)
        call = LLMCall(
            model_config_id=model.id,
            prompt_id=prompt.id,
            document_id=source.id,
            input_hash=key,
            root_input_hash=key,
            snapshot=snapshot,
            paid=False,
            status="invalid",
            response_raw="invalid fixture",
            truncated=False,
            cost_rub=0,
        )
        session.add(call)
        session.flush()
        error = save_result(
            session,
            source.id,
            model.id,
            snapshot,
            None,
            call.id,
            [],
            False,
            "preserved schema error",
        )
        error.preserved_error, error.input_hash = True, key
        config = LinkingConfig(tau_a=0.2, tau_r=0.1, use_semantic=False)
        payload = linking_payload(session, source.id, model.id, config, EncodingConfig())
        model_id = model.id
        session.commit()
    with pytest.raises(ProcessingError, match="source_extraction_error"):
        link_document(live, ids[0], payload)
    with get_session(live.database_url) as session:
        source_result = session.scalar(
            select(LinkingResult.id).where(
                LinkingResult.source_id == ids[0], LinkingResult.model_config_id == model_id
            )
        )
    work = create(
        live,
        WorkingSetCreate(
            name="Preserve",
            corpus_version_id=version,
            document_ids=[ids[0]],
            result_ids=[source_result],
            idempotency_key=uuid4(),
        ),
    )
    with get_session(live.database_url) as session:
        copied = session.scalar(
            select(WorkingCopy).where(
                WorkingCopy.working_set_id == work, WorkingCopy.source_document_id == ids[0]
            )
        )
        pipe = session.get(ProcessingPipeline, copied.document_id)
        stored = session.scalar(
            select(Extraction).where(
                Extraction.document_id == copied.document_id, Extraction.model_config_id == model_id
            )
        )
        assert stored.preserved_error and not stored.valid and stored.input_hash == key
        assert session.get(LinkingResult, pipe.result_id).outcome == "error"
        assert pipe.error == "preserved schema error"
        retry_pipeline(session, copied.document_id, mode="extract")
        reconcile(session)
        assert "защищена" in pipe.error
        assert session.scalar(select(func.count()).select_from(LLMCall)) == 1
        copied_id = copied.document_id
        session.commit()
    with pytest.raises(ProcessingError):
        extract_document(live, copied_id, model_id, prompt.id, snapshot)


def test_different_working_sets_have_disjoint_clients(
    live: Settings, corpus_sample: tuple[UUID, list[UUID], UUID]
) -> None:
    version, ids, _ = corpus_sample
    works = [
        create(
            live,
            WorkingSetCreate(
                name="Independent",
                corpus_version_id=version,
                document_ids=[ids[0]],
                idempotency_key=uuid4(),
            ),
        )
        for _ in range(2)
    ]
    with get_session(live.database_url) as session:
        clients = [
            {
                id
                for id in session.scalars(
                    select(Document.client_id)
                    .join(WorkingCopy, WorkingCopy.document_id == Document.id)
                    .where(WorkingCopy.working_set_id == work)
                )
            }
            for work in works
        ]
        assert clients[0].isdisjoint(clients[1])
