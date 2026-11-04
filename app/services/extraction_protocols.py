"""Selection-layer test history across related corpus versions; never model input."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models import CorpusVersion, Document


def test_history(session: Session, version: CorpusVersion) -> list[dict[str, Any]]:
    local, other = aliased(Document), aliased(Document)
    related = set(
        session.scalars(
            select(other.corpus_version_id)
            .join(
                local,
                (local.client_id == other.client_id) & (local.corpus_doc_id == other.corpus_doc_id),
            )
            .where(local.corpus_version_id == version.id, other.corpus_version_id.is_not(None))
            .distinct()
        )
    )
    pending = [version.id, *related]
    visited = set()
    history = []
    while pending:
        version_id = pending.pop()
        if version_id in visited:
            continue
        visited.add(version_id)
        row = session.get(CorpusVersion, version_id)
        if row is None:
            continue
        pending.extend(UUID(value) for value in row.report.get("extraction_source_versions", []))
        for key, protocol in row.report.get("extraction_protocols", {}).items():
            if protocol.get("test_started"):
                history.append({"corpus_version": str(row.id), "protocol_hash": key})
    return sorted(history, key=lambda item: (item["corpus_version"], item["protocol_hash"]))


def reused_test(history: list[dict[str, Any]], protocol_hash: str) -> bool:
    return any(item["protocol_hash"] != protocol_hash for item in history)
