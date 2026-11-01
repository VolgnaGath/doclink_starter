import hashlib
from io import BytesIO
from pathlib import Path

import pytest

from app.services.storage import StorageError, document_mime, save_blob


def test_atomic_dedup_and_limits(tmp_path: Path) -> None:
    content = b"same content"
    blob = save_blob(BytesIO(content), tmp_path, 100)
    assert blob.sha256 == hashlib.sha256(content).hexdigest()
    assert blob.path.read_bytes() == content
    assert save_blob(BytesIO(content), tmp_path, 100) == blob
    with pytest.raises(StorageError):
        save_blob(BytesIO(b"too large"), tmp_path, 1)
    assert not list(tmp_path.glob(".upload-*"))
    assert document_mime("../../file.PDF") == "application/pdf"
    with pytest.raises(StorageError):
        document_mime("file.exe")


def test_failed_stream_cleans_temporary(tmp_path: Path) -> None:
    class BrokenStream(BytesIO):
        def read(self, size: int = -1) -> bytes:
            raise OSError("broken stream")

    with pytest.raises(OSError):
        save_blob(BrokenStream(), tmp_path, 100)
    assert list(tmp_path.iterdir()) == []
