from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.documents import database
from app.models import Client, Document, DocumentText, Job, StageTiming
from app.services.text_extraction import TextResult


@pytest.fixture
def document_client(app: FastAPI, db_session: Session) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[database] = override
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "test-password"})
        yield client


def create_client(client: TestClient) -> str:
    response = client.post(
        "/documents/clients", data={"name": "Новый клиент"}, follow_redirects=False
    )
    assert response.status_code == 303
    return response.headers["location"].split("=")[1]


def test_upload_clean_db_detail_original_and_dedup(
    document_client: TestClient, db_session: Session, settings: Settings
) -> None:
    client = document_client
    assert "Сначала создайте клиента" in client.get("/documents/upload").text
    client_id = create_client(client)
    content = "Русский договор с текстом".encode()
    for _ in range(2):
        response = client.post(
            "/documents/upload",
            data={"client_id": client_id},
            files={"files": ("../../contract.txt", content, "application/octet-stream")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        detail = client.get(response.headers["location"])
        assert "Русский договор с текстом" in detail.text
        assert "Текст получен" in detail.text
        assert client.get(response.headers["location"] + "/original").content == content
    docs = db_session.scalars(select(Document)).all()
    assert len(docs) == 2 and docs[0].id != docs[1].id
    assert docs[0].storage_path == docs[1].storage_path
    assert docs[0].filename == "contract.txt"
    assert not docs[0].is_anonymized and not docs[0].in_scope
    assert docs[0].gold is None and docs[0].split is None
    assert len(list(Path(settings.files_dir).glob("*/*"))) == 1
    assert client.get("/documents").status_code == 200
    assert client.get(f"/documents/{uuid4()}").status_code == 404
    assert client.post(f"/documents/{docs[0].id}/retry-text").status_code == 409


def test_error_retains_original_and_retry_same_id(
    document_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = document_client
    client_id = create_client(client)
    response = client.post(
        "/documents/upload",
        data={"client_id": client_id},
        files={"files": ("broken.pdf", b"%PDF-broken", "application/pdf")},
        follow_redirects=False,
    )
    location = response.headers["location"]
    document_id = UUID(location.rsplit("/", 1)[1])
    assert "Ошибка обработки" in client.get(location).text
    assert client.get(location + "/original").content == b"%PDF-broken"
    assert db_session.get(DocumentText, document_id) is None
    monkeypatch.setattr(
        "app.services.text_extraction.extract_text",
        lambda *args: TextResult("Текст восстановлен", 1, 0),
    )
    retry = client.post(location + "/retry-text", follow_redirects=False)
    assert retry.headers["location"] == location
    assert "Текст восстановлен" not in client.get(location).text
    assert db_session.scalar(select(Job)).status == "queued"
    assert db_session.scalar(select(func.count()).select_from(Document)) == 1
    timings = db_session.scalars(select(StageTiming).order_by(StageTiming.id)).all()
    assert [t.ok for t in timings] == [False]


def test_reject_multiple_files_bad_client_format_and_blank_name(
    document_client: TestClient, db_session: Session, settings: Settings
) -> None:
    client = document_client
    assert client.post("/documents/clients", data={"name": "  "}).status_code == 422
    assert client.post("/documents/clients", data={"name": "test", "inn": "bad"}).status_code == 422
    client_id = create_client(client)
    for data, files in [
        ({"client_id": client_id}, [("files", ("a.txt", b"text")), ("files", ("b.txt", b"text"))]),
        ({"client_id": str(uuid4())}, [("files", ("a.txt", b"text"))]),
        ({"client_id": client_id}, [("files", ("file.exe", b"text"))]),
    ]:
        assert client.post("/documents/upload", data=data, files=files).status_code == 422
    settings.max_file_bytes = 2
    assert (
        client.post(
            "/documents/upload",
            data={"client_id": client_id},
            files={"files": ("large.txt", b"large")},
        ).status_code
        == 422
    )
    assert db_session.scalar(select(func.count()).select_from(Document)) == 0
    assert not list(Path(settings.files_dir).glob("*/*"))


def test_filters_pagination_and_client_namespace(
    document_client: TestClient, db_session: Session
) -> None:
    client = document_client
    db_session.add(Client(name="Новый клиент", namespace="corpus:real", external_key="client"))
    db_session.commit()
    client_id = create_client(client)
    assert db_session.scalar(select(func.count()).select_from(Client)) == 2
    for i in range(26):
        db_session.add(
            Document(
                client_id=UUID(client_id),
                source="upload",
                filename=f"file_{i}.txt",
                sha256="abc",
                mime="text/plain",
                storage_path="unused",
                status="text",
                split="tune" if i == 0 else None,
                doc_kind="акт" if i == 0 else None,
            )
        )
    db_session.commit()
    first = client.get(f"/documents?client_id={client_id}")
    assert "Страница 1 из 2" in first.text and "client_id=" in first.text
    assert "Страница 2 из 2" in client.get("/documents?page=2").text
    assert "Всего: 1." in client.get("/documents?kind=акт&split=tune&status=text").text
    assert "Всего: 0." in client.get("/documents?status=error").text
    assert client.get("/documents?kind=чек").status_code == 422


def test_unauthorized_no_files_or_database(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    settings.files_dir = str(tmp_path / "no-writes")
    for path in ("/documents", "/documents/upload", f"/documents/{uuid4()}"):
        assert client.get(path, follow_redirects=False).status_code == 303
    response = client.post(
        "/documents/upload",
        data={"client_id": str(uuid4())},
        files={"files": ("file.txt", b"text")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert not Path(settings.files_dir).exists()
    assert (
        client.post("/documents/clients", data={"name": "test"}, follow_redirects=False).status_code
        == 303
    )


def test_docx_and_pdf_uploads(document_client: TestClient, tmp_path: Path) -> None:
    from docx import Document as Docx
    from test_text_extraction import make_pdf

    client = document_client
    client_id = create_client(client)
    doc = Docx()
    doc.add_paragraph("Параграф документа")
    stream = BytesIO()
    doc.save(stream)
    pdf = tmp_path / "text.pdf"
    make_pdf(pdf)
    scan = tmp_path / "scan.pdf"
    make_pdf(scan, scan=True)
    for name, content, expected in [
        ("file.docx", stream.getvalue(), "Параграф документа"),
        ("file.pdf", pdf.read_bytes(), "Contract"),
        ("scan.pdf", scan.read_bytes(), "Договор"),
    ]:
        response = client.post(
            "/documents/upload", data={"client_id": client_id}, files={"files": (name, content)}
        )
        assert response.status_code == 200 and expected in response.text
