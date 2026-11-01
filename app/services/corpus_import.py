"""Evaluation-only corpus ingestion. This module must not be imported by linking."""

import csv
import hashlib
import io
import json
import math
import random
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Client, CorpusVersion, Document
from app.services.storage import save_blob
from app.services.text_extraction import process_text

ALGORITHM = "corpus-v1/split-v1"
KINDS = {"договор", "допсоглашение", "счёт", "акт", "УПД"}
LINK_TYPES = {"основание", "исполнение", "изменение"}
FIELDS = ("kind", "number", "date", "cp_name", "cp_inn", "amount", "subject")


class CorpusError(ValueError):
    pass


@dataclass
class CorpusDocument:
    key: str
    part: str
    deal: str
    client_key: str
    in_scope: bool
    content: bytes
    gold: dict[str, Any]
    split: str = "test"


@dataclass
class PreparedCorpus:
    fingerprint: str
    documents: list[CorpusDocument]
    report: dict[str, Any]


def split_deals(deals: list[str], seed: int, tune_share: float) -> dict[str, str]:
    if not math.isfinite(tune_share) or not 0 <= tune_share <= 1:
        raise CorpusError("tune_share должен быть от 0 до 1")
    ordered = sorted(set(deals))
    random.Random(seed).shuffle(ordered)
    tune = set(ordered[: math.floor(len(ordered) * tune_share)])
    return {deal: "tune" if deal in tune else "test" for deal in ordered}


def csv_rows(content: bytes, required: set[str], path: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")), delimiter=";", strict=True)
    headers = reader.fieldnames or []
    if not required <= set(headers) or len(set(headers)) != len(headers):
        raise CorpusError(f"Некорректные колонки: {path}")
    rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise CorpusError(f"Некорректная строка CSV: {path}")
    return rows


def safe_id(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or str(path) != value:
        raise CorpusError(f"Некорректный идентификатор: {value}")
    return value


def prepare_corpus(root: Path, seed: int, tune_share: float, settings: Settings) -> PreparedCorpus:
    # Snapshot bytes used both for fingerprinting and importing, avoiding mutable-input races.
    inputs: dict[str, bytes] = {}

    def read(relative: str) -> bytes:
        if relative not in inputs:
            path = root / relative
            if not path.resolve().is_relative_to(root.resolve()):
                raise CorpusError("Файл находится за пределами корпуса")
            with path.open("rb") as stream:
                content = stream.read(settings.max_file_bytes + 1)
            if len(content) > settings.max_file_bytes:
                raise CorpusError(f"Превышен размер файла: {relative}")
            inputs[relative] = content
        return inputs[relative]

    documents: dict[str, CorpusDocument] = {}
    warnings: list[dict[str, str]] = []
    distributions: dict[str, Any] = {}
    try:
        for part in ("real", "synth"):
            required = {"doc_id", "deal_id", "source", "kind", "number", "date", "hard_types"}
            required |= (
                {"client", "in_scope", "note"}
                if part == "real"
                else {"client_inn", "cp_inn", "cp_name", "amount", "subject"}
            )
            path = f"{part}/documents.csv"
            rows = csv_rows(read(path), required, path)
            if not rows:
                raise CorpusError(f"Пустая часть корпуса: {part}")
            split = split_deals([row["deal_id"] for row in rows], seed, tune_share)
            for row in rows:
                doc_id = safe_id(row["doc_id"])
                deal = safe_id(row["deal_id"])
                key = f"{part}/{doc_id}"
                if key in documents or row["source"] != part:
                    raise CorpusError(f"Повтор ID или некорректный source: {key}")
                in_scope = row.get("in_scope", "1")
                if in_scope not in {"0", "1"}:
                    raise CorpusError(f"Некорректный in_scope: {key}")
                if row["kind"] not in KINDS | {"чек", "приложение"} or (
                    in_scope == "1" and row["kind"] not in KINDS
                ):
                    raise CorpusError(f"Некорректный вид: {key}")
                fields: dict[str, Any] = {field: row.get(field) or None for field in FIELDS}
                if fields["date"]:
                    fields["date"] = (
                        datetime.strptime(fields["date"], "%d.%m.%Y").date().isoformat()
                    )
                if fields["amount"] is not None:
                    amount = Decimal(fields["amount"])
                    if not amount.is_finite() or amount < 0:
                        raise CorpusError(f"Некорректная сумма: {key}")
                    fields["amount"] = str(amount)
                hard_types = [int(v) for v in re.split(r"[,;|\s]+", row["hard_types"]) if v]
                if any(value <= 0 for value in hard_types):
                    raise CorpusError(f"Некорректный трудный тип: {key}")
                client_key = row["client"] if part == "real" else row["client_inn"]
                if not client_key.strip() or (
                    part == "synth" and not re.fullmatch(r"\d{10}|\d{12}", client_key)
                ):
                    raise CorpusError(f"Некорректный клиент: {key}")
                if fields["cp_inn"] and not re.fullmatch(r"\d{10}|\d{12}", fields["cp_inn"]):
                    raise CorpusError(f"Некорректный ИНН: {key}")
                content = read(f"{key}.txt")
                # Validate UTF-8 and emptiness before any database writes.
                if not content.decode("utf-8-sig").strip() or b"\0" in content:
                    raise CorpusError(f"Пустой или двоичный текст: {key}")
                documents[key] = CorpusDocument(
                    key=key,
                    part=part,
                    deal=deal,
                    client_key=client_key,
                    in_scope=in_scope == "1",
                    content=content,
                    split=split[deal],
                    gold={
                        "fields": fields,
                        "annotated_fields": [field for field in FIELDS if field in row],
                        "links": [],
                        "hard_types": hard_types,
                        "provenance": {"part": part, "row": row},
                    },
                )
            distributions[part] = {
                subset: {
                    "deals": sum(s == subset for s in split.values()),
                    "documents": sum(
                        d.part == part and d.split == subset for d in documents.values()
                    ),
                }
                for subset in ("tune", "test")
            }

        path = "real/manifest.csv"
        manifest = csv_rows(
            read(path),
            {"сделка", "документ", "страниц", "страниц_OCR", "символов", "замечание"},
            path,
        )
        seen_manifest: set[str] = set()
        for row in manifest:
            key = f"real/{safe_id(row['сделка'])}/{safe_id(row['документ'])}"
            if key not in documents or key in seen_manifest:
                raise CorpusError(f"Некорректная ссылка manifest: {key}")
            pages, ocr, chars = (int(row[f]) for f in ("страниц", "страниц_OCR", "символов"))
            if pages < 0 or not 0 <= ocr <= pages or chars < 0:
                raise CorpusError(f"Некорректные метрики manifest: {key}")
            seen_manifest.add(key)
            documents[key].gold["provenance"]["manifest"] = row
        if seen_manifest != {key for key in documents if key.startswith("real/")}:
            raise CorpusError("Manifest не покрывает real-документы")

        seen_links: set[tuple[str, str, str]] = set()
        for part in ("real", "synth"):
            path = f"{part}/links.csv"
            for row in csv_rows(read(path), {"source", "target", "type"}, path):
                source, target = (f"{part}/{safe_id(row[field])}" for field in ("source", "target"))
                link_type = row["type"]
                if source not in documents or target not in documents or source == target:
                    raise CorpusError(f"Битая эталонная ссылка: {source} → {target}")
                if documents[target].gold["fields"]["kind"] != "договор":
                    raise CorpusError(f"Цель не договор: {target}")
                if documents[source].client_key != documents[target].client_key:
                    raise CorpusError(f"Связь между разными клиентами: {source} → {target}")
                identity = source, target, link_type
                if link_type not in LINK_TYPES or identity in seen_links:
                    raise CorpusError(f"Повтор или некорректный тип связи: {identity}")
                seen_links.add(identity)
                documents[source].gold["links"].append({"target": target, "type": link_type})
                source_date = documents[source].gold["fields"]["date"]
                target_date = documents[target].gold["fields"]["date"]
                if source_date and target_date and target_date > source_date:
                    warnings.append(
                        {
                            "source": source,
                            "target": target,
                            "source_date": source_date,
                            "target_date": target_date,
                        }
                    )
    except (OSError, UnicodeError, ValueError, InvalidOperation, csv.Error) as exc:
        if isinstance(exc, CorpusError):
            raise
        raise CorpusError(f"Ошибка входных данных корпуса ({type(exc).__name__})") from exc

    parameters = {"algorithm": ALGORITHM, "seed": seed, "tune_share": tune_share}
    fingerprint_input = {
        "parameters": parameters,
        "files": [(p, hashlib.sha256(data).hexdigest()) for p, data in sorted(inputs.items())],
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_input, sort_keys=True).encode()).hexdigest()
    ordered = sorted(documents.values(), key=lambda doc: doc.key)
    report = {
        "parameters": parameters,
        "total": len(ordered),
        "in_scope": sum(doc.in_scope for doc in ordered),
        "links": len(seen_links),
        "link_sources": sum(bool(doc.gold["links"]) for doc in ordered),
        "distribution": distributions,
        "warnings": sorted(warnings, key=lambda w: (w["source"], w["target"])),
    }
    return PreparedCorpus(fingerprint, ordered, report)


def import_corpus(
    session: Session, root: Path, settings: Settings, seed: int = 2026, tune_share: float = 0.3
) -> dict[str, Any]:
    prepared = prepare_corpus(root, seed, tune_share, settings)
    with session.begin():
        # Serialize matching snapshots; DB uniqueness remains the final invariant.
        lock_key = int(prepared.fingerprint[:16], 16)
        if lock_key >= 2**63:
            lock_key -= 2**64
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
        version = session.scalar(
            select(CorpusVersion).where(CorpusVersion.fingerprint == prepared.fingerprint)
        )
        reused = version is not None
        if version is None:
            version = CorpusVersion(
                name=f"Корпус {prepared.fingerprint[:12]}",
                fingerprint=prepared.fingerprint,
                seed=seed,
                tune_share=tune_share,
                report=prepared.report,
            )
            session.add(version)
            session.flush()
            clients: dict[tuple[str, str], Client] = {}
            for item in prepared.documents:
                client_identity = item.part, item.client_key
                if client_identity not in clients:
                    namespace = f"corpus:{item.part}"
                    # Different versions can import concurrently and share namespace clients.
                    session.execute(
                        insert(Client)
                        .values(
                            namespace=namespace,
                            external_key=item.client_key,
                            name=item.client_key
                            if item.part == "real"
                            else f"Клиент {item.client_key}",
                            inn=item.client_key if item.part == "synth" else None,
                        )
                        .on_conflict_do_nothing(index_elements=["namespace", "external_key"])
                    )
                    client = session.scalar(
                        select(Client).where(
                            Client.namespace == namespace, Client.external_key == item.client_key
                        )
                    )
                    assert client is not None
                    clients[client_identity] = client
                blob = save_blob(
                    io.BytesIO(item.content), Path(settings.files_dir), settings.max_file_bytes
                )
                document = Document(
                    client_id=clients[client_identity].id,
                    corpus_version_id=version.id,
                    corpus_doc_id=item.key,
                    deal_id=item.deal,
                    split=item.split,
                    source="corpus",
                    filename=f"{item.key}.txt",
                    sha256=blob.sha256,
                    mime="text/plain",
                    storage_path=str(blob.path),
                    is_anonymized=True,
                    in_scope=item.in_scope,
                    gold=item.gold,
                )
                session.add(document)
                session.flush()
                if not process_text(session, document, settings):
                    raise CorpusError(f"Не удалось извлечь текст: {item.key}; импорт отменён")
        result = {"version_id": str(version.id), "reused": reused, **version.report}
    return result
