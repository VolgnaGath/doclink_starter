"""Bounded HTTP transport. Errors never include response bodies, paths or headers."""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from tempfile import SpooledTemporaryFile
from typing import Any, BinaryIO

import httpx

from app.config import Settings
from app.services.model_calls import ProcessingError
from mock1c.protocol import InboxDocument

PREFIX = "/odata/standard.odata"


class ExchangeError(ProcessingError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class Mock1CClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.http = httpx.Client(
            base_url=settings.mock1c_url,
            timeout=settings.mock1c_timeout_seconds,
            follow_redirects=False,
        )

    def __enter__(self) -> "Mock1CClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.http.close()

    @staticmethod
    def check(response: httpx.Response) -> None:
        if not response.is_success:
            raise ExchangeError(f"mock1c: HTTP {response.status_code}", response.status_code)

    def catalog(self, since: str) -> list[InboxDocument]:
        try:
            response = self.http.get(
                PREFIX + "/Catalog_Документы", params={"$filter": f"Дата gt datetime'{since}'"}
            )
            self.check(response)
            rows = response.json()["value"]
            if not isinstance(rows, list):
                raise ValueError
            result = []
            for row in rows:
                wire = dict(row)
                date = wire.pop("Дата")
                doc = InboxDocument.model_validate(wire)
                if not doc.sha256 or date != doc.registered_at.isoformat():
                    # datetime may use Z on the wire, compare instants instead.
                    from datetime import datetime

                    if not doc.sha256 or datetime.fromisoformat(date) != doc.registered_at:
                        raise ValueError
                result.append(doc)
            if len({d.id for d in result}) != len(result):
                raise ValueError
            return result
        except httpx.HTTPError as exc:
            raise ExchangeError(f"mock1c: {type(exc).__name__}") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ExchangeError("mock1c: некорректный каталог", response.status_code) from exc

    @contextmanager
    def file(self, doc: InboxDocument) -> Iterator[BinaryIO]:
        try:
            with SpooledTemporaryFile(max_size=1024 * 1024) as stream:
                digest, size = hashlib.sha256(), 0
                with self.http.stream("GET", PREFIX + f"/Файл/{doc.id}") as response:
                    self.check(response)
                    for block in response.iter_bytes():
                        size += len(block)
                        if size > self.settings.max_file_bytes:
                            raise ExchangeError(
                                "mock1c: файл превышает лимит", response.status_code
                            )
                        stream.write(block)
                        digest.update(block)
                if digest.hexdigest() != doc.sha256:
                    raise ExchangeError("mock1c: SHA-256 файла не совпал", 200)
                stream.seek(0)
                yield stream
        except httpx.HTTPError as exc:
            raise ExchangeError(f"mock1c: {type(exc).__name__}") from exc

    def send(self, payload: dict[str, Any]) -> int:
        try:
            response = self.http.post(PREFIX + "/РегистрСведений_СвязиДокументов", json=payload)
            self.check(response)
            receipt = response.json()
            if (
                receipt.get("accepted") is not True
                or receipt.get("event_id") != payload["event_id"]
            ):
                raise ValueError
            return response.status_code
        except httpx.HTTPError as exc:
            raise ExchangeError(f"mock1c: {type(exc).__name__}") from exc
        except (ValueError, TypeError, AttributeError) as exc:
            raise ExchangeError("mock1c: некорректная квитанция", response.status_code) from exc
