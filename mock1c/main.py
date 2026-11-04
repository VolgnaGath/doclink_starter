import os
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from mock1c.protocol import LinkEvent, utc_date
from mock1c.store import catalog, journal, receive, validate_publications

app = FastAPI(title="DocLink mock 1C", docs_url=None, redoc_url=None, openapi_url=None)


def inbox() -> Path:
    return Path(os.environ.get("MOCK1C_INBOX", "/data/inbox"))


def database() -> Path:
    return Path(os.environ.get("MOCK1C_DATABASE", "/data/mock1c/journal.sqlite3"))


def documents() -> list[Any]:
    try:
        rows = catalog(inbox())
        validate_publications(database(), rows)
        return rows
    except (ValueError, OSError) as exc:
        raise HTTPException(409, "Некорректный опубликованный inbox") from exc


class MockHealth(BaseModel):
    status: str


@app.get("/health", response_model=MockHealth)
def health() -> MockHealth:
    return MockHealth(status="ok")


@app.get("/odata/standard.odata/Catalog_Документы")
def list_documents(
    date_filter: Annotated[str | None, Query(alias="$filter")] = None,
) -> dict[str, Any]:
    rows = documents()
    if date_filter:
        match = re.fullmatch(r"Дата gt (?:datetime)?'([^']+)'", date_filter)
        try:
            since = datetime.fromisoformat(match[1]) if match else None
            if since is None or since.tzinfo is None:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(422, "Ожидается Дата gt datetime'<UTC>'") from exc
        rows = [d for d in rows if d.registered_at > since]
    return {
        "value": [{**d.model_dump(mode="json"), "Дата": utc_date(d.registered_at)} for d in rows]
    }


@app.get("/odata/standard.odata/Файл/{document_id}")
def file(document_id: UUID) -> FileResponse:
    for doc in documents():
        if doc.id == document_id:
            return FileResponse(inbox() / doc.filename, filename=doc.filename)
    raise HTTPException(404, "Документ не найден")


@app.post("/odata/standard.odata/РегистрСведений_СвязиДокументов")
def write_link(event: LinkEvent) -> dict[str, Any]:
    try:
        duplicate = receive(database(), event, documents())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"event_id": str(event.event_id), "accepted": True, "duplicate": duplicate}


@app.get("/journal")
def read_journal() -> dict[str, Any]:
    return journal(database())
