from pathlib import Path

import pytest

from app.config import Settings
from app.services.corpus_import import CorpusError, prepare_corpus, split_deals

CORPUS = Path(__file__).resolve().parents[1] / "corpus"


def test_split_independent_of_order_and_exact_groups() -> None:
    deals = [f"deal_{i}" for i in range(78)]
    result = split_deals(deals, 2026, 0.3)
    assert result == split_deals(deals[::-1] + deals, 2026, 0.3)
    assert sum(value == "tune" for value in result.values()) == 23
    assert result != split_deals(deals, 2027, 0.3)
    with pytest.raises(CorpusError):
        split_deals(deals, 2026, float("nan"))


def test_full_corpus_gold_and_report(settings: Settings) -> None:
    corpus = prepare_corpus(CORPUS, 2026, 0.3, settings)
    report = corpus.report
    assert (report["total"], report["in_scope"], report["links"], report["link_sources"]) == (
        252,
        250,
        171,
        147,
    )
    assert report["distribution"]["real"]["tune"]["deals"] == 2
    assert report["distribution"]["synth"]["tune"]["deals"] == 23
    assert report["warnings"] == []
    documents = {doc.key: doc for doc in corpus.documents}
    for doc in corpus.documents:
        assert (
            doc.split
            == split_deals([d.deal for d in corpus.documents if d.part == doc.part], 2026, 0.3)[
                doc.deal
            ]
        )
        for link in doc.gold["links"]:
            assert documents[link["target"]].gold["fields"]["kind"] == "договор"
    real = documents["real/deal_001/doc_01"].gold
    assert real["fields"]["date"] is None and "date" in real["annotated_fields"]
    assert "cp_inn" not in real["annotated_fields"]
    assert "manifest" in real["provenance"]
    assert len(documents["synth/synth_001/doc_01"].gold["annotated_fields"]) == 7


def test_snapshot_path_independence_and_validation(tmp_path: Path, settings: Settings) -> None:
    import shutil

    shutil.copytree(CORPUS, tmp_path / "copy")
    copied = tmp_path / "copy"
    expected = prepare_corpus(CORPUS, 2026, 0.3, settings)
    assert prepare_corpus(copied, 2026, 0.3, settings).fingerprint == expected.fingerprint
    path = copied / "synth/documents.csv"
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], *reversed(lines[1:])]) + "\n")
    reordered = prepare_corpus(copied, 2026, 0.3, settings)
    assert {d.key: d.split for d in reordered.documents} == {
        d.key: d.split for d in expected.documents
    }
    assert reordered.fingerprint != expected.fingerprint
    (copied / "real/deal_001/doc_01.txt").unlink()
    with pytest.raises(CorpusError):
        prepare_corpus(copied, 2026, 0.3, settings)
