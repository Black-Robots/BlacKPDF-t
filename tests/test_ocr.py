import pymupdf
import pytest

from blackpdf.core.errors import OcrUnavailableError
from blackpdf.core.ocr import available_languages, ocr_available, ocr_pdf, page_has_text


def _scanned_pdf(path, text="Factura numero 2024 del cliente"):
    """PDF con una página que solo contiene la imagen de un texto (como un escaneo)."""
    source = pymupdf.open()
    page = source.new_page(width=420, height=200)
    page.insert_text((30, 100), text, fontsize=20)
    pix = page.get_pixmap(dpi=200)
    scanned = pymupdf.open()
    target = scanned.new_page(width=420, height=200)
    target.insert_image(target.rect, pixmap=pix)
    target2 = scanned.new_page(width=420, height=200)
    target2.insert_text((30, 60), "Esta pagina ya tiene texto digital suficiente", fontsize=12)
    scanned.save(path)
    return path


def test_bundled_languages_are_found():
    languages = available_languages()
    assert "spa" in languages and "eng" in languages
    assert ocr_available()


def test_ocr_makes_scans_searchable(tmp_path):
    source = _scanned_pdf(tmp_path / "escaneo.pdf")
    assert not page_has_text(pymupdf.open(source)[0])
    output = tmp_path / "ocr.pdf"
    ocr_pdf(source, output, languages=["spa"], dpi=200)
    doc = pymupdf.open(output)
    assert doc.page_count == 2
    assert "Factura" in doc[0].get_text()
    assert doc[0].rect.width == pytest.approx(420, abs=0.5)
    assert len(doc[1].get_images()) == 0, "la página con texto se copia tal cual"


def test_ocr_rejects_missing_language(tmp_path):
    with pytest.raises(OcrUnavailableError):
        ocr_pdf(_scanned_pdf(tmp_path / "s.pdf"), tmp_path / "o.pdf", languages=["klingon"])
