"""Persistent mock journal: receipt and effective link state share one transaction."""

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from mock1c.protocol import InboxDocument, LinkEvent


def catalog(root: Path) -> list[InboxDocument]:
    result = []
    for sidecar in sorted(root.glob("*.json")):
        if sidecar.is_symlink():
            raise ValueError("Symlinks are not allowed")
        doc = InboxDocument.model_validate_json(sidecar.read_bytes())
        path = root / doc.filename
        if sidecar.name != doc.filename + ".json" or path.is_symlink():
            raise ValueError("Invalid inbox path")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        actual = digest.hexdigest()
        if doc.sha256 and doc.sha256 != actual:
            raise ValueError("Published content hash mismatch")
        result.append(doc.model_copy(update={"sha256": actual}))
    if len({d.id for d in result}) != len(result):
        raise ValueError("Duplicate external ID")
    return sorted(result, key=lambda d: (d.registered_at, str(d.id)))


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=10)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS links (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS published (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    return connection


def receive(path: Path, event: LinkEvent, docs: list[InboxDocument]) -> bool:
    payload = json.dumps(event.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT payload FROM events WHERE id=?", (str(event.event_id),)).fetchone()
        if old:
            if old[0] != payload:
                raise ValueError("Event ID payload conflict")
            return True
        by_id = {d.id: d for d in docs}
        source, target = by_id.get(event.source_id), by_id.get(event.target_id)
        if source is None or target is None or source.client_id != target.client_id:
            raise ValueError("Unknown or cross-client endpoints")
        if event.source_id == event.target_id:
            raise ValueError("Self link")
        previous = db.execute(
            "SELECT payload FROM links WHERE id=?", (str(event.link_id),)
        ).fetchone()
        if event.operation == "create":
            if previous:
                raise ValueError("Link already exists")
            db.execute("INSERT INTO links VALUES (?,?)", (str(event.link_id), payload))
        else:
            if not previous:
                raise ValueError("Create must precede remove")
            created = json.loads(previous[0])
            if any(
                created[key] != event.model_dump(mode="json")[key]
                for key in ("source_id", "target_id", "link_type")
            ):
                raise ValueError("Removal endpoints conflict")
            db.execute("DELETE FROM links WHERE id=?", (str(event.link_id),))
        db.execute("INSERT INTO events VALUES (?,?)", (str(event.event_id), payload))
    return False


def journal(path: Path) -> dict[str, Any]:
    with closing(connect(path)) as db, db:
        return {
            name: [
                json.loads(row[0])
                for row in db.execute(f"SELECT payload FROM {name} ORDER BY rowid")
            ]
            for name in ("events", "links")
        }


def validate_publications(path: Path, docs: list[InboxDocument]) -> None:
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        for doc in docs:
            payload = json.dumps(doc.model_dump(mode="json"), sort_keys=True)
            old = db.execute("SELECT payload FROM published WHERE id=?", (str(doc.id),)).fetchone()
            if old and old[0] != payload:
                raise ValueError("Published document changed")
            db.execute("INSERT OR IGNORE INTO published VALUES (?,?)", (str(doc.id), payload))
