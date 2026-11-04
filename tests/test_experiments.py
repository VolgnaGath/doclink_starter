"""Infrastructure fixtures only; scripted vectors/fields are not experimental evidence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_linking_acceptance import database as database
from test_linking_acceptance import sample as sample

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import (
    Document,
    DocumentText,
    ExperimentMetric,
    ExperimentResult,
    ExperimentRun,
    ExperimentSeries,
    Extraction,
    Job,
    LLMCall,
    ModelConfig,
)
from app.services.embeddings import EncodingConfig
from app.services.experiment_metrics import field_accuracy, linking_metrics
from app.services.experiments import (
    ExperimentCreate,
    create_series,
    execute_series,
    retry_series,
    runs,
    tune_run,
)
from app.services.jobs import work_once
from app.services.linking.types import LinkingConfig
from app.services.linking_store import linking_payload
from app.services.model_calls import ProcessingError


@pytest.fixture
def matrix(database: Settings, sample: tuple[UUID, UUID]) -> tuple[UUID, UUID]:
    with get_session(database.database_url) as session:
        model = session.scalar(select(ModelConfig))
        model.name = "Qwen"
        originals = list(session.scalars(select(Extraction)))
        for name in ("YandexGPT 5.1 Pro", "GigaChat 2 Lite"):
            other = ModelConfig(name=name, provider="regex", model_uri="fixture", placement="local")
            session.add(other)
            session.flush()
            for e in originals:
                values = {
                    c.name: getattr(e, c.name)
                    for c in Extraction.__table__.columns
                    if c.name not in {"id", "model_config_id", "created_at"}
                }
                session.add(Extraction(model_config_id=other.id, **values))
        for d in session.scalars(select(Document)):
            d.gold = {
                **d.gold,
                "annotated_fields": ["kind"],
                "hard_types": [1, 8],
                "provenance": {"part": "synth"},
            }
        session.commit()
    return sample


def create(settings: Settings, version: UUID, **kwargs: object) -> UUID:
    with get_session(settings.database_url) as session:
        row = create_series(
            session,
            ExperimentCreate(
                name="Fixture matrix", corpus_version_id=version, idempotency_key=uuid4(), **kwargs
            ),
        )
        session.commit()
        return row.id


def test_annotations_distinguish_missing_absent_and_errors() -> None:
    rows = [
        {
            "valid": True,
            "fields": {"kind": "акт"},
            "gold": {
                "fields": {"kind": "акт", "amount": None},
                "annotated_fields": ["kind", "amount"],
                "provenance": {"part": "real"},
            },
        }
    ]
    assert field_accuracy(rows)["amount"]["denominator"] == 0
    rows[0]["gold"]["provenance"]["part"] = "synth"
    assert field_accuracy(rows)["amount"] == {
        "value": 1,
        "numerator": 1,
        "denominator": 1,
        "unannotated": 0,
        "gold_absent": 1,
    }
    rows[0]["valid"] = False
    assert field_accuracy(rows)["amount"]["value"] == 0
    assert linking_metrics(())["error_share"] == {"value": None, "numerator": 0, "denominator": 0}


def test_full_matrix_and_replay_no_new_calls_or_results(
    database: Settings, matrix: tuple[UUID, UUID]
) -> None:
    id = create(database, matrix[0])
    execute_series(database, id)
    with get_session(database.database_url) as session:
        series = session.get(ExperimentSeries, id)
        assert series.status == "done", series.error
        assert len(runs(session, id)) == 8
        dataset = series.dataset
        assert len(dataset["table_2_6"]) == 3
        assert all(r.reused_test for r in runs(session, id))
        metrics = list(
            session.scalars(select(ExperimentMetric).where(ExperimentMetric.slice == "all"))
        )
        assert any(m.split == "test" and m.metric == "n" and m.value == 1 for m in metrics)
        results = session.scalar(select(func.count()).select_from(ExperimentResult))
        assert results > 0
    execute_series(database, id)
    with get_session(database.database_url) as session:
        assert session.get(ExperimentSeries, id).dataset == dataset
        assert session.scalar(select(func.count()).select_from(ExperimentResult)) == results
        assert session.scalar(select(func.count()).select_from(LLMCall)) == 0
    repeat = create(database, matrix[0])
    execute_series(database, repeat)
    with get_session(database.database_url) as session:
        repeated = session.get(ExperimentSeries, repeat).dataset
        before = {r["variant"]: r for r in dataset["runs"]}
        for r in repeated["runs"]:
            if r["status"] == "done":
                assert r["report"]["result_ids"] == before[r["variant"]]["report"]["result_ids"]
                assert r["report"]["metrics"] == before[r["variant"]]["report"]["metrics"]


def test_regex_ablation_never_reads_qwen_card(
    database: Settings, matrix: tuple[UUID, UUID]
) -> None:
    version, source = matrix
    config = LinkingConfig(tau_a=0.1, tau_r=0)
    with get_session(database.database_url) as session:
        first = linking_payload(session, source, None, config, EncodingConfig(), no_llm=True)
        doc = session.get(Document, source)
        doc.doc_kind, doc.date = "договор", date(1900, 1, 1)
        session.flush()
        second = linking_payload(session, source, None, config, EncodingConfig(), no_llm=True)
        assert first == second
        assert first["data"]["source"]["card"]["kind"] == "акт"
        assert all(
            "extraction" not in p and "card" not in p
            for p in first["provenance"]["documents"].values()
        )


@pytest.mark.parametrize("change", ["text", "gold", "card", "code"])
def test_stale_freeze_stops_before_test(
    database: Settings, matrix: tuple[UUID, UUID], monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    id = create(database, matrix[0], mode="single", method="semantic")
    with get_session(database.database_url) as session:
        run_id = runs(session, id)[0].id
    tune_run(database, run_id)
    if change == "code":
        monkeypatch.setattr("app.services.experiments.code_hash", lambda: "changed")
    else:
        with get_session(database.database_url) as session:
            if change == "text":
                session.get(DocumentText, matrix[1]).text += " changed"
            elif change == "gold":
                doc = session.get(Document, matrix[1])
                doc.gold = {**doc.gold, "hard_types": [9]}
            else:
                session.get(Document, matrix[1]).date = date(1900, 1, 1)
            session.commit()
    with pytest.raises(ProcessingError):
        execute_series(database, id)
    with get_session(database.database_url) as session:
        assert session.get(ExperimentSeries, id).status == "error"
        assert session.get(ExperimentRun, run_id).test_started_at is None
        assert session.scalar(select(func.count()).select_from(ExperimentResult)) == 0


def test_no_feasible_does_not_test_or_replace_threshold(
    database: Settings, matrix: tuple[UUID, UUID]
) -> None:
    with get_session(database.database_url) as session:
        doc = session.scalar(select(Document).where(Document.split == "tune"))
        doc.gold = {**doc.gold, "links": []}
        session.commit()
    id = create(database, matrix[0], mode="single", method="semantic")
    execute_series(database, id)
    with get_session(database.database_url) as session:
        run = runs(session, id)[0]
        assert run.status == "NO_FEASIBLE_CONFIGURATION"
        assert run.test_started_at is None and run.config == {}
        assert session.scalar(select(func.count()).select_from(ExperimentResult)) == 0


def test_queue_recovers_abandoned_job_and_creation_is_concurrent_idempotent(
    database: Settings, matrix: tuple[UUID, UUID]
) -> None:
    request = ExperimentCreate(
        name="Concurrent",
        corpus_version_id=matrix[0],
        idempotency_key=uuid4(),
        mode="single",
        method="semantic",
    )

    def submit() -> UUID:
        with get_session(database.database_url) as session:
            row = create_series(session, request)
            session.commit()
            return row.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: submit(), range(2)))
    assert ids[0] == ids[1]
    with get_session(database.database_url) as session:
        row = session.get(ExperimentSeries, ids[0])
        job = session.get(Job, row.job_id)
        job.status = "running"
        job.locked_at = row.created_at
        session.commit()
    assert work_once(database, job_types=[f"experiment:{ids[0]}"])
    with get_session(database.database_url) as session:
        assert session.get(ExperimentSeries, ids[0]).status == "done"
        assert session.get(Job, job.id).status == "done"


def test_restart_after_committed_document_keeps_error_and_progress(
    database: Settings, matrix: tuple[UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import experiment_reporting

    id = create(database, matrix[0], mode="single", method="semantic")
    real_finish = experiment_reporting.finish_run
    monkeypatch.setattr(
        experiment_reporting,
        "finish_run",
        lambda *_: (_ for _ in ()).throw(ProcessingError("Stopped")),
    )
    with pytest.raises(ProcessingError, match="Stopped"):
        execute_series(database, id)
    with get_session(database.database_url) as session:
        result_ids = list(session.scalars(select(ExperimentResult.result_id)))
        retry_series(session, id)
        session.commit()
    monkeypatch.setattr(experiment_reporting, "finish_run", real_finish)
    execute_series(database, id)
    with get_session(database.database_url) as session:
        assert session.get(ExperimentSeries, id).status == "done"
        assert list(session.scalars(select(ExperimentResult.result_id))) == result_ids


def test_preserved_model_error_stays_in_denominator_and_replays_explicitly(
    database: Settings, matrix: tuple[UUID, UUID]
) -> None:
    with get_session(database.database_url) as session:
        model = session.scalar(select(ModelConfig).where(ModelConfig.name == "YandexGPT 5.1 Pro"))
        e = session.scalar(
            select(Extraction).where(
                Extraction.document_id == matrix[1], Extraction.model_config_id == model.id
            )
        )
        e.valid, e.preserved_error, e.error = False, True, "Measured schema error"
        model_id = model.id
        session.commit()
    ids = [create(database, matrix[0], mode="single", model_config_id=model_id) for _ in range(2)]
    for id in ids:
        execute_series(database, id)
    with get_session(database.database_url) as session:
        first, second = [runs(session, id)[0] for id in ids]
        measured = first.report["metrics"]["test"]["all"]
        assert measured["n"] == 1 and measured["processing_errors"] == 1
        assert measured["m"] == 0 and measured["gold_links"] == 1 and measured["recall"] == 0
        assert first.report["result_ids"] == second.report["result_ids"]
        assert second.report["resources"]["reuse_share"] == 1
        assert session.get(Extraction, e.id).preserved_error


def test_api_web_exports_share_saved_dataset_and_enforce_auth(
    database: Settings, matrix: tuple[UUID, UUID]
) -> None:
    with TestClient(create_app(database)) as client:
        body = {
            "name": "HTTP matrix",
            "corpus_version_id": str(matrix[0]),
            "idempotency_key": str(uuid4()),
        }
        assert client.post("/api/v1/experiments", json=body).status_code == 401
        assert client.get(f"/api/v1/experiments/{uuid4()}").status_code == 401
        client.post("/login", data={"username": "admin", "password": "test-password"})
        response = client.post("/api/v1/experiments", json=body)
        assert response.status_code == 202, response.text
        id = response.json()["id"]
        assert response.json()["status"] == "queued"
        assert client.get(f"/api/v1/experiments/{id}/export?format=csv").status_code == 409
        assert client.post("/api/v1/experiments", json=body).json()["id"] == id
        assert (
            client.post("/api/v1/experiments", json={**body, "name": "Different"}).status_code
            == 409
        )
        assert (
            client.post("/api/v1/experiments", json={**body, "method": "semantic"}).status_code
            == 422
        )
        execute_series(database, UUID(id))
        response = client.get(f"/api/v1/experiments/{id}")
        assert response.status_code == 200
        saved = response.json()
        assert saved["status"] == "done" and saved["progress"] == saved["total"]
        assert any(m["split"] == "test" for r in saved["runs"] for m in r["metrics"])
        assert client.get("/experiments").status_code == 200
        page = client.get(f"/experiments/{id}")
        assert page.status_code == 200 and "Скачать" in page.text
        compare = client.get(
            "/experiments/compare", params=[("run_ids", r["id"]) for r in saved["runs"][:2]]
        )
        assert compare.status_code == 200 and "tune" in compare.text and "test" in compare.text
        for format in ("csv", "md", "png"):
            download = client.get(f"/api/v1/experiments/{id}/export?format={format}")
            assert download.status_code == 200, download.text[:200] if format != "png" else "PNG"
            assert saved["dataset_hash"] in download.headers["etag"]
            if format == "png":
                assert download.content.startswith(b"\x89PNG")
                assert saved["dataset_hash"].encode() in download.content
            else:
                assert saved["dataset_hash"] in download.text
            assert (
                client.get(f"/experiments/{id}/export?format={format}").content == download.content
            )
        assert client.get(f"/api/v1/experiments/{id}/export?format=exe").status_code == 422
        with get_session(database.database_url) as session:
            result_id = session.scalar(select(ExperimentResult.result_id))
        outcome = client.get(f"/experiments/results/{result_id}")
        assert outcome.status_code == 200 and "Сохранённый исход" in outcome.text


def test_concurrent_series_share_physical_results_without_losing_metrics(
    database: Settings, matrix: tuple[UUID, UUID]
) -> None:
    ids = [create(database, matrix[0], mode="single", method="semantic") for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda id: execute_series(database, id), ids))
    with get_session(database.database_url) as session:
        first, second = [runs(session, id)[0] for id in ids]
        assert first.status == second.status == "done"
        assert first.report["result_ids"] == second.report["result_ids"]
        assert first.report["metrics"] == second.report["metrics"]
        assert first.progress == second.progress == first.total == second.total == 2
