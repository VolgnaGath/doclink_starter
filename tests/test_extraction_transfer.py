"""Offline infrastructure fixtures, not measurements of extraction quality."""

import json
import os
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select, text

from app.config import Settings
from app.db import get_engine, get_session
from app.extract_cli import execute, parser
from app.models import (
    Client,
    CorpusVersion,
    Document,
    DocumentText,
    Extraction,
    Job,
    LLMCall,
    ModelConfig,
    Prompt,
)
from app.seed import seed
from app.services.extraction import configuration, extract_document
from app.services.extraction_transfer import card_snapshot, paid_blockers, preflight, transfer
from app.services.jobs import enqueue, work_once
from app.services.model_calls import ProcessingError, fingerprint
from app.services.providers import Completion


class Replies:
    def __init__(self, *items: str) -> None:
        self.items, self.count = items, 0

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> Completion:
        reply = self.items[self.count]
        self.count += 1
        return Completion(
            reply, json.dumps({"choices": [{"message": {"content": reply}}]}), 100, 20
        )


@pytest.fixture
def database(settings: Settings) -> Iterator[Settings]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Изолированная PostgreSQL: make verify-transfer")
    settings.database_url = url
    settings.llm_provider, settings.paid_calls_enabled = "live", True
    settings.budget_rub = Decimal(1000)
    settings.yandex_budget_rub = settings.gigachat_budget_rub = Decimal(500)

    def clean() -> None:
        with get_engine(url).begin() as conn:
            conn.execute(text("TRUNCATE clients, corpus_versions, model_configs, prompts CASCADE"))

    clean()
    yield settings
    clean()


def populate(settings: Settings) -> tuple[UUID, UUID, UUID, UUID, dict[str, Any]]:
    with get_session(settings.database_url) as session:
        seed(session)
        models = list(session.scalars(select(ModelConfig)))
        model = next(m for m in models if m.name == "Qwen")
        for row in models:
            row.active, row.default_card = row == model, row == model
        model.price_in_1k = model.price_out_1k = Decimal("0.1")
        session.flush()
        session.refresh(model)
        prompt = session.scalar(select(Prompt))
        snapshot = configuration(model, prompt)
        protocol_hash = fingerprint(
            {"models": [snapshot], "preparation": snapshot["preparation_version"]}
        )
        source = CorpusVersion(
            name="source",
            fingerprint="source",
            seed=2026,
            tune_share=0.3,
            report={
                "extraction_protocols": {
                    protocol_hash: {"models": [snapshot], "test_started": True}
                }
            },
        )
        target = CorpusVersion(
            name="target", fingerprint="target", seed=2026, tune_share=0.3, report={}
        )
        client = Client(name="Клиент", inn="7707083893")
        session.add_all([source, target, client])
        session.flush()
        source_docs = []
        for version in [source, target]:
            for index in range(3 if version == source else 4):
                content = f"Счёт № {index} от 01.01.2026. К договору № Д-1 от 01.12.2025."
                doc = Document(
                    client_id=client.id,
                    corpus_version_id=version.id,
                    corpus_doc_id=f"doc-{index}",
                    source="corpus",
                    filename="GOLD_SECRET_PATH",
                    storage_path="GOLD_SECRET_PATH",
                    sha256=f"hash-{index}",
                    mime="text/plain",
                    status="text",
                    is_anonymized=True,
                    split="test" if index else "tune",
                    gold={"number": "GOLD_NUMBER", "case": "GOLD_CASE"},
                    card_model_config_id=model.id if version == source else None,
                )
                session.add(doc)
                session.flush()
                session.add(
                    DocumentText(
                        document_id=doc.id, text=content, pages=1, chars=len(content), ocr_pages=0
                    )
                )
                if version == source:
                    source_docs.append(doc.id)
        session.commit()
    extract_document(
        settings,
        source_docs[0],
        model.id,
        prompt.id,
        snapshot,
        provider=Replies("invalid", '{"doc_kind":"счёт","number":"0","date":"01.01.2026"}'),
    )
    for doc_id in source_docs[1:]:
        with pytest.raises(ProcessingError, match="Невалидное"):
            extract_document(
                settings,
                doc_id,
                model.id,
                prompt.id,
                snapshot,
                provider=Replies("invalid", "invalid"),
            )
    execute(parser().parse_args(["--corpus-version", str(target.id), "--freeze"]), settings)
    return source.id, target.id, model.id, prompt.id, snapshot


def test_transfer_success_and_two_errors_are_idempotent_and_never_send(
    database: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target, model, prompt, snapshot = populate(database)

    def no_provider(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Transfer/resume must not construct a provider")

    monkeypatch.setattr("app.services.extraction.make_provider", no_provider)
    with get_session(database.database_url) as session:
        before = session.scalar(select(func.sum(LLMCall.cost_rub)))
        original_ids = set(session.scalars(select(Extraction.id)))
        report = preflight(session, source, target, database)
        assert report["counts"] == {"reuse_success": 1, "preserve_error": 2, "generate": 1}
        assert not report["blockers"]
        assert "GOLD_SECRET_PATH" not in json.dumps(report) and "GOLD_NUMBER" not in json.dumps(
            report
        )
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert transfer(session, report) == {
            "reuse_success": 1,
            "preserve_error": 2,
            "pending_generation": 1,
        }
        session.commit()
        assert transfer(session, report) == {"existing": 3, "pending_generation": 1}
        session.commit()
        rows = list(session.scalars(select(Extraction).where(Extraction.id.not_in(original_ids))))
        assert len(rows) == 3 and sum(r.preserved_error for r in rows) == 2
        assert all(r.source_extraction_id in original_ids and r.input_hash for r in rows)
        assert all(not r.cache_hit and not r.valid for r in rows if r.preserved_error)
        assert all(session.get(Extraction, id).is_current for id in original_ids)
        assert session.scalar(select(func.sum(LLMCall.cost_rub))) == before
        assert session.scalar(select(func.count()).select_from(LLMCall)) == 6
        manifest = card_snapshot(session, target)
        assert manifest["tau_a"] is None and manifest["status"] == "pending_tune_not_frozen"
        assert sum(card["card_extraction_id"] is not None for card in manifest["cards"]) == 1
        preserved = [r for r in rows if r.preserved_error]
        # Freeze before enqueue, including the inherited source history.
    execute(parser().parse_args(["--corpus-version", str(target), "--freeze"]), database)
    for row in preserved:
        with get_session(database.database_url) as session:
            assert (
                enqueue(
                    session,
                    session.get(Document, row.document_id),
                    [session.get(ModelConfig, model)],
                    retry=True,
                )
                == []
            )
            session.commit()
        with pytest.raises(ProcessingError, match="Невалидное"):
            extract_document(database, row.document_id, model, prompt, snapshot)
    with get_session(database.database_url) as session:
        assert session.scalar(select(func.count()).select_from(LLMCall)) == 6
        assert session.scalar(select(func.sum(LLMCall.cost_rub))) == before


@pytest.mark.parametrize("change", ["text", "client", "params", "prompt", "source_error"])
def test_stale_preflight_rejected_without_partial_results(database: Settings, change: str) -> None:
    source, target, model, prompt, _ = populate(database)
    with get_session(database.database_url) as session:
        report = preflight(session, source, target, database)
        session.commit()
        if change == "text":
            doc_id = UUID(report["pairs"][-1]["document_id"])
            session.get(DocumentText, doc_id).text += " Изменение"
        elif change == "client":
            doc = session.get(Document, UUID(report["pairs"][-1]["document_id"]))
            client = Client(name="Другой клиент")
            session.add(client)
            session.flush()
            doc.client_id = client.id
        elif change == "params":
            session.get(ModelConfig, model).params = {"temperature": 0.5}
        elif change == "prompt":
            session.get(Prompt, prompt).text += " Изменение"
        else:
            session.get(Extraction, UUID(report["pairs"][1]["source_extraction_id"])).valid = True
        session.commit()
        with pytest.raises(ProcessingError):
            transfer(session, report)
        session.rollback()
        assert (
            session.scalar(
                select(func.count())
                .select_from(Extraction)
                .join(Document, Extraction.document_id == Document.id)
                .where(Document.corpus_version_id == target)
            )
            == 0
        )


def test_preflight_changed_source_and_unknown_spend_are_visible(database: Settings) -> None:
    source, target, _, _, _ = populate(database)
    with get_session(database.database_url) as session:
        doc = session.scalar(select(Document).where(Document.corpus_version_id == target))
        doc.sha256 = "changed"
        call = session.scalar(select(LLMCall))
        call.cost_rub = None
        call.status = "uncertain"
        session.flush()
        report = preflight(session, source, target, database)
        assert report["blockers"] and report["budgets"]["yandex"]["unknown_attempts"] == 1
        with pytest.raises(ProcessingError, match="блокеры"):
            transfer(session, report)


def test_corpus_extension_does_not_reset_viewed_test_even_without_explicit_lineage(
    database: Settings,
) -> None:
    source, target, model, _, _ = populate(database)
    base = ["--corpus-version", str(target), "--freeze"]
    same = execute(parser().parse_args(base), database)
    assert not same["reused_test"]
    assert any(item["corpus_version"] == str(source) for item in same["test_history"])
    with get_session(database.database_url) as session:
        session.get(ModelConfig, model).params = {"temperature": 0.6}
        session.commit()
    changed = execute(parser().parse_args(base), database)
    assert changed["reused_test"] and changed["protocol_hash"] != same["protocol_hash"]
    with get_session(database.database_url) as session:
        doc = session.scalar(
            select(Document).where(Document.corpus_version_id == target, Document.split == "test")
        )
        enqueue(session, doc, [session.get(ModelConfig, model)])
        session.flush()
        protocol = session.get(CorpusVersion, target).report["extraction_protocols"][
            changed["protocol_hash"]
        ]
        assert protocol["reused_test"] and protocol["test_started"]


def test_budget_resume_keeps_initial_answer_and_same_job(
    database: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.model_calls import reserve

    source, target, model, _, _ = populate(database)
    with get_session(database.database_url) as session:
        report = preflight(session, source, target, database)
        assert not paid_blockers(report)
        transfer(session, report)
        novel = next(p for p in report["pairs"] if p["action"] == "generate")
        doc_id = UUID(novel["document_id"])
        ids = enqueue(session, session.get(Document, doc_id), [session.get(ModelConfig, model)])
        session.commit()
    provider = Replies("invalid", '{"number":"3"}')
    monkeypatch.setattr("app.services.extraction.make_provider", lambda *_: provider)
    monkeypatch.setattr(Replies, "close", lambda self: None, raising=False)

    def budget_refusal(
        session: Any, call: LLMCall, settings: Settings, messages: list[dict[str, str]]
    ) -> None:
        if len(messages) > 1:
            raise ProcessingError("Недостаточно бюджета провайдера yandex")
        reserve(session, call, settings, messages)

    monkeypatch.setattr("app.services.model_calls.reserve", budget_refusal)
    assert work_once(database, [doc_id], [model])
    assert provider.count == 1
    with get_session(database.database_url) as session:
        assert session.get(Job, ids[0]).status == "error"
        assert (
            enqueue(
                session,
                session.get(Document, doc_id),
                [session.get(ModelConfig, model)],
                resume_budget_errors=True,
            )
            == ids
        )
        session.commit()
    monkeypatch.setattr("app.services.model_calls.reserve", reserve)
    assert work_once(database, [doc_id], [model])
    assert provider.count == 2
    with get_session(database.database_url) as session:
        calls = list(
            session.scalars(
                select(LLMCall).where(LLMCall.job_id == ids[0]).order_by(LLMCall.started_at)
            )
        )
        assert [c.status for c in calls] == ["invalid", "success"]
        assert calls[0].input_hash == calls[0].root_input_hash
        assert calls[1].input_hash != calls[1].root_input_hash
        assert session.get(Job, ids[0]).status == "done"
        assert session.get(Job, ids[0]).attempts == 2


def test_paid_admission_rechecks_budget_and_unknown_spend(database: Settings) -> None:
    source, target, _, _, _ = populate(database)
    with get_session(database.database_url) as session:
        report = preflight(session, source, target, database)
        assert not paid_blockers(report)
        report["budgets"]["yandex"]["remaining_rub"] = "0"
        assert any("бюджета" in value for value in paid_blockers(report))
        report["budgets"]["yandex"]["unknown_attempts"] = 1
        assert any("Неизвестный" in value for value in paid_blockers(report))
