"""Bounded ZIP admission before the existing transactional corpus importer."""

import stat
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from app.services.corpus_import import CorpusError

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 10000


@contextmanager
def unpack_corpus(stream: BinaryIO, max_file_bytes: int) -> Iterator[Path]:
    stream.seek(0, 2)
    if stream.tell() > MAX_ARCHIVE_BYTES:
        raise CorpusError("Архив превышает 100 МиБ")
    stream.seek(0)
    try:
        with tempfile.TemporaryDirectory(prefix="doclink-import-") as directory:
            root = Path(directory)
            with zipfile.ZipFile(stream) as archive:
                members = archive.infolist()
                if len(members) > MAX_MEMBERS:
                    raise CorpusError("Слишком много файлов в архиве")
                seen, total = set(), 0
                for info in members:
                    path = PurePosixPath(info.filename)
                    if (
                        "\\" in info.filename
                        or path.is_absolute()
                        or ".." in path.parts
                        or not path.parts
                        or stat.S_ISLNK(info.external_attr >> 16)
                        or info.flag_bits & 1
                    ):
                        raise CorpusError("Недопустимый путь или тип файла в архиве")
                    parts = path.parts[1:] if path.parts[0] == "corpus" else path.parts
                    if not parts and info.is_dir():
                        continue
                    if not parts or parts[0] not in {"real", "synth", "synth_extra"}:
                        raise CorpusError(
                            "Архив должен содержать real, synth и необязательный synth_extra"
                        )
                    relative = PurePosixPath(*parts)
                    if str(relative) in seen:
                        raise CorpusError("Повтор пути в архиве")
                    seen.add(str(relative))
                    if info.is_dir():
                        continue
                    if info.file_size > max_file_bytes or info.file_size < 0:
                        raise CorpusError("Превышен размер файла в архиве")
                    total += info.file_size
                    if total > MAX_UNPACKED_BYTES:
                        raise CorpusError("Распакованный корпус превышает 256 МиБ")
                    target = root.joinpath(*parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("xb") as destination:
                        written = 0
                        while chunk := source.read(min(65536, max_file_bytes - written + 1)):
                            written += len(chunk)
                            if written > max_file_bytes or written > info.file_size:
                                raise CorpusError("Превышен размер распакованного файла")
                            destination.write(chunk)
            yield root
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError) as exc:
        raise CorpusError("Повреждённый или неподдерживаемый ZIP-архив") from exc
