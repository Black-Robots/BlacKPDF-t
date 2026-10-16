"""Comprimir, PDF a imagen e imagen a PDF."""

import pymupdf
import pytest

from blackpdf.core.compress import CompressionLevel, compress_pdf
from blackpdf.core.errors import EmptyInputError, InvalidOptionError
from blackpdf.core.images import images_to_pdf, pdf_to_images

from .conftest import build_image


def _photo_pdf(path, pages=2):
    """PDF con imágenes grandes y ruidosas, para que la compresión se note."""
    import random

    rng = random.Random(4)
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1200, 1600), False)
        data = bytearray(rng.getrandbits(8) for _ in range(1200 * 1600 * 3 // 64)) * 64
        pix.set_rect(pix.irect, (200, 200, 200))
        pix = pymupdf.Pixmap(pymupdf.csRGB, 1200, 1600, bytes(data[: 1200 * 1600 * 3]), False)
        page.insert_image(page.rect, pixmap=pix)
    doc.save(path)
    doc.close()
    return path


@pytest.mark.parametrize("level", list(CompressionLevel))
def test_compress_levels(tmp_path, level):
    source = _photo_pdf(tmp_path / "fotos.pdf")
    output = tmp_path / f"comprimido_{level.value}.pdf"
    result = compress_pdf(source, output, level)
    assert result.path == output and output.exists()
    assert result.compressed_size <= result.original_size
    assert pymupdf.open(output).page_count == 2
    if level is not CompressionLevel.LOW:
        assert result.saved_ratio > 0.3


def test_compress_never_grows(make_pdf, tmp_path):
    source = make_pdf(pages=1)
    result = compress_pdf(source, tmp_path / "c.pdf", CompressionLevel.LOW)
    assert result.compressed_size <= result.original_size


def test_pdf_to_images_png_and_jpg(make_pdf, tmp_path):
    source = make_pdf(pages=3, name="hojas.pdf", size=(144, 72))
    pngs = pdf_to_images(source, tmp_path / "png", fmt="png", dpi=72)
    assert [p.name for p in pngs] == ["hojas_p001.png", "hojas_p002.png", "hojas_p003.png"]
    pix = pymupdf.Pixmap(str(pngs[0]))
    assert (pix.width, pix.height) == (144, 72)
    jpgs = pdf_to_images(source, tmp_path / "jpg", fmt="jpeg", dpi=144, pages="2")
    assert len(jpgs) == 1 and jpgs[0].suffix == ".jpg"
    assert pymupdf.Pixmap(str(jpgs[0])).width == 288


def test_pdf_to_images_rejects_bad_options(make_pdf, tmp_path):
    with pytest.raises(InvalidOptionError):
        pdf_to_images(make_pdf(), tmp_path, fmt="gif")
    with pytest.raises(InvalidOptionError):
        pdf_to_images(make_pdf(), tmp_path, dpi=5000)


def test_images_to_pdf_original_size(make_image, tmp_path):
    images = [make_image(200, 100, "png"), make_image(50, 80, "jpg")]
    output = tmp_path / "album.pdf"
    images_to_pdf(images, output)
    doc = pymupdf.open(output)
    assert doc.page_count == 2
    assert doc[0].rect.width / doc[0].rect.height == pytest.approx(2.0, rel=0.01)
    assert len(doc[1].get_images()) == 1


def test_images_to_pdf_a4_auto_orientation(make_image, tmp_path):
    output = tmp_path / "a4.pdf"
    images_to_pdf([make_image(300, 100), make_image(100, 300)], output, page_size="A4", margin=20)
    doc = pymupdf.open(output)
    assert doc[0].rect.width > doc[0].rect.height
    assert doc[1].rect.width < doc[1].rect.height
    assert round(doc[1].rect.width) == 595
    info = doc[0].get_image_info()[0]
    bbox = pymupdf.Rect(info["bbox"])
    assert bbox.x0 >= 19.9 and bbox.x1 <= doc[0].rect.width - 19.9


def test_images_to_pdf_multiframe_tiff(tmp_path):
    from PIL import Image

    frames = [Image.new("RGB", (60, 40), color) for color in ("red", "green", "blue")]
    path = tmp_path / "multi.tif"
    frames[0].save(path, save_all=True, append_images=frames[1:])
    output = tmp_path / "tiff.pdf"
    images_to_pdf([path], output)
    assert pymupdf.open(output).page_count == 3


def test_images_to_pdf_validation(tmp_path, make_image):
    with pytest.raises(EmptyInputError):
        images_to_pdf([], tmp_path / "x.pdf")
    with pytest.raises(InvalidOptionError):
        images_to_pdf([make_image()], tmp_path / "x.pdf", page_size="B7")


def test_build_image_helper():
    assert build_image(10, 10).width == 10
