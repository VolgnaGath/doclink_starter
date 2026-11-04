"""Shared multipart admission. Blob identity does not identify a logical document."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Client, Document, OperationalConfig, ProcessingPipeline
from app.services.model_calls import ProcessingError
from app.services.storage import StorageError, document_mime, save_blob


@dataclass(frozen=True)
class IncomingFile:
    filename: str
    stream: BinaryIO


@dataclass(frozen=True)
class RegistrationResult:
    filename: str
    document_id: UUID | None = None
    error: str | None = None


def register_client(session: Session, name: str, inn: str | None = None) -> Client:
    name, inn = name.strip(), (inn or "").strip()
    if not name or len(name) > 300 or (inn and not re.fullmatch(r"\d{10}|\d{12}", inn)):
        raise ProcessingError("Укажите название до 300 символов и ИНН из 10 или 12 цифр")
    client = Client(name=name, inn=inn or None, namespace="upload")
    session.add(client)
    session.flush()
    return client


def register_batch(
    session: Session,
    settings: Settings,
    client_id: UUID,
    files: list[IncomingFile],
    *,
    is_anonymized: bool = False,
) -> list[RegistrationResult]:
    # Validate common parameters before touching storage. Client lock also serializes
    # admission with decisions and card writes in the same working namespace.
    client = session.scalar(select(Client).where(Client.id == client_id).with_for_update())
    if client is None or client.namespace.startswith("corpus"):
        raise ProcessingError("Выберите рабочего клиента")
    config = session.get(OperationalConfig, 1)
    if config is None:
        raise ProcessingError("Оперативная конфигурация не установлена")
    if not files:
        raise ProcessingError("Выберите хотя бы один файл")
    batch_id = uuid4()
    results = []
    for incoming in files:
        filename = incoming.filename.replace("\\", "/").rsplit("/", 1)[-1]
        try:
            mime = document_mime(filename)
            blob = save_blob(incoming.stream, Path(settings.files_dir), settings.max_file_bytes)
        except StorageError as exc:
            results.append(RegistrationResult(filename, error=str(exc)))
            continue
        doc = Document(
            client_id=client_id,
            source="upload",
            filename=filename,
            sha256=blob.sha256,
            storage_path=str(blob.path),
            mime=mime,
            is_anonymized=is_anonymized,
            card_model_config_id=UUID(config.snapshot["model_id"]),
        )
        session.add(doc)
        session.flush()
        session.add(
            ProcessingPipeline(document_id=doc.id, batch_id=batch_id, snapshot=config.snapshot)
        )
        results.append(RegistrationResult(filename, doc.id))
    session.flush()
    return results
