"""Actual PostgreSQL/HTTP plumbing; fixtures do not measure model quality."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from test_linking_store import live as live
from test_linking_store import sample as sample
from test_pipeline import operational as operational
from test_review import send, start

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import (
    Document,
    IntegrationAttempt,
    IntegrationEvent,
    IntegrationInbox,
    IntegrationState,
    Job,
    Link,
    ProcessingPipeline,
)
from app.services.integration import deliver, enqueue_deliveries, ingest_catalog, poll, retry
from app.services.jobs import work_once
from app.services.mock1c_client import ExchangeError, Mock1CClient
from app.services.review import Decision, decide
from mock1c.main import app as mock_app
from mock1c.protocol import InboxDocument


@pytest.fixture
def mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    root = tmp_path / "inbox"
    root.mkdir()
    monkeypatch.setenv("MOCK1C_INBOX", str(root))
    monkeypatch.setenv("MOCK1C_DATABASE", str(tmp_path / "journal.sqlite3"))
    with TestClient(mock_app) as client:
        yield client


def publish(
    root: Path,
    *,
    owner: UUID | None = None,
    body: bytes = b"document",
    registered_at: datetime | None = None,
    **kwargs: object,
) -> InboxDocument:
    doc = InboxDocument(
        id=uuid4(),
        client_id=owner or uuid4(),
        client_name="Клиент 1С",
        filename=f"{uuid4()}.txt",
        registered_at=registered_at or datetime.now(UTC),
        **kwargs,
    )
    (root / doc.filename).write_bytes(body)
    (root / (doc.filename + ".json")).write_text(doc.model_dump_json())
    return doc.model_copy(update={"sha256": hashlib.sha256(body).hexdigest()})


@pytest.fixture
def transport(mock: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    original = Mock1CClient.__init__

    def init(self: Mock1CClient, settings: Settings) -> None:
        original(self, settings)
        self.http.close()
        self.http = mock

    monkeypatch.setattr(Mock1CClient, "__init__", init)
    monkeypatch.setattr(Mock1CClient, "__exit__", lambda *args: None)


@pytest.fixture
def connector(live: Settings, transport: None) -> Settings:
    live.mock1c_enabled = True
    with get_session(live.database_url) as session:
        session.execute(text("TRUNCATE integration_state, integration_attempts"))
        session.commit()
    return live


def test_mock_catalog_filter_files_and_immutable_publication(
    mock: TestClient, tmp_path: Path
) -> None:
    doc = publish(tmp_path / "inbox")
    url = "/odata/standard.odata/Catalog_Документы"
    assert mock.get(url).json()["value"][0]["sha256"] == doc.sha256
    assert mock.get(
        url, params={"$filter": f"Дата gt datetime'{doc.registered_at.isoformat()}'"}
    ).json() == {"value": []}
    assert mock.get(url, params={"$filter": "bad"}).status_code == 422
    assert mock.get(f"/odata/standard.odata/Файл/{doc.id}").content == b"document"
    assert mock.get(f"/odata/standard.odata/Файл/{uuid4()}").status_code == 404
    (tmp_path / "inbox" / doc.filename).write_bytes(b"changed")
    assert mock.get(url).status_code == 409


@pytest.mark.parametrize("filename", ["../escape.txt", "foo/bar.txt", "bad.exe"])
def test_inbox_rejects_unsafe_files(mock: TestClient, tmp_path: Path, filename: str) -> None:
    wire = {
        "id": str(uuid4()),
        "client_id": str(uuid4()),
        "client_name": "Клиент",
        "filename": filename,
        "registered_at": datetime.now(UTC).isoformat(),
    }
    (tmp_path / "inbox" / "bad.json").write_text(json.dumps(wire))
    assert mock.get("/odata/standard.odata/Catalog_Документы").status_code == 409


def test_import_repeat_equal_dates_conflict_and_pipeline(
    connector: Settings,
    operational: tuple[UUID, UUID],
    tmp_path: Path,
) -> None:
    first = publish(tmp_path / "inbox")
    second = publish(tmp_path / "inbox", owner=first.client_id, registered_at=first.registered_at)
    assert poll(connector, force=True)
    with get_session(connector.database_url) as session:
        docs = list(session.scalars(select(Document)))
        assert len(docs) == 2
        assert all(
            d.source == "mock1c" and d.gold is None and d.split is None and not d.is_anonymized
            for d in docs
        )
        assert all(session.get(ProcessingPipeline, d.id) for d in docs)
        ids = {d.id for d in docs}
        assert session.get(IntegrationState, "mock1c").watermark == second.registered_at
    assert poll(connector, force=True)
    with get_session(connector.database_url) as session:
        assert set(session.scalars(select(Document.id))) == ids
        ingest_catalog(session, [first.model_copy(update={"sha256": "a" * 64})])
        session.commit()
        row = session.scalar(
            select(IntegrationInbox).where(IntegrationInbox.external_id == first.id)
        )
        assert row.status == "conflict"
        assert session.get(Document, row.document_id).sha256 == first.sha256


def test_partial_import_retry_survives_advanced_cursor(
    connector: Settings,
    operational: tuple[UUID, UUID],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = publish(tmp_path / "inbox")
    publish(tmp_path / "inbox", owner=first.client_id)
    real = Mock1CClient.file

    def failing(self: Mock1CClient, doc: InboxDocument):
        if doc.id == first.id:
            raise ExchangeError("temporary", 503)
        return real(self, doc)

    monkeypatch.setattr(Mock1CClient, "file", failing)
    poll(connector, force=True)
    with get_session(connector.database_url) as session:
        row = session.scalar(
            select(IntegrationInbox).where(IntegrationInbox.external_id == first.id)
        )
        assert row.status == "pending" and row.attempts == 1
        row.run_after = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
        assert session.scalar(select(func.count()).select_from(Document)) == 1
    monkeypatch.setattr(Mock1CClient, "file", real)
    poll(connector, force=True)
    with get_session(connector.database_url) as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 2
        assert all(r.status == "done" for r in session.scalars(select(IntegrationInbox)))


def mappings(settings: Settings, source: UUID, target: UUID, root: Path) -> None:
    first = publish(root)
    second = publish(root, owner=first.client_id)
    with get_session(settings.database_url) as session:
        for local, doc in ((source, first), (target, second)):
            session.add(
                IntegrationInbox(
                    source="mock1c",
                    external_id=doc.id,
                    document_id=local,
                    metadata_snapshot=doc.model_dump(mode="json"),
                    status="done",
                )
            )
        session.commit()


def test_outbox_atomic_retry_receipt_loss_remove_and_job_dedup(
    connector: Settings,
    sample: tuple[UUID, UUID, UUID],
    tmp_path: Path,
    mock: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, model = sample
    mappings(connector, source, target, tmp_path / "inbox")
    review, suggestion = start(connector, source, model)
    request = Decision(
        session_id=review,
        version=0,
        idempotency_key=uuid4(),
        action="confirm",
        suggestion_id=suggestion,
    )
    with get_session(connector.database_url) as session:
        decide(session, source, request)
        assert session.scalar(select(func.count()).select_from(IntegrationEvent)) == 1
        session.rollback()
    with get_session(connector.database_url) as session:
        assert session.scalar(select(func.count()).select_from(IntegrationEvent)) == 0
        assert session.scalar(select(func.count()).select_from(Link)) == 0
    action = send(connector, source, request)
    assert send(connector, source, request) == action
    with get_session(connector.database_url) as session:
        event = session.scalar(select(IntegrationEvent))
        event_id, payload, link_id = event.id, event.payload, event.link_id
    real_send = Mock1CClient.send

    def lost(self: Mock1CClient, payload: dict) -> int:
        real_send(self, payload)
        raise ExchangeError("ReadTimeout")

    monkeypatch.setattr(Mock1CClient, "send", lost)
    deliver(connector, event_id)
    monkeypatch.setattr(Mock1CClient, "send", real_send)
    deliver(connector, event_id)
    with get_session(connector.database_url) as session:
        event = session.get(IntegrationEvent, event_id)
        assert event.status == "done" and event.payload == payload and event.attempts == 2
    assert len(mock.get("/journal").json()["events"]) == 1
    send(
        connector,
        source,
        Decision(
            session_id=review, version=1, idempotency_key=uuid4(), action="remove", link_id=link_id
        ),
    )

    def enqueue(_: int) -> None:
        with get_session(connector.database_url) as session:
            enqueue_deliveries(session)
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(enqueue, range(2)))
    with get_session(connector.database_url) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Job).where(Job.type == "integration:deliver")
            )
            == 1
        )
    assert work_once(connector, job_types=["integration:deliver"])
    journal = mock.get("/journal").json()
    assert [e["operation"] for e in journal["events"]] == ["create", "remove"]
    assert not journal["links"]
    assert (
        mock.post(
            "/odata/standard.odata/РегистрСведений_СвязиДокументов",
            json={**payload, "link_type": "исполнение"},
        ).status_code
        == 409
    )


def test_failed_delivery_blocks_remove_and_explicit_retry(
    connector: Settings,
    sample: tuple[UUID, UUID, UUID],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, model = sample
    mappings(connector, source, target, tmp_path / "inbox")
    review, suggestion = start(connector, source, model)
    send(
        connector,
        source,
        Decision(
            session_id=review,
            version=0,
            idempotency_key=uuid4(),
            action="confirm",
            suggestion_id=suggestion,
        ),
    )
    with get_session(connector.database_url) as session:
        event_id = session.scalar(select(IntegrationEvent.id))
        link_id = session.scalar(select(Link.id))
    send(
        connector,
        source,
        Decision(
            session_id=review, version=1, idempotency_key=uuid4(), action="remove", link_id=link_id
        ),
    )

    def fail(*args: object) -> int:
        raise ExchangeError("HTTP 503", 503)

    monkeypatch.setattr(Mock1CClient, "send", fail)
    for _ in range(3):
        deliver(connector, event_id)
    with get_session(connector.database_url) as session:
        assert session.get(IntegrationEvent, event_id).status == "error"
        enqueue_deliveries(session)
        session.commit()
        assert (
            session.scalar(
                select(func.count()).select_from(Job).where(Job.type == "integration:deliver")
            )
            == 0
        )
        retry(session, "deliver", event_id)
        session.commit()
        assert session.get(IntegrationEvent, event_id).attempts == 0


def test_missing_mapping_visible_and_status_requires_auth(
    live: Settings,
    sample: tuple[UUID, UUID, UUID],
) -> None:
    source, _, model = sample
    review, suggestion = start(live, source, model)
    send(
        live,
        source,
        Decision(
            session_id=review,
            version=0,
            idempotency_key=uuid4(),
            action="confirm",
            suggestion_id=suggestion,
        ),
    )
    with get_session(live.database_url) as session:
        assert session.scalar(select(IntegrationEvent)).status == "blocked"
    with TestClient(create_app(live)) as client:
        assert client.get("/integration", follow_redirects=False).status_code == 303
        client.post("/login", data={"username": live.app_user, "password": live.app_password})
        response = client.get(f"/integration?document_id={source}")
        assert response.status_code == 200 and "blocked" in response.text
        assert "Нет внешней привязки" in response.text


def test_transport_bounded_file_and_safe_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.max_file_bytes = 2
    with Mock1CClient(settings) as client:
        client.http.close()
        client.http = httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"large")),
            base_url="http://mock",
        )
        doc = InboxDocument(
            id=uuid4(),
            client_id=uuid4(),
            client_name="x",
            filename="x.txt",
            registered_at=datetime.now(UTC),
            sha256="a" * 64,
        )
        with pytest.raises(ExchangeError, match="лимит"):
            with client.file(doc):
                pass


def test_concurrent_poll_and_restart_after_receipt(
    connector: Settings,
    sample: tuple[UUID, UUID, UUID],
    tmp_path: Path,
    mock: TestClient,
    operational: tuple[UUID, UUID],
) -> None:
    source, target, model = sample
    mappings(connector, source, target, tmp_path / "inbox")
    publish(tmp_path / "inbox")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: poll(connector, force=True), range(2)))
    with get_session(connector.database_url) as session:
        assert session.scalar(select(func.count()).select_from(IntegrationInbox)) == 3
    review, suggestion = start(connector, source, model)
    send(
        connector,
        source,
        Decision(
            session_id=review,
            version=0,
            idempotency_key=uuid4(),
            action="confirm",
            suggestion_id=suggestion,
        ),
    )
    with get_session(connector.database_url) as session:
        event = session.scalar(select(IntegrationEvent))
        with Mock1CClient(connector) as client:
            client.send(event.payload)
        event.attempts = 1
        # Worker disappeared after the receipt and before committing local success.
        job = Job(
            type="integration:deliver",
            document_id=source,
            payload={"event_id": str(event.id)},
            status="running",
            locked_at=datetime.now(UTC),
        )
        session.add(job)
        session.commit()
        job_id, event_id = job.id, event.id
    assert work_once(connector, job_types=["integration:deliver"])
    with get_session(connector.database_url) as session:
        assert session.get(Job, job_id).status == "done"
        assert session.get(IntegrationEvent, event_id).status == "done"
    assert len(mock.get("/journal").json()["events"]) == 1


def test_disabling_connector_pauses_already_queued_delivery(
    connector: Settings,
    sample: tuple[UUID, UUID, UUID],
    tmp_path: Path,
    mock: TestClient,
) -> None:
    source, target, model = sample
    mappings(connector, source, target, tmp_path / "inbox")
    review, suggestion = start(connector, source, model)
    send(
        connector,
        source,
        Decision(
            session_id=review,
            version=0,
            idempotency_key=uuid4(),
            action="confirm",
            suggestion_id=suggestion,
        ),
    )
    with get_session(connector.database_url) as session:
        enqueue_deliveries(session)
        session.commit()
    connector.mock1c_enabled = False
    assert not work_once(connector, job_types=["integration:deliver"])
    assert not mock.get("/journal").json()["events"]
    connector.mock1c_enabled = True
    assert work_once(connector, job_types=["integration:deliver"])
    assert len(mock.get("/journal").json()["events"]) == 1


def test_import_errors_are_bounded_and_retry_preserves_identity(
    connector: Settings,
    operational: tuple[UUID, UUID],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish(tmp_path / "inbox")
    original = Mock1CClient.file

    def fail(*args: object):
        raise ExchangeError("ConnectTimeout")

    monkeypatch.setattr(Mock1CClient, "file", fail)
    for _ in range(3):
        poll(connector, force=True)
        with get_session(connector.database_url) as session:
            row = session.scalar(select(IntegrationInbox))
            row.run_after = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
    poll(connector, force=True)
    with get_session(connector.database_url) as session:
        row = session.scalar(select(IntegrationInbox))
        row_id = row.id
        assert row.status == "error" and row.attempts == 3
        assert (
            session.scalar(
                select(func.count())
                .select_from(IntegrationAttempt)
                .where(IntegrationAttempt.kind == "import")
            )
            == 3
        )
        retry(session, "import", row.id)
        session.commit()
    monkeypatch.setattr(Mock1CClient, "file", original)
    poll(connector, force=True)
    with get_session(connector.database_url) as session:
        row = session.get(IntegrationInbox, row_id)
        assert row.status == "done" and row.document_id


def test_mock_rejects_cross_client_links_and_remove_before_create(
    mock: TestClient,
    tmp_path: Path,
) -> None:
    source, target = publish(tmp_path / "inbox"), publish(tmp_path / "inbox")
    payload = {
        "event_id": str(uuid4()),
        "link_id": str(uuid4()),
        "review_action_id": str(uuid4()),
        "operation": "create",
        "source_id": str(source.id),
        "target_id": str(target.id),
        "link_type": "основание",
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    url = "/odata/standard.odata/РегистрСведений_СвязиДокументов"
    assert mock.post(url, json=payload).status_code == 409
    target = publish(tmp_path / "inbox", owner=source.client_id)
    assert (
        mock.post(
            url, json={**payload, "target_id": str(target.id), "operation": "remove"}
        ).status_code
        == 409
    )
    assert mock.get("/journal").json() == {"events": [], "links": []}


def test_unfinished_attempt_is_durable_and_does_not_become_zero_success(
    connector: Settings,
    operational: tuple[UUID, UUID],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish(tmp_path / "inbox")

    def crash(*args: object):
        raise RuntimeError("process lost")

    monkeypatch.setattr(Mock1CClient, "file", crash)
    with pytest.raises(RuntimeError):
        poll(connector, force=True)
    with get_session(connector.database_url) as session:
        row = session.scalar(select(IntegrationInbox))
        assert row.status == "pending" and row.attempts == 1 and row.document_id is None
        attempt = session.scalar(
            select(IntegrationAttempt).where(IntegrationAttempt.kind == "import")
        )
        assert attempt.duration_ms is None and attempt.status_code is None and attempt.error
