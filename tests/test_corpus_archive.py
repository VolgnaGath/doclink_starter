import stat
import zipfile
from io import BytesIO

import pytest

from app.services.corpus_archive import unpack_corpus
from app.services.corpus_import import CorpusError


def archive(entries: list[tuple[str | zipfile.ZipInfo, bytes]]) -> BytesIO:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as zipped:
        for name, content in entries:
            zipped.writestr(name, content)
    stream.seek(0)
    return stream


@pytest.mark.parametrize(
    "name",
    [
        "../escape.txt",
        "/real/escape.txt",
        "real/../escape.txt",
        "real\\escape.txt",
        "other/file.txt",
    ],
)
def test_reject_archive_paths(name: str) -> None:
    with pytest.raises(CorpusError):
        with unpack_corpus(archive([(name, b"x")]), 100):
            pytest.fail("unsafe archive accepted")


def test_reject_archive_symlink_and_duplicate() -> None:
    info = zipfile.ZipInfo("real/link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(CorpusError):
        with unpack_corpus(archive([(info, b"/tmp/escape")]), 100):
            pytest.fail("symlink accepted")
    with pytest.warns(UserWarning), pytest.raises(CorpusError, match="Повтор"):
        with unpack_corpus(archive([("real/a.txt", b"a"), ("real/a.txt", b"b")]), 100):
            pytest.fail("duplicate accepted")


def test_archive_limits_and_valid_root(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(CorpusError, match="размер"):
        with unpack_corpus(archive([("real/a.txt", b"too long")]), 2):
            pytest.fail("large member accepted")
    monkeypatch.setattr("app.services.corpus_archive.MAX_UNPACKED_BYTES", 3)
    with pytest.raises(CorpusError, match="Распакованный"):
        with unpack_corpus(archive([("real/a.txt", b"aa"), ("synth/b.txt", b"bb")]), 100):
            pytest.fail("archive bomb accepted")
    with unpack_corpus(
        archive([("corpus/real/a.txt", b"a"), ("corpus/synth/b.txt", b"b")]), 100
    ) as root:
        assert (root / "real/a.txt").read_text() == "a"
        saved = root
    assert not saved.exists()


def test_broken_zip() -> None:
    with pytest.raises(CorpusError, match="ZIP"):
        with unpack_corpus(BytesIO(b"not a zip"), 100):
            pytest.fail("broken zip accepted")
