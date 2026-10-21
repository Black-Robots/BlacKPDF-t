"""Medir a escala."""

from __future__ import annotations

import math

import pymupdf
import pytest

from blackpdf.core import measure
from blackpdf.core.comments import set_comment
from blackpdf.core.errors import InvalidOptionError


def _page(rotation: int = 0):
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    page.set_rotation(rotation)
    return doc, page


def test_scales():
    plan = measure.Scale.from_ratio(100, "m")
    assert plan.ratio == "1:100" and math.isclose(plan.length(72), 2.54), "una pulgada de papel son 2,54 m"
    assert math.isclose(plan.area(72 * 72), 2.54 ** 2)
    assert math.isclose(plan.with_unit("cm").length(72), 254)
    paper = measure.Scale()
    assert math.isclose(paper.length(72), 25.4) and paper.unit == "mm"
    calibrated = measure.Scale.calibrated(72, 2.54, "m")
    assert calibrated.ratio == "1:100" and math.isclose(calibrated.factor, plan.factor)
    assert measure.Scale.calibrated(100, 3, "ft").unit == "ft"
    with pytest.raises(InvalidOptionError):
        measure.Scale.calibrated(0, 5, "m")
    with pytest.raises(InvalidOptionError):
        measure.Scale.from_ratio(100, "leguas")


def test_geometry():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert measure.path_length(square) == 30 and measure.path_length(square, closed=True) == 40
    assert measure.polygon_area(square) == 100 and measure.polygon_area(list(reversed(square))) == 100
    scale = measure.Scale(0.5, "m", "")
    assert measure.measure("distance", [(0, 0), (30, 40)], scale) == 25
    assert measure.measure("perimeter", square, scale) == 15
    assert measure.measure("area", square, scale) == 25


@pytest.mark.parametrize("rotation", [0, 90, 270])
def test_measurement_annotations(rotation):
    doc, page = _page(rotation)
    scale = measure.Scale.from_ratio(50, "m")
    line = measure.add_measurement(page, "distance", [(50, 60), (250, 60)], scale, "3,53 m")
    area = measure.add_measurement(page, "area", [(60, 100), (200, 100), (200, 200), (60, 200)], scale, "17,4 m²")
    walk = measure.add_measurement(page, "perimeter", [(250, 120), (300, 180), (260, 240)], scale, "4,10 m")
    assert [a.type[0] for a in (line, area, walk)] == [pymupdf.PDF_ANNOT_LINE, pymupdf.PDF_ANNOT_POLYGON,
                                                       pymupdf.PDF_ANNOT_POLY_LINE]
    assert all(measure.is_measurement(a) for a in (line, area, walk))
    assert doc.xref_get_key(line.xref, "IT")[1] == "/LineDimension"
    assert b"(3,53 m) Tj" in line._getAP() and b"(17,4 m\\262) Tj" in area._getAP()
    visual = pymupdf.Rect(line.rect) * page.rotation_matrix
    assert visual.contains(pymupdf.Rect(50, 50, 250, 61)), "el recuadro incluye la cifra, encima de la línea"

    reopened = pymupdf.open(stream=doc.tobytes(), filetype="pdf")
    again = reopened[0]
    found = measure.page_scale(again)
    assert found is not None and found.ratio == "1:50" and found.unit == "m"
    assert math.isclose(found.factor, scale.factor, rel_tol=1e-6), "la escala se recupera de las medidas"
    first = again.first_annot
    set_comment(again, first.xref, "Revisar con la obra")
    first = again.first_annot
    assert b"(3,53 m) Tj" in first._getAP(), "comentar no borra la cifra"
    assert first.info["content"] == "Revisar con la obra" and measure.measurement_label(first) == "3,53 m"


def test_page_scale_from_viewport():
    doc, page = _page()
    doc.xref_set_key(page.xref, "VP", "[<</Type/Viewport/BBox[0 0 300 400]/Measure<</Type/Measure/Subtype/RL"
                                      "/R(1 in = 10 ft)/X[<</Type/NumberFormat/U(ft)/C 0.1388889>>]>>>>"
                                      "<</Type/Viewport/BBox[300 0 600 400]/Measure<</Type/Measure/Subtype/RL"
                                      "/R(1:20)/X[<</Type/NumberFormat/U(m)/C 0.0070556>>]>>>>]")
    left = measure.page_scale(page, (100, 100))
    right = measure.page_scale(page, (450, 100))
    assert left.unit == "ft" and left.ratio == "1 in = 10 ft" and math.isclose(left.length(72), 10, rel_tol=1e-5)
    assert right.unit == "m" and right.ratio == "1:20"
    assert measure.page_scale(_page()[1]) is None


def test_snap_targets_on_rotated_page():
    doc, page = _page()
    page.draw_line((50, 60), (200, 60))
    page.draw_rect(pymupdf.Rect(220, 100, 300, 180))
    page.set_rotation(90)
    targets = set(measure.snap_targets(page))
    expected = [pymupdf.Point(p) * page.rotation_matrix for p in ((50, 60), (200, 60), (220, 100), (300, 180))]
    assert all((round(p.x, 2), round(p.y, 2)) in targets for p in expected)


def test_invalid_measurements():
    _, page = _page()
    scale = measure.Scale()
    with pytest.raises(InvalidOptionError):
        measure.add_measurement(page, "distance", [(10, 10), (10.2, 10)], scale, "0 mm")
    with pytest.raises(InvalidOptionError):
        measure.add_measurement(page, "area", [(10, 10), (50, 50), (90, 90)], scale, "0")
    with pytest.raises(InvalidOptionError):
        measure.add_measurement(page, "volume", [(10, 10), (50, 50)], scale, "0")
