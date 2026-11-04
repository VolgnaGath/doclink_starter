"""Human workflow with real PostgreSQL; fixtures are not model-quality measurements."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_linking_store import CONFIG, ENCODING
from test_linking_store import live as live
from test_linking_store import sample as sample

from app.config import Settings
from app.db import get_session
from app.models import (
    Document,
    Link,
    LinkingResult,
    LinkSuggestion,
    ReviewAction,
    ReviewSession,
    ReviewState,
)
from app.services.linking_store import link_document, linking_payload
from app.services.review import Decision, ReviewError, decide, open_review


def start(live: Settings, source: UUID, model: UUID) -> tuple[UUID, UUID]:
    with get_session(live.database_url) as session:
        payload = linking_payload(session, source, model, CONFIG, ENCODING)
    result = link_document(live, source, payload)
    with get_session(live.database_url) as session:
        suggestion = session.scalar(
            select(LinkSuggestion).where(LinkSuggestion.result_id == result)
        )
        review = open_review(session, source)
        review.opened_at = datetime.now(UTC) - timedelta(seconds=5)
        session.commit()
        return review.id, suggestion.id


def send(live: Settings, source: UUID, request: Decision) -> UUID:
    with get_session(live.database_url) as session:
        action = decide(session, source, request)
        session.commit()
        return action.id


def test_f16_confirm_completion_timer_and_exact_retry(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    source, target, model = sample
    review, suggestion = start(live, source, model)
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(Link)) == 0
    request = Decision(
        session_id=review,
        version=0,
        idempotency_key=uuid4(),
        action="confirm",
        suggestion_id=suggestion,
    )
    action_id = send(live, source, request)
    assert send(live, source, request) == action_id
    with get_session(live.database_url) as session:
        action = session.get(ReviewAction, action_id)
        link = session.scalar(select(Link))
        assert link.review_action_id == action_id and link.target_id == target
        assert link.link_type == "основание" and link.origin == "confirmed"
        assert action.duration_ms >= 5000
        assert session.get(ReviewState, source).status == "open"
        assert session.scalar(select(LinkingResult)).outcome == "recommendation"
    complete = Decision(session_id=review, version=1, idempotency_key=uuid4(), action="complete")
    send(live, source, complete)
    with get_session(live.database_url) as session:
        assert session.get(ReviewSession, review).duration_ms >= 5000
        assert session.get(ReviewState, source).status == "completed"
    with pytest.raises(ReviewError, match="другого запроса"):
        send(live, source, request.model_copy(update={"action": "reject"}))


def test_concurrent_requests_one_link_and_conflicting_tabs(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    source, target, model = sample
    review, suggestion = start(live, source, model)
    request = Decision(
        session_id=review,
        version=0,
        idempotency_key=uuid4(),
        action="confirm",
        suggestion_id=suggestion,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: send(live, source, request), range(2)))
    assert ids[0] == ids[1]
    with pytest.raises(ReviewError, match="другой вкладке"):
        send(live, source, request.model_copy(update={"idempotency_key": uuid4()}))


@pytest.mark.parametrize("change", ["date", "pool", "extraction"])
def test_stale_input_cannot_be_confirmed(
    live: Settings, sample: tuple[UUID, UUID, UUID], change: str
) -> None:
    source, target, model = sample
    review, suggestion = start(live, source, model)
    with get_session(live.database_url) as session:
        if change == "date":
            session.get(Document, target).date = date(2027, 1, 1)
        elif change == "pool":
            doc = session.get(Document, target)
            session.add(
                Document(
                    client_id=doc.client_id,
                    source="upload",
                    filename="extra.txt",
                    mime="text/plain",
                    storage_path="fixture",
                    sha256="fixture",
                    doc_kind="договор",
                    date=doc.date,
                )
            )
        else:
            session.get(Document, source).card_extraction_id = None
        session.commit()
    with pytest.raises(ReviewError, match="изменились"):
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
    # Reopening a tab cannot make an old recommendation current.
    with get_session(live.database_url) as session:
        fresh = open_review(session, source)
        fresh_id = fresh.id
        session.commit()
    with pytest.raises(ReviewError, match="устарел"):
        send(
            live,
            source,
            Decision(
                session_id=fresh_id,
                version=0,
                idempotency_key=uuid4(),
                action="confirm",
                suggestion_id=suggestion,
            ),
        )


def test_without_links_reject_reopen_manual_remove_history(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    source, target, model = sample
    review, suggestion = start(live, source, model)
    with pytest.raises(ReviewError, match="всем рекомендациям"):
        send(
            live,
            source,
            Decision(
                session_id=review,
                version=0,
                idempotency_key=uuid4(),
                action="without_links",
                reason="нет договора",
            ),
        )
    send(
        live,
        source,
        Decision(
            session_id=review,
            version=0,
            idempotency_key=uuid4(),
            action="reject",
            suggestion_id=suggestion,
        ),
    )
    send(
        live,
        source,
        Decision(
            session_id=review,
            version=1,
            idempotency_key=uuid4(),
            action="without_links",
            reason="подходящий отсутствует",
        ),
    )
    with get_session(live.database_url) as session:
        new = open_review(session, source)
        review = new.id
        session.commit()
    send(
        live,
        source,
        Decision(session_id=review, version=2, idempotency_key=uuid4(), action="reopen"),
    )
    send(
        live,
        source,
        Decision(
            session_id=review, version=3, idempotency_key=uuid4(), action="manual", target_id=target
        ),
    )
    with get_session(live.database_url) as session:
        link_id = session.scalar(select(Link.id))
    send(
        live,
        source,
        Decision(
            session_id=review, version=4, idempotency_key=uuid4(), action="remove", link_id=link_id
        ),
    )
    with get_session(live.database_url) as session:
        link = session.get(Link, link_id)
        assert link.origin == "manual" and link.removed_at and link.removal_action_id
        assert session.scalar(select(func.count()).select_from(ReviewAction)) == 5


@pytest.mark.parametrize("change", ["future", "kind", "client", "unknown", "self"])
def test_manual_target_validation(
    live: Settings, sample: tuple[UUID, UUID, UUID], change: str
) -> None:
    source, target, model = sample
    with get_session(live.database_url) as session:
        doc = session.get(Document, target)
        if change == "future":
            doc.date = date(2027, 1, 1)
        elif change == "kind":
            doc.doc_kind = "акт"
        elif change == "unknown":
            doc.date = None
        elif change == "client":
            from app.models import Client

            other = Client(name="Other")
            session.add(other)
            session.flush()
            doc.client_id = other.id
        else:
            target = source
        review = open_review(session, source)
        review_id = review.id
        session.commit()
    with pytest.raises(ReviewError, match="Недопустимая цель"):
        send(
            live,
            source,
            Decision(
                session_id=review_id,
                version=0,
                idempotency_key=uuid4(),
                action="manual",
                target_id=target,
            ),
        )


def test_action_and_link_rollback_together(live: Settings, sample: tuple[UUID, UUID, UUID]) -> None:
    source, target, model = sample
    review, suggestion = start(live, source, model)

    def fail(session: Session, *args: object) -> None:
        if any(isinstance(obj, Link) for obj in session.new):
            raise RuntimeError("fixture transaction failure")

    event.listen(Session, "before_flush", fail)
    try:
        with pytest.raises(RuntimeError):
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
    finally:
        event.remove(Session, "before_flush", fail)
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(ReviewAction)) == 0
        assert session.scalar(select(func.count()).select_from(Link)) == 0
        assert session.get(ReviewState, source).version == 0
        session.add(
            Link(
                source_id=source,
                target_id=target,
                link_type="основание",
                origin="confirmed",
                review_action_id=uuid4(),
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_corpus_is_readonly(live: Settings, sample: tuple[UUID, UUID, UUID]) -> None:
    source, _, _ = sample
    with get_session(live.database_url) as session:
        session.get(Document, source).source = "corpus"
        session.commit()
    with (
        get_session(live.database_url) as session,
        pytest.raises(ReviewError, match="рабочую копию"),
    ):
        open_review(session, source)


def test_f4_manual_layer_research_reset_and_preserved_extraction(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    from app.models import CardEdit, Extraction, Job
    from app.services.card_edits import CardPatch, edit_card
    from app.services.extraction import save_result
    from app.services.extraction_schema import ExtractedFields
    from app.services.jobs import work_once

    source, target, model = sample
    review, suggestion = start(live, source, model)
    with get_session(live.database_url) as session:
        old = session.scalar(
            select(Extraction).where(
                Extraction.document_id == source,
                Extraction.model_config_id == model,
                Extraction.is_current,
            )
        )
        original_refs = list(old.contract_refs)
        state = edit_card(
            session,
            source,
            CardPatch(
                version=0,
                session_id=review,
                idempotency_key=uuid4(),
                values={
                    "doc_kind": "акт",
                    "number": None,
                    "contract_refs": [{"number": "CORRECTED", "date": None, "quote": ""}],
                    "supp_ref": {"number": "S", "date": None, "quote": ""},
                    "invoice_ref": {"number": "I", "date": None, "quote": ""},
                },
            ),
        )
        assert state.version == 1 and "number" in state.overrides
        assert old.contract_refs == original_refs
        payload = linking_payload(session, source, model, CONFIG, ENCODING)
        assert payload["data"]["source"]["card"]["kind"] == "акт"
        assert payload["data"]["source"]["fields"]["references"][0]["number"] == "CORRECTED"
        assert not payload["data"]["source"]["fields"]["references"][0]["verified"]
        assert session.scalar(select(Job).where(Job.type == "link"))
        session.commit()
    with pytest.raises(ReviewError):
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
    assert work_once(live, job_types=["link"])
    with get_session(live.database_url) as session:
        doc = session.get(Document, source)
        card_model = doc.card_model_config_id
        save_result(
            session,
            source,
            card_model,
            {"fixture": "new"},
            ExtractedFields(doc_kind="счёт", number="MODEL-NEW", date="02.02.2026"),
            None,
            [],
            False,
            None,
        )
        assert doc.doc_kind == "акт" and doc.number is None
        state = edit_card(
            session,
            source,
            CardPatch(
                version=1, session_id=review, idempotency_key=uuid4(), reset=["number", "doc_kind"]
            ),
        )
        assert doc.doc_kind == "счёт" and doc.number == "MODEL-NEW"
        assert "number" not in state.overrides
        assert session.scalar(select(func.count()).select_from(CardEdit)) == 2
        session.commit()


def test_contract_edit_keeps_link_and_marks_dependent_review(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    from app.services.card_edits import CardPatch, edit_card

    source, target, model = sample
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
        target_review = open_review(session, target)
        edit_card(
            session,
            target,
            CardPatch(
                version=0,
                session_id=target_review.id,
                idempotency_key=uuid4(),
                values={"date": "03.02.2026"},
            ),
        )
        assert session.get(ReviewState, source).needs_review
        assert session.scalar(select(Link)).removed_at is None
        session.commit()


@pytest.mark.parametrize(
    "kind,link_type",
    [
        ("допсоглашение", "изменение"),
        ("счёт", "основание"),
        ("акт", "исполнение"),
        ("УПД", "исполнение"),
    ],
)
def test_manual_link_type_from_current_card(
    live: Settings,
    sample: tuple[UUID, UUID, UUID],
    kind: str,
    link_type: str,
) -> None:
    source, target, model = sample
    with get_session(live.database_url) as session:
        session.get(Document, source).doc_kind = kind
        review = open_review(session, source)
        review_id = review.id
        session.commit()
    send(
        live,
        source,
        Decision(
            session_id=review_id,
            version=0,
            idempotency_key=uuid4(),
            action="manual",
            target_id=target,
        ),
    )
    with get_session(live.database_url) as session:
        assert session.scalar(select(Link)).link_type == link_type


def test_browser_routes_auth_timer_edit_and_decision(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    import re

    from fastapi.testclient import TestClient

    from app.main import create_app

    source, target, model = sample
    _, suggestion = start(live, source, model)
    with TestClient(create_app(live)) as client:
        assert client.get("/review", follow_redirects=False).status_code == 303
        client.post("/login", data={"username": live.app_user, "password": live.app_password})
        queue = client.get("/review?client_id=&outcome=&status=")
        assert queue.status_code == 200 and str(source) in queue.text
        card = client.get(f"/review/{source}")
        assert card.status_code == 200 and "CORRECTED" not in card.text
        assert "Подтвердить рекомендацию" in card.text
        review_id = re.search(r'name="session_id" value="([^"]+)"', card.text).group(1)
        before = client.get(f"/review/{source}?session_id={review_id}")
        assert before.status_code == 200
        with get_session(live.database_url) as session:
            assert session.scalar(select(func.count()).select_from(ReviewSession)) == 2
        confirm = client.post(
            f"/review/{source}/decision",
            data={
                "session_id": review_id,
                "version": "0",
                "action": "confirm",
                "idempotency_key": str(uuid4()),
                "suggestion_id": str(suggestion),
            },
        )
        assert confirm.status_code == 200 and "Активна" in confirm.text
        complete = client.post(
            f"/review/{source}/decision",
            data={
                "session_id": review_id,
                "version": "1",
                "action": "complete",
                "idempotency_key": str(uuid4()),
            },
        )
        assert complete.status_code == 200 and "Открыть проверку повторно" in complete.text
        edit = client.post(
            f"/review/{source}/edit",
            data={
                "version": "2",
                "field": "doc_kind",
                "value": "акт",
                "session_id": review_id,
                "idempotency_key": str(uuid4()),
            },
        )
        assert edit.status_code == 200 and "Требуется повторная проверка" in edit.text
        assert client.get("/review?status=completed&outcome=recommendation").status_code == 200


def test_two_recommendations_need_two_decisions_and_no_links_without_reason(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    from app.models import DocumentText
    from app.services.extraction import save_result
    from app.services.extraction_schema import ExtractedFields, check_evidence

    source, target, model = sample
    with get_session(live.database_url) as session:
        doc = session.get(Document, target)
        other = Document(
            client_id=doc.client_id,
            source="upload",
            filename="second.txt",
            mime="text/plain",
            storage_path="fixture",
            sha256="fixture",
            doc_kind="договор",
            date=doc.date,
        )
        session.add(other)
        session.flush()
        body = "Договор № B1 от 01.02.2026"
        session.add(
            DocumentText(document_id=other.id, text=body, pages=1, chars=len(body), ocr_pages=0)
        )
        fields = ExtractedFields(
            doc_kind="договор", number="B1", date="01.02.2026", evidence={"number": body}
        )
        save_result(
            session, other.id, model, {}, fields, None, check_evidence(fields, [body]), False, None
        )
        body = "По договору № A1 от 01.02.2026\nПо договору № B1 от 01.02.2026"
        fields = ExtractedFields(
            doc_kind="счёт",
            date="02.02.2026",
            contract_refs=[
                {"number": number, "date": "01.02.2026", "quote": line}
                for number, line in zip(["A1", "B1"], body.splitlines(), strict=True)
            ],
        )
        save_result(
            session, source, model, {}, fields, None, check_evidence(fields, [body]), False, None
        )
        session.commit()
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
    with pytest.raises(ReviewError, match="всем рекомендациям"):
        send(
            live,
            source,
            Decision(session_id=review, version=1, idempotency_key=uuid4(), action="complete"),
        )
    with get_session(live.database_url) as session:
        second = session.scalar(select(LinkSuggestion.id).where(LinkSuggestion.id != suggestion))
    send(
        live,
        source,
        Decision(
            session_id=review,
            version=1,
            idempotency_key=uuid4(),
            action="reject",
            suggestion_id=second,
        ),
    )
    send(
        live,
        source,
        Decision(session_id=review, version=2, idempotency_key=uuid4(), action="complete"),
    )


def test_no_candidate_manual_session_provenance_and_no_zero_for_abandoned(
    live: Settings, sample: tuple[UUID, UUID, UUID]
) -> None:
    source, target, model = sample
    with get_session(live.database_url) as session:
        first = open_review(session, source)
        second = open_review(session, source)
        first_id, second_id = first.id, second.id
        session.commit()
    with pytest.raises(ReviewError, match="причина"):
        send(
            live,
            source,
            Decision(
                session_id=second_id, version=0, idempotency_key=uuid4(), action="without_links"
            ),
        )
    send(
        live,
        source,
        Decision(
            session_id=second_id,
            version=0,
            idempotency_key=uuid4(),
            action="without_links",
            reason="Договор не требуется",
        ),
    )
    with get_session(live.database_url) as session:
        first, second = session.get(ReviewSession, first_id), session.get(ReviewSession, second_id)
        assert first.duration_ms is None and first.completed_at is None
        assert second.duration_ms is not None and second.context["input_revision"]
        assert session.scalar(select(ReviewAction)).result_id is None
