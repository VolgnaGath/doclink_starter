"""Content-addressed blobs; logical document identity belongs to the database."""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


class StorageError(ValueError):
    pass


@dataclass(frozen=True)
class Blob:
    sha256: str
    path: Path
    size: int


MIMES = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def document_mime(filename: str) -> str:
    try:
        return MIMES[Path(filename).suffix.lower()]
    except KeyError as exc:
        raise StorageError("Поддерживаются только PDF, DOCX и TXT") from exc


def save_blob(stream: BinaryIO, root: Path, max_bytes: int) -> Blob:
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".upload-", delete=False) as target:
            temporary = Path(target.name)
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise StorageError(f"Файл превышает лимит {max_bytes} байт")
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        sha256 = digest.hexdigest()
        path = root / sha256[:2] / sha256
        path.parent.mkdir(exist_ok=True)
        try:
            # Atomic publish without replacing a blob another writer already published.
            os.link(temporary, path)
        except FileExistsError:
            pass
        return Blob(sha256, path, size)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
