import json
import shutil
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.corpus_addendum import audit
from app.models import Client, Document
from app.services.corpus_import import CorpusError, import_corpus, prepare_corpus

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
ARCHIVE = ROOT / "corpus_history/addendum_1-original.zip"


def test_approved_snapshot_and_legacy_fingerprint(settings: Settings) -> None:
    report = audit(CORPUS, ARCHIVE, settings)
    assert report == json.loads((ROOT / "corpus_history/addendum_1-audit.json").read_text())
    assert report["base_fingerprint"] == (
        "538864335f3b943a733a7242d0b3f3a6d3afd3d3c126ba5bbbf1acc2b3f5d8ab"
    )
    prepared = prepare_corpus(CORPUS, 2026, 0.3, settings)
    docs = {d.key: d for d in prepared.documents}
    for group in ((11, 13), (14, 15, 16), (17, 18, 19)):
        assert len({docs[f"synth_extra/extra_{n:03}/doc_01"].split for n in group}) == 1
        assert len({docs[f"synth_extra/extra_{n:03}/doc_01"].deal for n in group}) == 1
    extra = [d for d in prepared.documents if d.part == "synth_extra"]
    assert sum(d.gold["fields"]["kind"] != "договор" for d in extra) == 15
    assert sum(d.gold["case"] == "no_target" and not d.gold["links"] for d in extra) == 6
    for doc in extra:
        assert doc.gold["expected"] in {"разбор", "не рекомендация", "—"}
    assert "11.03.2025" not in docs["synth_extra/extra_016/doc_01"].content.decode()
    assert "3005/24" in docs["synth_extra/extra_008/doc_01"].content.decode()


@pytest.mark.parametrize("problem", ["target", "client", "case", "expected", "missing"])
def test_extra_validation(tmp_path: Path, settings: Settings, problem: str) -> None:
    root = tmp_path / "corpus"
    shutil.copytree(CORPUS, root)
    path = root / "synth_extra/documents.csv"
    if problem == "target":
        path = root / "synth_extra/links.csv"
        path.write_text(path.read_text(encoding="utf-8-sig").replace("synth_x039", "synth_missing"))
    elif problem == "missing":
        (root / "synth_extra/links.csv").unlink()
    else:
        old, new = {
            "client": (";7978809321;", ";123;"),
            "case": (";no_target;", ";unknown;"),
            "expected": (";не рекомендация", ";рекомендация"),
        }[problem]
        path.write_text(path.read_text(encoding="utf-8-sig").replace(old, new, 1))
    with pytest.raises(CorpusError):
        prepare_corpus(root, 2026, 0.3, settings)


def test_audit_rejects_unapproved_change(tmp_path: Path, settings: Settings) -> None:
    root = tmp_path / "corpus"
    shutil.copytree(CORPUS, root)
    path = root / "synth_extra/extra_001/doc_01.txt"
    path.write_text(path.read_text() + "\nизменение")
    with pytest.raises(CorpusError, match="согласованной"):
        audit(root, ARCHIVE, settings)


def test_shared_clients_and_distinct_versions(db_session: Session, settings: Settings) -> None:
    base = import_corpus(db_session, CORPUS, settings, include_extra=False)
    extended = import_corpus(db_session, CORPUS, settings)
    assert base["version_id"] != extended["version_id"]
    assert base["total"] == 233 and extended["total"] == 252
    docs = list(
        db_session.scalars(
            select(Document).where(Document.corpus_version_id == UUID(extended["version_id"]))
        )
    )
    by_key = {d.corpus_doc_id: d for d in docs}
    for doc in docs:
        if doc.corpus_doc_id.startswith("synth_extra/"):
            client = db_session.get(Client, doc.client_id)
            assert client.namespace == "corpus:synth" and client.inn
            assert doc.doc_kind is None and doc.date is None and doc.counterparty_id is None
            for link in doc.gold["links"]:
                assert by_key[link["target"]].client_id == doc.client_id
    db_session.commit()
    assert import_corpus(db_session, CORPUS, settings)["reused"]
