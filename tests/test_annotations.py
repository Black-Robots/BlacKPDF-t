import pymupdf
import pytest

from blackpdf.core.annotations import (
    add_ink,
    add_note,
    add_shape,
    add_signature,
    add_text_box,
    add_text_markup,
    annotation_at,
    delete_annotation,
    list_annotations,
    normalize_strokes,
    set_note_text,
    strokes_aspect,
    text_box_size,
)
from blackpdf.core.errors import InvalidOptionError
from blackpdf.core.text import line_rects, page_words, select_words

from .conftest import build_pdf, dark_bbox


def _page(rotation=0, width=300, height=400):
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.set_rotation(rotation)
    return doc, page


def test_ink_annotation():
    doc, page = _page()
    annot = add_ink(page, [[(10, 10), (50, 60), (90, 20)]], color=(1, 0, 0), width=3)
    assert annot.type[1] == "Ink"
    assert annot.colors["stroke"] == pytest.approx((1, 0, 0))
    assert annot.border["width"] == 3


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_coordinates_are_visual_on_rotated_pages(rotation):
    doc, page = _page(rotation)
    add_shape(page, "rect", (10, 10), (60, 40), color=(0, 0, 0), width=2)
    x0, y0, x1, y1 = dark_bbox(page)
    assert x0 == pytest.approx(9, abs=2) and y0 == pytest.approx(9, abs=2)
    assert x1 == pytest.approx(60, abs=2) and y1 == pytest.approx(40, abs=2)


@pytest.mark.parametrize("kind", ["rect", "ellipse", "line", "arrow"])
def test_shapes(kind):
    doc, page = _page()
    annot = add_shape(page, kind, (20, 20), (120, 90))
    assert annot is not None
    assert dark_bbox(page, threshold=250) is not None


def test_arrow_has_head():
    doc, page = _page()
    annot = add_shape(page, "arrow", (20, 20), (120, 90))
    assert annot.line_ends[1] == pymupdf.PDF_ANNOT_LE_CLOSED_ARROW


def test_too_small_shapes_are_rejected():
    doc, page = _page()
    with pytest.raises(InvalidOptionError):
        add_shape(page, "rect", (10, 10), (10.2, 10.2))
    with pytest.raises(InvalidOptionError):
        add_shape(page, "hexagon", (0, 0), (10, 10))


def test_text_markup_on_selected_words():
    doc = build_pdf(1)
    page = doc[0]
    words = page_words(page)
    chosen = select_words(words, pymupdf.Point(70, 130), pymupdf.Point(400, 135))
    assert [w.text for w in chosen][:2] == ["texto", "de"]
    for kind in ("highlight", "underline", "strikeout", "squiggly"):
        annot = add_text_markup(page, kind, line_rects(chosen), color=(1, 0.8, 0))
        assert annot.type[1].lower().startswith(kind[:5])
    with pytest.raises(InvalidOptionError):
        add_text_markup(page, "highlight", [], (1, 1, 0))


def test_text_box_is_upright_on_rotated_page():
    doc, page = _page(rotation=90)
    width, height = text_box_size("Hola mundo", 14)
    add_text_box(page, pymupdf.Rect(20, 20, 20 + width, 20 + height), "Hola mundo", font_size=14)
    x0, y0, x1, y1 = dark_bbox(page)
    assert (x1 - x0) > 2 * (y1 - y0)
    assert "Hola mundo" in page.first_annot.info["content"]
    with pytest.raises(InvalidOptionError):
        add_text_box(page, pymupdf.Rect(0, 0, 10, 10), "   ")


def test_note_and_edit():
    doc, page = _page()
    annot = add_note(page, (100, 100), "Revisar")
    assert annot.type[1] == "Text"
    assert set_note_text(page, annot.xref, "Hecho")
    assert page.first_annot.info["content"] == "Hecho"


def test_signature_fits_rect_and_aspect():
    strokes = [[(0, 0), (100, 50)], [(0, 50), (100, 0)]]
    assert strokes_aspect(normalize_strokes(strokes)) == pytest.approx(2.0)
    doc, page = _page()
    annot = add_signature(page, pymupdf.Rect(50, 50, 250, 250), strokes, color=(0, 0, 0), width=2)
    points = [pymupdf.Point(v) for stroke in annot.vertices for v in stroke]
    xs, ys = [p.x for p in points], [p.y for p in points]
    assert max(xs) - min(xs) == pytest.approx(200, abs=1)
    assert max(ys) - min(ys) == pytest.approx(100, abs=1)
    assert min(ys) == pytest.approx(100, abs=1), "centrada verticalmente en el rectángulo"
    with pytest.raises(InvalidOptionError):
        normalize_strokes([])


def test_annotation_hit_testing_and_delete():
    doc, page = _page()
    ink = add_ink(page, [[(10, 10), (200, 10)]], width=2)
    rect = add_shape(page, "rect", (50, 100), (150, 200))
    assert annotation_at(page, (100, 11)) == ink.xref
    assert annotation_at(page, (100, 40)) is None, "lejos del trazo no cuenta"
    assert annotation_at(page, (100, 150)) == rect.xref
    assert {a.kind for a in list_annotations(page)} == {"Ink", "Square"}
    assert delete_annotation(page, rect.xref)
    assert not delete_annotation(page, 99999)
    assert annotation_at(page, (100, 150)) is None


def test_hit_testing_on_rotated_page():
    doc, page = _page(rotation=90)
    shape = add_shape(page, "rect", (10, 10), (60, 40))
    assert annotation_at(page, (30, 25)) == shape.xref
    visual = list_annotations(page)[0].rect
    assert visual.x0 == pytest.approx(9, abs=2) and visual.x1 == pytest.approx(61, abs=2)


def test_redaction_removes_text_for_real():
    from blackpdf.core.annotations import redact_areas, redact_text

    doc = build_pdf(2)
    page = doc[0]
    words = [w for w in page.get_text("words") if w[4] == "ejemplo"]
    rect = pymupdf.Rect(words[0][:4])
    assert redact_areas(page, [rect]) == 1
    assert "ejemplo" not in page.get_text()
    assert "texto" in page.get_text(), "el resto del texto se conserva"
    assert len(list(page.annots())) == 0
    pix = page.get_pixmap(clip=rect)
    assert min(pix.samples) < 30, "queda un recuadro negro"
    assert redact_text(doc, "buscar") == 2
    assert all("buscar" not in p.get_text() for p in doc)
    with pytest.raises(InvalidOptionError):
        redact_text(doc, "  ")


def test_redaction_on_rotated_page_uses_visual_coordinates():
    from blackpdf.core.annotations import redact_areas

    doc, page = _page(rotation=90)
    page.insert_text(pymupdf.Point(20, 40) * page.derotation_matrix, "SECRETO", fontsize=20, rotate=90)
    assert "SECRETO" in page.get_text()
    redact_areas(page, [pymupdf.Rect(10, 10, 200, 60)])
    assert "SECRETO" not in page.get_text()
