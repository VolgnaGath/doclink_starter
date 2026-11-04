"""Durable inbox/outbox. External calls never run in the review transaction."""

from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_engine, get_session
from app.models import (
    Client,
    Document,
    IntegrationAttempt,
    IntegrationEvent,
    IntegrationInbox,
    IntegrationState,
    Job,
    Link,
    ReviewAction,
)
from app.services.extraction import lock_number
from app.services.mock1c_client import ExchangeError, Mock1CClient
from app.services.model_calls import ProcessingError
from app.services.registration import IncomingFile, register_batch
from mock1c.protocol import InboxDocument, LinkEvent

SOURCE = "mock1c"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def start_attempt(
    session: Session,
    kind: str,
    reference: str,
) -> IntegrationAttempt:
    attempt = IntegrationAttempt(
        kind=kind,
        reference=reference,
        started_at=datetime.now(UTC),
        error="Исход попытки ещё не зафиксирован",
    )
    session.add(attempt)
    session.commit()
    return attempt


def finish_attempt(
    attempt: IntegrationAttempt,
    clock: float,
    error: ExchangeError | None = None,
    status_code: int | None = 200,
) -> None:
    attempt.duration_ms = max(0, round((monotonic() - clock) * 1000))
    attempt.status_code = error.status_code if error else status_code
    attempt.error = str(error) if error else None


def ingest_catalog(session: Session, rows: list[InboxDocument]) -> None:
    for doc in rows:
        snapshot = doc.model_dump(mode="json")
        old = session.scalar(
            select(IntegrationInbox)
            .where(
                IntegrationInbox.source == SOURCE,
                IntegrationInbox.external_id == doc.id,
            )
            .with_for_update()
        )
        if old:
            if old.metadata_snapshot != snapshot:
                old.status, old.error = "conflict", "Внешний ID изменил опубликованные данные"
        else:
            session.add(
                IntegrationInbox(source=SOURCE, external_id=doc.id, metadata_snapshot=snapshot)
            )
    session.flush()


def import_entry(
    session: Session, settings: Settings, row: IntegrationInbox, client: Mock1CClient
) -> None:
    doc = InboxDocument.model_validate(row.metadata_snapshot)
    if row.attempts >= 3:
        row.status, row.error = "error", "Исчерпаны попытки импорта; требуется явный повтор"
        session.commit()
        return
    # Attempt count is durable before network; interrupted imports are safe to resume.
    row.attempts += 1
    attempt = start_attempt(session, "import", str(row.id))
    clock = monotonic()
    try:
        with client.file(doc) as stream, session.begin_nested():
            owner = session.scalar(
                select(Client).where(
                    Client.namespace == SOURCE,
                    Client.external_key == str(doc.client_id),
                )
            )
            if owner is None:
                owner = Client(
                    name=doc.client_name, namespace=SOURCE, external_key=str(doc.client_id)
                )
                session.add(owner)
                session.flush()
            result = register_batch(
                session,
                settings,
                owner.id,
                [IncomingFile(doc.filename, stream)],
                is_anonymized=doc.is_anonymized,
                source=SOURCE,
            )[0]
            if result.error:
                raise ExchangeError(result.error, 200)
            row.document_id, row.status, row.error = result.document_id, "done", None
            local = session.get(Document, result.document_id)
            local.registered_at = doc.registered_at
        finish_attempt(attempt, clock)
    except ProcessingError as exc:
        error = exc if isinstance(exc, ExchangeError) else ExchangeError(str(exc))
        row.status = "error" if row.attempts >= 3 else "pending"
        row.run_after = datetime.now(UTC) + timedelta(seconds=2**row.attempts)
        row.error = str(error)
        finish_attempt(attempt, clock, error)
    session.commit()


def poll(settings: Settings, *, force: bool = False, full: bool = False) -> bool:
    if not settings.mock1c_enabled:
        return False
    with get_engine(settings.database_url).connect() as guard:
        key = lock_number("integration:poll")
        if not guard.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}):
            return False
        guard.commit()
        try:
            with get_session(settings.database_url) as session, Mock1CClient(settings) as client:
                now = datetime.now(UTC)
                state = session.get(IntegrationState, SOURCE)
                if state is None:
                    state = IntegrationState(source=SOURCE, next_poll_at=now)
                    session.add(state)
                    session.flush()
                if not force and state.next_poll_at > now:
                    return False
                state.next_poll_at = now + timedelta(seconds=settings.mock1c_poll_seconds)
                state.last_poll_at = now
                attempt = start_attempt(session, "poll", SOURCE)
                clock = monotonic()
                try:
                    since = (
                        EPOCH
                        if full or state.watermark is None
                        else state.watermark - timedelta(seconds=1)
                    )
                    rows = client.catalog(since.isoformat())
                    ingest_catalog(session, rows)
                    if rows:
                        state.watermark = max(
                            [state.watermark or EPOCH, *(d.registered_at for d in rows)]
                        )
                    state.error = None
                    finish_attempt(attempt, clock)
                    session.commit()
                except ExchangeError as exc:
                    state.error = str(exc)
                    finish_attempt(attempt, clock, exc)
                    session.commit()
                ids = list(
                    session.scalars(
                        select(IntegrationInbox.id)
                        .where(
                            IntegrationInbox.status == "pending",
                            IntegrationInbox.run_after <= datetime.now(UTC),
                        )
                        .order_by(IntegrationInbox.id)
                    )
                )
                for entry_id in ids:
                    row = session.get(IntegrationInbox, entry_id)
                    import_entry(session, settings, row, client)
            return True
        finally:
            guard.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            guard.commit()


def create_event(
    session: Session, link: Link, action: ReviewAction, operation: str
) -> IntegrationEvent:
    session.flush()
    endpoints = {}
    for field, document_id in (("source_id", link.source_id), ("target_id", link.target_id)):
        entry = session.scalar(
            select(IntegrationInbox).where(
                IntegrationInbox.document_id == document_id,
                IntegrationInbox.source == SOURCE,
            )
        )
        if entry and entry.status == "done":
            endpoints[field] = str(entry.external_id)
    event = IntegrationEvent(
        id=uuid4(),
        link_id=link.id,
        document_id=link.source_id,
        review_action_id=action.id,
        operation=operation,
        payload={},
        status="pending" if len(endpoints) == 2 else "blocked",
        created_at=action.decided_at,
        error=None if len(endpoints) == 2 else "Нет внешней привязки обеих сторон в mock1c",
    )
    if len(endpoints) == 2:
        event.payload = LinkEvent(
            event_id=event.id,
            link_id=link.id,
            review_action_id=action.id,
            operation=operation,
            source_id=endpoints["source_id"],
            target_id=endpoints["target_id"],
            link_type=link.link_type,
            occurred_at=action.decided_at,
        ).model_dump(mode="json")
    session.add(event)
    session.flush()
    return event


def enqueue_deliveries(session: Session) -> None:
    events = list(
        session.scalars(
            select(IntegrationEvent)
            .where(
                IntegrationEvent.status != "done",
            )
            .order_by(IntegrationEvent.created_at, IntegrationEvent.operation, IntegrationEvent.id)
        )
    )
    seen = set()
    for event in events:
        if event.document_id in seen:
            continue
        seen.add(event.document_id)
        if event.status != "pending" or event.run_after > datetime.now(UTC):
            continue
        # Nullable model IDs do not conflict in uq_job_active. Serialize by document.
        locked = session.scalar(
            select(Document.id)
            .where(
                Document.id == event.document_id,
            )
            .with_for_update(skip_locked=True)
        )
        if locked is None:
            continue
        active = session.scalar(
            select(Job.id).where(
                Job.document_id == event.document_id,
                Job.type == "integration:deliver",
                Job.status.in_(["queued", "running"]),
            )
        )
        if active is None:
            session.add(
                Job(
                    type="integration:deliver",
                    document_id=event.document_id,
                    payload={"event_id": str(event.id)},
                )
            )


def deliver(settings: Settings, event_id: UUID) -> None:
    if not settings.mock1c_enabled:
        return
    with get_session(settings.database_url) as session, Mock1CClient(settings) as client:
        event = session.get(IntegrationEvent, event_id)
        if event.status == "done":
            return
        if event.status != "pending":
            return
        if event.attempts >= 3:
            event.status, event.error = (
                "error",
                "Исчерпаны попытки доставки; требуется явный повтор",
            )
            session.commit()
            return
        event.attempts += 1
        attempt = start_attempt(session, "deliver", str(event.id))
        clock = monotonic()
        try:
            code = client.send(event.payload)
            event.status, event.error, event.delivered_at = "done", None, datetime.now(UTC)
            finish_attempt(attempt, clock, status_code=code)
        except ExchangeError as exc:
            event.status = "error" if event.attempts >= 3 else "pending"
            event.run_after = datetime.now(UTC) + timedelta(seconds=2**event.attempts)
            event.error = str(exc)
            finish_attempt(attempt, clock, exc)
        session.commit()


def retry(session: Session, kind: str, row_id: UUID) -> None:
    model = (
        IntegrationInbox if kind == "import" else IntegrationEvent if kind == "deliver" else None
    )
    row = session.get(model, row_id, with_for_update=True) if model else None
    if row is None or row.status != "error":
        raise ProcessingError("Повтор доступен только для ошибки обмена")
    row.status, row.attempts, row.error, row.run_after = "pending", 0, None, datetime.now(UTC)
