"""REST integration: real PostgreSQL, no paid calls, shared services and safe DTOs."""

import zipfile
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_linking_store import live as live
from test_pipeline import drain
from test_pipeline import operational as operational
from test_working_sets import corpus_sample as corpus_sample

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import Document, Job, WorkingSet


@pytest.fixture
def api_client(live: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(live)) as client:
        client.post("/login", data={"username": "admin", "password": "test-password"})
        yield client


def submit(client: TestClient, client_id: UUID, files: list[tuple[str, bytes]]) -> list[str]:
    response = client.post(
        "/api/v1/documents",
        data={"client_id": str(client_id), "is_anonymized": "true"},
        files=[("files", (name, body)) for name, body in files],
    )
    assert response.status_code == 201, response.text
    return [r["document_id"] for r in response.json()["results"] if r["document_id"]]


def pair(client: TestClient, live: Settings, operational: tuple[UUID, UUID]) -> tuple[str, str]:
    ids = submit(
        client,
        operational[0],
        [
            ("in.txt", "Счёт № S1 от 02.02.2026\nПо договору № A1 от 01.02.2026".encode()),
            ("contract.txt", "Договор № A1 от 01.02.2026".encode()),
        ],
    )
    drain(live)
    return ids[0], ids[1]


def test_api_unauthorized_json_and_no_writes(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    settings.files_dir = str(tmp_path / "no-writes")
    for url in [
        "/api/v1/documents",
        "/api/v1/clients",
        "/api/v1/links",
        "/api/v1/suggestions",
        "/api/v1/working-sets",
    ]:
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 401 and response.json()["code"] == "unauthorized"
        assert "location" not in response.headers
    response = client.post(
        "/api/v1/documents", data={"client_id": str(uuid4())}, files={"files": ("in.txt", b"text")}
    )
    assert response.status_code == 401
    assert not Path(settings.files_dir).exists()


def test_api_partial_upload_validation_and_filters(
    api_client: TestClient, live: Settings, operational: tuple[UUID, UUID]
) -> None:
    client_id, _ = operational
    response = api_client.post(
        "/api/v1/documents",
        data={"client_id": str(client_id)},
        files=[
            ("files", ("a.txt", b"same")),
            ("files", ("b.txt", b"same")),
            ("files", ("bad.exe", b"x")),
        ],
    )
    assert response.status_code == 201
    assert [bool(r["document_id"]) for r in response.json()["results"]] == [True, True, False]
    page = api_client.get(
        f"/api/v1/documents?client_id={client_id}&source=upload&limit=1&offset=1"
    ).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    assert "storage_path" not in page["items"][0] and "gold" not in page["items"][0]
    assert api_client.get("/api/v1/documents?kind=акт").json()["total"] == 0
    for url in [
        "/api/v1/documents?limit=0",
        "/api/v1/documents?offset=-1",
        "/api/v1/documents?kind=bad",
        "/api/v1/documents?client_id=bad",
    ]:
        invalid = api_client.get(url)
        assert invalid.status_code == 422 and invalid.json()["code"] == "validation_error"
        assert all("input" not in d for d in invalid.json()["details"])
    assert api_client.get(f"/api/v1/documents/{uuid4()}").status_code == 404
    assert (
        api_client.post(
            "/api/v1/documents", data={"client_id": str(uuid4())}, files={"files": ("a.txt", b"x")}
        ).status_code
        == 422
    )
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 2
        assert all(not d.is_anonymized for d in session.scalars(select(Document)))


def test_api_confirmation_completion_reopen_and_removal(
    api_client: TestClient, live: Settings, operational: tuple[UUID, UUID]
) -> None:
    source, target = pair(api_client, live, operational)
    detail = api_client.get(f"/api/v1/documents/{source}").json()
    assert detail["result"]["outcome"] == "recommendation" and detail["links"] == []
    assert detail["document"]["card_model_config_id"] == detail["result"]["model_config_id"]
    assert detail["document"]["card_extraction_id"] == detail["extractions"][0]["id"]
    candidate = detail["result"]["candidates"][0]
    assert isinstance(candidate["penalties"], list) and candidate["target_id"] == target
    review = api_client.post(f"/api/v1/documents/{source}/review-sessions").json()
    request = {
        "session_id": review["id"],
        "version": 0,
        "idempotency_key": str(uuid4()),
        "action": "confirm",
    }
    confirm = api_client.post(f"/api/v1/suggestions/{candidate['id']}/review", json=request)
    assert confirm.status_code == 200, confirm.text
    assert (
        api_client.post(f"/api/v1/suggestions/{candidate['id']}/review", json=request).json()["id"]
        == confirm.json()["id"]
    )
    assert (
        api_client.get(f"/api/v1/links?document_id={target}&origin=confirmed").json()["total"] == 1
    )
    assert api_client.get("/api/v1/links?origin=manual").json()["total"] == 0
    completed = api_client.post(
        f"/api/v1/documents/{source}/review",
        json={
            "session_id": review["id"],
            "version": 1,
            "idempotency_key": str(uuid4()),
            "action": "complete",
        },
    )
    assert completed.status_code == 200, completed.text
    state = api_client.get(f"/api/v1/documents/{source}").json()
    assert state["review_state"]["status"] == "completed"
    assert state["review_actions"][-1]["duration_ms"] >= state["review_actions"][0]["duration_ms"]
    assert state["result"]["outcome"] == "recommendation"
    new_session = api_client.post(f"/api/v1/documents/{source}/review-sessions").json()["id"]
    assert (
        api_client.post(
            f"/api/v1/documents/{source}/review",
            json={
                "session_id": new_session,
                "version": 2,
                "idempotency_key": str(uuid4()),
                "action": "reopen",
            },
        ).status_code
        == 200
    )
    link = state["links"][0]
    assert (
        api_client.post(
            f"/api/v1/documents/{source}/review",
            json={
                "session_id": new_session,
                "version": 3,
                "idempotency_key": str(uuid4()),
                "action": "remove",
                "link_id": link["id"],
            },
        ).status_code
        == 200
    )
    assert api_client.get("/api/v1/links").json()["total"] == 0
    assert api_client.get("/api/v1/links?include_removed=true").json()["total"] == 1


def test_api_card_edit_idempotency_stale_conflict_and_validation(
    api_client: TestClient, live: Settings, operational: tuple[UUID, UUID]
) -> None:
    source, _ = pair(api_client, live, operational)
    review = api_client.post(f"/api/v1/documents/{source}/review-sessions").json()
    body = {
        "session_id": review["id"],
        "version": 0,
        "idempotency_key": str(uuid4()),
        "values": {"supp_ref": {"number": "ADD", "date": None, "quote": ""}, "invoice_ref": None},
    }
    edited = api_client.patch(f"/api/v1/documents/{source}/card", json=body)
    assert edited.status_code == 200, edited.text
    assert edited.json()["overrides"]["supp_ref"]["number"] == "ADD"
    assert api_client.patch(f"/api/v1/documents/{source}/card", json=body).json()["version"] == 1
    stale = api_client.patch(
        f"/api/v1/documents/{source}/card",
        json={**body, "idempotency_key": str(uuid4()), "values": {"number": "changed"}},
    )
    assert stale.status_code == 409 and stale.json()["code"] == "conflict"
    invalid = api_client.patch(
        f"/api/v1/documents/{source}/card",
        json={
            **body,
            "version": 1,
            "idempotency_key": str(uuid4()),
            "values": {"date": "32.13.2026"},
        },
    )
    assert invalid.status_code == 422
    detail = api_client.get(f"/api/v1/documents/{source}").json()
    assert detail["extractions"][0]["number"] == "S1"


def test_api_manual_link_and_conflicting_tabs(
    api_client: TestClient, live: Settings, operational: tuple[UUID, UUID]
) -> None:
    source, target = pair(api_client, live, operational)
    first = api_client.post(f"/api/v1/documents/{source}/review-sessions").json()
    second = api_client.post(f"/api/v1/documents/{source}/review-sessions").json()
    invalid = api_client.post(
        f"/api/v1/documents/{source}/review",
        json={
            "session_id": first["id"],
            "version": 0,
            "idempotency_key": str(uuid4()),
            "action": "manual",
            "target_id": source,
        },
    )
    assert invalid.status_code == 422
    accepted = api_client.post(
        f"/api/v1/documents/{source}/review",
        json={
            "session_id": first["id"],
            "version": 0,
            "idempotency_key": str(uuid4()),
            "action": "manual",
            "target_id": target,
        },
    )
    assert accepted.status_code == 200, accepted.text
    conflict = api_client.post(
        f"/api/v1/documents/{source}/review",
        json={
            "session_id": second["id"],
            "version": 0,
            "idempotency_key": str(uuid4()),
            "action": "manual",
            "target_id": target,
        },
    )
    assert conflict.status_code == 409
    links = api_client.get(f"/api/v1/links?document_id={source}&origin=manual").json()
    assert links["total"] == 1 and links["items"][0]["review_action_id"] == accepted.json()["id"]


def test_api_no_candidate_error_and_unknown_source_have_document_outcomes(
    api_client: TestClient, live: Settings, operational: tuple[UUID, UUID]
) -> None:
    for name, content, outcome in [
        ("unknown.txt", b"unknown document", "review"),
        ("in.txt", "Акт № 1 от 02.02.2026".encode(), "none"),
        ("broken.pdf", b"%PDF-broken", "error"),
    ]:
        new_client = api_client.post("/api/v1/clients", json={"name": outcome}).json()["id"]
        source = submit(api_client, UUID(new_client), [(name, content)])[0]
        drain(live)
        response = api_client.get(f"/api/v1/suggestions?decision={outcome}&document_id={source}")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["candidates"] == []
        if outcome == "none":
            review = api_client.post(f"/api/v1/documents/{source}/review-sessions").json()
            completed = api_client.post(
                f"/api/v1/documents/{source}/review",
                json={
                    "session_id": review["id"],
                    "version": 0,
                    "idempotency_key": str(uuid4()),
                    "action": "without_links",
                    "reason": "Договор отсутствует",
                },
            )
            assert completed.status_code == 200


def test_api_process_and_readonly_corpus_guards(
    api_client: TestClient, live: Settings, corpus_sample: tuple[UUID, list[UUID], UUID]
) -> None:
    _, ids, _ = corpus_sample
    assert api_client.get(f"/api/v1/documents/{ids[0]}").status_code == 200
    assert api_client.post(f"/api/v1/documents/{ids[0]}/process").status_code == 422
    assert api_client.post(f"/api/v1/documents/{ids[0]}/review-sessions").status_code == 422
    assert (
        api_client.post(f"/documents/{ids[0]}/extract", data={"model_id": str(uuid4())}).status_code
        == 422
    )
    detail = api_client.get(f"/api/v1/documents/{ids[0]}").text
    assert (
        "SECRET_GOLD" not in detail
        and "SECRET_STORAGE_PATH" not in detail
        and "SECRET_FOLDER" not in detail
    )
    assert '"deal_id"' not in detail and '"anon_maps"' not in detail
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0


def test_api_working_sets_and_browser_adapter(
    api_client: TestClient, live: Settings, corpus_sample: tuple[UUID, list[UUID], UUID]
) -> None:
    version, ids, original_result = corpus_sample
    request = {
        "name": "API working",
        "corpus_version_id": str(version),
        "document_ids": [str(ids[0])],
        "result_ids": [str(original_result)],
        "idempotency_key": str(uuid4()),
    }
    response = api_client.post("/api/v1/working-sets", json=request)
    assert response.status_code == 201, response.text
    work = response.json()
    assert len(work["copies"]) == 3
    assert api_client.post("/api/v1/working-sets", json=request).json()["id"] == work["id"]
    assert api_client.get("/api/v1/working-sets").json()["total"] == 1
    assert api_client.get(f"/api/v1/working-sets/{work['id']}").json() == work
    drain(live)
    copy_id = next(
        c["document_id"] for c in work["copies"] if c["source_document_id"] == str(ids[0])
    )
    detail = api_client.get(f"/api/v1/documents/{copy_id}").json()
    assert (
        detail["working_copy"]["initial_inputs"]
        and detail["result"]["working_copy"]["initial_inputs"]
    )
    assert detail["document"]["split"] is None
    assert len(detail["extractions"]) == 1
    assert api_client.get("/working-sets").status_code == 200
    assert api_client.get(f"/working-sets/{work['id']}").status_code == 200
    assert api_client.get("/api/v1/clients").json()["total"] == 1
    assert api_client.get("/api/v1/clients?working_only=false").json()["total"] == 2
    process = api_client.post(f"/api/v1/documents/{copy_id}/process", json={"mode": "search"})
    assert process.status_code == 202, process.text
    drain(live)
    repeated = api_client.get(f"/api/v1/documents/{copy_id}").json()
    assert not repeated["result"]["working_copy"]["initial_inputs"]
    assert repeated["result"]["id"] != detail["result"]["id"]
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(WorkingSet)) == 1


def test_api_zip_import_transaction_and_repeat(
    api_client: TestClient, live: Settings, tmp_path: Path
) -> None:
    live.files_dir = str(tmp_path / "files")
    root = Path(__file__).resolve().parents[1] / "corpus"
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".txt", ".csv"}:
                zipped.write(path, str(path.relative_to(root)))
    for reused in [False, True]:
        response = api_client.post(
            "/api/v1/corpus/import",
            files={"archive": ("corpus.zip", stream.getvalue())},
            data={"seed": "2026", "tune_share": "0.3"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["documents_imported"] == 252
        assert response.json()["reused"] == reused
        assert "report" not in response.json()
    bad = api_client.post("/api/v1/corpus/import", files={"archive": ("bad.zip", b"bad")})
    assert bad.status_code == 422
    assert api_client.get("/api/v1/corpus").json()["total"] == 1
    with get_session(live.database_url) as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 252
