from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Client, CorpusVersion, Counterparty, Document, DocumentText, StageTiming
from app.services.corpus_import import CorpusError, import_corpus, prepare_corpus

CORPUS = Path(__file__).resolve().parents[1] / "corpus"


def test_import_full_gold_idempotency_and_new_seed(db_session: Session, settings: Settings) -> None:
    first = import_corpus(db_session, CORPUS, settings)
    second = import_corpus(db_session, CORPUS, settings)
    assert first["version_id"] == second["version_id"] and second["reused"]
    assert db_session.scalar(select(func.count()).select_from(Document)) == 233
    assert db_session.scalar(select(func.count()).select_from(DocumentText)) == 233
    assert db_session.scalar(select(func.count()).select_from(StageTiming)) == 233
    assert db_session.scalar(select(func.count()).select_from(Counterparty)) == 0
    count = db_session.scalar(
        text(
            "SELECT SUM(jsonb_array_length(gold->'links')) FROM documents "
            "WHERE corpus_version_id = :version"
        ),
        {"version": first["version_id"]},
    )
    assert count == 156
    prepared = prepare_corpus(CORPUS, 2026, 0.3, settings)
    expected = {d.key: d.gold for d in prepared.documents}
    for document in db_session.scalars(select(Document)):
        assert document.gold == expected[document.corpus_doc_id]
        assert document.doc_kind is None and document.date is None
        assert document.number is None and document.counterparty_id is None
        assert document.status == "text" and document.is_anonymized
    real = db_session.scalar(
        select(Document).where(Document.corpus_doc_id == "real/deal_001/doc_02")
    )
    assert db_session.get(DocumentText, real.id).pages == 1  # not historical manifest pages
    db_session.commit()
    changed = import_corpus(db_session, CORPUS, settings, seed=2027)
    assert changed["version_id"] != first["version_id"]
    assert db_session.scalar(select(func.count()).select_from(Document)) == 466
    assert len(list(Path(settings.files_dir).glob("*/*"))) <= 233


def test_import_rolls_back_all_rows(
    db_session: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.corpus_import import process_text

    attempts = 0

    def fail_later(session: Session, doc: Document, settings: Settings) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 3:
            return False
        return process_text(session, doc, settings)

    monkeypatch.setattr("app.services.corpus_import.process_text", fail_later)
    with pytest.raises(CorpusError):
        import_corpus(db_session, CORPUS, settings)
    for model in (CorpusVersion, Client, Document, DocumentText, StageTiming):
        assert db_session.scalar(select(func.count()).select_from(model)) == 0
    assert list(Path(settings.files_dir).glob("*/*"))  # shared blobs survive rollback


@pytest.mark.parametrize("problem", ["target", "duplicate", "kind", "date", "scope"])
def test_structural_errors_before_write(tmp_path: Path, settings: Settings, problem: str) -> None:
    import shutil

    shutil.copytree(CORPUS, tmp_path / "corpus")
    root = tmp_path / "corpus"
    path = root / "real/documents.csv"
    content = path.read_text()
    if problem == "target":
        path = root / "real/links.csv"
        content = path.read_text().replace("deal_001/doc_02", "deal_001/missing")
    elif problem == "duplicate":
        content += content.splitlines()[1] + "\n"
    elif problem == "kind":
        content = content.replace("договор", "неизвестно", 1)
    elif problem == "date":
        content = content.replace("02.07.2026", "32.07.2026", 1)
    else:
        content = content.replace(";1;;", ";yes;;", 1)
    path.write_text(content)
    with pytest.raises(CorpusError):
        prepare_corpus(root, 2026, 0.3, settings)
