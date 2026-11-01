import math
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
import pytesseract
from docx import Document as open_docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Document, DocumentText, StageTiming


class TextExtractionError(ValueError):
    """A complete text result could not be produced; partial text is not success."""


@dataclass(frozen=True)
class TextResult:
    text: str
    pages: int
    ocr_pages: int

    @property
    def chars(self) -> int:
        return len(self.text)


def extract_pdf(path: Path, settings: Settings) -> TextResult:
    parts: list[str] = []
    ocr_pages = 0
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) > settings.max_pdf_pages:
            raise TextExtractionError("Превышен лимит страниц PDF")
        for index, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if len(text.strip()) < 30:
                with pdfium.PdfDocument(path) as raster:
                    raster_page = raster[index]
                    try:
                        scale = settings.ocr_dpi / 72
                        width, height = raster_page.get_size()
                        pixels = math.ceil(width * scale) * math.ceil(height * scale)
                        if pixels > settings.max_render_pixels:
                            raise TextExtractionError("Превышен лимит растеризации PDF")
                        bitmap = raster_page.render(scale=scale)
                        try:
                            image = bitmap.to_pil()
                            try:
                                text = pytesseract.image_to_string(
                                    image, lang="rus", timeout=settings.ocr_timeout_seconds
                                )
                            finally:
                                image.close()
                        finally:
                            bitmap.close()
                    finally:
                        raster_page.close()
                ocr_pages += 1
            parts.append(text)
    return TextResult("\n\n".join(parts).strip(), len(parts), ocr_pages)


def extract_text(path: Path, mime: str, settings: Settings) -> TextResult:
    try:
        if path.stat().st_size > settings.max_file_bytes:
            raise TextExtractionError("Превышен лимит размера файла")
        if mime == "text/plain":
            text = path.read_text(encoding="utf-8-sig")
            if "\x00" in text:
                raise TextExtractionError("TXT содержит двоичные данные")
            result = TextResult(text.strip(), 1, 0)
        elif mime == "application/pdf":
            with path.open("rb") as stream:
                if not stream.read(1024).lstrip().startswith(b"%PDF-"):
                    raise TextExtractionError("Содержимое файла не является PDF")
            result = extract_pdf(path, settings)
        elif mime.endswith("wordprocessingml.document"):
            with zipfile.ZipFile(path) as archive:
                if sum(i.file_size for i in archive.infolist()) > (
                    settings.max_docx_uncompressed_bytes
                ):
                    raise TextExtractionError("Превышен лимит распакованного DOCX")
            doc = open_docx(path)
            parts = []
            for block in doc.iter_inner_content():
                if isinstance(block, Paragraph):
                    parts.append(block.text)
                elif isinstance(block, Table):
                    parts.extend("\t".join(cell.text for cell in row.cells) for row in block.rows)
            result = TextResult("\n".join(parts).strip(), 1, 0)
        else:
            raise TextExtractionError("Неподдерживаемый формат")
        if not result.text:
            raise TextExtractionError("Извлечённый текст пуст")
        return result
    except TextExtractionError:
        raise
    except Exception as exc:
        # Do not persist library exception strings: they may include document text or paths.
        reason = "Некорректный файл или сбой извлечения"
        if isinstance(exc, UnicodeError):
            reason = "TXT должен быть в кодировке UTF-8"
        elif isinstance(exc, RuntimeError) and "timeout" in str(exc).lower():
            reason = "Истекло время OCR"
        raise TextExtractionError(f"{reason} ({type(exc).__name__})") from exc


def process_text(session: Session, document: Document, settings: Settings) -> bool:
    started_at = datetime.now(UTC)
    started = time.monotonic()
    error = None
    try:
        result = extract_text(Path(document.storage_path), document.mime, settings)
    except TextExtractionError as exc:
        error = str(exc)
        document.status = "error"
    else:
        saved = session.get(DocumentText, document.id)
        if saved is None:
            saved = DocumentText(document_id=document.id)
            session.add(saved)
        saved.text = result.text
        saved.pages = result.pages
        saved.chars = result.chars
        saved.ocr_pages = result.ocr_pages
        saved.anonymized_text = None
        document.status = "text"
    session.add(
        StageTiming(
            document_id=document.id,
            stage="text",
            duration_ms=max(0, round((time.monotonic() - started) * 1000)),
            ok=error is None,
            error=error,
            started_at=started_at,
        )
    )
    session.flush()
    return error is None
