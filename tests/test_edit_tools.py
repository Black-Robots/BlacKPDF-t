"""Marca de agua, números de página, recorte y redimensionado."""

import pymupdf
import pytest

from blackpdf.core.errors import EmptyInputError, InvalidOptionError
from blackpdf.core.geometry import Margins, content_bbox, crop_pdf, resize_pdf
from blackpdf.core.numbering import PageNumberOptions, add_page_numbers, apply_page_numbers
from blackpdf.core.watermark import WatermarkOptions, add_watermark, apply_watermark

from .conftest import build_pdf, dark_bbox


def _blank(width=300, height=300, rotation=0):
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.set_rotation(rotation)
    return doc


def test_text_watermark_center(make_pdf, tmp_path):
    output = tmp_path / "marca.pdf"
    options = WatermarkOptions(text="CONFIDENCIAL", opacity=0.5, rotation=0, color=(0, 0, 0), font_size=30)
    add_watermark(make_pdf(pages=2), output, options)
    doc = pymupdf.open(output)
    for page in doc:
        assert "CONFIDENCIAL" in page.get_text()


def test_watermark_is_semitransparent():
    doc = _blank()
    apply_watermark(doc, WatermarkOptions(text="XXXX", opacity=0.3, rotation=0, color=(0, 0, 0), font_size=60))
    pix = doc[0].get_pixmap(colorspace=pymupdf.csGRAY)
    darkest = min(pix.samples)
    assert 150 < darkest < 220, "con opacidad 0,3 el negro debe verse gris claro"


def test_watermark_rotation_is_counter_clockwise():
    doc = _blank(400, 400)
    apply_watermark(doc, WatermarkOptions(text="HHHHHHHHHH", rotation=45, opacity=1, color=(0, 0, 0), font_size=40))
    pix = doc[0].get_pixmap(colorspace=pymupdf.csGRAY)

    def ink(x0, y0, x1, y1):
        return sum(
            1 for y in range(y0, y1) for x in range(x0, x1) if pix.samples[y * pix.stride + x] < 128
        )

    assert ink(250, 50, 350, 150) > ink(250, 250, 350, 350), "debe subir hacia la derecha"


def test_watermark_upright_on_rotated_page():
    doc = _blank(200, 400, rotation=90)
    apply_watermark(doc, WatermarkOptions(text="WIDETEXTWIDE", rotation=0, opacity=1, color=(0, 0, 0), font_size=30))
    x0, y0, x1, y1 = dark_bbox(doc[0])
    assert (x1 - x0) > 2 * (y1 - y0), "el texto debe verse horizontal"


def test_watermark_positions_and_tile():
    doc = _blank(400, 400)
    apply_watermark(doc, WatermarkOptions(text="AB", rotation=0, opacity=1, color=(0, 0, 0), position="top-left", margin=10))
    x0, y0, _, _ = dark_bbox(doc[0])
    assert x0 < 40 and y0 < 40
    tiled = _blank(400, 400)
    apply_watermark(tiled, WatermarkOptions(text="AB", rotation=30, opacity=1, position="tile", font_size=20))
    assert len(tiled[0].get_text("words")) > 6


def test_image_watermark(make_image, tmp_path):
    doc = _blank(400, 400)
    image = make_image(100, 50, gray=0)
    apply_watermark(doc, WatermarkOptions(image_path=str(image), opacity=0.5, rotation=0, image_scale=0.5))
    x0, y0, x1, y1 = dark_bbox(doc[0])
    assert x1 - x0 == pytest.approx(200, abs=3)
    assert y1 - y0 == pytest.approx(100, abs=3)


def test_watermark_validation():
    with pytest.raises(EmptyInputError):
        WatermarkOptions().validate()
    with pytest.raises(InvalidOptionError):
        WatermarkOptions(text="a", opacity=0).validate()


def test_watermark_non_latin_text_uses_embedded_font():
    doc = _blank()
    apply_watermark(doc, WatermarkOptions(text="Привет", rotation=0, opacity=1))
    assert "Привет" in doc[0].get_text()


def test_page_numbers_formats(make_pdf, tmp_path):
    output = tmp_path / "numerado.pdf"
    add_page_numbers(make_pdf(pages=3), output, PageNumberOptions(template="Página {n} de {total}"))
    doc = pymupdf.open(output)
    assert "Página 2 de 3" in doc[1].get_text()


def test_page_numbers_start_skip_and_position():
    doc = build_pdf(3, size=(300, 400))
    apply_page_numbers(doc, PageNumberOptions(start=5, skip_first=True, position="top-right", template="{n}/{total}"))
    assert "5/6" in doc[1].get_text() and "6/6" in doc[2].get_text()
    assert "/" not in doc[0].get_text()
    words = [w for w in doc[1].get_text("words") if w[4] == "5/6"]
    x0, y0, x1, y1 = words[0][:4]
    assert x1 > 240 and y0 < 60


def test_page_numbers_on_rotated_page_are_upright_at_bottom():
    doc = _blank(200, 400, rotation=90)
    apply_page_numbers(doc, PageNumberOptions(template="Página {n} de {total}", font_size=14, margin=10))
    x0, y0, x1, y1 = dark_bbox(doc[0])
    assert (x1 - x0) > 3 * (y1 - y0)
    assert y1 > doc[0].rect.height - 30


def test_page_numbers_validation():
    with pytest.raises(InvalidOptionError):
        PageNumberOptions(template="sin numero").validate()
    with pytest.raises(InvalidOptionError):
        PageNumberOptions(position="centro").validate()


def test_crop_margins(make_pdf, tmp_path):
    output = tmp_path / "recortado.pdf"
    crop_pdf(make_pdf(pages=1, size=(400, 400)), output, Margins(10, 20, 30, 40))
    page = pymupdf.open(output)[0]
    assert (round(page.rect.width), round(page.rect.height)) == (340, 360)


def test_crop_margins_on_rotated_page_follow_visual_sides():
    doc = _blank(200, 400, rotation=90)
    from blackpdf.core.geometry import crop_page

    crop_page(doc[0], Margins(top=50, right=0, bottom=0, left=0))
    assert (round(doc[0].rect.width), round(doc[0].rect.height)) == (400, 150)


def test_auto_crop(tmp_path):
    doc = _blank(400, 400)
    doc[0].draw_rect(pymupdf.Rect(100, 120, 200, 260), color=(0, 0, 0), fill=(0, 0, 0))
    box = content_bbox(doc[0])
    assert box.x0 == pytest.approx(100, abs=1.5) and box.y1 == pytest.approx(260, abs=1.5)
    source = tmp_path / "s.pdf"
    doc.save(source)
    output = tmp_path / "auto.pdf"
    crop_pdf(source, output, auto=True, padding=10)
    page = pymupdf.open(output)[0]
    assert page.rect.width == pytest.approx(120, abs=3)
    assert page.rect.height == pytest.approx(160, abs=3)
    x0, y0, x1, y1 = dark_bbox(page)
    assert x0 == pytest.approx(10, abs=2) and y0 == pytest.approx(10, abs=2)


def test_crop_requires_margins(make_pdf, tmp_path):
    with pytest.raises(InvalidOptionError):
        crop_pdf(make_pdf(), tmp_path / "x.pdf", Margins())
    with pytest.raises(InvalidOptionError):
        crop_pdf(make_pdf(size=(100, 100)), tmp_path / "x.pdf", Margins.uniform(60))


def test_resize_to_a4_keeps_content_and_rotation(tmp_path):
    doc = _blank(200, 100)
    doc[0].insert_text((20, 50), "ABC", fontsize=20)
    doc[0].add_rect_annot(pymupdf.Rect(5, 5, 30, 30))
    rotated = doc.new_page(width=100, height=200)
    rotated.insert_text((10, 30), "ROT", fontsize=12)
    rotated.set_rotation(90)
    source = tmp_path / "s.pdf"
    doc.save(source)
    output = tmp_path / "a4.pdf"
    resize_pdf(source, output, "A4")
    out = pymupdf.open(output)
    assert (round(out[0].rect.width), round(out[0].rect.height)) == (842, 595)
    assert "ABC" in out[0].get_text()
    assert len(list(out[0].annots())) == 0, "las anotaciones quedan incorporadas"
    assert out[1].rotation == 0 and out[1].rect.width > out[1].rect.height
    x0, y0, x1, y1 = dark_bbox(out[1])
    assert (y1 - y0) > (x1 - x0), "el texto girado sigue viéndose girado"


def test_resize_letter_portrait(make_pdf, tmp_path):
    output = tmp_path / "carta.pdf"
    resize_pdf(make_pdf(pages=1, size=(300, 300)), output, "letter", "portrait")
    page = pymupdf.open(output)[0]
    assert (page.rect.width, page.rect.height) == (612, 792)
