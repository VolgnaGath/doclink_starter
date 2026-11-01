import json
import shutil
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.corpus_revision import archived_corpus, audit, snapshot
from app.models import Document, DocumentText
from app.services.corpus_import import CorpusError, import_corpus

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
ARCHIVE = ROOT / "corpus_history/v1-original.zip"


def test_revision_matches_approved_edits_and_preserves_original(settings: Settings) -> None:
    report = audit(CORPUS, ARCHIVE, settings)
    assert report == json.loads((ROOT / "corpus_history/v2-audit.json").read_text())
    assert len(report["original"]["warnings"]) == 10
    assert report["current"]["warnings"] == []
    assert len(report["changed_files"]) == 7
    assert report["split_unchanged"]
    assert report["original"]["fingerprint"] != report["current"]["fingerprint"]


def test_audit_rejects_unapproved_text_edits(tmp_path: Path, settings: Settings) -> None:
    root = tmp_path / "corpus"
    shutil.copytree(CORPUS, root)
    path = root / "synth/synth_010/doc_07.txt"
    path.write_text(path.read_text().replace("1 500 000,00", "1 600 000,00"))
    with pytest.raises(CorpusError, match="согласованной"):
        audit(root, ARCHIVE, settings)


def test_original_snapshot_is_reproducible_and_cannot_be_overwritten(
    tmp_path: Path, settings: Settings
) -> None:
    copy = tmp_path / "copy.zip"
    with archived_corpus(ARCHIVE) as original:
        snapshot(original, copy, settings)
        assert copy.read_bytes() == ARCHIVE.read_bytes()
        with pytest.raises(CorpusError, match="перезапись запрещена"):
            snapshot(original, copy, settings)


def test_import_revision_creates_distinct_version_without_rewriting_original(
    db_session: Session, settings: Settings
) -> None:
    with archived_corpus(ARCHIVE) as original:
        before = import_corpus(db_session, original, settings)
    after = import_corpus(db_session, CORPUS, settings)
    assert before["version_id"] != after["version_id"]
    assert before["links"] == after["links"] == 156
    assert len(before["warnings"]) == 10 and after["warnings"] == []
    documents = [
        db_session.scalar(
            select(Document).where(
                Document.corpus_version_id == UUID(report["version_id"]),
                Document.corpus_doc_id == "synth/synth_010/doc_07",
            )
        )
        for report in (before, after)
    ]
    assert documents[0].gold["fields"]["number"] == "642-ОК/2026"
    assert documents[1].gold["fields"]["number"] == "642-ОК/2025"
    assert documents[0].sha256 != documents[1].sha256
    for doc in documents:
        assert doc.doc_kind is None and doc.date is None and doc.number is None
        text = db_session.get(DocumentText, doc.id).text
        assert doc.gold["fields"]["number"] in text
    db_session.commit()
    repeat = import_corpus(db_session, CORPUS, settings)
    assert repeat["version_id"] == after["version_id"] and repeat["reused"]
