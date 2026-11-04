"""Portable real experiment artifacts, FK closure, no anonymization maps or credentials."""

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Table, and_, func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base, get_session
from app.models import ExperimentRun, ExperimentSeries
from app.services.model_calls import fingerprint
from app.services.storage import save_blob

TABLES = {
    "clients",
    "counterparties",
    "corpus_versions",
    "documents",
    "document_texts",
    "model_configs",
    "prompts",
    "llm_calls",
    "extractions",
    "jobs",
    "embeddings",
    "document_embeddings",
    "stage_timings",
    "linking_results",
    "link_suggestions",
    "experiment_series",
    "experiment_runs",
    "experiment_metrics",
    "threshold_curves",
    "experiment_results",
    "experiment_artifacts",
}


def json_value(value: Any) -> Any:
    if isinstance(value, (UUID, Decimal, date, datetime)):
        return str(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(type(value).__name__)


def export_bundle(session: Session, series_id: UUID) -> dict[str, Any]:
    series = session.get(ExperimentSeries, series_id)
    if series is None or series.status != "done":
        raise ValueError("Нужна завершённая серия")
    collected: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = {n: {} for n in TABLES}
    pending: list[tuple[Table, dict[str, Any]]] = []

    def collect(table: Table, row: dict[str, Any]) -> None:
        if table.name not in TABLES:
            raise ValueError(f"Запрещённая зависимость {table.name}")
        key = tuple(row[c.name] for c in table.primary_key)
        if key not in collected[table.name]:
            collected[table.name][key] = row
            pending.append((table, row))

    run_ids = list(
        session.scalars(select(ExperimentRun.id).where(ExperimentRun.series_id == series_id))
    )
    for name in TABLES:
        table = Base.metadata.tables[name]
        condition = None
        if name == "experiment_series":
            condition = table.c.id == series_id
        elif name == "experiment_runs":
            condition = table.c.series_id == series_id
        elif name.startswith("experiment_") or name == "threshold_curves":
            condition = table.c.run_id.in_(run_ids)
        elif name == "documents":
            condition = table.c.corpus_version_id == series.corpus_version_id
        if condition is not None:
            for row in session.execute(select(table).where(condition)).mappings():
                collect(table, dict(row))

    while True:
        while pending:
            table, row = pending.pop()
            for foreign in table.foreign_keys:
                value = row[foreign.parent.name]
                if value is None:
                    continue
                target = foreign.column.table
                if target.name not in TABLES:
                    raise ValueError(f"Непереносимая зависимость {target.name}")
                if len(target.primary_key) == 1 and (value,) in collected[target.name]:
                    continue
                parent = (
                    session.execute(select(target).where(foreign.column == value)).mappings().one()
                )
                collect(target, dict(parent))
        previous = sum(len(rows) for rows in collected.values())
        # Inverse children needed to replay a real method, not merely display its report.
        for name, column, parent in (
            ("document_texts", "document_id", "documents"),
            ("document_embeddings", "document_id", "documents"),
            ("extractions", "document_id", "documents"),
            ("link_suggestions", "result_id", "linking_results"),
        ):
            table = Base.metadata.tables[name]
            ids = [key[0] for key in collected[parent]]
            for row in session.execute(select(table).where(table.c[column].in_(ids))).mappings():
                collect(table, dict(row))
        if sum(len(rows) for rows in collected.values()) == previous:
            break
    documents = list(collected["documents"].values())
    if any(not d["is_anonymized"] or d["source"] != "corpus" for d in documents):
        raise ValueError("Перенос разрешён только для обезличенного корпуса")
    if any(j["status"] in {"queued", "running"} for j in collected["jobs"].values()):
        raise ValueError("В происхождении есть активные задания")
    blobs = {}
    for doc in documents:
        raw = Path(doc["storage_path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != doc["sha256"]:
            raise ValueError("Хеш исходного файла не совпадает")
        blobs[doc["sha256"]] = base64.b64encode(raw).decode("ascii")
    data = {
        "version": "delivery-artifacts-v1",
        "series_id": str(series.id),
        "corpus_version_id": str(series.corpus_version_id),
        "dataset_hash": series.dataset["dataset_hash"],
        "tables": {
            name: sorted(
                rows.values(),
                key=lambda r: str(tuple(r[c.name] for c in Base.metadata.tables[name].primary_key)),
            )
            for name, rows in sorted(collected.items())
        },
        "blobs": dict(sorted(blobs.items())),
    }
    portable = json.loads(json.dumps(data, default=json_value))
    # Replace machine-specific file locations with content-addressed identities.
    for doc in portable["tables"]["documents"]:
        doc["storage_path"] = doc["sha256"]
    return {**portable, "bundle_hash": fingerprint(portable)}


def decode(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for c in table.columns:
        value = row[c.name]
        if value is None:
            continue
        try:
            kind = c.type.python_type
        except NotImplementedError:
            continue
        if kind is UUID:
            result[c.name] = UUID(value)
        elif kind is datetime:
            result[c.name] = datetime.fromisoformat(value)
        elif kind is date:
            result[c.name] = date.fromisoformat(value)
        elif kind is Decimal:
            result[c.name] = Decimal(value)
    return result


def restore_bundle(session: Session, bundle: dict[str, Any], files_dir: Path) -> dict[str, Any]:
    if os.environ.get("DELIVERY_ACCEPTANCE_ENABLED") != "1":
        raise ValueError("Восстановление — только на отдельном стенде сдачи")
    if set(bundle["tables"]) != TABLES or bundle["version"] != "delivery-artifacts-v1":
        raise ValueError("Неподдерживаемый состав пакета")
    if (
        fingerprint({k: v for k, v in bundle.items() if k != "bundle_hash"})
        != bundle["bundle_hash"]
    ):
        raise ValueError("Хеш пакета не совпадает")
    for name in TABLES:
        if session.scalar(select(func.count()).select_from(Base.metadata.tables[name])):
            raise ValueError("Нужна пустая БД после миграций, без seed")
    paths = {}
    for digest, encoded in bundle["blobs"].items():
        raw = base64.b64decode(encoded, validate=True)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Повреждён файл пакета")
        blob = save_blob(io.BytesIO(raw), files_dir, len(raw))
        if os.geteuid() == 0:
            os.chown(blob.path, 10001, 10001)
            os.chown(blob.path.parent, 10001, 10001)
        paths[digest] = str(blob.path)
    tables = {n: Base.metadata.tables[n] for n in TABLES}
    deferred_columns = {
        ("documents", "card_extraction_id"),
        ("extractions", "source_extraction_id"),
    }
    inserted: set[str] = set()
    deferred = []
    while len(inserted) < len(tables):
        ready = [
            t
            for n, t in sorted(tables.items())
            if n not in inserted
            and all(
                f.column.table.name in inserted or f.column.table.name == n
                for f in t.foreign_keys
                if (t.name, f.parent.name) not in deferred_columns
            )
        ]
        if not ready:
            raise ValueError("Неустранимый цикл обязательных FK")
        for table in ready:
            rows = []
            for raw in bundle["tables"][table.name]:
                row = decode(table, raw)
                if table.name == "documents":
                    if not row["is_anonymized"] or row["source"] != "corpus":
                        raise ValueError("В пакете не обезличенный корпус")
                    row["storage_path"] = paths[row["sha256"]]
                later = {
                    f.parent.name: row[f.parent.name]
                    for f in table.foreign_keys
                    if (table.name, f.parent.name) in deferred_columns
                    and row[f.parent.name] is not None
                }
                if later:
                    deferred.append(
                        (table, {c.name: row[c.name] for c in table.primary_key}, later)
                    )
                rows.append({k: None if k in later else v for k, v in row.items()})
            if rows:
                session.execute(table.insert(), rows)
            inserted.add(table.name)
    for table, key, later in deferred:
        session.execute(
            update(table).where(and_(*(table.c[k] == v for k, v in key.items()))).values(**later)
        )
    # Explicit IDs must not leave identity/serial sequences behind restored rows.
    from sqlalchemy import text

    for table in tables.values():
        for c in table.primary_key:
            if c.autoincrement is True or (c.autoincrement == "auto" and str(c.type) == "INTEGER"):
                session.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', '{c.name}'), "
                        f"COALESCE((SELECT max({c.name}) FROM {table.name}), 1), "
                        f"EXISTS(SELECT 1 FROM {table.name})) "
                        f"WHERE pg_get_serial_sequence('{table.name}', '{c.name}') IS NOT NULL"
                    )
                )
    return {
        "bundle_hash": bundle["bundle_hash"],
        "dataset_hash": bundle["dataset_hash"],
        "counts": {n: len(r) for n, r in bundle["tables"].items()},
        "new_llm_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["export", "restore"])
    parser.add_argument("--series", type=UUID)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings()
    with get_session(settings.database_url) as session:
        if args.command == "export":
            bundle = export_bundle(session, args.series)
            args.file.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(args.file, "wt", encoding="utf-8") as out:
                json.dump(bundle, out, ensure_ascii=False, sort_keys=True)
            report = {
                "bundle_hash": bundle["bundle_hash"],
                "dataset_hash": bundle["dataset_hash"],
                "series_id": bundle["series_id"],
                "counts": {n: len(r) for n, r in bundle["tables"].items()},
            }
        else:
            with gzip.open(args.file, "rt", encoding="utf-8") as source:
                bundle = json.load(source)
            report = restore_bundle(session, bundle, Path(settings.files_dir))
            session.commit()
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
