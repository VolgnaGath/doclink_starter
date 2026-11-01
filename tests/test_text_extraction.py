from pathlib import Path

import pypdfium2 as pdfium
import pytest
from docx import Document
from PIL import Image, ImageDraw, ImageFont

from app.config import Settings
from app.services.storage import document_mime
from app.services.text_extraction import TextExtractionError, extract_text


def make_pdf(path: Path, scan: bool = False, mixed: bool = False) -> None:
    """Use PDFium's own PDF writer, no additional test-only PDF dependency."""
    pdf = pdfium.PdfDocument.new()
    for is_scan in [False, True] if mixed else [scan]:
        page = pdf.new_page(600, 300)
        if is_scan:
            image = Image.new("RGB", (1200, 600), "white")
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 42)
            ImageDraw.Draw(image).text(
                (60, 80), "Договор оказания услуг номер 123", font=font, fill="black"
            )
            bitmap = pdfium.PdfBitmap.from_pil(image)
            obj = pdfium.PdfImage.new(pdf)
            obj.set_bitmap(bitmap)
            obj.set_matrix(pdfium.PdfMatrix(600, 0, 0, 300, 0, 0))
            page.insert_obj(obj)
        else:
            # A standard font and text object via the public raw bindings.
            raw = pdfium.raw
            obj = raw.FPDFPageObj_NewTextObj(pdf, b"Helvetica", 16)
            text = "Contract for services number 123, signed today".encode("utf-16-le") + b"\0\0"
            import ctypes

            raw.FPDFText_SetText(obj, ctypes.cast(text, raw.FPDF_WIDESTRING))
            raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 40, 180)
            raw.FPDFPage_InsertObject(page, obj)
        page.gen_content()
        page.close()
    pdf.save(path)
    pdf.close()


def test_txt_and_docx_order(tmp_path: Path, settings: Settings) -> None:
    path = tmp_path / "file.txt"
    path.write_text("Текст договора", encoding="utf-8")
    result = extract_text(path, document_mime(path.name), settings)
    assert (result.text, result.pages, result.ocr_pages) == ("Текст договора", 1, 0)
    doc = Document()
    doc.add_paragraph("Начало")
    doc.add_table(rows=1, cols=1).cell(0, 0).text = "Таблица"
    doc.add_paragraph("Конец")
    path = tmp_path / "file.docx"
    doc.save(path)
    assert extract_text(path, document_mime(path.name), settings).text == "Начало\nТаблица\nКонец"


@pytest.mark.parametrize("content", [b"", b" \n ", b"\xff", b"binary\0data"])
def test_invalid_txt(tmp_path: Path, settings: Settings, content: bytes) -> None:
    path = tmp_path / "file.txt"
    path.write_bytes(content)
    with pytest.raises(TextExtractionError):
        extract_text(path, "text/plain", settings)


def test_pdf_and_corruption(tmp_path: Path, settings: Settings) -> None:
    path = tmp_path / "file.pdf"
    make_pdf(path)
    result = extract_text(path, "application/pdf", settings)
    assert "Contract" in result.text and result.ocr_pages == 0
    path.write_bytes(b"%PDF-broken")
    with pytest.raises(TextExtractionError):
        extract_text(path, "application/pdf", settings)


@pytest.mark.parametrize("error", [RuntimeError("Tesseract timeout"), OSError("OCR unavailable")])
def test_ocr_failure_no_partial(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    path = tmp_path / "mixed.pdf"
    make_pdf(path, mixed=True)

    def fail(*args: object, **kwargs: object) -> str:
        raise error

    monkeypatch.setattr("app.services.text_extraction.pytesseract.image_to_string", fail)
    with pytest.raises(TextExtractionError):
        extract_text(path, "application/pdf", settings)


@pytest.mark.slow
def test_real_ocr_smoke(tmp_path: Path, settings: Settings) -> None:
    path = tmp_path / "mixed.pdf"
    make_pdf(path, mixed=True)
    result = extract_text(path, "application/pdf", settings)
    assert "Договор" in result.text and "Contract" in result.text
    assert (result.pages, result.ocr_pages) == (2, 1)


def test_resource_limits_and_invalid_docx(tmp_path: Path, settings: Settings) -> None:
    path = tmp_path / "mixed.pdf"
    make_pdf(path, mixed=True)
    settings.max_pdf_pages = 1
    with pytest.raises(TextExtractionError, match="лимит страниц"):
        extract_text(path, "application/pdf", settings)
    settings.max_pdf_pages = 100
    settings.max_render_pixels = 10
    with pytest.raises(TextExtractionError, match="лимит растеризации"):
        extract_text(path, "application/pdf", settings)
    docx = tmp_path / "broken.docx"
    docx.write_bytes(b"not a zip")
    with pytest.raises(TextExtractionError):
        extract_text(docx, document_mime(docx.name), settings)
    doc = Document()
    doc.add_paragraph("Text")
    doc.save(docx)
    settings.max_docx_uncompressed_bytes = 1
    with pytest.raises(TextExtractionError, match="лимит распакованного"):
        extract_text(docx, document_mime(docx.name), settings)
